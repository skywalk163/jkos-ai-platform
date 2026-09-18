"""确认 dumate 反馈的问题"""
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

    # 测试 /tools/call 返回
    print("\n[2] 测试 dsh_text_generate 工具...")
    run(client, 'curl -s -X POST http://localhost:3000/tools/call -H "Content-Type: application/json" -d \'{"name":"dsh_text_generate","arguments":{"prompt":"测试","model":"deepseek","max_tokens":100}}\'')
    
    # 测试 /mcp 端点
    print("\n[3] 测试 /mcp 端点...")
    run(client, "curl -s http://localhost:3000/mcp")
    
    # 测试 /sse 端点
    print("\n[4] 测试 /sse 端点...")
    run(client, "curl -s http://localhost:3000/sse")
    
    # 测试 /api/v1/* 端点
    print("\n[5] 测试 /api/v1/health...")
    run(client, "curl -s http://localhost:3000/api/v1/health")
    
    # 检查 _execute_tool 方法
    print("\n[6] 检查 _execute_tool 方法...")
    run(client, "grep -A 10 'async def _execute_tool' /data/dsh/code/jkos_core/mcp/server.py")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
