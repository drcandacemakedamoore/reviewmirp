import copy
import warnings

import numpy as np

from typing import Union, List, Optional

import pydicom

from mirp.importData.maskContour import ContourClass
from mirp.importData.importImage import ImageFile
from mirp.importData.imageDicomFile import MaskDicomFile
from mirp.masks.baseMask import BaseMask
from mirp.importData.utilities import get_pydicom_meta_tag, has_pydicom_meta_tag


class MaskDicomFileRTSTRUCT(MaskDicomFile):
    def __init__(self, **kwargs):

        super().__init__(**kwargs)

        # RTSTRUCT does not have its own image information.
        self.image_origin = None
        self.image_orientation = None
        self.image_spacing = None
        self.image_dimension = None

    def is_stackable(self, stack_images: str):
        return False

    def create(self):
        return self

    def _complete_image_origin(self, force=False):
        return

    def _complete_image_orientation(self, force=False):
        return

    def _complete_image_spacing(self, force=False):
        return

    def _complete_image_dimensions(self, force=False):
        return

    def _complete_frame_of_reference_uid(self):
        if self.frame_of_reference_uid is None:
            # Try to obtain a frame of reference UID
            if has_pydicom_meta_tag(dcm_seq=self.image_metadata, tag=(0x0020, 0x0052)):
                if get_pydicom_meta_tag(dcm_seq=self.image_metadata, tag=(0x0020, 0x0052), tag_type="str") is not None:
                    self.frame_of_reference_uid = get_pydicom_meta_tag(
                        dcm_seq=self.image_metadata,
                        tag=(0x0020, 0x0052),
                        tag_type="str")

            # For RT structure sets, the FOR UID may be tucked away in the Structure Set ROI Sequence
            if self.frame_of_reference_uid is None and \
                    has_pydicom_meta_tag(dcm_seq=self.image_metadata, tag=(0x3006, 0x0020)):
                structure_set_roi_sequence = self.image_metadata[(0x3006, 0x0020)]

                for structure_set_roi_element in structure_set_roi_sequence:
                    if has_pydicom_meta_tag(dcm_seq=structure_set_roi_element, tag=(0x3006, 0x0024)):
                        self.frame_of_reference_uid = get_pydicom_meta_tag(
                            dcm_seq=structure_set_roi_element,
                            tag=(0x3006, 0x0024),
                            tag_type="str")
                        break

    def check_mask(self, raise_error=True):
        if self.image_metadata is None:
            raise TypeError("DEV: the image_meta_data attribute has not been set.")

        if not get_pydicom_meta_tag(dcm_seq=self.image_metadata, tag=(0x3006, 0x0020), test_tag=True):
            if raise_error:
                warnings.warn(
                    f"The RT-structure set ({self.file_path}) did not contain any ROI sequences.",
                )
            return False

        roi_name_present = [
            get_pydicom_meta_tag(
                dcm_seq=current_structure_set_roi_sequence, tag=(0x3006, 0x0026), tag_type="str", default=None)
            for current_structure_set_roi_sequence in self.image_metadata[(0x3006, 0x0020)]
        ]

        if len(roi_name_present) == 0 and raise_error:
            warnings.warn(
                f"The current RT-structure set ({self.file_path}) does not contain any ROI contours."
            )
            return False

        if any(x is None for x in roi_name_present) and raise_error:
            warnings.warn(
                f"The current RT-structure set ({self.file_path}) lacks one or more ROI names."
            )
            return False

        return True

    def load_data(self, **kwargs):
        pass

    def to_object(
            self,
            image: Union[None, ImageFile],
            disconnected_segments: str = "keep_as_is",
            **kwargs) -> Optional[List[BaseMask]]:

        if image is None:
            raise TypeError(
                f"Converting an RT-structure to a segmentation mask requires that the corresponding image is set. "
                f"No image was provided ({self.file_path})."
            )
        else:
            image.complete()

        self.load_metadata()
        if not self.check_mask():
            return None

        mask_list = []

        # Find which roi numbers (3006,0022) are associated with which roi names (3004,0024).
        roi_name_present = [
            get_pydicom_meta_tag(
                dcm_seq=current_structure_set_roi_sequence, tag=(0x3006, 0x0026), tag_type="str", default=None)
            for current_structure_set_roi_sequence in self.image_metadata[(0x3006, 0x0020)]
        ]
        roi_number_present = [
            get_pydicom_meta_tag(
                dcm_seq=current_structure_set_roi_sequence, tag=(0x3006, 0x0022), tag_type="int", default=None)
            for current_structure_set_roi_sequence in self.image_metadata[(0x3006, 0x0020)]
        ]

        # Identify user-provided roi names.
        provided_roi_names = None
        if isinstance(self.roi_name, str):
            provided_roi_names = [self.roi_name]
        elif isinstance(self.roi_name, list):
            provided_roi_names = self.roi_name
        elif isinstance(self.roi_name, dict):
            provided_roi_names = list(self.roi_name.keys())

        if provided_roi_names is not None:
            roi_number_present = [
                x for ii, x in enumerate(roi_number_present) if roi_name_present[ii] in provided_roi_names
            ]
            roi_name_present = [x for x in roi_name_present if x in provided_roi_names]

        if len(roi_number_present) == 0:
            return None

        # Obtain segmentation from ROI Contour Sequences (0x3006, 0x0039).
        for roi_contour_sequence in self.image_metadata[(0x3006, 0x0039)]:

            # Get the roi number of the current ROI Contour Sequence
            current_roi_number = get_pydicom_meta_tag(
                dcm_seq=roi_contour_sequence,
                tag=(0x3006, 0x0084),
                tag_type="int")

            if current_roi_number not in roi_number_present:
                continue

            # Determine strategy for converting contours to mask objects.
            use_reference_image, use_orientation, use_position = self._convert_contour_using_image(
                roi_contour_sequence=roi_contour_sequence,
                image=image
            )

            if not use_reference_image:
                # Set up image based on contours.
                temporary_image = self._create_image_from_contour(
                    roi_contour_sequence=roi_contour_sequence,
                    image=image,
                    use_orientation=use_orientation,
                    use_position=use_position
                )

                image_data = self.convert_contour_to_mask(
                    roi_contour_sequence=roi_contour_sequence,
                    image=temporary_image,
                    disconnected_segments=disconnected_segments
                )
            else:
                # Use associated image directly.
                image_data = self.convert_contour_to_mask(
                    roi_contour_sequence=roi_contour_sequence,
                    image=image,
                    disconnected_segments=disconnected_segments
                )

            if image_data is None:
                continue

            if not np.any(image_data):
                continue

            # Complete a copy of the current object.
            temp_mask_object = self.copy()
            temp_mask_object.image_data = image_data
            temp_mask_object.image_origin = image.image_origin
            temp_mask_object.image_spacing = image.image_spacing
            temp_mask_object.image_dimension = image.image_dimension
            temp_mask_object.image_orientation = image.image_orientation
            temp_mask_object.complete()
            temp_mask_object.update_image_data()

            current_roi_name = [
                x for ii, x in enumerate(roi_name_present) if roi_number_present[ii] == current_roi_number
            ][0]

            if isinstance(self.roi_name, dict):
                current_roi_name = self.roi_name.get(current_roi_name)

            mask_list += [
                BaseMask(
                    roi_name=current_roi_name,
                    sample_name=self.sample_name,
                    image_modality=self.modality,
                    image_data=temp_mask_object.image_data,
                    image_spacing=temp_mask_object.image_spacing,
                    image_origin=temp_mask_object.image_origin,
                    image_orientation=temp_mask_object.image_orientation,
                    image_dimensions=temp_mask_object.image_dimension
                )
            ]

        if len(mask_list) == 0:
            return None

        return mask_list

    def _convert_contour_using_image(
            self,
            roi_contour_sequence: pydicom.Dataset,
            image: None | ImageFile
    ) -> tuple[bool, bool, bool]:
        """
        Determine whether the image should be used as reference for creating the mask. In general, if the contours
        can be directly mapped to slices, the image can be used as reference.

        Parameters
        ----------
        roi_contour_sequence: pydicom.Dataset
            DICOM ROI Contour Sequence read using pydicom.
        image: ImageFile
            Reference image

        Returns
        -------
        bool
            Boolean value that determines whether the image can be used as reference for creating the segmentation
            mask (`True`) or an image should be generated based on contour data (`False`).
        """

        from mirp.importData.imageDicomFile import ImageDicomFile
        from mirp.importData.imageDicomFileStack import ImageDicomFileStack

        # In absence of an image, attempt to generate a reference segmentation mask using the ROI contour data
        # directly.
        if image is None:
            return False, False, False

        # Get series instance UID
        reference_series_instance_uid = None
        if isinstance(image, ImageDicomFile) or isinstance(image, ImageDicomFileStack):
            reference_series_instance_uid = image.series_instance_uid

        series_uid_correct = False
        if reference_series_instance_uid is not None:
            series_uid_correct = self.series_instance_uid == reference_series_instance_uid

        # Get SOP instance UID.
        reference_sop_instance_uid = None
        if isinstance(image, ImageDicomFile) and image.sop_instance_uid is not None:
            reference_sop_instance_uid = [image.sop_instance_uid]
        elif isinstance(image, ImageDicomFileStack) and image.sop_instance_uid is not None:
            reference_sop_instance_uid = image.sop_instance_uid

        sop_uid_correct = False
        if reference_sop_instance_uid is not None:
            referenced_sop_uids = []

            # Extract the SOP instance UIDs from images that are referenced to from the contour sequence.
            for contour_sequence in roi_contour_sequence[(0x3006, 0x0040)]:
                if has_pydicom_meta_tag(dcm_seq=contour_sequence, tag=(0x3006, 0x0016)):
                    for contour_image_sequence in contour_sequence[(0x3006, 0x0016)]:
                        referenced_sop_uids += [
                            get_pydicom_meta_tag(
                                dcm_seq=contour_image_sequence,
                                tag=(0x0008, 0x1155),
                                tag_type="str"
                            )
                        ]

            # Extract the SOP instance UIDs from images that are referenced to from the referenced frame of reference
            # sequence.
            if has_pydicom_meta_tag(dcm_seq=self.image_metadata, tag=(0x3006, 0x0010)):
                if has_pydicom_meta_tag(dcm_seq=self.image_metadata[(0x3006, 0x0010)][0], tag=(0x3006, 0x0012)):
                    for rt_referenced_series_sequence in self.image_metadata[(0x3006, 0x0010)][0][(0x3006, 0x0012)][0][(0x3006, 0x0014)]:
                        for contour_image_sequence in rt_referenced_series_sequence[(0x3006, 0x0016)]:
                            referenced_sop_uids += [
                                get_pydicom_meta_tag(
                                    dcm_seq=contour_image_sequence,
                                    tag=(0x0008, 0x1155),
                                    tag_type="str"
                                )
                            ]

            referenced_sop_uids = [referenced_sop_uid for referenced_sop_uid in referenced_sop_uids if referenced_sop_uid is not None]
            if len(referenced_sop_uids) > 1:
                sop_uid_correct = set(reference_sop_instance_uid) >= set(referenced_sop_uids)

        # Convert contours in the contour sequence to internal contour objects.
        contour_objects = self._collect_contours(roi_contour_sequence=roi_contour_sequence)
        if contour_objects is None:
            return True, True, True

        # Convert contour points (world space) to voxel space.
        contour_objects = [contour.to_voxel_coordinates(image=image) for contour in contour_objects]

        # Check that within each plane a) each z-coordinate is constant, and b) maps to an integer value (i.e. the
        # slice number in the reference image).
        orientation_correct = True
        position_correct = True
        for contour in contour_objects:
            for sub_contour in contour.contour:
                # Check position
                slice_position = sub_contour[0, 0]
                if not np.allclose(sub_contour[:, 0], slice_position):
                    orientation_correct = False
                if not np.isclose(np.round(slice_position), slice_position):
                    position_correct = False

        if not orientation_correct:
            return False, orientation_correct, position_correct
        if not position_correct:
            return False, orientation_correct, position_correct

        # Check that, if a mask is spread across multiple slices, at least two slices are adjacent OR the contours
        # refer to the series instance UID or to the SOP instance UID.
        slice_position = [sub_contour[0, 0] for contour in contour_objects for sub_contour in contour.contour]
        slice_position = np.unique(slice_position)

        # In case only one slice is present, we should attempt to compute differences.
        if len(slice_position) == 1:
            return True, True, True

        # Check if all slices are non-adjacent.
        if not np.isclose(np.min(np.diff(slice_position)), 1.0) and not sop_uid_correct and not series_uid_correct:
            return False, True, False

        return True, True, True

    def _create_image_from_contour(
            self,
            roi_contour_sequence: pydicom.Dataset,
            image: ImageFile,
            use_orientation: bool,
            use_position: bool
    ) -> ImageFile:
        # Local copy of image.
        image = image.copy()

        # Convert contours in the contour sequence to internal contour objects.
        contour_objects = self._collect_contours(roi_contour_sequence=roi_contour_sequence)
        use_orientation = False

        # Determine orientation from roi contours.
        if not use_orientation:

            points = np.vstack([sub_contour for sub_contour in contour_objects[0].contour])
            svd = np.linalg.svd(points.T - np.mean(points.T, axis=1, keepdims=True))

            # Extract the left singular U vectors.
            mask_orientation = svd.U[:, (2, 1, 0)]

            # Check right-handedness
            if np.linalg.det(mask_orientation) == -1.0:
                for ii in range(3):
                    if np.sum(mask_orientation[:, ii]) < 0.0:
                        mask_orientation[:, ii] *= -1.0

        else:
            mask_orientation = copy.deepcopy(image.image_orientation)

        if not use_position:
            # Convert to normalised space: all segments are projected into their planes.
            contours = [
                image.to_voxel_coordinates(
                    x=sub_contour.T,
                    origin=np.array([0.0, 0.0, 0.0]),
                    orientation=mask_orientation,
                    spacing=np.array([1.0, 1.0, 1.0])
                ).T
                for contour in contour_objects
                for sub_contour in contour.contour
            ]

            # Determine sample spacing.
            mask_z_spacing = np.min(np.diff(np.unique(np.vstack(contours)[:, 0])))
            mask_y_spacing = image.image_spacing[1]
            mask_x_spacing = image.image_spacing[2]
            mask_spacing = tuple([mask_z_spacing, mask_y_spacing, mask_x_spacing])

            # Determine origin.
            mask_z_origin = np.min(np.unique(np.vstack(contours)[:, 0]))
            mask_y_origin = np.min(np.unique(np.vstack(contours)[:, 1])) - mask_y_spacing
            mask_x_origin = np.min(np.unique(np.vstack(contours)[:, 2])) - mask_x_spacing
            mask_origin = tuple([mask_z_origin, mask_y_origin, mask_x_origin])

            # Determine dimensions.
            mask_z_dimension = int(np.ceil((np.max(np.unique(np.vstack(contours)[:, 0])) - mask_z_origin) / mask_z_spacing))
            mask_y_dimension = int(np.ceil((np.max(np.unique(np.vstack(contours)[:, 1])) - mask_y_origin) / mask_y_spacing)) + 1
            mask_x_dimension = int(np.ceil((np.max(np.unique(np.vstack(contours)[:, 2])) - mask_x_origin) / mask_x_spacing)) + 1
            mask_dimension = tuple([mask_z_dimension, mask_y_dimension, mask_x_dimension])
            
        else:
            mask_spacing = copy.deepcopy(image.image_spacing)
            mask_origin = copy.deepcopy(image.image_origin)
            mask_dimension = copy.deepcopy(image.image_dimension)

        # Update image
        image.image_spacing = mask_spacing
        image.image_origin = mask_origin
        image.image_dimension = mask_dimension
        image.image_orientation = mask_orientation

        return image

    def convert_contour_to_mask(
            self,
            roi_contour_sequence: pydicom.Dataset,
            image: ImageFile,
            draw_method: str = "ray_cast",
            disconnected_segments: str = "keep_as_is"
    ) -> None | np.ndarray:

        if image is None:
            return None

        # Initialise a list to contain contour data.
        contour_objects = self._collect_contours(roi_contour_sequence=roi_contour_sequence)
        if contour_objects is None:
            return None

        # Create an empty roi volume
        roi_mask = np.zeros(image.image_dimension, dtype=bool)

        # Create empty slice and mask lists.
        slice_list = []
        mask_list = []

        # Convert contour points (world space) to voxel space.
        contour_objects = [contour.to_voxel_coordinates(image=image) for contour in contour_objects]

        # Merge contours that belong to the same slice. Each contour should belong to a single slice, but multiple
        # contours of the same region of interest may be present in one slice. We therefore collect the contours for
        # each slice first.
        contour_objects = self._merge_contours(contour_objects)

        # Iterate over contours to fill out the mask
        for contour in contour_objects:

            # Ray casting method to draw segmentation map based on polygon contour - this is currently the only
            # implemented method.
            if draw_method == "ray_cast":
                contour_slice_list, contour_mask_list = contour.contour_to_grid_ray_cast(image=image)

                slice_list += contour_slice_list
                mask_list += contour_mask_list
            else:
                raise NotImplementedError(
                    f"The requested method ({draw_method}) for converting contours to a segmentation mask is not "
                    f"available. Currently available: ray_cast"
                )

        if len(slice_list) == 0:
            return None

        # Match slices in image.
        known_slice_positions = image.get_slice_position()
        slice_list = [
            self._match_slice_position(
                slice_position=slice_position,
                known_position=known_slice_positions,
                image_spacing_z=image.image_spacing[0]
            )
            for slice_position in slice_list
        ]

        # Retain mask and slice indices for slices that were matched.
        mask_list = [
            mask_list[ii]
            for ii, slice_position in enumerate(slice_list)
            if slice_position is not None
        ]
        slice_list = [slice_position for slice_position in slice_list if slice_position is not None]

        if len(slice_list) == 0:
            return None

        # Check for out-of-range slices.
        mask_list = [mask_list[ii] for ii, _ in enumerate(mask_list) if slice_list[ii] >= 0]
        slice_list = [slice_id for slice_id in slice_list if slice_id >= 0]

        if len(slice_list) == 0:
            return None

        # Identify any slices that lie outside the negative or positive z-range.
        mask_list = [mask_list[ii] for ii, _ in enumerate(mask_list) if abs(slice_list[ii]) < image.image_dimension[0]]
        slice_list = [slice_id for slice_id in slice_list if slice_id < image.image_dimension[0]]

        if len(slice_list) == 0:
            return None

        # Iterate over the elements in the slice list to set the mask.
        for ii in np.arange(len(slice_list)):
            slice_id = slice_list[ii]
            roi_mask[slice_id, :, :] = np.logical_or(roi_mask[slice_id, :, :], mask_list[ii])

        if disconnected_segments == "keep_largest":
            # Check if the created roi mask consists of multiple, separate segments, and keep only the largest.
            import skimage.measure

            # Label regions
            roi_label_mask, n_regions = skimage.measure.label(roi_mask, connectivity=2, return_num=True)

            # Determine size of regions
            roi_sizes = np.zeros(n_regions)
            for ii in np.arange(start=0, stop=n_regions):
                roi_sizes[ii] = np.sum(roi_label_mask == ii + 1)

            # Select largest region
            roi_mask = roi_label_mask == np.argmax(roi_sizes) + 1

        return roi_mask

    @staticmethod
    def _collect_contours(roi_contour_sequence: pydicom.Dataset) -> Union[None, List[ContourClass]]:
        contour_objects = []

        for contour_sequence in roi_contour_sequence[(0x3006, 0x0040)]:
            # Check if the geometric type exists (3006, 0042)
            if not get_pydicom_meta_tag(dcm_seq=contour_sequence, tag=(0x3006, 0x0042), test_tag=True):
                continue

            # Check if the geometric type equals "CLOSED_PLANAR"
            if get_pydicom_meta_tag(
                    dcm_seq=contour_sequence,
                    tag=(0x3006, 0x0042),
                    tag_type="str") != "CLOSED_PLANAR":
                continue

            # Check if contour data exists (3006, 0050)
            if not get_pydicom_meta_tag(dcm_seq=contour_sequence, tag=(0x3006, 0x0050), test_tag=True):
                continue

            # Read contour data.
            contour_data = np.array(
                get_pydicom_meta_tag(dcm_seq=contour_sequence, tag=(0x3006, 0x0050), tag_type="mult_float"),
                dtype=np.float64)
            contour_data = contour_data.reshape((-1, 3))

            # Determine if there is an offset (3006, 0045)
            contour_offset = np.array(
                get_pydicom_meta_tag(
                    dcm_seq=contour_sequence,
                    tag=(0x3006, 0x0045),
                    tag_type="mult_float",
                    default=[0.0, 0.0, 0.0]
                ),
                dtype=np.float64)

            # Remove the offset from the data
            contour_data -= contour_offset

            # Store as contour.
            contour = ContourClass(contour=contour_data)

            # Add contour data to the contour list
            contour_objects += [contour]

        if len(contour_objects) == 0:
            return None

        return contour_objects

    @staticmethod
    def _merge_contours(contour_objects: List[ContourClass]):
        """This function collects contours from the same slice."""
        # Find slice ids for each contour object.
        slice_ids = [contour.which_slice() for contour in contour_objects]

        unique_slice_ids: List[int] = list(np.unique(np.concatenate(slice_ids)))

        merged_contour_list = []

        for slice_id in unique_slice_ids:
            parent_contour = None
            merging_contours = None
            for ii in range(len(contour_objects)):
                if slice_id not in slice_ids[ii]:
                    continue

                if parent_contour is None:
                    parent_contour = contour_objects[ii]
                elif merging_contours is None:
                    merging_contours = [contour_objects[ii]]
                else:
                    merging_contours += [contour_objects[ii]]

            merged_contour_list += [
                parent_contour.merge(other_contours=merging_contours, slice_id=slice_id)]

        return merged_contour_list

    @staticmethod
    def _match_slice_position(slice_position, known_position, image_spacing_z):
        # Match slice position of mask with any known slice position.
        img_slice_position = slice_position * image_spacing_z
        position_difference = np.around(np.abs(img_slice_position - known_position), 3)

        # Check if there is any matching position.
        if np.any(position_difference == 0.0):
            int_slice_position = int(np.argwhere(position_difference == 0.0))
        else:
            int_slice_position = None

        return int_slice_position

    def export_roi_labels(self):

        self.load_metadata()

        # Find which roi numbers (3006,0022) are associated with which roi names (3004,0024).
        labels = [
            get_pydicom_meta_tag(
                dcm_seq=current_structure_set_roi_sequence, tag=(0x3006, 0x0026), tag_type="str", default=None)
            for current_structure_set_roi_sequence in self.image_metadata[(0x3006, 0x0020)]
        ]

        n_labels = max([1, len(labels)])

        if len(labels) == 0:
            labels = [None]

        return {
            "sample_name": [self.sample_name] * n_labels,
            "dir_path": [self.dir_path] * n_labels,
            "file_path": [self.file_name] * n_labels,
            "roi_label": labels
        }
