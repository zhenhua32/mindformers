# Copyright 2024 Huawei Technologies Co., Ltd
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
"""test multiturn_dataloader."""
import pytest

import numpy as np

from mindformers import MindFormerConfig, AutoTokenizer, MultiTurnDataset
from tests.ut.test_tool_alpaca_dataset import make_test_tool_alpaca_dataset

@pytest.mark.level0
@pytest.mark.platform_x86_cpu
@pytest.mark.env_onecard
def test_multiturn_dataloader_correct():
    """
    Feature: Test multiturn dataloader correction
    Description: Create multiturn dataloader and iter it
    Expectation: The output data is different from expect data
    """
    batch_size = 2
    file_name = make_test_tool_alpaca_dataset(valid_num=8)
    tokenizer = AutoTokenizer.from_pretrained('glm3_6b')
    train_dataset = {"data_loader": {"type": "ToolAlpacaDataLoader",
                                     "dataset_dir": file_name,
                                     "shuffle": True},
                     "tokenizer": tokenizer,
                     "max_seq_length": 2048,
                     "num_parallel_workers": 8,
                     "python_multiprocessing": False,
                     "drop_remainder": True,
                     "batch_size": batch_size,
                     "repeat": 1,
                     "numa_enable": False,
                     "prefetch_size": 1,
                     "seed": 0}

    dataset_config = MindFormerConfig(train_dataset=train_dataset)['train_dataset']
    print(dataset_config)
    dataset_config['data_loader']['dataset_dir'] = "./checkpoint_download/tool_alpaca.jsonl"
    dataset_config['max_seq_length'] = 64
    dataset = MultiTurnDataset(dataset_config)

    expected_inputs = [64790, 64792, 64794, 30910, 13, 20115, 267, 1762, 2554, 362, 1077, 362,
                       344, 457, 30930, 809, 431, 1675, 289, 267, 1762, 4159, 30954, 13,
                       4812, 25812, 5515, 64795, 30910, 13, 2384, 64796, 1462, 13, 16014, 23720,
                       13, 22268, 30962, 10372, 1123, 13, 10846, 31040, 64797, 30910, 13, 11973,
                       64796, 30910, 13, 2384, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    expected_labels = [-100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100,
                       -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100,
                       -100, -100, -100, -100, -100, -100, -100, 1462, 13, 16014, 23720, 13,
                       22268, 30962, 10372, 1123, 13, 10846, 31040, 64797, -100, -100, -100, -100,
                       30910, 13, 2384, 2, -100, -100, -100, -100, -100, -100, -100, -100,
                       -100, -100, -100, -100]
    expected_inputs = np.array([expected_inputs for _ in range(batch_size)])
    expected_labels = np.array([expected_labels for _ in range(batch_size)])

    for item in dataset.create_tuple_iterator():

        real_inputs, real_labels = item[0].asnumpy(), item[1].asnumpy()

        assert (real_inputs == expected_inputs).all(), f"expect inputs\n{expected_inputs},\nbut got\n{real_inputs}"
        assert (real_labels == expected_labels).all(), f"expect inputs:\n{real_labels},\nbut got\n{expected_labels}"
