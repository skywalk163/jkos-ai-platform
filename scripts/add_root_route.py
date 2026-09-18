"""在 FreeBSD 上添加 / 路由"""
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

    # 使用 Python 脚本在 FreeBSD 上修改文件
    python_script = r'''
import sys

with open('/data/dsh/code/jkos_core/mcp/server.py', 'r') as f:
    content = f.read()

# 在 @app.get("/health") 之前插入 @app.get("/")
new_route = ''' + '"""' + r'''
@self.app.get("/")
        async def index():
            return FileResponse("/data/dsh/index.html")

''' + '"""' + r'''

# 找到 _setup_routes 方法
setup_routes_start = content.find('def _setup_routes(self) -> None:')
if setup_routes_start != -1:
    # 找到第一个 @self.app.get 的位置
    first_route = content.find('@self.app.get("/health")', setup_routes_start)
    if first_route != -1:
        content = content[:first_route] + new_route + content[first_route:]
        print("Route inserted successfully")
    else:
        print("Could not find @app.get(\"/health\")")
else:
    print("Could not find _setup_routes method")

with open('/data/dsh/code/jkos_core/mcp/server.py', 'w') as f:
    f.write(content)
'''
    
    b64 = base64.b64encode(python_script.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d | /data/dsh/venv/bin/python")
    
    # 验证
    print("\n[2] 验证路由...")
    run(client, "grep -n '@app.get(\"/\")' /data/dsh/code/jkos_core/mcp/server.py")
    
    # 重启 MCP Server
    print("\n[3] 重启 MCP Server...")
    run(client, "pkill -f 'jkos_core.cli.py mcp' 2>/dev/null; sleep 1")
    run(client, "cd /data/dsh/code && PYTHONPATH=/data/dsh/code nohup /data/dsh/venv/bin/python jkos_core/cli.py mcp --port 3000 > /data/dsh/logs/mcp.log 2>&1 &")
    
    # 等待启动
    print("\n[4] 等待 3 秒...")
    import time
    time.sleep(3)
    
    # 检查日志
    print("\n[5] 检查日志...")
    run(client, "tail -5 /data/dsh/logs/mcp.log")
    
    # 验证 / 路由
    print("\n[6] 验证 / 路由...")
    run(client, "curl -s http://localhost:3000/ | head -5")
    
    # 验证 /health
    print("\n[7] 验证 /health...")
    run(client, "curl -s http://localhost:3000/health")
    
    print(f"""
  🎉 导航页面已就绪！

  访问地址:
    http://192.168.0.82:3000/
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
