"""查找大文件并清理"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

print("查找大于 10M 的文件:")
stdin, stdout, stderr = client.exec_command("find / -type f -size +10M 2>/dev/null | xargs ls -lh 2>/dev/null | sort -k5 -hr | head -20")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /var/log 大文件:")
stdin, stdout, stderr = client.exec_command("ls -lh /var/log/* 2>/dev/null | awk '{print $5, $9}' | sort -hr | head -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /home 目录大文件:")
stdin, stdout, stderr = client.exec_command("find /home -type f -size +1M 2>/dev/null -exec ls -lh {} \\; 2>/dev/null | sort -k5 -hr | head -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n检查 /root 目录:")
stdin, stdout, stderr = client.exec_command("ls -lh /root 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理 /var/log 日志:")
stdin, stdout, stderr = client.exec_command("truncate -s 0 /var/log/* 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理 /var/tmp:")
stdin, stdout, stderr = client.exec_command("rm -rf /var/tmp/* 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理 /tmp:")
stdin, stdout, stderr = client.exec_command("rm -rf /tmp/* 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理后磁盘空间:")
stdin, stdout, stderr = client.exec_command("df -h /")
for line in stdout:
    print(f"  {line.strip()}")

print("\nZFS 池状态:")
stdin, stdout, stderr = client.exec_command("zpool list")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
