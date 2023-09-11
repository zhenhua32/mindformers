# Copyright 2023 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Using API to define Baichuan13b Net."""

import math
import numpy as np

try:
    from mindspore._checkparam import Validator
except ImportError:
    import mindspore._checkparam as Validator

from mindspore import context
from mindspore import nn, ops
from mindspore.common.tensor import Tensor
from mindspore.context import ParallelMode
from mindspore.ops import operations as P
from mindspore.parallel._utils import _get_parallel_mode
import mindspore.common.dtype as mstype
from mindspore.common.parameter import Parameter

from mindformers.core.loss.loss import CrossEntropyLoss
from mindformers.models.base_model import BaseModel
from mindformers.models.utils import cell_reuse
from mindformers.modules.layers import Linear, AlibiTensor, _check_input_dtype, _check_past_none_input_none
from mindformers.modules.transformer.op_parallel_config import _check_config
from mindformers.modules.transformer.transformer import AttentionMask
from mindformers.modules.transformer import TransformerOpParallelConfig
from mindformers.tools.register.register import MindFormerModuleType, MindFormerRegister

from mindformers.models.llama.llama import layer_compute_dtype
from mindformers.models.llama.llama_config import LlamaConfig
from mindformers.models.llama.llama_layer import LlamaEmbedding, LlamaFeedForward, LlamaRMSNorm


@MindFormerRegister.register(MindFormerModuleType.MODELS)
class Baichuan13bForCausalLM(BaseModel):
    r"""
        Provide baichuan training loss or logits through network.
        Args:
            config (LlamaConfig): The config of llama model. Baichuan reuses LlamaConfig.

        Inputs:
            input_ids(Tensor): the tokenized inputs with datatype int32, Tensor of shape :math:`(batch, seq\_length)`.
            label_ids(Tensor): the tokenized labels with datatype int32, Tensor of shape :math:`(batch, seq\_length)`
            input_position(Tensor): current position, used by model.predict
            (bool, optional): Default: True.
            attention_mask(Tensor): Reserved param, not used.
            batch_valid_length(Tensor): Reserved param, not used.

        Returns:
            Tensor, the loss or logits of the network.

        Examples:
            >>> from mindformers.models.llama import LlamaConfig, Baichuan13bForCausalLM
            >>> config = LlamaConfig(batch_size=2)
            >>> network = Baichuan13bForCausalLM(config=config)
        """

    @cell_reuse()
    def __init__(self, config: LlamaConfig = None):
        super(Baichuan13bForCausalLM, self).__init__(config, auto_prefix=True)
        _check_config(config.parallel_config)
        self.model = Baichuan13bModel(config=config)

        self.lm_head = Linear(in_channels=config.hidden_size,
                              out_channels=config.vocab_size,
                              has_bias=False,
                              compute_dtype=config.compute_dtype,
                              param_init_type=config.param_init_type,
                              weight_init="normal")  # meta default: xavier_normal
        if config.parallel_config.vocab_emb_dp:
            self.lm_head.shard(strategy_matmul=(
                (config.parallel_config.data_parallel, 1), (1, 1)))
        else:
            self.lm_head.shard(strategy_matmul=((config.parallel_config.data_parallel, 1),
                                                (config.parallel_config.model_parallel, 1)))
        if config.parallel_config.pipeline_stage > 1:
            self.lm_head.pipeline_stage = config.parallel_config.pipeline_stage - 1

        self.use_past = config.use_past
        self.ignore_token_id = config.ignore_token_id
        self.pad_token_id = config.pad_token_id
        parallel_config = config.parallel_config
        self.loss = CrossEntropyLoss(parallel_config=parallel_config)
        dp = parallel_config.data_parallel
        self.slice = P.StridedSlice().shard(((dp, 1),))
        self.not_equal = P.NotEqual().shard(((dp, 1), ()))
        self.reshape = P.Reshape()
        self.cast = P.Cast()
        self.mul = P.Mul().shard(((parallel_config.data_parallel, 1),
                                  (parallel_config.data_parallel, 1)))
        self.add = P.Add().shard(((parallel_config.data_parallel, 1), ()))

        if self.use_past:
            self.input_mask_all_ones = Tensor(
                np.ones((self.config.batch_size, self.config.seq_length), np.float32), mstype.float32)

        # used for increased predict
        self.is_first_iteration = True

        self.load_checkpoint(config)

    # pylint: disable=W0613
    def prepare_inputs_for_generation(self, input_ids, **kwargs):
        return {
            "input_ids": Tensor(input_ids, mstype.int32)
        }

    # pylint: disable=W0613
    def construct(self, input_ids, labels=None, input_position=None, position_ids=None, attention_mask=None,
                  input_embeds=None, init_reset=True, batch_valid_length=None):
        """Baichuan13bForCausalLM forward."""
        bsz, seqlen = input_ids.shape
        if self.training:
            tokens = self.slice(input_ids, (0, 0), (bsz, seqlen - 1), (1, 1))
        else:
            tokens = input_ids

        input_mask = self.cast(self.not_equal(
            tokens, self.pad_token_id), mstype.float32) \
            if not self.use_past else self.input_mask_all_ones

        output = self.model(tokens, input_mask, input_position,
                            init_reset, batch_valid_length)
        logits = self.lm_head(output)

        logits = self.cast(logits, mstype.float32)
        if not self.training:
            logits = self.reshape(logits, (bsz, seqlen, -1))

            # makes cast effective to avoid allgather issue in Mindspore1.10
            input_mask = self.add(input_mask, 1)
            return logits, tokens, input_mask

        if labels is None:
            labels = self.slice(input_ids, (0, 1), (bsz, seqlen), (1, 1))
        else:
            labels = self.slice(labels, (0, 1), (bsz, seqlen), (1, 1))
            label_mask = self.cast(self.not_equal(
                labels, self.ignore_token_id), mstype.float32)
            input_mask = self.mul(input_mask, label_mask)

        if logits.ndim > 2:
            logits = self.reshape(logits, (-1, logits.shape[-1]))
        labels = self.reshape(labels, (-1,))
        input_mask = self.reshape(input_mask, (-1,))
        loss = self.loss(logits, labels, input_mask)
        return loss


class Baichuan13bModel(BaseModel):
    r"""
    Transformer decoder consisting of *config.num_hidden_layers* layers. Each layer is a [`Baichuan13bDecoderLayer`]
    Args:
        config(LlamaConfig): the config of network

    Inputs:
        input_ids: the tokenized inputs with datatype int32

    Returns:
        output: Tensor, the output of Baichuan13b decoderlayer
    """

    def __init__(self,
                 config: LlamaConfig = None):
        super().__init__(config, auto_prefix=True)
        _check_config(config.parallel_config)
        if config.batch_size or config.use_past:
            Validator.check_positive_int(config.batch_size)
        self.parallel_config = config.parallel_config
        self.vocab_size = config.vocab_size
        self.num_layers = config.num_layers
        self.pad_token_id = config.pad_token_id
        self.slice = P.StridedSlice().shard(((1, 1),))
        self.reshape = P.Reshape()
        self.cast = P.Cast()

        self.tok_embeddings = LlamaEmbedding(config.vocab_size, config.hidden_size,
                                             param_init_type=config.param_init_type,
                                             parallel_config=config.parallel_config)
        self.tok_embeddings.pipeline_stage = 0
        if config.parallel_config.pipeline_stage > 1:
            self.tok_embeddings.set_comm_fusion(2)
        else:
            self.tok_embeddings.set_comm_fusion(
                config.parallel_config.gradient_aggregation_group)

        self.layers = nn.CellList()
        for layer_id in range(config.num_layers):
            layer = Baichuan13bDecodeLayer(config.batch_size,
                                           config.seq_length,
                                           layer_id,
                                           dim=config.hidden_size,
                                           n_heads=config.num_heads,
                                           multiple_of=config.multiple_of,
                                           norm_eps=config.rms_norm_eps,
                                           compute_dtype=config.compute_dtype,
                                           layernorm_compute_dtype=config.layernorm_compute_type,
                                           softmax_compute_dtype=config.softmax_compute_type,
                                           param_init_type=config.param_init_type,
                                           use_past=config.use_past,
                                           compute_in_2d=config.compute_in_2d,
                                           parallel_config=config.parallel_config)
            layer_compute_dtype(layer, layer_id, config.offset,
                                config.parallel_config, self.num_layers)
            self.layers.append(layer)

        self.norm_out = LlamaRMSNorm(
            config.hidden_size, config.rms_norm_eps,
            compute_type=config.layernorm_compute_type)
        if config.parallel_config.pipeline_stage > 1:
            self.norm_out.set_comm_fusion(2)
        else:
            self.norm_out.set_comm_fusion(
                config.parallel_config.gradient_aggregation_group)
        if config.compute_in_2d:
            self.norm_out.shard(((config.parallel_config.data_parallel, 1),))
        else:
            self.norm_out.shard(
                ((config.parallel_config.data_parallel, 1, 1),))
        self.norm_out.pipeline_stage = config.parallel_config.pipeline_stage - 1
        if config.parallel_config.pipeline_stage > 1:
            self.norm_out.set_comm_fusion(2)
        else:
            self.norm_out.set_comm_fusion(
                config.parallel_config.gradient_aggregation_group)

        self.get_attention_mask = AttentionMask(
            config.seq_length, parallel_config=config.parallel_config.dp_mp_config).to_float(config.compute_dtype)
        self.not_equal = P.NotEqual().shard(
            ((config.parallel_config.data_parallel, 1), ()))
        self.build_alibi_tensor = AlibiTensor(
            seq_length=config.seq_length, num_heads=config.num_heads, parallel_config=config.parallel_config)

        # used for increased predict
        self.gather = P.Gather().shard(((1, 1), (1,)))
        # when in train process,it's always True;when in predict process,only first iteration is True.
        self.is_first_iteration = True
        self.all_ones_attention_mask = P.Ones()((1, 1, 1), mstype.float32)
        self.use_past = config.use_past
        self.input_position_delta = Tensor(
            np.arange(0, config.batch_size), mstype.int32) * config.seq_length
        self.sub = P.Sub()
        self.tile = P.Tile()

    def construct(self, input_ids: Tensor, input_mask: Tensor,
                  input_position=None, init_reset=True, batch_valid_length=None):
        """Forward of Baichuan13b model."""
        _ = input_position
        # (b, t, d) , dp, 1, 1
        h = self.tok_embeddings(input_ids)
        mask = self.get_attention_mask(input_mask)
        alibi_tensor = self.build_alibi_tensor(input_mask, h.dtype)

        # dp,1,1 -> dp,1,1
        for i in range(self.num_layers):
            h, _ = self.layers[i](
                h, alibi_tensor, mask, init_reset=init_reset, batch_valid_length=batch_valid_length)
        # dp,1,1 -> dp,1,1
        output = self.norm_out(h)
        return output


class Baichuan13bDecodeLayer(nn.Cell):
    r"""
        Transformer Layer. This is an implementation of the single layer of the transformer
        encoder layer, including multihead attention and feedward layer.

        Args:
            batch_size(int): The batch size of the input tensor when do increnmental prediction. Should be a positive
                value. When do training or prediction, the argument will not work and the user can just pass None to
                the argument.
            seq_length(int): The input sequence length.
            layer_id(int): The layer id of current transformer block layer.
            dim(int): The hidden size of the input.
            num_heads(int): The number of the heads.
            multiple_of(int): The SwiGLU hidden layer size multiple of large power of 2.
            norm_eps (float): The epsilon value of the denominator. Default 1e-5.
            compute_dtype(dtype.Number): The computation type of the layer.
                Should be mstype.float32 or mstype.float16. Default mstype.float32.
            layernorm_compute_type(dtype.Number): The computation type of the norm.
                Should be mstype.float32 or mstype.float16. Default mstype.float32.
            softmax_compute_type(dtype.Number): The computation type of the softmax in the attention.
                Should be mstype.float32 or mstype.float16. Default mstype.float32.
            param_init_type(dtype.Number): The parameter initialization type of the module.
                Should be mstype.float32 or mstype.float16. Default mstype.float32.
            use_past(bool): Use the past state to compute, used for incremental prediction. For example, if we have two
                words and want to generate the ten more words. We just need to compute the two words' state only once,
                and generate the next word one by one. When use_past is True, there are two steps to run the prediction.
                In the first step, set the is_first_iteration to be True by
                `model.add_flags_recursive(is_first_iteration=True)`, and pass the full inputs. Then, set the
                is_first_iteration to be False by `model.add_flags_recursive(is_first_iteration=False)`.
                At this moment, pass the single step's input tensor, and loop it. Default False.
            parallel_config(OpParallelConfig, MoEParallelConfig): The parallel configure. When MoE is applied,
                MoEParallelConfig is effective, otherwise OpParallelConfig is effective. Default `default_dpmp_config`,
                an instance of `OpParallelConfig` with default args.

        Inputs:
            - **x** (Tensor) - Float Tensor, shape should be [batch_size, seq_length, hidden_size] or
              [batch_size * seq_length, hidden_size], if the use_past is False or is_first_iteration=True. Otherwise,
              should be [batch_size, 1, hidden_size]
            - **alibi_tensor** (Tensor) - Alibi Tensor.
            - **input_mask** (Tensor) - Float Tensor, If the use_past is False or is_first_iteration=True,
              the attention mask matrix should ba [batch_size, seq_length, seq_length], or None. None means there will
              be no mask in softmax computation. Otherwise, should be [batch_size, 1, hidden_size]
            - **init_reset** (Tensor) - A bool tensor with shape [1], used to clear the past key parameter and
              past value parameter used in the incremental prediction. Only valid when use_past is True. Default True.
            - **batch_valid_length** (Tensor) - Int32 tensor with shape [batch_size] the past calculated the index.
              Used for incremental prediction when the use_past is True. Default None.

        Outputs:
            Tuple, a tuple contains(`output`, `layer_present`).

            - **output** (Tensor) - The float tensor of the output of the layer with
              shape (batch_size, seq_length, hidden_size) or (batch_size * seq_length, hidden_size), if the use_past is
              False or is_first_iteration=True. Otherwise, it will be (batch_size, 1, hidden_size)

            - **layer_present** (Tuple) - A tuple of the Tensor of the projected key and value vector with
              ((batch_size, num_heads, size_per_head, seq_length),
              (batch_size, num_heads, seq_length, size_per_head)).

    """

    def __init__(self,
                 batch_size,
                 seq_length,
                 layer_id,
                 dim: int = 512,
                 n_heads: int = 8,
                 multiple_of: int = 256,
                 norm_eps: float = 1e-5,
                 compute_dtype=mstype.float16,
                 layernorm_compute_dtype=mstype.float32,
                 softmax_compute_dtype=mstype.float32,
                 param_init_type=mstype.float32,
                 use_past=False,
                 compute_in_2d=False,
                 parallel_config=TransformerOpParallelConfig()):
        super().__init__()
        if batch_size or use_past:
            Validator.check_positive_int(batch_size)
        self.batch_size = batch_size
        self.use_past = use_past
        self.compute_in_2d = compute_in_2d
        self.seq_length = seq_length
        self.layer_id = layer_id
        self.hidden_size = dim
        self.n_head = n_heads
        self.head_dim = self.hidden_size // self.n_head
        self.is_first_iteration = True

        _check_config(parallel_config)
        if self.n_head % parallel_config.model_parallel != 0:
            raise ValueError("For 'MultiHeadAttention', the class variable 'n_head' must be a multiple of "
                             "'parallel_config.model_parallel', but got the n_head is {} "
                             "and the parallel_config.model_parallel  is {}."
                             .format(self.n_head, parallel_config.model_parallel))
        if self.hidden_size % parallel_config.model_parallel != 0:
            raise ValueError(
                "For 'TransformerEncoderLayer', the class variable 'hidden_size' must be divisibled by "
                "the 'parallel_config.model_parallel', but got the hidden_size is {} and parallel_config."
                " model_parallel is {}.".format(self.hidden_size, parallel_config.model_parallel))
        self.attention_norm = LlamaRMSNorm(self.hidden_size, norm_eps, compute_type=layernorm_compute_dtype)
        self.attention_norm.shard(((parallel_config.data_parallel, 1, 1),))
        self.ffn_norm = LlamaRMSNorm(self.hidden_size, norm_eps, compute_type=layernorm_compute_dtype)
        self.ffn_norm.shard(((parallel_config.data_parallel, 1, 1),))

        self.attention = Baichuan13bAttention(batch_size=batch_size,
                                              src_seq_length=seq_length,
                                              tgt_seq_length=seq_length,
                                              dim=dim,
                                              n_heads=n_heads,
                                              compute_dtype=compute_dtype,
                                              softmax_compute_dtype=softmax_compute_dtype,
                                              param_init_type=param_init_type,
                                              use_past=use_past,
                                              compute_in_2d=compute_in_2d,
                                              parallel_config=parallel_config)
        self.feed_forward = LlamaFeedForward(dim=self.hidden_size,
                                             hidden_dim=4 * self.hidden_size,
                                             multiple_of=multiple_of,
                                             compute_dtype=compute_dtype,
                                             param_init_type=param_init_type,
                                             parallel_config=parallel_config)
        self.add = P.Add()
        if self.compute_in_2d:
            self.attention_norm.shard(((parallel_config.data_parallel, 1),))
            self.ffn_norm.shard(((parallel_config.data_parallel, 1),))
            self.add.shard(((parallel_config.data_parallel, 1),
                            (parallel_config.data_parallel, 1)))
            self.feed_forward.mul.shard(((parallel_config.data_parallel, parallel_config.model_parallel),
                                         (parallel_config.data_parallel, parallel_config.model_parallel)))
        else:
            self.attention_norm.shard(((parallel_config.data_parallel, 1, 1),))
            self.ffn_norm.shard(((parallel_config.data_parallel, 1, 1),))
            self.add.shard(((parallel_config.data_parallel, 1, 1),
                            (parallel_config.data_parallel, 1, 1)))
        self.dtype = compute_dtype
        self.key_past = None
        self.value_past = None
        self.reshape = P.Reshape()

        if self.use_past:
            # operator used for state reuse
            self.reducesum = P.ReduceSum().shard(((1, 1, 1, 1),))
            self.not_equal = P.NotEqual().shard(((1, 1, 1, 1), ()))
            self.slice = P.StridedSlice().shard(((1, 1, 1, 1),))
            size_per_head = self.hidden_size // self.n_head
            self.key_shape = (batch_size, self.n_head,
                              seq_length, size_per_head)
            self.value_shape = (batch_size, self.n_head,
                                seq_length, size_per_head)
            # parameters saving key and value states
            self.key_past = Parameter(
                Tensor(np.zeros(shape=self.key_shape), self.dtype), name="key_past")
            self.value_past = Parameter(
                Tensor(np.zeros(shape=self.value_shape), self.dtype), name="value_past")
            self.tile = P.Tile().shard(((1, 1),))
            self.mul = P.Mul().shard(((1, 1, 1, 1), (1,)))
            self.assign = P.Assign().shard(((1, 1, 1, 1), (1, 1, 1, 1)))

    def construct(self, x, alibi_tensor, input_mask=None, init_reset=True, batch_valid_length=None):
        """ Forward of transformer block. """
        self._check_input(x, alibi_tensor, input_mask,
                          init_reset, batch_valid_length)
        if self.compute_in_2d:
            x = self.reshape(x, (-1, x.shape[-1]))
        # dp, 1, 1 -> dp, 1, 1
        input_x = self.attention_norm(x)
        key_reset = None
        value_reset = None

        if self.use_past and self.is_first_iteration:
            # reset states, init_reset True for reuse and False for reset
            self.assign(self.key_past, self.mul(
                self.key_past, init_reset.astype(self.dtype)))
            key_reset = self.key_past
            self.assign(self.value_past, self.mul(
                self.value_past, init_reset.astype(self.dtype)))
            value_reset = self.value_past
            # add dependency for desired execution order
            input_x = ops.depend(input_x, key_reset)
            input_x = ops.depend(input_x, value_reset)

        # dp, 1, 1 -> dp, 1, 1
        h, layer_present = self.attention(input_x, alibi_tensor, input_mask,
                                          self.key_past, self.value_past, batch_valid_length)
        h = self.add(x, h)
        # dp, 1, 1 -> dp, 1, 1
        ffn_norm = self.ffn_norm(h)
        # dp, 1, 1 -> dp, 1, 1
        ffn_out = self.feed_forward(ffn_norm)

        value_update = None
        key_update = None
        if self.use_past:
            # current key and value
            key_present, value_present = layer_present
            # update key and value calculated this step
            self.assign(self.key_past, key_present)
            key_update = self.key_past
            self.assign(self.value_past, value_present)
            value_update = self.value_past
            # add dependency for desired execution order
            key_update = ops.depend(key_update, key_reset)
            value_update = ops.depend(value_update, value_reset)

        # add dependency for desired execution order
        ffn_out = ops.depend(ffn_out, value_update)
        ffn_out = ops.depend(ffn_out, key_update)
        # if shape is 3d, we reshape the inputs of the add
        out = self.add(h, ffn_out)
        return out, layer_present

    def _check_input(self, x, alibi_tensor, input_mask, init_reset, batch_valid_length):
        r"""Check inputs"""
        _check_input_dtype(
            x.dtype, "x", [mstype.float32, mstype.float16], self.cls_name)
        _check_input_dtype(alibi_tensor.dtype, "alibi_tensor",
                           [mstype.float32, mstype.float16], self.cls_name)
        if input_mask is not None:
            _check_input_dtype(input_mask.dtype, "input_mask",
                               [mstype.float32, mstype.float16], self.cls_name)

        init_reset_is_tensor = isinstance(init_reset, Tensor)
        init_reset_is_default = init_reset is True
        batch_valid_length_is_tensor = isinstance(batch_valid_length, Tensor)
        batch_is_default = batch_valid_length is None
        _check_past_none_input_none(self.use_past, "init_reset", self.cls_name, True, init_reset_is_tensor,
                                    init_reset_is_default)
        _check_past_none_input_none(self.use_past, "batch_valid_length", self.cls_name, None,
                                    batch_valid_length_is_tensor, batch_is_default)

        if self.use_past:
            _check_input_dtype(init_reset.dtype, "init_reset",
                               [mstype.bool_], self.cls_name)
            _check_input_dtype(batch_valid_length.dtype, "batch_valid_length",
                               [mstype.int32], self.cls_name)
        return True


class Baichuan13bAttention(nn.Cell):
    r"""
    This is an implementation of multihead attention in Baichuan13b.

    Args:
            - **batch_size** (int): The batch size of the input tensor when do increnmental prediction. Should be a
                positive value.
                When do training or prediction, the argument will not work and the user can just pass None to the
                argument.
            - **src_seq_length** (int): The sequence length of the query vector.
            - **tgt_seq_length** (int): The sequence length of the key and value vector.
            - **dim** (int): The hidden size of the input.
            - **n_heads** (int): The number of the heads.
            - **compute_dtype** (dtype.Number): The computation type of dense. Default mstype.float16.
                Should be mstype.float32 or mstype.float16.
            - **softmax_compute_type** (dtype.Number): The type of softmax computation module. Default mstype.float32.
                Should be mstype.float32 or mstype.float16.
            - **param_init_type** (dtype.Number): The parameter initialization type of the module. Default mstype.
                float32. Should be mstype.float32 or mstype.float16.
            - **use_past** (bool): Use the past state to compute, used for incremental prediction.
                For example, if we have two words and want to generate the ten more words.
                We just need to compute the two words' state only once, and generate the next word one by one.
                When use_past is True, there are two steps to run the prediction.
                In the first step, set the is_first_iteration to be True by
                `model.add_flags_recursive(is_first_iteration=True)`, and pass the full inputs. Then, set the
                is_first_iteration to be False by `model.add_flags_recursive(is_first_iteration=False)`. At this moment,
                pass the single step's input tensor, and loop it. Default False.
            - **parallel_config** (OpParallelConfig): The parallel configure. Default `default_dpmp_config`,
                an instance of `OpParallelConfig` with default args.

    Inputs:
            - **x** (Tensor) - The input tokens with shape (batch_size, src_seq_length, hidden_size) or
                (batch_size * src_seq_length, hidden_size), if the use_past is False or is_first_iteration=True.
                Otherwise, must be (batch_size, 1, hidden_size)
            - **alibi_tensor** (Tensor) - Alibi Tensor.
            - **attention_mask** (Tensor) - If the use_past is False or is_first_iteration=True, the attention mask
                matrix should ba (batch_size, src_seq_length, tgt_seq_length), or None. None means there will be no mask
                in softmax computation. Otherwise, the mask must be (batch_size, 1, tgt_seq_length)
            - **key_past** (Tensor) - Float16 tensor with shape (batch_size, num_heads, size_per_head, tgt_seq_length).
                The past calculated key vector. Used for incremental prediction when the use_past is True.
                Default None.
            - **value_past** (Tensor) - Float16 tensor with shape (batch_size, num_heads, tgt_seq_length,
                size_per_head).
                The past calculated value vector. Used for incremental prediction when the use_past is True.
                Default None.
            - **batch_valid_length** (Tensor) - Int32 tensor with shape (batch_size,) the past calculated the index.
                Used for incremental prediction when the use_past is True. Default None.

    Outputs:
            Tuple, a tuple contains(`output`, `layer_present`)

            - **output** (Tensor) - Tensor, the float tensor of the output of the layer with
                shape (batch_size, src_seq_length, hidden_size) or (batch_size * src_seq_length, hidden_size),
                if the use_past is False or is_first_iteration=True. Otherwise, it will be (batch_size, 1, hidden_size).

            - **layer_present** (Tuple) - A tuple of the Tensor of the projected key and value vector with
                ((batch_size, num_heads, size_per_head, tgt_seq_length),
                (batch_size, num_heads, tgt_seq_length, size_per_head)).
    """

    def __init__(self,
                 batch_size,
                 src_seq_length,
                 tgt_seq_length,
                 dim: int = 512,
                 n_heads: int = 8,
                 compute_dtype=mstype.float16,
                 softmax_compute_dtype=mstype.float32,
                 param_init_type=mstype.float32,
                 use_past=False,
                 compute_in_2d=False,
                 parallel_config=TransformerOpParallelConfig()):
        super().__init__()
        self._is_ascend = context.get_context('device_target') in ["Ascend"]
        self.dp = parallel_config.data_parallel
        self.is_parallel_mode = _get_parallel_mode() in (
            ParallelMode.SEMI_AUTO_PARALLEL, ParallelMode.AUTO_PARALLEL)
        if batch_size:
            Validator.check_positive_int(batch_size)
        self.compute_in_2d = compute_in_2d
        self.reshape = P.Reshape()

        _check_config(parallel_config)
        self.src_seq_length = src_seq_length
        self.tgt_seq_length = tgt_seq_length
        self.hidden_size = dim
        self.n_head = n_heads
        self.batch_size = batch_size
        if self.hidden_size % self.n_head != 0:
            raise ValueError("For 'MultiHeadAttention', the class variable 'hidden_size' must be a multiple "
                             "of 'n_head', but got the hidden_size is {} and the n_head is {}."
                             .format(self.hidden_size, self.n_head))
        if self.n_head % parallel_config.model_parallel != 0:
            raise ValueError("For 'MultiHeadAttention', the class variable 'n_head' must be a multiple of "
                             "'parallel_config.model_parallel', but got the n_head is {} "
                             "and the parallel_config.model_parallel  is {}."
                             .format(self.n_head, parallel_config.model_parallel))
        self.is_first_iteration = True
        # Output layer
        self.wo = Linear(in_channels=self.hidden_size,
                         out_channels=self.hidden_size,
                         has_bias=False,
                         compute_dtype=compute_dtype,
                         param_init_type=param_init_type)
        self.wo.shard(strategy_matmul=((parallel_config.data_parallel, parallel_config.model_parallel),
                                       (1, parallel_config.model_parallel)))
        self.transpose = P.Transpose().shard(((parallel_config.data_parallel, 1,
                                               parallel_config.model_parallel, 1),))
        self.merger_head_transpose = P.Transpose().shard(((parallel_config.data_parallel,
                                                           parallel_config.model_parallel, 1, 1),))
        self.n_head = n_heads
        # embedding size per head
        self.size_per_head = self.hidden_size // self.n_head
        self.concat_k = P.Concat(axis=3)
        self.concat_v = P.Concat(axis=2)
        self.multiply_data = Tensor([
            -10000.0,
        ], dtype=compute_dtype)
        self.one = Tensor([
            1.0,
        ], dtype=compute_dtype)
        self.batch_matmul_q_k = P.BatchMatMul(transpose_b=True).shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1),
             (parallel_config.data_parallel, parallel_config.model_parallel, 1, 1)))
        self.batch_matmul = P.BatchMatMul().shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1),
             (parallel_config.data_parallel, parallel_config.model_parallel, 1, 1)))
        self.real_div = P.RealDiv().shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1), ()))
        self.sub = P.Sub().shard(((1,), (parallel_config.data_parallel, 1, 1, 1)))
        self.mul = P.Mul().shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1), ()))
        self.mul_mask = P.Mul().shard(((parallel_config.data_parallel, 1, 1, 1), (1,)))
        self.add = P.Add().shard(
            ((parallel_config.data_parallel, 1, 1, 1),
             (parallel_config.data_parallel, parallel_config.model_parallel, 1, 1)))
        # Normalize factor for attention, sqrt(dk) as widely used
        self.inv_norm_factor = Tensor(
            [1.0 / math.sqrt(self.size_per_head)], dtype=compute_dtype)
        self.beta = Tensor([1.0])
        self.use_past = use_past

        self.softmax = nn.Softmax().to_float(softmax_compute_dtype)
        self.softmax.softmax.shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1),))
        self.softmax_3d = nn.Softmax().to_float(softmax_compute_dtype)
        self.softmax_3d.softmax.shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1),))
        self.expand_dims = P.ExpandDims().shard(
            ((parallel_config.data_parallel, 1, 1),))

        # Query
        self.wq = Linear(self.hidden_size,
                         self.hidden_size,
                         has_bias=False,
                         compute_dtype=compute_dtype,
                         param_init_type=param_init_type)
        # dp,mp -> dp, 1 : dp,1 -> slice -> dp , mp * mp , 1 -> all reduce -> dp, 1
        self.wq.shard(strategy_matmul=(
            (parallel_config.data_parallel, 1), (parallel_config.model_parallel, 1)))
        # Key
        self.wk = Linear(self.hidden_size,
                         self.hidden_size,
                         has_bias=False,
                         compute_dtype=compute_dtype,
                         param_init_type=param_init_type)
        # dp, 1 -> dp, mp
        self.wk.shard(strategy_matmul=(
            (parallel_config.data_parallel, 1), (parallel_config.model_parallel, 1)))

        # Value
        self.wv = Linear(self.hidden_size,
                         self.hidden_size,
                         has_bias=False,
                         compute_dtype=compute_dtype,
                         param_init_type=param_init_type)
        # dp, 1 -> dp, mp
        self.wv.shard(strategy_matmul=(
            (parallel_config.data_parallel, 1), (parallel_config.model_parallel, 1)))

        self.dtype = compute_dtype
        self.softmax_dtype = softmax_compute_dtype

        self.add_alibi = P.Add().shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1),
             (parallel_config.data_parallel, parallel_config.model_parallel, 1, 1)))
        self.mul_alibi = P.Mul().shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1), (1,)))
        self.mul_alibi1 = P.Mul().shard(
            ((parallel_config.data_parallel, parallel_config.model_parallel, 1, 1), (1,)))

        if self.use_past:
            # operators used for state reuse
            seq_range = np.arange(src_seq_length).reshape(1, 1, -1)
            self.range = Tensor(
                np.tile(seq_range, (batch_size, 1, 1)), mstype.int32)
            self.seq_length = src_seq_length
            self.attention_mask = Tensor(
                np.tril(np.ones(shape=(self.seq_length, self.seq_length))), mstype.int32)
            self.slice = P.StridedSlice().shard(((1, 1, 1, 1),))
            self.not_equal = P.NotEqual().shard(((1, 1, 1, 1), ()))
            self.reducesum = P.ReduceSum().shard(((1, 1, 1, 1),))
            self.expand_dims = P.ExpandDims().shard(((1, 1, 1),))
            self.tensor_le = P.LessEqual().shard(((1, 1, 1), (1, 1, 1)))
            self.add = P.Add().shard(((1, 1, 1, 1), (1, 1, 1, 1)))
            self.equal = P.Equal().shard(((1, 1, 1), (1, 1, 1)))
            self.sub1 = P.Sub().shard(((1,), ()))
            self.tile = P.Tile().shard(((1, 1, 1, 1),))
            self.less = P.Less().shard(((1, 1, 1), (1, 1, 1)))
            self.mul1 = P.Mul().shard(((1, 1, 1, 1), (1, 1, 1, 1)))

    def construct(self, x: Tensor, alibi_tensor: Tensor, attention_mask=None,
                  key_past=None, value_past=None, batch_valid_length=None):
        """Forward process of the MultiHeadAttention"""
        batch_size = self._get_batch_size_from_input(x)
        x = self.reshape(x, (-1, x.shape[-1]))
        ori_dtype = x.dtype
        # multi head attention: query, key, value are derived from the same inputs
        query = self.wq(x).astype(self.dtype)  # dp, 1 -> dp, mp
        key = self.wk(x).astype(self.dtype)    # dp, 1 -> dp, mp
        value = self.wv(x).astype(self.dtype)  # dp, 1 -> dp, mp

        # do transpose first # dp, 1, mp, 1 -> dp, mp, 1, 1
        query = self.transpose(
            query.reshape((batch_size, self._get_seq_length_under_incremental(self.tgt_seq_length),
                           self.n_head, self.size_per_head)),
            (0, 2, 1, 3))
        # dp, 1, mp, 1 -> dp, mp, 1, 1
        key = self.transpose(
            key.reshape((batch_size, self._get_seq_length_under_incremental(self.tgt_seq_length),
                         self.n_head, self.size_per_head)),
            (0, 2, 1, 3))

        # the returned shape is [bs, n_head, seq_length, size_per_head] # dp, mp -> dp, 1, mp, 1 -> dp, mp, 1, 1
        value = self.transpose(
            self.reshape(value, (batch_size, self._get_seq_length_under_incremental(self.tgt_seq_length),
                                 self.n_head, self.size_per_head)),
            (0, 2, 1, 3))
        # support input shape is [bs, seq, seq] or [bs, heads, seq, seq]
        if attention_mask is not None and attention_mask.ndim == 3:
            # expand attention mask from [bs, seq, seq] -> [bs, 1, seq, seq]
            attention_mask = self.expand_dims(attention_mask, 1)
        # key and value for current token(s)
        key_present = key
        value_present = value
        if self.use_past:
            # The first graph with the input size of (bs, seq_length)
            if self.is_first_iteration:
                # Get the valid input length without padding
                valid_length_vector = (
                    self.less(self.range, batch_valid_length.view(-1, 1, 1))).astype(self.dtype)
                # Cover the key and value numbers corresponding to the padding position
                key_present = self.mul1(
                    key, self.expand_dims(valid_length_vector, 3))
                value_present = self.mul1(
                    value, self.expand_dims(valid_length_vector, 3))
            # The second graph with the inpus size of (bs, 1)
            else:
                # Get the current token position index
                valid_length = batch_valid_length - 1
                valid_length = self.reshape(valid_length, (-1, 1, 1))
                valid_length_vector = (self.equal(
                    self.range, valid_length)).astype(self.dtype)
                # Pad the key and value to seq_length with only the position index not zero
                current_key = self.mul1(
                    key, self.expand_dims(valid_length_vector, 3))
                current_value = self.mul1(
                    value, self.expand_dims(valid_length_vector, 3))
                # Concat the previous saved state and current state
                key = self.add(key_past, current_key)
                value = self.add(value_past, current_value)
                # Update key_present and value_present for state update
                key_present = key
                value_present = value

        layer_present = (key_present, value_present)
        # multi head attention considering attention mask
        # the return shape is [bs * seq_length, hidden_size]
        attention = self._attn(
            query, key, value, alibi_tensor, attention_mask, batch_valid_length)

        # Output
        output = self.wo(attention)
        # output = self.reshape(output, ori_shape)
        output = output.astype(ori_dtype)
        return output, layer_present

    def _get_batch_size_from_input(self, input_tensor):
        """Get the batch size from query tensor"""
        # For the incremental prediction, the seq length for the input is 1.
        if input_tensor.ndim == 2 and ((self.use_past and self.is_first_iteration) or (not self.use_past)):
            return input_tensor.shape[0] // self.src_seq_length
        return input_tensor.shape[0]

    def _get_seq_length_under_incremental(self, length):
        r"""Return the length of the tensor.
            For the incremental prediction, the seq length for the input is 1.
        """
        if self.use_past and not self.is_first_iteration:
            return 1
        return length

    def _merge_heads(self, x):
        """
        convert a 4d input to a 2d output

        Inputs:
            x: input tensor

        Output:
            x_merge: the 2d output
        """
        # dp,mp,1,1 -> dp,1,mp,1
        x = self.merger_head_transpose(
            x, (0, 2, 1, 3))  # bs, seq_length, head, size_per_head
        x_shape = x.shape
        if self.compute_in_2d:
            # [bs * seq/1, hidden_dim]
            new_shape = (-1, x_shape[-2] * x_shape[-1])
        else:
            # [bs, seq/1, hidden_dim]
            new_shape = (x_shape[0], x_shape[1], -1)
        x_merge = self.reshape(x, new_shape)
        return x_merge

    def _softmax(self, attention_scores):
        """
        For the consideration of the performance, do softmax according to different situations
        :param attention_scores: a 3d tensor before softmax
        :return: the attention scores.
        """
        attention_probs = self.softmax(attention_scores)
        return attention_probs

    def _attn(self, query, key, value, alibi_tensor, attention_mask, valid_length):
        """
        Get the weighted score along the seq_length

        Inputs:
            query: the query matrix
            key: the key matrix
            value: the value matrix
            alibi_tensor: alibi tensor
            attention_mask: the attention mask matrix with shape (batch_size,
            1, seq_length, seq_length)
        Outputs:
            weighted_values: Tensor, the weighted sum scores
        """
        # Normalize query and key before MatMul, default off
        # Attention score [bs, n_head, seq_length, seq_length] query, key, value : dp, mp, 1, 1
        score = self.batch_matmul_q_k(query, key)
        # score : b,num_head,t,t; dp, mp, 1, 1
        # score = self.mul(score, self.inv_norm_factor)
        score = self.add_alibi(
            self.mul_alibi1(score, self.inv_norm_factor),
            self.mul_alibi(alibi_tensor, self.beta)
        )
        # for input size of (bs, 1) namely the second graph,
        # the shape of attention_mask matrix should be (bs, 1, 1, seq_length)
        if attention_mask is not None:
            if self.use_past and not self.is_first_iteration:
                index = self.reshape(valid_length - 1, (-1, 1, 1))
                # Calculate the attention_mask matrix via the position index
                attention_mask = (self.tensor_le(
                    self.range, index)).astype(mstype.int32)
                attention_mask = self.expand_dims(attention_mask, 2)
            # Minus 10000 for the position where masked to exclude them from softmax
            multiplu_out = self.sub(self.one, attention_mask.astype(
                self.dtype))  # dp,1,1,1->dp,1,1,1

            # dp,1,1,1->dp,1,1,1
            adder = self.mul_mask(multiplu_out, self.multiply_data)
            score = self.add(adder, score)  # dp,1,1,1->dp,mp,1,1

        # attention probs
        attention_probs = self._softmax(score.astype(self.softmax_dtype))

        # Weighted sum output [bs, n_head, seq_length, size_per_head]
        weighted_values = self.batch_matmul(
            attention_probs.astype(self.dtype), value)
        # dp,mp,1,1 -> dp,1,mp,1 -> dp,mp
        attention_merge = self._merge_heads(weighted_values)
        return attention_merge
