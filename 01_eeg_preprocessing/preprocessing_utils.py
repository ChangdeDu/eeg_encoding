def epoching(args, data_part):
	"""This function first converts the EEG data to MNE raw format, and
	performs channel selection, epoching, baseline correction and frequency
	downsampling. Then, it sorts the EEG data of each session according to the
	image conditions.

	Parameters
	----------
	args : Namespace
		Input arguments.
	data_part : str
		'test' or 'training' data partitions.

	Returns
	-------
	epoched_data : list of float
		Epoched EEG data.
	img_conditions : list of int
		Unique image conditions of the epoched and sorted EEG data.
	ch_names : list of str
		EEG channel names.
	times : float
		EEG time points.

	"""

	import os
	import mne
	import numpy as np
	from sklearn.utils import shuffle

	# Set random seed for reproducible results
	np.random.seed(seed=20200220)

	### Looping across data collection sessions ###
	epoched_data = []
	img_conditions = []
	for s in range(args.n_ses):

		### Loading the EEG data and converting it to MNE raw format ###
		eeg_dir = os.path.join('eeg_dataset', 'raw_data', 'sub-'+
			format(args.sub,'02'), 'ses-'+format(s+1,'02'), 'raw_eeg_'+
			data_part+'.npy')
		eeg_data = np.load(os.path.join(args.project_dir, eeg_dir),
			allow_pickle=True).item()
		ch_names = eeg_data['ch_names']
		sfreq = eeg_data['sfreq']
		ch_types = eeg_data['ch_types']
		eeg_data = eeg_data['raw_eeg_data']
		# Converting to MNE raw format
		info = mne.create_info(ch_names, sfreq, ch_types)
		raw = mne.io.RawArray(eeg_data, info)
		del eeg_data

		### Get events, drop unused channels and reject target trials ###
		events = mne.find_events(raw, stim_channel='stim')
		# Selecting only occipital (O) and posterior (P) channels
		chan_idx = np.asarray(mne.pick_channels_regexp(raw.info['ch_names'],
			'^O *|^P *'))
		new_chans = [raw.info['ch_names'][c] for c in chan_idx]
		raw.pick_channels(new_chans)
		# Rejecting the target trials (event 99999)
		idx_target = np.where(events[:,2] == 99999)[0]
		events = np.delete(events, idx_target, 0)

		### Epoching, baseline correcting and resampling ###
		epochs = mne.Epochs(raw, events, tmin=-.2, tmax=.8, baseline=(None,0),
			preload=True)
		del raw
		# Resampling
		if args.sfreq < 1000:
			epochs.resample(args.sfreq)
		ch_names = epochs.info['ch_names']
		times = epochs.times

		### Sorting the data ###
		data = epochs.get_data()
		events = epochs.events[:,2]
		img_cond = np.unique(events)
		del epochs
		# Selecting only a maximum number of EEG repetitions
		if data_part == 'test':
			max_rep = 20
		else:
			max_rep = 2
		# Sorted data matrix of shape:
		# Image conditions × EEG repetitions × EEG channels × EEG time points
		sorted_data = np.zeros((len(img_cond),max_rep,data.shape[1],
			data.shape[2]))
		for i in range(len(img_cond)):
			# Finding the indices of the selected image condition
			idx = np.where(events == img_cond[i])[0]
			# Randomly selecting only the max number of EEG repetitions
			idx = shuffle(idx)[:max_rep]
			sorted_data[i] = data[idx]
		del data
		epoched_data.append(sorted_data)
		img_conditions.append(img_cond)
		del sorted_data

	### Output ###
	return epoched_data, img_conditions, ch_names, times


def mvnn(args, epoched_test, epoched_train):
	"""Computing the covariance matrices of the EEG data (calculated for each
	time-point or epoch/repetitions of each image condition), and then averaging
	them across image conditions and data partitions. The inverse of the
	resulting averaged covariance matrix is used to whiten the EEG data
	(independently for each session).

	Parameters
	----------
	args : Namespace
		Input arguments.
	epoched_test : list of floats
		Epoched test EEG data.
	epoched_train : list of floats
		Epoched training EEG data.

	Returns
	-------
	whitened_test : list of float
		Whitened test EEG data.
	whitened_train : list of float
		Whitened training EEG data.

	"""

	import numpy as np
	from tqdm import tqdm
	from sklearn.discriminant_analysis import _cov
	import scipy

	### Looping across data collection sessions ###
	whitened_test = []
	whitened_train = []
	for s in range(args.n_ses):
		session_data = [epoched_test[s], epoched_train[s]]

		### Computing the covariance matrices ###
		# Data partitions covariance matrix of shape:
		# Data partitions × EEG channels × EEG channels
		sigma_part = np.empty((len(session_data),session_data[0].shape[2],
			session_data[0].shape[2]))
		for p in range(sigma_part.shape[0]):
			# Image conditions covariance matrix of shape:
			# Image conditions × EEG channels × EEG channels
			sigma_cond = np.empty((session_data[p].shape[0],
				session_data[0].shape[2],session_data[0].shape[2]))
			for i in tqdm(range(session_data[p].shape[0])):
				cond_data = session_data[p][i]
				# Computing covariace matrices at each time point, and then
				# averaging across time points
				if args.mvnn_dim == "time":
					sigma_cond[i] = np.mean([_cov(cond_data[:,:,t],
						shrinkage='auto') for t in range(cond_data.shape[2])],
						axis=0)
				# Computing covariace matrices at each epoch (EEG repetition),
				# and then averaging across epochs/repetitions
				elif args.mvnn_dim == "epochs":
					sigma_cond[i] = np.mean([_cov(np.transpose(cond_data[e]),
						shrinkage='auto') for e in range(cond_data.shape[0])],
						axis=0)
			# Averaging the covariance matrices across image conditions
			sigma_part[p] = sigma_cond.mean(axis=0)
		# Averaging the covariance matrices across image partitions
		sigma_tot = sigma_part.mean(axis=0)
		# Computing the inverse of the covariance matrix
		sigma_inv = scipy.linalg.fractional_matrix_power(sigma_tot, -0.5)

		### Whitening the data ###
		whitened_test.append(np.reshape((np.reshape(session_data[0], (-1,
			session_data[0].shape[2],session_data[0].shape[3])).swapaxes(1, 2)
			@ sigma_inv).swapaxes(1, 2), session_data[0].shape))
		whitened_train.append(np.reshape((np.reshape(session_data[1], (-1,
			session_data[1].shape[2],session_data[1].shape[3])).swapaxes(1, 2)
				@ sigma_inv).swapaxes(1, 2), session_data[1].shape))

	### Output ###
	return whitened_test, whitened_train


def save_prepr(args, whitened_test, whitened_train, img_conditions_train,
	ch_names, times):
	"""Merging the EEG data of all sessions together, shuffling the EEG
	repetitions across sessions and reshaping the data to the format:
	Image conditions × EGG repetitions × EEG channels × EEG time points.
	Then, the data of both test and training EEG partitions is saved.

	Parameters
	----------
	args : Namespace
		Input arguments.
	whitened_test : list of float
		Whitened test EEG data.
	whitened_train : list of float
		Whitened training EEG data.
	img_conditions_train : list of int
		Unique image conditions of the epoched and sorted train EEG data.
	ch_names : list of str
		EEG channel names.
	times : float
		EEG time points.

	"""

	import numpy as np
	from sklearn.utils import shuffle
	import os

	### Merging and saving the test data ###
	for s in range(args.n_ses):
		if s == 0:
			merged_test = whitened_test[s]
		else:
			merged_test = np.append(merged_test, whitened_test[s], 1)
	del whitened_test
	# Shuffling the repetitions of different sessions
	idx = shuffle(np.arange(0, merged_test.shape[1]))
	merged_test = merged_test[:,idx]
	# Inserting the data into a dictionary
	test_dict = {
		'preprocessed_eeg_data': merged_test,
		'ch_names': ch_names,
		'times': times
	}
	del merged_test
	# Saving directories
	save_dir = os.path.join('eeg_dataset', 'preprocessed_data', 'sub-'+
		format(args.sub,'02'))
	file_name_test = 'preprocessed_eeg_test.npy'
	file_name_train = 'preprocessed_eeg_training.npy'
	# Creating the directory if not existing and saving the data
	if os.path.isdir(os.path.join(args.project_dir, save_dir)) == False:
		os.makedirs(os.path.join(args.project_dir, save_dir))
	np.save(os.path.join(args.project_dir, save_dir, file_name_test), test_dict)
	del test_dict

	### Merging and saving the training data ###
	for s in range(args.n_ses):
		if s == 0:
			white_data = whitened_train[s]
			img_cond = img_conditions_train[s]
		else:
			white_data = np.append(white_data, whitened_train[s], 0)
			img_cond = np.append(img_cond, img_conditions_train[s], 0)
	del whitened_train, img_conditions_train
	# Data matrix of shape:
	# Image conditions × EGG repetitions × EEG channels × EEG time points
	merged_train = np.zeros((len(np.unique(img_cond)), white_data.shape[1]*2,
		white_data.shape[2],white_data.shape[3]))
	for i in range(len(np.unique(img_cond))):
		# Finding the indices of the selected category
		idx = np.where(img_cond == i+1)[0]
		for r in range(len(idx)):
			if r == 0:
				ordered_data = white_data[idx[r]]
			else:
				ordered_data = np.append(ordered_data, white_data[idx[r]], 0)
		merged_train[i] = ordered_data
	# Shuffling the repetitions of different sessions
	idx = shuffle(np.arange(0, merged_train.shape[1]))
	merged_train = merged_train[:,idx]
	# Inserting the data into a dictionary
	train_dict = {
		'preprocessed_eeg_data': merged_train,
		'ch_names': ch_names,
		'times': times
	}
	del merged_train
	# Creating the directory if not existing and saving the data
	if os.path.isdir(os.path.join(args.project_dir, save_dir)) == False:
		os.makedirs(os.path.join(args.project_dir, save_dir))
	np.save(os.path.join(args.project_dir, save_dir, file_name_train),
		train_dict)
	del train_dict