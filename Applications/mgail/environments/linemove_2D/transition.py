import tensorflow as tf
import common

class TRANSITION(object):

    def __init__(self, in_dim, out_dim, size, lr, do_keep_prob):

        self.arch_params = {
            'in_dim': in_dim,
            'out_dim': out_dim,
            'n_hidden_0': size[0], #800,
            'n_hidden_1': size[1], #400,
            'do_keep_prob': do_keep_prob
        }

        self.solver_params = {
            'lr': lr, #0.0001,
            'weight_decay': 0.000001,
            'weights_stddev': 0.01,
        }

        self._init_layers()

    def forward(self, state_, action, autoencoder):
        '''
        state_: matrix
        action: matrix
        '''

        if autoencoder is None:
            _input = state_
        else:
            _input, _ = autoencoder.forward(state_)

        concat = tf.concat(concat_dim=1, values=[_input, action], name='input')

        z0 = tf.nn.xw_plus_b(concat, self.weights['0'], self.biases['0'], name='h0')
        h0 = tf.nn.relu(z0)

        z1 = tf.nn.xw_plus_b(h0, self.weights['1'], self.biases['1'], name='h1')
        h1 = tf.nn.relu(z1)

        h1_do = tf.nn.dropout(h1, self.arch_params['do_keep_prob'])

        delta = tf.nn.xw_plus_b(h1_do, self.weights['c'], self.biases['c'], name='delta')

        previous_state = tf.stop_gradient(state_)

        state = previous_state + delta

        # real model for debug
        # v_ = tf.slice(state_, [0, 0], [-1, 2])
        # x_ = tf.slice(state_, [0, 2], [-1, 2])
        # v = tf.clip_by_value(v_ + 0.15 * action, -0.5, 0.5)
        # x = x_ + 0.15 * v
        # state = tf.concat(concat_dim=1, values=[v, x])

        return state

    def backward(self, loss):

        # create an optimizer
        opt = tf.train.AdamOptimizer(learning_rate=self.solver_params['lr'])

        # weight decay
        if self.solver_params['weight_decay']:
            loss += self.solver_params['weight_decay'] * tf.add_n([tf.nn.l2_loss(v) for v in self.trainable_variables])

        # compute the gradients for a list of variables
        grads_and_vars = opt.compute_gradients(loss=loss, var_list=self.weights.values() + self.biases.values())

        mean_abs_grad, mean_abs_w = common.compute_mean_abs_norm(grads_and_vars)

        # apply the gradient
        apply_grads = opt.apply_gradients(grads_and_vars)

        return apply_grads, mean_abs_grad, mean_abs_w

    def train(self, objective):
        self.loss = objective
        self.minimize, self.mean_abs_grad, self.mean_abs_w = self.backward(self.loss)
        self.loss_summary = tf.scalar_summary('loss_t', objective)

    def _init_layers(self):
        weights = {
            '0': tf.Variable(tf.random_normal([self.arch_params['in_dim']    , self.arch_params['n_hidden_0']], stddev=self.solver_params['weights_stddev'])),
            '1': tf.Variable(tf.random_normal([self.arch_params['n_hidden_0'], self.arch_params['n_hidden_1']], stddev=self.solver_params['weights_stddev'])),
            'c': tf.Variable(tf.random_normal([self.arch_params['n_hidden_1'], self.arch_params['out_dim']]   , stddev=self.solver_params['weights_stddev'])),
        }

        biases = {
            '0': tf.Variable(tf.random_normal([self.arch_params['n_hidden_0']], stddev=self.solver_params['weights_stddev'])),
            '1': tf.Variable(tf.random_normal([self.arch_params['n_hidden_1']], stddev=self.solver_params['weights_stddev'])),
            'c': tf.Variable(tf.random_normal([self.arch_params['out_dim']], stddev=self.solver_params['weights_stddev']))
        }
        self.weights = weights
        self.biases = biases
        self.trainable_variables = weights.values() + biases.values()
