set -e
echo "== clone venv-test =="
rm -rf /data/dsh/venv-test
cp -a /data/dsh/venv /data/dsh/venv-test
echo cloned
echo "== pip install test stack =="
/data/dsh/venv-test/bin/python -m pip install --no-cache-dir -q pytest==9.1.1 pytest-asyncio==1.4.0 pytest-cov==7.1.0 pytest-xdist==3.8.0 respx==0.23.1 python-dotenv==1.2.3 websockets==17.0.1 Jinja2==3.1.6
echo INSTALL-OK
/data/dsh/venv-test/bin/python -m pytest --version
/data/dsh/venv-test/bin/python -c 'import fastapi, pydantic, yaml, jinja2, pytest; print("imports-ok", fastapi.__version__, pydantic.VERSION, pytest.__version__)'
