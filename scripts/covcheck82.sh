P=/data/dsh/venv-test/bin/python
$P -c "import coverage; print('coverage', coverage.__version__)"
ls /data/dsh/venv-test/lib/python3.11/site-packages/coverage/*.so 2>&1
$P -c "import coverage.tracer; print('ctracer-ok')" 2>&1 | tail -2
echo "compilers:"; which cc clang gcc 2>&1
