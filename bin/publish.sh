#!/bin/bash
set -euo pipefail
poetry install
./bin/lint.sh || exit 1
./bin/test.sh || exit 1
rm -Rf ./dist/
# Ensure a clean environment without dev dependencies
rm -Rf ./.venv
# Install only non-dev dependencies (support both new and old Poetry flags)
poetry install --without dev --no-interaction --no-ansi || poetry install --no-dev --no-interaction --no-ansi

# Build and publish using a clean, tool-only virtualenv so build/twine
# don't need to be part of project dev dependencies
TOOLS_ENV=".publish-env"
python -m venv "${TOOLS_ENV}"
set +u
source "${TOOLS_ENV}/bin/activate"
set -u
python -m pip install --upgrade pip
python -m pip install build twine
python -m build
python -m twine upload dist/*
deactivate || true
rm -rf "${TOOLS_ENV}"
