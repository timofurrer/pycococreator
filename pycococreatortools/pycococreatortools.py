#!/usr/bin/env python3

import os
import re
import datetime
import numpy as np
from itertools import groupby
from skimage import measure
from PIL import Image
from pycocotools import mask

convert = lambda text: int(text) if text.isdigit() else text.lower()
natrual_key = lambda key: [ convert(c) for c in re.split('([0-9]+)', key) ]

def resize_binary_mask(array, new_size):
    image = Image.fromarray(array.astype(np.uint8)*255)
    image = image.resize(new_size)
    return np.asarray(image).astype(np.bool_)

def close_contour(contour):
    if not np.array_equal(contour[0], contour[-1]):
        contour = np.vstack((contour, contour[0]))
    return contour

def binary_mask_to_rle(binary_mask):
    rle = {'counts': [], 'size': list(binary_mask.shape)}
    counts = rle.get('counts')
    for i, (value, elements) in enumerate(groupby(binary_mask.ravel(order='F'))):
        if i == 0 and value == 1:
                counts.append(0)
        counts.append(len(list(elements)))

    return rle

def binary_mask_to_polygon(binary_mask, tolerance=0):
    """Converts a binary mask to COCO polygon representation

    Args:
        binary_mask: a 2D binary numpy array where '1's represent the object
        tolerance: Maximum distance from original points of polygon to approximated
            polygonal chain. If tolerance is 0, the original coordinate array is returned.

    """
    polygons = []
    # pad mask to close contours of shapes which start and end at an edge
    padded_binary_mask = np.pad(binary_mask, pad_width=1, mode='constant', constant_values=0)
    contours = measure.find_contours(padded_binary_mask, 0.5)
    contours = np.subtract(contours, 1)
    for contour in contours:
        contour = close_contour(contour)
        contour = measure.approximate_polygon(contour, tolerance)
        if len(contour) < 3:
            continue
        contour = np.flip(contour, axis=1)
        segmentation = contour.ravel().tolist()
        # after padding and subtracting 1 we may get -0.5 points in our segmentation 
        segmentation = [0 if i < 0 else i for i in segmentation]
        polygons.append(segmentation)

    return polygons

def create_image_info(image_id, file_name, image_size, 
                      date_captured=datetime.datetime.utcnow().isoformat(' '),
                      license_id=1, coco_url="", flickr_url=""):

    image_info = {
            "id": image_id,
            "file_name": file_name,
            "width": image_size[0],
            "height": image_size[1],
            "date_captured": date_captured,
            "license": license_id,
            "coco_url": coco_url,
            "flickr_url": flickr_url
    }

    return image_info

def create_annotation_info(annotation_id, image_id, category_info, binary_mask, 
                           image_size=None, tolerance=2, bounding_box=None):

    if image_size is not None:
        binary_mask = resize_binary_mask(binary_mask, image_size)

    binary_mask_encoded = mask.encode(np.asfortranarray(binary_mask.astype(np.uint8)))

    area = mask.area(binary_mask_encoded)
    if area < 1:
        return None

    if bounding_box is None:
        bounding_box = mask.toBbox(binary_mask_encoded)

    if "is_crowd" in category_info and category_info["is_crowd"]:
        is_crowd = 1
        segmentation = binary_mask_to_rle(binary_mask)
    else:
        is_crowd = 0
        segmentation = binary_mask_to_polygon(binary_mask, tolerance)
        if not segmentation:
            return None

    annotation_info = {
        "id": annotation_id,
        "image_id": image_id,
        "category_id": category_info["id"],
        "iscrowd": is_crowd,
        "area": area.tolist(),
        "bbox": bounding_box.tolist(),
        "segmentation": segmentation,
        "width": binary_mask.shape[1],
        "height": binary_mask.shape[0],
    }

    return annotation_info


def create_annotation_infos(
    start_annotation_id, image_id, category_info, binary_mask,
    image_size=None, tolerance=2, connectivity=None
):
    """Create multiple annotation infos for each connected component in the given binary mask

    The same category is used for each annotation.
    The annotation ids start from `start_annotation_id` and
    are incremented for each annotation.

    The `connectivity` argument can be used to specify the connectivity
    for the labels according to:
    https://scikit-image.org/docs/dev/api/skimage.measure.html#skimage.measure.label

    Limitations:
        * Crowds are not supported
    """
    if "is_crowd" in category_info and category_info["is_crowd"]:
        raise NotImplementedError("Creating multiple crowd annotations from a single binary mask is not supported")

    if image_size is not None:
        binary_mask = resize_binary_mask(binary_mask, image_size)

    # label connected components in binary mask image
    label_image = measure.label(binary_mask, connectivity=connectivity)
    region_props = measure.regionprops(label_image)

    # create a binary mask image per region property
    binary_masks = []
    for region_bbox in (r.bbox for r in region_props):
        region_binary_mask = np.zeros_like(binary_mask, dtype=np.bool)

        # copy the region into the region binary mask
        bbox_slice = (
            slice(region_bbox[0], region_bbox[2]),
            slice(region_bbox[1], region_bbox[3]),
        )
        region_binary_mask[bbox_slice] = binary_mask[bbox_slice]
        binary_masks.append(region_binary_mask)

    # create annotations for each binary mask
    annotation_infos = []
    for annotation_id, region_binary_mask in enumerate(binary_masks, start=start_annotation_id):
        annotation_info = create_annotation_info(
            annotation_id, image_id, category_info, region_binary_mask,
            image_size=image_size, tolerance=tolerance
        )
        annotation_infos.append(annotation_info)

    return annotation_infos
