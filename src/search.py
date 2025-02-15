from __future__ import print_function
from keras_vggface.vggface import VGGFace
from keras_vggface import utils
from keras.layers import Input
from keras.engine import Model
from keras import backend as K
from feature_extraction import extract_feature_from_face
from batch_feature_extraction import extract_database_faces_features
from apply_super_resolution import apply_super_res
from face_extraction_queries import detect_face_by_path
from vgg_finetune import fine_tune, extract_feature_from_face_list
from sticher import ImageSticher
from keras.models import load_model
from util import calculate_average_faces_sim, cosine_similarity, mean_max_similarity, write_result_to_file, write_result, create_stage_folder, adjust_size_different_images, create_image_label, max_mean_similarity, max_max_similarity, mean_mean_similarity
from scipy import stats
from PIL import Image
from checkGBFace_solvePnP import getFaceRotationAngles
from sklearn.svm import SVC, LinearSVC
from sklearn.model_selection import cross_val_score
from natsort import natsorted
from checkGBFace import GoodFaceChecker
from preprocess import extendBB

import numpy as np
import json
import pickle
import os
import cv2
import time
import sys
import glob
import multiprocessing
import subprocess


class SearchEngine(object):
    def __init__(self, image_sticher):
        with open("../cfg/config.json", "r") as f:
            self.cfg = json.load(f)
        with open("../cfg/search_config.json", "r") as f:
            self.search_cfg = json.load(f)

        # Set up folder path
        self.query_feature_folder = os.path.abspath(self.cfg['features']['Query_feature'])
        
        self.default_feature_folder = os.path.abspath(
            self.cfg["features"]["VGG_default_features"])
        self.faces_folder = os.path.abspath(
            self.cfg["processed_data"]["faces_folder"])
        self.landmarks_folder = os.path.abspath(
            self.cfg["processed_data"]["landmarks_folder"])
        self.frames_folder = os.path.abspath(
            self.cfg["processed_data"]["frames_folder"])

        self.result_path = os.path.abspath(
            os.path.join(self.cfg["result"], self.cfg["config"]))

        self.fine_tune_feature_folder = os.path.abspath(os.path.join(
            self.cfg["features"]["VGG_fine_tuned_features"], self.cfg["config"]))

        self.vgg_fine_tune_model_path = os.path.abspath(
            os.path.join(self.cfg["models"]["VGG_folder"]["VGG_fine_tuned_folder"], self.cfg["config"]))

        self.svm_model_path = os.path.abspath(
            os.path.join(self.cfg['models']["SVM_folder"], self.cfg["config"]))

        self.vgg_training_data_folder = os.path.abspath(
            os.path.join(self.cfg["training_data"]["VGG_data"], self.cfg["config"]))

        self.svm_training_data_folder = os.path.abspath(
            os.path.join(self.cfg["training_data"]["SVM_data"], self.cfg["config"]))

        self.query_shot_folder = None

        # Set up config
        self.net = self.search_cfg["net"]
        self.rmBF_method = self.search_cfg["rmBadFacesMethod"]
        self.rmBF_landmarks_params = self.search_cfg["rmBadFacesLandmarkBasedParams"]
        self.rmBF_classifier_params = self.search_cfg["rmBadFacesClassifierParams"]


        # Making directories if not exists
        os.makedirs(self.result_path, exist_ok=True)
        os.makedirs(self.fine_tune_feature_folder, exist_ok=True)
        os.makedirs(self.vgg_fine_tune_model_path, exist_ok=True)
        os.makedirs(self.svm_model_path, exist_ok=True)
        os.makedirs(self.vgg_training_data_folder, exist_ok=True)
        os.makedirs(self.svm_training_data_folder, exist_ok=True)

        self.query_name = None
        self.sticher = image_sticher
        self.fine_tune_vgg = None
        self.svm_clf = None
        self.n_jobs = self.search_cfg['n_jobs']
        self.good_face_checker = GoodFaceChecker(method=self.rmBF_method, checkBlur=(self.rmBF_landmarks_params["is_check_blur"] == "True"))

    def extract_query_shot_feature(self, query_shot_folder):
        query_shots = [os.path.join(query_shot_folder, folder) for folder in os.listdir(query_shot_folder)] 
        if self.net == "VGGFace":
            vgg_model = VGGFace(input_shape=(224, 224, 3), pooling='avg')
            out = vgg_model.get_layer(self.search_cfg["feature_descriptor"]).output
            default_vgg = Model(vgg_model.input, out)
            face_feature = []
            for query_shot in query_shots:
                faces_img = [os.path.join(query_shot, file) for file in os.listdir(query_shot)]
                for face_img in faces_img:
                    img = cv2.imread(face_img)
                    face_sr = apply_super_res(img)
                    feature = extract_feature_from_face(default_vgg, face_sr)
                    face_feature.append((face_sr, feature))
            K.clear_session()
            return face_feature
        else:
            return []

    # def apply_sr_query_shot(self, query_shot_folder):
    #     faces_sr = []
    #     query_shots = [os.path.join(query_shot_folder, folder) for folder in os.listdir(query_shot_folder)]
    #     for query_shot in query_shots:
    #         faces_img = [os.path.join(query_shot, file) for file in os.listdir(query_shot)]
    #         for face_img in faces_img:
    #             img = cv2.imread(face_img)
    #             face_sr = apply_super_res(face_img)
    #             faces_sr.append(face_sr)
    #     return faces_sr

    def remove_bad_faces(self, query):
        '''
        Parameters:
        - query: [(face matrix, feature vector)]
        Returns:
        - query_final: resulting query after remove some bad faces. (same format at parameter)
        '''
        n = len([q for q in query if q is not None])
        confs = [0] * n
        query_final = []
        for i in range(n):
            if query[i] is not None:
                confs[i] = sum([cosine_similarity(query[i][1], query[j][1])
                                for j in range(n) if i != j and query[j] is not None])

        if n > 1:
            for i in range(n):
                if query[i] is not None:
                    mean = sum([confs[j] for j in range(n) if i !=
                                j and confs[j] != 0]) / (n - 1)
                    if confs[i] + 0.05 >= mean:
                        query_final.append(query[i])
                    else:
                        query_final.append(None)
                else:
                    query_final.append(None)
        else:
            return query

        if query_final.count(None) == n:
            print("[!] ERROR : No image in query")
            return query     # In case all faces are "bad" faces, return the same query features
        return query_final  # Return list of features of "good" faces

    def split_by_average_comparision(self, record, thresh=0.5):
        '''
        Specifiy if a sample is positive or negative based on
        given thresh. If mean similarity of that shot is larger than
        thresh, a sample is positive. Otherwise, it is negative.
        Parameters:
        record: a list contains 3 elements:
        - shot_id
        - sim(query, shot_id): similarity between input query and current shot
        - a matrix of shape(num_query_face, num_shot_face_detected):
            + num_query_face: #remaining faces in query after remove bad faces
            + num_shot_face_detected: #faces detected of the current shot
            + matrix[i][j]: ((frame file, bb), cosine similarity score
        between 'query face i' and 'shot face j')
        thresh: a similarity thresh for choosing pos and neg_

        Returns:
        X: a list of faces use for training
        y: a list of corresponding labels for each face
        pos: # positive samples
        neg: # neg samples
        '''
        X = []
        Y = []
        pos, neg = 0, 0
        shot_id = record[0]
        video_id = shot_id.split('_')[0][4:]
        data = calculate_average_faces_sim(record)
        for face_data in data:
            img = cv2.imread(os.path.join(
                self.frames_folder, 'video' + video_id, shot_id, face_data[0][0]))
            x1, y1, x2, y2 = face_data[0][1]
            face = img[y1: y2, x1: x2]

            face = cv2.resize(face, (256, 256))

            X.append(face)
            if face_data[1] >= thresh:
                Y.append(1)
                pos += 1
            else:
                Y.append(0)
                neg += 1
        return X, Y, pos, neg

    def _PEsolvePnP(self, x_sample, y_sample, landmarks_info):
        '''
        Eliminate side faces by using solvePnP for pose estimation
        '''
        new_x_sample = []
        new_y_sample = []
        pos = 0
        neg = 0
        rotation_vecs = []
        for image_points, (height, width) in landmarks_info:
            rotation_vecs.append(getFaceRotationAngles(
                image_points, (height, width)))
        for idx, (x_smp, y_smp) in enumerate(zip(x_sample, y_sample)):
            rotation_vecs[idx] = np.minimum(
                180 - rotation_vecs[idx], rotation_vecs[idx])
            if abs(rotation_vecs[idx][1]) < 45:
                new_x_sample.append(x_smp)
                new_y_sample.append(y_smp)

        for x_smp, y_smp in zip(new_x_sample, new_y_sample):
            if y_smp == 1:
                pos += 1
            else:
                neg += 1

        return new_x_sample, new_y_sample, pos, neg

    def form_training_set(self, result, thresh=0.7, rmBadFaces=None):
        X = []
        Y = []
        pos = 0
        neg = 0
        print("[+] Forming training set...")
        for record in result:
            shot_id = record[0]
            video_id = shot_id.split('_')[0][4:]
            with open(os.path.join(self.faces_folder, 'video' + video_id, shot_id + ".pickle"), 'rb') as f:
                faces = pickle.load(f)
            with open(os.path.join(self.landmarks_folder, 'video' + video_id, shot_id + ".pickle"), 'rb') as f:
                landmarks = pickle.load(f)

            # Get rotation vector of  each face in current shot
            landmarks_info = []
            for (frame_id, _), landmark in zip(faces, landmarks):
                img = Image.open(os.path.join(
                    self.frames_folder, 'video' + video_id, shot_id, frame_id))
                width, height = img.size

                image_points = []
                for i in range(int(len(landmark)/2.)):
                    x, y = int(landmark[i]), int(landmark[i+5])
                    image_points.append((x, y))
                image_points = np.array(image_points, dtype='double')

                # rotation_vecs.append(getFaceRotationAngles(
                #     image_points, (height, width)))
                landmarks_info.append((image_points, (height, width)))

            # Choose positive and negative sample
            x_sample, y_sample, pos_, neg_ = self.split_by_average_comparision(
                record, thresh=thresh)

            if rmBadFaces is not None:
                x_sample, y_sample, pos_, neg_ = rmBadFaces(
                    x_sample, y_sample, landmarks_info)

            X.extend(x_sample)
            Y.extend(y_sample)
            pos += pos_
            neg += neg_

        print("[+] Finished, There are %d positive sample and %d negative sample in top %d" %
              (pos, neg, len(result)))
        data = list(zip(X, Y))
        data.sort(reverse=True, key=lambda x: x[1])
        data = data[:pos + pos + pos]
        X = [a[0] for a in data]
        Y = [a[1] for a in data]

        # filename = self.query_name + '_' + 'thresh=' + str(thresh)
        # if rmBadFaces is not None:
        #    filename += rmBadFaces.__name__

        # save_path = os.path.join(self.training_data_folder, self.query_name)
        # if not os.path.isdir(save_path):
        #     os.makedirs(save_path)

        # self.sticher.process_training_set(training_set_path)

        return [X, Y]

    def form_training_set_using_best_face_in_each_shot(self, result, rmBadFaces=None):
        X, Y, landmarks_info = [], [], []

        ############################################################
        ################### GET POSITIVE SAMPLES ###################
        ############################################################
        for seqNum in range(100):
            record = result[seqNum]
            shot_id = record[0]
            video_id = shot_id.split('_')[0][4:]

            frameSet = set()
            for r in record[2][0]:
                frameSet.add(r[0][0])
            frameNum = len(frameSet)

            # Load faces and landmarks files
            with open(os.path.join(self.faces_folder, 'video' + video_id, shot_id + '.pickle'), 'rb') as f:
                faces = pickle.load(f)
            with open(os.path.join(self.landmarks_folder, 'video' + video_id, shot_id + '.pickle'), 'rb') as f:
                landmarks = pickle.load(f)

            # Get best face
            best_face_data = sorted(calculate_average_faces_sim(
                record), key=lambda x: x[1])[-4:]

            for face_data in best_face_data:

                frame_file = face_data[0][0]
                frame = cv2.imread(os.path.join(
                    self.frames_folder, 'video' + video_id, shot_id, frame_file))
                height, width = frame.shape[:2]


                best_face_landmark = None
                for face, landmark in zip(faces, landmarks):
                    if face == face_data[0]:
                        best_face_landmark = landmark
                        break

                image_points = []
                for i in range(int(len(best_face_landmark)/2.)):
                    x, y = int(best_face_landmark[i]), int(best_face_landmark[i+5])
                    image_points.append((x, y))
                    # cv2.circle(frame, (x, y), 2, (0, 255, 0), 2)
                image_points = np.array(image_points, dtype='double')

                x, y, _x, _y = face_data[0][1]
                # x, y, _x, _y = extendBB((height, width), x, y, _x, _y, ratio=1.0)
                best_face = frame[y:_y, x:_x]
                X.append(best_face)
                landmarks_info.append((image_points, (height, width)))

                Y.append(1)

        ############################################################
        ################### GET NEGATIVE SAMPLES ###################
        ############################################################
        for seqNum in range(100):
            record = result[seqNum]
            shot_id = record[0]
            video_id = shot_id.split('_')[0][4:]

            frameSet = set()
            for r in record[2][0]:
                frameSet.add(r[0][0])
            frameNum = len(frameSet)

            # Load faces and landmarks files
            with open(os.path.join(self.faces_folder, 'video' + video_id, shot_id + '.pickle'), 'rb') as f:
                faces = pickle.load(f)
            with open(os.path.join(self.landmarks_folder, 'video' + video_id, shot_id + '.pickle'), 'rb') as f:
                landmarks = pickle.load(f)

            # Get bad face
            bad_face_data = sorted(calculate_average_faces_sim(
                record), key=lambda x: x[1])[(-frameNum-4):-frameNum]

            for face_data in bad_face_data:

                frame_file = face_data[0][0]
                frame = cv2.imread(os.path.join(
                    self.frames_folder, 'video' + video_id, shot_id, frame_file))
                height, width = frame.shape[:2]


                bad_face_landmark = None
                for face, landmark in zip(faces, landmarks):
                    if face == face_data[0]:
                        bad_face_landmark = landmark
                        break

                image_points = []
                for i in range(int(len(bad_face_landmark)/2.)):
                    x, y = int(bad_face_landmark[i]), int(bad_face_landmark[i+5])
                    image_points.append((x, y))
                    # cv2.circle(frame, (x, y), 2, (0, 255, 0), 2)
                image_points = np.array(image_points, dtype='double')

                x, y, _x, _y = face_data[0][1]
                # x, y, _x, _y = extendBB((height, width), x, y, _x, _y, ratio=1.0)
                bad_face = frame[y:_y, x:_x]
                X.append(bad_face)
                landmarks_info.append((image_points, (height, width)))

                Y.append(0)

        # all_bad_faces = []
        # all_bad_faces_sim = []
        # all_bad_faces_landmark = []
        # for seqNum in range(100):
        #     record = result[seqNum]
        #     shot_id = record[0]
        #     video_id = shot_id.split('_')[0][4:]

        #     frameSet = set()
        #     for r in record[2][0]:
        #         frameSet.add(r[0][0])
        #     frameNum = len(frameSet)

        #     # Load faces and landmarks files
        #     with open(os.path.join(self.faces_folder, 'video' + video_id, shot_id + '.pickle'), 'rb') as f:
        #         faces = pickle.load(f)
        #     with open(os.path.join(self.landmarks_folder, 'video' + video_id, shot_id + '.pickle'), 'rb') as f:
        #         landmarks = pickle.load(f)

        #     # Get bad face
        #     bad_face_data = sorted(calculate_average_faces_sim(
        #         record), key=lambda x: x[1])[:-frameNum]

        #     for face_data in bad_face_data:

        #         frame_file = face_data[0][0]
        #         frame = cv2.imread(os.path.join(
        #             self.frames_folder, 'video' + video_id, shot_id, frame_file))
        #         height, width = frame.shape[:2]


        #         bad_face_landmark = None
        #         for face, landmark in zip(faces, landmarks):
        #             if face == face_data[0]:
        #                 bad_face_landmark = landmark
        #                 break

        #         image_points = []
        #         for i in range(int(len(bad_face_landmark)/2.)):
        #             x, y = int(bad_face_landmark[i]), int(bad_face_landmark[i+5])
        #             image_points.append((x, y))
        #             # cv2.circle(frame, (x, y), 2, (0, 255, 0), 2)
        #         image_points = np.array(image_points, dtype='double')

        #         x, y, _x, _y = face_data[0][1]
        #         # x, y, _x, _y = extendBB((height, width), x, y, _x, _y, ratio=1.0)
        #         bad_face = frame[y:_y, x:_x]

        #         all_bad_faces.append(bad_face)
        #         all_bad_faces_sim.append(np.asscalar(face_data[1]))
        #         all_bad_faces_landmark.append((image_points, (height, width)))

        # _, all_bad_faces, all_bad_faces_landmark = zip(*sorted((list(zip(all_bad_faces_sim, all_bad_faces, all_bad_faces_landmark))), key=lambda x: x[0]))

        # num_pos = len(X)
        # X.extend(all_bad_faces[-num_pos:])
        # landmarks_info.extend(all_bad_faces_landmark[-num_pos:])
        # Y.extend([0] * num_pos)

        # Filter Bad Faces in training data
        if rmBadFaces is not None:
            X, Y, _, _ = rmBadFaces(
                X, Y, landmarks_info)

        return [X, Y]

    def form_SVM_training_set(self, result, thresh=0.7, rmBadFaces=None):
        X, Y = self.form_training_set(result, thresh, rmBadFaces)

        print('[+] Extracting face features')
        model_path = os.path.join(
            self.vgg_fine_tune_model_path, self.query_name, 'vgg_model.h5')
        features = extract_feature_from_face_list(model_path, X)
        print('[+] Finished extracting face features')

        K.clear_session()
        return [features, Y], [X, Y]

    def form_SVM_training_set_using_best_face_in_each_shot(self, result, rmBadFaces=None):
        X, Y = self.form_training_set_using_best_face_in_each_shot(result, rmBadFaces)

        print('[+] Extracting face features')
        model_path = os.path.join(
            self.vgg_fine_tune_model_path, self.query_name, 'vgg_model.h5')
        features = extract_feature_from_face_list(model_path, X)
        print('[+] Finished extracting face features')

        K.clear_session()
        return [features, Y], [X, Y]

    def uniprocess_stage_1(self, query, feature_folder, isStage3=False, block_interval=None):
        '''
        Parameters:
        - query: [(face matrix, feature vector)]
        - feature_folder: path to folder of features
        - top: the number of retrieval results

        Returns:
        List of elements, each consist of:
        - shot_id
        - sim(query, shot_id): similarity between input query and current shot
        - a matrix of shape(num_query_face, num_shot_face_detected):
            + num_query_face: #remaining faces in query after remove bad faces
            + num_shot_face_detected: #faces detected of the current shot
            + matrix[i][j]: ((frame file, bb), cosine similarity score
        between 'query face i' and 'shot face j')
        '''

        result = []
        print("[+] Current feature folder : %s\n" % (feature_folder))
        video_feature_files = \
            natsorted([(file, os.path.join(feature_folder, file))
                       for file in os.listdir(feature_folder)])
        # shot_feature_files = [(os.path.basename(path), path) for path in glob.iglob(
        #     feature_folder + '/**/*.pickle', recursive=True)]

        if block_interval:
            # start = block_id * self.search_cfg['blocksize']
            # end = start + self.search_cfg['blocksize']
            video_feature_files = \
                video_feature_files[block_interval[0]:block_interval[1]]

        cosine_similarity = []
        classification_score = []

        print('[+] Start to compute the similarity between person and each shot\n')
        idx = 0
        for video_feature_file in video_feature_files:
            video_id = video_feature_file[0].split('.')[0]
            print('Processing video', video_id)
            if self.net == 'VGGFace':
                with open(video_feature_file[1], 'rb') as f:
                    video_feature = pickle.load(f)
            elif self.net == 'VGGFace2':
                with open(video_feature_file[1], 'rb') as f:
                    video_feature = pickle.load(f, encoding='latin1')

            # for idx, shot_feature_file in enumerate(shot_feature_files):
            for shot_id, shot_faces_feat in video_feature.items():
                idx += 1
                # shot_id = shot_feature_file[0].split(".")[0]
                # video_id = shot_id.split('_')[0][4:]
                # print('[id: %d], computing similarity for %s' %
                #       (idx, shot_id))
                # print(len(result))
                # feature_path = shot_feature_file[1]
                face_path = os.path.join(
                    self.faces_folder, video_id, shot_id + '.pickle')
                # with open(feature_path, "rb") as f:
                #     shot_faces_feat = pickle.load(f)
                with open(face_path, "rb") as f:
                    shot_faces = pickle.load(f)
                # shot faces is a list with elements consist of  ((frame, (x1, y1, x2, y2)), face features)
                shot_faces = list(zip(shot_faces, shot_faces_feat))

                # print("\t%s , number of faces : %d" % (shot_id, len(shot_faces)))

                # shot_faces = shot_faces_feat
                sim, frames_with_bb_sim = mean_max_similarity(
                    query, shot_faces)

                if isStage3:
                    arr = [self.svm_clf.decision_function(
                        face_feat) for face_feat in shot_faces_feat]
                    decision_score = max(arr)
                    exact_distance = decision_score / \
                        np.linalg.norm(self.svm_clf.coef_)

                    cosine_similarity.append(sim)
                    classification_score.append(
                        np.expand_dims(exact_distance, 0))

                # Result is a list of elements consist of (shot_id, similarity(query, shot_id), corresponding matrix faces like explaination (1)
                result.append((shot_id, sim, frames_with_bb_sim))
            #     if len(result) == 100:
            #         break
            # if len(result) == 100:
            #     break

        print('[+] Finished computing similarity for all shots')

        if isStage3:
            person_similarity = 0.7 * stats.zscore(
                cosine_similarity) + 0.3 * stats.zscore(classification_score)
            shot_id, _, frames_with_bb_sim = zip(*result)
            result = list(zip(shot_id,
                              person_similarity, frames_with_bb_sim))

        result.sort(reverse=True, key=lambda x: x[1])
        print("[+] Search completed")
        # with open(os.path.join('../temp', str(block_interval) + '.pkl'), 'wb') as f:
        #     pickle.dump(result, f)
        return result[:1000]

    def multiprocess_stage_1(self, query, feature_folder, isStage3=False):
        total_videos = len(os.listdir(self.frames_folder))
        avg_video_per_process = total_videos // self.n_jobs
        remain_videos = total_videos % self.n_jobs

        processes = []

        blocks = []
        start_idx = 0
        end_idx = 0
        for job_id in range(self.n_jobs):
            start_idx = end_idx

            batch_size = avg_video_per_process
            if job_id < remain_videos:
                batch_size += 1

            end_idx = start_idx + batch_size
            print('BLock Interval:', start_idx, end_idx)
            blocks.append((start_idx, end_idx))

        arg = [(query, feature_folder, isStage3, block) for block in blocks]
        with multiprocessing.get_context("spawn").Pool() as pool:
            result = pool.starmap(self.uniprocess_stage_1, arg)

        result = [item for sublist in result for item in sublist]
        result.sort(reverse=True, key=lambda x: x[1])
        return result[:1000]
        #     p = multiprocessing.Process(target=self.uniprocess_stage_1,
        #                                 args=(query, feature_folder,
        #                                       isStage3, (start_idx, end_idx)))
        #     p.daemon = True
        #     processes.append(p)
        #     p.start()

        # for process in processes:
        #     process.join()

    def stage_1(self, query, feature_folder, isStage3=False, multiprocess=False):
        if multiprocess:
            # if os.path.isdir('../temp'):
            #     subprocess.call(['rm', '-rf', '../temp'])
            # os.mkdir('../temp')
            # self.multiprocess_stage_1(query, feature_folder, isStage3)
            # result = []
            # for result_path in glob.glob('../temp/*pkl'):
            #     with open(result_path, 'rb') as f:
            #         result.extend(pickle.load(f))

            # subprocess.call(['rm', '-rf', '../temp'])
            # result.sort(reverse=True, key=lambda x: x[1])
            # return result[:1000]
            return self.multiprocess_stage_1(query, feature_folder, isStage3)
        return self.uniprocess_stage_1(query, feature_folder, isStage3)[:1000]

    def stage_2(self, query, training_set, multiprocess=False):
        '''
        Parameter:
        - query: [((face matrix, img query path, binary mask path), feature vector)]
        - training_set: a training set
        '''
        print("[+] Begin stage 2 of searching")

        model_path = os.path.join(
            self.vgg_fine_tune_model_path, self.query_name, 'vgg_model.h5')
        if not os.path.exists(model_path):
            self.fine_tune_vgg = fine_tune(
                training_set, save_path=model_path, batchSize=20, eps=20)
            print("[+] Finished fine tuned VGG Face model")
        else:
            self.fine_tune_vgg = load_model(model_path)
            print("[+] Load fine tuned VGG Face model")

        feature_extractor = Model(
            self.fine_tune_vgg.input, self.fine_tune_vgg.get_layer(self.search_cfg["feature_descriptor"]).output)

        fine_tune_feature_folder = os.path.join(
            self.fine_tune_feature_folder, self.query_name)

        if not os.path.exists(fine_tune_feature_folder):
            os.makedirs(fine_tune_feature_folder)

        print("[+] Begin extract feature using fine tuned model")
        extract_database_faces_features(
            feature_extractor, self.frames_folder, self.faces_folder, fine_tune_feature_folder)
        print("[+] Finished extract feature")

        query_faces = []
        for face in query:
            # faces_features store extractly like query_faces_sr except with addtional information, feature of query faces
            feature = extract_feature_from_face(feature_extractor, face[0])
            query_faces.append((face[0], feature))

        K.clear_session()
        # return self.stage_1(query_faces, fine_tune_feature_folder, multiprocess=multiprocess)
        return query_faces

    def stage_3(self, query, training_set=None, multiprocess=False):

        X, y = training_set[0], training_set[1]

        os.makedirs(os.path.join(self.svm_model_path,
                                 self.query_name), exist_ok=True)
        svm_model_path = os.path.join(
            self.svm_model_path, self.query_name, 'svm_model.pkl')

        if not os.path.exists(svm_model_path):
            print('[+] Begin Training SVM')
            self.svm_clf = SVC(probability=True, verbose=True,
                               random_state=42, kernel='linear', decision_function_shape='ovo')
            self.svm_clf.fit(X, y)
            print('[+] Fininshed Training SVM')

            with open(svm_model_path, 'wb') as f:
                pickle.dump(self.svm_clf, f)
        else:
            print('[+] SVM model already exists')
            with open(svm_model_path, 'rb') as f:
                self.svm_clf = pickle.load(f)

        query_faces = []
        vgg_fine_tune_model_path = os.path.join(
            self.vgg_fine_tune_model_path, self.query_name, 'vgg_model.h5')
        fine_tune_vgg = load_model(vgg_fine_tune_model_path)
        feature_extractor = Model(
            fine_tune_vgg.input, fine_tune_vgg.get_layer('fc7').output)
        for face in query:
            # faces_features store extractly like query_faces_sr except with addtional information, feature of query faces
            feature = extract_feature_from_face(feature_extractor, face[0])
            query_faces.append((face[0], feature))

        K.clear_session()

        fine_tune_feature_folder = os.path.join(
            self.fine_tune_feature_folder, self.query_name)

        return self.stage_1(query_faces, fine_tune_feature_folder, isStage3=True, multiprocess=multiprocess)

    def searching(self, query, mask, isStage1=True, isStage2=False, isStage3=False, multiprocess=False, use_query_shots=False):

        root_result_folder = os.path.join(self.result_path, self.query_name)
        os.makedirs(root_result_folder, exist_ok=True)
        stage_1_execution_time = 0
        stage_2_execution_time = 0
        stage_3_execution_time = 0

        # Get size of each frame in query
        frames_size = []
        for qpath in query:
            frame = cv2.imread(qpath)
            frames_size.append(frame.shape[:2])

        # Detect faces in query
        query_faces, bb, landmarks = detect_face_by_path(query, mask)
        
        K.clear_session()
        print("[+] Detected faces from query")

        # Convert landmark from MTCNN format to list of landmark points
        landmark_list = []
        for landmark, bb_coord in zip(landmarks, bb):
            if landmark is None:
                continue
            landmark_points = []
            for i in range(int(len(landmark)/2.)):
                x, y = int(landmark[i]), int(landmark[i+5])

                landmark_points.append((x - bb_coord[0], y - bb_coord[1]))
            landmark_list.append(np.array(landmark_points, dtype='double'))

        faces_v = list(zip(*query_faces))[0]
        v_faces = adjust_size_different_images(faces_v, 341, 341/2)

        # Apply super resolution
        super_res_faces_path = os.path.join(
            root_result_folder, 'super_res_faces.pkl')
        faces_sr = []
        if not os.path.exists(super_res_faces_path):
            faces_sr = []
            for idx, face in enumerate(faces_v):
                if face is not None:
                    faces_sr.append(apply_super_res(face))
                else:
                    faces_sr.append(None)
            with open(super_res_faces_path, 'wb') as f:
                pickle.dump(faces_sr, f)
            print("[+] Applied Super Resolution to detected faces")
        else:
            with open(super_res_faces_path, 'rb') as f:
                faces_sr = pickle.load(f)
            print("[+] Super resolution faces already existed!")
        K.clear_session()

        v_faces_sr = adjust_size_different_images(faces_sr, 341, 341)

        temp_1 = []
        temp_2 = []
        for i, face in enumerate(v_faces):
            if face is None:
                temp_1.append(np.zeros((341, 192, 3), dtype=np.uint8))
                temp_2.append(np.zeros((341, 192, 3), dtype=np.uint8))
            else:
                temp_1.append(face)
                temp_2.append(v_faces_sr[i])

        imgs_v = [cv2.imread(q) for q in query]
        for i, img in enumerate(imgs_v):
            if bb[i]:
                cv2.rectangle(img, (bb[i][0], bb[i][1]),
                              (bb[i][2], bb[i][3]), (0, 255, 0), 5)

        # Extract query feature
        faces_features = []

        if self.net == "VGGFace":
            vgg_model = VGGFace(input_shape=(224, 224, 3), pooling='avg')
            out = vgg_model.get_layer(self.search_cfg["feature_descriptor"]).output
            default_vgg = Model(vgg_model.input, out)
            # default_vgg = VGGFace(input_shape=(224,224,3), pooling='avg', include_top=False)

            for face in faces_sr:
                if face is not None:
                    feature = extract_feature_from_face(default_vgg, face)
                    faces_features.append((face, feature))
                else:
                    faces_features.append(None)
            K.clear_session()
        elif self.net == "VGGFace2":
            if os.path.exists(os.path.join(self.query_feature_folder, self.query_name)):
                for idx, face in enumerate(faces_sr):
                    if face is not None:
                        feature = np.load(os.path.join(self.query_feature_folder, self.query_name, f'{self.query_name}{idx}.npy'))
                        faces_features.append((face, feature))
                    else:
                        faces_features.append(None)
            else:
                raise Exception('Query Feature did not exist!')

        print("[+] Extracted feature of query images")


        # Remove Bad Faces in query
        
        query_faces = faces_features

        if self.rmBF_method == 'peking':
            # query_faces = self.remove_bad_faces(faces_features)
            if use_query_shots == True:
                query_shot_feature_path = os.path.join(
                    root_result_folder, 'query_shot_feature.pkl')
                if not os.path.exists(query_shot_feature_path):
                    query_shot_feature = self.extract_query_shot_feature(self.query_shot_folder)
                    with open(query_shot_feature_path, 'wb') as f:
                        pickle.dump(query_shot_feature, f)
                else:
                    with open(query_shot_feature_path, 'rb') as f:
                        query_shot_feature = pickle.load(f)
                temp_query_shot_feature = query_shot_feature
                query_shot_feature = self.remove_bad_faces(query_shot_feature)
                self.sticher.save_query_shot_face(temp_query_shot_feature, query_shot_feature,
                                                    save_path=os.path.join(root_result_folder, "shot_query_example.jpg"))
                query_faces.extend(query_shot_feature)
                # query_faces[0] = None
                # query_faces[1] = None
                # for idx in [0,5,6,9,10,21,22,23,24,25,26,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46]:
                #     query_faces[idx + 4] = None
                # self.sticher.save_query_shot_face(temp_query_shot_feature, query_faces[4:],
                #                                     save_path=os.path.join(root_result_folder, "shot_query_example.jpg"))
            else:
                query_faces = self.remove_bad_faces(faces_features)

        elif self.rmBF_method == 'landmark_based':
            if self.rmBF_landmarks_params['landmark_type'] == 'dlib':
                query_faces = faces_features
                for idx, (face, frame_size) in enumerate(zip(faces_v, frames_size)):
                    if face is not None:
                        if not self.good_face_checker.isGoodFace(face, frame_size):
                            query_faces[idx] = None
            elif self.rmBF_landmarks_params['landmark_type'] == 'mtcnn':
                query_faces = faces_features
                for idx, (face, frame_size, landmark) in enumerate(zip(faces_v, frames_size, landmark_list)):
                    if face is not None:
                        if not self.good_face_checker.isGoodFace(face, frame_size, landmark):
                            query_faces[idx] = None

        elif self.rmBF_method == 'classifier':
            classifier_type = self.rmBF_classifier_params['model']
            query_faces = faces_features
            if use_query_shots == True:
                query_shot_feature_path = os.path.join(
                    root_result_folder, 'query_shot_feature.pkl')
                if not os.path.exists(query_shot_feature_path):
                    query_shot_feature = self.extract_query_shot_feature(self.query_shot_folder)
                    with open(query_shot_feature_path, 'wb') as f:
                        pickle.dump(query_shot_feature, f)
                else:
                    with open(query_shot_feature_path, 'rb') as f:
                        query_shot_feature = pickle.load(f)
                query_faces.extend(query_shot_feature)
            
            faces_sr_with_shot = []
            for face_feature in query_faces:
                if face_feature is not None:
                    faces_sr_with_shot.append(face_feature[0])
                else:
                    faces_sr_with_shot.append(None)
            # faces_sr_with_shot = [face_feature[0] for face_feature in query_faces]

            remove_bad_face_t = time.time() 
            total_extract_feat_t = 0
            for idx, face in enumerate(faces_sr_with_shot):
                if face is not None:
                    good, extract_feat_t = self.good_face_checker.isGoodFace(face, classifier_type=classifier_type)
                    if not good:
                        query_faces[idx] = None
                    total_extract_feat_t += extract_feat_t

            print('Elapsed time for removing bad faces: %f seconds' % (time.time() - remove_bad_face_t - total_extract_feat_t))

            if use_query_shots == True:
                self.sticher.save_query_shot_face(query_shot_feature, query_faces[4:],
                                                    save_path=os.path.join(root_result_folder, "shot_query_example.jpg"))

        # visulize the query after remove bad faces
        temp = []
        for query_face in query_faces[:4]:
            if not query_face:
                temp.append(np.zeros((341, 192, 3), dtype=np.uint8))
            else:
                temp.append(query_face[0])
        temp = adjust_size_different_images(temp, 341, 341)

        frames_faces_label = create_image_label(
            "Detect faces in frames", (192, 350, 3))
        imgs_v = [cv2.resize(img, (341, 192)) for img in imgs_v]
        imgs_v = [frames_faces_label] + imgs_v

        before_sr_label = create_image_label(
            "Before apply SR", (temp_1[0].shape[0], 350, 3))
        temp_1 = [before_sr_label] + temp_1

        after_sr_label = create_image_label(
            "After apply SR", (temp_2[0].shape[0], 350, 3))
        temp_2 = [after_sr_label] + temp_2

        rmbf_label = create_image_label(
            "After remove bad faces", (temp[0].shape[0], 350, 3))
        temp = [rmbf_label] + temp

        self.sticher.stich(matrix_images=[imgs_v, temp_1, temp_2,  temp], title="Preprocess query",
                           save_path=os.path.join(root_result_folder, "preprocess.jpg"), size=None, reduce_size=True)

        query_faces = [query_face for query_face in query_faces if query_face]
        if isStage1:
            print(
                "\n==============================================================================")
            print("\n                       [+] Stage 1 of searching:\n")
            print(
                "==============================================================================")
            stage_1_path = os.path.join(root_result_folder, "stage 1")
            create_stage_folder(stage_1_path)
            start = time.time()
            default_feature_folder = os.path.join(
                self.default_feature_folder, self.search_cfg["feature_descriptor"])
            result = self.stage_1(
                query_faces, default_feature_folder, multiprocess=multiprocess)
            stage_1_execution_time = time.time() - start

            write_result_to_file(self.query_name, result, os.path.join(
                root_result_folder, 'stage 1', "result.txt"))
            write_result(self.query_name, result, os.path.join(
                root_result_folder, "stage_1.pkl"))
            # self.sticher.save_shots_max_images(
            #     result, os.path.join(stage_1_path))

        if isStage2:
            print(
                "\n==============================================================================")
            print("\n                       [+] Stage 2 of searching:\n")
            print(
                "==============================================================================")
            stage_2_path = os.path.join(root_result_folder, "stage 2")
            create_stage_folder(stage_2_path)

            start = time.time()
            stage_1_result_file = os.path.join(
                root_result_folder, "stage_1.pkl")
            with open(stage_1_result_file, 'rb') as f:
                result = pickle.load(f)

            if not os.path.isdir(os.path.join(self.vgg_training_data_folder, self.query_name)):
                os.mkdir(os.path.join(
                    self.vgg_training_data_folder, self.query_name))

            training_set_path = os.path.join(
                self.vgg_training_data_folder, self.query_name, "training_data.pkl")

            print('Training set Path:', training_set_path)
            if not os.path.exists(training_set_path):
                # training_set = self.form_training_set(
                #     result[:100], thresh=0.85, rmBadFaces=None)
                training_set = self.form_training_set_using_best_face_in_each_shot(
                    result[:100], rmBadFaces=None)
                with open(training_set_path, "wb") as f:
                    pickle.dump(training_set, f)
                self.sticher.process_training_set(
                    training_set_path, save_path=os.path.join(self.vgg_training_data_folder, self.query_name), shape=(15, 18))
                print("[+] Builded training data")
            else:
                with open(training_set_path, 'rb') as f:
                    training_set = pickle.load(f)
                print("[+] Loaded training data")

            finetuned_query_faces  = self.stage_2(query_faces, training_set,
                                  multiprocess=multiprocess)
            # finetuned_query_faces_save_path = os.path.join(root_result_folder, 'finetuned_query_faces.pkl')
            # if not os.path.isfile(finetuned_query_faces_save_path):
            #     finetuned_query_faces  = self.stage_2(query_faces, training_set,
            #                           multiprocess=multiprocess)
            #     with open(finetuned_query_faces_save_path, 'wb') as f:
            #         pickle.dump(finetuned_query_faces, f)
            # else:
            #     with open(finetuned_query_faces_save_path, 'rb') as f:
            #         finetuned_query_faces = pickle.load(f)

            #     fine_tune_feature_folder = os.path.join(
            #         self.fine_tune_feature_folder, self.query_name)
            #     result = self.stage_1(
            #         finetuned_query_faces, fine_tune_feature_folder, multiprocess=multiprocess)
            # 
            #     stage_2_execution_time = time.time() - start

            #     write_result_to_file(self.query_name, result, os.path.join(
            #         root_result_folder, 'stage 2', "result.txt"))
            #     write_result(self.query_name, result, os.path.join(
            #         root_result_folder, "stage_2.pkl"))

        if isStage3:
            print(
                "\n==============================================================================")
            print("\n                       [+] Stage 3 of searching:\n")
            print(
                "==============================================================================")
            stage_3_path = os.path.join(root_result_folder, "stage 3")
            create_stage_folder(stage_3_path)
            start = time.time()

            stage_2_result_file = os.path.join(
                root_result_folder, "stage_2.pkl")
            with open(stage_2_result_file, 'rb') as f:
                result = pickle.load(f)

            if not os.path.isdir(os.path.join(self.svm_training_data_folder, self.query_name)):
                os.mkdir(os.path.join(
                    self.svm_training_data_folder, self.query_name))

            training_set_path = os.path.join(
                self.svm_training_data_folder, self.query_name, "training_data.pkl")
            faces_training_set_path = os.path.join(
                self.svm_training_data_folder, self.query_name, "faces_training_data.pkl")

            if not os.path.exists(training_set_path):
                # training_set, faces_training_set = self.form_SVM_training_set(
                #     result[:200], thresh=0.65, rmBadFaces=self._PEsolvePnP)
                training_set, faces_training_set = self.form_SVM_training_set_using_best_face_in_each_shot(result[:1000], rmBadFaces=None)
                

                with open(training_set_path, "wb") as f:
                    pickle.dump(training_set, f)

                with open(faces_training_set_path, 'wb') as f:
                    pickle.dump(faces_training_set, f)
                self.sticher.process_training_set(
                    faces_training_set_path, save_path=os.path.join(self.svm_training_data_folder, self.query_name), shape=(15, 18))

                print("[+] Builded training data")
            else:
                print('Training data already exists')
                with open(training_set_path, 'rb') as f:
                    training_set = pickle.load(f)
                print("[+] Loaded training data")

            result = self.stage_3(query_faces, training_set,
                                  multiprocess=multiprocess)
            stage_3_execution_time = time.time() - start

            write_result_to_file(self.query_name, result, os.path.join(
                root_result_folder, 'stage 3', "result.txt"))
            write_result(self.query_name, result, os.path.join(
                root_result_folder, "stage_3.pkl"))


        with open(os.path.join(self.result_path, self.query_name, 'log.txt'), 'w') as f:
            f.write("Execution time of stage 1 : " +
                    str(stage_1_execution_time))
            f.write("\nExecution time of stage 2 : " +
                    str(stage_2_execution_time))
            f.write("\nExecution time of stage 3 : " +
                    str(stage_3_execution_time))


if __name__ == '__main__':

    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    os.environ['CUDA_VISIBLE_DEVICES'] = sys.argv[1]

    # Load config file
    with open("../cfg/config.json", "r") as f:
        cfg = json.load(f)
    with open('../cfg/search_config.json', 'r') as f:
        search_cfg = json.load(f)
    query_folder = cfg['raw_data']['queries_folder']
    query_shot_folder = cfg['raw_data']['query_shot_folder']

    # names = ['bradley', 'denise', 'dot', 'heather', 'ian', 'jack', 'jane', 'max', 'pat', 'phil', 'sean', 'shirley', 'stacey']
    # names = ["9104", "9115", "9116", "9119", "9124", "9138", "9143"]
    names = ["chelsea", "darrin", "garry", "heather", "jack",
                "jane", "max", "minty", "mo", "zainab"]
    # names = ['chelsea', 'darrin']
    # names = ['mo']
    # names = ['archie', 'billy', 'ian', 'janine', 'peggy', 'phil', 'ryan', 'shirley']

    # Search
    search_engine = SearchEngine(ImageSticher())
    print("[+] Initialized searh engine")
    ext = 'png'
    for name in names:
        query = [
            name + f".1.src.{ext}",
            name + f".2.src.{ext}",
            name + f".3.src.{ext}",
            name + f".4.src.{ext}"
        ]
        masks = [
            name + f".1.mask.{ext}",
            name + f".2.mask.{ext}",
            name + f".3.mask.{ext}",
            name + f".4.mask.{ext}"
        ]

        query = [os.path.join(query_folder, q) for q in query]
        masks = [os.path.join(query_folder, m) for m in masks]
        print("============================================================================\n\n")
        print()
        print("                       QUERY CHARACTER : %s\n\n" % (name))
        print(
            "============================================================================")
        imgs_v = [cv2.imread(q) for q in query]
        masks_v = [cv2.imread(m) for m in masks]
        search_engine.query_name = name

        search_engine.sticher.stich(matrix_images=[imgs_v, masks_v], title="Query : " + name,
                                    save_path=os.path.join(search_engine.result_path, name, "query.jpg"))
        search_engine.query_shot_folder = os.path.join(query_shot_folder, name)
        
        search_engine.searching(
            query, masks, isStage1=False, isStage2=False, isStage3=False, multiprocess=False, use_query_shots=(search_cfg['use_query_shots'] == 'True'))
