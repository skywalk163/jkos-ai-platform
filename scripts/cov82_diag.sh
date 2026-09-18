#!/bin/sh
SP=/data/dsh/venv-test/lib/python3.11/site-packages/coverage
echo "== a: site-packages 里的 tracer 文件 =="
ls -la "$SP" | grep -i tracer
echo "== b: 直接 import 的真实报错 =="
/data/dsh/venv-test/bin/python -c "import coverage.tracer" 2>&1
echo "== c: coverage 包的安装来源 =="
/data/dsh/venv-test/bin/pip show -f coverage 2>/dev/null | head -8
echo "== d: .so 依赖检查 =="
cc -Wall 2>/dev/null; ldd "$SP/tracer.cpython-311.so" 2>&1
