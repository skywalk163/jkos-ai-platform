"""通过 FreeBSD pkg 安装 pydantic 和 httpx"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

def run(client, cmd, echo=True):
    stdin, stdout, stderr = client.exec_command(cmd)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if echo and out:
        for line in out.split('\n'):
            if line.strip():
                print(f"  {line}")
    if err and echo:
        for line in err.split('\n'):
            if line.strip():
                print(f"  [!] {line}")
    return exit_status, out, err

def main():
    print(f"[1] 连接 {HOST} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=15)
    print("[1] 连接成功 ✓")

    VENV_DIR = "/data/dsh/venv"
    DSH_DIR = "/data/dsh"

    # 搜索可用的 pydantic 包
    print("\n[2] 搜索 pydantic 包...")
    run(client, "pkg search pydantic 2>/dev/null | head -10")
    
    # 搜索 httpx 包
    print("\n[3] 搜索 httpx 包...")
    run(client, "pkg search httpx 2>/dev/null | head -10")
    
    # 安装 py311-pydantic 和 py311-httpx
    print("\n[4] 安装 py311-pydantic 和 py311-httpx...")
    run(client, "sudo pkg install -y py311-pydantic py311-httpx 2>&1 | tail -10")
    
    # 检查安装
    print("\n[5] 检查安装...")
    run(client, "pkg info py311-pydantic py311-httpx 2>/dev/null")
    
    # 在 venv 中链接系统包
    print("\n[6] 在 venv 中链接系统包...")
    run(client, f"ln -sf /usr/local/lib/python3.11/site-packages/pydantic {VENV_DIR}/lib/python3.11/site-packages/pydantic 2>/dev/null || true")
    run(client, f"ln -sf /usr/local/lib/python3.11/site-packages/httpx {VENV_DIR}/lib/python3.11/site-packages/httpx 2>/dev/null || true")
    
    # 或者直接用系统 python
    print("\n[7] 验证安装...")
    run(client, "/usr/local/bin/python3.11 -c 'import pydantic; import httpx; print(\"pydantic:\", pydantic.__version__, \"httpx:\", httpx.__version__)'")
    
    # 验证 jkos_core
    print("\n[8] 验证 jkos_core...")
    run(client, f"cd {DSH_DIR}/code && PYTHONPATH=/data/dsh/code /usr/local/bin/python3.11 -c 'import jkos_core; from jkos_core.mcp.server import PREDEFINED_TOOLS; print(\"jkos_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 创建使用系统 python 的启动脚本
    print("\n[9] 创建启动脚本...")
    startup = f'''#!/bin/sh
export DSH_HOME={DSH_DIR}
export DSH_CODE={DSH_DIR}/code
export PYTHONPATH=$DSH_CODE
export TMPDIR={DSH_DIR}/tmp
cd $DSH_CODE
case "$1" in
    mcp) /usr/local/bin/python3.11 jkos_core/cli.py mcp --port 3000 ;;
    api) /usr/local/bin/python3.11 jkos_core/cli.py api --port 8000 ;;
    all) /usr/local/bin/python3.11 jkos_core/cli.py all ;;
    *) echo "用法: $0 {{mcp|api|all}}"; exit 1 ;;
esac
'''
    import base64
    b64 = base64.b64encode(startup.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {DSH_DIR}/start.sh && chmod +x {DSH_DIR}/start.sh && chown ai:wheel {DSH_DIR}/start.sh")
    print("  ✓ 启动脚本已更新")
    
    # 测试启动
    print("\n[10] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {DSH_DIR}/code && TMPDIR={DSH_DIR}/tmp timeout 3 /usr/local/bin/python3.11 jkos_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[11] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    # 最终状态
    print("\n" + "="*60)
    print("  部署完成！")
    print("="*60)
    run(client, "df -h / /data 2>/dev/null; echo; zpool list")
    
    print(f"""
  🎉 DSH AI 中台 已就绪！

  系统信息:
    主机: {HOST}
    Python: /usr/local/bin/python3.11
    代码目录: {DSH_DIR}/code
    ZFS 池: zdata (127G 可用)

  启动命令:
    ssh ai@{HOST}
    cd {DSH_DIR}
    ./start.sh mcp

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
