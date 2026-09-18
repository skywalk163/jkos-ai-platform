"""分析 FreeBSD 磁盘空间使用"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

print("根目录大文件/目录:")
stdin, stdout, stderr = client.exec_command("du -sh /* 2>/dev/null | sort -h | tail -20")
for line in stdout:
    print(f"  {line.strip()}")

print("\n/usr 目录:")
stdin, stdout, stderr = client.exec_command("du -sh /usr/* 2>/dev/null | sort -h | tail -20")
for line in stdout:
    print(f"  {line.strip()}")

print("\n/var 目录:")
stdin, stdout, stderr = client.exec_command("du -sh /var/* 2>/dev/null | sort -h | tail -20")
for line in stdout:
    print(f"  {line.strip()}")

print("\n/home 目录:")
stdin, stdout, stderr = client.exec_command("du -sh /home/* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /usr/ports (ports tree):")
stdin, stdout, stderr = client.exec_command("du -sh /usr/ports 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /usr/src (kernel source):")
stdin, stdout, stderr = client.exec_command("du -sh /usr/src 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /usr/obj (build objects):")
stdin, stdout, stderr = client.exec_command("du -sh /usr/obj 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 Python 安装:")
stdin, stdout, stderr = client.exec_command("du -sh /usr/local/lib/python3* 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 pkg 数据库:")
stdin, stdout, stderr = client.exec_command("du -sh /var/db/pkg 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
