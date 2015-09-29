
from theano import tensor as T
from NetworkBaseLayer import Layer
from ActivationFunctions import strtoact, strtoact_single_joined


class HiddenLayer(Layer):
  def __init__(self, activation="sigmoid", **kwargs):
    """
    :type activation: str | list[str]
    """
    super(HiddenLayer, self).__init__(**kwargs)
    self.set_attr('activation', activation.encode("utf8"))
    self.activation = strtoact(activation)
    self.W_in = [self.add_param(self.create_forward_weights(s.attrs['n_out'],
                                                            self.attrs['n_out'],
                                                            name="W_in_%s_%s" % (s.name, self.name)))
                 for s in self.sources]
    self.set_attr('from', ",".join([s.name for s in self.sources]))


class ForwardLayer(HiddenLayer):
  layer_class = "hidden"

  def __init__(self, sparse_window = 1, **kwargs):
    super(ForwardLayer, self).__init__(**kwargs)
    self.set_attr('sparse_window', sparse_window) # TODO this is ugly
    self.attrs['n_out'] = sparse_window * kwargs['n_out']
    self.z = self.b
    assert len(self.sources) == len(self.masks) == len(self.W_in)
    for s, m, W_in in zip(self.sources, self.masks, self.W_in):
      if s.attrs['sparse']:
        if s.output.ndim == 3: out_dim = s.output.shape[2]
        elif s.output.ndim == 2: out_dim = 1
        else: assert False, s.output.ndim
        self.z += W_in[T.cast(s.output, 'int32')].reshape((s.output.shape[0],s.output.shape[1],out_dim * W_in.shape[1]))
      elif m is None:
        self.z += self.dot(s.output, W_in)
      else:
        self.z += self.dot(self.mass * m * s.output, W_in)
    self.make_output(self.z if self.activation is None else self.activation(self.z))


class EmbeddingLayer(ForwardLayer):
  layer_class = "embedding"

  def __init__(self, **kwargs):
    super(EmbeddingLayer, self).__init__(**kwargs)
    self.z -= self.b
    self.make_output(self.z if self.activation is None else self.activation(self.z))

class EmbeddingLayer(ForwardLayer):
  layer_class = "embedding"
  def __init__(self, **kwargs):
    super(EmbeddingLayer, self).__init__(**kwargs)
    self.make_output(self.z - self.b)

class _NoOpLayer(Layer):
  """
  Use this as a base class if you want to remove all params by the Layer base class.
  Note that this overwrites n_out, so take care of that yourself.
  """
  def __init__(self, **kwargs):
    # The base class will already have a bias.
    # We will reset all this.
    # This is easier for now than to refactor the ForwardLayer.
    kwargs['n_out'] = 1  # This is a hack so that the super init is fast. Will be reset later.
    super(_NoOpLayer, self).__init__(**kwargs)
    self.params = {}  # Reset all params.
    self.set_attr('from', ",".join([s.name for s in self.sources]))


class CopyLayer(_NoOpLayer):
  layer_class = "copy"

  def __init__(self, activation=None, **kwargs):
    super(CopyLayer, self).__init__(**kwargs)
    self.set_attr('n_out', sum([s.attrs['n_out'] for s in self.sources]))
    if activation:
      self.set_attr('activation', activation.encode("utf8"))
    act_f = strtoact_single_joined(activation)

    assert len(self.sources) == len(self.masks)
    zs = []
    for s, m in zip(self.sources, self.masks):
      if m is None:
        zs += [s.output]
      else:
        zs += [self.mass * m * s.output]
    if len(zs) > 1:
      # We get (time,batch,dim) input shape.
      # Concat over dimension, axis=2.
      self.z = T.concatenate(zs, axis=2)
    elif len(zs) == 1:
      self.z = zs[0]
    else:
      raise Exception("CopyLayer needs at least one source")
    self.make_output(act_f(self.z))


class ConstantLayer(_NoOpLayer):
  layer_class = "constant"

  def __init__(self, value, dtype="float32", **kwargs):
    super(ConstantLayer, self).__init__(**kwargs)
    assert not self.sources
    self.set_attr("value", value)
    self.set_attr("dtype", dtype)
    value = T.constant(numpy.array(value), dtype=dtype)
    if value.ndim == 0:
      value = value.dimshuffle('x', 'x', 'x')
    elif value.ndim == 1:
      value = value.dimshuffle('x', 'x', 0)
    else:
      raise Exception("ndim %i not supported" % value.ndim)
    assert value.ndim == 3
    self.make_output(value)


class BinOpLayer(_NoOpLayer):
  layer_class = "bin_op"

  def __init__(self, op=None, **kwargs):
    """
    :type op: str
    """
    super(BinOpLayer, self).__init__(**kwargs)
    assert len(self.sources) == 2
    s1, s2 = self.sources
    assert s1.attrs["n_out"] == s2.attrs["n_out"]
    assert op
    self.set_attr('op', op.encode("utf8"))
    if ":" in op:
      op, act = op.split(":", 1)
    else:
      act = None
    op_f = self.get_bin_op(op)
    act_f = strtoact_single_joined(act)
    self.make_output(act_f(op_f(s1.output, s2.output)))

  @staticmethod
  def get_bin_op(op):
    """
    :type op: str
    :rtype: theano.Op
    """
    m = {"+": "add", "-": "sub", "*": "mul", "/": "div"}
    if op in m:
      op = m[op]
    # Assume it's in theano.tensor.
    return getattr(T, op)


class DualStateLayer(ForwardLayer):
  layer_class = "dual"

  def __init__(self, acts = "relu", acth = "tanh", **kwargs):
    super(DualStateLayer, self).__init__(**kwargs)
    self.set_attr('acts', acts)
    self.set_attr('acth', acth)
    self.activations = [strtoact(acth), strtoact(acts)]
    self.params = {}
    self.W_in = []
    self.act = [self.b,self.b]  # TODO b is not in params anymore?
    for s,m in zip(self.sources,self.masks):
      assert len(s.act) == 2
      for i,a in enumerate(s.act):
        self.W_in.append(self.add_param(self.create_forward_weights(s.attrs['n_out'],
                                                                    self.attrs['n_out'],
                                                                    name="W_in_%s_%s_%d" % (s.name, self.name, i))))
        if s.attrs['sparse']:
          self.act[i] += self.W_in[-1][T.cast(s.act[i], 'int32')].reshape((s.act[i].shape[0],s.act[i].shape[1],s.act[i].shape[2] * self.W_in[-1].shape[1]))
        elif m is None:
          self.act[i] += self.dot(s.act[i], self.W_in[-1])
        else:
          self.act[i] += self.dot(self.mass * m * s.act[i], self.W_in[-1])
    for i in xrange(2):
      self.act[i] = self.activations[i](self.act[i])
    self.make_output(self.act[0])


class StateToAct(ForwardLayer):
  layer_class = "state_to_act"

  def __init__(self, dual=False, **kwargs):
    kwargs['n_out'] = 1
    super(StateToAct, self).__init__(**kwargs)
    self.set_attr("dual", dual)
    self.params = {}
    #self.make_output(T.concatenate([s.act[-1][-1] for s in self.sources], axis=-1).dimshuffle('x',0,1).repeat(self.sources[0].output.shape[0], axis=0))
    self.act = [ T.concatenate([s.act[i][-1] for s in self.sources], axis=-1).dimshuffle('x',0,1) for i in xrange(len(self.sources[0].act)) ] # 1BD
    self.attrs['n_out'] = sum([s.attrs['n_out'] for s in self.sources])
    if dual and len(self.act) > 1:
      self.make_output(self.act[1])
      self.act[0] = T.tanh(self.act[1])
    else:
      self.make_output(self.act[0])
    if 'target' in self.attrs:
      self.output = self.output.repeat(self.index.shape[0],axis=0)
    else:
      self.index = T.ones((1, self.index.shape[1]), dtype = 'int8')


class HDF5DataLayer(Layer):
  recurrent=True
  layer_class = "hdf5"

  def __init__(self, filename, dset, **kwargs):
    kwargs['n_out'] = 1
    super(HDF5DataLayer, self).__init__(**kwargs)
    self.set_attr('filename', filename)
    self.set_attr('dset', dset)
    import h5py
    h5 = h5py.File(filename, "r")
    data = h5[dset][...]
    self.z = theano.shared(value=data.astype('float32'), borrow=True, name=self.name)
    self.make_output(self.z) # QD
    self.index = T.ones((1, self.index.shape[1]), dtype = 'int8')
    h5.close()


class CentroidLayer2(ForwardLayer):
  recurrent=True
  layer_class="centroid2"

  def __init__(self, centroids, output_scores=False, **kwargs):
    assert centroids
    kwargs['n_out'] = centroids.z.get_value().shape[1]
    super(CentroidLayer2, self).__init__(**kwargs)
    self.set_attr('centroids', centroids.name)
    self.set_attr('output_scores', output_scores)
    self.z = self.output
    diff = T.sqr(self.z.dimshuffle(0,1,'x', 2).repeat(centroids.z.get_value().shape[0], axis=2) - centroids.z.dimshuffle('x','x',0,1).repeat(self.z.shape[0],axis=0).repeat(self.z.shape[1],axis=1)) # TBQD
    if output_scores:
      self.make_output(T.cast(T.argmin(T.sqrt(T.sum(diff, axis=3)),axis=2,keepdims=True),'float32'))
    else:
      self.make_output(centroids.z[T.argmin(T.sqrt(T.sum(diff, axis=3)), axis=2)])

    if 'dual' in centroids.attrs:
      self.act = [ T.tanh(self.output), self.output ]
    else:
      self.act = [ self.output, self.output ]


class CentroidLayer(ForwardLayer):
  recurrent=True
  layer_class="centroid"

  def __init__(self, centroids, output_scores=False, entropy_weight=1.0, **kwargs):
    assert centroids
    kwargs['n_out'] = centroids.z.get_value().shape[1]
    super(CentroidLayer, self).__init__(**kwargs)
    self.set_attr('centroids', centroids.name)
    self.set_attr('output_scores', output_scores)
    self.set_attr('entropy_weight', entropy_weight)
    W_att_ce = self.add_param(self.create_forward_weights(centroids.z.get_value().shape[1], 1), name = "W_att_ce_%s" % self.name)
    W_att_in = self.add_param(self.create_forward_weights(self.attrs['n_out'], 1), name = "W_att_in_%s" % self.name)

    zc = centroids.z.dimshuffle('x','x',0,1).repeat(self.z.shape[0],axis=0).repeat(self.z.shape[1],axis=1) # TBQD
    ze = T.exp(T.dot(zc, W_att_ce) + T.dot(self.z, W_att_in).dimshuffle(0,1,'x',2).repeat(centroids.z.get_value().shape[0],axis=2)) # TBQ1
    att = ze / T.sum(ze, axis=2, keepdims=True) # TBQ1
    if output_scores:
      self.make_output(att.flatten(ndim=3))
    else:
      self.make_output(T.sum(att.repeat(self.attrs['n_out'],axis=3) * zc,axis=2)) # TBD

    self.constraints += entropy_weight * -T.sum(att * T.log(att))

    if 'dual' in centroids.attrs:
      self.act = [ T.tanh(self.output), self.output ]
    else:
      self.act = [ self.output, self.output ]


class CentroidEyeLayer(ForwardLayer):
  recurrent=True
  layer_class="eye"

  def __init__(self, n_clusters, output_scores=False, entropy_weight=0.0, **kwargs):
    centroids = T.eye(n_clusters)
    kwargs['n_out'] = n_clusters
    super(CentroidEyeLayer, self).__init__(**kwargs)
    self.set_attr('n_clusters', n_clusters)
    self.set_attr('output_scores', output_scores)
    self.set_attr('entropy_weight', entropy_weight)
    W_att_ce = self.add_param(self.create_forward_weights(n_clusters, 1), name = "W_att_ce_%s" % self.name)
    W_att_in = self.add_param(self.create_forward_weights(self.attrs['n_out'], 1), name = "W_att_in_%s" % self.name)

    zc = centroids.dimshuffle('x','x',0,1).repeat(self.z.shape[0],axis=0).repeat(self.z.shape[1],axis=1) # TBQD
    ze = T.exp(T.dot(zc, W_att_ce) + T.dot(self.z, W_att_in).dimshuffle(0,1,'x',2).repeat(n_clusters,axis=2)) # TBQ1
    att = ze / T.sum(ze, axis=2, keepdims=True) # TBQ1
    if output_scores:
      self.make_output(att.flatten(ndim=3))
    else:
      self.make_output(T.sum(att.repeat(self.attrs['n_out'],axis=3) * zc,axis=2)) # TBD
      #self.make_output(centroids[T.argmax(att.reshape((att.shape[0],att.shape[1],att.shape[2])), axis=2)])

    self.constraints += entropy_weight * -T.sum(att * T.log(att))
    self.act = [ T.tanh(self.output), self.output ]


class ProtoLayer(ForwardLayer):
  recurrent=True
  layer_class="proto"

  def __init__(self, train_proto=True, output_scores=False, **kwargs):
    super(ProtoLayer, self).__init__(**kwargs)
    W_proto = self.create_random_uniform_weights(self.attrs['n_out'], self.attrs['n_out'])
    if train_proto:
      self.add_param(W_proto, name = "W_proto_%s" % self.name)
    if output_scores:
      self.make_output(T.cast(T.argmax(self.z,axis=-1,keepdims=True),'float32'))
    else:
      self.make_output(W_proto[T.argmax(self.z,axis=-1)])
    self.act = [ T.tanh(self.output), self.output ]


class BaseInterpolationLayer(ForwardLayer): # takes a base defined over T and input defined over T' and outputs a T' vector built over an input dependent linear combination of the base elements
  layer_class = "base"

  def __init__(self, base=None, method="softmax", output_weights = False, **kwargs):
    assert base, "missing base in " + kwargs['name']
    kwargs['n_out'] = 1
    super(BaseInterpolationLayer, self).__init__(**kwargs)
    self.set_attr('base', ",".join([b.name for b in base]))
    self.set_attr('method', method)
    self.W_base = [ self.add_param(self.create_forward_weights(bs.attrs['n_out'], 1, name='W_base_%s_%s' % (bs.attrs['n_out'], self.name)), name='W_base_%s_%s' % (bs.attrs['n_out'], self.name)) for bs in base ]
    self.base = T.concatenate([b.output for b in base], axis=2) # TBD
    # self.z : T'
    bz = 0 # : T
    for x,W in zip(base, self.W_base):
      bz += T.dot(x.output,W) # TB1
    z = bz.reshape((bz.shape[0],bz.shape[1])).dimshuffle('x',1,0) + self.z.reshape((self.z.shape[0],self.z.shape[1])).dimshuffle(0,1,'x') # T'BT
    h = z.reshape((z.shape[0] * z.shape[1], z.shape[2])) # (T'xB)T
    if method == 'softmax':
      h_e = T.exp(h).dimshuffle(1,0)
      w = (h_e / T.sum(h_e, axis=0)).dimshuffle(1,0).reshape(z.shape).dimshuffle(2,1,0,'x').repeat(self.base.shape[2], axis=3) # TBT'D
      #w = T.nnet.softmax(h).reshape(z.shape).dimshuffle(2,1,0,'x').repeat(self.base.shape[2], axis=3) # TBT'D
    else:
      assert False, "invalid method %s in %s" % (method, self.name)

    self.set_attr('n_out', sum([b.attrs['n_out'] for b in base]))
    if output_weights:
      self.make_output((h_e / T.sum(h_e, axis=0, keepdims=True)).dimshuffle(1,0).reshape((self.base.shape[0],z.shape[1],z.shape[0])).dimshuffle(2,1,0))
    else:
      self.make_output(T.sum(self.base.dimshuffle(0,1,'x',2).repeat(z.shape[0], axis=2) * w, axis=0, keepdims=False).dimshuffle(1,0,2)) # T'BD


class ChunkingLayer(ForwardLayer): # Time axis reduction like in pLSTM described in http://arxiv.org/pdf/1508.01211v1.pdf
  layer_class = "chunking"

  def __init__(self, chunk_size=1, **kwargs):
    assert chunk_size >= 1
    kwargs['n_out'] = sum([s.attrs['n_out'] for s in kwargs['sources']]) * chunk_size
    super(ChunkingLayer, self).__init__(**kwargs)
    self.set_attr('chunk_size', chunk_size)
    z = T.concatenate([s.output for s in self.sources], axis=2) # TBD
    calloc = T.alloc(numpy.cast[theano.config.floatX](0), self.index.shape[0] + chunk_size - (self.index.shape[0] % chunk_size), z.shape[1], z.shape[2])
    container = T.set_subtensor(
      calloc[:self.index.shape[0]],
      z).dimshuffle(1,0,2) # BT'D
    ialloc = T.alloc(numpy.cast['int32'](1), self.index.shape[0] + chunk_size - (self.index.shape[0] % chunk_size), self.index.shape[1])
    self.index = T.set_subtensor(
      ialloc[:self.index.shape[0]],
      self.index)[::chunk_size] # BT'D

    #self.index = self.index.repeat(self.index.shape[0] % chunk_size, axis = 0)
    self.make_output(container.reshape((container.shape[0], container.shape[1]/chunk_size, container.shape[2] * chunk_size)).dimshuffle(1,0,2)) # T'BD


class CorruptionLayer(ForwardLayer): # x = x + noise
  layer_class = "corruption"
  rng = T.shared_randomstreams.RandomStreams(hash(layer_class) % 4294967295)

  def __init__(self, noise='gaussian', p=0.0, **kwargs):
    kwargs['n_out'] = sum([s.attrs['n_out'] for s in kwargs['sources']])
    super(CorruptionLayer, self).__init__(**kwargs)
    self.set_attr('noise', noise)
    self.set_attr('p', p)

    z = T.concatenate([s.output for s in self.sources], axis=2)
    if noise == 'gaussian':
      z = self.rng.normal(size=z.shape,avg=0,std=p,dtype='float32') + (z - T.mean(z, axis=(0,1), keepdims=True)) / T.std(z, axis=(0,1), keepdims=True)
    self.make_output(z)


import theano
from theano.tensor.nnet import conv
import numpy

class ConvPoolLayer(ForwardLayer):
  layer_class = "convpool"

  def __init__(self, dx, dy, fx, fy, **kwargs):
    kwargs['n_out'] = fx * fy
    super(ConvPoolLayer, self).__init__(**kwargs)
    self.set_attr('dx', dx) # receptive fields
    self.set_attr('dy', dy)
    self.set_attr('fx', fx) # receptive fields
    self.set_attr('fy', fy)

    # instantiate 4D tensor for input
    n_in = numpy.sum([s.output for s in self.sources])
    assert n_in == dx * dy
    x_in  = T.concatenate([s.output for s in self.sources], axis = -1).dimshuffle(0,1,2,'x').reshape(self.sources[0].shape[0], self.sources[0].shape[1],dx, dy)
    range = 1.0 / numpy.sqrt(dx*dy)
    self.W = self.add_param(theano.shared( numpy.asarray(self.rng.uniform(low=-range,high=range,size=(2,1,fx,fy)), dtype = theano.config.floatX), name = "W_%s" % self.name), name = "W_%s" % self.name)
    conv_out = conv.conv2d(x_in, self.W)

    # initialize shared variable for weights.
    w_shp = (2, 3, 9, 9)
    w_bound = numpy.sqrt(3 * 9 * 9)
    W = theano.shared( numpy.asarray(
                rng.uniform(
                    low=-1.0 / w_bound,
                    high=1.0 / w_bound,
                    size=w_shp),
                dtype=input.dtype), name ='W')

    # initialize shared variable for bias (1D tensor) with random values
    # IMPORTANT: biases are usually initialized to zero. However in this
    # particular application, we simply apply the convolutional layer to
    # an image without learning the parameters. We therefore initialize
    # them to random values to "simulate" learning.
    b_shp = (2,)
    b = theano.shared(numpy.asarray(
                rng.uniform(low=-.5, high=.5, size=b_shp),
                dtype=input.dtype), name ='b')

    # build symbolic expression that computes the convolution of input with filters in w
    conv_out = conv.conv2d(input, W)

    # build symbolic expression to add bias and apply activation function, i.e. produce neural net layer output
    # A few words on ``dimshuffle`` :
    #   ``dimshuffle`` is a powerful tool in reshaping a tensor;
    #   what it allows you to do is to shuffle dimension around
    #   but also to insert new ones along which the tensor will be
    #   broadcastable;
    #   dimshuffle('x', 2, 'x', 0, 1)
    #   This will work on 3d tensors with no broadcastable
    #   dimensions. The first dimension will be broadcastable,
    #   then we will have the third dimension of the input tensor as
    #   the second of the resulting tensor, etc. If the tensor has
    #   shape (20, 30, 40), the resulting tensor will have dimensions
    #   (1, 40, 1, 20, 30). (AxBxC tensor is mapped to 1xCx1xAxB tensor)
    #   More examples:
    #    dimshuffle('x') -> make a 0d (scalar) into a 1d vector
    #    dimshuffle(0, 1) -> identity
    #    dimshuffle(1, 0) -> inverts the first and second dimensions
    #    dimshuffle('x', 0) -> make a row out of a 1d vector (N to 1xN)
    #    dimshuffle(0, 'x') -> make a column out of a 1d vector (N to Nx1)
    #    dimshuffle(2, 0, 1) -> AxBxC to CxAxB
    #    dimshuffle(0, 'x', 1) -> AxB to Ax1xB
    #    dimshuffle(1, 'x', 0) -> AxB to Bx1xA
    output = T.nnet.sigmoid(conv_out + b.dimshuffle('x', 0, 'x', 'x'))

    # create theano function to compute filtered images
    f = theano.function([input], output)


class LossLayer(Layer):
  layer_class = "loss"

  def __init__(self, loss, copy_input=None, **kwargs):
    """
    :param theano.Variable index: index for batches
    :param str loss: e.g. 'ce'
    """
    super(LossLayer, self).__init__(**kwargs)
    y = self.y_in
    if copy_input:
      self.set_attr("copy_input", copy_input.name)
    if not copy_input:
      self.z = self.b
      self.W_in = [self.add_param(self.create_forward_weights(source.attrs['n_out'], self.attrs['n_out'],
                                                              name="W_in_%s_%s" % (source.name, self.name)))
                   for source in self.sources]

      assert len(self.sources) == len(self.masks) == len(self.W_in)
      assert len(self.sources) > 0
      for source, m, W in zip(self.sources, self.masks, self.W_in):
        if source.attrs['sparse']:
          self.z += W[T.cast(source.output[:,:,0], 'int32')]
        elif m is None:
          self.z += self.dot(source.output, W)
        else:
          self.z += self.dot(self.mass * m * source.output, W)
    else:
      self.z = copy_input.output
    self.set_attr('from', ",".join([s.name for s in self.sources]))
    if self.y.dtype.startswith('int'):
      i = (self.index.flatten() > 0).nonzero()
    elif self.y.dtype.startswith('float'):
      i = (self.index.flatten() > 0).nonzero()
    self.j = ((T.constant(1.0) - self.index.flatten()) > 0).nonzero()
    loss = loss.encode("utf8")
    self.attrs['loss'] = self.loss
    n_reps = T.switch(T.eq(self.z.shape[0], 1), self.index.shape[0], 1)
    output = self.output.repeat(n_reps,axis=0)
    y_m = output.reshape((output.shape[0]*output.shape[1],output.shape[2]))
    self.known_grads = None
    if loss == 'ce':
      if self.y.type == T.ivector().type:
        nll, pcx = T.nnet.crossentropy_softmax_1hot(x=y_m[i], y_idx=y[i])
      else:
        pcx = T.nnet.softmax(y_m[i])
        nll = -T.dot(T.log(T.clip(pcx, 1.e-38, 1.e20)), y[i].T)
      self.constraints += T.sum(nll)
      self.make_output(pcx.reshape(output.shape))
    elif loss == 'entropy':
      h_e = T.exp(self.y_m) #(TB)
      pcx = T.clip((h_e / T.sum(h_e, axis=1, keepdims=True)).reshape((self.index.shape[0],self.index.shape[1],self.attrs['n_out'])), 1.e-6, 1.e6) # TBD
      ee = self.index * -T.sum(pcx * T.log(pcx)) # TB
      nll, pcx = T.nnet.crossentropy_softmax_1hot(x=self.y_m, y_idx=self.y) # TB
      ce = nll.reshape(self.index.shape) * self.index # TB
      y = self.y.reshape(self.index.shape) * self.index # TB
      f = T.any(T.gt(y,0), axis=0) # B
      self.constraints += T.sum(f * T.sum(ce, axis=0) + (1-f) * T.sum(ee, axis=0))
      self.make_output(pcx.reshape(output.shape))
    elif loss == 'priori':
      pcx = T.nnet.softmax(y_m)[i, y[i]]
      pcx = T.clip(pcx, 1.e-38, 1.e20)  # For pcx near zero, the gradient will likely explode.
      self.constraints += -T.sum(T.log(pcx))
      self.make_output(pcx.reshape(output.shape))
    elif loss == 'sse':
      if self.y.dtype.startswith('int'):
        y_f = T.cast(T.reshape(y, (y.shape[0] * y.shape[1]), ndim=1), 'int32')
        y_oh = T.eq(T.shape_padleft(T.arange(self.attrs['n_out']), y_f.ndim), T.shape_padright(y_f, 1))
        self.constraints += T.mean(T.sqr(y_m[i] - y_oh[i]))
      else:
        self.constraints += T.sum(T.sqr(y_m[i] - y.reshape(y_m.shape)[i]))
      self.make_output(y_m[i].reshape(output.shape))
    else:
      raise NotImplementedError()

    if y.dtype.startswith('int'):
      if y.type == T.ivector().type:
        self.error = T.sum(T.neq(T.argmax(y_m[i], axis=-1), y[i]))
      else:
        self.error = T.sum(T.neq(T.argmax(y_m[i], axis=-1), T.argmax(y[i], axis = -1)))
    elif y.dtype.startswith('float'):
      self.error = T.sum(T.sqr(y_m[i] - y.reshape(y_m.shape)[i]))
    else:
      raise NotImplementedError()
