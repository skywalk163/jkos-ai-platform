#!/bin/sh
cd /data/dsh/code-jkos || exit 1
PY=/data/dsh/venv-test312/bin/python

echo "== a: 清理旧进程，启动 API(8001) =="
pkill -f 'dsh-server' 2>/dev/null; sleep 1
nohup $PY -c "import sys; sys.argv=['dsh-server','api','--port','8001']; from dsh_core.cli import main; sys.exit(main())" > /tmp/dsh_api312.log 2>&1 &
echo "API_PID=$!"

echo "== b: 等待 /health 就绪 =="
i=0
while [ $i -lt 15 ]; do
  if curl -sf -m 2 http://127.0.0.1:8001/api/v1/health > /tmp/health.json 2>/dev/null; then
    echo "HEALTH-UP (尝试 $i 次)"
    cat /tmp/health.json
    echo
    break
  fi
  sleep 1
  i=$((i+1))
done
[ $i -ge 15 ] && { echo "API 启动失败，日志："; tail -20 /tmp/dsh_api312.log; exit 1; }

echo "== c: openapi 路由统计 =="
curl -sf -m 5 http://127.0.0.1:8001/openapi.json | $PY -c "import json,sys; d=json.load(sys.stdin); print('paths:', len(d['paths']), '| title:', d['info']['title'], '| version:', d['info']['version'])"

echo "== d: 签发 JWT 并调用业务接口 =="
TOKEN=$($PY -c "import sys; sys.argv=['dsh-server','token','--tenant','dev','--user','admin']; from dsh_core.cli import main; sys.exit(main())" 2>/dev/null | grep -Eo 'eyJ[A-Za-z0-9._-]+' | head -1)
echo "TOKEN 前 30 字符: $(printf '%s' "$TOKEN" | cut -c1-30)..."
curl -sf -m 15 -X POST http://127.0.0.1:8001/api/v1/text/generate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"用一句话介绍 FreeBSD"}' | head -c 400
echo

echo "== e: 启动 MCP(3001) 并测 SSE =="
nohup $PY -c "import sys; sys.argv=['dsh-server','mcp','--port','3001']; from dsh_core.cli import main; sys.exit(main())" > /tmp/dsh_mcp312.log 2>&1 &
i=0
while [ $i -lt 15 ]; do
  curl -sf -m 2 http://127.0.0.1:3001/health > /dev/null 2>&1 && break
  sleep 1; i=$((i+1))
done
echo "--- SSE 流(前 5 行, 最多 4 秒) ---"
curl -sN -m 4 http://127.0.0.1:3001/sse 2>/dev/null | head -5
echo "--- MCP /health ---"
curl -sf -m 3 http://127.0.0.1:3001/health; echo

echo "== f: 收尾：停服务，看日志尾部 =="
pkill -f 'dsh-server' 2>/dev/null; sleep 1
echo "--- API 日志尾部 ---"
tail -5 /tmp/dsh_api312.log
echo "--- MCP 日志尾部 ---"
tail -5 /tmp/dsh_mcp312.log
echo "INTEGRATION-DONE"
