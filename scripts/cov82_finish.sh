#!/bin/sh
echo "== a: 修复 venv-test 的 pip shebang (FreeBSD sed 语法) =="
sed -i '' '1s|.*|#!/data/dsh/venv-test/bin/python|' /data/dsh/venv-test/bin/pip /data/dsh/venv-test/bin/pip3
head -1 /data/dsh/venv-test/bin/pip
/data/dsh/venv-test/bin/pip --version 2>&1 | head -1

echo "== b: 系统 python3.12 是否自带 setuptools =="
python3.12 -c "import setuptools; print('setuptools', setuptools.__version__)" 2>&1
python3.12 -c "import setuptools_scm; print('scm', setuptools_scm.__version__)" 2>&1
