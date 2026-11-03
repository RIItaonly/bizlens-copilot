"""规则路由器：把问题解析成"指标 + 维度 + 取数方式"，再交给语义层执行。

这是 baseline v0，**不含任何大模型**。它的作用是先把"确定性骨架"跑通：
- 口径、聚合、过滤全部由语义层完成；
- 路由器只负责选指标、选维度、选排序，不做任何计算。

它只能覆盖受控的问法（T1/T2），这是刻意的：先证明"数是对的"，
再讨论"听懂人话"。后续接入 LLM 时，替换的只是这一层。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

METRIC_RULES = [
    ("人均产值", "revenue_per_head"),
    ("在职人数", "headcount"),
    ("人数", "headcount"),
    ("回款率", "collection_rate"),
    ("毛利率", "gross_margin"),
    ("进度偏差", "schedule_variance"),
    ("合同额", "contract_amount"),
    ("成本", "project_cost"),
    ("收入", "recognized_revenue"),
]

REGIONS = ["华东", "华南", "华北"]
YEAR_PERIODS = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
QUARTERS = {f"Q{i}": f"2025Q{i}" for i in range(1, 5)}


@dataclass
class Plan:
    kind: str                     # value | rank | qoq_rank
    metric: str
    periods: list[str]
    region: str | None = None
    project: str | None = None
    n: int = 1
    asc: bool = False
    by: str = "project"
    raw_question: str = ""
    notes: list[str] = field(default_factory=list)


def _metric(q: str) -> str | None:
    for key, metric in METRIC_RULES:
        if key in q:
            return metric
    return None


def _periods(q: str) -> list[str]:
    m = re.search(r"Q([1-4])", q)
    if m:
        return [QUARTERS[f"Q{m.group(1)}"]]
    if any(k in q for k in ("全年", "年度", "累计", "全年合计")):
        return YEAR_PERIODS
    return YEAR_PERIODS


def plan(question: str) -> Plan:
    q = question.replace(" ", "")
    metric = _metric(q)
    if metric is None:
        raise ValueError(f"无法识别指标：{question}")

    periods = _periods(q)
    region = next((r for r in REGIONS if r in q), None)
    pm = re.search(r"(P-\d{3})", q)
    project = pm.group(1) if pm else None

    by = "region" if ("区域" in q and "项目" not in q) else "project"
    if metric == "revenue_per_head":
        by = "region"

    # 排名类问题
    n_m = re.search(r"(\d+)\s*个", q)
    if n_m or ("最高" in q or "最低" in q):
        n = int(n_m.group(1)) if n_m else 1
        asc = "最低" in q or "降幅" in q
        if "环比" in q and ("降幅" in q or "增幅" in q or "升幅" in q):
            return Plan("qoq_rank", metric, periods, region, project, n, asc, by, question)
        return Plan("rank", metric, periods, region, project, n, asc, by, question)

    return Plan("value", metric, periods, region, project, 1, False, by, question)


def _shape(k, name, metric, value, by, extra=None):
    """表格行统一用"真实字段名"，方便评测按列比对。"""
    row = {("project_code" if by == "project" else "region_name"): k,
           "project_name" if by == "project" else "region_name": name,
           metric: value}
    if extra:
        row.update(extra)
    return row


def execute(layer, p: Plan):
    if p.kind == "value":
        return layer.value(p.metric, p.periods, p.region, p.project)

    if p.kind == "rank":
        rows = layer.rank(p.metric, p.periods, p.n, p.asc, p.by)
        out = []
        for r in rows:
            k = r["k"]
            name = layer.project_name(k) if p.by == "project" else k
            out.append(_shape(k, name, p.metric, r["v"], p.by))
        return out

    prev = {"2025Q4": "2025Q3", "2025Q3": "2025Q2",
            "2025Q2": "2025Q1", "2025Q1": "2024Q4"}[p.periods[0]]
    rows = layer.qoq_change(p.metric, p.periods[0], prev, p.n, p.asc, p.by)
    out = []
    for r in rows:
        k = r["k"]
        name = layer.project_name(k) if p.by == "project" else k
        out.append(_shape(k, name, p.metric, r["v"], p.by,
                          extra={"qoq_change": r["v"]}))
    return out
