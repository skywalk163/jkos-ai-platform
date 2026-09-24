# 参与开发（`jkos-dsh-plugin` 分支）

面向要改本插件的协作者。**使用说明看 [README.md](README.md)**，这边只讲怎么改、改在哪、怎么验。

## 仓库有两条线

| | `main`（FreeBSD 企业版） | `jkos-dsh-plugin`（本分支，开源插件线） |
|---|---|---|
| 定位 | 定制部署在 FreeBSD 的企业级 AI 服务操作系统 | 装上就能用的 deepseek-harness 插件 |
| 宿主关系 | JKOS 托管 harness（sidecar：拉起子进程、反代 Web UI、自建会话与事件流） | harness 托管 JKOS（插件提供面板与工具） |
| 接入层 | `jkos_core/harness/`（config / runtime / gateway / routes / proxy / toolbridge） | `plugin/`（bundle + Host/Client 两个半包） |
| 发布通道 | 内网 Gitea（`origin`） | 开源：`github` / `gitcode` |

两条线**共享 `jkos_core/` 的中台能力**：认证、多租户、工具注册表与可见性、配额、MCP 网关。中台改一处，两条线都受益；差异只在接入层。

## 改动该落在哪

| 要改的东西 | 落在 | 备注 |
|---|---|---|
| 中台能力（认证 / 工具 / 配额 / 多租户 / MCP 网关） | `jkos_core/` | 两线共享；动它要同时想 `main` 侧的托管路径还成不成立 |
| 面板、反代路由、bundle 清单、安装脚本 | `plugin/` | 本分支专有 |
| harness 托管（sidecar 生命周期、Web UI 反代、JKOS 内的会话/事件流） | `jkos_core/harness/` | `main` 专有；本分支保留未删，作为回退路径 |
| JKOS 控制台页面本身 | JKOS 侧服务 | 插件只反代并 iframe 它，不生成页面 |

### 同步靠 cherry-pick，不是 merge

两条线会随时间发散，所以：

- 改 `jkos_core/` 时**小步提交、一次一件事**，方便挑拣；
- 别顺手重排无关代码 —— 那会制造无意义的冲突；
- `README.md` / `CHANGELOG.md` / `CONTEXT.md` 是**与 `main` 共享的文件**，本分支尽量只追加、少改既有行；插件线自己的记录写进 [`plugin/CHANGELOG.md`](CHANGELOG.md)。

## 契约（改之前必读）

### 1. 零构建

`client.js` 是手写的**经典脚本**（顶层不能出现 `import` / `export` —— 宿主用 `<script src>` 加载），格式固定：

```js
window.__ModuleLoader__.load({
  id: '<包名>',
  factory(require) { /* ... */ return { inject, apply } },
})
```

- `id` **必须等于 `package.json` 的 `name`**，否则到达校验抛 `bundle <url> loaded without registering "<id>"`；
- `factory(require)` 里的 `require` 只能取平台模块表里的词（`react`、`react-dom`、`@deepseek-ai/cordis`、`.../dsh-client-store`、`.../dsh-client-ui-slots`、`.../dsh-client-ui-primitives`、`.../dsh-client-ui-dockkit`），取别的会抛 `require("...") missed the module table`；要 UI 基元用 `ui-primitives`，**不要**去引另一个 feature 插件的值；
- 没有构建步骤是**刻意的**：仓库外的 bundle 复刻不了 harness 内部的 `clientBundle` 构建预设，手写是这个位置唯一稳的形态。别为了"优雅"引入 TS/JSX/打包器。

### 2. 注册即 effect

所有注册都经 `ctx.effect()` / `ctx.on()` 的 disposer 管理，插件卸载时自动撤销。`ctx.slots.register()` 必须包在 `ctx.slots.inject(<slot>, …)` 里 —— 它等父级声明、声明消失时自动卸载、重新声明后再跑。

### 3. 密钥只走环境变量

任何文件里不得出现凭据 / token / 内网地址；需要密码学材料就经 `!!js` 从 `process.env` 读（见 `cordis.patch.yml` 的 `Authorization` 行）。本分支是**开源发布**的，写进文件就等于公开。

### 4. 三组必须对齐的常量

| 常量 | 位置 | 对齐要求 |
|---|---|---|
| 反代路径 | `index.js` 的 `ROUTE_PREFIX` ↔ `client.js` 的 `FRAME_SRC` | 改一处必须同时改另一处 |
| 面板 id | `client.js` 的 `PANEL_ID` | 同一个值既是 `sidebar.panellist` 条目的 `id`，也是 `main` keyed 条目的 `key` |
| 包名 | `package.json` 的 `name` ↔ `client.js` 的 `ModuleLoader` id | 必须一致 |

## patch 语义（`cordis.patch.yml`）

- 顶层**带 `id`** = **覆盖**既有行（按 id 合并；目标不存在会报 `entry "<id>" not found`）；
- **新增**行必须包在 `insert:` 列表里（外层不带 id）；不带 id 的顶层条目会被**静默忽略**；
- `!!js` 只允许用在 `config` 与条目的 `disabled` 上，其他元数据保持字面量。

## 开发循环

1. 改 `plugin/`。
2. **本地自检**（不需要 harness）：`node --check index.js client.js`、两个 `package.json` 能解析、`cordis.patch.yml` 的 YAML 结构能解析（解析器要放行 `!!js` 自定义标签）。
3. **上验证机复验**。`--dump-config` 是 boot-free 的：它不评估 `!!js`、也不做启动检查，**不能只靠它下结论**。
4. 提交（沿用仓库风格：`feat(plugin):` / `fix(plugin):` / `docs(plugin):` + 中文正文，正文写清「改了什么」与「怎么验证的」）。
5. 推送三个远端：`origin`（Gitea）/ `github` / `gitcode`。

### 复验清单

```sh
# 1) 装进沙箱 profile（不要动在用的 profile）
DSH_BIN=<harness 启动器> sh install.sh <沙箱 profile>

# 2) 组合是否生效：应打印 # == jkos-plugin 层与 jkos / mcp-jkos 两行
<dsh> --profile <沙箱> --dump-config | grep -A6 '^# == jkos-plugin'

# 3) 真的起得来（日志会打印 Web UI 的 ?token=）
<dsh> --profile <沙箱> &
curl -s -o /dev/null -w 'anon  /jkos/ = %{http_code}\n' http://127.0.0.1:3080/jkos/      # 期望 401
curl -s -c /tmp/ck -L "http://127.0.0.1:3080/?token=<日志里的 token>" > /dev/null
curl -s -b /tmp/ck -o /dev/null -w 'auth  /jkos/ = %{http_code} bytes=%{size_download}\n' \
  http://127.0.0.1:3080/jkos/                                                            # 期望 200，字节数等于上游控制台页面

# 4) client 半包进了 boot 图（说明面板有地方渲染）
curl -s -b /tmp/ck http://127.0.0.1:3080/ | grep -o '"id":"jkos-plugin"[^}]*}'

# 5) 工具桥：JKOS MCP 服务日志里应出现 harness 发来的 POST /mcp 200
```

**浏览器里的渲染没法在验证机上直接确认**（harness Web 只绑 loopback 且带 loopback 信任围栏）。要看视觉效果，先做 SSH 端口转发再打开页面：

```sh
ssh -L 3080:127.0.0.1:3080 <user>@<验证机>
```

验证环境（机器、SSH 跳板脚本、JKOS 代码目录与 venv）见 `main` 线的 `docs/ops/deploy.md` 与 `scripts/` 下的 82 系列脚本；凭据在仓库外的 `.env`，脚本读取，**绝不写入仓库或日志**。

## 踩过的坑（别重犯）

| 坑 | 现象 | 结论 |
|---|---|---|
| CLI 形式随版本变 | `--profile <name> is required` | 老版本只有 `dsh --profile <name>` 与硬编码的 `dsh web`，没有 `dsh <name>` 简写 |
| CLI 自带的默认 profile 模板不含 Web 界面 | 装完侧栏没入口 | 用 `--from-default-profile web` 初始化，或把 `dsh-web-app` 加进 `bundles` |
| `--from-default-profile` 对已存在的 profile | 报 `profile "X" already exists; omit --from-default-profile to use it` | 这是**设计行为**，按正常路径处理，别当失败刷屏 |
| 两层各 `insert` 同一个 id | 启动报冲突 | 插件只在 bundle 层 insert；用户层只允许按 id 覆盖 |
| 铸 token 的进程与 JKOS 服务密钥不一致 | 带了 token 仍 401 | 两者必须共享 `DSH_JWT_SECRET`（未配置时各生成临时密钥） |
| JKOS 控制台根路由在 MCP 服务上 | 面板 404 | 默认上游是控制台服务（0.82 上是 3000）；API 服务（8000）只有 `/api/v1/*` |
| CRLF | FreeBSD `/bin/sh` 读不了脚本 | 仓库 `.gitattributes` 已强制 `*.sh` 为 LF，别绕过 |
| 本地 Windows 跑不了 harness 源码 CLI | `pnpm dsh` 触发 lockfile 校验失败；`node apps/cli/src/bin.ts` 静默无输出（`import.meta.main` 需 node ≥24.2） | 验证放到验证机上做，本地只做语法/结构自检 |

## 已发布的位置

`jkos-dsh-plugin` 分支同时存在于三个远端：

```sh
git push origin jkos-dsh-plugin    # 内网 Gitea
git push github jkos-dsh-plugin    # 开源主通道
git push gitcode jkos-dsh-plugin
```