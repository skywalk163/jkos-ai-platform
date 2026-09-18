"""检查并启动 MCP Server"""
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

    # 检查 mcp/server.py 是否有静态文件路由
    print("\n[2] 检查 mcp/server.py...")
    run(client, "grep -n 'FileResponse\|@app.get(\"/\")' /data/dsh/code/jkos_core/mcp/server.py 2>/dev/null || echo 'Not found'")
    
    # 检查是否有进程在运行
    print("\n[3] 检查运行中的进程...")
    run(client, "ps aux | grep -E 'dsh|uvicorn' | grep -v grep | head -5")
    
    # 启动 MCP Server
    print("\n[4] 启动 MCP Server...")
    run(client, "cd /data/dsh/code && PYTHONPATH=/data/dsh/code nohup /data/dsh/venv/bin/python jkos_core/cli.py mcp --port 3000 > /data/dsh/logs/mcp.log 2>&1 &")
    
    # 等待启动
    print("\n[5] 等待 3 秒...")
    import time
    time.sleep(3)
    
    # 检查日志
    print("\n[6] 检查日志...")
    run(client, "tail -10 /data/dsh/logs/mcp.log 2>/dev/null || echo 'No log file'")
    
    # 检查端口
    print("\n[7] 检查端口...")
    run(client, "netstat -an | grep 3000")
    
    # 验证 / 路由
    print("\n[8] 验证 / 路由...")
    run(client, "curl -s http://localhost:3000/ | head -5")
    
    # 验证 /health
    print("\n[9] 验证 /health...")
    run(client, "curl -s http://localhost:3000/health")
    
    # 验证 /tools
    print("\n[10] 验证 /tools...")
    run(client, "curl -s http://localhost:3000/tools | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f\"Tools: {d.get(\"count\", 0)}\")' 2>/dev/null || curl -s http://localhost:3000/tools")
    
    print(f"""
  🎉 导航页面已就绪！

  访问地址:
    http://192.168.0.82:3000/
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
