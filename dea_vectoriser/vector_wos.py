# read in WOs geotiff, output Vector

# 1. create binary arrays for classed of interest: 1)water and 2)Not Analysed
# 2. conduct binary errosion and dialation to remove single pixles/big gaps between datatypes
#     - B) conduct fill on water to remove single pixle holes?
#     - C) conduct 1 pixle buffer of no-data class? (unsure if should be latter in workflow)
# 3. vectorise
# 4. simplify shapes to remove complixity
# 5. join both data types back together as one Geopandas Geodataframe (container for shapely objects with projection
#    information)
# 6. export an a single shapefile with attributes intact.

import geopandas
import geopandas as gp
import pandas as pd
import rasterio.features
import xarray as xr
from fiona.crs import from_epsg
from scipy import ndimage
from shapely.geometry import shape
# Derived from https://github.com/GeoscienceAustralia/dea-notebooks/blob/KooieCate/vector_WOs_draft4.py
from typing import Tuple


def load_wos_data(url) -> xr.Dataset:
    """Open a GeoTIFF info an in memory DataArray """
    geotiff_wos = xr.open_rasterio(url)
    wos_dataset = geotiff_wos.to_dataset('band')
    wos_dataset = wos_dataset.rename({1: 'wo'})
    return wos_dataset


def generate_raster_layers(wos_dataset: xr.Dataset) -> Tuple[xr.DataArray, xr.DataArray]:
    # Defining the three 'classes':
    # a) Water: where water is observed. Bit value 128
    # b) unspoken 'dry'. this is not vectorised and is left as a transparent layer. bit values: 1 (no data) 2 (
    # Contiguity)
    # c) Not_analysed: every masking applied to the data except terrain shadow. bit values: composed of everything else,
    # 1 create binary arrays for two classes of interest
    water_vals = (wos_dataset.wo == 128)  # water only has 128 water observations
    # here we used reversed logic to turn all pixels that should be 'not analysed' to a value of 3. is is easier to
    # list the 4 classes that are passed to the unlabled 'dry' class
    not_analysed = wos_dataset.wo.where(((wos_dataset.wo == 0) | (wos_dataset.wo == 1) | (wos_dataset.wo == 8)
                                         | (wos_dataset.wo == 2) | (wos_dataset.wo == 128) | (wos_dataset.wo == 130) | (
                                                 wos_dataset.wo == 142)), 3)
    not_analysed = not_analysed.where((not_analysed == 3), 0)  # now keep the 3 values and make everything else 0
    # 2 conduct binary erosion and closing to remove single pixels
    erroded_water = xr.DataArray(ndimage.binary_erosion(water_vals, iterations=2).astype(water_vals.dtype),
                                 coords=water_vals.coords)
    erroded_not_analysed = xr.DataArray(ndimage.binary_erosion(not_analysed, iterations=2).astype(not_analysed.dtype),
                                        coords=not_analysed.coords)
    # dilating cloud 3 times after eroding 2, to create small overlap and illuminate gaps in data
    dilated_water = xr.DataArray(ndimage.binary_dilation(erroded_water, iterations=3).astype(water_vals.dtype),
                                 coords=water_vals.coords)
    dilated_not_analysed = xr.DataArray(
        ndimage.binary_dilation(erroded_not_analysed, iterations=3).astype(not_analysed.dtype),
        coords=not_analysed.coords)

    return dilated_water, dilated_not_analysed


def vectorise_data(data_array: xr.DataArray, transform, crs, label='Label'):
    """this module takes an Xarray DataArray and vectorises it as shapely geometries in a Geopandas Geodataframe

    Input
    data_array: a DataArray with boolean values (1,0) with 1 or True equal to the areas that will be turned
                into vectors
    label: default 'Label', String, the data label that will be added to each geometry in geodataframe

    output
    Geodataframe containing shapely geometries with data type label in a series called attribute"""

    vector = rasterio.features.shapes(
        data_array.data.astype('float32'),
        mask=data_array.data.astype('float32') == 1,  # this defines which part of array becomes polygons
        transform=transform)

    # rasterio.features.shapes outputs tuples. we only want the polygon coordinate portions of the tuples
    vectored_data = list(vector)  # put tuple output in list

    # Extract the polygon coordinates from the list
    polygons = [polygon for polygon, value in vectored_data]

    # create a list with the data label type
    labels = [label for _ in polygons]

    # Convert polygon coordinates into polygon shapes
    polygons = [shape(polygon) for polygon in polygons]

    # Create a geopandas dataframe populated with the polygon shapes
    data_gdf = gp.GeoDataFrame(data={'attribute': labels},
                               geometry=polygons,
                               crs=crs)
    return data_gdf


def vectorise_wos_from_url(url) -> geopandas.GeoDataFrame:
    raster = load_wos_data(url)

    dataset_crs = from_epsg(raster.crs[11:])
    dataset_transform = raster.transform

    # grab crs from input tiff
    dilated_water, dilated_not_analysed = generate_raster_layers(raster)

    # vectorise the arrays
    notAnalysedGPD = vectorise_data(dilated_not_analysed, dataset_transform, dataset_crs, label='Not_analysed')

    WaterGPD = vectorise_data(dilated_water, dataset_transform, dataset_crs, label='Water')

    # Simplify

    # Run simplification with 15 tolerance
    simplified_water = WaterGPD.simplify(10)

    simplified_not_analysed = notAnalysedGPD.simplify(15)

    # Put simplified shapes in a dataframe
    simple_waterGPD = gp.GeoDataFrame(geometry=simplified_water,
                                      crs=dataset_crs)

    simple_notAnalysedGPD = gp.GeoDataFrame(geometry=simplified_not_analysed,
                                            crs=dataset_crs)

    # add attribute labels back in
    simple_waterGPD['attribute'] = WaterGPD['attribute']

    simple_notAnalysedGPD['attribute'] = notAnalysedGPD['attribute']

    # change to 'epsg:3577' for output
    simple_waterGPD = simple_waterGPD.to_crs('epsg:3577')
    simple_notAnalysedGPD = simple_notAnalysedGPD.to_crs('epsg:3577')

    # 6 Join together and save to file

    all_classes = gp.GeoDataFrame(pd.concat([simple_waterGPD, simple_notAnalysedGPD], ignore_index=True),
                                  crs=simple_notAnalysedGPD.crs)

    return all_classes
