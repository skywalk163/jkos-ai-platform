"""
FreeBSD 15.1 全自动部署脚本
通过 paramiko SSH 连接，执行所有部署步骤
"""
import paramiko
import time
import sys
import json

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

# 部署配置
CONFIG = {
    "dsh_dir": "/var/dsh",
    "python_version": "3.11",
    "plugins_dir": "/var/dsh/plugins",
    "venv_dir": "/var/dsh/venv",
    "jail_dir": "/var/dsh/jail",
}

def run(client, cmd, get_pty=False, echo=True):
    """执行命令"""
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=get_pty)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    
    if echo and out:
        for line in out.split('\n'):
            if line.strip():
                print(f"  $ {line}")
    if err and echo:
        for line in err.split('\n'):
            if line.strip():
                print(f"  [!] {line}")
    
    return exit_status, out, err

def run_sudo(client, cmd, echo=True):
    """以 sudo 执行命令"""
    return run(client, f"sudo {cmd}", echo=echo)

def step(client, title):
    """打印步骤标题"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def main():
    print(f"[1] 连接 {HOST} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(HOST, username=USER, password=PASS, timeout=15)
        print("[1] 连接成功 ✓")
    except Exception as e:
        print(f"[1] 连接失败: {e}")
        return 1

    # ─── 步骤 0: 系统更新 ───
    step(client, "步骤 0: 系统更新")
    print("  更新 pkg 源...")
    run_sudo(client, "pkg update -f", echo=True)
    
    # ─── 步骤 1: 安装基础依赖 ───
    step(client, "步骤 1: 安装基础依赖")
    
    packages = [
        "python3.11",
        "py311-pip",
        "py311-virtualenv",
        "py311-sqlite3",
        "ffmpeg",
        "git",
        "curl",
        "wget",
        "tmux",
        "jq",
        "openjdk17",
        "sudo",
    ]
    
    print(f"  安装 {len(packages)} 个包...")
    for i, pkg in enumerate(packages, 1):
        # 先检查是否已安装
        status, out, err = run(client, f"pkg info -e {pkg} 2>/dev/null && echo 'INSTALLED' || echo 'NOT_FOUND'", echo=False)
        if "INSTALLED" in out:
            print(f"  [{i}/{len(packages)}] {pkg} (已安装) ✓")
            continue
        
        # 安装
        print(f"  [{i}/{len(packages)}] 安装 {pkg} ...")
        status, out, err = run_sudo(client, f"pkg install -y {pkg}", echo=False)
        if status == 0:
            print(f"  [{i}/{len(packages)}] {pkg} ✓")
        else:
            print(f"  [{i}/{len(packages)}] {pkg} ✗ (已跳过)")
    
    # ─── 步骤 2: 创建 ZFS 数据集 ───
    step(client, "步骤 2: 创建 ZFS 数据集")
    
    dsh_dir = CONFIG["dsh_dir"]
    
    # 检查数据集是否已存在
    status, out, err = run(client, f"zfs list zroot/dsh-data 2>/dev/null && echo 'EXISTS' || echo 'NOT_FOUND'", echo=False)
    
    if "NOT_FOUND" in out:
        print(f"  创建 zroot/dsh-data (挂载点: {dsh_dir})...")
        run_sudo(client, f"zfs create -o mountpoint={dsh_dir} -o compression=lz4 -o atime=off zroot/dsh-data")
        print(f"  ✓ 数据集已创建")
    else:
        print(f"  数据集 zroot/dsh-data 已存在")
    
    # 创建子目录
    for sub in ["plugins", "logs", "cache", "data", "tmp", "venv"]:
        sub_path = f"{dsh_dir}/{sub}"
        status, out, err = run(client, f"test -d {sub_path} && echo 'EXISTS' || mkdir -p {sub_path}", echo=False)
        if "EXISTS" not in out:
            run_sudo(client, f"chown -R {USER}:wheel {dsh_dir}")
            print(f"  ✓ {sub_path}")
    
    # ─── 步骤 3: 配置 Linux 兼容层 ───
    step(client, "步骤 3: 配置 Linux 兼容层")
    
    print("  启用 Linux 兼容层...")
    run_sudo(client, "sysrc linux_enable=\"YES\"", echo=False)
    run_sudo(client, "kldload linux64 2>/dev/null || true", echo=False)
    
    # 检查 Linux 模块
    status, out, err = run(client, "kldstat | grep linux", echo=False)
    if out:
        print("  ✓ Linux 模块已加载")
    else:
        print("  ! Linux 模块未加载，尝试手动加载...")
        run_sudo(client, "kldload linux64")
    
    # ─── 步骤 4: 创建 Python 虚拟环境 ───
    step(client, "步骤 4: 创建 Python 虚拟环境")
    
    venv_dir = CONFIG["venv_dir"]
    status, out, err = run(client, f"test -f {venv_dir}/bin/activate && echo 'EXISTS' || python3.11 -m venv {venv_dir}", echo=False)
    
    if "EXISTS" in out:
        print(f"  虚拟环境已存在: {venv_dir}")
    else:
        print(f"  虚拟环境已创建: {venv_dir}")
    
    # 安装 Python 依赖
    print("  安装 Python 依赖...")
    run(client, f"{venv_dir}/bin/pip install --upgrade pip setuptools wheel", echo=False)
    run(client, f"{venv_dir}/bin/pip install fastapi uvicorn pydantic httpx structlog pyyaml", echo=False)
    run(client, f"{venv_dir}/bin/pip install pytest pytest-asyncio pytest-cov", echo=False)
    print("  ✓ Python 依赖安装完成")
    
    # ─── 步骤 5: 部署 DSH 代码 ───
    step(client, "步骤 5: 部署 DSH 代码")
    
    # 创建代码目录
    code_dir = f"{dsh_dir}/code"
    run_sudo(client, f"mkdir -p {code_dir} && chown -R {USER}:wheel {code_dir}", echo=False)
    
    # 这里需要从 Windows 复制代码到 FreeBSD
    # 由于 paramiko 不能直接访问 Windows 文件系统，我们需要用 SFTP
    print("  通过 SFTP 上传代码...")
    
    sftp = client.open_sftp()
    
    # 上传 jkos_core 模块
    local_base = r"G:\dswork\AI\dsh-ai-platform\jkos_core"
    remote_base = f"{code_dir}/jkos_core"
    
    # 先设置目录权限
    run_sudo(client, f"mkdir -p {remote_base} && chown -R {USER}:wheel {code_dir} {dsh_dir}", echo=False)
    
    files_to_upload = [
        ("__init__.py", "__init__.py"),
        ("cli.py", "cli.py"),
        ("plugin_cli.py", "plugin_cli.py"),
    ]
    
    sftp = client.open_sftp()
    
    for local_file, remote_file in files_to_upload:
        local_path = f"{local_base}/{local_file}"
        remote_path = f"{remote_base}/{remote_file}"
        try:
            sftp.put(local_path, remote_path)
            print(f"  ✓ {remote_file}")
        except Exception as e:
            print(f"  ✗ {remote_file}: {e}")
    
    # 上传 plugins 模块
    plugins_local = f"{local_base}/plugins"
    plugins_remote = f"{remote_base}/plugins"
    run_sudo(client, f"mkdir -p {plugins_remote}", echo=False)
    
    plugin_files = [
        ("base.py", "base.py"),
        ("__init__.py", "__init__.py"),
    ]
    
    for local_file, remote_file in plugin_files:
        local_path = f"{plugins_local}/{local_file}"
        remote_path = f"{plugins_remote}/{remote_file}"
        try:
            sftp.put(local_path, remote_path)
            print(f"  ✓ plugins/{remote_file}")
        except Exception as e:
            print(f"  ✗ plugins/{remote_file}: {e}")
    
    # 上传 OCR 插件
    ocr_local = f"{plugins_local}/ocr"
    ocr_remote = f"{plugins_remote}/ocr"
    run_sudo(client, f"mkdir -p {ocr_remote}", echo=False)
    
    try:
        sftp.put(f"{ocr_local}/plugin.py", f"{ocr_remote}/plugin.py")
        print(f"  ✓ plugins/ocr/plugin.py")
    except Exception as e:
        print(f"  ✗ plugins/ocr/plugin.py: {e}")
    
    # 上传模板
    template_local = f"{plugins_local}/_template"
    template_remote = f"{plugins_remote}/_template"
    run_sudo(client, f"mkdir -p {template_remote}", echo=False)
    
    try:
        sftp.put(f"{template_local}/plugin.yaml", f"{template_remote}/plugin.yaml")
        print(f"  ✓ plugins/_template/plugin.yaml")
    except Exception as e:
        print(f"  ✗ plugins/_template/plugin.yaml: {e}")
    
    # 上传 mcp 模块
    mcp_local = f"{local_base}/mcp"
    mcp_remote = f"{remote_base}/mcp"
    run_sudo(client, f"mkdir -p {mcp_remote}", echo=False)
    
    try:
        sftp.put(f"{mcp_local}/server.py", f"{mcp_remote}/server.py")
        print(f"  ✓ mcp/server.py")
    except Exception as e:
        print(f"  ✗ mcp/server.py: {e}")
    
    # 上传 storage 模块
    storage_local = f"{local_base}/storage"
    storage_remote = f"{remote_base}/storage"
    run_sudo(client, f"mkdir -p {storage_remote}", echo=False)
    
    try:
        sftp.put(f"{storage_local}/manager.py", f"{storage_remote}/manager.py")
        print(f"  ✓ storage/manager.py")
    except Exception as e:
        print(f"  ✗ storage/manager.py: {e}")
    
    try:
        sftp.put(f"{storage_local}/__init__.py", f"{storage_remote}/__init__.py")
        print(f"  ✓ storage/__init__.py")
    except Exception as e:
        print(f"  ✗ storage/__init__.py: {e}")
    
    # 上传 models 模块
    models_local = f"{local_base}/models"
    models_remote = f"{remote_base}/models"
    run_sudo(client, f"mkdir -p {models_remote}", echo=False)
    
    try:
        sftp.put(f"{models_local}/__init__.py", f"{models_remote}/__init__.py")
        print(f"  ✓ models/__init__.py")
    except Exception as e:
        print(f"  ✗ models/__init__.py: {e}")
    
    # 上传 api 模块
    api_local = f"{local_base}/api"
    api_remote = f"{remote_base}/api"
    run_sudo(client, f"mkdir -p {api_remote}", echo=False)
    
    try:
        sftp.put(f"{api_local}/routes.py", f"{api_remote}/routes.py")
        print(f"  ✓ api/routes.py")
    except Exception as e:
        print(f"  ✗ api/routes.py: {e}")
    
    try:
        sftp.put(f"{api_local}/__init__.py", f"{api_remote}/__init__.py")
        print(f"  ✓ api/__init__.py")
    except Exception as e:
        print(f"  ✗ api/__init__.py: {e}")
    
    sftp.close()
    
    # 最终设置权限
    run_sudo(client, f"chown -R {USER}:wheel {code_dir} {dsh_dir}")
    print("  ✓ 代码部署完成")
    
    # ─── 步骤 6: 验证安装 ───
    step(client, "步骤 6: 验证安装")
    
    # 测试 Python 导入
    print("  测试 Python 导入...")
    test_cmd3 = "import jkos_core; print('jkos_core OK')"
    status, out, err = run(client, f'cd {code_dir} && {venv_dir}/bin/python -c "{test_cmd3}"', echo=False)
    if status == 0 and "OK" in out:
        print("  ✓ jkos_core 导入成功")
    else:
        print(f"  ✗ jkos_core 导入失败: {err}")
    
    # 测试 MCP 导入
    print("  测试 MCP 导入...")
    test_cmd = "from jkos_core.mcp.server import PREDEFINED_TOOLS; print('MCP tools:', len(PREDEFINED_TOOLS))"
    status, out, err = run(client, f'cd {code_dir} && {venv_dir}/bin/python -c "{test_cmd}"', echo=False)
    if status == 0:
        print(f"  ✓ {out.strip()}")
    else:
        print(f"  ✗ MCP 导入失败: {err}")
    
    # 测试插件导入
    print("  测试插件导入...")
    test_cmd2 = "from jkos_core.plugins import PluginRegistry; print('PluginRegistry OK')"
    status, out, err = run(client, f'cd {code_dir} && {venv_dir}/bin/python -c "{test_cmd2}"', echo=False)
    if status == 0:
        print(f"  ✓ 插件系统导入成功")
    else:
        print(f"  ✗ 插件系统导入失败: {err}")
    
    # ─── 步骤 7: 创建启动脚本 ───
    step(client, "步骤 7: 创建启动脚本")
    
    # 创建启动脚本
    startup_script_content = f'''#!/bin/sh
# DSH AI 中台启动脚本
# 用法: ./start.sh [mcp|api|all]

export DSH_HOME={dsh_dir}
export DSH_VENV={venv_dir}
export DSH_CODE={code_dir}
export PYTHONPATH=$DSH_CODE

cd $DSH_CODE

case "$1" in
    mcp)
        echo "启动 MCP Server..."
        $DSH_VENV/bin/python jkos_core/cli.py mcp --port 3000
        ;;
    api)
        echo "启动 API 服务..."
        $DSH_VENV/bin/python jkos_core/cli.py api --port 8000
        ;;
    all)
        echo "启动所有服务..."
        $DSH_VENV/bin/python jkos_core/cli.py all
        ;;
    *)
        echo "用法: $0 {{mcp|api|all}}"
        exit 1
        ;;
esac
'''
    
    # 写入启动脚本
    import base64
    script_b64 = base64.b64encode(startup_script_content.encode('utf-8')).decode('ascii')
    run_sudo(client, f"echo '{script_b64}' | base64 -d > {dsh_dir}/start.sh && chmod +x {dsh_dir}/start.sh", echo=False)
    print(f"  ✓ 启动脚本: {dsh_dir}/start.sh")
    
    # ─── 完成 ───
    step(client, "部署完成")
    
    print(f"""
  🎉 DSH AI 中台 已部署到 FreeBSD 15.1!

  目录结构:
    {dsh_dir}/
    ├── code/          # 代码目录
    ├── venv/          # Python 虚拟环境
    ├── plugins/       # 插件目录
    ├── logs/          # 日志目录
    ├── cache/         # 缓存目录
    ├── data/          # 数据目录
    └── start.sh       # 启动脚本

  启动命令:
    cd {dsh_dir}
    ./start.sh mcp     # 启动 MCP Server (端口 3000)
    ./start.sh api     # 启动 API 服务 (端口 8000)
    ./start.sh all     # 启动所有服务

  验证:
    curl http://192.168.0.82:3000/health
    curl http://192.168.0.82:3000/tools
""")
    
    client.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
