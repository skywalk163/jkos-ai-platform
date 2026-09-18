"""M0 部署脚本：上传基础层（db/audit/llm/auth/bootstrap）到 FreeBSD 并验证

用法: python scripts/deploy_m0.py
步骤:
  1. 连接 192.168.0.82
  2. SFTP 上传 M0 模块到 /data/dsh/code
  3. 服务器端运行 m0_selftest.py（20 个测试）
  4. CLI 验证: db stats / token / llm ping
  5. 重启 MCP Server 并做 /health 检查
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

# (本地相对路径, 服务器绝对路径)
FILES = [
    # 轨道 A: 数据层 + 审计
    ("jkos_core/db/__init__.py",      f"{CODE}/jkos_core/db/__init__.py"),
    ("jkos_core/db/ulid.py",          f"{CODE}/jkos_core/db/ulid.py"),
    ("jkos_core/db/connection.py",    f"{CODE}/jkos_core/db/connection.py"),
    ("jkos_core/db/schema.py",        f"{CODE}/jkos_core/db/schema.py"),
    ("jkos_core/db/repos.py",         f"{CODE}/jkos_core/db/repos.py"),
    ("jkos_core/audit/__init__.py",   f"{CODE}/jkos_core/audit/__init__.py"),
    ("jkos_core/audit/logger.py",     f"{CODE}/jkos_core/audit/logger.py"),
    # 轨道 B: LLM 路由
    ("jkos_core/llm/__init__.py",     f"{CODE}/jkos_core/llm/__init__.py"),
    ("jkos_core/llm/base.py",         f"{CODE}/jkos_core/llm/base.py"),
    ("jkos_core/llm/deepseek.py",     f"{CODE}/jkos_core/llm/deepseek.py"),
    ("jkos_core/llm/simulated.py",    f"{CODE}/jkos_core/llm/simulated.py"),
    ("jkos_core/llm/router.py",       f"{CODE}/jkos_core/llm/router.py"),
    # 轨道 C: 租户上下文/认证
    ("jkos_core/auth/__init__.py",      f"{CODE}/jkos_core/auth/__init__.py"),
    ("jkos_core/auth/context.py",       f"{CODE}/jkos_core/auth/context.py"),
    ("jkos_core/auth/jwt.py",           f"{CODE}/jkos_core/auth/jwt.py"),
    ("jkos_core/auth/dependencies.py",  f"{CODE}/jkos_core/auth/dependencies.py"),
    # 装配层 + 入口
    ("jkos_core/bootstrap.py",        f"{CODE}/jkos_core/bootstrap.py"),
    ("jkos_core/cli.py",              f"{CODE}/jkos_core/cli.py"),
    ("jkos_core/mcp/server.py",       f"{CODE}/jkos_core/mcp/server.py"),
    # 测试与自检
    ("tests/test_db.py",             f"{CODE}/tests/test_db.py"),
    ("tests/test_llm.py",            f"{CODE}/tests/test_llm.py"),
    ("tests/test_auth.py",           f"{CODE}/tests/test_auth.py"),
    ("scripts/m0_selftest.py",       f"{CODE}/scripts/m0_selftest.py"),
]


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
    rc, out, err = run(client, (
        f"mkdir -p {CODE}/jkos_core/db {CODE}/jkos_core/audit {CODE}/jkos_core/llm"
        f" {CODE}/jkos_core/auth {CODE}/tests {CODE}/scripts /var/dsh /data/dsh/logs"
    ))
    assert rc == 0, f"mkdir 失败: {err}"

    # 上传
    print("[2] SFTP 上传 M0 模块 ...")
    sftp = client.open_sftp()
    for local_rel, remote in FILES:
        local = os.path.join(ROOT, local_rel.replace("/", os.sep))
        sftp.put(local, remote)
        print(f"  -> {remote} ({os.path.getsize(local)} B)")
    sftp.close()
    print(f"[2] 共上传 {len(FILES)} 个文件")

    # 服务器端自检
    print("[3] 服务器端 m0_selftest ...")
    rc, out, err = run(client, f"cd {CODE} && PYTHONPATH={CODE} {VENV} scripts/m0_selftest.py", timeout=120)
    print(out)
    if err:
        print("[stderr]", err)
    if rc != 0:
        print("[3] 自检失败，终止部署")
        return 1

    # CLI 验证
    print("[4] CLI 验证 ...")
    for label, cmd in [
        ("db migrate", f"cd {CODE} && PYTHONPATH={CODE} DSH_DB_PATH=/var/dsh/dsh.db {VENV} jkos_core/cli.py db migrate"),
        ("db stats",   f"cd {CODE} && PYTHONPATH={CODE} DSH_DB_PATH=/var/dsh/dsh.db {VENV} jkos_core/cli.py db stats"),
        ("token",      f"cd {CODE} && PYTHONPATH={CODE} DSH_DB_PATH=/var/dsh/dsh.db {VENV} jkos_core/cli.py token --tenant dev --days 1"),
        ("llm ping",   f"cd {CODE} && PYTHONPATH={CODE} DSH_DB_PATH=/var/dsh/dsh.db {VENV} jkos_core/cli.py llm"),
    ]:
        rc, out, err = run(client, cmd, timeout=60)
        print(f"  [{label}] rc={rc}")
        if out:
            print("    " + "\n    ".join(out.split("\n")[-8:]))
        if rc != 0 and err:
            print("    [err]", err[-500:])

    # 重启 MCP Server（沿用既有模式）
    print("[5] 重启 MCP Server ...")
    run(client, "pkill -f 'jkos_core.cli.py mcp' 2>/dev/null; sleep 1")
    rc, out, err = run(client, (
        f"cd {CODE} && PYTHONPATH={CODE} nohup {VENV} jkos_core/cli.py mcp --port 3000"
        " > /data/dsh/logs/mcp.log 2>&1 &"
    ))
    run(client, "sleep 3")
    rc, out, err = run(client, "curl -s -m 5 http://127.0.0.1:3000/health || echo 'HEALTH-CHECK-FAILED'")
    print(f"  /health: {out}")
    rc, out, err = run(client, "pgrep -fl 'jkos_core.cli.py mcp' || echo 'NOT-RUNNING'")
    print(f"  process: {out}")

    client.close()
    print("\n[完成] M0 基础层已部署至 192.168.0.82")
    return 0


if __name__ == "__main__":
    sys.exit(main())
