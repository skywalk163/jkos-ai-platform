# Changelog — JKOS Plugin for deepseek-harness

> 本分支（`jkos-dsh-plugin`，开源插件线）自己的变更记录。格式与仓库根的 [CHANGELOG.md](../CHANGELOG.md) 一致。
> 中台本体（`jkos_core/`）的里程碑记录仍在仓库根那份，不在这里重复。

## [Unreleased]

### 新增（文档与协作者入口）
- **`plugin/CONTRIBUTING.md`**：两条线的定位与职责边界、改动落点、cherry-pick 同步约定、插件四条契约（零构建 / 注册即 effect / 密钥只走环境变量 / 三组必须对齐的常量）、patch 语义、开发循环与复验清单、踩坑清单
- **`plugin/CHANGELOG.md`**（本文件）：插件线独立的变更记录 —— 这样两线共享的根 CHANGELOG 只需极少改动，降低 cherry-pick 冲突面
- **`docs/adr/0002-jkos-as-harness-plugin.md`**：方向反转的决策记录（为什么从「JKOS 托管 harness」改为「JKOS 作 harness 插件」、边界、保留项、未决项）
- **README 补两节**：`自检（装之前先确认上游）` 给出三条 curl 判据与令牌签发方式；`排错` 用「症状 → 原因 → 处理」表覆盖 10 个已知故障

### 新增（安装脚本）
- **`plugin/install.sh`**：一条命令装进指定 profile（默认 `jkos`），三级降级 —— ① PATH 里的 `dsh` → ② `DSH_BIN` 指定的可执行文件 → ③ 都没有就照 `examples/profile/` 手工搭建，并**把模板里的 `link:` 占位路径自动替换为插件实际路径**（`&` 已转义，替换后复核，失败即退出码 1，不留半成品）
  - 手工路径幂等：清单与用户层已存在则保留不动，符号链接按需重建；链接位已有非符号链接则拒绝覆盖
  - 走 ①/② 建**新** profile 时先用 `web` 模板初始化 —— CLI 自带的默认模板只含 `dsh-base`，没有 Web 界面 bundle，面板就无处渲染；装完再用 `--dump-config` 复核 `dsh-web-app` 是否在组合里，缺失则打印可执行的修复命令
  - 已存在的 profile 不修改只检查；CLI 对 `--from-default-profile` 的 `already exists` 拒绝按正常情形处理

### 新增（示例 profile 与通用化）
- **`plugin/examples/profile/`**：可直接复制的 profile 骨架 —— `package.json`（bundles 层叠 `dsh-base` → `dsh-web-app` → `jkos-plugin`）+ `cordis.patch.yml`（用户层覆盖模板，含改两处上游地址的写法，默认 `[]` 可跑）
- **通用化**：`cordis.patch.yml` 不再叙述特定部署的端口分工，改为说明两个上游互相独立、各自可配置；`index.js` 注释去掉「同机 loopback」措辞（那是部署事实不是插件性质），http-only 与不转发 WebSocket 升级明确列为限制；README 的配置/限制两节改为部署无关表述

### 验证（FreeBSD 验证机 + harness `65e04a5a07`）
- 本地：`node --check` ×2、两个 `package.json` 解析、三个 YAML（含 `!!js` 标签）结构解析全通过
- 示例 profile：复制成 profile 后由 `dsh plugin add` 补齐依赖，`bundles` 列表被保留，`--dump-config` 组合出 `# == jkos-plugin` 层含 `jkos` + `mcp-jkos` 两行
- `install.sh` 三条路径实测：无 dsh 且无 `DSH_HOME` 时退出码 1 并给提示；手工路径生成的清单依赖指向实际路径、符号链接正确、重跑保留清单；CLI 路径新建 profile 的 bundles 为 `[dsh-base, dsh-web-app, jkos-plugin]`；手工造一个缺 `dsh-web-app` 的 profile 会给出告警与修复命令
- 沙箱 profile 护栏：全程使用 `jkos-verify` / `jkos-example` / `jkos-script2` 等沙箱 profile，未触碰在用的 `sdk` / `web`

## [0.1.0] 初始分叉点 - 2026-09-24

从 `main`（`5a1233f`）分出 `jkos-dsh-plugin`，`b1abb2a` 为分叉点。

### 新增
- **JKOS 作为 deepseek-harness 的插件 bundle**（`plugin/`，5 个文件、零构建）：
  - `package.json`：`dsh.bundle.patch` + `dsh.client{platform:'web'}` + `exports['./client']`
  - `cordis.patch.yml`：`insert:` 两行 —— 插件本体行 + harness 内置 `@deepseek-ai/dsh-mcp-client` 行（指向 JKOS `/mcp`，Bearer 经 `!!js` 从进程环境读取）
  - `index.js`（Host 半包）：注册 `/jkos` 前缀路由反代到 JKOS 服务；鉴权复用 `ctx.connection.requestRejection`（Host/Origin 围栏 + 浏览器会话鉴权），不另造一套；转发用 `node:http` 管道（SSE 不缓冲），剥逐跳头与 `accept-encoding`，对 HTML 做绝对路径前缀改写
  - `client.js`（Client 半包）：手写经典脚本，注册 `sidebar.panellist` 入口与 `main` keyed 面板（同源 iframe 指向 `/jkos/`）
  - `patches/llm-chat-completions.yml`：可选 LLM 协议覆盖层（公网 `api.deepseek.com` 需要 `chat-completions`，否则 404）
- `plugin/README.md`：定位、前置条件、安装、配置、验证、与 FreeBSD 企业版的关系、已知限制、验证状态
- `plugin/examples/profile/` 之前的形态：README 内说明的手工安装步骤

### 保留（本分支未动 `main` 的托管代码）
- `jkos_core/harness/`（901 行，`runtime` / `proxy` / `gateway` / `routes` / `toolbridge` / `config`）原样保留 —— 它是 `main` 的接入层，也是回退路径

### 验证（FreeBSD 验证机 + harness `65e04a5a07`）
| 检查项 | 结果 |
|---|---|
| `dsh plugin add` 离线安装 | 1.1s 成功，bundle 进入 `dsh.profile.bundles` |
| `--dump-config` | `# == jkos-plugin` 层含 `jkos` + `mcp-jkos` 两行 |
| harness 启动 | 16s 起来；`failOnStartupError: true` 未导致失败 |
| 匿名 `/` 与 `/jkos/` | 均 401（信任围栏生效） |
| 带令牌 `/jkos/` | 200，25241 字节 = JKOS 控制台页面 |
| Client 半包 | 进入 boot 图，生成独立 bundle URL `/plugins/??jkos-plugin/client.js&rev=…` |
| 工具桥 | JKOS MCP 服务日志出现 `POST /mcp 200 OK`（harness 客户端握手） |

**未验证**：面板在浏览器里的实际渲染（验证机 Web 只绑 loopback，需 SSH 端口转发后人工确认）。