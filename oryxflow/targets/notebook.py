import os
import pathlib
import shutil

from oryxflow.targets import _LocalPathTarget


class NotebookTarget(_LocalPathTarget):
    """Executed notebook (.ipynb, code included) plus a standalone HTML report (.html).

    ``publish`` lists extra copies under readable names: a ``.html`` path gets the report, a
    ``.ipynb`` path the executed notebook. The target exists only while every copy does, so a
    deleted published file reruns the report. ``title`` is the html page title.
    """

    def __init__(self, path=None, publish=(), title=None):
        super().__init__(path)
        self.publish = [pathlib.Path(p) for p in publish]
        self.title = title

    @property
    def html_path(self):
        return self.path.with_suffix('.html')

    def exists(self):
        return (self.path.exists() and self.html_path.exists()
                and all(p.exists() for p in self.publish))

    def load(self, cached=False, **kwargs):
        if not self.exists():
            raise RuntimeError('Target does not exist, make sure task is complete')
        return self.html_path

    def save(self, executed_path, html_template='classic', include_code=False):
        """Export the HTML report, move the executed notebook into place, then copy them to
        the ``publish`` paths."""
        import nbformat
        from nbconvert import HTMLExporter

        executed_path = pathlib.Path(executed_path)
        exporter = HTMLExporter(template_name=html_template)
        exporter.exclude_input = not include_code
        exporter.exclude_input_prompt = True
        exporter.exclude_output_prompt = True
        nb = nbformat.read(str(executed_path), as_version=4)
        resources = {'metadata': {'name': self.title}} if self.title else None
        body, _ = exporter.from_notebook_node(nb, resources=resources)
        self.html_path.write_text(body, encoding='utf-8')
        executed_path.replace(self.path)
        for dest in self.publish:
            _copy_atomic(self.path if dest.suffix.lower() == '.ipynb' else self.html_path, dest)
        return self.path

    def invalidate(self):
        # a published copy is a deliverable people open: the last good one stays in place
        for p in (self.html_path, self.path):
            if p.exists():
                p.unlink()
        return not self.exists()


def _copy_atomic(src, dest):
    """Copy via a temporary file, so a reader (or a sync client) never sees half a report."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + '.tmp')
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
