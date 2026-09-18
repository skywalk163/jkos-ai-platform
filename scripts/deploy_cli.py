"""部署更新后的 cli.py 并启动 MCP Server"""
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

    CODE_DIR = "/data/dsh/code"
    VENV_DIR = "/data/dsh/venv"

    # 部署更新后的 cli.py
    print("\n[2] 部署 cli.py...")
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\cli.py", "r", encoding="utf-8") as f:
        content = f.read()
    b64 = base64.b64encode(content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/cli.py && chown ai:wheel {CODE_DIR}/dsh_core/cli.py")
    print("  ✓ cli.py 已更新")
    
    # 验证 dsh_core
    print("\n[3] 验证 dsh_core...")
    run(client, f"cd {CODE_DIR} && PYTHONPATH={CODE_DIR} {VENV_DIR}/bin/python -c 'import dsh_core; from dsh_core.mcp.server import PREDEFINED_TOOLS; print(\"dsh_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 后台启动 MCP Server
    print("\n[4] 后台启动 MCP Server...")
    run(client, f"cd {CODE_DIR} && PYTHONPATH={CODE_DIR} nohup {VENV_DIR}/bin/python dsh_core/cli.py mcp --port 3000 > /data/dsh/logs/mcp.log 2>&1 &")
    
    # 等待启动
    print("\n[5] 等待 3 秒...")
    import time
    time.sleep(3)
    
    # 检查日志
    print("\n[6] 检查日志...")
    run(client, "tail -10 /data/dsh/logs/mcp.log 2>/dev/null || echo 'No log file'")
    
    # 检查端口
    print("\n[7] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    # 检查进程
    print("\n[8] 检查进程...")
    run(client, "ps aux | grep dsh | grep -v grep | head -5")
    
    # 最终状态
    print("\n" + "="*60)
    print("  部署完成！")
    print("="*60)
    run(client, "df -h / /data 2>/dev/null; echo; zpool list")
    
    print(f"""
  🎉 DSH AI 中台 已就绪！

  系统信息:
    主机: {HOST}
    MCP Server: 端口 3000
    日志: /data/dsh/logs/mcp.log

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools

  停止:
    pkill -f "dsh_core.cli.py mcp"
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
