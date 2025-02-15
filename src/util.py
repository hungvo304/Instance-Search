import numpy as np
import pickle
import os
import shutil
import cv2


def mean_max_similarity(query, shot_faces):
    '''
    Parameters:
    - query: [(face matrix, feature vector)]
    '''
    final_sim = 0
    frames_with_bb_sim = []
    for q_face in query:
        faces_sim = [(shot_face[0], cosine_similarity(
            q_face[1], shot_face[1])) for shot_face in shot_faces]
        faces_sim = sorted(faces_sim, key=lambda x: x[1], reverse=True)
        final_sim += faces_sim[0][1]
        # Each image q_face in query have a list of corresponding faces which sorted based on similarity between faces and q_face. Overall, it a matrix of faces (1)
        frames_with_bb_sim.append(faces_sim)
    return final_sim / len(query), frames_with_bb_sim


def max_max_similarity(query, shot_faces):
    '''
    Parameters:
    - query: [(face matrix, feature vector)]
    '''
    final_sim = 0
    frames_with_bb_sim = []
    for q_face in query:
        faces_sim = [(shot_face, cosine_similarity(
            q_face[1], shot_face[1])) for shot_face in shot_faces]
        faces_sim = sorted(faces_sim, key=lambda x: x[1], reverse=True)
        final_sim = max(faces_sim[0][1], final_sim)
        # Each image q_face in query have a list of corresponding faces which sorted based on similarity between faces and q_face. Overall, it a matrix of faces (1)
        frames_with_bb_sim.append(faces_sim)
    return final_sim, frames_with_bb_sim


def max_mean_similarity(query, shot_faces):
    final_sim = 0
    frames_with_bb_sim = []
    n = len(query)
    total_sim_per_faces = [0] * len(shot_faces)
    for q_face in query:
        faces_sim = [(shot_face[0], cosine_similarity(
            q_face[1], shot_face[1])) for shot_face in shot_faces]
        total_sim_per_faces = [total + sim[1]
                               for total, sim in zip(total_sim_per_faces, faces_sim)]
        frames_with_bb_sim.append(faces_sim)
    return max([sim / n for sim in total_sim_per_faces]), frames_with_bb_sim


def mean_mean_similarity(query, shot_faces):
    final_sim = 0
    frames_with_bb_sim = []
    n = len(query)
    total_sim_per_faces = [0] * len(shot_faces)
    for q_face in query:
        faces_sim = [(shot_face[0], cosine_similarity(
            q_face[1], shot_face[1])) for shot_face in shot_faces]
        total_sim_per_faces = [total + sim[1]
                               for total, sim in zip(total_sim_per_faces, faces_sim)]
        frames_with_bb_sim.append(faces_sim)

    return sum([sim / n for sim in total_sim_per_faces])/len(query), frames_with_bb_sim


def calculate_average_faces_sim(record):
    '''
    Calculate the average similarity of all face in query w.r.t each shot.
    _____________|shotface 1|shotface 2|shotface..|shotface m|
    query_face 1 |  sim_1_1 |  sim_1_2 |    ..    |  sim_1_m |
    query_face 2 |  sim_2_1 |  sim_2_2 |    ..    |  sim_2_m |
    query_face ..|    ..    |    ..    |    ..    |    ..    |
    query_face n |  sim_n_1 |  sim_n_2 |    ..    |  sim_n_m |
    ----------------------------------------------------------
    query_face   | avg_sim_1| avg_sim_2|    ..    | avg_sim_m|

    Parameters:
    record: a list contains 3 elements:
    - shot_id
    - sim(query, shot_id): similarity between input query and current shot
    - a matrix of shape(num_query_face, num_shot_face_detected):
        + num_query_face: #remaining faces in query after remove bad faces
        + num_shot_face_detected: #faces detected of the current shot
        + matrix[i][j]: ((frame file, bb), cosine similarity score
    between 'query face i' and 'shot face j')

    Returns:
    - faces_data: a list of tuple ((frame file, bb), mean_sim)
    '''
    faces_data = []
    faces_matrix = record[2]
    for idx, _ in enumerate(faces_matrix):
        faces_matrix[idx] = sorted(
            faces_matrix[idx], key=lambda x: (x[0][0], x[0][1]))

    col = len(faces_matrix[0])
    row = len(faces_matrix)
    for i in range(col):
        data = faces_matrix[0][i][0]  # Get (frame file, bb)
        mean_sim = 0
        for j in range(row):
            mean_sim += faces_matrix[j][i][1]
        mean_sim /= row
        faces_data.append((data, mean_sim))
    return faces_data


def cosine_similarity(vector_a, vector_b):
    l2_vector_a = np.linalg.norm(vector_a) + 0.001
    l2_vector_b = np.linalg.norm(vector_b) + 0.001
    return np.dot((vector_a / l2_vector_a), (vector_b.T / l2_vector_b))


def euclidDistance(vector_a, vector_b):
    return 100000-np.linalg.norm(vector_a - vector_b)


def matching_audio_feature(feature_a, feature_b):
    '''
    feature_a : list feature of shot a
    feature_b : list feature of shot b
    '''

    row_a = feature_a.shape[0]
    row_b = feature_b.shape[0]
    longer, shorter = feature_a, feature_b
    if row_a < row_b:
        longer, shorter = feature_b, feature_a
    step = 0
    max_cosine = 0
    # list_sum = []
    while True:
        if len(shorter) + step > len(longer):
            break
        sum_dis = 0
        for i in range(len(shorter)):
            sum_dis += cosine_similarity(shorter[i], longer[i + step])
        sum_dis = sum_dis / len(shorter)
        if sum_dis > max_cosine:
            max_cosine = sum_dis
            # list_sum.append(sum_dis)
        step += 1
    return max_cosine


def matching_audio_feature_with_padding(embedding_a, embedding_b, sim_func):
    sec_a = embedding_a.shape[0]
    sec_b = embedding_b.shape[0]

    embedding_b = np.concatenate(
        (np.zeros((sec_a-1, 128)), embedding_b, np.zeros((sec_a-1, 128))))

    all_sims = []
    for i in range(0, sec_a-1 + sec_b):
        total_sim = 0
        total_embeddings = sec_a
        for j in range(0, sec_a):
            sim = sim_func(embedding_a[j], embedding_b[i+j])
            total_sim += sim
            if sim == 0:
                total_embeddings -= 1

        all_sims.append(total_sim/total_embeddings)

    return max(all_sims)


def matching_max_audio_feature_with_padding(embedding_a, embedding_b, sim_func):
    sec_a = embedding_a.shape[0]
    sec_b = embedding_b.shape[0]

    embedding_b = np.concatenate(
        (np.zeros((sec_a-1, 128)), embedding_b, np.zeros((sec_a-1, 128))))

    all_sims = []
    for i in range(0, sec_a-1 + sec_b):
        max_sim = 0
        for j in range(0, sec_a):
            max_sim = max(max_sim, sim_func(embedding_a[j], embedding_b[i+j]))

        all_sims.append(max_sim)

    return max(all_sims)


def mean_max_similarity_audio(query_embedding, test_embedding, matching_func, sim_func):
    num_q = len(query_embedding)
    total_sim = 0
    for emb in query_embedding:
        total_sim += matching_func(emb, test_embedding, sim_func)

    return total_sim / num_q


def max_max_similarity_audio(query_embedding, test_embedding, matching_func, sim_func, max_score=None):
    num_q = len(query_embedding)
    final_sim = 0
    for emb in query_embedding:
        sim = matching_func(emb, test_embedding, sim_func)
        # print('sim', sim)
        if max_score is not None and sim >= 2*max_score: 
            print('sim', sim)
            continue
        final_sim = max(final_sim, sim)

    return final_sim


def get_maximum_matching_score_audio(query_embedding, sim_func):
    num_query_embedding = len(query_embedding)
    
    max_score = 0
    for i in range(0, num_query_embedding):
        for j in range(i+1, num_query_embedding): 
            max_score = max(max_score, matching_max_audio_feature_with_padding(query_embedding[i], query_embedding[j], sim_func))
    return max_score


def pairwise_matching_audio(query_embedding, test_embedding, max_score, sim_func):
    return max_max_similarity_audio(query_embedding, test_embedding, matching_max_audio_feature_with_padding, sim_func, max_score)


def mean_max_similarity_action(query_embedding, test_embedding):
    num_q = len(query_embedding)
    total_sim = 0
    for emb in query_embedding:
        total_sim += cosine_similarity(emb, test_embedding)
    return total_sim / num_q


def mean_max_similarity_semantic(topic_embeddings, test_embeddings):
    '''
    Param:
    - topic_embeddings: list of list of query embeddings
    - test_embeddings: list of test embeddings
    '''
    total = 0
    count = 0
    for query_embeddings in topic_embeddings:
        max_score = 0
        for q_emb in query_embeddings:
            for t_emb in test_embeddings:
                sim_score = cosine_similarity(q_emb, t_emb)
                max_score = max(max_score, sim_score)
        total += max_score
        count += 1

    return total/count 


def write_result_to_file(query_id, result, file_path):
    with open(file_path, 'w') as f:
        for i, record in enumerate(result):
            f.write(str(query_id) + ' Q0 ' + record[0] + ' ' + str(
                i + 1) + ' ' + str(record[1][0][0]) + ' STANDARD\n')


def write_result(query_id, result, file_path):
    with open(file_path, 'wb') as f:
        pickle.dump(result, f)


def create_stage_folder(path):
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    print("Make folder ", path)


def adjust_size_different_images(ImgList, frame_width, desire_face_width):
    MAX_HEIGHT = 0
    result_imgs = []

    frame_width = int(frame_width)
    desire_face_width = int(desire_face_width)
    for img in ImgList:
        if img is not None:
            height, width, _ = img.shape
            new_height = int(desire_face_width * height / width)
            if new_height > MAX_HEIGHT:
                MAX_HEIGHT = new_height

    for img in ImgList:
        if img is not None:
            height, width, _ = img.shape
            new_height = int(desire_face_width * height / width)

            resized_img = cv2.resize(img, (desire_face_width, new_height))
            x = np.vstack(
                tuple([resized_img, np.zeros((MAX_HEIGHT - new_height, desire_face_width, 3), dtype=np.uint8)]))
            x = np.hstack(
                tuple([x, np.zeros((MAX_HEIGHT, frame_width - desire_face_width, 3), dtype=np.uint8)]))
        else:
            x = np.zeros((MAX_HEIGHT, frame_width, 3))

        result_imgs.append(x)

    return result_imgs


def create_image_label(label, size):
    img = np.zeros(size, dtype=np.uint8)
    img.fill(255)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.9
    color = (0, 0, 0)
    line = 2
    text_size = cv2.getTextSize(label, font, font_scale, line)[0]
    textX = (img.shape[1] - text_size[0]) / 2
    textY = (img.shape[0] + text_size[1]) / 2
    cv2.putText(img, label, (int(textX), int(textY)),
                font, font_scale, color, line)
    return img


if __name__ == '__main__':
    result = [['shot239_123', 2], ['shot239_135', 3]]
    write_result_to_file(1, result, 'test.txt')
