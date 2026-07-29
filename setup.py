import os
from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='oryxflow',
    version='26.7.28',
    packages=['oryxflow','oryxflow.targets','oryxflow.tasks'],
    url='https://github.com/oryxintel/oryxflow',
    project_urls={
        'Documentation': 'https://docs.oryxflow.dev/',
        'Changelog': 'https://docs.oryxflow.dev/docs/changelog/',
        'Source': 'https://github.com/oryxintel/oryxflow',
    },
    license='MIT',
    author='Oryx Intelligence LLC',
    author_email='dev@oryxintel.com',
    description='Faster, cheaper, more trustworthy data analysis — for humans and AI coding agents.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    install_requires=['pandas', 'pyarrow', 'markdown', 'openpyxl', 'loguru'
    ],
    extras_require={
        'dask': ['toolz','dask[dataframe]'],
        'cloud-base': ['universal_pathlib'],
        'gcs': ['gcsfs','universal_pathlib'],
        's3': ['s3fs','universal_pathlib'],
        'export': ['jinja2']},
include_package_data=True,
    python_requires='>=3.9',
    keywords=['oryxflow', 'data workflow', 'data pipelines'],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Topic :: Scientific/Engineering',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ]
)

'''
pip install -e .

# publish: bump the version above, tag, then publish a GitHub Release. CI takes it from there
# (.github/workflows/release.yml -> PyPI Trusted Publishing, no token, with attestations).
# Full checklist, verification and troubleshooting: devops/publish.md
git tag v26.7.21 && git push github v26.7.21
gh release create v26.7.21 --generate-notes

# local build to inspect the artifacts before releasing (does NOT upload)
python -m build

# --- manual upload from this machine: the pre-CI process, kept as the fallback ---------------
# Emergency use only (CI broken / PyPI Trusted Publisher not yet registered). Needs an API token
# and produces NO attestations, so the release loses its provenance signal.
# pip install setuptools wheel twine
# python -m build
# python -m twine upload --skip-existing dist/*

# testpypi
# python -m twine upload --repository testpypi dist/*
# pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ --no-deps oryxflow

'''
