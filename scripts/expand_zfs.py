"""彻底清理并扩容 ZFS 池"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

print("=" * 60)
print("第一步：彻底删除 debug 符号")
print("=" * 60)

# 检查 /usr/lib/debug
stdin, stdout, stderr = client.exec_command("du -sh /usr/lib/debug 2>/dev/null")
for line in stdout:
    print(f"  /usr/lib/debug 大小: {line.strip()}")

# 删除 /usr/lib/debug
print("  删除 /usr/lib/debug ...")
stdin, stdout, stderr = client.exec_command("rm -rf /usr/lib/debug 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

# 检查 /usr/lib/clang
stdin, stdout, stderr = client.exec_command("du -sh /usr/lib/clang 2>/dev/null")
for line in stdout:
    print(f"  /usr/lib/clang 大小: {line.strip()}")

# 删除 /usr/lib/clang (clang 编译器，非必需)
print("  删除 /usr/lib/clang ...")
stdin, stdout, stderr = client.exec_command("rm -rf /usr/lib/clang 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

# 检查 /usr/local/share/icu
stdin, stdout, stderr = client.exec_command("du -sh /usr/local/share/icu 2>/dev/null")
for line in stdout:
    print(f"  /usr/local/share/icu 大小: {line.strip()}")

# 删除 /usr/local/share/icu (ICU 数据，非必需)
print("  删除 /usr/local/share/icu ...")
stdin, stdout, stderr = client.exec_command("rm -rf /usr/local/share/icu 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("第二步：检查 ada1 磁盘")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("geom disk list ada1 2>/dev/null | head -10")
for line in stdout:
    print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("第三步：清理后磁盘空间")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("df -h /")
for line in stdout:
    print(f"  {line.strip()}")

stdin, stdout, stderr = client.exec_command("zpool list")
for line in stdout:
    print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("第四步：扩容 ZFS 池（添加 ada1）")
print("=" * 60)

# 先检查 ada1 是否有分区
stdin, stdout, stderr = client.exec_command("gpart show ada1 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

# 添加 ada1 到 zroot 池
print("  添加 ada1 到 zroot 池...")
stdin, stdout, stderr = client.exec_command("sudo zpool add zroot ada1 2>&1")
for line in stdout:
    print(f"  {line.strip()}")
for line in stderr:
    print(f"  [err] {line.strip()}")

print("\n" + "=" * 60)
print("第五步：扩容后磁盘空间")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("zpool list")
for line in stdout:
    print(f"  {line.strip()}")

stdin, stdout, stderr = client.exec_command("df -h /")
for line in stdout:
    print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("第六步：创建数据集用于 DSH")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("zfs create -o mountpoint=/var/dsh -o compression=lz4 -o atime=off zroot/dsh-data 2>/dev/null && echo 'done' || echo 'exists'")
for line in stdout:
    print(f"  {line.strip()}")

stdin, stdout, stderr = client.exec_command("zfs list | grep dsh")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
