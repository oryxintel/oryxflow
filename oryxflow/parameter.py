"""
Self-contained, trimmed set of parameter types used by oryxflow.

There is no command-line / config-file value resolution, parameter visibility,
``date_interval``, freezing (``FrozenOrderedDict``) or ``jsonschema`` support. Values
are resolved from the constructor argument or the ``default`` only.

Only the parameter types oryxflow actually uses are kept:
``Parameter``, ``IntParameter``, ``FloatParameter``, ``BoolParameter``, ``DateParameter``,
``DictParameter``, ``ListParameter``, ``ChoiceParameter`` and ``EnumParameter``.
"""

import datetime
import json

# Sentinel for "no default value provided".
_no_value = object()


class ParameterException(Exception):
    """Base parameter exception."""
    pass


class MissingParameterException(ParameterException):
    """Raised when a required parameter has no value and no default."""
    pass


class UnknownParameterException(ParameterException):
    """Raised when an unknown parameter is supplied to a task."""
    pass


class DuplicateParameterException(ParameterException):
    """Raised when a parameter is supplied both positionally and as a keyword."""
    pass


class Parameter:
    """
    Parameter whose value is a ``str``, and the base class for the other parameter types.

    Parameters are set on the Task class to parameterize tasks::

        class MyTask(oryxflow.tasks.TaskData):
            foo = oryxflow.Parameter()

    When a value is not provided at instantiation, the ``default`` is used.
    """

    # Non-atomically increasing counter used to preserve declaration order (see Task.get_params).
    _counter = 0

    def __init__(self, default=_no_value, significant=True, description=None, positional=True):
        """
        :param default: the default value for this parameter. If not given, a value must be
                        specified when the task is instantiated.
        :param bool significant: ``False`` if the parameter should not be part of a task's unique
                                 identifier (eg passwords, environment markers). Default ``True``.
        :param str description: human-readable description of the parameter.
        :param bool positional: if ``True`` the parameter may be set positionally. Default ``True``.
        """
        self._default = default
        self.significant = significant
        self.description = description
        self.positional = positional

        self._counter = Parameter._counter
        Parameter._counter += 1

    def has_task_value(self):
        """Whether a default value is available."""
        return self._default != _no_value

    def task_value(self):
        """The normalized default value. Raises if no default is set."""
        if self._default == _no_value:
            raise MissingParameterException("No default specified")
        return self.normalize(self._default)

    def parse(self, x):
        """Parse an individual value from a string. Identity by default."""
        return x

    def serialize(self, x):
        """Convert the value ``x`` to a string. Opposite of :py:meth:`parse`."""
        return str(x)

    def normalize(self, x):
        """Normalize a parsed/default/constructor value. Identity by default."""
        return x


class IntParameter(Parameter):
    """Parameter whose value is an ``int``."""

    def parse(self, s):
        return int(s)


class FloatParameter(Parameter):
    """Parameter whose value is a ``float``."""

    def parse(self, s):
        return float(s)


class BoolParameter(Parameter):
    """
    A Parameter whose value is a ``bool``. Has an implicit default of ``False``.
    """

    def __init__(self, *args, **kwargs):
        super(BoolParameter, self).__init__(*args, **kwargs)
        if self._default == _no_value:
            self._default = False

    def parse(self, val):
        """Parse a ``bool`` from the string, matching 'true'/'false' case-insensitively."""
        s = str(val).lower()
        if s == "true":
            return True
        elif s == "false":
            return False
        else:
            raise ValueError("cannot interpret '{}' as boolean".format(val))

    def normalize(self, value):
        try:
            return self.parse(value)
        except ValueError:
            return None


class DateParameter(Parameter):
    """
    Parameter whose value is a :py:class:`~datetime.date`, formatted ``YYYY-MM-DD``.
    """

    date_format = '%Y-%m-%d'

    def parse(self, s):
        return datetime.datetime.strptime(s, self.date_format).date()

    def serialize(self, dt):
        if dt is None:
            return str(dt)
        return dt.strftime(self.date_format)

    def normalize(self, value):
        if value is None:
            return None
        if isinstance(value, datetime.datetime):
            value = value.date()
        return value


class DictParameter(Parameter):
    """
    Parameter whose value is a ``dict``.

    The value is stored as-is (no freezing); it is serialized with sorted keys so that the
    task id stays deterministic.
    """

    def parse(self, source):
        if not isinstance(source, str):
            return source
        return json.loads(source)

    def serialize(self, x):
        return json.dumps(x, sort_keys=True)


class ListParameter(Parameter):
    """
    Parameter whose value is a ``list``.

    The value is stored as-is (no freezing); it is serialized as JSON so that the task id
    stays deterministic.
    """

    def parse(self, x):
        if not isinstance(x, str):
            return x
        i = json.loads(x)
        if i is None:
            return None
        return list(i)

    def serialize(self, x):
        return json.dumps(x)


class ChoiceParameter(Parameter):
    """
    A string-valued parameter restricted to a fixed set of ``choices``::

        class MyTask(oryxflow.tasks.TaskData):
            model = oryxflow.ChoiceParameter(choices=['rf', 'lgbm'], default='rf')

    Values stay plain strings (no ``enum.Enum`` ceremony, unlike
    :class:`EnumParameter`); a value outside ``choices`` raises immediately at task
    construction (or when the default is resolved), so typos fail fast instead of
    dying deep in downstream code.
    """

    def __init__(self, *args, **kwargs):
        if 'choices' not in kwargs:
            raise ParameterException('A choices list must be specified.')
        choices = kwargs.pop('choices')
        try:
            self._choices = list(choices)
        except TypeError:
            raise ParameterException('choices must be an iterable of values.')
        if not self._choices:
            raise ParameterException('choices must not be empty.')
        super(ChoiceParameter, self).__init__(*args, **kwargs)

    def normalize(self, x):
        if x not in self._choices:
            raise ValueError(
                "'{}' is not a valid choice - must be one of {}".format(x, self._choices))
        return x


class EnumParameter(Parameter):
    """
    A parameter whose value is an :class:`~enum.Enum`. Pass the enum class via ``enum=``::

        class Model(enum.Enum):
            Honda = 1
            Volvo = 2

        class MyTask(oryxflow.tasks.TaskData):
            my_param = oryxflow.EnumParameter(enum=Model)
    """

    def __init__(self, *args, **kwargs):
        if 'enum' not in kwargs:
            raise ParameterException('An enum class must be specified.')
        self._enum = kwargs.pop('enum')
        super(EnumParameter, self).__init__(*args, **kwargs)

    def parse(self, s):
        try:
            return self._enum[s]
        except KeyError:
            raise ValueError('Invalid enum value - could not be parsed')

    def serialize(self, e):
        return e.name
