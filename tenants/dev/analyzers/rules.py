"""规则扫描器（M1 任务 1.2 之一）——对 diff 新增行做静态规则匹配

设计：
  - 只扫 patch 中 "+" 开头的新增行（排除 +++ 文件头），行号由 hunk 头 @@ -a,b +c,d @@ 推算；
  - 规则表 RULES 声明式维护，每条规则含 code/severity/regex/message；
  - 纯函数、零依赖，单测可直接构造 files 列表喂入。

severity 语义（与审批风险对齐）：high 阻断级 / medium 需关注 / low 提示。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Pattern

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class Rule:
    code: str
    severity: str        # high / medium / low
    pattern: Pattern[str]
    message: str


RULES: List[Rule] = [
    Rule("PY_EVAL_EXEC", "high",
         re.compile(r"\b(?:eval|exec)\s*\("),
         "使用 eval/exec 存在代码注入风险"),
    Rule("PY_SHELL_TRUE", "high",
         re.compile(r"shell\s*=\s*True"),
         "subprocess shell=True 存在命令注入风险"),
    Rule("HARDCODED_SECRET", "high",
         re.compile(r"(?:password|passwd|secret|api_key|apikey|token|access_key)\s*=\s*[\"'][^\"']{8,}[\"']",
                    re.IGNORECASE),
         "疑似硬编码密钥/口令"),
    Rule("SQL_FSTRING", "medium",
         re.compile(r"\b(?:execute|executemany)\s*\(\s*f[\"']"),
         "SQL 使用 f-string 拼接，存在注入风险，应参数化"),
    Rule("BARE_EXCEPT", "medium",
         re.compile(r"except\s*:"),
         "裸 except 会吞掉 KeyboardInterrupt/SystemExit，应捕获具体异常"),
    Rule("HTTP_NO_VERIFY", "medium",
         re.compile(r"verify\s*=\s*False"),
         "HTTPS 请求关闭证书校验，存在中间人风险"),
    Rule("TODO_FIXME", "low",
         re.compile(r"\b(?:TODO|FIXME)\b"),
         "遗留 TODO/FIXME 待办标记"),
    Rule("PRINT_DEBUG", "low",
         re.compile(r"^\s*print\s*\("),
         "调试 print 输出，建议改用 logger"),
]


def scan_patch(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """扫描 diff 文件列表，返回规则命中列表。

    files 元素结构（GitAdapter.collect_diff 输出）: {path, additions, deletions, patch}
    返回 finding: {code, severity, path, line, message, snippet}
    """
    findings: List[Dict[str, Any]] = []
    for file_info in files or []:
        path = file_info.get("path", "")
        patch = file_info.get("patch") or ""
        new_line_no = 0
        for raw_line in patch.splitlines():
            hunk = HUNK_RE.match(raw_line)
            if hunk:
                new_line_no = int(hunk.group(1)) - 1   # hunk 头后第一个新行 = 起始行
                continue
            if raw_line.startswith("+++") or raw_line.startswith("---") \
                    or raw_line.startswith("diff --git") or raw_line.startswith("index "):
                continue
            if raw_line.startswith("+"):
                new_line_no += 1
                content = raw_line[1:]
                for rule in RULES:
                    if rule.pattern.search(content):
                        findings.append({
                            "code": rule.code, "severity": rule.severity,
                            "path": path, "line": new_line_no,
                            "message": rule.message,
                            "snippet": content.strip()[:200],
                        })
            elif raw_line.startswith("-"):
                continue          # 删除行不影响新文件行号
            else:
                new_line_no += 1  # 上下文行
    return findings


def summarize_findings(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    """按严重度统计命中数（报告用）"""
    summary = {"high": 0, "medium": 0, "low": 0, "total": len(findings)}
    for f in findings:
        sev = f.get("severity", "low")
        summary[sev] = summary.get(sev, 0) + 1
    return summary
