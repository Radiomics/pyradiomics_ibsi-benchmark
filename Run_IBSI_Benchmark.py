#!/usr/bin/env python
import ast
import logging
import os

import numpy as np
import pandas as pd
import SimpleITK as sitk
import six

import radiomics
from radiomics import setVerbosity, featureextractor

if not os.path.isdir("results"):
  os.mkdir("results")

IBSI_BINNING = False
IBSI_RESAMPLING = False


def IBSI_binning(parameterValues, **kwargs):
  ibsiLogger = logging.getLogger('radiomics.ibsi')

  binWidth = kwargs.get('binWidth', 25)
  binCount = kwargs.get('binCount')
  resegmentRange = kwargs.get('resegmentRange')
  resegmentMode = kwargs.get('resegmentMode', 'absolute')

  if binCount is not None:
    binEdges = np.histogram(parameterValues, binCount)[1]
    binEdges[-1] += 1  # Ensures that the maximum value is included in the topmost bin when using numpy.digitize
  else:
    minimum = min(parameterValues)
    maximum = max(parameterValues)

    # Start binning form the first value lesser than or equal to the minimum value and evenly dividable by binwidth
    lowBound = minimum - (minimum % binWidth)
    # Add + 2* binwidth to ensure the maximum value is included in the range generated by numpy.arange, and that values
    # equal to highbound are binned into a separate bin by numpy.histogram (This ensures ALL bins are half open, as
    # numpy.histogram treats the last bin as a closed interval. Moreover, this ensures consistency with numpy.digitize,
    # which will assign len(bins) + 1 to values equal to rightmost bin edge, treating all bins as half-open)
    highBound = maximum + 2 * binWidth

    # #####################################
    # IBSI difference
    # #####################################
    if resegmentRange is not None:
      if resegmentMode == 'absolute':
        lowBound = min(resegmentRange)
      elif resegmentMode == 'sigma':
        lowBound = minimum
    # #####################################

    binEdges = np.arange(lowBound, highBound, binWidth)

    # if min(parameterValues) % binWidth = 0 and min(parameterValues) = max(parameterValues), binEdges will only contain
    # 1 value. If this is the case (flat region) ensure that numpy.histogram creates 1 bin (requires 2 edges). For
    # numpy.histogram, a binCount (1) would also suffice, however, this is not accepted by numpy.digitize, which also uses
    # binEdges calculated by this function.
    if len(binEdges) == 1:  # Flat region, ensure that there is 1 bin
      binEdges = [binEdges[0] - .5, binEdges[0] + .5]  # Simulates binEdges returned by numpy.histogram if bins = 1

    ibsiLogger.debug('Calculated %d bins for bin width %g with edges: %s)', len(binEdges) - 1, binWidth, binEdges)

  return binEdges  # numpy.histogram(parameterValues, bins=binedges)


def IBSI_resampling(image, mask, **kwargs):
  ibsiLogger = logging.getLogger('radiomics.ibsi')
  # resample image to new spacing, align centers of both resampling grids.
  spacing = kwargs.get('resampledPixelSpacing')
  grayValuePrecision = kwargs.get('grayValuePrecision')
  interpolator = kwargs.get('interpolator', sitk.sitkLinear)

  try:
    if isinstance(interpolator, six.string_types):
      interpolator = getattr(sitk, interpolator)
  except Exception:
    ibsiLogger.warning('interpolator "%s" not recognized, using sitkLinear', interpolator)
    interpolator = sitk.sitkLinear

  im_spacing = np.array(image.GetSpacing(), dtype='float')
  im_size = np.array(image.GetSize(), dtype='float')

  spacing = np.where(np.array(spacing) == 0, im_spacing, spacing)

  spacingRatio = im_spacing / spacing
  newSize = np.ceil(im_size * spacingRatio)

  # Calculate center in real-world coordinates
  im_center = image.TransformContinuousIndexToPhysicalPoint((im_size - 1) / 2)

  new_origin = tuple(np.array(image.GetOrigin()) + 0.5 * ((im_size - 1) * im_spacing - (newSize - 1) * spacing))

  ibsiLogger.info('Resampling from %s to %s (size %s to %s), aligning Centers', im_spacing, spacing, im_size, newSize)

  rif = sitk.ResampleImageFilter()
  rif.SetOutputOrigin(new_origin)
  rif.SetSize(np.array(newSize, dtype='int').tolist())
  rif.SetOutputDirection(image.GetDirection())
  rif.SetOutputSpacing(spacing)

  rif.SetOutputPixelType(sitk.sitkFloat32)
  rif.SetInterpolator(interpolator)
  res_im = rif.Execute(sitk.Cast(image, sitk.sitkFloat32))

  # Round to n decimals (0 = to nearest integer)
  if grayValuePrecision is not None:
    ibsiLogger.debug('Rounding Image Gray values to %d decimals', grayValuePrecision)
    im_arr = sitk.GetArrayFromImage(res_im)
    im_arr = np.round(im_arr, grayValuePrecision)
    round_im = sitk.GetImageFromArray(im_arr)
    round_im.CopyInformation(res_im)
    res_im = round_im

  # Sanity check: Compare Centers!
  new_center = res_im.TransformContinuousIndexToPhysicalPoint((newSize - 1) / 2)
  ibsiLogger.debug("diff centers: %s" % np.abs(np.array(im_center) - np.array(new_center)))

  rif.SetOutputPixelType(sitk.sitkFloat32)
  rif.SetInterpolator(sitk.sitkLinear)
  res_ma = rif.Execute(sitk.Cast(mask, sitk.sitkFloat32))
  res_ma = sitk.BinaryThreshold(res_ma, lowerThreshold=0.5)

  return res_im, res_ma


if IBSI_BINNING:
  radiomics.imageoperations.getBinEdges = IBSI_binning
if IBSI_RESAMPLING:
  radiomics.imageoperations.resampleImage = IBSI_resampling

rLogger = radiomics.logger
logHandler = logging.FileHandler(filename='results/IBSIlog.log', mode='w')
logHandler.setLevel(logging.INFO)
logHandler.setFormatter(logging.Formatter('%(levelname)-.1s: %(name)s: %(message)s'))

rLogger.setLevel(logging.INFO)
rLogger.addHandler(logHandler)

setVerbosity(logging.INFO)


class IbsiBenchmark:
  def __init__(self, mapping_file, **kwargs):
    self.logger = logging.getLogger('radiomics.ibsi')
    self.mapping = pd.read_csv(mapping_file)
    self.mapping['required_settings'] = self.mapping['required_settings'].apply(self._parse_dict)

    self.ibsi_settings = kwargs

  def run_phantom(self):
    self.logger.info('################################### Extracting Phantom #############################')
    extractor = featureextractor.RadiomicsFeatureExtractor()

    extraction_types = self.ibsi_settings.get('extraction_types', ['avg', 'comb'])
    correct_kurtosis = self.ibsi_settings.get('correct_kurtosis', True)

    data_dir = os.path.join('data', 'phase_1_digital_phantom')

    image = sitk.ReadImage(os.path.join(data_dir, 'Phantom.nrrd'))
    mask = sitk.ReadImage(os.path.join(data_dir, 'Phantom-label.nrrd'))

    result_series = pd.Series()
    extraction_dimensionality = ('2D', '3D')
    for d in extraction_dimensionality:
      self.logger.info('######################### Dimensionality %s ####################' % d)
      for t in extraction_types:
        self.logger.info('######################### TYPE %s ####################' % t)
        params = os.path.join(data_dir, 'configuration', 'Phantom_%s.yml' % t)
        if not os.path.isfile(params):
          self.logger.warning('unable to find config file %s', params)
          continue
        extractor.loadParams(params)
        extractor.addProvenance(provenance_on=((t == 'avg') & (d == '2D')))
        if d == '3D':
          extractor.enableFeatureClassByName('firstorder', False)
          extractor.enableFeatureClassByName('shape', False)
        extractor.settings['force2D'] = d == '2D'

        fv = pd.Series()
        for k, v in six.iteritems(extractor.execute(image, mask)):
          for ibsi_name, ibsi_value in self._translate_to_ibsi(k, v, extractor.settings):
            fv[ibsi_name] = ibsi_value

        result_series = result_series.append(fv)

    if correct_kurtosis and 'stat_kurt' in result_series:
      result_series['stat_kurt'] -= 3

    result_series.name = 'phantom'

    return result_series

  def run_phase2(self,):
    data_dir = os.path.join('data', 'phase_2_patient_cases')

    extraction_types = self.ibsi_settings.get('extraction_types', ['avg', 'comb'])
    gray_value_rounding = self.ibsi_settings.get('gray_value_rounding', False)
    correct_kurtosis = self.ibsi_settings.get('correct_kurtosis', True)

    cases = ('A', 'B', 'C', 'D', 'E')
    image = sitk.ReadImage(os.path.join(data_dir, 'PAT1', 'PAT1.nrrd'))
    mask = sitk.ReadImage(os.path.join(data_dir, 'PAT1', 'GTV.nrrd'))

    for case_idx in cases:

      self.logger.info('################################## Extracting Case %s ############################' % case_idx)
      extractor = featureextractor.RadiomicsFeatureExtractor()

      result_series = pd.Series()
      for t in extraction_types:
        self.logger.info('######################### TYPE %s ####################' % t)

        params = os.path.join(data_dir, 'configuration', 'config_%s_%s.yml' % (case_idx, t))
        if not os.path.isfile(params):
          self.logger.warning('unable to find config file %s', params)
          continue

        extractor.loadParams(params)
        if gray_value_rounding:
          extractor.settings['grayValuePrecision'] = 0  # round to nearest integer when using IBSI resampling
        extractor.addProvenance(provenance_on=(t == 'avg'))

        fv = pd.Series()
        for k, v in six.iteritems(extractor.execute(image, mask)):
          for ibsi_name, ibsi_value in self._translate_to_ibsi(k, v, extractor.settings):
            fv[ibsi_name] = ibsi_value

        result_series = result_series.append(fv)

      if correct_kurtosis and 'stat_kurt' in result_series:
        result_series['stat_kurt'] -= 3

      result_series.name = 'config_' + case_idx

      yield result_series

  def run_phase3(self):
    global IBSI_RESAMPLING
    data_dir = os.path.join('data', 'phase_3_sarcoma')
    gray_value_rounding = self.ibsi_settings.get('gray_value_rounding', False)
    correct_kurtosis = self.ibsi_settings.get('correct_kurtosis', True)

    cases = pd.read_csv(os.path.join(data_dir, 'cases.csv'))
    params_dir = os.path.join(data_dir, 'configuration')

    results = pd.DataFrame()

    modalities = cases.groupby('Modality')
    for m in modalities.groups.keys():
      assert os.path.isfile(os.path.join(params_dir, m + '.yml'))

    for mod, cases in modalities:
      params_file = os.path.join(params_dir, mod + '.yml')
      extractor = featureextractor.RadiomicsFeatureExtractor(params_file)

      if gray_value_rounding and IBSI_RESAMPLING:
        extractor.settings['grayValuePrecision'] = 0  # round to nearest integer when using IBSI resampling

      for case_idx, case in cases.iterrows():
        image = os.path.join(data_dir, case['Image'])
        mask = os.path.join(data_dir, case['Mask'])
        fv = pd.Series(case)
        for k, v in six.iteritems(extractor.execute(image, mask)):
          for ibsi_name, ibsi_value in self._translate_to_ibsi(k, v, extractor.settings):
            fv[ibsi_name] = ibsi_value

        if correct_kurtosis and 'stat_kurt' in fv:
          fv['stat_kurt'] -= 3

        fv.name = case_idx

        results = results.append(fv)
    return results


  def _parse_dict(self, str_val):
    if pd.isnull(str_val):
      return str_val

    return ast.literal_eval(str_val)

  def _translate_to_ibsi(self, featureName, featureValue, settings):
    ibsi_features = self.mapping[self.mapping['pyradiomics_feature'] == featureName]
    if ibsi_features.shape[0] == 0:
      return
    elif ibsi_features.shape[0] == 1:
      yield ibsi_features['ibsi_name'].iloc[0], featureValue
    elif not pd.isnull(ibsi_features['idx'].iloc[0]):
      for row_idx, row in ibsi_features.iterrows():
        yield row['ibsi_name'], featureValue[int(row['idx'])]
    else:
      for row_idx, row in ibsi_features.iterrows():
        match = True
        if not pd.isnull(row['required_settings']):
          for s in row['required_settings']:
            if not settings[s] == row['required_settings'][s]:
              match = False
              break
        if match:
          yield row['ibsi_name'], featureValue


if __name__ == '__main__':
  results_folder = 'results/%s' % radiomics.__version__[:5]
  if not os.path.isdir(results_folder):
    os.makedirs(results_folder)

  mapping_file = os.path.join('mapping', 'mapping.csv')
  benchmark = IbsiBenchmark(mapping_file)

  results_phase_3 = benchmark.run_phase3()
  results_phase_3.to_csv('%s/results_phase3.csv' % results_folder)
  exit(0)
  results_phantom = benchmark.run_phantom()
  results_phantom.sort_index().to_csv('%s/results_phantom.csv' % results_folder)
  # exit(0)
  for results_case in benchmark.run_phase2():
    results_case.to_csv('%s/results_%s.csv' % (results_folder, results_case.name))

  exit(0)
