# T5

## 模型描述

T5:全名`Text-to-Text Transfer Transformer`模型是谷歌在2019年基于C4数据集训练的Transformer模型。

[论文](https://arxiv.org/abs/1910.10683)C Raffel，N Shazeer，A Roberts，K Lee，S Narang，M Matena，Y Zhou，W Li，PJ Liu, 2020

## 数据集准备

使用的数据集：[WMT16](https://cdn-datasets.huggingface.co/translation/wmt_en_ro.tar.gz)

对应的文件路径如下：

```bash
└── wmt_en_ro
    ├── test.source
    ├── test.target
    ├── train.source
    ├── train.target
    ├── val.source
    └── val.target
```

## 快速使用

### 脚本启动

> 需开发者提前clone工程。

- 请参考[使用脚本启动](https://gitee.com/mindspore/transformer/blob/master/README.md#%E6%96%B9%E5%BC%8F%E4%B8%80clone-%E5%B7%A5%E7%A8%8B%E4%BB%A3%E7%A0%81)

### 调用API启动

> 需开发者提前pip安装。具体接口说明请参考[API接口](https://gitee.com/mindspore/transformer/wikis/API/)

#### 计算Loss

```python
from mindformers import T5ModelForLoss, T5Tokenizer
model = T5ModelForLoss.from_pretrained('t5_small')
tokenizer = T5Tokenizer.from_pretrained('t5_small')

src_output = tokenizer(["hello world"], padding='max_length', max_length=model.config.seq_length,
                       return_tensors='ms')

model_input = tokenizer(["So happy to see you!"], padding='max_length', max_length=model.config.max_decode_length,
                        return_tensors='ms')["input_ids"]
input_ids = src_output['input_ids']
attention_mask = src_output['attention_mask']
output = model(input_ids, attention_mask, model_input)
print(output)
#[5.64458]
```

#### 推理

执行下述的命令，可以自动云上拉取`t5_small`模型并且进行推理。

```python
from mindformers import T5ModelForGeneration, T5Tokenizer
t5 = T5ModelForGeneration.from_pretrained("t5_small")
tokenizer = T5Tokenizer.from_pretrained("t5_small")
words = tokenizer("translate the English to the Romanian: UN Chief Says There Is No Military "
                  "Solution in Syria")['input_ids']
output = t5.generate(words, do_sample=False)
output = tokenizer.decode(output, skip_special_tokens=True)
print(output)
# "eful ONU declară că nu există o soluţie militară în Siri"
```

## 模型权重

本仓库中的`t5_small`来自于HuggingFace的[`t5_small`](https://huggingface.co/t5-small), 基于下述的步骤获取：

1. 从上述的链接中下载`t5_small`的HuggingFace权重，文件名为`pytorch_model.bin`

2. 执行转换脚本，得到转换后的输出文件`mindspore_t5.ckpt`

```python
python mindformers/models/t5/convert_weight.py --layers 6 --torch_path pytorch_model.bin --mindspore_path ./mindspore_t5.ckpt
```