# JKOS Plugin for deepseek-harness

把「极快AI操作系统」（JKOS）做成 deepseek-harness 的插件 bundle：装上它，harness 的 Web 界面里多出一个 JKOS 面板，agent 也能直接调用 JKOS 的工具。

**不改 harness 一行代码** —— 插件只用两个标准边界对接：harness 的 Web 路由表，和 harness 内置的通用 MCP 客户端。

## 它做什么

| 能力 | 实现方式 | 用户看到的结果 |
|---|---|---|
| JKOS 面板 | Host 半包注册 `/jkos` 前缀路由反代到 JKOS 服务；Client 半包注册侧栏入口 + 主区面板（同源 iframe） | 侧栏出现「极快AI操作系统」，点开是 JKOS 控制台 |
| 工具桥 | `cordis.patch.yml` 插入 harness 内置的 `@deepseek-ai/dsh-mcp-client`，指向 JKOS 的 `/mcp` | agent 获得 `mcp__jkos__<工具名>` 工具 |

文件就是这五个，零构建（手写经典脚本，不需要 TypeScript / JSX / 打包）：

| 文件 | 角色 |
|---|---|
| `package.json` | bundle 清单：`dsh.bundle.patch` + `dsh.client{platform:web}` + `exports['./client']` |
| `cordis.patch.yml` | 组合层：`insert:` 两行（插件本体行 + MCP 客户端行） |
| `index.js` | Host 半包：`/jkos` 反代路由 |
| `client.js` | Client 半包：侧栏入口 + 主区面板 |
| `patches/llm-chat-completions.yml` | 可选的 LLM 协议覆盖层（见下） |

## 前置条件

**插件单独装上跑不起来** —— 它只是接入层，用户管理、agent 管理、工具注册表、多租户、配额都在 JKOS 服务（Python）里。要同时满足：

1. 已装好可用的 deepseek-harness（有 `dsh` 命令）。
2. JKOS 服务在运行，并且：
   - 它的 `/` 提供控制台页面（面板 iframe 的内容源）；
   - 它的 `/mcp` 提供标准 MCP 端点（工具桥的上游）。
3. 建议 JKOS 侧开启严格鉴权：`JKOS_MCP_REQUIRE_AUTH=true`（匿名请求 401，工具列表也不泄露租户自定义工具）。

## 自检（装之前先确认上游）

三条命令确认 JKOS 服务真的提供了插件要的两件东西：

```sh
# 1) 控制台页面在 baseUrl 的 / 上（面板内容源）—— 期望 200 + text/html
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://127.0.0.1:3000/

# 2) MCP 端点在，且严格鉴权下匿名被拒 —— 期望 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:3000/mcp \
  -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":0,"method":"tools/list","params":{}}'

# 3) 带令牌时工具列表可读 —— 期望 200 且 result.tools 非空
curl -s -X POST http://127.0.0.1:3000/mcp \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $JKOS_MCP_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head -c 200
```

第 2 条返回 200 说明 JKOS 侧没开严格鉴权，插件仍能用，但匿名可见性会退化。第 3 条要的令牌由 JKOS 签发（`jkos_core.auth` 的 JWTManager，带 tenant/user/roles），例如：

```sh
python -c "import sys; sys.path.insert(0,'<jkos 代码目录>'); from jkos_core.auth import AuthConfig, JWTManager; print(JWTManager(AuthConfig.from_env()).issue_token(tenant_id='dev', tenant_code='dev', user_id='admin', roles=['admin'], expires_in=3600))"
```

**铸令牌的进程与 JKOS 服务必须共享 `DSH_JWT_SECRET`**，否则签出来的 token 立刻被判无效（JKOS 未配置该变量时每个进程各生成一把临时密钥）。

## 安装

```sh
dsh plugin --profile <你的 profile> add /绝对路径/to/本插件目录
```

装完该 bundle 会进入 profile 的 `dsh.profile.bundles`，`node_modules/<包名>` 指向插件目录。

**装进哪个 profile？** 装进你实际启动 Web 界面的那一个 —— 浏览器里的会话与 agent 都跑在这个 profile 里，面板和工具才会同时生效。

注意不同 harness 版本的 CLI 形式：0.82 部署的 `65e04a5a07` 只支持 `dsh --profile <name>` 和硬编码的 `dsh web`，**没有** `dsh <name>` 简写。

> **先检查冲突**：若该 profile 的用户层 patch（`$DSH_HOME/profiles/<profile>/cordis.patch.yml` 或 `$DSH_HOME/cordis.patch.yml`）里已经有 `mcp-jkos` 行（旧版 JKOS 自动生成过），**必须先删掉** —— 两层各 `insert` 一个同名 id 会冲突。

## 快速开始（安装脚本）

在插件目录里跑一条命令，默认装进 profile `jkos`：

```sh
sh install.sh              # 或 sh install.sh <profile 名>
```

脚本按顺序尝试：① PATH 里的 `dsh` → ② `DSH_BIN` 指定的可执行文件 → ③ 都没有就照 `examples/profile/` 手工搭建 profile，并**把模板里的 `link:` 占位路径自动替换成插件的实际路径**。

走 ①/② 建**新** profile 时，脚本会先用 `web` 模板初始化它 —— CLI 自带的默认模板只有 `dsh-base`，没有 Web 界面 bundle，面板就无处渲染；装完再用 `--dump-config` 复核一次，缺 `dsh-web-app` 会明确告警而不是让你自己去猜。已有的 profile 不会被改动，只是被检查。

| 环境变量 | 用途 |
|---|---|
| `DSH_BIN` | dsh 可执行文件路径；PATH 里没有 `dsh` 时用它（例如源码启动器 `<harness>/dsh-jkos.sh`） |
| `DSH_HOME` | harness 的 home 目录；只在退化成 ③ 手工搭建时才需要 |
| `JKOS_MCP_TOKEN` | 启动 harness 前 export，工具桥的 Bearer token |

脚本跑完会打印剩下的注意事项：令牌、启动命令、`mcp-jkos` 冲突检查、以及怎么改上游地址。

不想用脚本也可以手工装，模板在 [`examples/profile/`](examples/profile/)：

| 文件 | 作用 |
|---|---|
| [`examples/profile/package.json`](examples/profile/package.json) | profile 清单：`dsh.profile.bundles` 按顺序层叠 `dsh-base` → `dsh-web-app` → `jkos-plugin`；`dependencies` 里的 `link:` 是占位路径，要改成插件实际路径（脚本会自动填） |
| [`examples/profile/cordis.patch.yml`](examples/profile/cordis.patch.yml) | 用户层覆盖模板：留空即可跑，注释里给了「改上游地址」的覆盖写法 |

启动：

```sh
export JKOS_MCP_TOKEN=<JKOS 签发的 token>
dsh --profile jkos          # 较新版本的 CLI 也接受简写：dsh jkos
```

## 配置

两个上游互相独立，可以在不同机器、不同端口，都在 `cordis.patch.yml` 里：

| 项 | 默认 | 说明 |
|---|---|---|
| 插件行 `config.baseUrl` | `http://127.0.0.1:3000` | 面板要显示的控制台所在服务；`/jkos/*` 反代到它的对应路径 |
| MCP 行 `config.url` | `http://127.0.0.1:3000/mcp` | 工具桥消费的 JKOS MCP 端点 |

默认值指向 JKOS 的 MCP 服务（默认端口 3000，当前同时承担控制台根路由）。换地址的写法见 [`examples/profile/cordis.patch.yml`](examples/profile/cordis.patch.yml)。

密钥一律经环境变量，不落任何文件：

| 环境变量 | 用途 |
|---|---|
| `JKOS_MCP_TOKEN` | 工具桥的 Bearer token（harness 侧经 `!!js` 从进程环境读取） |
| `JKOS_BASE_URL` | 覆盖插件行的 `config.baseUrl`（可选） |

反代挂载路径 `/jkos/` 由 Host 半包的 `ROUTE_PREFIX` 与 `client.js` 的 `FRAME_SRC` 两个常量共同决定，改路径要同时改；侧栏显示名在 `client.js` 的 `PANEL_LABEL`。

## 验证

```sh
dsh --profile <profile> --dump-config      # 应看到 # == jkos-plugin 层，含 jkos 与 mcp-jkos 两行
```

启动 Web 界面后逐项确认：

- 侧栏出现入口，点开显示 JKOS 控制台 → Host 与 Client 半包都在工作；
- 匿名请求 `/jkos/` 应 401、带 harness 登录令牌应 200 → 反代复用了 harness 的 connection 信任围栏（Host/Origin 校验 + 浏览器会话鉴权），没有另造一套鉴权；
- 让 agent 调用 `mcp__jkos__<工具名>` 并拿到真实返回 → 工具桥工作。

## 排错

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `--dump-config` 里看不到 `# == jkos-plugin` 层 | bundle 没进 profile | `dsh plugin --profile <p> add <插件目录>`；再查 `dsh.profile.bundles` 是否含 `jkos-plugin` |
| harness 启动直接失败，日志涉及 mcp / dsh-mcp-client | MCP 端点不可达或令牌无效 —— `failOnStartupError: true` 会让整行失败 | 先跑上面「自检」三条；定位阶段可临时把它改 `false` |
| 侧栏没有 JKOS 入口、面板无处显示 | profile 的 bundles 里没有 `dsh-web-app`（CLI 自带默认模板就没有） | `dsh --profile <p> --from-default-profile web --dump-config` 初始化后重装，或把 `dsh-web-app` 加进 bundles |
| 面板打开是 404 | `baseUrl` 指向了没有控制台根路由的服务（例如 JKOS 的 API 服务 8000） | 把插件行的 `baseUrl` 改到控制台服务 |
| 面板打开是 502 | 反代连不上上游 | 确认 JKOS 服务在跑、`baseUrl` 可达；harness 日志会打印「上游不可达」与原因 |
| 匿名 curl `/jkos/` 返回 200 而不是 401 | 请求带了浏览器登录 cookie，`requestRejection` 已放行 | 属正常；用不带 cookie 的 curl 复核 |
| agent 看不到 `mcp__jkos__*` 工具 | 工具桥没连上，或令牌与 JKOS 侧密钥不匹配 | 跑「自检」第 3 条；确认铸令牌的进程与 JKOS 服务共享 `DSH_JWT_SECRET` |
| `dsh: error: --profile <name> is required` | 用了 `dsh <profile>` 简写，但该版本 CLI 没有这个简写（见「安装」一节） | 改用 `dsh --profile <name>` |
| 启动报同一 id 被插了两次 | profile 用户层已有一份自动生成的 `mcp-jkos` 行 | 删掉用户层那份（保留插件的 bundle 层） |
| 面板里的静态资源 404 | 页面用了根绝对路径引用 | 反代会把 HTML 里的 `href/src/action="/x"` 改写到 `/jkos/x`；若仍 404，请附页面来源提 issue |

## 与 FreeBSD 企业版的关系

同一仓库另一条线（`main`）是定制部署在 FreeBSD 上的企业级中台实现，其中 JKOS 是 **harness 被 JKOS 托管**（sidecar：JKOS 拉起 harness 子进程、反代它的 Web UI、在自身里重造会话与事件流）。

本分支是它的开源对偶：**harness 当宿主，JKOS 当插件**。两者共享 `jkos_core/` 的中台能力（认证、多租户、工具注册表与可见性、MCP 网关），差异只在接入层 —— 这也是两条线能同步开发的原因：中台改一处，两条线都受益。

## 已知限制

- 反代只支持 `http` 上游，也不转发 WebSocket 升级。JKOS 与 harness 不同机、或中间要 TLS 时，需要自行补传输层。
- 面板内容取自插件行 `config.baseUrl` 的 `/`。JKOS 的控制台根路由当前挂在 MCP 服务（默认 3000）上，API 服务（默认 8000）只提供 `/api/v1/*` —— 把面板指向 8000 会 404。
- 公网 `api.deepseek.com` 需要 `patches/llm-chat-completions.yml` 那份覆盖层（harness 的 `deepseek-official` 默认走 messages 协议，指向公网端点会 100% 404）。这属于部署环境问题而非插件职责，所以没有默认挂载 —— 把它的内容放进你的 profile 用户层即可。

## 验证状态（2026-09-24）

在 FreeBSD 0.82 + harness `65e04a5a07` 上，用沙箱 profile（`@deepseek-ai/dsh-base` + `@deepseek-ai/dsh-web-app` + 本插件）实测：

| 检查项 | 结果 |
|---|---|
| `dsh plugin add` 离线安装 | 1.1s 成功，bundle 进入 `dsh.profile.bundles` |
| `dsh --profile <p> --dump-config` | `# == jkos-plugin` 层含 `jkos` + `mcp-jkos` 两行 |
| harness 启动 | 16s 起来；`failOnStartupError: true` 未导致失败 |
| 匿名 `/` 与 `/jkos/` | 均 401（信任围栏生效） |
| 带令牌 `/jkos/` | 200，25241 字节 = JKOS 控制台页面 |
| Client 半包 | 进入 boot 图并生成独立 bundle URL `/plugins/??jkos-plugin/client.js&rev=…`（rev 为内容哈希） |
| 工具桥 | JKOS MCP 服务日志出现 `POST /mcp 200 OK`（harness 客户端握手） |

**未验证**：侧栏入口与面板在浏览器里的实际渲染效果。0.82 的 Web 界面只绑 loopback（3080）且带 loopback 信任围栏，无法从外部浏览器直连；要确认视觉效果需先做 SSH 端口转发（`ssh -L 3080:127.0.0.1:3080 <user>@<host>`）再打开页面。