# Copyright 2022 Huawei Technologies Co., Ltd
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
"""Test tokenizer class"""
import os
import shutil

import pytest
from mindformers import PretrainedTokenizer, AutoTokenizer
from mindformers import BertTokenizer


class TestAutoTokenizerMethod:
    """A test class for testing the AutoTokenizer"""
    def generate_fake_vocab(self):
        vocabs = ["[PAD]", "[unused1]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "hello", "world", "!"]
        with open(os.path.join(self.output_path, 'vocab.txt'), 'w') as fp:
            for item in vocabs:
                fp.write(item + '\n')

    def setup_method(self):
        self.output_path = os.path.join(os.path.dirname(__file__), 'test_tokenizer_output')
        os.makedirs(self.output_path, exist_ok=True)
        self.generate_fake_vocab()

    def teardown_method(self):
        shutil.rmtree(self.output_path)

    def test_from_pretrained_tokenizer(self):
        """
        Feature: The Tokenizer test using from python class
        Description: Using call forward process of the tokenizer without error
        Expectation: The returned ret is not equal to [[6, 7]].
        """
        output_path = '/home/mark/code/huggingface_tokenizer_learner/output_only_vocab/'
        tokenizer = AutoTokenizer.from_pretrained(output_path)
        assert isinstance(tokenizer, BertTokenizer)

    def test_save_and_load_tokenizer(self):
        """
        Feature: The test load and save function for the tokenizer
        Description: Load the tokenizer and then saved it
        Expectation: The restored kwargs is not expected version.
        """
        output_path = '/home/mark/code/huggingface_tokenizer_learner/output_only_vocab/'
        bert_tokenizer = BertTokenizer.from_pretrained(output_path)
        bert_tokenizer.save_pretrained(self.output_path)
        restore_tokenizer = AutoTokenizer.from_pretrained(self.output_path)
        assert isinstance(restore_tokenizer, BertTokenizer)


class TestPretrainedTokenizerMethod:
    """A test class for testing the PretrainedTokenizer"""
    def generate_fake_vocab(self):
        vocabs = ["[PAD]", "[unused1]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "hello", "world", "!"]
        with open(os.path.join(self.output_path, 'vocab.txt'), 'w') as fp:
            for item in vocabs:
                fp.write(item + '\n')

    def setup_method(self):
        self.output_path = os.path.join(os.path.dirname(__file__), 'test_tokenizer_output')
        os.makedirs(self.output_path, exist_ok=True)
        self.generate_fake_vocab()

    def teardown_method(self):
        shutil.rmtree(self.output_path)

    def test_from_pretrained_tokenizer(self):
        """
        Feature: The Tokenizer test using from python class
        Description: Using call forward process of the tokenizer without error
        Expectation: The returned ret is not equal to [[6, 7]].
        """
        tokenizer = PretrainedTokenizer.from_pretrained(self.output_path)
        with pytest.raises(NotImplementedError):
            tokenizer("hello world")


class TestBertTokenizerMethod:
    """A test class for testing the BertTokenizer"""
    def generate_fake_vocab(self):
        vocabs = ["[PAD]", "[unused1]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "hello", "world", "!"]
        with open(os.path.join(self.output_path, 'vocab.txt'), 'w') as fp:
            for item in vocabs:
                fp.write(item + '\n')

    def setup_method(self):
        self.output_path = os.path.join(os.path.dirname(__file__), 'test_tokenizer_output')
        os.makedirs(self.output_path, exist_ok=True)
        self.generate_fake_vocab()

    def teardown_method(self):
        shutil.rmtree(self.output_path)

    def test_from_pretrained_tokenizer(self):
        """
        Feature: The BertTokenizer test using from python class
        Description: Using call forward process of the tokenizer without error
        Expectation: The returned ret is not equal to [[6, 7]].
        """
        bert_tokenizer = BertTokenizer.from_pretrained(self.output_path)
        res = bert_tokenizer("hello world")
        assert res == {'attention_mask': [1, 1, 1, 1], 'input_ids': [3, 6, 7, 4],
                       'token_type_ids': [0, 0, 0, 0]}, f"The res is {res}"

    def test_call_with_one_setence_bert_tokenizer(self):
        """
        Feature: The BERT Tokenizer test using from python class
        Description: Using call forward process of the tokenizer without error
        Expectation: The returned ret is not equal to [[6, 7]].
        """
        bert_tokenizer = BertTokenizer(vocab_file=os.path.join(self.output_path, 'vocab.txt'))
        res = bert_tokenizer("hello world")
        assert res == {'attention_mask': [1, 1, 1, 1], 'input_ids': [3, 6, 7, 4],
                       'token_type_ids': [0, 0, 0, 0]}, f"The res is {res}"

    def test_call_with_two_sentence_bert_tokenizer(self):
        """
        Feature: The BERT Tokenizer test using from python class
        Description: Using call forward process of the tokenizer without error
        Expectation: The returned ret is not equal to [[6, 7]].
        """
        bert_tokenizer = BertTokenizer(vocab_file=os.path.join(self.output_path, 'vocab.txt'))
        res = bert_tokenizer("hello world", text_pair="hello world !")
        assert res == {'attention_mask': [1, 1, 1, 1, 1, 1, 1], 'input_ids': [3, 6, 7, 4, 6, 7, 8],
                       'token_type_ids': [0, 0, 0, 1, 1, 1, 1]}, f"The res is {res}"

    def test_tokenize_in_tokenizer(self):
        """
        Feature: The BERT Tokenizer test using from python class
        Description: Using call forward process of the tokenizer without error
        Expectation: The returned ret is not equal to [[6, 7]].
        """
        bert_tokenizer = BertTokenizer(vocab_file=os.path.join(self.output_path, 'vocab.txt'))
        res = bert_tokenizer.tokenize("hello world")
        assert res == ["hello", "world"], f"The res is {res} is not equal to the target"
