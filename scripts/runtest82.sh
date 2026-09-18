cd /data/dsh/code-jkos
/data/dsh/venv-test/bin/python -m pytest -q --tb=line > /data/dsh/test-jkos.log 2>&1
echo "EXIT=$?"
echo "===== tail ====="
tail -55 /data/dsh/test-jkos.log
