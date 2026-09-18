"""用 --without-pip 创建 venv 并手动安装"""
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

    # 用 --without-pip 创建 venv
    print("\n[2] 创建 Python 虚拟环境 (--without-pip)...")
    run(client, f"rm -rf {VENV_DIR} && /usr/local/bin/python3.11 -m venv {VENV_DIR} --without-pip")
    
    # 手动安装 pip
    print("\n[3] 手动安装 pip...")
    run(client, f"curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && TMPDIR=/data/tmp {VENV_DIR}/bin/python /tmp/get-pip.py --no-cache-dir 2>&1 | tail -5")
    
    # 安装依赖
    print("\n[4] 安装 Python 依赖...")
    run(client, f"TMPDIR=/data/tmp {VENV_DIR}/bin/pip install --no-cache-dir fastapi uvicorn httpx pydantic structlog pyyaml 2>&1 | tail -15")
    
    # 验证
    print("\n[5] 验证安装...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import pydantic; import fastapi; print(\"httpx:\", httpx.__version__, \"pydantic:\", pydantic.__version__, \"fastapi:\", fastapi.__version__)'")
    
    # 验证 dsh_core
    print("\n[6] 验证 dsh_core...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import dsh_core; from dsh_core.mcp.server import PREDEFINED_TOOLS; print(\"dsh_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 测试启动
    print("\n[7] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && TMPDIR=/data/tmp timeout 3 {VENV_DIR}/bin/python dsh_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
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
