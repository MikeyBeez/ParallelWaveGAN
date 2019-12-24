#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2019 Tomoki Hayashi
#  MIT License (https://opensource.org/licenses/MIT)

import logging

import numpy as np
import pytest
import torch
import torch.nn.functional as F

from parallel_wavegan.losses import MultiResolutionSTFTLoss
from parallel_wavegan.models import ParallelWaveGANDiscriminator
from parallel_wavegan.models import ParallelWaveGANGenerator
from parallel_wavegan.optimizers import RAdam

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s (%(module)s:%(lineno)d) %(levelname)s: %(message)s")


def make_generator_args(**kwargs):
    defaults = dict(
        in_channels=1,
        out_channels=1,
        kernel_size=3,
        layers=6,
        stacks=3,
        residual_channels=8,
        gate_channels=16,
        skip_channels=8,
        aux_channels=10,
        aux_context_window=0,
        dropout=1 - 0.95,
        use_weight_norm=True,
        use_causal_conv=False,
        upsample_conditional_features=True,
        upsample_net="ConvInUpsampleNetwork",
        upsample_params={"upsample_scales": [4, 4]},
    )
    defaults.update(kwargs)
    return defaults


def make_discriminator_args(**kwargs):
    defaults = dict(
        in_channels=1,
        out_channels=1,
        kernel_size=3,
        layers=5,
        conv_channels=16,
        nonlinear_activation="LeakyReLU",
        nonlinear_activation_params={"negative_slope": 0.2},
        bias=True,
        use_weight_norm=True,
    )
    defaults.update(kwargs)
    return defaults


def make_mutli_reso_stft_loss_args(**kwargs):
    defaults = dict(
        fft_sizes=[64, 128, 256],
        hop_sizes=[32, 64, 128],
        win_lengths=[48, 96, 192],
        window='hann_window',
    )
    defaults.update(kwargs)
    return defaults


@pytest.mark.parametrize(
    "dict_g, dict_d, dict_loss", [
        ({}, {}, {}),
        ({"layers": 1, "stacks": 1}, {}, {}),
        ({}, {"layers": 1}, {}),
        ({"kernel_size": 5}, {}, {}),
        ({}, {"kernel_size": 5}, {}),
        ({"gate_channels": 8}, {}, {}),
        ({"stacks": 1}, {}, {}),
        ({"use_weight_norm": False}, {"use_weight_norm": False}, {}),
        ({"aux_context_window": 2}, {}, {}),
        ({"upsample_net": "UpsampleNetwork"}, {}, {}),
        ({"upsample_params": {"upsample_scales": [4], "freq_axis_kernel_size": 3}}, {}, {}),
        ({"upsample_conditional_features": False, "upsample_params": {"upsample_scales": [1]}}, {}, {}),
        ({}, {"nonlinear_activation": "ReLU", "nonlinear_activation_params": {}}, {}),
        ({"use_causal_conv": True}, {}, {}),
        ({"use_causal_conv": True, "aux_context_window": 1}, {}, {}),
        ({"use_causal_conv": True, "aux_context_window": 2}, {}, {}),
        ({"use_causal_conv": True, "aux_context_window": 3}, {}, {}),
    ])
def test_parallel_wavegan_trainable(dict_g, dict_d, dict_loss):
    # setup
    batch_size = 4
    batch_length = 4096
    args_g = make_generator_args(**dict_g)
    args_d = make_discriminator_args(**dict_d)
    args_loss = make_mutli_reso_stft_loss_args(**dict_loss)
    z = torch.randn(batch_size, 1, batch_length)
    y = torch.randn(batch_size, 1, batch_length)
    c = torch.randn(batch_size, args_g["aux_channels"],
                    batch_length // np.prod(
                        args_g["upsample_params"]["upsample_scales"]) + 2 * args_g["aux_context_window"])
    model_g = ParallelWaveGANGenerator(**args_g)
    model_d = ParallelWaveGANDiscriminator(**args_d)
    aux_criterion = MultiResolutionSTFTLoss(**args_loss)
    optimizer_g = RAdam(model_g.parameters())
    optimizer_d = RAdam(model_d.parameters())

    # check generator trainable
    y_hat = model_g(z, c)
    p_hat = model_d(y_hat)
    y, y_hat, p_hat = y.squeeze(1), y_hat.squeeze(1), p_hat.squeeze(1)
    adv_loss = F.mse_loss(p_hat, p_hat.new_ones(p_hat.size()))
    sc_loss, mag_loss = aux_criterion(y_hat, y)
    aux_loss = sc_loss + mag_loss
    loss_g = adv_loss + aux_loss
    optimizer_g.zero_grad()
    loss_g.backward()
    optimizer_g.step()

    # check discriminator trainable
    y, y_hat = y.unsqueeze(1), y_hat.unsqueeze(1).detach()
    p = model_d(y)
    p_hat = model_d(y_hat)
    p, p_hat = p.squeeze(1), p_hat.squeeze(1)
    loss_d = F.mse_loss(p, p.new_ones(p.size())) + F.mse_loss(p_hat, p_hat.new_zeros(p_hat.size()))
    optimizer_d.zero_grad()
    loss_d.backward()
    optimizer_d.step()
