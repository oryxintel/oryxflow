---
title: Built for AI coding agents
description: "oryxflow is AI-coding ready: the whole documentation is available as llms.txt and llms-full.txt for one-request ingestion, the core examples are executed by the test suite on every build, and the API reference is generated from docstrings that cover 100% of the public API."
faq:
  - q: "Does oryxflow have an llms.txt?"
    a: "Yes. https://docs.oryxflow.dev/llms.txt is a sectioned index of every page, and https://docs.oryxflow.dev/llms-full.txt is the entire documentation concatenated into one file, so an agent can read the whole thing in a single request instead of crawling page by page."
  - q: "How do I get an AI coding agent to write correct oryxflow code?"
    a: "Point it at https://docs.oryxflow.dev/llms-full.txt once at the start of the session, or install the Claude Code plugin, which loads the conventions automatically. Both work because the examples an agent reads first — the home page, the quickstart and the I/O formats guide — are executed by the test suite on every build, so what it copies is code that actually runs."
  - q: "Can I trust the code examples in the oryxflow docs?"
    a: "The core learning path is unit-tested: the home page, the quickstart and the I/O formats guide are compiled into pytest files with phmdoctest and run top-to-bottom on every build, so an example that stopped working fails CI instead of quietly misleading you or your agent. Examples on the deeper guide pages and blog posts are not all executed."
  - q: "Does oryxflow work with agents other than Claude Code?"
    a: "Yes. The machine-readable docs, the tested examples and the docstrings are plain files on a public site, so any agent or assistant can use them. The Claude Code plugin is a convenience that packages the same conventions as a skill; the library itself is an ordinary Python package."
---

# oryxflow is built for AI coding agents — documentation included

Most libraries are documented for humans and then discovered by agents by accident. oryxflow is
written for both. Two halves:

- **The library** gives an agent the discipline it can't hold in its head — every step cached with
  an identity derived from its parameters *and* its code, so it reuses expensive results and can't
  build on stale ones. That's the story in
  [Claude Code for data science](claude-code-for-data-science.md).
- **The documentation** gives an agent something it can actually read, in bulk, and trust. That's
  this page, collected in one place so you don't have to take "AI-friendly" on faith.

## Read the entire documentation in one request

The docs publish the [llms.txt convention](https://llmstxt.org/), so an agent doesn't have to crawl
page by page and guess what it missed:

| URL | What it is | Use it for |
| --- | --- | --- |
| [`/llms.txt`](https://docs.oryxflow.dev/llms.txt) | A sectioned index — every page, grouped, with a one-line description of the library | Cheap orientation: the agent picks the pages it needs |
| [`/llms-full.txt`](https://docs.oryxflow.dev/llms-full.txt) | The complete documentation concatenated into a single file | One request, whole corpus — no crawling, nothing missed |

The fastest way to make any agent competent at oryxflow is to hand it the second URL at the start
of a session:

```text
Read https://docs.oryxflow.dev/llms-full.txt, then convert my script in analysis.py
into oryxflow tasks.
```

Both files are regenerated on every deploy, so they never describe an older version of the library
than the one you installed.

## The examples an agent reads first are executed by the test suite

An agent that reads a broken example writes broken code with total confidence. So the pages an agent
learns the library from aren't just illustrative — the [home page](../index.md), the
[quickstart](quickstart.md) and the [I/O formats guide](targets.md) are compiled into real pytest
files and run top-to-bottom on every build. If one stops working, the build fails and it gets fixed;
it doesn't sit there misleading people.

That's the core learning path, so you can tell an agent to copy a pattern from it and expect it to
run. To be straight about the boundary: examples on the deeper guide pages and the blog aren't all
executed, so treat those as illustrative — and the machine-readable
[API reference](reference.md) below is generated from the source either way.

## The API reference is generated from the docstrings — all of them

[API Reference](reference.md) is built from the source, not maintained alongside it, so it can't
drift. **100% of the public API carries a docstring**, with arguments, return values and a usage
example where the call isn't obvious.

This matters for agents specifically: docstrings are available at runtime, so an agent working in a
REPL or a notebook can check a signature without leaving the process, and without inventing an
argument that doesn't exist.

```python
help(oryxflow.requires)      # what it does, what it takes, and an example
help(oryxflow.runLoad)
```

## Answer engines get a machine-readable description of the project

Every page carries structured data (JSON-LD): the library as a single identified entity
cross-linked to its GitHub and PyPI identities, each page as an article, and the pages with a Q&A
section as a `FAQPage`. Every answer in that structured data is generated from the visible prose,
so an assistant quoting oryxflow quotes what the page actually says.

Canonical URLs are stamped on every page and the sitemap's freshness dates come from git commit
history, so an agent or crawler comparing versions sees one authoritative copy with an honest
last-modified date.

## The conventions ship as a skill, not just prose

Reading the docs is the general-purpose route. For Claude Code there's a shortcut: the
[oryxflow plugin](claude-plugin/index.md) installs a skill that auto-activates in a pipeline
project, so the agent applies the conventions as it writes instead of needing to be reminded. The
slash commands scaffold a project, migrate an existing script, and check an existing project
against the standards.

Projects scaffolded by the plugin also get a `CLAUDE.md` describing the layout and rules — which
any agent that reads repo instructions can follow, not only Claude Code.

## Frequently asked questions

**Does oryxflow have an llms.txt?**
Yes. [`/llms.txt`](https://docs.oryxflow.dev/llms.txt) is a sectioned index of every page, and
[`/llms-full.txt`](https://docs.oryxflow.dev/llms-full.txt) is the entire documentation
concatenated into one file, so an agent can read the whole thing in a single request instead of
crawling page by page.

**How do I get an AI coding agent to write correct oryxflow code?**
Point it at `https://docs.oryxflow.dev/llms-full.txt` once at the start of the session, or install
the [Claude Code plugin](claude-plugin/index.md), which loads the conventions automatically. Both
work because the examples an agent reads first — the home page, the quickstart and the I/O formats
guide — are executed by the test suite on every build, so what it copies is code that actually runs.

**Can I trust the code examples in the oryxflow docs?**
The core learning path is unit-tested: the home page, the quickstart and the I/O formats guide are
compiled into pytest files with phmdoctest and run top-to-bottom on every build, so an example that
stopped working fails CI instead of quietly misleading you or your agent. Examples on the deeper
guide pages and blog posts are not all executed.

**Does oryxflow work with agents other than Claude Code?**
Yes. The machine-readable docs, the tested examples and the docstrings are plain files on a public
site, so any agent or assistant can use them. The Claude Code plugin is a convenience that packages
the same conventions as a skill; the library itself is an ordinary Python package.

## Next

- **[Claude Code for data science](claude-code-for-data-science.md)** — the other half: what the
  library stops an agent from getting wrong.
- **[Build with Claude Code](claude-plugin/index.md)** — the plugin, its commands and its trust
  model.
- **[API Reference](reference.md)** — the generated symbol reference.
- **[Quickstart](quickstart.md)** — from nothing to a self-caching pipeline in minutes.
