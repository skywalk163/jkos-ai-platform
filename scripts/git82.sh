echo "== git availability =="
which git && git --version || { echo NO-GIT-BINARY; exit 0; }
echo "== git init in code-jkos =="
cd /data/dsh/code-jkos
git init -q 2>/dev/null || true
git config user.email test@jkos.local
git config user.name jkos-test
git add -A
git commit -qm "baseline: test fixture repo" || echo "commit-empty-or-done"
git log --oneline | head -2
echo HEAD=$(git rev-parse --short HEAD)
