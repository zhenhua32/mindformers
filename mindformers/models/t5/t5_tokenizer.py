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
"""T5 Tokenzier"""

import os
import shutil

import sentencepiece as spm

from mindformers.tools.register import MindFormerRegister, MindFormerModuleType
from mindformers.models.base_tokenizer import PretrainedTokenizer

__all__ = ['T5Tokenizer']


@MindFormerRegister.register(MindFormerModuleType.TOKENIZER)
class T5Tokenizer(PretrainedTokenizer):
    """
        The tokenizer for T5 model
    """
    VOCAB_FILES = {'vocab_file': 'spiece.model'}
    FILE_LIST = ['tokenizer_config.json']

    def __init__(self,
                 vocab_file,
                 eos_token="</s>",
                 unk_token="<unk>",
                 pad_token="<pad>",
                 **kwargs):
        """
        Initialize the sentence piece model according to the model path
        Args:
             sp_model(str): the sentence piece model path.
        """
        super(T5Tokenizer, self).__init__(eos_token=eos_token,
                                          unk_token=unk_token,
                                          pad_token=pad_token,
                                          **kwargs)
        self.s = spm.SentencePieceProcessor(model_file=vocab_file)
        self.vocab_file = vocab_file

    def _tokenize(self, text, **kwargs):
        token_list = self.s.encode(text, out_type=str)
        return token_list

    def _convert_tokens_to_ids(self, input_tokens):
        if not input_tokens:
            raise ValueError(f"Input token {input_tokens} is None.")
        if isinstance(input_tokens, str):
            return self.s.piece_to_id(input_tokens)
        res = []
        for item in input_tokens:
            res.append(self.s.piece_to_id(item))
        return res

    def tokenize(self, text):
        """Tokenizer the input_text"""
        if not isinstance(text, str):
            raise ValueError("Text should be type str, but found type", type(text))
        return self._tokenize(text)

    def build_inputs_with_special_tokens(self, token_ids_0, token_ids_1=None):
        """Add the eos to the token_ids0"""
        if not token_ids_1:
            return token_ids_0 + [self.eos_token_id]
        raise ValueError("Only token_ids_1=None is supported now.")

    def save_vocabulary(self, save_directory, filename_prefix):
        """write the word to the files"""
        output_file_path = os.path.join(save_directory, filename_prefix)
        shutil.copy(self.vocab_file, output_file_path)
        return output_file_path

    def _convert_ids_to_tokens(self, ids):
        if isinstance(ids, int):
            return self.s.IdToPiece(ids)

        if isinstance(ids, list):
            res = []
            for item in ids:
                res.append(self.s.IdToPiece(item))
            return res
        raise TypeError(f"The type of ids should be int or list, but found {type(ids)}.")

    def convert_tokens_to_string(self, tokens):
        if not tokens:
            return ""
        return self.s.decode_pieces(tokens).strip()

    @property
    def vocab_size(self):
        """Return the vocab size of the tokenizer"""
        return self.s.vocab_size()
