from typing import Any

import torch
from torch import nn, optim
from tqdm import tqdm

from .base_postprocessor import BasePostprocessor


class TemperatureScalingPostprocessor(BasePostprocessor):
    """A decorator which wraps a model with temperature scaling, internalize
    'temperature' parameter as part of a net model."""
    def __init__(self, config):
        super(TemperatureScalingPostprocessor, self).__init__(config)
        self.config = config
        self.temperature = nn.Parameter(torch.ones(1) * 1.5)  # initialize T

    def setup(self, net: nn.Module, id_loader_dict, ood_loader_dict):
        assert 'val' in id_loader_dict.keys(
        ), 'No validation dataset found!'  # make sure that validation set exists
        val_dl = id_loader_dict['val']
        nll_criterion = nn.CrossEntropyLoss().cuda()

        logits_list = []  # fit in whole dataset at one time to back prop
        labels_list = []
        with torch.no_grad(
        ):  # fix other params of the net, only learn temperature
            for batch in tqdm(val_dl):
                data = batch['data'].cuda()
                labels = batch['label']
                logits = net(data)
                logits_list.append(logits)
                labels_list.append(labels)
            logits = torch.cat(logits_list).cuda(
            )  # convert a list of many tensors (each of a batch) to one tensor
            labels = torch.cat(labels_list).cuda()
            # calculate NLL before temperature scaling
            before_temperature_nll = nll_criterion(logits, labels)

        print('Before temperature - NLL: %.3f' % (before_temperature_nll))

        optimizer = optim.LBFGS([self.temperature], lr=0.01, max_iter=50)

        def eval(
        ):  # make sure only temperature parameter will be learned, fix other parameters of the network
            optimizer.zero_grad()
            loss = nll_criterion(self._temperature_scale(logits), labels)
            loss.backward()
            return loss

        optimizer.step(eval)

        # print learned parameter temperature, calculate NLL after temperature scaling
        after_temperature_nll = nll_criterion(self._temperature_scale(logits),
                                              labels).item()
        print('Optimal temperature: %.3f' % self.temperature.item())
        print('After temperature - NLL: %.3f' % (after_temperature_nll))

    def _temperature_scale(self, logits):
        temperature = self.temperature.unsqueeze(1).expand(
            logits.size()[0],
            logits.size()[1]).cuda()
        return logits / temperature

    def postprocess(self, net: nn.Module, data: Any):
        logits = net(data)
        logits_ts = self._temperature_scale(logits)
        score = torch.softmax(logits_ts, dim=1)
        conf, pred = torch.max(score, dim=1)
        return pred, conf
