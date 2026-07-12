.. d6t-celery-combine documentation master file, created by
   sphinx-quickstart on Tue Nov 28 11:32:56 2017.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to oryxflow documentation!
==============================================

For data scientists and data engineers, oryxflow is a python library which makes it easier to
build data workflows. You declare each step of your analysis as a task; oryxflow runs them in the
right order, skips anything already computed, reruns exactly what a parameter, data, or **code**
change affects, and lets you load any result by name. It also records **what ran, when, and why**,
so "is this result stale?", "was it produced by the current code?", and "did I already run this?"
are queries, not guesses. The payoff: no wasted recomputation, reproducible outputs you can trust,
and pipelines that are easy to share — instead of a fragile chain of scripts and files you manage
by hand.


Installation
------------------------------------------------------------

Follow github instructions https://github.com/oryxintel/oryxflow#installation

Benefits of using oryxflow
------------------------------------------------------------

See `4 Reasons Why Your Machine Learning Code is Probably Bad <https://medium.com/@citynorman/4-reasons-why-your-machine-learning-code-is-probably-bad-c291752e4953>`_

Quickstart
------------------------------------------------------------

See https://github.com/oryxintel/oryxflow/blob/master/docs/example-ml.md

Build with an AI assistant (Claude Code)
------------------------------------------------------------

Prefer to have an AI coding assistant set up your project structure and wire the task
dependencies for you? There is an official Claude Code plugin,
`oryxflow-claude-plugin <https://github.com/oryxintel/oryxflow-claude-plugin>`_,
that does exactly that. See :doc:`Using oryxflow with Claude Code <claude-plugin>`.

Real-life project template
------------------------------------------------------------

https://github.com/d6t/d6tflow-template

Transition to oryxflow from typical scripts
------------------------------------------------------------

[5 Step Guide to Scalable Deep Learning Pipelines with oryxflow](https://htmlpreview.github.io/?https://github.com/d6t/d6t-python/blob/master/blogs/blog-20190813-d6tflow-pytorch.html)

Parameter Management
------------------------------------------------------------

Intelligent parameter management is one of the most powerful features of oryxflow. New users often have questions on parameter management, this is an important section to read.

User Guide
------------------------------------------------------------

.. toctree::
   :maxdepth: 2

   quickstart
   claude-plugin
   transition
   tasks
   workflow
   run
   logging
   targets
   collaborate
   managing-workflows
   advtasksdyn
   advparam
   modules
   functional_tasks
   changelog


API Docs
""""""""""

* :ref:`modindex`

Search
""""""""""

* :ref:`search`
