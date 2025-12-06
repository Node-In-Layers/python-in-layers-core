#!/bin/bash

# Run Black
poetry run black .

# Run Ruff
poetry run ruff check --fix .