"""DSH 探索引擎 - 问题分解引擎（11.1，P0）

职责：把大任务递归分解为可直接执行的子任务，自动识别依赖关系。
策略：
  1. LLM 可用时驱动分解（要求输出 JSON 数组），解析失败自动回退；
  2. 无 LLM / LLM 异常时使用规则模板（分析/开发/部署/排查/调研/优化 六类）；
  3. 最多 5 层（MAX_DEPTH），层数越界返回叶子任务；
  4. 依赖自动识别：剔除未知依赖、空依赖挂前序兄弟；
  5. 人工干预：add_subtask / remove_subtask / update_subtask /
     replace_decomposition 作用于最近一次分解结果。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jkos_core.exploration.base import SubTask

logger = logging.getLogger("dsh.exploration")

_JSON_ARRAY = re.compile(r"\[[\s\S]*\]")


def extract_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
    """从 LLM 输出中提取 JSON 数组（容错前后缀文字）"""
    match = _JSON_ARRAY.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


# 规则模板：命中关键词即生成标准流水线，保证无 LLM 时也有可执行分解
_RULE_TEMPLATES: List[Dict[str, Any]] = [
    {
        "keywords": ("分析", "统计", "洞察"),
        "subtasks": [
            ("数据采集", "确定数据来源并完成采集"),
            ("数据清洗", "去重、补全、格式统一"),
            ("指标计算", "按目标口径计算核心指标"),
            ("结果汇总", "汇总结论并输出报告"),
        ],
    },
    {
        "keywords": ("开发", "实现", "编码"),
        "subtasks": [
            ("需求梳理", "明确功能边界与验收标准"),
            ("接口设计", "定义输入输出与数据结构"),
            ("编码实现", "按设计完成代码编写"),
            ("测试验证", "编写用例并验证功能正确"),
        ],
    },
    {
        "keywords": ("部署", "发布", "上线"),
        "subtasks": [
            ("环境准备", "检查集群资源与依赖配置"),
            ("构建打包", "完成制品构建与版本归档"),
            ("发布执行", "执行发布并观察运行状态"),
            ("验证回滚", "冒烟验证，失败则回滚"),
        ],
    },
    {
        "keywords": ("排查", "修复", "定位"),
        "subtasks": [
            ("现象确认", "复现问题并记录现场信息"),
            ("根因分析", "定位根因并评估影响面"),
            ("修复实施", "实施修复并补充防护"),
            ("回归验证", "验证问题消除且无副作用"),
        ],
    },
    {
        "keywords": ("调研", "研究", "评估"),
        "subtasks": [
            ("资料收集", "收集相关资料与竞品信息"),
            ("方案对比", "多方案横向对比优劣"),
            ("结论输出", "给出建议与决策依据"),
        ],
    },
    {
        "keywords": ("优化", "提升", "改善"),
        "subtasks": [
            ("基线测量", "测量当前性能基线"),
            ("瓶颈定位", "定位关键瓶颈与热点"),
            ("优化实施", "实施针对性优化"),
            ("效果验证", "对比优化前后数据"),
        ],
    },
]


class TaskDecomposer:
    """问题分解引擎：任务 → 带依赖关系的子任务列表"""

    MAX_DEPTH = 5

    def __init__(self, llm: Optional[Any] = None, *,
                 max_depth: int = MAX_DEPTH,
                 max_subtasks: int = 8):
        self.llm = llm
        self.max_depth = max_depth
        self.max_subtasks = max_subtasks
        self._last: List[SubTask] = []  # 最近一次分解结果，供人工干预

    # ---------- 主流程 ----------
    async def decompose(self, task: str, *, depth: int = 1,
                        recursive: bool = False) -> List[SubTask]:
        """把任务分解为子任务列表

        - depth: 当前层级（1 ~ max_depth，越界返回叶子任务）
        - recursive: 是否对复杂子任务继续递归细分（最多 max_depth 层）
        """
        task = (task or "").strip()
        if not task:
            return []
        if depth > self.max_depth:
            return [SubTask(name=task, description="叶子任务，不再细分",
                            depth=self.max_depth)]

        if self.llm is not None:
            try:
                subtasks = await self._decompose_via_llm(task, depth)
            except Exception as exc:  # LLM 异常/解析失败 → 回退规则分解
                logger.warning("LLM 分解失败，回退规则分解: %s", exc)
                subtasks = self._decompose_rules(task, depth)
        else:
            subtasks = self._decompose_rules(task, depth)

        self._link_dependencies(subtasks)

        if recursive:
            flat: List[SubTask] = []
            for st in subtasks:
                flat.append(st)
                if depth < self.max_depth and self._is_complex(st):
                    children = await self.decompose(
                        f"{st.name}: {st.description}",
                        depth=depth + 1, recursive=True)
                    flat.extend(children)
            subtasks = flat

        self._last = subtasks
        return subtasks

    async def _decompose_via_llm(self, task: str, depth: int) -> List[SubTask]:
        prompt = (
            f"请把以下任务分解为 {self.max_subtasks} 个以内的可直接执行子任务，"
            "并识别它们的依赖关系。\n"
            f"任务：{task}\n"
            "只输出 JSON 数组，元素格式为 "
            '{"name":"子任务名","description":"做什么","depends_on":["前置子任务名"]}，'
            "不要输出任何其它内容。"
        )
        resp = await self.llm.chat_text(
            prompt, purpose="exploration.decompose",
            temperature=0.0, max_tokens=2048)
        data = extract_json_array(resp.content)
        if data is None:
            raise ValueError(
                "LLM 输出不是合法 JSON 数组: " + (resp.content or "")[:120])
        subtasks = []
        for item in data[: self.max_subtasks]:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            name = str(item["name"]).strip()
            deps = [str(d).strip() for d in item.get("depends_on", [])
                    if str(d).strip()]
            subtasks.append(SubTask(
                name=name,
                description=str(item.get("description", "")).strip(),
                depends_on=deps,
                depth=depth,
            ))
        if not subtasks:
            raise ValueError("LLM 未返回有效子任务")
        return subtasks

    def _decompose_rules(self, task: str, depth: int) -> List[SubTask]:
        """规则模板分解：按任务关键词命中流水线"""
        for template in _RULE_TEMPLATES:
            if any(kw in task for kw in template["keywords"]):
                names = template["subtasks"]
                return [
                    SubTask(
                        name=name,
                        description=desc,
                        depends_on=[] if i == 0 else [names[i - 1][0]],
                        depth=depth,
                    )
                    for i, (name, desc) in enumerate(names)
                ]
        # 兜底：两段式（准备 → 执行）
        return [
            SubTask(name="准备",
                    description=f"为「{task}」准备输入与前置条件", depth=depth),
            SubTask(name="执行",
                    description=f"实施「{task}」并输出结果",
                    depends_on=["准备"], depth=depth),
        ]

    # ---------- 依赖自动识别 ----------
    def _link_dependencies(self, subtasks: List[SubTask]) -> None:
        """依赖清洗：剔除未知依赖；空依赖自动挂到前序兄弟"""
        names = {s.name for s in subtasks}
        for i, st in enumerate(subtasks):
            st.depends_on = [d for d in st.depends_on
                             if d in names and d != st.name]
            if not st.depends_on and i > 0:
                st.depends_on = [subtasks[i - 1].name]

    @staticmethod
    def _is_complex(subtask: SubTask) -> bool:
        return any(kw in subtask.name
                   for kw in ("分析", "设计", "实现", "优化", "评估"))

    # ---------- 人工干预 ----------
    def add_subtask(self, name: str, *, description: str = "",
                    depends_on: Optional[List[str]] = None,
                    depth: int = 1) -> SubTask:
        """人工补充子任务（重名抛 ValueError）"""
        if any(s.name == name for s in self._last):
            raise ValueError(f"子任务已存在: {name}")
        st = SubTask(name=name, description=description,
                     depends_on=list(depends_on or []), depth=depth)
        self._last.append(st)
        self._link_dependencies(self._last)
        return st

    def remove_subtask(self, name: str) -> bool:
        """人工删除子任务；连带清除其它任务的引用"""
        before = len(self._last)
        self._last = [s for s in self._last if s.name != name]
        if len(self._last) != before:
            for s in self._last:
                s.depends_on = [d for d in s.depends_on if d != name]
            return True
        return False

    def update_subtask(self, name: str, *, description: Optional[str] = None,
                       depends_on: Optional[List[str]] = None) -> bool:
        """人工修改子任务的描述/依赖"""
        for s in self._last:
            if s.name == name:
                if description is not None:
                    s.description = description
                if depends_on is not None:
                    s.depends_on = list(depends_on)
                self._link_dependencies(self._last)
                return True
        return False

    def replace_decomposition(self, subtasks: List[SubTask]) -> None:
        """人工整体替换分解结果（人工干预的最高优先级）"""
        self._link_dependencies(subtasks)
        self._last = subtasks