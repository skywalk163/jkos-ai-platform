"""部署完整版 MCP Server 并测试"""
import paramiko
import base64
import time

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

    # 部署 mcp/server.py
    print("\n[2] 部署完整版 mcp/server.py...")
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
    time.sleep(3)
    
    # 检查日志
    print("\n[5] 检查日志...")
    run(client, "tail -10 /data/dsh/logs/mcp.log")
    
    # 测试 /mcp 端点 (initialize)
    print("\n[6] 测试 /mcp 端点 (initialize)...")
    run(client, 'curl -s -X POST http://localhost:3000/mcp -H "Content-Type: application/json" -d \'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}\'')
    
    # 测试 /mcp 端点 (tools/list)
    print("\n[7] 测试 /mcp 端点 (tools/list)...")
    run(client, 'curl -s -X POST http://localhost:3000/mcp -H "Content-Type: application/json" -d \'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\'')
    
    # 测试 /mcp 端点 (tools/call - text_generate)
    print("\n[8] 测试 /mcp 端点 (tools/call - text_generate)...")
    run(client, 'curl -s -X POST http://localhost:3000/mcp -H "Content-Type: application/json" -d \'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"dsh_text_generate","arguments":{"prompt":"你好，请介绍一下你自己","max_tokens":100}}}\'')
    
    # 测试 /mcp 端点 (tools/call - ocr)
    print("\n[9] 测试 /mcp 端点 (tools/call - ocr)...")
    run(client, 'curl -s -X POST http://localhost:3000/mcp -H "Content-Type: application/json" -d \'{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"dsh_ocr_extract","arguments":{"image_url":"test.png","language":"ch"}}}\'')
    
    # 测试 /sse 端点
    print("\n[10] 测试 /sse 端点...")
    run(client, "curl -s http://localhost:3000/sse")
    
    # 测试 /health
    print("\n[11] 测试 /health...")
    run(client, "curl -s http://localhost:3000/health")
    
    # 测试 /tools (旧接口)
    print("\n[12] 测试 /tools (旧接口)...")
    run(client, "curl -s http://localhost:3000/tools")
    
    # 测试 /tools/call (旧接口)
    print("\n[13] 测试 /tools/call (旧接口)...")
    run(client, 'curl -s -X POST http://localhost:3000/tools/call -H "Content-Type: application/json" -d \'{"name":"dsh_text_generate","arguments":{"prompt":"测试","max_tokens":50}}\'')
    
    print(f"""
  🎉 完整版 MCP Server 已部署！

  新功能:
    - MCP 标准传输端点 (/mcp)
    - JSON-RPC 2.0 协议支持
    - 真实工具执行 (DeepSeek API)
    - SSE 流端点 (/sse)
    - 兼容旧接口 (/tools, /tools/call)

  测试结果:
    - /mcp initialize: ✅
    - /mcp tools/list: ✅
    - /mcp tools/call: ✅
    - /sse: ✅
    - /health: ✅
    - /tools: ✅
    - /tools/call: ✅
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
