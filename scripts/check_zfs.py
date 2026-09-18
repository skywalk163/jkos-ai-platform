"""检查 ZFS 池状态和扩容选项"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

print("ZFS 池状态:")
stdin, stdout, stderr = client.exec_command("zpool status -v")
for line in stdout:
    print(f"  {line.strip()}")

print("\nZFS 池历史:")
stdin, stdout, stderr = client.exec_command("zpool history | tail -20")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查是否有未使用的磁盘:")
stdin, stdout, stderr = client.exec_command("camcontrol devlist 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查磁盘:")
stdin, stdout, stderr = client.exec_command("geom disk list 2>/dev/null | head -30")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /boot 目录:")
stdin, stdout, stderr = client.exec_command("du -sh /boot/* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /usr/local:")
stdin, stdout, stderr = client.exec_command("du -sh /usr/local/* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /usr/lib:")
stdin, stdout, stderr = client.exec_command("du -sh /usr/lib/* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /var/db:")
stdin, stdout, stderr = client.exec_command("du -sh /var/db/* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 Python 包:")
stdin, stdout, stderr = client.exec_command("du -sh /usr/local/lib/python3* 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /usr/local/lib:")
stdin, stdout, stderr = client.exec_command("du -sh /usr/local/lib/* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /usr/local/share:")
stdin, stdout, stderr = client.exec_command("du -sh /usr/local/share/* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
