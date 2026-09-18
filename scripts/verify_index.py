"""验证导航页面"""
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

    # 检查进程
    print("\n[2] 检查进程...")
    run(client, "ps aux | grep dsh | grep -v grep | head -5")
    
    # 检查日志
    print("\n[3] 检查日志...")
    run(client, "tail -10 /data/dsh/logs/mcp.log 2>/dev/null || echo 'No log file'")
    
    # 验证 / 路由
    print("\n[4] 验证 / 路由...")
    run(client, "curl -s http://localhost:3000/ | head -10")
    
    # 验证 /health
    print("\n[5] 验证 /health...")
    run(client, "curl -s http://localhost:3000/health")
    
    # 检查端口
    print("\n[6] 检查端口...")
    run(client, "netstat -an | grep 3000")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
