from oryxflow.tasks import TaskData
from oryxflow.targets.h5 import H5PandasTarget

class TaskH5Pandas(TaskData):
    """
    Task which saves to HDF5
    """
    target_class = H5PandasTarget
    target_ext = 'hdf5'
