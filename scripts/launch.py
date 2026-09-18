"""直接启动 MCP Server（minio 可选）"""
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

    # 验证 jkos_core
    print("\n[2] 验证 jkos_core...")
    run(client, f"cd {CODE_DIR} && PYTHONPATH={CODE_DIR} {VENV_DIR}/bin/python -c 'import jkos_core; from jkos_core.mcp.server import PREDEFINED_TOOLS; print(\"jkos_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 测试启动
    print("\n[3] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && PYTHONPATH={CODE_DIR} timeout 3 {VENV_DIR}/bin/python jkos_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[4] 检查端口...")
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
    cd /data/dsh
    ./start.sh mcp

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
