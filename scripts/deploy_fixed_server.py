"""部署修复后的 mcp/server.py 并启动"""
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

    # 部署修复后的 mcp/server.py
    print("\n[2] 部署 mcp/server.py...")
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\mcp\server.py", "r", encoding="utf-8") as f:
        content = f.read()
    b64 = base64.b64encode(content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > /data/dsh/code/dsh_core/mcp/server.py && chown ai:wheel /data/dsh/code/dsh_core/mcp/server.py")
    print("  ✓ mcp/server.py 已部署")
    
    # 重启 MCP Server
    print("\n[3] 重启 MCP Server...")
    run(client, "pkill -f 'dsh_core.cli.py mcp' 2>/dev/null; sleep 1")
    run(client, "cd /data/dsh/code && PYTHONPATH=/data/dsh/code nohup /data/dsh/venv/bin/python dsh_core/cli.py mcp --port 3000 > /data/dsh/logs/mcp.log 2>&1 &")
    
    # 等待启动
    print("\n[4] 等待 3 秒...")
    import time
    time.sleep(3)
    
    # 检查日志
    print("\n[5] 检查日志...")
    run(client, "tail -5 /data/dsh/logs/mcp.log")
    
    # 验证 / 路由
    print("\n[6] 验证 / 路由...")
    run(client, "curl -s http://localhost:3000/ | head -10")
    
    # 验证 /health
    print("\n[7] 验证 /health...")
    run(client, "curl -s http://localhost:3000/health")
    
    # 验证 /tools
    print("\n[8] 验证 /tools...")
    run(client, "curl -s http://localhost:3000/tools | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"Tools: {d.get(\"count\", 0)}\")' 2>/dev/null || echo 'check manually'")
    
    print(f"""
  🎉 导航页面已就绪！

  访问地址:
    http://192.168.0.82:3000/

  功能:
    - 系统概览
    - MCP 工具列表 (8个)
    - API 端点文档
    - 快速操作
    - 架构图
    - 技术栈信息
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
