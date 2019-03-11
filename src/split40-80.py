import numpy as np
import pickle


def split(video_id):
    k = np.load('video' + str(video_id) + '_feat.npy')
    k = k.reshape(k.shape[0], 1, 4096)

    with open('./order_and_info/order_and_info' + str(video_id) + '.pkl', 'rb') as f:
        order_and_info = pickle.load(f)

    idx = 0
    for order, info in order_and_info:
        print(order, info)
        feat = k[idx:idx+info]
        with open('./vgg-feat/' + order + '.pickle', 'wb') as f:
            pickle.dump(feat, f)
        idx += info


if __name__ == '__main__':
    for i in range(40, 80):
        split(i)
