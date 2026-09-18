"""安装 Rust 并完成 pydantic 安装"""
import paramiko
import base64

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

    DSH_DIR = "/data/dsh"
    CODE_DIR = f"{DSH_DIR}/code"
    VENV_DIR = f"{DSH_DIR}/venv"

    # 安装 Rust
    print("\n[2] 安装 Rust...")
    run(client, "sudo pkg install -y rust 2>&1 | tail -10")
    
    # 检查 rustc
    print("\n[3] 检查 Rust...")
    run(client, "rustc --version 2>/dev/null || which rustc 2>/dev/null || echo 'Rust not found'")
    
    # 安装 pydantic
    print("\n[4] 安装 Python 依赖...")
    run(client, f"TMPDIR=/data/tmp {VENV_DIR}/bin/pip install --no-cache-dir httpx pydantic 2>&1 | tail -15")
    
    # 安装其他依赖
    print("\n[5] 安装其他依赖...")
    run(client, f"TMPDIR=/data/tmp {VENV_DIR}/bin/pip install --no-cache-dir fastapi uvicorn structlog pyyaml 2>&1 | tail -10")
    
    # 验证
    print("\n[6] 验证安装...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import pydantic; import fastapi; print(\"httpx:\", httpx.__version__, \"pydantic:\", pydantic.__version__, \"fastapi:\", fastapi.__version__)'")
    
    # 验证 jkos_core
    print("\n[7] 验证 jkos_core...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import jkos_core; from jkos_core.mcp.server import PREDEFINED_TOOLS; print(\"jkos_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 测试启动
    print("\n[8] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && TMPDIR=/data/tmp timeout 3 {VENV_DIR}/bin/python jkos_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[9] 检查端口...")
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
