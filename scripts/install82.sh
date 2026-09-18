P=/data/dsh/venv-test/bin/python
W=/data/dsh/wheels
$P -c "import setuptools; print('setuptools', setuptools.__version__)"
echo "== a: urllib3+pycparser (pure wheels) =="
$P -m pip install --no-index --find-links=$W -q urllib3 pycparser && echo SA-OK || echo SA-FAIL
echo "== b: setuptools_scm 8.3.1 =="
SETUPTOOLS_SCM_PRETEND_VERSION=8.3.1 $P -m pip install --no-index --no-build-isolation --find-links=$W -q setuptools_scm==8.3.1 && echo SB-OK || echo SB-FAIL
echo "== c: build argon2-cffi-bindings wheel =="
rm -rf /tmp/a2b && tar -C /tmp -xzf $W/argon2-cffi-bindings-21.2.0.tar.gz && mv /tmp/argon2-cffi-bindings-21.2.0 /tmp/a2b
SETUPTOOLS_SCM_PRETEND_VERSION=21.2.0 $P -m pip wheel --no-index --no-build-isolation --no-deps -w $W /tmp/a2b > /tmp/a2b-build.log 2>&1 && ls $W/argon2_cffi_bindings-21.2.0-*.whl && echo SC-OK || { echo SC-FAIL; tail -30 /tmp/a2b-build.log; }
echo "== d: install bindings + argon2-cffi =="
$P -m pip install -q --no-index --no-deps --find-links=$W $W/argon2_cffi_bindings-21.2.0-*.whl argon2-cffi==23.1.0 && echo SD-OK || echo SD-FAIL
echo "== e: minio =="
$P -m pip install -q --no-index --find-links=$W minio==7.2.20 && echo SE-OK || echo SE-FAIL
echo "== import check =="
$P -c "import minio, argon2; from Crypto.Cipher import ChaCha20_Poly1305; from argon2.low_level import Type; print('minio-import-ok', minio.__version__)" || echo IMPORT-FAIL
