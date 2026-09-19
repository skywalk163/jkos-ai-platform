#!/usr/bin/env bash
# run_coverage.sh — M6 覆盖率报告生成脚本
# 用法: ./scripts/run_coverage.sh [pytest 选项]
# 输出: coverage_report.html (HTML 报告) + 终端覆盖率摘要

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"

echo "=== DSH AI Platform 覆盖率测试 ==="
echo "工作目录: $(pwd)"
echo

# 运行测试并收集覆盖率
$PYTHON -m pytest "$@" \
    --cov=jkos_core \
    --cov-report=term-missing \
    --cov-report=html:coverage_report \
    --tb=short

echo
echo "=== HTML 报告已生成 ==="
echo "文件: coverage_report.html"
echo "打开: file://$(pwd)/coverage_report.html"
