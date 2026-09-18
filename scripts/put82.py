"""把本地目录(默认 .wheels82)通过 SFTP 上传到 82:/data/dsh/wheels/
用法: python scripts/put82.py [local_dir]
"""
import pathlib
import sys

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCAL = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".wheels82"
REMOTE_DIR = "/data/dsh/wheels"

env = {}
for raw in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(env["82SSH_HOST"], username=env["SSH_USER_AI"], password=env["SSH_PASS_AI"],
               timeout=20, allow_agent=False, look_for_keys=False)
try:
    client.exec_command(f"mkdir -p {REMOTE_DIR}")
    sftp = client.open_sftp()
    for f in sorted(LOCAL.iterdir()):
        if f.is_file():
            dest = f"{REMOTE_DIR}/{f.name}"
            sftp.put(str(f), dest)
            print(f"[up] {f.name} ({f.stat().st_size/1e3:.0f} KB)")
    _, stdout, _ = client.exec_command(f"ls -la {REMOTE_DIR} | tail -n +2 | wc -l")
    print(f"[remote] {REMOTE_DIR} 文件数: {stdout.read().decode().strip()}")
finally:
    client.close()
