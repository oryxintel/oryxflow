isinit = False # is initialized?
cached = False # cache files in memory
save_with_param = True # save files for each parameter setting

from pathlib import Path
dir = 'data'
dirpath = Path(dir)

# code-invalidation record store (see oryxflow/state.py): one JSON file with this name
# lives in every data directory and travels with its artifacts (not a database)
state_filename = '.oryxflow-code-status.json'

# event stream (see oryxflow/events.py): run records are always on; set events=False to
# make every append a complete no-op (no dir created, no index touched)
events = True
eventspath = Path('.oryxflow')

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
