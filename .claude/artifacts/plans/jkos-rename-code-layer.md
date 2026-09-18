# JKOS 更名代码层落地 Implementation Plan

> Status: APPROVED
> Source: CHANGELOG.md「待推送 2026-09-18」代码层更名声明 + 用户方向选择（无独立 spec）
> Mode: default (Planner → Architect → Critic)
> Iterations: 2 / 3
> Author: skywalk
> Last updated: 2026-09-18

## Requirements summary

CHANGELOG「待推送」批次已完成文档与元数据更名（README / CHANGELOG / pyproject 描述 / docs 全量），并声明**代码层**（`dsh_core` 包名、`dsh-server` CLI、API title、locales 品牌文案、部署脚本横幅，涉及 5 处测试断言同步）将在后续里程碑落地。本 plan 即该里程碑（M15）的实施契约。deepseek-harness 深度集成不在本轮。

现状盘点（rg 实测）：
- `dsh_core`：612+ 处引用，分布在 `dsh_core/`、`tests/`、`tenants/`、`server.py`、`scripts/*.py`（约 120 个 py 文件）
- 品牌文案「DSH AI 中台」代码残留：`server.py:2,44,91,96,98,120,187`、`dsh_core/cli.py:3,54,101,303`、`dsh_core/api/routes.py:257`、`locales/zh/common.json:3`、`dsh_core/plugins/i18n.py:168,257,545`、`dsh_core/optimization/__init__.py:1`、`dsh_core/mcp/server.py:378,382,403`（模拟 OCR/ASR 文案，已核实无测试断言依赖）
- en 文案：`locales/en/common.json:3` `"DSH AI Platform"`
- CLI：pyproject.toml:32-33 scripts `dsh-server`/`dsh-plugin`；`dsh_core/cli.py` 内 usage 字符串；`tests/test_cli.py:449,538,693-727` argv 断言
- 5 处品牌测试断言：`tests/test_api_plugin_tool_routes.py:714`、`tests/test_cli.py:255`、`tests/test_m5_i18n.py:108,144,190`

## Acceptance criteria

- **AC-1**：0.82 venv-test312（py3.12）全量回归 **789 passed、TOTAL coverage 88%**（与 2026-09-18 基线完全一致）；venv-test（3.11）同绿
- **AC-2**：残留扫描 0 命中 —— `rg "DSH AI 中台|dsh_core|dsh-server|dsh-plugin|DSH AI Platform"` 限定 `dsh_core|jkos_core/ tests/ tenants/ server.py pyproject.toml locales/ scripts/*.py`（排除 docs/、CHANGELOG.md、reports/、coverage_report*），命中数 = 0
- **AC-3**：集成冒烟（`scripts/cov82_integ.sh`）`/openapi.json` 的 `info.title == "极快AI操作系统 API"`，且 i18n 接口返回 `app.name == "极快AI操作系统"`
- **AC-4**：CLI 验证 —— 0.82 上 `python -c "from jkos_core.cli import main"` 成功；`tests/test_cli.py` 中全部 dispatch/usage 断言（含 `"用法: jkos-server db <migrate|stats>"`）在回归中通过
- **AC-5**：`CHANGELOG.md` 新增 `[M15] JKOS 代码层更名落地` 条目，列明包名/CLI/文案/断言四类变更

## RALPLAN-DR

### Principles

- 跟随 baseline「最小代码」：只做更名映射，不顺手重构、不加兼容别名层
- 跟随 CHANGELOG 声明范围：包名 / CLI / API title / locales / 部署横幅 / 5 断言，不擅自扩
- 历史文档存档原则（沿用上一批次的「更名公告」先例）：docs/ 与历史 CHANGELOG 保留旧称谓不改
- 可验证成功标准：机械替换由「全量回归 + 残留扫描」双重判据兜底，不靠目测

### Decision drivers

1. **CHANGELOG 已公开声明该范围** —— 包名 rename 是既定承诺，不是本次新决策
2. **回归基线稳固**（789 passed 双 venv + 集成脚本现成）—— 机械改动的验证成本极低
3. **避免双轨期** —— 别名方案会让 dsh/jkos 两套名字共存，后续 deepseek-harness 集成时还要再收敛一次

### Viable options

**Option A: 一次性全量落地（chosen）**
- 实现思路：目录 mv + 全局机械替换 + 文案逐点更新，一轮完成包名/CLI/文案/断言
- 改动文件：约 130 个（`dsh_core/`→`jkos_core/` 全目录、`tests/` 全部引用文件、`server.py`、`pyproject.toml`、`locales/{zh,en}/common.json`、`CHANGELOG.md`）
- Pros：一步到位、无双轨、与 CHANGELOG 声明严格对齐、回归完全可验证
- Cons：diff 大（~600 处）；本地仓库无 git 历史，回滚依赖本次新建的基线 commit

**Option B: 文案先行、包名后置**
- 实现思路：本轮只改品牌文案（API title / locales / 横幅 / 5 断言），`dsh_core` 包名与 CLI 留待 deepseek-harness 集成里程碑
- 改动文件：~10 个（`server.py`、`cli.py`、`routes.py`、`locales/*`、`plugins/i18n.py`、5 个测试文件）
- Pros：diff 小、风险最低
- Cons：品牌落地不彻底；CHANGELOG 声明的范围被拆散，包名 rename 的承诺悬置

**Option C: 仅加别名、永不改名（invalidated）**
- invalidation rationale：新增 `jkos-server` alias 但保留 `dsh_core`/`dsh-server` —— 品牌双轨永久化，两套名字共存使后续所有文档/脚本/运维都面临「用哪个」的问题，CHANGELOG 的更名目标实际未达成。仅在 B 被选中且后续又想避免 rename 时才有意义，与 A/B 相比无独立价值。

### Implementation steps（基于 Option A）

1. **基线防护网** — 本地 `g:\dswork\AI\dsh-ai-platform` 当前无 `.git`：`git init && git add -A && git commit -m "baseline before JKOS rename"`；验证 `git rev-parse HEAD` 非空。此后所有改动可用 `git diff` 审查、`git checkout -- .` 回滚
2. **包名目录与导入替换** — `mv dsh_core jkos_core`；全局替换 `dsh_core` → `jkos_core`，范围：`jkos_core/ tests/ tenants/ server.py scripts/*.py`；**排除** `docs/ CHANGELOG.md reports/ coverage_report/ coverage_report.html *.pyc`（历史文档存档不动）
3. **pyproject.toml** — `:2` `name = "jkos-ai-platform"`；`:32-33` scripts → `jkos-server = "jkos_core.cli:main"` / `jkos-plugin = "jkos_core.plugin_cli:main"`；`:52` `addopts` 中 `--cov=jkos_core`；`:55` `source = ["jkos_core"]`
4. **CLI 文案** — `dsh_core/cli.py:303` description → `"极快AI操作系统 (JKOS)"`；cli.py 与 `plugin_cli.py` 内全部 usage 字符串 `dsh-server`/`dsh-plugin` → `jkos-server`/`jkos-plugin`（rg 定位，含 docstring 示例）
5. **品牌文案逐点更新** — `server.py:2,44,91,96,98,120,187`；`cli.py:3,54,101`；`routes.py:257` → `"极快AI操作系统 API"`；`locales/zh/common.json:3` → `"极快AI操作系统"`；`locales/en/common.json:3` → `"Jikuai AI OS"`；`plugins/i18n.py:168,257,545`；`optimization/__init__.py:1`；`mcp/server.py:378,382,403`
6. **测试断言同步** — 5 处品牌断言（step 5 清单所列文件行号）；`tests/test_cli.py:449,538,693-727` 的 `dsh-server` argv/usage → `jkos-server`
7. **CHANGELOG** — 新增 `[M15] JKOS 代码层更名落地` 条目（含包名/CLI/文案/断言四类变更与新旧映射表）
8. **本地回归** — Windows py3.14 全量 pytest，基线 789 passed
9. **服务器回归** — `python scripts/push82.py`（会保留远端 .git）→ 0.82 `venv-test`（3.11）与 `venv-test312`（3.12）全量 pytest，各 789 passed / 88%
10. **集成冒烟 + 残留扫描** — `cov82_integ.sh` 全绿且 openapi title 符合 AC-3；执行 AC-2 的 rg 命令确认 0 残留

## Workspace setup

- 实施前运行 `git status --short` 与 `git branch --show-current`。**本仓库当前无 `.git`**（服务器侧有快照 32ed6c4），因此第一步必须先 `git init` + 基线 commit，使后续改动全部可 diff / 可回滚 —— 这同时也是本 plan 的回滚机制
- 本地无分支体系，不创建 worktree；以基线 commit 之上的 working tree 直接实施，完成后按用户习惯决定是否 commit / 推送
- 服务器侧 `/data/dsh/code-jkos/.git`（HEAD 32ed6c4）是当前唯一 git 历史，push82 已配置保留，勿动

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 漏改 import → 运行时 ImportError | 全量回归（双 venv）+ AC-2 残留扫描双重兜底；任何一处漏网会立即在回归暴露 |
| pytest `--cov=jkos_core` 失效 → 覆盖率静默归零 | 回归输出必须核对 `TOTAL 88%`，非 88% 即中断排查 `pyproject.toml:52,55` |
| `mcp/server.py` 模拟文案改动破坏隐藏断言 | 已 rg 核实 tests/ 无引用（0 命中）；回归兜底 |
| legacy 一次性脚本（`deploy_m0.py` 等 40+ 个）被机械替换后无人验证 | 明确不作为验收项、不逐个手测；它们本就不在运行路径；回归与集成覆盖当前活跃入口（server.py / cli.py / push82 链路） |
| 运维习惯断档（`dsh-server` → `jkos-server`） | CHANGELOG M15 条目附新旧命令映射表；服务器部署链路（python -c 方式）同步验证 |
| 本地无 git 期间误操作不可回滚 | Step 1 基线 commit 是实施前置条件，未完成不得开始任何替换 |

## Verification steps

- **AC-1**：`ssh82.py` 执行 `/data/dsh/venv-test312/bin/python -m pytest -q` → `789 passed` 且 `TOTAL ... 88%`；`venv-test` 同命令同结果
- **AC-2**：`rg -c "DSH AI 中台|dsh_core|dsh-server|dsh-plugin|DSH AI Platform" dsh_core tests tenants server.py pyproject.toml locales scripts` → 无输出（0 文件命中）
- **AC-3**：`cov82_integ.sh` 输出 `paths: 30` 且新增 grep 确认 `info.title`；或 `curl -s http://127.0.0.1:8001/openapi.json | python -c "import json,sys; assert json.load(sys.stdin)['info']['title']=='极快AI操作系统 API'"`
- **AC-4**：0.82 执行 `python -c "import sys; sys.argv=['jkos-server','--help']; from jkos_core.cli import main; main()"` 输出 usage 且 description 为新品牌
- **AC-5**：`CHANGELOG.md` 顶部出现 `[M15]` 条目；rg 确认 5 处断言文件行号处均为新文案

## ADR

- **Decision**：采用 Option A 一次性完成 JKOS 代码层更名（`dsh_core`→`jkos_core`、`dsh-server`/`dsh-plugin`→`jkos-server`/`jkos-plugin`、API title / locales / 横幅文案、5 处测试断言），历史文档存档不动
- **Drivers**：CHANGELOG 已声明的承诺范围（决定性）；回归基线稳固使机械替换验证成本低；避免双轨命名
- **Alternatives considered**：Option A chosen；Option B rejected —— 范围拆散导致承诺悬置、后续仍要二次改动；Option C rejected —— 双轨永久化，无独立价值
- **Why chosen**：改动虽大但完全机械、可由现成回归基线与残留扫描完全验证；一次性收敛使 deepseek-harness 集成里程碑从干净命名起步
- **Consequences**：正向 —— 品牌/代码/文档三层一致，后续里程碑不再背负命名债；负向 —— ~600 处 diff、外部引用 dsh-server 的记忆与文档示例失效（已在 CHANGELOG 记录映射）
- **Follow-ups**（明确不做，进 backlog）：① pyproject `name` 目标值若想用 `jkos` 而非 `jkos-ai-platform` 需另行决策（默认 jkos-ai-platform）；② `DSH_*` 环境变量前缀是否更名（运维兼容性，牵动 .env 与部署文档）；③ 本地目录 `dsh-ai-platform` 与远端 `/data/dsh/*` 路径改名（牵动全部 82 系脚本与 memory 记录）

## Review trail

- Planner draft v1：全量 rename 方案，3 options，10 步实施
- Architect challenge v1：steelman —— 本地无 git、回滚不对称，612 处替换出错会侵蚀关键路径时间；tension —— 品牌彻底性 vs churn/风险；结论：风险有界但防护网必须前置
- Critic verdict v1：**REVISE** —— ① git 基线仅出现在 workspace setup，未成为带验证的实施步骤；② AC-4 不可二值验证；③ pyproject name 的选择散落在 step 3 未进 ADR
- Planner draft v2：Step 1 增补 `git rev-parse HEAD` 验证；AC-4 改为具体命令 + 预期输出；pyproject name 决策移入 ADR Follow-ups
- Architect challenge v2：确认 steelman 已被 Step 1 化解，无新增 tension
- Critic verdict v2：**APPROVED**（2 处保留意见，见下）
- Final iterations: 2 / 3

### Critic reservations（APPROVED 仍保留）

- scripts/ 目录的历史脚本（`deploy_m0.py`、`install_rust_and_deploy.py` 等）随全局替换改名后**无任何验证手段**（回归不覆盖它们）；plan 将其明确排除出验收项是诚实的，但实施者应知晓：这些脚本从此处于「改过但未验证」状态，若将来复用需先手工核对
- AC-3 依赖 `cov82_integ.sh` 内部改断言，集成脚本本身属于本 plan 的改动面（step 10），存在「用被改的脚本验证自己」的轻微自证风险；缓解 —— AC-3 同时给出独立的 curl+assert 单行命令作为第二判据
