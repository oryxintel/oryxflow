isinit = False # is initialized?
cached = False # cache files in memory
save_with_param = True # save files for each parameter setting

from pathlib import Path
dir = 'data'
dirpath = Path(dir)

db=dirpath/'.oryxflow.json'

check_dependencies = True
check_crc = False
log_level = 'INFO'  # default level used by oryxflow.enable_logging(); see oryxflow/log.py
execution_summary = True

from oryxflow import core
def set_parameter_len(nparams=20, len=64):
    core.TASK_ID_INCLUDE_PARAMS=nparams
    core.TASK_ID_TRUNCATE_PARAMS=len
set_parameter_len()

uri = None

# cloud storage
cloud_fs_prefix = None
cloud_fs_enabled = False
