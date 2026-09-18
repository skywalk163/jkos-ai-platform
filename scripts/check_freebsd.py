"""检查 FreeBSD 上可用的 Python 包"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

# 搜索 Python 包
print("搜索 Python 包...")
stdin, stdout, stderr = client.exec_command("pkg search '^python' 2>/dev/null | head -20")
for line in stdout:
    print(f"  {line.strip()}")

print("\n搜索 py311 包...")
stdin, stdout, stderr = client.exec_command("pkg search '^py311' 2>/dev/null | head -20")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 python3 命令...")
stdin, stdout, stderr = client.exec_command("which python3 python3.11 python3.12 2>/dev/null || echo 'not found'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /var/dsh 目录权限...")
stdin, stdout, stderr = client.exec_command("ls -la /var/dsh/ 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /var/dsh/venv 目录...")
stdin, stdout, stderr = client.exec_command("ls -la /var/dsh/venv/ 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
