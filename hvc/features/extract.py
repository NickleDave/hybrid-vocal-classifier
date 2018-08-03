import os
import warnings
from glob import glob

# from dependencies
import numpy as np
from scipy.io import wavfile
from sklearn.externals import joblib

from ..utils import timestamp, write_select_config, annotation
from ..audiofileIO import Spectrogram, Segmenter, make_syls
from .feature_dicts import single_syl_features_switch_case_dict
from .feature_dicts import multiple_syl_features_switch_case_dict
from .feature_dicts import neural_net_features_switch_case_dict
from .. import evfuncs
from ..koumura import to_csv


class FeatureExtractor:
    """class for Feature Extraction

    Attributes
    ----------
    spect_params : dict
        parameters used to compute spectrogram of audio file
    feature_list : list
        list of features to extract
        e.g., ['amplitude', 'duration', 'spectral entropy', 'spectral kurtosis']
    feature_list_group_ID : numpy ndarray
        same length as feature_list, each element is an int that identifies which group
        of features the feature in feature_list belongs to
        e.g., [0, 0, 0, 1]
    feature_group_ID_dict : dict
        map from ints in feature_list_group_ID to feature group names
        e.g. {0: 'knn', 1: 'svm'}
    segment_params : dict
        parameters used to segment audio file into syllables

    Methods
    -------
    extract(labels_to_use='all', data_dirs=None, data_dirs_validated=False,
            file_format=None, annotation_file=None, segment=False, output_dir=None,
            make_output_subdir=True, make_summary_file=True, return_extract_dict=True)
        extracts features

    _from_file
        helper function, extracts features from a single file
    """

    def __init__(self,
                 spect_params,
                 feature_list,
                 feature_list_group_ID=None,
                 feature_group_ID_dict=None,
                 segment_params=None):
        """
        Parameters
        ----------
        spect_params : dict
            parameters used to create spectrograms from audio files
            as defined for hvc.audiofileIO.Spectrogram class
        feature_list : list
            list of features to extract.
            supplied by user or generated by hvc.parse.extract
        e.g., ['amplitude', 'duration', 'spectral entropy', 'spectral kurtosis']
        feature_list_group_ID : numpy ndarray
            same length as feature_list, each element is an int that identifies which group
            of features the feature in feature_list belongs to
            e.g., [0, 0, 0, 1]
        feature_group_ID_dict : dict
            map from ints in feature_list_group_ID to feature group names
            e.g. {0: 'knn', 1: 'svm'}            
        segment_params : dict
            parameters used to find segments--i.e. syllables--in audio files
            as defined for hvc.audiofileIO.segment_song.
            Not required if user supplies segments (i.e. syllable onsets/offsets).
            Default is None.
        """

        self.spect_params = spect_params
        self.spectrogram_maker = Spectrogram(**self.spect_params)
        if segment_params:
            self.segment_params = segment_params
            self.segmenter = Segmenter(**self.segment_params)
        self.feature_list = feature_list
        if feature_list_group_ID:
            self.feature_list_group_ID = feature_list_group_ID
            self.feature_group_ID_dict = feature_group_ID_dict

    def extract(self,
                labels_to_use='all',
                data_dirs=None,
                data_dirs_validated=False,
                file_format=None,
                annotation_file=None,
                segment=False,
                output_dir=None,
                make_output_subdir=True,
                make_summary_file=True,
                return_extract_dict=True):
        """extract features and save feature files

        Parameters
        ----------
        labels_to_use : str
            set of labels for syllables from which features should be extracted
            specified as one string, e.g., 'iabd' would be the set {'i', 'a', 'b', 'd'}.
            Features will not be extracted from segments with labels not in this set.
            Default is 'all' in which features are extracted from all segments regardless
            of label.
        data_dirs : list
            list of directories with data files
        data_dirs_validated: bool
            If True, does not check with any of the paths in data_dirs are valid.
            Default is False.
            The high-level hvc.extract function sets this to True when the user provides a
            config file.
        file_format : str
            audio file format. Value formats: {'cbin', 'wav'}
        annotation_file : str
            filename of csv file with annotations.
            Must be a comma-separated values (csv) file.
            Default is None.
        segment : bool
            if True, segment audio files into syllables and then extract features from
            those syllables.
            Default is False.
        output_dir : str
            directory in which to save features file.
            Default is None, in which case the feature file is saved in the current
            directory.
        make_output_subdir : bool
            if True, make a subdirectory in output_dir (or current directory) and save the
            feature files in that subdirectory.
            Default is True.
        make_summary_file : bool
            if True, combine feature files from each directory to make a summary file
        return_extract_dict : bool
            if True, return dict that contains all extracted features
        """

        if data_dirs and annotation_file:
            raise ValueError('received values for both data_dirs and '
                             'annotation_file arguments, unclear which to use. '
                             'Please only specify one or the other.')

        if segment:
            if not hasattr(self, 'segment_params'):
                raise ValueError('FeatureExtractor.extract was called with segment=True, '
                                 'but FeatureExtractor does not have segmenting '
                                 'parameters set.')

        # get absolute path to output
        # **before** we change directories
        # so we're putting it where user specified, if user wrote a relative path in 
        # config file
        if output_dir:
            if make_output_subdir:
                output_subdir = 'extract_output_' + timestamp()
                output_dir_with_path = os.path.join(
                    os.path.abspath(
                        os.path.normpath(output_dir)),
                    output_subdir)
            else:
                output_dir_with_path = os.path.abspath(
                    os.path.normpath(output_dir))
            if not os.path.isdir(output_dir_with_path):
                os.makedirs(output_dir_with_path)
            output_dir = output_dir_with_path

        if data_dirs:
            if data_dirs_validated is False:
                validated_data_dirs = []
                for data_dir in data_dirs:
                    cwd = os.getcwd()
                    if not os.path.isdir(data_dir):
                        # if item is not absolute path to dir
                        # try adding item to absolute path to config_file
                        # i.e. assume it is written relative to config file
                        data_dir = os.path.join(
                            os.path.dirname(cwd),
                            os.path.normpath(data_dir))
                        if not os.path.isdir(data_dir):
                            raise ValueError('directory {} in data_dirs is not a valid '
                                             'directory.'.format(data_dir))
                    validated_data_dirs.append(data_dir)
                data_dirs = validated_data_dirs

            # try to auto-discover file format
            if file_format is None:
                os.chdir(data_dirs[0])
                cbins = glob('*.cbin')
                wavs = glob('*.wav')
                if cbins and wavs:
                    raise ValueError('Could not determine file format for feature extract'
                                     ' automatically,found more than one valid format '
                                     'in {}.'.format(data_dirs[0]))
                elif cbins and wavs==[]:
                    print('found .cbin files in {}, will use .cbin as file format'
                          .format(data_dirs[0]))
                    file_format = 'cbin'
                elif wavs and cbins==[]:
                    file_format = 'wav'
                    print('found .wav files in {}, will use .wav as file format'
                          .format(data_dirs[0]))

            if segment:
                annotation_list = []
                if file_format == 'cbin':
                    search_str = '*.cbin'
                elif file_format == 'wav':
                    search_str = '*.wav'
                audio_files = []
                for data_dir in data_dirs:
                    audio_files.extend(glob(os.path.join(data_dir,
                                                    search_str)))
                for audio_file in audio_files:
                    if file_format == 'cbin':
                        raw_audio, samp_freq = evfuncs.load_cbin(audio_file)
                    elif file_format == 'wav':
                        samp_freq, raw_audio = wavfile.read(audio_file)
                        search_str = '*.wav'
                    segment_dict = self.segmenter.segment(raw_audio,
                                                          method='evsonganaly',
                                                          samp_freq=samp_freq)
                    fake_labels = np.full((segment_dict['onsets_s'].shape),
                                          '-')
                    annotation_dict = {'filename': audio_file,
                                       'labels': fake_labels,
                                       'onsets_s': segment_dict['onsets_s'],
                                       'offsets_s': segment_dict['offsets_s']}
                    if 'onsets_Hz' in segment_dict:
                        # onsets_Hz will always be in segment_dict unless someone uses
                        # 'amp' version of segment, but that would make everything else
                        # crash. Neglecting that issue at the moment
                        annotation_dict['onsets_Hz'] = segment_dict['onsets_Hz']
                        annotation_dict['offsets_Hz'] = segment_dict['offsets_Hz']
                    annotation_list.append(annotation_dict)

            elif segment is False:
                # if we are not segmenting songs (e.g. for prediction of unlabeled song)
                # but user passed data_dir instead of annotation_file,
                # look for annotation files (for now just .not.mat files)
                notmats = []
                annot_xmls = []
                for data_dir in data_dirs:
                    notmat_search_str = os.path.join(data_dir, '*.not.mat')
                    notmats_this_dir = glob(notmat_search_str)
                    if notmats_this_dir:
                        notmats.extend(notmats_this_dir)
                    else:
                        if file_format == 'cbin':
                            # if audio files are .cbin, we expect .not.mat files, so raise error
                            raise ValueError('Identified file format as .cbin but did not find '
                                             'files with annotations in data_dir {}.'
                                             .format(data_dir))
                        elif file_format == 'wav':
                            # if audio files are .wav, annotation could be xml files (from Koumura dataset),
                            # try looking in parent directory
                            annot_xml = glob(os.path.join(data_dir,
                                                          '..',
                                                          'Annotation.xml'))
                            if annot_xml:
                                annot_xmls.append(annot_xml)
                            else:
                                raise ValueError('Identified file format as .wav but did not find '
                                                 'files with annotations in data_dir {}.'
                                                 .format(data_dir))

                annotation_list = []  # list of annotation_dicts
                if notmats:
                    for notmat in notmats:
                        annotation_dict = annotation.notmat_to_annot_dict(notmat)
                        annotation_list.append(annotation_dict)

                if annot_xmls:
                    for annot_xml in annot_xmls:
                        annotation_csv = to_csv(annot_xml)
                        annotation_list.extend(annotation.csv_to_list(annotation_csv))

        # if user passed argument for annotation_file, not data_dirs
        elif annotation_file:
            # load annotation file
            # then convert to annotation_list
            annotation_list = annotation.csv_to_annot_list(annotation_file)

        num_songfiles = len(annotation_list)
        all_labels = []
        all_onsets_Hz = []
        all_offsets_Hz = []
        all_sampfreqs = []
        songfiles = []
        songfile_IDs = []
        songfile_ID_counter = 0
        for file_num, annotation_dict in enumerate(annotation_list):
            print('Processing audio file {} of {}.'.format(file_num + 1, num_songfiles))
            # segment_params defined for todo_list item takes precedence over any default
            # defined for `extract` config
            extract_dict = self._from_file(annotation_dict['filename'],
                                           annotation_dict['labels'],
                                           annotation_dict['onsets_Hz'],
                                           annotation_dict['offsets_Hz'],
                                           labels_to_use=labels_to_use)

            if extract_dict is None:
                # because no labels from labels_to_use were found in songfile
                continue

            if 'feature_inds' in extract_dict:
                if 'feature_inds' not in locals():
                    feature_inds = extract_dict['feature_inds']
                else:
                    ftr_inds_err_msg = "feature indices changed between files"
                    assert np.array_equal(feature_inds, extract_dict['feature_inds']), ftr_inds_err_msg

            all_labels.extend(extract_dict['labels'])
            all_onsets_Hz.extend(extract_dict['onsets_Hz'])
            all_offsets_Hz.extend(extract_dict['offsets_Hz'])
            all_sampfreqs.append(extract_dict['samp_freq'])
            songfiles.append(annotation_dict['filename'])
            songfile_IDs.extend(
                [songfile_ID_counter] * extract_dict['onsets_Hz'].shape[0])
            songfile_ID_counter += 1

            if 'features_arr' in extract_dict:
                if 'features_from_all_files' in locals():
                    features_from_all_files = np.concatenate((features_from_all_files,
                                                              extract_dict['features_arr']),
                                                             axis=0)
                else:
                    features_from_all_files = extract_dict['features_arr']

            if 'neuralnet_inputs_dict' in extract_dict:
                if 'neuralnet_inputs_all_files' in locals():
                    for input_type, list_of_input_arr in neuralnet_inputs_all_files.items():
                        list_of_input_arr.append(extract_dict['neuralnet_inputs_dict'][input_type])
                else:
                    neuralnet_inputs_all_files = {}
                    for input_type, input_arr in extract_dict['neuralnet_inputs_dict'].items():
                        neuralnet_inputs_all_files[input_type] = [input_arr]  # make list so we can append

        if make_summary_file:
            feature_file = os.path.join(output_dir,
                                        'features_created_' + timestamp())
            feature_file_dict = {
                'labels': all_labels,
                'onsets_Hz': np.asarray(all_onsets_Hz),
                'offsets_Hz': np.asarray(all_offsets_Hz),
                'feature_list': self.feature_list,
                'spect_params': self.spect_params,
                'segment_params': self.segment_params,
                'labels_to_use': labels_to_use,
                'file_format': file_format,
                'songfiles': songfiles,
                'songfile_IDs': songfile_IDs,
                'all_sampfreqs': all_sampfreqs,
                'annotation_list': annotation_list,
                'feature_extractor': self,
            }

            if 'features_from_all_files' in locals():
                feature_file_dict['features'] = features_from_all_files
                feature_file_dict['features_arr_column_IDs'] = feature_inds
                num_samples = feature_file_dict['features'].shape[0]
                feature_file_dict['num_samples'] = num_samples

                if hasattr(self, 'feature_list_group_ID'):
                    feature_file_dict['feature_list_group_ID'] = self.feature_list_group_ID
                    feature_file_dict['feature_group_ID_dict'] = self.feature_group_ID_dict

            if 'neuralnet_inputs_all_files' in locals():
                for input_type, input_list in neuralnet_inputs_all_files.items():
                    concatenated = np.concatenate(input_list)
                    neuralnet_inputs_all_files[input_type] = concatenated
                feature_file_dict['neuralnet_inputs'] = neuralnet_inputs_all_files
                if 'num_samples' in feature_file_dict:
                    # because we computed it for non-neural net features already
                    pass
                else:
                    if len(feature_file_dict['neuralnet_inputs']) == 1:
                        key = list(
                            feature_file_dict['neuralnet_inputs'].keys()
                        )[0]
                        num_samples = feature_file_dict['neuralnet_inputs'][key].shape[0]
                        feature_file_dict['num_samples'] = num_samples
                    else:
                        raise ValueError('can\'t determine number of samples '
                                         'in neuralnet_inputs because there\'s '
                                         'more than one key in dictionary.')

            if 'features' in feature_file_dict:
                feature_file_dict['num_samples'] = \
                    feature_file_dict['features'].shape[0]
            elif 'neuralnet_inputs' in feature_file_dict:
                if len(feature_file_dict['neuralnet_inputs']) == 1:
                    key = list(
                        feature_file_dict['neuralnet_inputs'].keys()
                    )[0]
                    num_samples = feature_file_dict['neuralnet_inputs'][key].shape[0]
                    feature_file_dict['num_samples'] = num_samples
                else:
                    raise ValueError('can\'t determine number of samples '
                                     'in neuralnet_inputs because there\'s '
                                     'more than one key in dictionary.')

            joblib.dump(feature_file_dict,
                        feature_file,
                        compress=3)

        if return_extract_dict:
            extract_dict = {'labels': all_labels}
            if 'features_from_all_files' in locals():
                extract_dict['features'] = features_from_all_files
            if 'neuralnet_inputs_all_files' in locals():
                extract_dict['neuralnet_inputs'] = neuralnet_inputs_all_files
            return extract_dict

    def _from_file(self,
                   filename,
                   labels,
                   onsets_Hz,
                   offsets_Hz,
                   labels_to_use,
                   file_format=None,
                   ):
        """
        extracts features from an audio file containing birdsong

        Parameters
        ----------
        filename : str
            full path to audio file
        labels : ndarray
            of dtype 'char'. labels applied to segments.
        onsets_Hz : ndarray
            of dtype 'int'. onsets of segments in units of samples
        offsets_Hz : ndarray
            of dtype 'int'. offsets of segments in units of samples
        labels_to_use : str
            either
                a string representing unique set of labels which, if
                a syllable/segment is annotated with that label, then features
                will be calculated for that syllable
                e.g., 'iabcdef' or '012345'
            or
                'all'
                    in which case features are extracted from all syllable segments
        file_format : str
            {'cbin','wav'}
            format of audio file

        Returns
        -------
        extract_dict : dict
            with following key, value pairs:
                labels : ndarray
                    of dtype 'char'. labels applied to segments.
                    Only will contain labels that were in labels_to_use.
                onsets_Hz : ndarray
                    of dtype 'int'. onsets of segments in units of samples
                    Only for segments which were annotated with labels from labels_to_use.
                offsets_Hz : ndarray
                    of dtype 'int'. offsets of segments in units of samples
                    Only for segments which were annotated with labels from labels_to_use.
                features_arr : ndarray
                    m-by-n matrix, where each column n is a feature
                    or one element of a multi-column feature
                    (e.g. spectrum is a multi-column feature)
                    and each row m represents one syllable
                    Returned for all non-"neuralnet input" features.
                feature_inds : ndarray
                    1-dimensional indexing array used by hvc.extract
                    to split feature_arr back up into feature groups
                    Array will be of length n where n is number of columns in features_arr,
                    but unique(feature_inds) = len(feature_list)
                    Returned for all non-"neuralnet input" features.
                neuralnet_inputs_dict : dict
                    dict where keys are names of a neuralnet model and value is corresponding
                    input for each model, e.g., 2-d array containing spectrogram
        """
        if filename.endswith('.cbin'):
            raw_audio, samp_freq = evfuncs.load_cbin(filename)
        elif filename.endswith('.wav'):
            samp_freq, raw_audio = wavfile.read(filename)

        if labels_to_use == 'all':
            use_these_labels_bool = np.ones((labels.shape)).astype(bool)
        else:
            use_these_labels_bool = np.asarray([label in labels_to_use
                                                for label in labels])
        if type(labels) is str:
            labels = np.asarray(list(labels))

        if not np.any(use_these_labels_bool):
            warnings.warn('No labels in {0} matched labels to use: {1}\n'
                          'Did not extract features from file.'
                          .format(filename, labels_to_use))
            return None

        # initialize indexing array for features
        # used to split back up into feature groups
        feature_inds = []
    
        # loop through features first instead of syls because
        # some features do not require making spectrogram
        ########################################################################
        # so how this loop works is, make an array of length syllables, and for#
        # each syllable calculate the feature and then insert the values in    #
        # the corresponding index. After looping through all syllables,        #
        # concatenate w/growing features array.                                #
        ########################################################################
        for ftr_ind, current_feature in enumerate(self.feature_list):
            # if this is a feature extracted from a single syllable, i.e.,
            # if this feature requires a spectrogram
            if current_feature in single_syl_features_switch_case_dict:
                if 'syls' not in locals():
                    syls = make_syls(raw_audio,
                                     samp_freq,
                                     self.spectrogram_maker,
                                     labels[use_these_labels_bool],
                                     onsets_Hz[use_these_labels_bool],
                                     offsets_Hz[use_these_labels_bool])
                if 'curr_feature_arr' in locals():
                    del curr_feature_arr

                for ind, syl in enumerate(syls):
                    # extract current feature from every syllable
                    if syl.spect is np.nan:
                        # can't extract feature so leave as nan
                        continue
                    ftr = single_syl_features_switch_case_dict[current_feature](syl)

                    if 'curr_feature_arr' in locals():
                        if np.isscalar(ftr):
                            curr_feature_arr[ind] = ftr
                        else:
                            # note have to add dimension with newaxis because np.concat requires
                            # same number of dimensions, but extract_features returns 1d.
                            # Decided to keep it explicit that we go to 2d here.
                            curr_feature_arr[ind, :] = ftr[np.newaxis, :]
                    else:  # if curr_feature_arr doesn't exist yet
                        # initialize vector, if feature is a scalar, or matrix, if feature is a vector
                        # where each element (scalar feature) or row (vector feature) is feature from
                        # one syllable.
                        # Initialize as nan so that if there are syllables from which feature could
                        # not be extracted, the value for that feature stays as nan
                        # (e.g. because segment was too short to make spectrogram
                        # with given spectrogram values)
                        if np.isscalar(ftr):
                            curr_feature_arr = np.full((len(syls)), np.nan)
                            # may not be on first syllable if first spectrogram was nan
                            # so need to index into initialized array
                            curr_feature_arr[ind] = ftr
                        else:
                            curr_feature_arr = np.full((len(syls),
                                                        ftr.shape[-1]), np.nan)
                            # may not be on first syllable if first spectrogram was nan
                            # so need to index into initialized array
                            curr_feature_arr[ind, :] = ftr[np.newaxis, :]  # make 2-d for concatenate

                # after looping through all syllables:
                if 'features_arr' in locals():
                    if np.isscalar(ftr):
                        # if feature is scalar,
                        # then `ftr` from all syllables will be a (row) vector
                        # so transpose to column vector then add to growing end of 2d matrix
                        feature_inds.extend([ftr_ind])
                        features_arr = np.concatenate((features_arr,
                                                       curr_feature_arr[np.newaxis, :].T),
                                                      axis=1)
                    else:
                        # if feature is not scalar,
                        # `ftr` will be 2-d, so don't transpose before you concatenate
                        feature_inds.extend([ftr_ind] * ftr.shape[-1])
                        features_arr = np.concatenate((features_arr,
                                                       curr_feature_arr),
                                                      axis=1)
                else:  # if 'features_arr' doesn't exist yet
                    if np.isscalar(ftr):
                        feature_inds.extend([ftr_ind])
                    else:
                        feature_inds.extend([ftr_ind] * ftr.shape[-1])
                    features_arr = curr_feature_arr

            elif current_feature in multiple_syl_features_switch_case_dict:
                curr_feature_arr = multiple_syl_features_switch_case_dict[current_feature](onsets_Hz,
                                                                                           offsets_Hz,
                                                                                           use_these_labels_bool)
                feature_inds.extend([ftr_ind])
                if 'features_arr' in locals():
                    features_arr = np.concatenate((features_arr,
                                                   curr_feature_arr[:, np.newaxis]),
                                                  axis=1)
                else:
                    features_arr = curr_feature_arr[:, np.newaxis]
            elif current_feature in neural_net_features_switch_case_dict:
                curr_neuralnet_input = neural_net_features_switch_case_dict[current_feature](raw_audio,
                                                                                             samp_freq,
                                                                                             self.spectrogram_maker,
                                                                                             labels[
                                                                                                 use_these_labels_bool],
                                                                                             onsets_Hz[
                                                                                                 use_these_labels_bool],
                                                                                             offsets_Hz[
                                                                                                 use_these_labels_bool])
                if 'neuralnet_inputs_dict' in locals():
                    if current_feature in neuralnet_inputs_dict:
                        if type(neuralnet_inputs_dict[current_feature]) is np.ndarray:
                            neuralnet_inputs_dict[current_feature] = \
                                np.concatenate((neuralnet_inputs_dict[current_feature],
                                                curr_neuralnet_input),
                                               axis=-1)
                    else:
                        neuralnet_inputs_dict[current_feature] = curr_neuralnet_input
                else:
                    neuralnet_inputs_dict = {current_feature: curr_neuralnet_input}

        # return extract dict that has labels and features_arr and/or neuralnet_inputs_dict
        extract_dict = {'labels': labels[use_these_labels_bool]}
        extract_dict['onsets_Hz'] = onsets_Hz[use_these_labels_bool]
        extract_dict['offsets_Hz'] = offsets_Hz[use_these_labels_bool]
        if 'features_arr' in locals():
            extract_dict['features_arr'] = features_arr
            extract_dict['feature_inds'] = np.asarray(feature_inds)
        if 'neuralnet_inputs_dict' in locals():
            extract_dict['neuralnet_inputs_dict'] = neuralnet_inputs_dict
        extract_dict['samp_freq'] = samp_freq
        return extract_dict
