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
"""test PretrainedModel using GPT2LMHeadModel"""

import os
os.environ["MODELFOUNDRY_HUB_ENDPOINT_M"] = "https://modelfoundrysh.test.osinfra.cn"
# pylint: disable=C0413
import shutil
import unittest
import numpy as np

from mindspore import nn

from mindformers import GPT2LMHeadModel

REPO_ID = "mindformersinfra/test_gpt2_layer2"
MODEL_NAME = "gpt2"
YAML_NAME = "mindspore_model.yaml"
CONFIG_NAME = "config.json"
DEFAULT_MODEL_NAME = "mindspore_model.ckpt"

LOCAL_REPO = "./gpt2_local_repo_ut"
LOCAL_DIR = "./gpt2_local_dir_ut"

def get_state_dict(model):
    """get state dict"""
    if isinstance(model, dict):
        return model
    if isinstance(model, nn.Cell):
        state_dict = {}
        for item in model.get_parameters():
            state_dict[item.name] = item.data
        return state_dict
    raise ValueError("model must be dict or nn.Cell")


def compare_state_dict(model_1, model_2):
    """compare state dict"""
    state_dict_1 = get_state_dict(model_1)
    state_dict_2 = get_state_dict(model_2)

    assert state_dict_1.keys() == state_dict_2.keys()
    for key in state_dict_1:
        value1 = state_dict_1[key].asnumpy()
        value2 = state_dict_2[key].asnumpy()
        assert np.array_equal(value1, value2), f"The value of {key} is not match!"


class TestPretrainedModel(unittest.TestCase):
    """test PretrainedModel"""
    def test_pretrained_model_load_from_model_name(self):
        """test pretrained model load from model name"""
        model_1 = GPT2LMHeadModel.from_pretrained(MODEL_NAME)
        model_1.save_pretrained(LOCAL_DIR)

        file_names = os.listdir(LOCAL_DIR)
        assert YAML_NAME in file_names, f"{YAML_NAME} not found!"
        assert DEFAULT_MODEL_NAME in file_names, f"{DEFAULT_MODEL_NAME} not found!"
        shutil.rmtree(LOCAL_DIR)

    def test_pretrained_model_load_from_local_dir(self):
        """test pretrained model load from local dir"""
        model_1 = GPT2LMHeadModel.from_pretrained(MODEL_NAME)
        model_1.save_pretrained(LOCAL_DIR)
        model_2 = GPT2LMHeadModel.from_pretrained(LOCAL_DIR)

        compare_state_dict(model_1, model_2)
        shutil.rmtree(LOCAL_DIR)

    def test_pretrained_model_load_from_repo(self):
        """test pretrained model load from repo"""
        model_1 = GPT2LMHeadModel.from_pretrained(REPO_ID)
        model_1.save_pretrained(LOCAL_REPO, save_json=True)

        file_names = os.listdir(LOCAL_REPO)
        assert CONFIG_NAME in file_names, f"{CONFIG_NAME} not found!"
        assert DEFAULT_MODEL_NAME in file_names, f"{DEFAULT_MODEL_NAME} not found!"
        shutil.rmtree(LOCAL_REPO)

    def test_pretrained_model_load_from_local_repo(self):
        """test pretrained model load from local repo"""
        model_1 = GPT2LMHeadModel.from_pretrained(REPO_ID)
        model_1.save_pretrained(LOCAL_REPO, save_json=True)
        model_2 = GPT2LMHeadModel.from_pretrained(LOCAL_REPO)

        compare_state_dict(model_1, model_2)
        shutil.rmtree(LOCAL_REPO)

    def test_pretrained_model_load_from_local_dir_with_yaml_json_ckpt(self):
        """test pretrained model load from dir which contains yaml, ckpt and json"""
        model_1 = GPT2LMHeadModel.from_pretrained(REPO_ID)
        model_1.save_pretrained(LOCAL_DIR, save_json=True)
        model_1 = GPT2LMHeadModel.from_pretrained(MODEL_NAME)
        model_1.save_pretrained(LOCAL_DIR)
        model_2 = GPT2LMHeadModel.from_pretrained(LOCAL_DIR)  # load from local path

        compare_state_dict(model_1, model_2)
        shutil.rmtree(LOCAL_DIR)

    def test_pretrained_model_load_from_local_repo_shard(self):
        """test pretrained model load from local repo shard"""
        model_1 = GPT2LMHeadModel.from_pretrained(REPO_ID)
        model_1.save_pretrained(LOCAL_REPO, max_shard_size="100MB", save_json=True)

        file_names = os.listdir(LOCAL_REPO)
        assert "config.json" in file_names, "config.json not found!"
        assert "mindspore_model.ckpt.index.json" in file_names, "mindspore_model.ckpt.index.json not found!"
        assert "mindspore_model-00001-of-00002.ckpt" in file_names, "mindspore_model-00001-of-00002.ckpt not found!"
        assert "mindspore_model-00002-of-00002.ckpt" in file_names, "mindspore_model-00002-of-00002.ckpt not found!"

        model_2, loading_info = GPT2LMHeadModel.from_pretrained(LOCAL_REPO, output_loading_info=True)
        assert "missing_keys" in loading_info, "missing_keys not found!"
        assert "unexpected_keys" in loading_info, "unexpected_keys not found!"
        assert "mismatched_keys" in loading_info, "mismatched_keys not found!"

        compare_state_dict(model_1, model_2)
        shutil.rmtree(LOCAL_REPO)

    def test_pretrained_model_load_from_error_model_name(self):
        """test PretrainedModel load from error model name"""
        with self.assertRaises(ValueError):
            GPT2LMHeadModel.from_pretrained("xxxx")

    def test_pretrained_model_load_from_error_model_name_prefix_mindspore(self):
        """test PretrainedModel load from error model name"""
        with self.assertRaises(ValueError):
            GPT2LMHeadModel.from_pretrained("mindspore/xxxx")

    def test_pretrained_model_load_from_dir_or_repo_not_exist(self):
        """test PretrainedModel load from dir or repo not exist"""
        with self.assertRaises(RuntimeError):
            GPT2LMHeadModel.from_pretrained("xxxx/xxxx")

    def test_pretrained_model_load_from_local_dir_empty(self):
        """test PretrainedModel load from empty dir"""
        os.makedirs(LOCAL_DIR)
        with self.assertRaises(FileNotFoundError):
            GPT2LMHeadModel.from_pretrained(LOCAL_DIR)  # load from local path
        shutil.rmtree(LOCAL_DIR)

    def test_pretrained_model_load_from_local_dir_only_ckpt(self):
        """test PretrainedModel load from dir only ckpt"""
        model_1 = GPT2LMHeadModel.from_pretrained(MODEL_NAME)
        model_1.save_pretrained(LOCAL_DIR)
        os.remove(os.path.join(LOCAL_DIR, YAML_NAME))
        with self.assertRaises(FileNotFoundError):
            GPT2LMHeadModel.from_pretrained(LOCAL_DIR)  # load from local path
        shutil.rmtree(LOCAL_DIR)

    def test_pretrained_model_load_from_local_dir_only_yaml(self):
        """test PretrainedModel load from dir only yaml"""
        model_1 = GPT2LMHeadModel.from_pretrained(MODEL_NAME)
        model_1.save_pretrained(LOCAL_DIR)
        os.remove(os.path.join(LOCAL_DIR, DEFAULT_MODEL_NAME))
        with self.assertRaises(FileNotFoundError):
            GPT2LMHeadModel.from_pretrained(LOCAL_DIR)  # load from local path
        shutil.rmtree(LOCAL_DIR)

    def test_pretrained_model_load_from_local_dir_only_json(self):
        """test PretrainedModel load from dir only json"""
        model_1 = GPT2LMHeadModel.from_pretrained(REPO_ID)
        model_1.save_pretrained(LOCAL_REPO, save_json=True)
        os.remove(os.path.join(LOCAL_REPO, DEFAULT_MODEL_NAME))
        with self.assertRaises(EnvironmentError):
            GPT2LMHeadModel.from_pretrained(LOCAL_REPO)  # load from local path
        shutil.rmtree(LOCAL_REPO)

    def test_pretrained_model_load_from_local_dir_only_yaml_and_json(self):
        """test PretrainedModel load from dir only yaml and json"""
        model_1 = GPT2LMHeadModel.from_pretrained(REPO_ID)
        model_1.save_pretrained(LOCAL_DIR, save_json=True)
        model_1 = GPT2LMHeadModel.from_pretrained(MODEL_NAME)
        model_1.save_pretrained(LOCAL_DIR)
        os.remove(os.path.join(LOCAL_DIR, DEFAULT_MODEL_NAME))
        with self.assertRaises(FileNotFoundError):
            GPT2LMHeadModel.from_pretrained(LOCAL_DIR)  # load from local path
        shutil.rmtree(LOCAL_DIR)