"""M0 任务 0.2 部署脚本：上传工作流引擎（workflow/*）+ 修复补丁到 FreeBSD 并验收

用法: python scripts/deploy_m02.py
步骤:
  1. 连接 192.168.0.82
  2. SFTP 上传 workflow 模块与 db/cli 修复到 /data/dsh/code
  3. 服务器端运行 m0_selftest.py（29 个测试）
  4. CLI 验收链: hello 完整执行 → 崩溃续跑 → 审批同意/驳回
"""
import os
import sys

import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"
CODE = "/data/dsh/code"
VENV = "/data/dsh/venv/bin/python"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (本地相对路径, 服务器绝对路径) —— 0.2 新增/修改文件
FILES = [
    # 0.2 新增: 工作流引擎
    ("dsh_core/workflow/__init__.py",  f"{CODE}/dsh_core/workflow/__init__.py"),
    ("dsh_core/workflow/base.py",      f"{CODE}/dsh_core/workflow/base.py"),
    ("dsh_core/workflow/nodes.py",     f"{CODE}/dsh_core/workflow/nodes.py"),
    ("dsh_core/workflow/engine.py",    f"{CODE}/dsh_core/workflow/engine.py"),
    # 0.2 修改: db 层（autocommit 修复 + 常量导出 + list 反序列化）
    ("dsh_core/db/__init__.py",        f"{CODE}/dsh_core/db/__init__.py"),
    ("dsh_core/db/connection.py",      f"{CODE}/dsh_core/db/connection.py"),
    ("dsh_core/db/repos.py",           f"{CODE}/dsh_core/db/repos.py"),
    # 0.2 修改: CLI（去重 + workflow 命令）
    ("dsh_core/cli.py",                f"{CODE}/dsh_core/cli.py"),
    # 测试与自检
    ("tests/test_db.py",               f"{CODE}/tests/test_db.py"),
    ("tests/test_workflow.py",         f"{CODE}/tests/test_workflow.py"),
    ("scripts/m0_selftest.py",         f"{CODE}/scripts/m0_selftest.py"),
]

ENV = f"cd {CODE} && PYTHONPATH={CODE} DSH_DB_PATH=/var/dsh/dsh.db"
CLI = f"{ENV} {VENV} dsh_core/cli.py"


def run(client, cmd, timeout=90):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return rc, out, err


def main():
    print(f"[1] 连接 {HOST} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=15)
    print("[1] 连接成功")

    # 目录准备
    rc, out, err = run(client, f"mkdir -p {CODE}/dsh_core/workflow {CODE}/tests {CODE}/scripts")
    assert rc == 0, f"mkdir 失败: {err}"

    # 上传
    print("[2] SFTP 上传 0.2 模块 ...")
    sftp = client.open_sftp()
    for local_rel, remote in FILES:
        local = os.path.join(ROOT, local_rel.replace("/", os.sep))
        sftp.put(local, remote)
        print(f"  -> {remote} ({os.path.getsize(local)} B)")
    sftp.close()
    print(f"[2] 共上传 {len(FILES)} 个文件")

    # 服务器端自检
    print("[3] 服务器端 m0_selftest ...")
    rc, out, err = run(client, f"{ENV} {VENV} scripts/m0_selftest.py", timeout=120)
    print(out)
    if err:
        print("[stderr]", err[-800:])
    if rc != 0:
        print("[3] 自检失败，终止部署")
        return 1

    # CLI 验收链
    print("[4] CLI 验收链 ...")
    checks = [
        ("hello 完整执行", f"{CLI} workflow run hello --tenant dev --user deploy"),
        ("实例列表", f"{CLI} workflow list --limit 3"),
    ]
    for label, cmd in checks:
        rc, out, err = run(client, cmd, timeout=60)
        print(f"  [{label}] rc={rc}")
        if out:
            print("    " + "\n    ".join(out.split("\n")[:6]))

    # 崩溃续跑：拿到崩溃实例 ID 后 resume
    rc, out, _ = run(client, f"{CLI} workflow run hello_crash --tenant dev 2>/dev/null", timeout=60)
    crash_id = next((l.split()[1] for l in out.split("\n") if l.startswith("实例")), "")
    print(f"  [崩溃演练] instance={crash_id}")
    rc, out, err = run(client, f"{CLI} workflow resume {crash_id} --user deploy", timeout=60)
    status_line = next((l.strip() for l in out.split("\n") if "状态" in l), "")
    print(f"  [崩溃续跑] rc={rc} {status_line}")

    # 审批同意
    rc, out, _ = run(client, f"{CLI} workflow run hello_approval --tenant dev 2>/dev/null", timeout=60)
    ap_id = next((l.split()[1] for l in out.split("\n") if l.startswith("实例")), "")
    rc, out, err = run(
        client,
        f"{CLI} workflow approve {ap_id} --by manager --reason '部署验收通过' 2>/dev/null",
        timeout=60,
    )
    status_line = next((l.strip() for l in out.split("\n") if "状态" in l), "")
    print(f"  [审批同意] rc={rc} instance={ap_id} {status_line}")

    # 审批驳回
    rc, out, _ = run(client, f"{CLI} workflow run hello_approval --tenant media 2>/dev/null", timeout=60)
    rj_id = next((l.split()[1] for l in out.split("\n") if l.startswith("实例")), "")
    rc, out, err = run(
        client,
        f"{CLI} workflow approve {rj_id} --reject --by manager --reason '验收演练驳回' 2>/dev/null",
        timeout=60,
    )
    status_line = next((l.strip() for l in out.split("\n") if "状态" in l), "")
    print(f"  [审批驳回] rc={rc} instance={rj_id} {status_line}")

    # MCP Server 健康检查（0.2 未改动 server.py，确认存量服务不受影响）
    print("[5] MCP Server 健康检查 ...")
    rc, out, err = run(client, "curl -s -m 5 http://127.0.0.1:3000/health || echo 'HEALTH-CHECK-FAILED'")
    print(f"  /health: {out}")
    rc, out, err = run(client, "pgrep -fl 'dsh_core.cli.py mcp' || echo 'NOT-RUNNING'")
    print(f"  process: {out}")

    client.close()
    print("\n[完成] M0 0.2 工作流引擎已部署至 192.168.0.82")
    return 0


if __name__ == "__main__":
    sys.exit(main())
