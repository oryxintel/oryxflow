# pull latest github master
git fetch github master:master-prod
git checkout master-prod
git pull github master

git checkout master
git pull gitlab master
git merge master-prod

git commit -am "publish"
git push gitlab master
git push github master

# publish: releases go out from CI now (.github/workflows/release.yml, PyPI Trusted Publishing
# with attestations). Tag and publish a GitHub Release instead of uploading from this machine.
git tag v$(python -c "import oryxflow; print(oryxflow.__version__)") && git push github --tags
gh release create v$(python -c "import oryxflow; print(oryxflow.__version__)") --generate-notes

# local build only, to inspect artifacts (does NOT upload)
python -m build

# --- manual upload from this machine: the pre-CI process, kept as the fallback ---------------
# Emergency use only (CI broken / PyPI Trusted Publisher not yet registered). Needs an API token
# and produces NO attestations, so the release loses its provenance signal.
# pip install setuptools wheel twine
# python setup.py sdist bdist_wheel
# twine upload dist/*  --skip-existing

# admin
git checkout -b master-prod
git branch -d master-prod

