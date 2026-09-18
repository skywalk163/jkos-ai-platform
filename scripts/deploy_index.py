"""部署 index.html 并启动 HTTP 服务"""
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

    # 部署 index.html
    print("\n[2] 部署 index.html...")
    with open(r"G:\dswork\AI\dsh-ai-platform\index.html", "r", encoding="utf-8") as f:
        content = f.read()
    b64 = base64.b64encode(content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > /data/dsh/index.html && chown ai:wheel /data/dsh/index.html")
    print("  ✓ index.html 已部署")
    
    # 更新 MCP Server 添加静态文件服务
    print("\n[3] 更新 MCP Server 添加静态文件服务...")
    
    # 读取当前 mcp/server.py 并添加静态文件路由
    with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\mcp\server.py", "r", encoding="utf-8") as f:
        mcp_content = f.read()
    
    # 在 create_app 函数中添加静态文件路由
    if "@app.get(\"/\")" not in mcp_content:
        # 找到 create_app 函数
        lines = mcp_content.split('\n')
        new_lines = []
        for i, line in enumerate(lines):
            new_lines.append(line)
            if line.strip() == "@app.get(\"/health\")":
                # 在 /health 之前添加根路由
                new_lines.insert(i, "@app.get(\"/\")")
                new_lines.insert(i+1, "async def index():")
                new_lines.insert(i+2, "    return FileResponse(\"/data/dsh/index.html\")")
                new_lines.insert(i+3, "")
                break
        
        mcp_content = '\n'.join(new_lines)
        
        # 添加 FileResponse 导入
        mcp_content = mcp_content.replace(
            "from fastapi import FastAPI, HTTPException",
            "from fastapi import FastAPI, HTTPException\nfrom fastapi.responses import FileResponse"
        )
        
        # 写回文件
        b64 = base64.b64encode(mcp_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > /data/dsh/code/jkos_core/mcp/server.py && chown ai:wheel /data/dsh/code/jkos_core/mcp/server.py")
        print("  ✓ MCP Server 已更新")
    else:
        print("  静态文件路由已存在")
    
    # 重启 MCP Server
    print("\n[4] 重启 MCP Server...")
    run(client, "pkill -f 'jkos_core.cli.py mcp' 2>/dev/null; sleep 1")
    run(client, "cd /data/dsh && PYTHONPATH=/data/dsh/code nohup /data/dsh/venv/bin/python jkos_core/cli.py mcp --port 3000 > /data/dsh/logs/mcp.log 2>&1 &")
    
    # 等待启动
    print("\n[5] 等待 2 秒...")
    import time
    time.sleep(2)
    
    # 验证
    print("\n[6] 验证...")
    run(client, "curl -s http://localhost:3000/ | head -20")
    run(client, "curl -s http://localhost:3000/health")
    
    # 检查端口
    print("\n[7] 检查端口...")
    run(client, "netstat -an | grep 3000")
    
    print(f"""
  🎉 导航页面已部署！

  访问地址:
    http://192.168.0.82:3000/

  功能:
    - 系统概览
    - MCP 工具列表
    - API 端点文档
    - 快速操作
    - 架构图
    - 技术栈信息
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
