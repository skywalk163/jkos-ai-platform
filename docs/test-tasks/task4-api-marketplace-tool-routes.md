# Task 4 — 插件市场/i18n/工具 REST 路由：零覆盖转全绿

> 这份文档是给一个**全新 AI Agent** 看的任务书。按顺序读完、照做即可，不需要额外上下文。

---

## 0. 30 秒概况（先看这个）

| 项 | 值 |
|---|---|
| 仓库根目录 | `G:\dswork\AI\dsh-ai-platform` |
| 目标文件 | ① `dsh_core/api/marketplace_routes.py`（301 行，**0%**）② `dsh_core/api/tool_routes.py`（342 行，**48%，缺 76 行**） |
| 你要做的全部事情 | **只创建 1 个新测试文件** `tests/test_api_plugin_tool_routes.py`，用 FastAPI `TestClient` 盖掉两个文件的缺失行 |
| 完成后自检（3 条命令） | 见「第 7 节 运行与验收命令」 |
| 预计耗时 | 90–120 分钟 |

**绝对禁止**：修改源码、修改任何其他测试文件、使用 `pytest.skip` / `# pragma: no cover` / `coverage: ignore` 绕过缺失行。

---

## 1. 背景：为什么有这个任务

M13/M5 的 REST 路由层从未被 HTTP 级测试覆盖：`tool_routes.py` 只被 MCP JSON-RPC 路径间接擦到 48%，`marketplace_routes.py`（插件市场 + i18n 两组路由）完全是 0%。

平行任务 Task 3 负责 CLI 层（`plugin_cli.py`），文件零冲突、手法不同（你测 HTTP，它测 argv），可完全并行。

---

## 2. 目标：让这些行号从"缺失"变"命中"

### 2.1 tool_routes.py 当前缺失（76 行，全部归你）

```
80, 90, 108,                  # 三个 _serialize_* 的 return 体
124-129, 134-139,             # _parse_visibility / _parse_category（含 400）
152, 157, 162-163, 168-169,   # categories / industry-tags / featured / verified
180-197,                      # GET /api/v1/tools 列表+搜索+过滤
205-234,                      # POST /register
243-246, 251-252, 260-268,    # GET 详情 / GET versions / POST publish
276-279, 284-287,             # activate / DELETE 注销
295-298, 303-307,             # rate / downloads
315-328                       # PATCH marketplace
```

### 2.2 marketplace_routes.py 当前缺失（0%，全文件）

除模块级 import（L7-58，import 即覆盖）外，你需要覆盖：

```
L65-208   create_plugin_router 全部端点（categories/statistics/featured/列表/详情/install/uninstall/rate/featured/verified）
L218-280  create_i18n_router 全部端点（languages/单语言/当前语言/translate/batch/set-language）
L290-301  register_marketplace_routes / register_i18n_routes 两个挂载 helper
```

---

## 3. 动手前必须读的文件（按顺序）

| # | 文件 | 为什么读 | 重点看什么 |
|---|---|---|---|
| 1 | `dsh_core/api/tool_routes.py` | 被测对象①（全读） | 各端点入参/返回形状、`_serialize_*`、两个 `_parse_*` 的 400 分支 |
| 2 | `dsh_core/api/marketplace_routes.py` | 被测对象②（全读） | `create_plugin_router` / `create_i18n_router` 的**实例注入参数**、L285 底部才 import 的 `Header, Body` |
| 3 | `dsh_core/auth/dependencies.py` L127-146 | 认证依赖行为 | `get_tenant_context` 匿名时返回什么。**先读这个再决定匿名用例**（见 4.3） |
| 4 | `tests/test_m14_mcp_mt.py` | 认证注入范本 | L31-37 `_setup_global_auth` fixture + L47-56 `_auth()` 签发 Bearer 的完整手法（直接照抄） |
| 5 | `tests/test_m5_marketplace.py` | 市场构造范本 | L321 `TestPluginMarketplace` fixture、L759 `TestMarketplaceInstallUninstall` fixture（install 成功路径怎么造） |
| 6 | `tests/test_m5_i18n.py` | I18nManager 构造范本 | L77-80：`I18nManager()` 零参构造 |

---

## 4. 测试手法（本任务的核心技巧）

### 4.1 构造 app：**实例注入**，不碰全局

三个工厂都支持注入，绝不调用无参版（避免污染全局单例）：

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from dsh_core.api.tool_routes import create_tool_router
from dsh_core.api.marketplace_routes import create_plugin_router, create_i18n_router

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_tool_router(ToolRegistry()))        # 全新注册表
    app.include_router(create_plugin_router(mp))                  # 见 4.2 构造
    app.include_router(create_i18n_router(I18nManager()))         # 零参真实例
    return app

@pytest.fixture
def tc():
    with TestClient(_make_app()) as client:
        yield client
```

### 4.2 PluginMarketplace 实例

`PluginMarketplace()` 零参可构造（见 `plugins/marketplace.py` L378 默认路径），但 `list_plugins` L94 会读 `mp.registry._plugins`，`install` 成功路径需要真实插件——**照抄 test_m5_marketplace.py L321/L759 的 fixture 构造**（注册一个 `DeterministicPlugin` 测试插件再喂给 marketplace）。若 install 成功路径构造代价过大，允许只覆盖失败分支（400），但要在 docstring 写明原因。

### 4.3 认证：默认全部带 Bearer（保熟），匿名按源码实测

- **保熟方案**（照抄 test_m14_mcp_mt.py L31-37 + L47-56）：模块级 autouse fixture 注入全局 `JWTManager` 并在模块结束恢复 `auth_deps._jwt_manager`；每个请求带 `_auth("tenant-a", "TA")` 签发的头。
- **匿名用例**：先读 `dependencies.py` L127-146 确认 `get_tenant_context` 匿名时返回 `None` 还是"空租户上下文"。`tool_routes.py` L188 直接访问 `ctx.tenant_id`——若匿名返回 `None` 会 500（疑似缺陷）。此时**不要**给该端点写匿名用例，也不要改源码，在该测试文件头部 docstring 记录一句"匿名访问 list_tools 会 500，疑似缺陷，待修复"即可。
- **注意**：`_jwt_manager` 是全局的，你的恢复 fixture 必须可靠（模式已验证）。

### 4.4 断言基准

所有状态码与响应 JSON 形状以**源码为准**（本文档行号精确，但响应字段请对照 handler 的 return 语句）。

---

## 5. 用例清单（按端点对照行号）

### 5.1 tool_routes.py（prefix `/api/v1/tools`）

| 用例 | 请求 | 断言 | 覆盖行 |
|---|---|---|---|
| 分类列表 | GET `/categories` | 200，含 9 个分类 | 152 |
| 行业标签 | GET `/industry-tags` | 200 | 157 |
| 推荐/验证 | GET `/featured`、`/verified` | 200（先注册一个工具并置 featured/verified） | 162-163, 168-169, 80 |
| 列表-搜索 | GET `?search=xxx` | 命中/不命中 | 180-182 |
| 列表-过滤 | GET `?visibility=core&category=text&industry=finance` | 各过滤生效；非法值→400 | 184-197, 124-129, 134-139 |
| 注册成功 | POST `/register` | 200 `{"status":"registered",...}` | 205-238 |
| 注册重名 | 同名再注册 | **400** | 231-232 |
| 注册非法可见域 | `visibility="bogus"` | **400** | 128-129 |
| 详情 | GET `/{name}` | 200 完整 `_serialize_info` 形状；不存在→**404** | 108, 90, 243-246 |
| 版本历史 | GET `/{name}/versions` | 200 | 251-252 |
| 发布版本 | POST `/{name}/versions` | 200；不存在→**404** | 260-268 |
| 激活版本 | POST `/{name}/versions/{v}/activate` | 200（先 publish）；失败→**404** | 276-279 |
| 注销 | DELETE `/{name}` | 200；再删→**404** | 284-287 |
| 评分 | POST `/{name}/rate` | 200；对不存在工具→**400** | 295-298 |
| 下载+1 | POST `/{name}/downloads` | 200 含 downloads；不存在→**404** | 303-307 |
| 更新市场信息 | PATCH `/{name}/marketplace` | 200；空 body→**400**；不存在→**404** | 315-328 |

### 5.2 marketplace_routes.py — 插件市场（prefix `/api/v1/plugins`）

| 用例 | 请求 | 断言 | 覆盖行 |
|---|---|---|---|
| 分类/统计/推荐 | GET `/categories` `/statistics` `/featured` | 200 | 65-78 |
| 列表-搜索 | GET `?search=x` | 200 含 total | 89-90 |
| 列表-过滤 | GET `?category=&visibility=&industry=` | 各过滤生效（含 CUSTOM 租户隔离 L108-110） | 93-125 |
| 详情 | GET `/{plugin_id}` | 200/404 | 130-133 |
| 安装 | POST `/install` | 非法 visibility→**400**；失败→**400**；成功→200 | 141-157 |
| 卸载 | POST `/uninstall` | 失败→**400**；成功→200 | 165-173 |
| 评分 | POST `/{id}/rate` | 200 / **400** | 181-184 |
| 置推荐/置验证 | POST `/{id}/featured`、`/{id}/verified` | 200 / **400** | 192-195, 203-206 |

### 5.3 marketplace_routes.py — i18n（prefix `/api/v1/i18n`）

| 用例 | 请求 | 断言 | 覆盖行 |
|---|---|---|---|
| 语言列表 | GET `/languages` | 200 | 221 |
| 单语言 | GET `/zh`（或实际存在的码） | 200；不存在→**404** | 226-229 |
| 当前语言 | GET `""`（可带 `Accept-Language` 头或 `?lang=`） | 200 | 231-242 |
| 翻译 | POST `/translate` | 200 | 244-255 |
| 批量翻译 | POST `/translate/batch`，body 为**JSON 数组** `["k1","k2"]` | 200 | 257-268 |
| 设置语言 | POST `/set-language?language=zh` | 200；非法码→**400** | 270-278 |

### 5.4 挂载 helper L290-301

一个用例直接 `register_marketplace_routes(app)` / `register_i18n_routes(app)` + `register_tool_routes` 不归你（那是 tool_routes 的 helper，可顺手调），断言 `app.routes` 数量增加即可——覆盖 L290-301。

---

## 6. 测试文件骨架建议（可照着搭）

```python
"""插件市场/i18n/工具 REST 路由集成测试
覆盖：dsh_core/api/tool_routes.py 缺失 76 行、dsh_core/api/marketplace_routes.py 全文件。
认证采用 test_m14_mcp_mt.py 的全局 JWTManager 注入模式（模块结束恢复）。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dsh_core.auth import AuthConfig, JWTManager, issue_access_token
from dsh_core.auth import dependencies as auth_deps
from dsh_core.api.tool_routes import create_tool_router
from dsh_core.api.marketplace_routes import (
    create_plugin_router, create_i18n_router,
    register_marketplace_routes, register_i18n_routes,
)
from dsh_core.mcp.registry import ToolRegistry, ToolVisibility
from dsh_core.plugins.marketplace import PluginMarketplace
from dsh_core.plugins.i18n import I18nManager

_SECRET = "m14-test-secret-0123456789abcdef"

@pytest.fixture(scope="module", autouse=True)
def _setup_global_auth():          # 照抄 test_m14_mcp_mt.py L31-37
    ...

@pytest.fixture(scope="module")
def mp():                          # 照抄 test_m5_marketplace.py L321/L759
    ...

class TestToolRoutes:              # 5.1 全表
class TestPluginRoutes:            # 5.2 全表
class TestI18nRoutes:              # 5.3 全表
class TestRegisterHelpers:         # 5.4
```

---

## 7. 运行与验收命令（PowerShell，仓库根目录执行）

```powershell
# 1) 只跑你的文件
python -m pytest tests/test_api_plugin_tool_routes.py -q

# 2) 精确覆盖率（注意：必须带 --no-cov！pyproject.toml 的 addopts 自带
#    --cov=dsh_core，与手动 coverage run 冲突会采不到数据）
python -m coverage run -m pytest tests/test_api_plugin_tool_routes.py -q --no-cov
python -m coverage report --show-missing dsh_core/api/tool_routes.py dsh_core/api/marketplace_routes.py

# 3) 全量回归
python -m pytest -q
```

---

## 8. 验收标准（全满足才叫完成）

- [ ] `tests/test_api_plugin_tool_routes.py` 存在，且只这一个新文件被创建/修改
- [ ] 命令 1 全部通过，无 skip/xfail/pragma/ignore
- [ ] 命令 2：`tool_routes.py` missing 为空（100%）；`marketplace_routes.py` missing 仅剩不可达行（如有，逐行注明原因）
- [ ] 命令 3 全量通过
- [ ] 认证 fixture 恢复可靠（模块结束后 `auth_deps._jwt_manager` 为原值）
- [ ] 每个测试有中文 docstring

---

## 9. 红线（违反即返工）

1. 不修改两个路由文件及 `auth/dependencies.py`、`plugins/**` 源码。
2. 不修改、不删除 `tests/` 下任何已有文件。
3. 不用 `pytest.skip` / `# pragma: no cover` / `coverage: ignore` / `# type: ignore` 掩盖缺失行。
4. 发现疑似缺陷（如匿名 500）只记录，不修源码、不为凑覆盖而 write 源码补丁。
5. 不调用无参 `get_marketplace()/get_i18n()/get_registry()` 全局工厂——一律走工厂函数的实例注入参数。
6. 断言以源码 handler 的实际返回为准；本文档响应形状是提示，冲突时以源码为准。