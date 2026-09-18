#!/bin/sh
cd /data/dsh/code-jkos || exit 1

echo "== a: venv-test312 (py3.12) 全量回归 =="
/data/dsh/venv-test312/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2

echo "== b: venv-test (py3.11) 全量回归 =="
/data/dsh/venv-test/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2

echo "== c: AC-2 残留扫描 =="
grep -rEl 'DSH AI 中台|dsh_core|dsh-server|dsh-plugin|DSH AI Platform' \
  jkos_core tests tenants server.py pyproject.toml locales 2>/dev/null && echo "有残留!" || echo "AC2-CLEAN"
