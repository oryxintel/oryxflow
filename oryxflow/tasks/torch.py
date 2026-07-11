from oryxflow.tasks import TaskData
from oryxflow.targets.torch import PyTorchModel

class PyTorch(TaskData):
    """
    Task which saves to .pt models
    """
    target_class = PyTorchModel
    target_ext = '.pt'

