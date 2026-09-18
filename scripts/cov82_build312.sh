#!/bin/sh
echo "== a: 创建 py312 构建 venv =="
rm -rf /data/dsh/venv-build312
python3.12 -m venv /data/dsh/venv-build312 && echo VENV312-OK

echo "== b: 只装 setuptools wheel =="
/data/dsh/venv-build312/bin/python -m pip install -q --no-index \
  /data/dsh/wheels/setuptools-84.0.0-py3-none-any.whl && echo DEPS-OK

echo "== c: 编译 cp312 coverage wheel =="
/data/dsh/venv-build312/bin/python -m pip wheel --no-index --no-build-isolation \
  -w /data/dsh/wheels /data/dsh/wheels/coverage-7.16.1.tar.gz 2>&1 | tail -4

echo "== d: 验证 cp312 wheel 内含 .so =="
ls -la /data/dsh/wheels/coverage*cp312*.whl 2>/dev/null
/data/dsh/venv-build312/bin/python -m zipfile -l /data/dsh/wheels/coverage-7.16.1-cp312-cp312-freebsd*.whl 2>/dev/null | grep '\.so$'

echo "== e: 冒烟测试 cp312 wheel =="
/data/dsh/venv-build312/bin/python -m pip install -q --no-index --no-deps --force-reinstall /data/dsh/wheels/coverage-7.16.1-cp312-cp312-freebsd*.whl
/data/dsh/venv-build312/bin/python -c "from coverage.tracer import CTracer; print('CP312-C-TRACER-OK')"
/data/dsh/venv-build312/bin/coverage --version

echo "== f: 清理 =="
rm -rf /data/dsh/venv-build312 && echo CLEAN-OK
