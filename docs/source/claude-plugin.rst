Using oryxflow with Claude Code
===============================================

Prefer to build oryxflow projects with an AI coding assistant instead of writing the task
wiring yourself? There is an official **Claude Code plugin**, ``oryxflow``, that sets up a
ready-to-run project structure, wires tasks with ``@oryxflow.requires``, and follows the house
conventions automatically.

Install
-----------------------------------------------------------

.. code-block:: text

    /plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
    /plugin install oryxflow@oryxflow

Commands
-----------------------------------------------------------

- ``/oryxflow:init-project`` - set up a ready-to-run project structure in an empty directory, so
  you start writing tasks straight away instead of building the folders, files, and conventions
  by hand.
- ``/oryxflow:init-gitlfs`` - put ``data/`` under Git LFS, so you version and share your data as
  easily as your code — teammates clone the repo and get the exact datasets each run produced.
- ``/oryxflow:update-project`` - bring an older project up to the current project structure, so you
  pick up the latest conventions and layout without a manual migration.
- ``/oryxflow:check-standards`` - check names, style, and docstrings against the house standards,
  so the codebase stays consistent and easy for teammates (and the AI) to navigate and extend.

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
