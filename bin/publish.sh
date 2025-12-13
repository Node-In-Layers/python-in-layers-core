#!/bin/bash
set -e
./bin/lint.sh || exit 1
./bin/test.sh || exit 1
rm -Rf ./dist/
poetry run python -m build
poetry run python -m twine upload dist/*