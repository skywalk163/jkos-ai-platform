#!/bin/sh
WHL=/data/dsh/wheels/coverage-7.16.1-cp311-cp311-freebsd_15_1_stable_amd64.whl
SP=/data/dsh/venv-test/lib/python3.11/site-packages/coverage

echo "== a: pip shebang 现状 =="
head -1 /data/dsh/venv-test/bin/pip /data/dsh/venv-test/bin/pip3 2>/dev/null

echo "== b: 用 python -m pip 强制重装到 venv-test =="
/data/dsh/venv-test/bin/python -m pip install -q --no-index --no-deps --force-reinstall "$WHL" && echo INSTALL-OK

echo "== c: tracer .so 落地确认 =="
ls -la "$SP" | grep -i tracer

echo "== d: C tracer 导入检查 =="
/data/dsh/venv-test/bin/python -c "from coverage.tracer import CTracer; print('C-TRACER-OK')"

echo "== e: coverage --version =="
/data/dsh/venv-test/bin/coverage --version
