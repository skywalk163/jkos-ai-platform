#!/bin/sh
# M16 智能中枢 - 0.82 验收冒烟（AC-6 Web UI 反代 / AC-3 工具桥接）
cd /data/dsh/code-jkos || exit 1
PY=/data/dsh/venv-test312/bin/python
API_PORT=8002

export DSH_HOME=/data/dsh/harness-home
export JKOS_HARNESS_DSH_BIN=/data/dsh/harness/dsh-jkos.sh
export JKOS_HARNESS_ENABLED=true
mkdir -p "$DSH_HOME"

echo "== a: 启动 harness Web UI（127.0.0.1:3080） =="
pkill -f 'dsh-jkos.sh web' 2>/dev/null
pkill -f 'apps/cli/src/bin.ts web' 2>/dev/null
sleep 1
nohup /data/dsh/harness/dsh-jkos.sh web > /tmp/dsh_web.log 2>&1 &
i=0
while [ $i -lt 45 ]; do
  # 注意：Web UI 未带令牌时返回 401，不能用 curl -f（会把 401 当失败）
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:3080/ 2>/dev/null)
  if [ "$code" = "401" ] || [ "$code" = "200" ] || [ "$code" = "303" ]; then
    echo "WEB-UP after ${i}s (直接访问 http=$code)"
    break
  fi
  sleep 1; i=$((i+1))
done
if [ $i -ge 45 ]; then echo "Web UI 未就绪"; sed -e 's/token=[A-Za-z0-9_-]*/token=<REDACTED>/g' /tmp/dsh_web.log | tail -5; fi

echo "== a2: 提取 Web UI 令牌（不回显完整值） =="
UI_TOKEN=$(grep -oE 'token=[A-Za-z0-9_-]+' /tmp/dsh_web.log 2>/dev/null | head -1 | cut -d= -f2)
if [ -n "$UI_TOKEN" ]; then
  export JKOS_HARNESS_UI_TOKEN="$UI_TOKEN"
  echo "UI 令牌已提取（长度 $(printf '%s' "$UI_TOKEN" | wc -c) 字符，不回显）"
else
  echo "未提取到 UI 令牌；日志："; tail -5 /tmp/dsh_web.log
fi

echo "== b: 直连 Web UI（无令牌应 401） =="
curl -s -o /dev/null -w "direct-no-token-http=%{http_code}\n" -m 5 http://127.0.0.1:3080/

echo "== c: 启动 JKOS API（harness 启用，端口 $API_PORT） =="
pkill -f 'jkos-server' 2>/dev/null
pkill -f "sys.argv=\['jkos-server'" 2>/dev/null
sleep 1
nohup $PY -c "import sys; sys.argv=['jkos-server','api','--port','$API_PORT']; from jkos_core.cli import main; sys.exit(main())" > /tmp/dsh_api_harness.log 2>&1 &
i=0
while [ $i -lt 20 ]; do
  curl -sf -m 2 http://127.0.0.1:$API_PORT/api/v1/health > /dev/null 2>&1 && break
  sleep 1; i=$((i+1))
done
echo "--- harness 路由是否挂载 ---"
curl -s -m 5 http://127.0.0.1:$API_PORT/api/v1/harness/health
echo

echo "== d: AC-6 —— JKOS 反代 harness Web UI =="
curl -s -L -o /tmp/ui_body.html -w "proxy-http=%{http_code} size=%{size_download}\n" -m 20 "http://127.0.0.1:$API_PORT/harness/ui/"
echo "--- 页面首 120 字符 ---"
head -c 120 /tmp/ui_body.html
echo
echo "--- base 标签改写（相对资源解析基准） ---"
grep -oE '<base href="[^"]*"' /tmp/ui_body.html | head -2
echo "--- 主资源可达性（经代理取 JS） ---"
ASSET=$(grep -oE 'src="\./assets/[^"]+\.js"' /tmp/ui_body.html | head -1 | sed -e 's/src="\.\///' -e 's/"//')
if [ -n "$ASSET" ]; then
  curl -s -o /dev/null -w "asset-http=%{http_code} size=%{size_download} ($ASSET)\n" -m 20 \
    "http://127.0.0.1:$API_PORT/harness/ui/$ASSET"
else
  echo "(未找到主 JS 资源引用)"
fi

echo "== e: 收尾 =="
pkill -f 'jkos-server' 2>/dev/null
pkill -f 'dsh-jkos.sh web' 2>/dev/null
pkill -f 'apps/cli/src/bin.ts web' 2>/dev/null
sleep 1
echo "HARNESS-SMOKE-DONE"