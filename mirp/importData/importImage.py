import os
import os.path
from functools import singledispatch
from typing import Union, List

import numpy as np
import pandas as pd

from mirp.importData.imageDirectory import ImageDirectory, MaskDirectory
from mirp.importData.imageGenericFile import ImageFile, MaskFile
from mirp.importData.utilities import supported_file_types, supported_image_modalities, flatten_list


def import_image(
        image,
        sample_name: Union[None, str, List[str]] = None,
        image_name: Union[None, str, List[str]] = None,
        image_file_type: Union[None, str] = None,
        image_modality: Union[None, str, List[str]] = None,
        image_sub_folder: Union[None, str] = None,
        stack_images: str = "auto"
) -> List[ImageFile]:
    """
    Creates and curates references to image files. Actual image data are generally not loaded.

    :param image: A path to an image file, a path to a directory containing image files, a path to a config_data.xml
    file, a path to a csv file containing references to image files, a pandas.DataFrame containing references to
    image files, or a numpy.ndarray.
    :param sample_name: Name of expected sample names. This is used to select specific image files. If None,
    no image files are filtered based on the corresponding sample name (if known).
    :param image_name: Pattern to match image files against. The matches are exact. Use wildcard symbols ("*") to
    match varying structures. The sample name (if part of the file name) can also be specified using "#". For example,
    image_name = '#_*_image' would find John_Doe in John_Doe_CT_image.nii or John_Doe_001_image.nii. File extensions
    do not need to specified. If None, file names are not used for filtering files and setting sample names.
    :param image_file_type: The type of file that is expected. If None, the file type is not used for filtering
    files. Options: "dicom", "nifti", "nrrd", "numpy" and "itk". "itk" comprises "nifti" and "nrrd" file types.
    :param image_modality: The type of modality that is expected. If None, modality is not used for filtering files.
    Note that only DICOM files contain metadata concerning modality. Options: "ct", "pet" or "pt", "mri" or "mr",
    and "generic".
    :param image_sub_folder: Fixed directory substructure where image files are located. If None,
    this directory substructure is not used for filtering files.
    :param stack_images: One of auto, yes or no. If image files in the same directory cannot be assigned to
    different samples, and are 2D (slices) of the same size, they might belong to the same 3D image stack. "auto"
    will stack 2D numpy arrays, but not other file types. "yes" will stack all files that contain 2D images,
    that have the same dimensions, orientation and spacing, except for DICOM files. "no" will not stack any files.
    DICOM files ignore this argument, because their stacking can be determined from metadata.
    :return: list of image files.
    """
    # Check modality.
    if image_modality is not None:
        if not isinstance(image_modality, str):
            raise TypeError(
                f"The image_modality argument is expected to be a single character string or None. The following "
                f"modalities are supported: {', '.join(supported_image_modalities(None))}.")
        _ = supported_image_modalities(image_modality.lower())

    # Check image_file_type.
    if image_file_type is not None:
        if not isinstance(image_file_type, str):
            raise TypeError(
                f"The image_file_type argument is expected to be a single character string, or None. The following file "
                f"types are supported: {', '.join(supported_file_types(None))}.")
        _ = supported_file_types(image_file_type)

    # Check stack_images
    if stack_images not in ["yes", "auto", "no"]:
        raise ValueError(
            f"The stack_images argument is expected to be one of yes, auto, or no. Found: {stack_images}."
        )

    image_list = _import_image(
        image,
        sample_name=sample_name,
        image_name=image_name,
        image_file_type=image_file_type,
        image_modality=image_modality,
        image_sub_folder=image_sub_folder,
        stack_images=stack_images,
        is_mask=False
    )

    if not isinstance(image_list, list):
        image_list = [image_list]

    # Flatten list.
    image_list = flatten_list(image_list)

    return image_list


@singledispatch
def _import_image(image, **kwargs):
    raise NotImplementedError(f"Unsupported image type: {type(image)}")


@_import_image.register(list)
def _(image: list, **kwargs):
    # List can be anything. Hence, we dispatch import_image for the individual list elements.
    image_list = [_import_image(current_image, **kwargs) for current_image in image]

    return image_list


@_import_image.register(str)
def _(image: str, is_mask=False, **kwargs):
    # Image is a string, which could be a path to a xml file, to a csv file, or just a regular
    # path a path to a file, or a path to a directory. Test which it is and then dispatch.

    if image.lower().endswith("xml"):
        ...

    elif image.lower().endswith("csv"):
        ...

    elif os.path.isdir(image):
        if is_mask:
            return MaskDirectory(directory=image, **kwargs)
        else:
            return ImageDirectory(directory=image, **kwargs)

    elif os.path.exists(image):
        if is_mask:
            return MaskFile(file_path=image, **kwargs).create()
        else:
            return ImageFile(file_path=image, **kwargs).create()

    else:
        raise ValueError("The image path does not point to a xml file, a csv file, a valid image file or a directory "
                         "containing imaging.")


@_import_image.register(pd.DataFrame)
def _(image: pd.DataFrame,
      image_modality: Union[None, str] = None,
      **kwargs):
    ...


@_import_image.register(np.ndarray)
def _(image: np.ndarray,
      is_mask: bool = False,
      **kwargs):

    from imageNumpyFile import ImageNumpyFile, MaskNumpyFile

    if is_mask:
        image_object = MaskNumpyFile(**kwargs)
    else:
        image_object = ImageNumpyFile(**kwargs)

    image_object.image_data = image_object.image_metadata = image
    image_object.complete()
    image_object.update_image_data()
    image_object.check(raise_error=True, remove_metadata=False)


@_import_image.register(ImageFile)
def _(image: ImageFile, **kwargs):

    if not issubclass(type(image), ImageFile):
        image = image.create()

    # Check if the data are consistent.
    image.check(raise_error=True)

    # Complete image data and add identifiers (if any)
    image.complete()

    return image


@_import_image.register(ImageDirectory)
def _(image: ImageDirectory, **kwargs):

    # Check first if the data are consistent for a directory.
    image.check(raise_error=True)

    # Yield image files.
    image.create_images()

    # Dispatch to import_image method for ImageFile objects. This performs a last check and completes the object.
    return [_import_image(current_image, **kwargs) for current_image in image.image_files]
