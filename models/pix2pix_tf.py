import os
from six.moves import xrange
import time
from models.tf_utils import *


class Config(object):
    """Configuration for model
    """

    batch_size = None
    input_dimensions = None
    log_dir = None

    l1_lambda = None
    gen_conv1_filters = None
    dis_conv1_filters = None
    learning_rate = None
    momentum = None

    def __init__(self,
                 batch_size,
                 input_dimensions,
                 log_dir,
                 l1_lambda=100,
                 gen_conv1_filters=64,
                 dis_conv1_filters=64,
                 learning_rate=0.0002,
                 momentum=0.5):
        self.batch_size = batch_size
        self.input_dimensions = input_dimensions
        self.log_dir = log_dir
        self.l1_lambda = l1_lambda
        self.gen_conv1_filters = gen_conv1_filters
        self.dis_conv1_filters = dis_conv1_filters
        self.learning_rate = learning_rate
        self.momentum = momentum


class Pix2PixParams(object):
    input_image = None

    dis_loss = None
    dis_fake_loss = None
    dis_real_loss = None
    gen_loss = None

    dis_vars = None
    gen_vars = None

    dis_summary = None
    gen_summary = None
    gen_output_summary = None


class Pix2pix(object):
    # Use L1 for less blurring
    # 'Regularize' the generator with tasking it to additionally reduce loss to ground truth.

    _params = Pix2PixParams()
    _saver = None
    _model_name = None
    _config = None

    def __init__(self, config=None, restore_path=None):
        """
        Args:
            config: Hyperparameters of the model
            restore_path: Path to a stored model state.
                     If None, a new model will be created.
        """
        self._model_name = str(type(self).__name__)
        if restore_path is not None:
            config.log_dir = restore_path
        else:
            config.log_dir = str(os.path.join(config.log_dir, self._model_name + "_" + str(time.time())))
        self._config = config
        self._setup_model()

    def save(self, sess, step):
        self._saver.save(sess, self._config.log_dir, global_step=step)

    def restore(self, sess):
        print("Reading checkpoint...")

        ckpt = tf.train.get_checkpoint_state(self._config.log_dir)
        if ckpt and ckpt.model_checkpoint_path:
            ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
            self._saver.restore(sess, os.path.join(self._config.log_dir, ckpt_name))
            return True
        else:
            return False

    def train(self, training_set, epochs, validation_set=None):
        """Fits the model parameters to the dataset.

        Args:
            training_set: Instance of Dataset
            epochs: Number of epochs to train
            validation_set: Data on which to evaluate the model.

        """
        with tf.Session() as sess:

            # Optimizers
            dis_optimizer = tf.train.AdamOptimizer(self._config.learning_rate, beta1=self._config.momentum).minimize(
                self._params.dis_loss, var_list=self._params.dis_vars)
            gen_optimizer = tf.train.AdamOptimizer(self._config.learning_rate, beta1=self._config.momentum).minimize(
                self._params.gen_loss, var_list=self._params.gen_vars)

            # Initialization
            init_op = tf.global_variables_initializer()
            sess.run(init_op)

            writer = tf.summary.FileWriter(self._config.log_dir, sess.graph)

            train_step = 1
            start_time = time.time()

            if self.restore(sess):
                print("Load SUCCESS")
            else:
                print("Load failed...")

            for epoch in xrange(epochs):
                for batch in training_set.batch_iter(stop_after_epoch=True):

                    # Discriminator
                    _, summary_str = sess.run([dis_optimizer, self._params.dis_summary],
                                              feed_dict={self._params.input_image: batch})
                    writer.add_summary(summary_str, train_step)

                    # Generator
                    _, summary_str = sess.run([gen_optimizer, self._params.gen_summary],
                                              feed_dict={self._params.input_image: batch})
                    writer.add_summary(summary_str, train_step)

                    dis_fake_error = self._params.dis_fake_loss.eval({self._params.input_image: batch})
                    dis_real_error = self._params.dis_real_loss.eval({self._params.input_image: batch})
                    gen_error = self._params.gen_loss.eval({self._params.input_image: batch})
                    print("Epoch: [%2d] Train Step: [%2d] Batch: [%2d Time: %4.4f, dis_loss: %.8f, gen_loss: %.8f" % (
                        epoch,
                        train_step,
                        train_step // epoch,
                        time.time() - start_time,
                        dis_fake_error + dis_real_error,
                        gen_error))

                    if train_step % 500 == 0:
                        self.save(sess, train_step)

                    if train_step % 100 == 0:
                        for summary in self.validate(sess, validation_set):
                            writer.add_summary(summary, global_step=train_step)
                    train_step = train_step + 1

    def validate(self, sess, validation_set):
        for batch in validation_set.batch_iter(stop_after_epoch=True):
            _, summary = sess.run([self._params.gen_output_summary], feed_dict={self._params.input_image: batch})
            yield summary

    def _setup_model(self):
        """ Creates a new pix2pix tensorflow model.
        """
        # Create Generator and Discriminator
        self._params.input_image = tf.placeholder(tf.float32, [self._config.batch_size,
                                                               self._config.input_dimensions.height,
                                                               self._config.input_dimensions.width,
                                                               self._config.input_dimensions.depth])

        generator_output = self._generator(self._params.input_image)
        real_image = tf.concat([self._params.input_image, self._params.input_image], 3)
        generator_image = tf.concat([self._params.input_image, generator_output], 3)
        dis_real_pred, dis_real_logits = self._discriminator(real_image, reuse=False)
        dis_fake_pred, dis_fake_logits = self._discriminator(generator_image, reuse=True)

        # Loss functions
        self._params.dis_real_loss = tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(logits=dis_real_logits, labels=tf.ones_like(dis_real_pred)))
        self._params.dis_fake_loss = tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(logits=dis_fake_logits, labels=tf.zeros_like(dis_fake_pred)))
        self._params.dis_loss = self._params.dis_fake_loss + self._params.dis_real_loss
        self._params.gen_loss = \
            tf.reduce_mean(
                tf.nn.sigmoid_cross_entropy_with_logits(logits=dis_fake_logits, labels=tf.ones_like(dis_fake_pred))) \
            + self._config.l1_lambda * tf.reduce_mean(tf.abs(self._params.input_image - generator_output))

        # Tensorboard
        self._params.dis_summary = tf.summary.scalar("dis_loss", self._params.dis_loss)
        self._params.gen_summary = tf.summary.scalar("gen_loss", self._params.gen_loss)
        # TODO: I am not sure this is correct... see validate
        self._params.gen_output_summary = \
            tf.summary.image("gen_output", tf.concat([generator_output, self._params.input_image], 1))

        # Trainable Variables
        train_vars = tf.trainable_variables()
        self._params.dis_vars = [var for var in train_vars if 'd_' in var.name]
        self._params.gen_vars = [var for var in train_vars if 'g_' in var.name]

        self._saver = tf.train.Saver()

    def _discriminator(self, input_output_image, reuse=False):
        with tf.variable_scope("discriminator") as scope:
            if reuse:
                tf.get_variable_scope().reuse_variables()
            else:
                assert not tf.get_variable_scope().reuse

            """
            EXAMPLE:  if _dis_conv1_filters = 64
            image_AB:     [batch_size, 1024, 1024, 1+1]
            h0:           [batch_size, 512,  512,  64]
            h1:           [batch_size, 256,  256,  128]
            h2:           [batch_size, 128,  128,  256]
            h3:           [batch_size, 128,  128,  512]
            """

            h0 = lrelu(conv2d(input_output_image, self._config.dis_conv1_filters, name='d_h0_conv'))
            h1 = lrelu(batch_norm(conv2d(h0, self._config.dis_conv1_filters * 2, name='d_h1_conv'), name='d_bn1'))
            h2 = lrelu(batch_norm(conv2d(h1, self._config.dis_conv1_filters * 4, name='d_h2_conv'), name='d_bn2'))
            h3 = lrelu(batch_norm(conv2d(h2, self._config.dis_conv1_filters * 8, stride_height=1, stride_width=1,
                                         name='d_h3_conv'), name='d_bn3'))
            h4 = linear(tf.reshape(h3, [self._config.batch_size, -1]), 1, scope='d_h3_lin')
            return tf.nn.sigmoid(h4), h4

    def _generator(self, image):
        """
        Args:
            image: tensor of shape [batch_size, height, width, depth]
        """
        with tf.variable_scope("generator") as scope:
            o_c = self._config.input_dimensions.depth
            o_h = self._config.input_dimensions.height
            o_w = self._config.input_dimensions.width
            h2, h4, h8, h16, h32, h64, h128 = \
                int(o_h / 2), int(o_h / 4), int(o_h / 8), int(o_h / 16), int(o_h / 32), int(o_h / 64), int(o_h / 128)
            w2, w4, w8, w16, w32, w64, w128 = \
                int(o_w / 2), int(o_w / 4), int(o_w / 8), int(o_w / 16), int(o_w / 32), int(o_w / 64), int(o_w / 128)
            self.gen_dim = self._config.gen_conv1_filters

            """
            EXAMPLE:  if _gen_conv1_filters = 64
            image:    [batch_size, 1024, 1024, 1]
            e1:       [batch_size, 512, 512, 64]
            e2:       [batch_size, 256, 256, 128]
            e3:       [batch_size, 128, 128, 256]
            e4:       [batch_size, 64,  64,  512]
            e5:       [batch_size, 32,  32,  512]
            e6:       [batch_size, 16,  16,  512]
            e7:       [batch_size, 8,   8,   512]
            e8:       [batch_size, 4,   4,   512]
            """

            # Encoder
            e1 = conv2d(image, self.gen_dim, name='g_e1_conv')
            e2 = batch_norm(conv2d(lrelu(e1), self.gen_dim * 2, name='g_e2_conv'), name='g_bn_e2')
            e3 = batch_norm(conv2d(lrelu(e2), self.gen_dim * 4, name='g_e3_conv'), name='g_bn_e3')
            e4 = batch_norm(conv2d(lrelu(e3), self.gen_dim * 8, name='g_e4_conv'), name='g_bn_e4')
            e5 = batch_norm(conv2d(lrelu(e4), self.gen_dim * 8, name='g_e5_conv'), name='g_bn_e5')
            e6 = batch_norm(conv2d(lrelu(e5), self.gen_dim * 8, name='g_e6_conv'), name='g_bn_e6')
            e7 = batch_norm(conv2d(lrelu(e6), self.gen_dim * 8, name='g_e7_conv'), name='g_bn_e7')
            e8 = batch_norm(conv2d(lrelu(e7), self.gen_dim * 8, name='g_e8_conv'), name='g_bn_e8')

            """
            EXAMPLE:
            d1:       [batch_size, 8,   8,   512+512]
            d2:       [batch_size, 16,   16,   512+512]
            d3:       [batch_size, 32,   32,   512+512]
            d4:       [batch_size, 64,   64,   512+512]
            d5:       [batch_size, 128,   128,   256+256]
            d6:       [batch_size, 256,   256,   128+128]
            d7:       [batch_size, 512,   512,   64+64]
            d8:       [batch_size, 1024,  1024,  1]
            """

            # Decoder
            d1 = tf.nn.dropout(batch_norm(deconv2d(tf.nn.relu(e8),
                                                   [self._config.batch_size, h128, w128, self.gen_dim * 8],
                                                   name='g_d1'), name='g_bn_d1'), 0.5)
            d1 = tf.concat([d1, e7], 3)
            d2 = tf.nn.dropout(batch_norm(deconv2d(tf.nn.relu(d1),
                                                   [self._config.batch_size, h64, w64, self.gen_dim * 8],
                                                   name='g_d2'), name='g_bn_d2'), 0.5)
            d2 = tf.concat([d2, e6], 3)
            d3 = tf.nn.dropout(batch_norm(deconv2d(tf.nn.relu(d2),
                                                   [self._config.batch_size, h32, w32, self.gen_dim * 8],
                                                   name='g_d3'), name='g_bn_d3'), 0.5)
            d3 = tf.concat([d3, e5], 3)
            d4 = tf.nn.dropout(batch_norm(deconv2d(tf.nn.relu(d3),
                                                   [self._config.batch_size, h16, w16, self.gen_dim * 8],
                                                   name='g_d4'), name='g_bn_d4'), 0.5)
            d4 = tf.concat([d4, e4], 3)
            d5 = batch_norm(deconv2d(tf.nn.relu(d4),
                                     [self._config.batch_size, h8, w8, self.gen_dim * 4],
                                     name='g_d5'), name='g_bn_d5')
            d5 = tf.concat([d5, e3], 3)
            d6 = batch_norm(deconv2d(tf.nn.relu(d5),
                                     [self._config.batch_size, h4, w4, self.gen_dim * 2],
                                     name='g_d6'), name='g_bn_d6')
            d6 = tf.concat([d6, e2], 3)
            d7 = batch_norm(deconv2d(tf.nn.relu(d6),
                                     [self._config.batch_size, h2, w2, self.gen_dim],
                                     name='g_d7'), name='g_bn_d7')
            d7 = tf.concat([d7, e1], 3)
            d8 = deconv2d(tf.nn.relu(d7), [self._config.batch_size, o_h, o_w, o_c], name='g_d8')
            return tf.nn.tanh(d8)
