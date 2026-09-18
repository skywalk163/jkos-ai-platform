#!/bin/sh
cd /data/dsh/code-jkos || exit 1
echo "== a: 全量回归 =="
/data/dsh/venv-test312/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2
