#!/bin/sh
cd /data/dsh/code-jkos || exit 1
/data/dsh/venv-test/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -8
echo "PYTEST_EXIT=$?"
