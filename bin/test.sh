#!/bin/bash
poetry run pytest --cov=. --cov-report=term-missing --cov-report=html -q