P=/data/dsh/venv-test/bin/python
echo "== 1. minio install =="
if $P -m pip install --no-cache-dir --retries 10 --timeout 30 -q minio==7.2.20; then
  echo MINIO-FULL-OK
else
  echo "full install failed -> fallback --no-deps"
  $P -m pip install --no-cache-dir --retries 10 --timeout 30 -q --no-deps minio==7.2.20 && echo MINIO-NODEPS-OK
fi
echo "== minio import check =="
$P -c 'import minio; print("minio-import-ok", minio.__version__)' || echo MINIO-IMPORT-FAIL
echo "== 2. git init in code-jkos =="
if which git >/dev/null 2>&1; then
  echo git-found: $(git --version)
  cd /data/dsh/code-jkos
  git init -q 2>/dev/null || true
  git config user.email test@jkos.local
  git config user.name jkos-test
  git add -A
  git commit -qm "baseline: test fixture repo" || echo "commit-empty-or-done"
  git log --oneline | head -2
else
  echo NO-GIT-BINARY
fi
