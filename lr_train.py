import tensorflow as tf
import os
from models.lr_model import CleanGAN
from datetime import datetime
import helpers.utils as utils
import logging
from os import listdir, makedirs, error
from os.path import isfile, join
import cv2
import numpy as np
import math

'''
os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID";
os.environ["CUDA_VISIBLE_DEVICES"]="2";
'''
FLAGS = tf.flags.FLAGS

tf.flags.DEFINE_integer('batch_size', 16, 'batch size, default: 16')
tf.flags.DEFINE_bool('validate', True, 'validation flag, default: True')
tf.flags.DEFINE_bool('save_samples', True, 'samples flag, default: False')
tf.flags.DEFINE_float('b0', 1, 'weight for adversarial loss, default: 1')
tf.flags.DEFINE_float('b1', 15, 'weight for cycle consistency loss, default: 10')
tf.flags.DEFINE_float('b2', 7.5, 'weight for identity loss, default: 5')
tf.flags.DEFINE_float('b3', 0.75, 'weight for total variation loss, default: 0.5')
tf.flags.DEFINE_float('learning_rate', 0.0002, 'initial learning rate for Adam, default: 0.0002')
tf.flags.DEFINE_float('beta1', 0.5, 'momentum term of Adam, default: 0.5')
tf.flags.DEFINE_float('beta2', 0.999, 'momentum term of Adam, default: 0.999')
tf.flags.DEFINE_float('epsilon', 1e-8, 'constant for numerical stability of Adam, default: 1e-8')
tf.flags.DEFINE_string('X', '../data/tfrecords/train_x.tfrecords',
                       'X tfrecords file for training, default: data/tfrecords/train_x.tfrecords')
tf.flags.DEFINE_string('Y', '../data/tfrecords/train_y.tfrecords',
                       'Y tfrecords file for training, default: data/tfrecords/train_y.tfrecords')
tf.flags.DEFINE_string('load_model', None,
                       'folder of saved model that you wish to continue training (e.g. checkpoints/lr/20190625-1144), default: None')
tf.flags.DEFINE_integer('max_iter', 400000, 'maximum number of iterations during training, default: 400000')
tf.flags.DEFINE_string('validation_set', '../data/DIV2K/X_validation/', 'validation set')
tf.flags.DEFINE_string('validation_ground_truth', '../data/DIV2K/X_validation_gt/', 'validation ground truth set')


def train():
    if FLAGS.load_model is not None:
        # load the specified model
        checkpoints_dir = "checkpoints/lr/" + FLAGS.load_model.lstrip("checkpoints/lr/")
    else:
        # create checkpoint directory
        current_time = datetime.now().strftime("%Y%m%d-%H%M")
        checkpoints_dir = "checkpoints/lr/{}".format(current_time)
        try:
            os.makedirs(checkpoints_dir)
        except os.error:
            pass

    write_config_file(checkpoints_dir)

    graph = tf.Graph()
    with graph.as_default():
        lr_gan = CleanGAN(
            X_train_file=FLAGS.X,
            Y_train_file=FLAGS.Y,
            batch_size=FLAGS.batch_size,
            b0=FLAGS.b0,
            b1=FLAGS.b1,
            b2=FLAGS.b2,
            b3=FLAGS.b3,
            learning_rate=FLAGS.learning_rate,
            beta1=FLAGS.beta1,
            beta2=FLAGS.beta2,
            epsilon=FLAGS.epsilon
        )
        G1_loss, G2_loss, D1_loss, val_y, x, fake_y= lr_gan.model()
        optimizers = lr_gan.optimize(G1_loss, G2_loss, D1_loss)

        summary_op = tf.summary.merge_all()
        train_writer = tf.summary.FileWriter(checkpoints_dir, graph)
        saver = tf.train.Saver()

    flag_resume = False
    with tf.Session(graph=graph) as sess:
        if FLAGS.load_model is not None:
            '''
            checkpoint = tf.train.get_checkpoint_state(checkpoints_dir)
            meta_graph_path = checkpoint.model_checkpoint_path + ".meta"
            restore = tf.train.import_meta_graph(meta_graph_path)
            restore.restore(sess, tf.train.latest_checkpoint(checkpoints_dir))
            '''
            sess.run(tf.global_variables_initializer())
            latest_ckpt = tf.train.latest_checkpoint(checkpoints_dir)
            print('#'*200)
            print(latest_ckpt)
            saver.restore(sess, latest_ckpt)
            checkpoint = tf.train.get_checkpoint_state(checkpoints_dir)

            meta_graph_path = checkpoint.model_checkpoint_path + ".meta"
            step = int(meta_graph_path.split("-")[2].split(".")[0])
            flag_resume = True
        else:
            sess.run(tf.global_variables_initializer())
            step = 0

        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(sess=sess, coord=coord)
        logging.info('CleanGAN initialized.')
        logging.info('Starting from step {}'.format(step))

        try:
            print_total_parameters()
            ps = 0
            ss = 0
            while (not coord.should_stop()) and step <= FLAGS.max_iter:

                if flag_resume or step == FLAGS.max_iter:
                    flag_resume = False
                    ps, ss = validate(sess, lr_gan, val_y)

                fake_y_val = fake_y.eval()
                _, G1_loss_val, G2_loss_val, D1_loss_val, summary = (
                    sess.run(
                        [optimizers, G1_loss, G2_loss, D1_loss, summary_op],
                        feed_dict={lr_gan.fake_y: fake_y_val,
                                   lr_gan.psnr_validation: ps,
                                   lr_gan.ssim_validation: ss}
                    )
                )

                train_writer.add_summary(summary, step)
                train_writer.flush()

                if step % 1000 == 0:
                    logging.info('-----------Step %d:-------------' % step)
                    logging.info('  G1_loss   : {}'.format(G1_loss_val))
                    logging.info('  G2_loss   : {}'.format(G2_loss_val))
                    logging.info('  D1_loss   : {}'.format(D1_loss_val))
                    if FLAGS.save_samples:
                        save_samples(checkpoints_dir, step, lr_gan, val_y, sess)

                if step % 10000 == 0:
                    save_path = saver.save(sess, checkpoints_dir + "/model.ckpt", global_step=step)
                    logging.info("Model saved in file: %s" % save_path)
                    if FLAGS.validate:
                        ps, ss = validate(sess, lr_gan, val_y)

                step += 1
        except KeyboardInterrupt:
            logging.info('Interrupted')
            coord.request_stop()
        except Exception as e:
            coord.request_stop(e)
        finally:
            save_path = saver.save(sess, checkpoints_dir + "/model.ckpt", global_step=step)
            logging.info("Model saved in file: %s" % save_path)
            # When done, ask the threads to stop.
            coord.request_stop()
            coord.join(threads)


def validate(sess, lr_gan, val_y):
    files = [f for f in listdir(FLAGS.validation_set) if isfile(join(FLAGS.validation_set, f))]
    gt_files = [f for f in listdir(FLAGS.validation_ground_truth) if isfile(join(FLAGS.validation_ground_truth, f))]
    rounds = len(files)
    logging.info('Validating...')
    ps = 0
    ss = 0
    for i in range(rounds):
        img = cv2.imread(FLAGS.validation_set + files[i])
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        im1 = np.zeros([1, img.shape[0], img.shape[1], img.shape[2]])
        im1[0] = img
        im1 = im1.astype('uint8')
        gt = cv2.imread(FLAGS.validation_ground_truth + gt_files[i])
        gt = cv2.cvtColor(gt, cv2.COLOR_BGR2RGB)
        y = val_y.eval(feed_dict={lr_gan.val_x: im1})
        y = y[0]
        ps += psnr(y, gt)
        ss += ssim(y, gt)
    ps /= rounds
    ss /= rounds
    logging.info('Validation completed. PSNR: {:f}, SSIM: {:f}.'.format(ps, ss))
    return ps, ss


def save_samples(checkpoints_dir, step, lr_gan, val_y, sess):
    img_name_803 = '../data/DIV2K/X_validation/0803x4.png'
    img_name_810 = '../data/DIV2K/X_validation/0810x4.png'
    img_name_823 = '../data/DIV2K/X_validation/0823x4.png'
    img_name_829 = '../data/DIV2K/X_validation/0829x4.png'
    output_folder = checkpoints_dir + '/samples'
    try:
        os.makedirs(output_folder)
    except os.error:
        pass

    files_sv = [img_name_803, img_name_810, img_name_823, img_name_829]
    rounds_sv = len(files_sv)

    for i in range(rounds_sv):
        img = cv2.imread(files_sv[i])
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        im1 = np.zeros([1, img.shape[0], img.shape[1], img.shape[2]])
        im1[0] = img
        im1 = im1.astype('uint8')
        y = sess.run(val_y, feed_dict={lr_gan.val_x: im1})
        y = y[0]
        y = cv2.cvtColor(y, cv2.COLOR_RGB2BGR)
        out_name = output_folder + '/' + 'step_' + str(step) + '_img_' + str(i) + '.png'
        cv2.imwrite(out_name, y)


def psnr(imageA, imageB):
    E = imageA.astype("double")/255 - imageB.astype("double")/255
    N = imageA.shape[0] * imageA.shape[1] * imageA.shape[2]
    return round(10 * math.log10(N / np.sum(np.power(E, 2))), 4)

def ssim(im1, im2):
    h, w, d = im1.shape
    ssim = 0
    for i in range(d):
        a = im1[:, :, i]
        b = im2[:, :, i]
        K = [0.01, 0.03]
        L = 255

        C1 = (K[0]*L)**2
        C2 = (K[1]*L)**2
        a = a.astype(float)
        b = b.astype(float)
        mu1 = cv2.GaussianBlur(a, (11, 11), 1.5, cv2.BORDER_ISOLATED)
        mu1 = mu1[5:, 5:]
        mu1 = mu1[:-5, :-5]
        mu2 = cv2.GaussianBlur(b.astype(float), (11, 11), 1.5, cv2.BORDER_ISOLATED)
        mu2 = mu2[5:, 5:]
        mu2 = mu2[:-5, :-5]

        mu1_sq = mu1 ** 2
        mu2_sq = mu2 ** 2
        mu1_mu2 = np.multiply(mu1, mu2)

        sigma1_sq = cv2.GaussianBlur(a**2, (11, 11), 1.5)
        sigma1_sq = sigma1_sq[5:, 5:]
        sigma1_sq = sigma1_sq[:-5, :-5] - mu1_sq

        sigma2_sq = cv2.GaussianBlur(b**2, (11, 11), 1.5)
        sigma2_sq = sigma2_sq[5:, 5:]
        sigma2_sq = sigma2_sq[:-5, :-5] - mu2_sq

        sigma12 = cv2.GaussianBlur(np.multiply(a, b), (11, 11), 1.5)
        sigma12 = sigma12[5:, 5:]
        sigma12 = sigma12[:-5, :-5] - mu1_mu2

        if C1 > 0 and C2 > 0:
            ssim_map = np.divide(np.multiply((2 * mu1_mu2 + C1), (2 * sigma12 + C2)), np.multiply((mu1_sq + mu2_sq + C1), (sigma1_sq + sigma2_sq + C2)))
        else:
            # this is useless
            numerator1 = 2 * mu1_mu2 + C1
            numerator2 = 2 * sigma12 + C2
            denominator1 = mu1_sq + mu2_sq + C1
            denominator2 = sigma1_sq + sigma2_sq + C2
            ssim_map = np.ones((h, w))
            index = np.nonzero(np.clip(np.dot(denominator1, denominator2), a_min=0))
            ssim_map[index] = np.dot(numerator1[index], numerator2[index]) / \
                              np.dot(denominator1[index], denominator2[index])
            index = np.nonzero(denominator1) and np.argwhere(denominator2 == 0)
            ssim_map[index] = numerator1[index] / denominator1[index]
        ssim += np.mean(ssim_map)
    ssim /= d
    return np.round(ssim, 4)


def write_config_file(checkpoints_dir):
    now = datetime.now()
    date_time = now.strftime("%m/%d/%Y, %H:%M:%S")
    with open(checkpoints_dir + '/config.txt', 'w') as c:
        c.write('LOW RESOLUTION MODEL' + '\n')
        c.write(date_time + '\n')
        c.write('Batch size:' + str(FLAGS.batch_size) + '\n')
        c.write('Iterations:' + str(FLAGS.max_iter) + '\n')
        c.write('Adversarial loss term (b0):' + str(FLAGS.b0) + '\n')
        c.write('Cycle consistency loss term (b1):' + str(FLAGS.b1) + '\n')
        c.write('Identity loss term (b2):' + str(FLAGS.b2) + '\n')
        c.write('Total variation loss term (b3):' + str(FLAGS.b3) + '\n')


def print_total_parameters():
    total_parameters = 0
    for variable in tf.trainable_variables():
        # shape is an array of tf.Dimension
        shape = variable.get_shape()
        variable_parameters = 1
        for dim in shape:
            variable_parameters *= dim.value
        total_parameters += variable_parameters
    print('# Total parameters of the network: ', total_parameters, '#')


def main(unused_argv):
    train()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    tf.app.run()

