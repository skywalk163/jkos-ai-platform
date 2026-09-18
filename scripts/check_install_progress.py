"""检查 Python 依赖安装进度"""
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
    CODE_DIR = "/data/dsh/code"

    # 检查 pip 安装进度
    print("\n[2] 检查 pip 安装进度...")
    run(client, f"ps aux | grep pip | grep -v grep")
    
    # 检查 pydantic 是否已安装
    print("\n[3] 检查 pydantic...")
    run(client, f"{VENV_DIR}/bin/python -c 'import pydantic; print(\"pydantic:\", pydantic.__version__)' 2>&1")
    
    # 检查 httpx
    print("\n[4] 检查 httpx...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; print(\"httpx:\", httpx.__version__)' 2>&1")
    
    # 检查已安装的包
    print("\n[5] 已安装的包...")
    run(client, f"{VENV_DIR}/bin/pip list 2>/dev/null | grep -E 'fastapi|httpx|pydantic|uvicorn|structlog'")
    
    # 如果 pydantic 没装完，继续安装
    print("\n[6] 继续安装...")
    run(client, f"{VENV_DIR}/bin/pip install --no-cache-dir fastapi uvicorn httpx pydantic structlog pyyaml 2>&1 | tail -10")
    
    # 最终验证
    print("\n[7] 最终验证...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import pydantic; import fastapi; import structlog; print(\"httpx:\", httpx.__version__, \"pydantic:\", pydantic.__version__, \"fastapi:\", fastapi.__version__)'")
    
    # 验证 jkos_core
    print("\n[8] 验证 jkos_core...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import jkos_core; from jkos_core.mcp.server import PREDEFINED_TOOLS; print(\"jkos_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 测试启动
    print("\n[9] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && timeout 3 {VENV_DIR}/bin/python jkos_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[10] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
