"""检查 mcp/server.py 路由"""
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

    # 检查 mcp/server.py 的路由
    print("\n[2] 检查路由...")
    run(client, "grep -n '@app.get' /data/dsh/code/jkos_core/mcp/server.py | head -10")
    
    # 检查 index.html 是否存在
    print("\n[3] 检查 index.html...")
    run(client, "ls -la /data/dsh/index.html")
    
    # 检查 FileResponse 导入
    print("\n[4] 检查导入...")
    run(client, "head -20 /data/dsh/code/jkos_core/mcp/server.py | grep -E 'import|from'")
    
    # 检查 / 路由的代码
    print("\n[5] 检查 / 路由代码...")
    run(client, "sed -n '14,25p' /data/dsh/code/jkos_core/mcp/server.py")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
