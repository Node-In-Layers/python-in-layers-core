#!/bin/bash
rm -Rf ./dist/
poetry run python -m build
poetry run python -m twine upload dist/*