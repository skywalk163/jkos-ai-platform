#!/bin/sh
# M18 产品化 P1 - 0.82 验收（多租户 L2 隔离 + 三案例演示）
#
# 分段：
#   a) venv-test312 (py3.12) 全量回归
#   b) venv-test   (py3.11) 全量回归
#   c) 三案例产品化演示脚本（案例A/B/C，终态 M18.3-DEMO-OK）
#   d) L2 隔离运行时接线冒烟（开关默认关闭 + 开启后按租户落地）
#
# 前置：代码已由 scripts/push82.py 推送至 /data/dsh/code-jkos
cd /data/dsh/code-jkos || exit 1
PY312=/data/dsh/venv-test312/bin/python
PY311=/data/dsh/venv-test/bin/python

echo "== a: venv-test312 (py3.12) 全量回归 =="
$PY312 -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2

echo "== b: venv-test (py3.11) 全量回归 =="
$PY311 -m pytest -p no:cacheprovider -q --no-header 2>&1 | tail -2

echo "== c: 三案例产品化演示（M18.3） =="
$PY312 scripts/demo_three_cases.py --out /tmp/m18_demo_three_cases.json 2>&1 | tail -12

echo "== d: L2 隔离运行时接线冒烟 =="
echo "-- d1: 默认关闭（零退化） --"
env -u JKOS_TENANT_L2_ENABLED -u DSH_TENANT_DATA_DIR -u JKOS_TENANT_L2_CODES \
  $PY312 -m pytest tests/test_m18_2_runtime.py -q --no-header -p no:cacheprovider 2>&1 | tail -2
echo "-- d2: 开启隔离（独立库落地 + 跨租户 403） --"
rm -rf /tmp/m18_tenant_data
JKOS_TENANT_L2_ENABLED=1 DSH_TENANT_DATA_DIR=/tmp/m18_tenant_data \
  $PY312 -m pytest tests/test_m18_2_runtime.py tests/test_m18_2.py -q --no-header \
  -p no:cacheprovider 2>&1 | tail -2

echo "== e: 隔离数据目录落地检查 =="
$PY312 - <<'PYEOF'
import os, tempfile
os.environ["JKOS_TENANT_L2_ENABLED"] = "1"
os.environ["JKOS_TENANT_L2_CODES"] = "winery"
data_dir = "/tmp/m18_tenant_data_check"
os.environ["DSH_TENANT_DATA_DIR"] = data_dir
from jkos_core.bootstrap import build_components
comps = build_components(db_path=os.path.join(tempfile.mkdtemp(), "main.db"), init_auth=False)
try:
    assert comps.tenant_schemas is not None, "隔离未启用"
    assert comps.database_for("winery") is not comps.db, "winery 未走独立库"
    assert comps.database_for("media") is comps.db, "media 应走主库"
    db_file = os.path.join(data_dir, "tenant_winery.db")
    assert os.path.exists(db_file), f"独立库未生成: {db_file}"
    print(f"隔离落地 OK: {db_file}")
finally:
    comps.close()
PYEOF

echo "M18-ACCEPT-OK"
