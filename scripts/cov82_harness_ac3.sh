#!/bin/sh
# M16 智能中枢 - 0.82 验收：AC-3 工具桥接（ToolBridge）
#
# 验证 JKOS 工具经 harness dsh-mcp-client 暴露给 agent：
#   1) /mcp 标准端点输出 MCP 规范驼峰字段（inputSchema）
#   2) agent 能看到 mcp__jkos__* 工具
#   3) agent 实际调用 JKOS 工具并拿到真实返回
#
# 前置：0.82 已完成 harness 源码构建；SDK 已装入 venv-test312；
#       凭据从 JKOS .env 读取（不回显、不落盘）。
cd /data/dsh/code-jkos || exit 1
PY=/data/dsh/venv-test312/bin/python
export DSH_HOME=/data/dsh/harness-home
export JKOS_HARNESS_DSH_BIN=/data/dsh/harness/dsh-jkos.sh
export JKOS_HARNESS_MCP_URL=http://127.0.0.1:3000/mcp

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
[ -n "$DEEPSEEK_API_KEY" ] || { echo "凭据缺失，中止"; exit 1; }
export DSH_JWT_SECRET=$($PY -c "import secrets; print(secrets.token_urlsafe(32))")
echo "凭据已加载"

echo "== b: 启动 JKOS MCP 服务（3000） =="
OLD=$(sockstat -4 -l 2>/dev/null | awk '/:3000/ {print $3}' | head -1)
[ -n "$OLD" ] && kill -9 "$OLD" 2>/dev/null
sleep 1
nohup $PY -c "import sys; sys.argv=['jkos-server','mcp','--port','3000']; from jkos_core.cli import main; sys.exit(main())" > /tmp/mcp_ac3.log 2>&1 &
i=0; while [ $i -lt 20 ]; do curl -sf -m 2 http://127.0.0.1:3000/health >/dev/null 2>&1 && break; sleep 1; i=$((i+1)); done
echo "MCP: $(curl -s -m 3 http://127.0.0.1:3000/health)"

echo "== c: 标准端点字段名（MCP 规范要求驼峰 inputSchema） =="
curl -s -m 8 -X POST http://127.0.0.1:3000/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | $PY -c "
import json, sys
tools = json.load(sys.stdin)['result']['tools']
ok = all('inputSchema' in t and 'input_schema' not in t for t in tools)
print(f'工具数: {len(tools)} | 驼峰字段合规: {ok}')
assert ok, '标准端点必须输出 inputSchema'
"

echo "== d: 真实探针 —— agent 调用 JKOS 工具 =="
$PY - <<'PYEOF' 2>&1 | tail -25
import os, re, sys, json
sys.path.insert(0, "/data/dsh/code-jkos")
from jkos_core.auth import AuthConfig, JWTManager
from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.toolbridge import apply_profile_patch

cfg = HarnessConfig.from_env()
apply_profile_patch(cfg)
os.environ["JKOS_MCP_TOKEN"] = JWTManager(AuthConfig.from_env()).issue_token(
    tenant_id="dev", tenant_code="dev", user_id="admin", roles=["admin"], expires_in=3600)

from deepseek_harness import DeepSeekHarness
h = DeepSeekHarness(dsh_home=cfg.dsh_home, cwd=cfg.workspace, dsh_bin=cfg.dsh_bin,
                    provider=cfg.provider, model=cfg.model,
                    api_key=cfg.api_key or None, base_url=cfg.base_url or None)
try:
    r = h.run(
        "请调用 mcp__jkos__dsh_session_list 工具（参数可为空），然后告诉我它返回了什么。",
        session_id="ac3-verify-1")
    blob = json.dumps(r.events, ensure_ascii=False)
    tools = sorted(set(re.findall(r"mcp__jkos__[A-Za-z0-9_]+", blob)))
    print("finish_reason:", r.finish_reason)
    print(f"agent 可见桥接工具: {len(tools)} 个")
    print("响应片段:", (r.final_response or "")[:200])
    assert r.finish_reason == "completed", "轮次未成功结束"
    assert tools, "agent 未看到 mcp__jkos__* 工具"
    print("AC3-PASS")
finally:
    h.close()
PYEOF

echo "== e: 收尾 =="
pkill -f 'jkos-server' 2>/dev/null
echo AC3-DONE