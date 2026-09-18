"""打包本地 dsh-ai-platform 并上传到 FreeBSD 192.168.0.82:/data/dsh/code-jkos

排除 .git/__pycache__/缓存/数据库等，仅传源码+测试+文档。
用法: python scripts/push82.py
"""
import io
import os
import pathlib
import sys
import tarfile

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "dsh-ai-platform"
ENV_FILE = ROOT / ".env"

REMOTE_ROOT = "/data/dsh/code-jkos"
TAR_PATH = "/data/dsh/code-jkos.tar.gz"

EXCLUDE_DIRS = {".git", ".opencode", "__pycache__", ".pytest_cache", ".ruff_cache",
                "htmlcov", "dist", "build", "node_modules", ".venv", ".mypy_cache"}
EXCLUDE_FILES = {".coverage", "coverage.xml", "pytest_report.html"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".db", ".log", ".sqlite")


def load_env() -> dict:
    env = {}
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def build_tar() -> int:
    buf = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(SRC):
            dirnames[:] = [d for d in dirnames
                           if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
            for fn in filenames:
                if fn in EXCLUDE_FILES or fn.endswith(EXCLUDE_SUFFIX) or fn.endswith(".egg-info"):
                    continue
                full = pathlib.Path(dirpath) / fn
                arc = pathlib.Path("dsh-ai-platform") / full.relative_to(SRC)
                tar.add(full, arcname=str(arc), recursive=False)
                count += 1
    data = buf.getvalue()
    out = ROOT / "dsh-ai-platform.tar.gz"
    out.write_bytes(data)
    print(f"[打包] {count} 个文件 -> {out.name} ({len(data)/1e6:.1f} MB)")
    return count


def main() -> int:
    env = load_env()
    host, user, pwd = env["82SSH_HOST"], env["SSH_USER_AI"], env["SSH_PASS_AI"]

    build_tar()
    tar_bytes = (ROOT / "dsh-ai-platform.tar.gz").read_bytes()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=pwd, timeout=20,
                   allow_agent=False, look_for_keys=False)
    try:
        sftp = client.open_sftp()
        with sftp.open(TAR_PATH, "wb") as rf:
            rf.write(tar_bytes)
        print(f"[上传] {TAR_PATH} ({len(tar_bytes)/1e6:.1f} MB)")

        cmd = (
            # 保留远端 .git：d1 工作流用例依赖 git HEAD（git diff <空树> HEAD）
            f"rm -rf {REMOTE_ROOT}.git-keep && "
            f"if [ -d {REMOTE_ROOT}/.git ]; then mv {REMOTE_ROOT}/.git {REMOTE_ROOT}.git-keep; fi && "
            f"rm -rf {REMOTE_ROOT} && mkdir -p {REMOTE_ROOT} && "
            f"tar -xzf {TAR_PATH} -C {REMOTE_ROOT} --strip-components=1 && "
            f"if [ -d {REMOTE_ROOT}.git-keep ]; then mv {REMOTE_ROOT}.git-keep {REMOTE_ROOT}/.git; fi && "
            f"rm -f {TAR_PATH} && find {REMOTE_ROOT} -name __pycache__ -type d "
            f"-exec rm -rf {{}} + 2>/dev/null; "
            f"ls {REMOTE_ROOT} | head -20 && "
            f"echo '--- 文件数:' $(find {REMOTE_ROOT} -type f | wc -l)"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=120)
        print(stdout.read().decode("utf-8", errors="replace"))
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if err:
            print(f"[stderr] {err}", file=sys.stderr)
        code = stdout.channel.recv_exit_status()
        (ROOT / "dsh-ai-platform.tar.gz").unlink(missing_ok=True)
        return code
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
