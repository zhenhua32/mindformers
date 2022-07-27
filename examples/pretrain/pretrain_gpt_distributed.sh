#!/bin/bash
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

echo "=============================================================================================================="
echo "Please run the script as: "
echo "bash examples/pretrain/pretrain_gpt_distributed.sh DATA_DIR RANK_TABLE_FILE DEVICE_NUM"
echo "for example: examples/pretrain/pretrain_gpt_distributed.sh 8 hostfile /path/dataset"
echo "It is better to use absolute path."
echo "=============================================================================================================="
export GLOG_v=3

RANK_SIZE=$1
HOSTFILE=$2
DATASET=$3

mpirun --allow-run-as-root -n $RANK_SIZE --hostfile $HOSTFILE \
      --output-filename run_distributed_train_gpt \
      --mca btl tcp,self --mca btl_tcp_if_include 10.90.43.0/24,enp177s0f0 --merge-stderr-to-stdout \
python -s ./transformer/train.py  \
    --config='./transformer/configs/gpt/gpt_base.yaml' \
    --device_num=$RANK_SIZE \
    --data_url=$DATASET \
    --max_seq_length=1024 \
    --global_batch_size=4 \
    --vocab_size=50304 \
    --parallel_mode="semi_auto_parallel" \
    --hidden_size=5120 \
    --recompute=True \
    --num_layers=24 \
    --data_parallel=2 \
    --model_parallel=4 \
    --num_attention_heads=40 \
    --device_target="GPU" > distribute_train_gpu_log.txt 2>&1 &
