"""先安装不需要编译的包，然后等 pydantic 编译"""
import paramiko
import time

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
    CODE_DIR = "/data/dsh/code"

    # 先安装 httpx 和 fastapi（不需要编译）
    print("\n[2] 安装 httpx 和 fastapi...")
    run(client, f"{VENV_DIR}/bin/pip install --no-cache-dir httpx fastapi uvicorn structlog pyyaml 2>&1 | tail -5")
    
    # 验证
    print("\n[3] 验证安装...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import fastapi; import structlog; print(\"httpx:\", httpx.__version__, \"fastapi:\", fastapi.__version__)'")
    
    # 等待 pydantic 编译完成
    print("\n[4] 等待 pydantic 编译完成...")
    for i in range(30):
        status, out, err = run(client, f"{VENV_DIR}/bin/python -c 'import pydantic; print(\"pydantic:\", pydantic.__version__)' 2>&1", echo=False)
        if status == 0:
            print(f"  ✓ pydantic 安装成功: {out.strip()}")
            break
        print(f"  等待中... ({i+1}/30)")
        time.sleep(10)
    else:
        print("  ! pydantic 编译超时，尝试安装 pydantic v1...")
        run(client, f"{VENV_DIR}/bin/pip install --no-cache-dir 'pydantic<2.0' 2>&1 | tail -5")
    
    # 验证 dsh_core
    print("\n[5] 验证 dsh_core...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import dsh_core; from dsh_core.mcp.server import PREDEFINED_TOOLS; print(\"dsh_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 测试启动
    print("\n[6] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && timeout 3 {VENV_DIR}/bin/python dsh_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[7] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    # 最终状态
    print("\n" + "="*60)
    print("  部署完成！")
    print("="*60)
    run(client, "df -h / /data 2>/dev/null; echo; zpool list")
    
    print(f"""
  🎉 DSH AI 中台 已就绪！

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
