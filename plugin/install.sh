#!/bin/sh
# 把本插件装进一个 deepseek-harness profile。
#
# 用法:
#   sh install.sh [profile 名]        # profile 名默认 jkos
#
# 环境变量:
#   DSH_BIN   dsh 可执行文件路径。PATH 里没有 dsh 时用它，
#             例如源码启动器 /data/dsh/harness/dsh-jkos.sh。
#   DSH_HOME  harness 的 home 目录。只有在既没有 dsh 也没有 DSH_BIN、
#             退化为手工搭建 profile 时才需要（此时用它定位 profiles 目录）。
#
# 行为:
#   1) 有 dsh 可用 -> 交给官方 CLI：初始化 profile、写依赖、建 node_modules 链接；
#   2) 没有 dsh    -> 按 examples/profile/ 模板手工搭，并把模板里的占位路径
#                     自动替换为插件实际路径。
set -eu

PROFILE="${1:-jkos}"
PLUGIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ ! -f "$PLUGIN_DIR/package.json" ]; then
  echo "错误：$PLUGIN_DIR 下没有 package.json，请在插件目录内运行本脚本。" >&2
  exit 1
fi

# 优先官方 CLI；没有就退回 DSH_BIN 指定的可执行文件
DSH=""
if command -v dsh >/dev/null 2>&1; then
  DSH="dsh"
elif [ -n "${DSH_BIN:-}" ]; then
  DSH="$DSH_BIN"
fi

if [ -n "$DSH" ]; then
  echo "== 用 $DSH 安装到 profile：$PROFILE =="
  # profile 不存在时先从 web 模板初始化：CLI 默认模板只含 dsh-base，
  # 没有 Web 界面 bundle，面板就无处渲染。已存在的 profile 会被 CLI 拒绝
  # （"already exists; omit --from-default-profile"），那正是期望结果，不算错。
  init_err=$("$DSH" --profile "$PROFILE" --from-default-profile web --dump-config 2>&1 >/dev/null || true)
  case "$init_err" in
    *"already exists"*) ;;
    ?*) printf '   提示：未能从 web 模板初始化，继续安装：\n%s\n' "$init_err" >&2 ;;
  esac
  "$DSH" plugin --profile "$PROFILE" add "$PLUGIN_DIR"

  # 用 CLI 自己的组合输出确认面板有地方渲染，而不是猜
  if "$DSH" --profile "$PROFILE" --dump-config 2>/dev/null | grep -q 'dsh-web-app'; then
    echo "   已确认 profile 含 Web 界面 bundle（dsh-web-app）"
  else
    echo "   警告：该 profile 的 bundles 里没有 dsh-web-app，Web 界面里不会出现 JKOS 面板。" >&2
    echo "         想让它出现：$DSH --profile $PROFILE --from-default-profile web --dump-config" >&2
    echo "         然后重跑本脚本；命令行/无界面用法不受影响。" >&2
  fi
else
  if [ -z "${DSH_HOME:-}" ]; then
    echo "错误：PATH 里没有 dsh、也没有设置 DSH_BIN，手工安装还需要 DSH_HOME。" >&2
    echo "      请 export DSH_BIN=<dsh 可执行文件> 或 export DSH_HOME=<harness home> 后重试。" >&2
    exit 1
  fi

  PROFILE_DIR="$DSH_HOME/profiles/$PROFILE"
  TEMPLATE_DIR="$PLUGIN_DIR/examples/profile"
  LINK="$PROFILE_DIR/node_modules/jkos-plugin"
  echo "== 没有 dsh，改为手工搭建 profile：$PROFILE_DIR =="

  mkdir -p "$PROFILE_DIR/node_modules"

  if [ -f "$PROFILE_DIR/package.json" ]; then
    echo "   profile 清单已存在，保留不动（要重建请先删掉 $PROFILE_DIR）"
  else
    # 占位路径替换：& 在 sed 替换串里有特殊含义，先转义
    PLUGIN_DIR_SED=$(printf '%s' "$PLUGIN_DIR" | sed 's/&/\\&/g')
    sed "s|link:/absolute/path/to/jkos-plugin|link:$PLUGIN_DIR_SED|" \
      "$TEMPLATE_DIR/package.json" > "$PROFILE_DIR/package.json"
    grep -q "$PLUGIN_DIR" "$PROFILE_DIR/package.json" || {
      echo "错误：模板占位路径替换失败，请手工把 $PROFILE_DIR/package.json 里的 link: 改成 $PLUGIN_DIR" >&2
      exit 1
    }
    echo "   已按模板写入 profile 清单，依赖指向 $PLUGIN_DIR"
  fi

  if [ ! -f "$PROFILE_DIR/cordis.patch.yml" ]; then
    cp "$TEMPLATE_DIR/cordis.patch.yml" "$PROFILE_DIR/cordis.patch.yml"
    echo "   已复制用户层覆盖模板"
  fi

  if [ -L "$LINK" ]; then
    rm -f "$LINK"
  elif [ -e "$LINK" ]; then
    echo "错误：$LINK 已存在且不是符号链接，请手工处理后再运行。" >&2
    exit 1
  fi
  ln -s "$PLUGIN_DIR" "$LINK"
  echo "   已建立 node_modules/jkos-plugin -> $PLUGIN_DIR"
fi

cat <<EOF

完成。启动前还有三件事：

1. 导出工具桥令牌（只经环境变量，不落盘）：
     export JKOS_MCP_TOKEN=<JKOS 签发的 token>
2. 启动 harness（本仓库实测的 CLI 形式）：
     dsh --profile $PROFILE
3. 若该 profile 的用户层 patch 里已经有 mcp-jkos 行（旧版 JKOS 自动生成过），
   先删掉它，否则两层各 insert 一个同名 id 会冲突：
     <DSH_HOME>/profiles/$PROFILE/cordis.patch.yml
     <DSH_HOME>/cordis.patch.yml

控制台或 MCP 端点不在本机 3000 时，用用户层覆盖改写，写法见
examples/profile/cordis.patch.yml 的注释。
EOF