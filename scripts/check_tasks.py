"""轻量自检：任务集结构、引用完整性、覆盖度。

不依赖 PyYAML，用正则做结构检查（任务集是本项目自己生成的，格式可控）。

用法：
    python scripts/check_tasks.py
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(ROOT, "eval", "tasks_v1.yaml")
METRICS = os.path.join(ROOT, "data", "metrics.yaml")
POLICY = os.path.join(ROOT, "data", "policy.md")

TABLES = {"revenue", "cost", "plan", "project", "headcount", "contract", "org"}

errors: list[str] = []
warnings: list[str] = []


def main() -> int:
    text = open(TASKS, encoding="utf-8").read()
    metrics_text = open(METRICS, encoding="utf-8").read()
    policy_text = open(POLICY, encoding="utf-8").read()

    metric_ids = set(re.findall(r"^\s{2}- id: (\w+)$", metrics_text, re.M))
    policy_ids = set(re.findall(r"^## (P-\d+)", policy_text, re.M))

    ids = re.findall(r'^\s{2}- id: "(T\d-\d{3})"', text, re.M)
    cats = re.findall(r'^\s{4}category: "(T\d)"', text, re.M)

    if len(ids) != 60:
        errors.append(f"任务数应为 60，实际 {len(ids)}")
    if len(set(ids)) != len(ids):
        dup = [k for k, v in Counter(ids).items() if v > 1]
        errors.append(f"任务 id 重复：{dup}")
    if Counter(cats) != Counter({f"T{i}": 10 for i in range(1, 7)}):
        errors.append(f"分类覆盖不均：{dict(Counter(cats))}")

    if len(metric_ids) != 9:
        warnings.append(f"指标数应为 9，实际 {len(metric_ids)}")

    for ref in set(re.findall(r'"(metric:\w+)"', text)):
        if ref.split(":")[1] not in metric_ids:
            errors.append(f"引用了不存在的指标：{ref}")
    for ref in set(re.findall(r'"(table:\w+)"', text)):
        if ref.split(":")[1] not in TABLES:
            errors.append(f"引用了不存在的表：{ref}")
    for ref in set(re.findall(r'"(policy:P-\d+)"', text)):
        if ref.split(":")[1] not in policy_ids:
            errors.append(f"引用了不存在的制度条款：{ref}")

    # 每题都必须有 must_cite 与 notes
    if text.count("must_cite:") != len(ids):
        errors.append(f"must_cite 数量 {text.count('must_cite:')} 与任务数 {len(ids)} 不一致")
    if text.count("notes:") != len(ids):
        errors.append(f"notes 数量 {text.count('notes:')} 与任务数 {len(ids)} 不一致")

    print(f"任务数：{len(ids)}　分类：{dict(Counter(cats))}")
    print(f"指标：{len(metric_ids)}　制度条款：{len(policy_ids)}")
    if warnings:
        for w in warnings:
            print("WARN:", w)
    if errors:
        for e in errors:
            print("ERROR:", e)
        return 1
    print("OK：结构、引用、覆盖度检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
