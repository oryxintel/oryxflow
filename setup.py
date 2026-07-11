from setuptools import setup

setup(
    name='oryxflow',
    version='26.6.6',
    packages=['oryxflow','oryxflow.targets','oryxflow.tasks'],
    url='https://github.com/oryxintel/oryxflow',
    license='MIT',
    author='Oryx Intelligence LLC',
    author_email='dev@oryxintel.com',
    description='For data scientists and data engineers, oryxflow is a python library which makes building complex data science workflows easy, fast and intuitive.',
    long_description='oryxflow is a python library which makes it easier to build data workflows'
        'See https://github.com/oryxintel/oryxflow for details',
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
    classifiers=[]
)

'''
pip install -e .

# publish
# pip install setuptools wheel twine
python setup.py sdist bdist_wheel
twine upload dist/*  --skip-existing
'''
