#!/bin/sh
# 在 0.82 上验证 coverage wheel 内含 C tracer，并装入 venv-test
WHL=/data/dsh/wheels/coverage-7.16.1-cp311-cp311-freebsd_15_1_stable_amd64.whl

echo "== a: wheel 内的 C 扩展 =="
/data/dsh/venv-build/bin/python -m zipfile -l "$WHL" | grep -E 'tracer|\.so$'

echo "== b: 安装到 venv-test =="
/data/dsh/venv-test/bin/pip install -q --no-index --no-deps --force-reinstall "$WHL" && echo INSTALL-OK

echo "== c: C tracer 导入检查 =="
/data/dsh/venv-test/bin/python -c "from coverage.tracer import CTracer; print('C-TRACER-OK')"

echo "== d: coverage --version =="
/data/dsh/venv-test/bin/coverage --version
