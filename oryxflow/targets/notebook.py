import pathlib

from oryxflow.targets import _LocalPathTarget


class NotebookTarget(_LocalPathTarget):
    """Executed notebook (.ipynb, code included) plus a standalone HTML report (.html)."""

    @property
    def html_path(self):
        return self.path.with_suffix('.html')

    def exists(self):
        return self.path.exists() and self.html_path.exists()

    def load(self, cached=False, **kwargs):
        if not self.exists():
            raise RuntimeError('Target does not exist, make sure task is complete')
        return self.html_path

    def save(self, executed_path, html_template='classic', include_code=False):
        """Export the HTML report, then move the executed notebook into place."""
        import nbformat
        from nbconvert import HTMLExporter

        executed_path = pathlib.Path(executed_path)
        exporter = HTMLExporter(template_name=html_template)
        exporter.exclude_input = not include_code
        exporter.exclude_input_prompt = True
        exporter.exclude_output_prompt = True
        nb = nbformat.read(str(executed_path), as_version=4)
        body, _ = exporter.from_notebook_node(nb)
        self.html_path.write_text(body, encoding='utf-8')
        executed_path.replace(self.path)
        return self.path

    def invalidate(self):
        for p in (self.html_path, self.path):
            if p.exists():
                p.unlink()
        return not self.exists()
