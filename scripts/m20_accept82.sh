#!/bin/sh
# M20 自举第二期 - 0.82 验收（D3 自举闭环 + harness 融合 + 旧称收口）
#
# 分段：
#   a) venv-test312 (py3.12) 全量回归
#   b) venv-test   (py3.11) 全量回归
#   c) tests/test_selfboot/ 独立成册（D3 执行器 / 四段闭环 / MCP 工具）
#   d) D3 闭环演示：默认 LLM 路由模式 + --no-llm 离线复跑（证明确定性降级）
#   e) harness 融合验收：MCP 端点带 token 可见 13 个工具（含两条 dsh_optimize_*）、
#      匿名被拒（严格模式），且真实调用两条工具成功
#   f) 旧称残留扫描
#
# 任一关键分段失败即 exit 1（不会打出 M20-ACCEPT-OK）。
# 前置：代码已由 scripts/push82.py 推送至 /data/dsh/code-jkos
cd /data/dsh/code-jkos || exit 1
PY312=/data/dsh/venv-test312/bin/python
PY311=/data/dsh/venv-test/bin/python
# 注意：JKOS_MCP_REQUIRE_AUTH 只能在段 e 启动 MCP 服务时按进程设置。
# 若在此处 export，全量回归会带着严格鉴权跑 —— 匿名 MCP 用例（/tools、/mcp
# 不带 token）会大面积 401 失败（实测 a 段 37 项失败全由此引起）。

# 全量回归断言：必须"0 failed"且"有 passed"，否则整个验收立即失败。
# 注意不能只看 tail 的结尾（不能有 failed/error 字样出现在汇总行之前就放过）。
assert_regression() {
  label="$1"; out="$2"
  echo "$out" | grep -qE '[0-9]+ passed' || { echo "$label: 未见 passed 汇总"; return 1; }
  if echo "$out" | grep -qE '[1-9][0-9]* (failed|error)'; then
    echo "$label: 存在失败用例"
    return 1
  fi
  return 0
}

echo "== a: venv-test312 (py3.12) 全量回归 =="
A_OUT=$($PY312 -m pytest -p no:cacheprovider -q --no-header -rf 2>&1)
echo "$A_OUT" | tail -3
assert_regression "A(py3.12)" "$A_OUT" || { echo "A-FAIL"; exit 1; }

echo "== b: venv-test (py3.11) 全量回归 =="
B_OUT=$($PY311 -m pytest -p no:cacheprovider -q --no-header -rf 2>&1)
echo "$B_OUT" | tail -3
assert_regression "B(py3.11)" "$B_OUT" || { echo "B-FAIL"; exit 1; }

echo "== c: M20 自举闭环独立测试册 =="
C_OUT=$($PY312 -m pytest tests/test_selfboot -q --no-header -p no:cacheprovider -rf 2>&1)
echo "$C_OUT" | tail -3
assert_regression "C(selfboot)" "$C_OUT" || { echo "C-FAIL"; exit 1; }

echo "== d: D3 闭环演示（默认 LLM 路由模式） =="
D_OUT=$($PY312 scripts/d3_selfboot.py --out /tmp/m20_demo_loop.json 2>&1)
echo "$D_OUT" | tail -18
echo "$D_OUT" | grep -q 'M20.3-DEMO-OK' || { echo "D-DEMO-FAIL"; exit 1; }

echo "== d2: D3 闭环演示（--no-llm 离线确定性复跑） =="
D2_OUT=$($PY312 scripts/d3_selfboot.py --no-llm --out /tmp/m20_demo_loop_nollm.json 2>&1)
echo "$D2_OUT" | tail -10
echo "$D2_OUT" | grep -q 'M20.3-DEMO-OK' || { echo "D2-DEMO-FAIL"; exit 1; }

echo "== e: harness 融合验收（MCP 端点 + 真实工具调用） =="
# e1: 先固定密钥，再铸造访问 token（与 M16 AC-3 同法：密钥必须在父 shell 导出，
#     否则铸造端与服务端的 DSH_JWT_SECRET 各自随机、token 必然验签失败）
export DSH_JWT_SECRET=$($PY312 -c "import secrets; print(secrets.token_urlsafe(32))")
JKOS_MCP_TOKEN=$($PY312 - <<'PYEOF'
import sys
sys.path.insert(0, "/data/dsh/code-jkos")
from jkos_core.auth import AuthConfig, JWTManager
print(JWTManager(AuthConfig.from_env()).issue_token(
    tenant_id="dev", tenant_code="dev", user_id="admin", roles=["admin"], expires_in=3600))
PYEOF
)
export JKOS_MCP_TOKEN
[ -n "$JKOS_MCP_TOKEN" ] || { echo "token 铸造失败，中止"; exit 1; }
echo "token 长度 $(printf '%s' "$JKOS_MCP_TOKEN" | wc -c) 字符"

# e2: 启动 JKOS MCP 服务（3000）；自举数据目录指向 /tmp，不写代码目录旁
OLD=$(sockstat -4 -l 2>/dev/null | awk '/:3000/ {print $3}' | head -1)
[ -n "$OLD" ] && kill -9 "$OLD" 2>/dev/null
sleep 1
rm -rf /tmp/m20_selfboot_data
JKOS_MCP_REQUIRE_AUTH=true DSH_SELFBOOT_DATA_DIR=/tmp/m20_selfboot_data \
  nohup $PY312 -c "import sys; sys.argv=['jkos-server','mcp','--port','3000']; from jkos_core.cli import main; sys.exit(main())" > /tmp/mcp_m20.log 2>&1 &
i=0; while [ $i -lt 20 ]; do curl -sf -m 2 http://127.0.0.1:3000/health >/dev/null 2>&1 && break; sleep 1; i=$((i+1)); done
echo "MCP: $(curl -s -m 3 http://127.0.0.1:3000/health)"

# e3+e4: 匿名 401、带 token 工具清单与驼峰合规、两条 M20 工具的真实调用
E_OUT=$($PY312 - <<'PYEOF'
import json, os, urllib.error, urllib.request

TOKEN = os.environ["JKOS_MCP_TOKEN"]
BASE = "http://127.0.0.1:3000"
TASK = "为 JKOS selfboot 自举目标函数生成单元测试并产出覆盖率报告"
CALL_TIMEOUT = 180


def post(path, payload, *, token=None, timeout=20):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.load(resp)


# e3-a: 严格鉴权下匿名必须 401
try:
    status, _ = post("/mcp", {"jsonrpc": "2.0", "id": 0, "method": "tools/list"})
except urllib.error.HTTPError as exc:
    status = exc.code
print(f"匿名 tools/list -> HTTP {status}（严格模式应为 401）")
assert status == 401, "严格鉴权模式下匿名未被拒绝"

# e3-b: 带 token 的工具清单（驼峰合规 + M20 工具存在）
_, body = post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=TOKEN)
tools = body["result"]["tools"]
names = [t["name"] for t in tools]
camel = all("inputSchema" in t and "input_schema" not in t for t in tools)
print(f"带 token 工具数: {len(tools)} | 驼峰字段合规: {camel}")
print("M20 新增工具:", [n for n in names if n.startswith("dsh_optimize")])
assert camel, "标准端点必须输出 inputSchema"
for name in ("dsh_optimize_execute", "dsh_optimize_stats"):
    assert name in names, f"缺少 M20 工具 {name}"


# e4: 真实调用（走完整闭环，真跑 pytest 子进程）
def call(name, args, req_id):
    _, payload = post("/mcp", {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                               "params": {"name": name, "arguments": args}},
                      token=TOKEN, timeout=CALL_TIMEOUT)
    return json.loads(payload["result"]["content"][0]["text"])


stats = call("dsh_optimize_stats", {"limit": 2}, 2)
print("dsh_optimize_stats -> 模板库:", stats["template_count"],
      "缓存:", stats["cached_count"])

first = call("dsh_optimize_execute", {"task": TASK}, 3)
print(f"dsh_optimize_execute -> success={first['success']} "
      f"hit={first['template_hit']} source={first['source']} "
      f"省 {first['savings']['token_saved_ratio'] * 100:.2f}%")
assert first["success"] and first["template_hit"], "闭环未成立"
assert first["savings"]["token_saved_ratio"] > 0.9, "Token 节省不足 90%"

second = call("dsh_optimize_execute", {"task": TASK}, 4)
print(f"再次调用 -> source={second['source']} 复用模板={second['template_id']}")
assert second["source"] == "template", "第二次应命中模板"
print("E-PASS")
PYEOF
)
echo "$E_OUT"
echo "$E_OUT" | grep -q 'E-PASS' || { echo "E-FAIL"; exit 1; }

echo "== e5: 收尾 =="
pkill -f 'jkos-server' 2>/dev/null
sleep 1

echo "== f: M20 旧称与陈旧信息残留扫描 =="
echo "-- f1: 代码层 + 活跃文档（README / CONTEXT），沿革说明行（原名/更名前）除外 --"
if grep -rEn 'DSH AI 中台|dsh_core|dsh-server|dsh-plugin' \
     README.md CONTEXT.md jkos_core tenants server.py pyproject.toml locales 2>/dev/null \
     | grep -v '原名' | grep -v '更名前'; then
  echo "有残留!"
  exit 1
fi
echo "AC2-CLEAN"

echo "-- f2: CHANGELOG 历史条目提及数（历史记录，按 M15「历史文档称谓不变」口径保留） --"
grep -cE 'dsh_core|dsh-server|dsh-plugin|DSH AI 中台' CHANGELOG.md 2>/dev/null || echo 0

echo "M20-ACCEPT-OK"