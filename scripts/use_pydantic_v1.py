"""杀掉 pip 进程，安装 pydantic v1"""
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

    # 杀掉 pip 和 rustc 进程
    print("\n[2] 杀掉 pip 和 rustc 进程...")
    run(client, "sudo pkill -9 -f 'pip install' 2>/dev/null; sudo pkill -9 rustc 2>/dev/null; echo 'done'")
    
    # 等待进程结束
    time.sleep(2)
    
    # 安装 pydantic v1（不需要 Rust）
    print("\n[3] 安装 pydantic v1...")
    run(client, f"{VENV_DIR}/bin/pip install --no-cache-dir 'pydantic<2.0' 2>&1 | tail -5")
    
    # 安装 fastapi 和 uvicorn
    print("\n[4] 安装 fastapi 和 uvicorn...")
    run(client, f"{VENV_DIR}/bin/pip install --no-cache-dir fastapi uvicorn structlog pyyaml 2>&1 | tail -5")
    
    # 验证
    print("\n[5] 验证安装...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import pydantic; import fastapi; import structlog; print(\"httpx:\", httpx.__version__, \"pydantic:\", pydantic.__version__, \"fastapi:\", fastapi.__version__)'")
    
    # 验证 dsh_core
    print("\n[6] 验证 dsh_core...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import dsh_core; from dsh_core.mcp.server import PREDEFINED_TOOLS; print(\"dsh_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 测试启动
    print("\n[7] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && timeout 3 {VENV_DIR}/bin/python dsh_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[8] 检查端口...")
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
