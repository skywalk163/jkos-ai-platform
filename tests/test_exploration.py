"""探索引擎单元测试（M11）— 纯 assert，兼容 pytest 与 m0_selftest 直跑

覆盖：问题分解（规则/LLM/人工干预）、知识库存取检索、方案搜索（知识/代码/文档）、
试错实验（通过/失败调整/放大/审批钩子）、端到端 explore（知识复用/新鲜探索/教训沉淀）。
"""
import asyncio
import json
import os
import tempfile
from types import SimpleNamespace

from dsh_core.exploration import (
    ExplorationEngine,
    Experimenter,
    Hypothesis,
    KnowledgeBase,
    SolutionSearcher,
    TaskDecomposer,
)
from dsh_core.exploration.base import Result, Solution, SolutionCandidate, SubTask
from dsh_core.exploration.knowledge_base import keyword_score, tokenize
from dsh_core.exploration.seed_data import (
    SEED_FAILURES,
    SEED_SOLUTIONS,
    seed_knowledge_base,
)


def run(coro):
    return asyncio.run(coro)


def make_kb(prefix="dsh_kb_"):
    """临时目录下的独立知识库，避免污染默认 knowledge.db"""
    return KnowledgeBase(db_path=os.path.join(tempfile.mkdtemp(prefix=prefix), "kb.db"))


def make_engine(prefix="dsh_exp_"):
    """独立知识库 + 隔离仓库根（无 dsh_core/docs，搜索确定且快速）"""
    tmp = tempfile.mkdtemp(prefix=prefix)
    kb = KnowledgeBase(db_path=os.path.join(tmp, "kb.db"))
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo, exist_ok=True)
    searcher = SolutionSearcher(knowledge_base=kb, repo_root=repo)
    engine = ExplorationEngine(
        knowledge_base=kb,
        searcher=searcher,
        report_dir=os.path.join(tmp, "reports"),
    )
    return engine, kb, repo, tmp


# ---------- 切词与打分（11.4） ----------

def test_keyword_score_and_tokenize():
    assert keyword_score("分析销售数据", "分析销售数据") == 1.0
    assert keyword_score("分析销售数据", "部署模型服务") == 0.0
    assert keyword_score("销售数据统计", "") == 0.0
    assert keyword_score("", "销售") == 0.0
    assert "数据" in tokenize("分析销售数据")
    assert "sales" in tokenize("Analyze sales data")
    assert "的" not in tokenize("数据的清洗")   # 停用词剔除


# ---------- 知识积累（11.4） ----------

def test_kb_store_search_roundtrip():
    kb = make_kb()
    run(kb.initialize())
    run(kb.initialize())   # 幂等
    sol = Solution(
        solution_id="s-1",
        task="统计服务接口调用量",
        steps=["采集日志", "聚合频次"],
        source_type="exploration",
    )
    run(kb.store(sol, Result(task="统计服务接口调用量", success=True,
                             summary="TOP10 接口占 80%", metric=0.9)))
    assert run(kb.count()) == 1
    hits = run(kb.search("统计接口调用量"))
    assert hits and hits[0].task == "统计服务接口调用量"
    assert hits[0].category == "solution" and hits[0].outcome == "success"
    assert hits[0].meta["solution_id"] == "s-1"


def test_kb_search_filters_by_scoring():
    kb = make_kb()
    for i, t in enumerate(["分析销售数据", "部署模型服务", "优化查询性能"]):
        run(kb.store(Solution(solution_id=f"s{i}", task=t, steps=["a"]),
                     Result(task=t, success=True, summary="ok")))
    hits = run(kb.search("分析销售数据"))
    assert hits[0].task == "分析销售数据"      # 精确命中排序第一
    # 无关键词重叠的内容得 0 分，不被召回
    assert run(kb.search("招投标流程")) == []


def test_kb_store_failure():
    kb = make_kb()
    run(kb.store_failure(
        Solution(solution_id="f1", task="全量重算历史数据", steps=["x"]),
        "子任务超时，应先按维度分批增量计算"))
    k = run(kb.search("全量重算历史数据"))[0]
    assert k.category == "failure" and k.outcome == "failure"
    assert "失败原因" in k.content
    assert run(kb.search("全量重算历史数据", category="solution")) == []


def test_kb_persists_across_instances():
    path = os.path.join(tempfile.mkdtemp(prefix="dsh_kbp_"), "kb.db")
    kb1 = KnowledgeBase(db_path=path)
    run(kb1.store(Solution(solution_id="a", task="调研向量库选型", steps=["收集资料"]),
                  Result(task="调研向量库选型", success=True, summary="ok")))
    kb2 = KnowledgeBase(db_path=path)   # 重新开库，验证 SQLite 持久化
    assert run(kb2.count()) == 1
    assert run(kb2.search("调研向量库选型"))[0].task == "调研向量库选型"


def test_kb_all_fetch():
    kb = make_kb()
    run(kb.store(Solution(solution_id="a", task="开发埋点接口", steps=["x"]),
                 Result(task="开发埋点接口", success=True, summary="ok")))
    rows = run(kb.all())
    assert len(rows) == 1 and rows[0].task == "开发埋点接口"


# ---------- 种子数据（M11 验收：知识库覆盖 100+ 场景） ----------

def test_seed_knowledge_base_populates():
    kb = make_kb("dsh_seed_")
    expected = len(SEED_SOLUTIONS) + len(SEED_FAILURES)
    n = run(seed_knowledge_base(kb))
    assert n == expected
    assert run(kb.count()) == expected
    assert run(seed_knowledge_base(kb)) == 0   # 幂等：已满则跳过


# ---------- 方案搜索与匹配（11.2） ----------

def test_searcher_empty_task():
    searcher = SolutionSearcher(knowledge_base=make_kb(), repo_root=tempfile.mkdtemp())
    assert run(searcher.search("   ")) == []


def test_searcher_knowledge_first():
    kb = make_kb()
    run(kb.store(Solution(solution_id="s1", task="统计销售数据报表", steps=["a"]),
                 Result(task="统计销售数据报表", success=True, summary="完成")))
    searcher = SolutionSearcher(knowledge_base=kb, repo_root=tempfile.mkdtemp())
    res = run(searcher.search("统计销售数据"))
    assert res and res[0].source_type == "knowledge"
    assert res[0].title.startswith("[知识库]")


def test_searcher_scans_code_and_docs():
    tmp = tempfile.mkdtemp(prefix="dsh_sea_")
    code_dir = os.path.join(tmp, "dsh_core", "sales")
    doc_dir = os.path.join(tmp, "docs")
    os.makedirs(code_dir)
    os.makedirs(doc_dir)
    with open(os.path.join(code_dir, "sales_report.py"), "w", encoding="utf-8") as fh:
        fh.write('"""生成销售分析报表模块\n销售数据 汇总 指标\n"""\nimport os\n')
    with open(os.path.join(doc_dir, "sales.md"), "w", encoding="utf-8") as fh:
        fh.write("# 销售数据分析手册\n\n销售 统计 洞察 报表\n")
    searcher = SolutionSearcher(repo_root=tmp)
    res = run(searcher.search("销售数据统计"))
    assert res
    types = {c.source_type for c in res}
    assert {"code", "doc"} <= types
    assert all(c.similarity > 0 for c in res)
    assert searcher.last_duration_ms >= 0


def test_searcher_dedup_same_ref():
    searcher = SolutionSearcher(repo_root=tempfile.mkdtemp())
    dup = [SolutionCandidate("a", "标题", "", "doc", "docs/x.md", 0.5)] * 2
    out = searcher._dedup_and_rank(dup, 8)
    assert len(out) == 1
    assert out[0].source_ref == "docs/x.md"


# ---------- 试错学习机制（11.3） ----------

def test_experimenter_invalid_params():
    for bad in (0.0, -0.1, 0.6):
        raised = False
        try:
            Experimenter(sample_ratio=bad)
        except ValueError:
            raised = True
        assert raised
    exp = Experimenter()
    raised = False
    try:
        run(exp.experiment(Hypothesis(statement="x"), dataset_size=-1))
    except ValueError:
        raised = True
    assert raised


def test_experimenter_success_scales_up():
    exp = Experimenter()
    res = run(exp.experiment(
        Hypothesis(statement="新方案", approach="小步验证", confidence=0.9),
        dataset_size=1000))
    assert res.success is True
    assert res.metric_name == "trial_estimate"
    assert res.attempts == 1
    assert res.sample_size == 100      # 1000 * 0.1
    assert res.scale_up is True        # 有更大数据集 → 放大


def test_experimenter_no_scale_when_small_dataset():
    exp = Experimenter()
    res = run(exp.experiment(
        Hypothesis(statement="小任务", approach="x", confidence=0.8),
        dataset_size=0))
    assert res.success is True
    assert res.scale_up is False       # dataset_size <= sample_size
    assert res.sample_size == 1


def test_experimenter_failure_reaches_max_attempts():
    async def bad(h, n):
        return False, 0.1, "样本不足，结果不可靠"

    exp = Experimenter(max_attempts=3)
    res = run(exp.experiment(
        Hypothesis(statement="某方案", approach="", confidence=0.4),
        executor=bad, dataset_size=1000))
    assert res.success is False
    assert res.attempts == 3
    assert res.metric == 0.1
    assert res.scale_up is False
    assert res.hypothesis.confidence > 0.4   # 失败后策略已调整


def test_experimenter_custom_async_executor():
    async def ok(h, n):
        return True, 0.95, "ok"

    exp = Experimenter()
    res = run(exp.experiment(
        Hypothesis(statement="t", approach="", confidence=0.5),
        executor=ok, dataset_size=500))
    assert res.success is True
    assert res.metric == 0.95
    assert res.sample_size == 50


def test_experimenter_approval_hook_controls_scale_up():
    async def approve(h, r):
        return True

    exp = Experimenter(require_approval=True, intervention_hook=approve)
    res = run(exp.experiment(
        Hypothesis(statement="大方案", approach="逐步铺开", confidence=0.9),
        dataset_size=1000))
    assert res.scale_up is True

    async def reject(h, r):
        return False

    exp2 = Experimenter(require_approval=True, intervention_hook=reject)
    res2 = run(exp2.experiment(
        Hypothesis(statement="大方案2", approach="逐步铺开", confidence=0.9),
        dataset_size=1000))
    assert res2.scale_up is False

    def approve_sync(h, r):   # 同步钩子也支持
        return True

    exp3 = Experimenter(require_approval=True, intervention_hook=approve_sync)
    res3 = run(exp3.experiment(
        Hypothesis(statement="大方案3", approach="逐步铺开", confidence=0.9),
        dataset_size=1000))
    assert res3.scale_up is True

    async def boom(h, r):
        raise RuntimeError("审批服务不可用")

    exp4 = Experimenter(require_approval=True, intervention_hook=boom)
    res4 = run(exp4.experiment(
        Hypothesis(statement="大方案4", approach="逐步铺开", confidence=0.9),
        dataset_size=1000))
    assert res4.scale_up is False   # 钩子异常按不放大处理


# ---------- 问题分解引擎（11.1） ----------

def test_decompose_empty():
    assert run(TaskDecomposer().decompose("  ")) == []


def test_decompose_rules_analysis():
    subs = run(TaskDecomposer().decompose("分析2026年第一季度销售数据"))
    assert [s.name for s in subs] == ["数据采集", "数据清洗", "指标计算", "结果汇总"]
    assert subs[0].depends_on == []
    assert subs[1].depends_on == ["数据采集"]
    assert all(s.depth == 1 for s in subs)


def test_decompose_fallback_two_stage():
    subs = run(TaskDecomposer().decompose("写一封商业邮件"))
    assert [s.name for s in subs] == ["准备", "执行"]
    assert subs[1].depends_on == ["准备"]


def test_decompose_depth_limit():
    subs = run(TaskDecomposer(max_depth=5).decompose("分析销售数据", depth=6))
    assert len(subs) == 1
    assert subs[0].depth == 5
    assert "不再细分" in subs[0].description


def test_decompose_recursive():
    flat = run(TaskDecomposer(max_depth=5).decompose("开发一个多语言查重工具", recursive=True))
    assert len(flat) > 4                    # 复杂子任务继续细分
    assert max(s.depth for s in flat) <= 5  # 不越界


def test_decompose_via_llm():
    class FakeLLM:
        def __init__(self, content):
            self._c = content

        async def chat_text(self, *a, **kw):
            return SimpleNamespace(content=self._c)

    fake = FakeLLM(
        '[{"name":"抓数据","description":"抓取数据源","depends_on":[]},'
        '{"name":"做报表","description":"生成图表","depends_on":["抓数据"]}]')
    subs = run(TaskDecomposer(llm=fake).decompose("统计销售并出报表"))
    assert [s.name for s in subs] == ["抓数据", "做报表"]
    assert subs[1].depends_on == ["抓数据"]


def test_decompose_llm_failure_falls_back():
    class BrokenLLM:
        async def chat_text(self, *a, **kw):
            raise RuntimeError("LLM 宕机")

    subs = run(TaskDecomposer(llm=BrokenLLM()).decompose("分析销售数据"))
    assert [s.name for s in subs] == ["数据采集", "数据清洗", "指标计算", "结果汇总"]


def test_decompose_manual_intervention():
    dec = TaskDecomposer()
    run(dec.decompose("分析销售数据"))
    added = dec.add_subtask("撰写报告", description="汇总结论")
    assert added.name == "撰写报告"
    raised = False
    try:
        dec.add_subtask("撰写报告")   # 重名抛 ValueError
    except ValueError:
        raised = True
    assert raised
    assert dec.remove_subtask("数据清洗") is True
    assert all("数据清洗" not in s.depends_on for s in dec._last)
    assert dec.update_subtask("指标计算", description="按口径计算指标") is True
    assert next(s for s in dec._last if s.name == "指标计算").description == "按口径计算指标"
    dec.replace_decomposition([SubTask(name="唯一任务", depth=1)])
    assert [s.name for s in dec._last] == ["唯一任务"]


# ---------- 端到端 explore（M11 快速开始） ----------

def test_engine_empty_task():
    engine, _, _, _ = make_engine()
    res = run(engine.explore("   "))
    assert res.success is False
    assert res.summary == "任务为空，未开始探索"


def test_engine_fresh_exploration_succeeds_and_persists():
    engine, kb, _, _ = make_engine()
    res = run(engine.explore("探索如何优化中台资源利用率"))
    assert res.success is True
    assert res.solution is not None
    assert res.solution.source_type == "exploration"
    assert "实验通过" in res.summary
    assert run(kb.count()) >= 1   # 成功后已沉淀


def test_engine_knowledge_reuse():
    engine, kb, _, _ = make_engine()
    run(seed_knowledge_base(kb))
    res = run(engine.explore("分析2026年第一季度销售数据"))
    assert res.success is True
    assert res.solution.source_type == "knowledge"
    assert "命中历史方案" in res.summary
    assert res.knowledge
    assert res.experiment is None   # 直接复用，未重复实验


def test_engine_failure_persists_lesson():
    async def always_fail(h, n):
        return False, 0.2, "模拟失败"

    engine, kb, _, _ = make_engine()
    res = run(engine.explore("分析新领域任务", executor=always_fail))
    assert res.success is False
    assert "教训已沉淀" in res.summary
    hits = run(kb.search("分析新领域任务"))
    assert any(h.outcome == "failure" for h in hits)


def test_engine_knowledge_persists_after_restart():
    tmp = tempfile.mkdtemp(prefix="dsh_per_")
    path = os.path.join(tmp, "kb.db")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo, exist_ok=True)
    kb = KnowledgeBase(db_path=path)
    searcher = SolutionSearcher(knowledge_base=kb, repo_root=repo)
    engine = ExplorationEngine(knowledge_base=kb, searcher=searcher)
    res1 = run(engine.explore("分析季度异常波动"))
    assert res1.success is True
    run(engine.close())

    # 重启后（新实例）同一任务直接命中历史知识
    kb2 = KnowledgeBase(db_path=path)
    engine2 = ExplorationEngine(knowledge_base=kb2,
                                searcher=SolutionSearcher(knowledge_base=kb2, repo_root=repo))
    res2 = run(engine2.explore("分析季度异常波动"))
    assert res2.success is True
    assert res2.solution.source_type == "knowledge"


def test_engine_saves_report():
    engine, _, _, base = make_engine()
    res = run(engine.explore("分析新报告任务", save_report=True))
    assert res.success is True
    report_dir = os.path.join(base, "reports")
    files = [f for f in os.listdir(report_dir) if f.endswith(".json")]
    assert files
    with open(os.path.join(report_dir, files[0]), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["task"] == "分析新报告任务"
    assert payload["success"] is True
    assert "duration_ms" in payload


def test_engine_comps_injection():
    kb = make_kb()
    engine = ExplorationEngine(knowledge_base=kb, comps=SimpleNamespace(llm="fake-llm"))
    assert engine.llm == "fake-llm"   # M2 组合装配 comps.llm 注入