import logging
import sys
import warnings

from typing import Optional, Tuple, List

from mirp.importSettings import SettingsClass
from mirp.importData.imageGenericFile import ImageFile
from mirp.importData.readData import read_image_and_masks
from mirp.images.genericImage import GenericImage
from mirp.masks.baseMask import BaseMask

class BaseWorkflow:
    def __init__(
            self,
            image_file: ImageFile
    ):
        self.image_file = image_file


class StandardWorkflow(BaseWorkflow):
    def __init__(
            self,
            image_file: ImageFile,
            settings: SettingsClass,
            noise_iteration_id: Optional[int] = None,
            rotation: Optional[float] = None,
            translation: Optional[Tuple[float, ...]] = None,
            new_image_spacing: Optional[Tuple[float, ...]] = None
    ):

        super().__init__(
            image_file=image_file
        )

        self.settings = settings
        self.noise_iteration_id = noise_iteration_id
        self.rotation = rotation
        self.translation = translation
        self.new_image_spacing = new_image_spacing

    def standard_image_processing(self) -> Optional[Tuple[GenericImage, BaseMask]]:
        from mirp.imageProcess import crop, alter_mask, randomise_mask, split_masks

        # Configure logger
        logging.basicConfig(
            format="%(levelname)s\t: %(processName)s \t %(asctime)s \t %(message)s",
            level=logging.INFO, stream=sys.stdout)

        # Notify
        logging.info(self._message_computation_initialisation())

        # Read image and masks.
        image, masks = read_image_and_masks(self.image_file, to_numpy=False)

        if masks is None or len(masks) == 0:
            warnings.warn("No segmentation masks were read.")
            return

        # Add type hints and remove masks that are empty.
        masks: List[BaseMask] = [mask for mask in masks if not mask.is_empty() and not mask.roi.is_empty_mask()]
        if len(masks) == 0:
            warnings.warn("No segmentation masks were read.")
            return

        # Select the axial slice with the largest portion of the ROI.
        if self.settings.general.select_slice == "largest" and self.settings.general.by_slice:
            [mask.select_largest_slice() for mask in masks]

        # Crop slice stack
        if self.settings.perturbation.crop_around_roi:
            image, masks = crop(image=image, masks=masks, boundary=self.settings.perturbation.crop_distance)

        # Extract diagnostic features from initial image and rois
        # self.extract_diagnostic_features(img_obj=img_obj, roi_list=roi_list, append_str="init")

        ########################################################################################################
        # Bias field correction and normalisation
        ########################################################################################################

        # Create a tissue mask
        if self.settings.post_process.bias_field_correction or \
                not self.settings.post_process.intensity_normalisation == "none":
            tissue_mask = create_tissue_mask(img_obj=img_obj, settings=curr_setting)

            # Perform bias field correction
            if self.settings.post_process.bias_field_correction:
                image.bias_field_correction(
                    n_fitting_levels=self.settings.post_process.n_fitting_levels,
                    n_max_iterations=self.settings.post_process.n_max_iterations,
                    convergence_threshold=self.settings.post_process.convergence_threshold,
                    mask=tissue_mask,
                    in_place=True
                )

            image.normalise_intensities(
                normalisation_method=self.settings.post_process.intensity_normalisation,
                intensity_range=self.settings.post_process.intensity_normalisation_range,
                saturation_range=self.settings.post_process.intensity_normalisation_saturation,
                mask=tissue_mask)

        ########################################################################################################
        # Determine image noise levels
        ########################################################################################################

        # Estimate noise level.
        estimated_noise_level = self.settings.perturbation.noise_level
        if self.settings.perturbation.add_noise and estimated_noise_level is None:
            estimated_noise_level = image.estimate_noise()

        if self.settings.perturbation.add_noise:
            image.add_noise(noise_level=estimated_noise_level, noise_iteration_id=self.noise_iteration_id)

        ########################################################################################################
        # Interpolation of base image
        ########################################################################################################

        # Translate, rotate and interpolate image
        image.interpolate(
            by_slice=self.settings.img_interpolate.interpolate,
            new_spacing=self.new_image_spacing,
            translation=self.translation,
            rotation=self.rotation,
            spline_order=self.settings.img_interpolate.spline_order,
            anti_aliasing=self.settings.img_interpolate.anti_aliasing,
            anti_aliasing_smoothing_beta=self.settings.img_interpolate.smoothing_beta
        )
        [mask.register(
                image=image,
                spline_order=self.settings.roi_interpolate.spline_order,
                anti_aliasing=self.settings.img_interpolate.anti_aliasing,
                anti_aliasing_smoothing_beta=self.settings.img_interpolate.smoothing_beta
            ) for mask in masks]

        # self.extract_diagnostic_features(img_obj=img_obj, roi_list=roi_list, append_str="interp")

        ########################################################################################################
        # Mask-based operations
        ########################################################################################################

        # Adapt roi sizes by dilation and erosion.
        masks = alter_mask(
            masks=masks,
            alteration_size=self.settings.perturbation.roi_adapt_size,
            alteration_method=self.settings.perturbation.roi_adapt_type,
            max_erosion=self.settings.perturbation.max_volume_erosion,
            by_slice=self.settings.general.by_slice
        )

        # Update roi using SLIC
        if self.settings.perturbation.randomise_roi:
            masks = randomise_mask(
                image=image,
                masks=masks,
                repetitions=self.settings.perturbation.roi_random_rep,
                by_slice=self.settings.general.by_slice
            )

        # Extract boundaries and tumour bulk
        masks = split_masks(
            masks=masks,
            boundary_sizes=self.settings.perturbation.roi_boundary_size,
            max_erosion=self.settings.perturbation.max_volume_erosion,
            by_slice=self.settings.general.by_slice
        )

        # Resegmentise masks.
        [mask.resegmentise_mask(
            image=image,
            resegmentation_method=self.settings.roi_resegment.resegmentation_method,
            intensity_range=self.settings.roi_resegment.intensity_range,
            sigma=self.settings.roi_resegment.sigma
        ) for mask in masks]

        # self.extract_diagnostic_features(img_obj=img_obj, roi_list=roi_list, append_str="reseg")

        ########################################################################################################
        # Base image
        ########################################################################################################

        for mask in masks:
            yield image, mask

        ########################################################################################################
        # Response maps
        ########################################################################################################

        if self.settings.img_transform.spatial_filters is not None:
            for transformed_image in self.transform_images(image=image):
                for mask in masks:
                    yield transformed_image, mask

    def transform_images(self, image: GenericImage):
        # Check if image transformation is required
        if self.settings.img_transform.spatial_filters is None:
            return

        # Get spatial filters to apply
        spatial_filter = self.settings.img_transform.spatial_filters

        # Iterate over spatial filters
        for current_filter in spatial_filter:

            if self.settings.img_transform.has_separable_wavelet_filter(x=current_filter):
                # Separable wavelet filters
                from mirp.imageFilters.separableWaveletFilter import SeparableWaveletFilter
                filter_obj = SeparableWaveletFilter(settings=self.settings, name=current_filter)

            elif self.settings.img_transform.has_nonseparable_wavelet_filter(x=current_filter):
                # Non-separable wavelet filters
                from mirp.imageFilters.nonseparableWaveletFilter import NonseparableWaveletFilter
                filter_obj = NonseparableWaveletFilter(settings=self.settings, name=current_filter)

            elif self.settings.img_transform.has_gaussian_filter(x=current_filter):
                # Gaussian filters
                from mirp.imageFilters.gaussian import GaussianFilter
                filter_obj = GaussianFilter(settings=self.settings, name=current_filter)

            elif self.settings.img_transform.has_laplacian_of_gaussian_filter(x=current_filter):
                # Laplacian of Gaussian filters
                from mirp.imageFilters.laplacianOfGaussian import LaplacianOfGaussianFilter
                filter_obj = LaplacianOfGaussianFilter(settings=self.settings, name=current_filter)

            elif self.settings.img_transform.has_laws_filter(x=current_filter):
                # Laws' kernels
                from mirp.imageFilters.lawsFilter import LawsFilter
                filter_obj = LawsFilter(settings=self.settings, name=current_filter)

            elif self.settings.img_transform.has_gabor_filter(x=current_filter):
                # Gabor kernels
                from mirp.imageFilters.gaborFilter import GaborFilter
                filter_obj = GaborFilter(settings=self.settings, name=current_filter)

            elif self.settings.img_transform.has_mean_filter(x=current_filter):
                # Mean / uniform filter
                from mirp.imageFilters.meanFilter import MeanFilter
                filter_obj = MeanFilter(settings=self.settings, name=current_filter)

            else:
                raise ValueError(
                    f"{current_filter} is not implemented as a spatial filter. Please use one of ",
                    ", ".join(self.settings.img_transform.get_available_image_filters())
                )

            for current_filter_object in filter_obj.generate_object():
                # Create a response map.
                response_map = current_filter_object.transform(img_obj=img_obj)

                yield response_map