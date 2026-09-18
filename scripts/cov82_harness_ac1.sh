#!/bin/sh
# M16 智能中枢 - 0.82 验收：AC-1 真实 runtime 集成测试 + 双 venv 全量回归
#
# 凭据从 JKOS .env 读取并映射为 harness 所需的 DEEPSEEK_* （不回显、不落盘）。
# SDK 从锁定 checkout 安装（协议对齐 65e04a5a07）；FreeBSD 无 runtime wheel，
# 须经 JKOS_HARNESS_DSH_BIN 指向源码构建的 launcher。
cd /data/dsh/code-jkos || exit 1
PY=/data/dsh/venv-test312/bin/python

echo "== a: 加载凭据（映射 OPENAI_* → DEEPSEEK_*，不回显） =="
eval "$($PY - <<'PYEOF'
import pathlib, shlex
env = {}
for line in pathlib.Path("/data/dsh/code-jkos/.env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
print("export DEEPSEEK_API_KEY=" + shlex.quote(env.get("OPENAI_API_KEY", "")))
print("export DEEPSEEK_BASE_URL=" + shlex.quote(env.get("OPENAI_BASE_URL", "")))
PYEOF
)"
if [ -z "$DEEPSEEK_API_KEY" ]; then echo "凭据缺失，中止"; exit 1; fi
echo "凭据已加载（长度 $(printf '%s' "$DEEPSEEK_API_KEY" | wc -c) 字符）"

export DSH_HOME=/data/dsh/harness-home
export JKOS_HARNESS_DSH_BIN=/data/dsh/harness/dsh-jkos.sh
export JKOS_HARNESS_INTEGRATION=1
mkdir -p "$DSH_HOME"

echo "== b: AC-1 集成测试（真实 LLM + sidecar 闭环） =="
$PY -m pytest tests/test_harness_integration.py -q --no-header -p no:cacheprovider --no-cov 2>&1 | tail -12

echo "== c: 双 venv 全量回归 =="
/data/dsh/venv-test312/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2
/data/dsh/venv-test/bin/python -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2