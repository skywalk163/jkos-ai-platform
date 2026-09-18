#!/bin/sh
# FreeBSD 15.0 环境初始化脚本
# 用途：为 DSH AI 中台准备生产级 FreeBSD 环境
# 用法：sh scripts/freebsd-init.sh [--mode dev|test|prod]
# 警告：此脚本不会 remount /usr 为 rw，所有可写数据存 /var/dsh

set -e

# ─── 颜色定义 ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ─── 参数解析 ───
MODE="dev"
for arg in "$@"; do
  case $arg in
    --mode=*) MODE="${arg#*=}" ;;
  esac
done

info "开始 FreeBSD 环境初始化 (模式: $MODE)"
info "当前用户: $(whoami)"

# ─── 0. 系统版本检查 ───
OSVERSION=$(freebsd-version -u | cut -d'-' -f1)
info "FreeBSD 版本: $OSVERSION"

case $OSVERSION in
  14.*)
    warn "检测到 FreeBSD $OSVERSION，建议升级到 15.0 以获得最佳兼容性"
    warn "执行: freebsd-update upgrade -r 15.0-RELEASE"
    ;;
  15.0)
    info "FreeBSD 15.0 检测通过 ✓"
    ;;
  *)
    error "不支持的 FreeBSD 版本: $OSVERSION"
    ;;
esac

# ─── 1. 系统基础配置 ───
info "1. 系统基础配置..."

# 启用 Linux 兼容层
sysrc linux_enable="YES" 2>/dev/null || warn "linux_enable 已设置"
kldload linux64 2>/dev/null || warn "linux64 模块加载失败（可能已加载）"

# 启用 ZFS 压缩
sysctl vfs.zfs.compress.lz4=1 2>/dev/null || true

# 设置时区（默认上海）
if [ ! -f /etc/localtime ]; then
  ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
  info "时区设置为 Asia/Shanghai"
fi

# ─── 2. ZFS 数据集创建 ───
info "2. 创建 ZFS 数据集..."

ZFS_POOL="zroot"
DSH_DATASET="${ZFS_POOL}/dsh-data"

# 检查数据集是否已存在
if zfs list "${DSH_DATASET}" >/dev/null 2>&1; then
  warn "数据集 ${DSH_DATASET} 已存在，跳过创建"
else
  zfs create -o mountpoint=/var/dsh -o compression=lz4 -o atime=off "${DSH_DATASET}"
  info "数据集 ${DSH_DATASET} 创建成功，挂载点 /var/dsh"
fi

# 创建子数据集
for sub in plugins logs cache data tmp; do
  SUB_DS="${DSH_DATASET}/${sub}"
  if ! zfs list "${SUB_DS}" >/dev/null 2>&1; then
    zfs create "${SUB_DS}"
    info "  创建 ${SUB_DS}"
  fi
done

# 设置目录权限
mkdir -p /var/dsh/{plugins,logs,cache,data,tmp}
chmod 755 /var/dsh
chmod 700 /var/dsh/logs
chmod 700 /var/dsh/data

# ─── 3. 宿主依赖安装 ───
info "3. 安装宿主依赖 (pkg 预编译包)..."

PKG_DEPS="
  ffmpeg
  py311-pip
  py311-virtualenv
  py311-sqlite3
  openjdk17
  git
  curl
  wget
  tmux
  htop
  jq
"

for pkg in $PKG_DEPS; do
  if pkg info -e "$pkg" >/dev/null 2>&1; then
    info "  $pkg 已安装 ✓"
  else
    info "  安装 $pkg..."
    pkg install -y "$pkg" || warn "  $pkg 安装失败"
  fi
done

# ─── 4. Python 虚拟环境 ───
info "4. 创建 Python 虚拟环境..."

VENV_DIR="/var/dsh/venv"
if [ ! -f "${VENV_DIR}/bin/activate" ]; then
  python3.11 -m venv "${VENV_DIR}"
  info "虚拟环境创建于 ${VENV_DIR}"
else
  info "虚拟环境已存在"
fi

# 激活并安装基础依赖
. "${VENV_DIR}/bin/activate"
pip install --upgrade pip setuptools wheel
pip install fastapi uvicorn pydantic asyncpg redis boto3 minio httpx structlog pyyaml

# ─── 5. Linux Jail 准备 ───
info "5. 准备 Linux Jail..."

JAIL_DIR="/var/dsh/jail"
JAIL_CONF="/etc/jail.d/dsh-linux.conf"

mkdir -p "${JAIL_DIR}"

# 创建 Jail 配置
cat > "${JAIL_CONF}" << 'JAIL_EOF'
dsh-linux {
  host.hostname = "dsh-linux";
  ip4.addr = "127.0.0.2";
  path = "/var/dsh/jail";
  mount.devfs;
  enforce_statfs = 2;
  allow.raw_sockets;
  allow.sysvipc;
  exec.start = "/bin/sh /etc/rc";
  exec.stop = "/bin/sh /etc/rc.shutdown";
  exec.clean;
  securelevel = 2;
}
JAIL_EOF

info "Jail 配置已写入 ${JAIL_CONF}"

# ─── 6. 目录结构验证 ───
info "6. 验证目录结构..."

cat << EOF

${GREEN}═══════════════════════════════════════════════════════════
  FreeBSD 环境初始化完成！
═══════════════════════════════════════════════════════════

  数据集:      ${DSH_DATASET}
  挂载点:      /var/dsh
  虚拟环境:    ${VENV_DIR}
  Jail 配置:   ${JAIL_CONF}

  目录结构:
  /var/dsh/
  ├── plugins/    # DSH 插件目录
  ├── logs/       # 日志目录
  ├── cache/      # 缓存目录
  ├── data/       # 数据目录
  ├── tmp/        # 临时文件
  └── venv/       # Python 虚拟环境

  下一步:
  1. 激活虚拟环境: source ${VENV_DIR}/bin/activate
  2. 启动 DSH 服务: dsh-server
  3. 配置 PostgreSQL: 见 docs/deploy.md
${NC}

EOF
