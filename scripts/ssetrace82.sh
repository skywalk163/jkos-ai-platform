cd /data/dsh/code-jkos
/data/dsh/venv-test/bin/python -m pytest -q --tb=short > /data/dsh/test-jkos.log 2>&1
echo "EXIT=$?"
awk '/= FAILURES =/,0' /data/dsh/test-jkos.log
