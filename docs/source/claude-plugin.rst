Using oryxflow with Claude Code
===============================================

Prefer to build oryxflow projects with an AI coding assistant instead of writing the task
wiring yourself? There is an official **Claude Code plugin**, ``oryxflow``, that scaffolds
projects, wires tasks with ``@oryxflow.requires``, and follows the house conventions
automatically.

Install
-----------------------------------------------------------

.. code-block:: text

    /plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
    /plugin install oryxflow@oryxflow

Commands
-----------------------------------------------------------

- ``/oryxflow:init-project`` - scaffold a runnable project in an empty directory.
- ``/oryxflow:init-gitlfs`` - put ``data/`` under Git LFS.
- ``/oryxflow:update-project`` - update an older project's scaffold floor.
- ``/oryxflow:check-standards`` - check names, style, and docstrings against the house standards.

Once installed, the ``oryxflow`` skill auto-activates whenever you work in a oryxflow project
(editing ``tasks.py`` / ``flow.py`` / ``run.py`` / ``cfg.py`` / ``flow_params.py``).

Learn more
-----------------------------------------------------------

- Plugin repository and issues: https://github.com/oryxintel/oryxflow-claude-plugin
- House conventions the plugin follows:
  https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/skills/oryxflow/conventions.md
- Plugin changelog:
  https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/docs/CHANGELOG.md

This library is the engine the plugin drives; the full API is documented throughout the rest of
these docs.
