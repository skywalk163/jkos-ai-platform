#!/bin/sh
# 0.82 双 venv 全量回归
cd /data/dsh/code-jkos || exit 1
echo "== venv-test312 (py3.12) =="
/data/dsh/venv-test312/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2
echo "== venv-test (py3.11) =="
/data/dsh/venv-test/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2
