# Installation

Install the latest release from PyPI:

```bash
pip install oryxflow
```

To upgrade later:

```bash
pip install oryxflow -U
```

!!! note

    oryxflow is **Python 3 only**. If Python 3 is not your default, use `pip3 install oryxflow`.
    Behind an enterprise firewall you can clone the repository and run `pip install .` from its
    root instead.

## Development version

To install the latest unreleased code straight from GitHub:

```bash
pip install git+https://github.com/oryxintel/oryxflow.git
```

Or upgrade in place without touching dependencies:

```bash
pip install git+https://github.com/oryxintel/oryxflow.git -U --no-deps
```

## Optional extras

The core install keeps dependencies light. Enable the pieces you need with pip *extras*:

| Extra | Install | Adds |
| --- | --- | --- |
| Cloud storage (GCS) | `pip install "oryxflow[gcs]"` | Read/write task outputs on Google Cloud Storage |
| Cloud storage (S3) | `pip install "oryxflow[s3]"` | Read/write task outputs on Amazon S3 |
| Cloud storage (base) | `pip install "oryxflow[cloud-base]"` | fsspec/`universal_pathlib` backend without a specific provider |
| Flow export | `pip install "oryxflow[export]"` | `FlowExport` / `FlowImport` standalone task files (needs Jinja2) |
| Dask | `pip install "oryxflow[dask]"` | Dask DataFrame task types |

After installing a cloud extra, point the engine at remote storage with
`oryxflow.enable_gcs(bucket, prefix=None)` or the generic
`oryxflow.enable_cloud_storage(protocol, bucket, prefix=None)`. See
[Collaborate & share flows](collaborate.md) for the full workflow.

## Build with an AI assistant

If you use [Claude Code](https://claude.com/claude-code), the official plugin can scaffold your
project and wire task dependencies for you:

```text
/plugin install oryxflow@oryxflow
```

See [Build with Claude Code](claude-plugin/index.md) for what it sets up.

## Next steps

- [Quickstart](quickstart.md) — build your first self-caching pipeline.
- [Transition from scripts](transition.md) — convert an existing analysis script.
