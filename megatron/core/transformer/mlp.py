# Copyright (c) 2023, NVIDIA CORPORATION. All rights reserved.

import torch
import torch.nn.functional as F

from megatron.core import tensor_parallel
from megatron.core.fusions.fused_bias_gelu import bias_gelu_impl
from megatron.core.transformer.module import MegatronModule
from megatron.core.transformer.transformer_config import TransformerConfig
from megatron.core.transformer.custom_layers.transformer_engine import \
        TERowParallelLinear, TEColumnParallelLinear

class MLP(MegatronModule):
    """
    MLP will take the input with h hidden state, project it to 4*h
    hidden dimension, perform nonlinear transformation, and project the
    state back into h hidden dimension.


    Returns an output and a bias to be added to the output.
    If config.add_bias_linear is False, the bias returned is None.

    We use the following notation:
     h: hidden size
     p: number of tensor model parallel partitions
     b: batch size
     s: sequence length
    """

    def __init__(self, config: TransformerConfig):
        super().__init__(config=config)

        self.config: TransformerConfig = config

        # If this is a gated linear unit we double the output width, see https://arxiv.org/pdf/2002.05202.pdf
        ffn_hidden_size = self.config.ffn_hidden_size
        if self.config.gated_linear_unit:
            ffn_hidden_size *= 2

        self.linear_fc1 = TEColumnParallelLinear(
            self.config.hidden_size,
            ffn_hidden_size,
            config=self.config,
            init_method=self.config.init_method,
            bias=self.config.add_bias_linear,
            skip_bias_add=True,
        )

        if self.config.gated_linear_unit:
            def glu(x):
                x = torch.chunk(x, 2, dim=-1)
                return self.config.activation_func(x[0]) * x[1]
            self.activation_func = glu
        else:
            self.activation_func = self.config.activation_func

        self.linear_fc2 = TERowParallelLinear(
            self.config.ffn_hidden_size,
            self.config.hidden_size,
            config=self.config,
            init_method=self.config.output_layer_init_method,
            bias=self.config.add_bias_linear,
            skip_bias_add=True,
        )

        self.checkpoint_mlp = (self.config.recompute_granularity in ['selective_mlp_only', 'selective_both'])
        self.recompute_non_linear_layer_in_mlp = config.recompute_non_linear_layer_in_mlp

    def _forward(self, hidden_states):

        # [s, b, 4 * h/p]
        intermediate_parallel, bias_parallel = self.linear_fc1(hidden_states)

        
        # Reduce excessive tensor offloading
        def _apply_non_linear(intermediate_parallel, bias_parallel):
            if self.config.bias_gelu_fusion:
                assert self.config.add_bias_linear is True
                assert self.activation_func == F.gelu
                intermediate_parallel = bias_gelu_impl(intermediate_parallel, bias_parallel)
            else:
                if bias_parallel is not None:
                    intermediate_parallel = intermediate_parallel + bias_parallel
                intermediate_parallel = self.activation_func(intermediate_parallel)
            return intermediate_parallel
        
        if self.recompute_non_linear_layer_in_mlp:
            intermediate_parallel = tensor_parallel.checkpoint(_apply_non_linear, False, intermediate_parallel, bias_parallel)
        else:
            intermediate_parallel = _apply_non_linear(intermediate_parallel, bias_parallel)

        # [s, b, h]
        output, output_bias = self.linear_fc2(intermediate_parallel)
        return output, output_bias

    def forward(self, hidden_states):
        if self.checkpoint_mlp:
            def _custom_function(hidden_states):
                """torch activation checkpointing cannot handle case where an output and gradients are both None. We use this function to handle this case."""
                output, output_bias = self._forward(hidden_states)
                if output_bias is not None:
                    return output, output_bias
                else:
                    return output
            results =  tensor_parallel.checkpoint(_custom_function, False, hidden_states)
            if isinstance(results, tuple) and len(results) == 2:
                return results
            else:
                return results, None
        else:
            return self._forward(hidden_states)