import sys
from loguru import logger

# Library best practice: emit nothing until the host app opts in.
logger.disable("oryxflow")

# Handler id of the sink the most recent enable_logging() added, so repeat calls
# replace it instead of stacking duplicate sinks.
_handler_id = None


def enable_logging(level=None, sink=sys.stderr, colorize=None):
    """Turn on oryxflow's internal logging.

    With the default ``sink=sys.stderr`` this gives one clean oryxflow log stream:
    loguru's pristine default handler is removed (so records aren't printed twice)
    and a oryxflow-filtered handler is added at ``level``. Calling it again replaces
    that handler rather than adding another. Pass ``sink=None`` to leave all loguru
    handlers untouched and route oryxflow records into the host app's existing sinks.

    Args:
        level (str): minimum level to surface. Defaults to
            ``oryxflow.settings.log_level`` ("INFO").
        sink: where to write. Default sys.stderr. Pass sink=None to only
            re-enable the 'oryxflow' namespace and rely on the host app's
            existing loguru sinks (no new handler added).
        colorize (bool): whether to emit ANSI color markup. ``True``/``False``
            force it on/off; the default ``None`` auto-detects -- colored only
            when the sink is a TTY, so redirected or captured runs (files, pipes,
            pytest capture) come out clean without a separate plain sink.
    Returns:
        handler id (int) if a sink was added, else None. Pass it to
        logger.remove() for fine-grained teardown.
    """
    global _handler_id
    if level is None:
        # lazy import: settings -> core -> log would be circular at import time.
        from oryxflow import settings
        level = settings.log_level
    logger.enable("oryxflow")
    if sink is None:
        return None
    if colorize is None:
        # Uncolored unless the sink is an interactive terminal, so redirected
        # output stays free of ANSI escape codes.
        colorize = getattr(sink, "isatty", lambda: False)()
    # Drop the sink a previous enable_logging() added, and loguru's pristine
    # default handler (id 0), so oryxflow records appear exactly once.
    for hid in ([_handler_id] if _handler_id is not None else []) + [0]:
        try:
            logger.remove(hid)
        except ValueError:
            pass
    _handler_id = logger.add(sink, level=level, filter="oryxflow", colorize=colorize)
    return _handler_id


def disable_logging():
    """Silence oryxflow's internal logging again."""
    logger.disable("oryxflow")


# Capture hook for task-authored log lines (see oryxflow/events.py `task_log` events).
# Set by core.build() for the duration of a build. A loguru sink cannot be used for
# this: with logging disabled (the library default) loguru drops oryxflow records
# before any sink sees them, and events must be always-on regardless of log gating.
_task_log_capture = None

_LEVEL_METHODS = {"trace", "debug", "info", "success", "warning", "error",
                  "critical", "exception", "log"}


def set_task_log_capture(fn):
    """Install (or with None, remove) the build's task-log capture; returns the previous
    hook so re-entrant builds can restore it."""
    global _task_log_capture
    previous = _task_log_capture
    _task_log_capture = fn
    return previous


class TaskLogger:
    """Contextual logger facade returned by ``Task.logger``.

    Two things make it behave like the rest of oryxflow's logging:

    * It pre-binds the task's ``task_id`` / ``task_family`` onto every record.
    * The actual emit happens *here*, inside the ``oryxflow`` package, so records
      land in the ``oryxflow`` logging namespace and are therefore governed by
      ``enable_logging`` / ``disable_logging`` -- silent until the app opts in --
      no matter which module the task class is defined in. (The record is patched
      to display the name ``oryxflow.task`` for readability.)

    Forwards every loguru logger method (``debug``/``info``/``warning``/``error``/
    ``exception``/``bind``/...) unchanged; use loguru's ``{}`` brace style for args,
    e.g. ``self.logger.info("rows: {}", len(df))``.
    """
    __slots__ = ("_logger", "_context")

    def __init__(self, _logger=None, _context=None, **context):
        self._logger = _logger if _logger is not None else logger.patch(
            lambda record: record.update(name="oryxflow.task")).bind(**context)
        # kept alongside the bound loguru logger so the task_log capture can tag
        # events with task identity without reaching into loguru internals
        self._context = dict(_context if _context is not None else context)

    def _capture(self, level, args, kwargs):
        if _task_log_capture is None:
            return
        try:
            if level == "log" and args:
                level, args = str(args[0]), args[1:]
            elif level == "exception":
                level = "error"
            message = str(args[0]) if args else ""
            fmt_args = args[1:]
            if fmt_args or kwargs:
                try:
                    message = message.format(*fmt_args, **kwargs)
                except Exception:
                    pass
            _task_log_capture(level.upper(), message, dict(self._context))
        except Exception:
            pass  # capture must never break task logging

    def __getattr__(self, name):
        # Delegate to the bound loguru logger, but wrap so the actual call happens
        # in THIS module: loguru derives record["name"] from the calling frame, and
        # only a oryxflow.* name is governed by enable_logging/disable_logging.
        # bind() must stay a TaskLogger so the wrapping survives added context.
        if name in ("_logger", "_context"):  # slot unset (e.g. pre-init) -> avoid recursion
            raise AttributeError(name)
        attr = getattr(self._logger, name)
        if name == "bind":
            return lambda **kwargs: TaskLogger(_logger=attr(**kwargs),
                                               _context={**self._context, **kwargs})
        if not callable(attr):
            return attr
        if name in _LEVEL_METHODS:
            def _wrapped(*args, __name=name, **kwargs):
                self._capture(__name, args, kwargs)
                return attr(*args, **kwargs)
            return _wrapped
        return lambda *args, **kwargs: attr(*args, **kwargs)
