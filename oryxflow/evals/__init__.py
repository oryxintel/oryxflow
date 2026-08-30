"""LLM evaluation harness: one cached cell per arm of an eval matrix.

Requires the optional extra::

    pip install oryxflow[evals]

Nothing here is imported by ``oryxflow/__init__.py``, so a user who does not run
evals is unaffected by it. Import it explicitly::

    import oryxflow.evals as ev

SEAM: the per-case DataFrame is the interface. Everything downstream of a run --
``Metric``, the slices, the intervals, the verdict, the report -- reads only that
frame and never touches a pydantic-evals object. The two override points on
``TaskEval`` are the exit: ``_evaluate()`` returns a report, so swap it for a
different runner, and ``_to_frame(report)`` returns the rows, so swap it for a
different report shape. Nothing else has to change. The property that buys is
available today rather than later: every metric, verdict and report test is
written against a hand-built DataFrame, so everything downstream of a run is
testable without pydantic-evals present.

``cli`` and ``build_app`` are resolved on first use rather than at import, so
``import oryxflow.evals`` does not pay for Typer -- a sweep run from a notebook
never needs a command line.
"""

import importlib
import types

try:
    import pydantic_evals as _pydantic_evals
except ImportError:
    raise ImportError(
        "oryxflow.evals needs the 'evals' extra: pip install oryxflow[evals] "
        "(pydantic-evals, scipy, typer)")

from oryxflow.evals.baseline import (
    BaselineError,
    PromptArm,
    Variant,
    VariantError,
    git_sha,
    git_tree,
)
from oryxflow.evals.cases import RESERVED_METADATA, CaseList, load_cases
from oryxflow.evals.metric import Metric
from oryxflow.evals.stats import (DeltaCI, JudgeAlignment, RateCI, cluster_labels,
                                  corrected_rate, delta_ci, judge_alignment,
                                  rate_ci)
from oryxflow.evals.sweep import EvalResult, sweep
from oryxflow.evals.task import EvalFailureRateError, TaskEval

__all__ = [
    'BaselineError',
    'CaseList',
    'DeltaCI',
    'EvalFailureRateError',
    'EvalResult',
    'JudgeAlignment',
    'Metric',
    'PromptArm',
    'RESERVED_METADATA',
    'RateCI',
    'TaskEval',
    'Variant',
    'VariantError',
    'build_app',
    'cli',
    'cluster_labels',
    'corrected_rate',
    'delta_ci',
    'git_sha',
    'git_tree',
    'judge_alignment',
    'load_cases',
    'rate_ci',
    'sweep',
]

_LAZY = ('cli', 'build_app')


class _CliModule(types.ModuleType):
    """The ``cli`` submodule, made callable so ``ev.cli(MyEval)`` runs the command line.

    ``cli.py`` holds a function of the same name, and a package cannot expose both
    under one attribute: binding the function over it would hide the module, and
    with it ``oryxflow.evals.cli.check``. Calling the module runs its ``cli()``, so
    ``import oryxflow.evals.cli`` and ``ev.cli(MyEval)`` both mean what they say.
    """

    def __call__(self, task, argv=None):
        return self.cli(task, argv)


def __getattr__(name):
    """Import the Typer-backed names only when one of them is actually used."""
    if name in _LAZY:
        module = importlib.import_module('oryxflow.evals.cli')
        module.__class__ = _CliModule
        value = module if name == 'cli' else getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError("module {!r} has no attribute {!r}".format(__name__, name))


def __dir__():
    return sorted(set(list(globals()) + list(__all__)))
