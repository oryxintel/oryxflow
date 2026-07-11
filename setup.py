import os
from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='oryxflow',
    version='26.7.11',
    packages=['oryxflow','oryxflow.targets','oryxflow.tasks'],
    url='https://github.com/oryxintel/oryxflow',
    project_urls={
        'Documentation': 'https://oryxflow.readthedocs.io/',
        'Changelog': 'https://oryxflow.readthedocs.io/en/stable/changelog.html',
        'Source': 'https://github.com/oryxintel/oryxflow',
    },
    license='MIT',
    author='Oryx Intelligence LLC',
    author_email='dev@oryxintel.com',
    description='For data scientists and data engineers, oryxflow is a python library which makes building complex data science workflows easy, fast and intuitive.',
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
    python_requires='>=3.5',
    keywords=['oryxflow', 'data workflow', 'data pipelines'],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Programming Language :: Python :: 3',
        'Topic :: Scientific/Engineering',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ]
)

'''
pip install -e .

# publish
# pip install setuptools wheel twine
python -m build
python -m twine upload dist/*

# python -m twine upload --repository testpypi dist/*
# pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ --no-deps oryxflow

'''
