import numpy as np
import theano as t
import theano.tensor as tt
import cPickle
import pickle
from theano.compile import ViewOp
from theano.gradient import DisconnectedType
from collections import OrderedDict
import optimizers
import math

def weight_decay(objective, params, l1_weight):
    for w in params:
        objective += l1_weight *tt.sum(abs(w))
    return objective

def np_floatX(data):
    return np.asarray(data, dtype=t.config.floatX)

def build_shared_zeros(shape, name):
    """ Builds a theano shared variable filled with a zeros numpy array """
    return t.shared(value=np.zeros(shape, dtype=t.config.floatX),
            name=name, borrow=True)

def detect_nan(node, fn):
    for output in fn.outputs:
        if (not isinstance(output[0], np.random.RandomState) and
            np.isnan(output[0]).any()):
            print '*** NaN detected ***'
            t.printing.debugprint(node)
            print 'Inputs : %s' % [input[0] for input in fn.inputs]
            print 'Outputs: %s' % [output[0] for output in fn.outputs]
            break

def get_params(obj):
    params = {}
    for param in obj:
        params[param.name] = param.get_value()
    return params

def dotproduct(v1, v2):
    return sum((a*b) for a,b in zip(v1,v2))

def length(v):
    return math.sqrt(dotproduct(v, v))

def save_params(fName,saver,session):
    # f = file(fName,'wb')
    # cPickle.dump(obj, f, protocol=cPickle.HIGHEST_PROTOCOL)
    # f.close()
    # pickle.dump(obj,open(fName,"wb"))
    saver.save(session,fName)

def load_params(fName):
    f = file(fName,'rb')
    obj = cPickle.load(f)
    f.close()
    return obj

def relu(x):
    return 0.5 * (x + abs(x))

def bounce(x, v, width):
    x_ = tt.abs_(x) - 2*relu(x-width)
    x_in_range = (x>=0)*(x<=width)
    v_ = v*x_in_range -v*(1-x_in_range)
    return x_, v_

def step_cat(x_cat_, v_cat_, u_ext, u, inv_m, dt, width):

    a = inv_m*(u + u_ext)

    v_cat_ = v_cat_ + a*dt
    x_cat_ = x_cat_ + v_cat_*dt

    x_cat, v_cat = bounce(x_cat_, v_cat_, width)

    return x_cat, v_cat

def get_fans(shape):
    fan_in = shape[0] if len(shape) == 2 else np.prod(shape[1:])
    fan_out = shape[1] if len(shape) == 2 else shape[0]
    return fan_in, fan_out

def uniform_initializer(shape):
    scale = np.sqrt(6. / sum(get_fans(shape)))
    weight = np.random.uniform(-1, 1, size=shape) * scale
    return weight

def create_weight(input_n, output_n, suffix):
    shape = (input_n,output_n)
    weight = t.shared(uniform_initializer(shape).astype(t.config.floatX), name='W_{}'.format(suffix), borrow=True)
    return weight

def create_bias(output_n, value, suffix):
    bs = np.ones(output_n,)
    bs *= value
    bias = t.shared(bs.astype(t.config.floatX), name='b_{}'.format(suffix), borrow=True)
    return bias

class DisconnectedGrad(ViewOp):
    def grad(self, args, g_outs):
        return [ DisconnectedType()() for g_out in g_outs]

    def connection_pattern(self, node):
        return [[False]]

disconnected_grad = DisconnectedGrad()

def calculate_mean_abs_norm(obj):
    abs_norm = 0
    # i = 0
    for o in obj:
        abs_norm += tt.mean(tt.abs_(o))
        # i += 1
    return abs_norm / len(obj)

def dim_to_var(ndim, name="k"):
    if ndim == 1:
        return tt.vector(name)
    elif ndim == 2:
        return tt.matrix(name)
    elif ndim == 3:
        return tt.tensor3(name)
    elif ndim == 4:
        return tt.tensor4(name)
    else:
        raise NotImplementedError

def optimize_function(model, solver_params, config=None):
    """
    Create a optimizing function receives gradients.
    Parameters:
        params - parameters
        config - training configuration
    Returns:
        updating function receives gradients
    """
    gradients_ = [dim_to_var(p.ndim) for p in model.params]

    lr_ = tt.fscalar('lr_')

    updates = optimizers.optimizer(lr=lr_,
                                   model=model,
                                   gradients=gradients_,
                                   solver_params=solver_params)

    return t.function(inputs=[lr_]+ gradients_, outputs=[], updates=updates)

class PARAMS(object):

    def __init__(self, *args):

        self.params = []
        for a in args:
            self.params.append(a)

        #initialize lists for optimizers
        self._accugrads = []  # for adadelta
        self._accugrads2 = []  # for rmsprop_graves
        self._accudeltas = []  # for adadelta

        self._accugrads.extend([build_shared_zeros(p.shape.eval(),
                'accugrad') for p in self.params])

        self._accugrads2.extend([build_shared_zeros(p.shape.eval(),
                'accugrad2') for p in self.params])

        self._accudeltas.extend([build_shared_zeros(p.shape.eval(),
                'accudeltas') for p in self.params])

def create_grad_from_obj(objective, params, decay_val, grad_clip_val):

    if decay_val:
        objective = weight_decay(objective=objective, params=params, l1_weight=decay_val)

    if grad_clip_val:
        objective = t.gradient.grad_clip(objective, -grad_clip_val, grad_clip_val)
        # gradients = [norm_constraint(g, grad_clip_val, range(g.ndim)) for g in gradients]

    gradients = tt.grad(objective, params)
    return gradients

def norm_constraint(tensor_var, max_norm, norm_axes=None, epsilon=1e-7):
    """Max weight norm constraints and gradient clipping
    This takes a TensorVariable and rescales it so that incoming weight
    norms are below a specified constraint value. Vectors violating the
    constraint are rescaled so that they are within the allowed range.
    Parameters
    ----------
    tensor_var : TensorVariable
        Theano expression for update, gradient, or other quantity.
    max_norm : scalar
        This value sets the maximum allowed value of any norm in
        `tensor_var`.
    norm_axes : sequence (list or tuple)
        The axes over which to compute the norm.  This overrides the
        default norm axes defined for the number of dimensions
        in `tensor_var`. When this is not specified and `tensor_var` is a
        matrix (2D), this is set to `(0,)`. If `tensor_var` is a 3D, 4D or
        5D tensor, it is set to a tuple listing all axes but axis 0. The
        former default is useful for working with dense layers, the latter
        is useful for 1D, 2D and 3D convolutional layers.
        (Optional)
    epsilon : scalar, optional
        Value used to prevent numerical instability when dividing by
        very small or zero norms.
    Returns
    -------
    TensorVariable
        Input `tensor_var` with rescaling applied to weight vectors
        that violate the specified constraints.
    """
    ndim = tensor_var.ndim

    if norm_axes is not None:
        sum_over = tuple(norm_axes)
    elif ndim == 2:  # DenseLayer
        sum_over = (0,)
    elif ndim in [3, 4, 5]:  # Conv{1,2,3}DLayer
        sum_over = tuple(range(1, ndim))
    else:
        raise ValueError(
            "Unsupported tensor dimensionality {}."
            "Must specify `norm_axes`".format(ndim)
        )

    dtype = np.dtype(t.config.floatX).type
    norms = tt.sqrt(tt.sum(tt.sqr(tensor_var), axis=sum_over, keepdims=True))
    target_norms = tt.clip(norms, 0, dtype(max_norm))
    constrained_output = \
        (tensor_var * (target_norms / (dtype(epsilon) + norms)))

    return constrained_output