#!/usr/bin/env bash

set -e
set -x

python -m tests.database
python -m tests.database check
coverage run -m pytest tests/
coverage report
coverage html --title "${@-coverage}"
