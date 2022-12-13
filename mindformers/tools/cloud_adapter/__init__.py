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
"""MindFormers Cloud Adapter API."""
from ..utils import PARALLEL_MODE, MODE, DEBUG_INFO_PATH,\
    Validator, check_in_modelarts, sync_trans, get_net_outputs
from .cloud_monitor import cloud_monitor
from .cfts import CFTS
from .cloud_adapter import Obs2Local, Local2ObsMonitor, CheckpointCallBack