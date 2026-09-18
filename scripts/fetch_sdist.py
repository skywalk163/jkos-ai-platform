"""从 PyPI JSON API 直接下载 sdist（绕开 pip 的本地构建卡顿）
用法: python fetch_sdist.py pkg1==ver1 pkg2 ...
"""
import json
import pathlib
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DEST = pathlib.Path(__file__).resolve().parents[2] / ".wheels82"
DEST.mkdir(exist_ok=True)

for spec in sys.argv[1:]:
    name, _, ver = spec.partition("==")
    api = f"https://pypi.org/pypi/{name}/{ver}/json" if ver else f"https://pypi.org/pypi/{name}/json"
    with urllib.request.urlopen(api, timeout=30) as r:
        data = json.load(r)
    ver = data["info"]["version"]
    sdists = [u for u in data["urls"] if u["packagetype"] == "sdist"]
    if not sdists:
        print(f"[跳过] {name}: 无 sdist")
        continue
    u = sdists[0]
    dest = DEST / u["filename"]
    if dest.exists():
        print(f"[已有] {dest.name}")
        continue
    print(f"[下载] {dest.name} ({u['size']/1e3:.0f} KB) ...", flush=True)
    urllib.request.urlretrieve(u["url"], dest)
    print(f"[完成] {dest.name}")
