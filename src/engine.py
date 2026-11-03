"""统一问答引擎：把问题分派到取数 / 筛选 / 归因 / 制度 / 报告五条路径。

设计原则：
- 路由只决定"用哪条路"，不产生数字；
- 每条路都返回结构化数据 + 引用 + SQL，便于回放；
- 规则路由覆盖受控问法，自由问法交给 LLM（见 intent/）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import analytics
import report
import router
from policy import PolicyIndex

PERIOD_KEYS = ["2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4"]
PREV = {"2025Q4": "2025Q3", "2025Q3": "2025Q2",
        "2025Q2": "2025Q1", "2025Q1": "2024Q4"}

COND_PATTERNS = [
    (r"毛利率(?:低于|小于)\s*(-?\d+(?:\.\d+)?)\s*%", "gross_margin", "<", 0.01),
    (r"回款率(?:低于|小于)\s*(\d+(?:\.\d+)?)\s*%", "collection_rate", "<", 0.01),
    (r"进度(?:偏差|滞后)[^\d]{0,6}(?:低于|超过)\s*(-?\d+(?:\.\d+)?)\s*%",
     "schedule_variance", "<", 0.01),
    (r"成本(?:超过|大于)\s*(\d+(?:\.\d+)?)\s*万", "project_cost", ">", 10000),
]


@dataclass
class Answer:
    kind: str
    data: object = None
    text: str = ""
    cites: list = field(default_factory=list)
    sql: str = ""


def _periods(q: str) -> list[str]:
    m = re.search(r"Q([1-4])", q)
    if m:
        return [f"2025Q{m.group(1)}"]
    return ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]


def _region(q: str):
    for r in ["华东", "华南", "华北"]:
        if r in q:
            return r
    return None


def _project(q: str):
    m = re.search(r"(P-\d{3})", q)
    return m.group(1) if m else None


def classify(q: str) -> str:
    policy_kw = any(k in q for k in [
        "制度", "规定", "上限", "流程", "分摊", "计提", "审批", "口径", "出具",
        "算不算", "后果", "需要", "触发", "什么时候", "有没有", "是否可以",
        "怎么处理", "会有什么"])
    if any(k in q for k in ["生成", "经营简报", "专项报告", "专项说明",
                            "年度总结", "一页纸", "结构分析", "摘要", "改善建议"]):
        return "report"
    if "有哪些" in q or "哪些项目" in q or "哪些合同" in q:
        return "filter"
    if "哪些" in q:
        return "table"
    attribution_kw = any(k in q for k in [
        "为什么", "主因", "贡献", "拆解", "造成", "拖累", "占比最高", "占比最大",
        "比例最高", "增幅最大", "降幅最大", "变化如何", "差在"])
    if attribution_kw and not policy_kw:
        return "attribution"
    if policy_kw:
        return "policy"
    return "table"


# ------------------------------------------------------------------ filter
def _conditions(q):
    conds = []
    for pat, metric, op, scale in COND_PATTERNS:
        m = re.search(pat, q)
        if m:
            value = float(m.group(1)) * scale
            # “滞后超过 20%” = 进度偏差 < -0.20；“偏差低于 -20%” 自带负号
            if metric == "schedule_variance" and "滞后" in m.group(0) and value > 0:
                value = -value
            conds.append((metric, op, value))
    if "负毛利" in q:
        conds.append(("gross_margin", "<", 0))
    return conds


def _filter(layer, q):
    periods = _periods(q)
    if "首付款" in q:
        m = re.search(r"超过\s*(\d+)\s*%", q)
        thr = float(m.group(1)) / 100 if m else 0.30
        rows = layer.contracts_over_ratio(thr)
        cites = ["policy:P-07", "table:contract"]
        return Answer("table", rows, cites=cites, sql=layer.last_sql)

    if "没有确认收入" in q:
        rows = layer.projects_without_revenue(periods[0])
        return Answer("table", rows, cites=["metric:recognized_revenue", "table:project"],
                      sql=layer.last_sql)

    if "环比" in q and ("下滑" in q or "下降" in q):
        m = re.search(r"超过\s*(\d+)\s*%", q)
        thr = float(m.group(1)) / 100 if m else 0.20
        now = {r["project_code"]: r for r in layer.project_table([periods[0]])}
        prev = {r["project_code"]: r for r in layer.project_table([PREV[periods[0]]])}
        rows = []
        for code, r in now.items():
            if code in prev and prev[code]["revenue"]:
                chg = (r["revenue"] - prev[code]["revenue"]) / prev[code]["revenue"]
                if chg < -thr:
                    rows.append({**r, "qoq_change": chg})
        rows.sort(key=lambda x: x["qoq_change"])
        return Answer("table", rows, cites=["metric:recognized_revenue", "table:revenue"],
                      sql=layer.last_sql)

    conds = _conditions(q)
    if not conds:
        raise ValueError(f"没有识别出筛选条件：{q}")
    rows = layer.project_table(periods, conds)
    order_metric = conds[0][0]
    rows.sort(key=lambda r: (r[order_metric] is None, r[order_metric]))
    policy_map = {"gross_margin": "policy:P-04", "collection_rate": "policy:P-05",
                  "schedule_variance": "policy:P-06"}
    cites = [f"metric:{c[0]}" for c in conds] + ["table:revenue", "table:cost"]
    cites += [policy_map[c[0]] for c in conds if c[0] in policy_map]
    return Answer("table", rows, cites=cites, sql=layer.last_sql)


# ------------------------------------------------------------------ attribution
def _attribution(layer, q):
    project = _project(q)
    region = _region(q)
    period = _periods(q)[0]
    prev = PREV[period]

    if "回款率" in q and "差在" in q:
        year = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
        p = project
        if not p:
            rows = [r for r in layer.project_table(year) if r["collection_rate"] is not None]
            p = min(rows, key=lambda r: r["collection_rate"])["project_code"]
        g = analytics.collection_gap(layer, p, year)
        text = (f"{p} 累计开票 ¥{g['invoiced']:,.0f}，累计回款 ¥{g['collected']:,.0f}，"
                f"回款率 {g['rate']:.2%}；未回款 ¥{g['gap']:,.0f}。"
                f"缺口在回款端：开票已发生但未到账。")
        return Answer("structured", None, text, ["metric:collection_rate", "table:revenue"])

    if "人工成本占" in q:
        r = analytics.labor_share(layer, ["2025Q1", "2025Q2", "2025Q3", "2025Q4"])
        text = f"{r['project']}（{layer.project_name(r['project'])}）人工成本占比 {r['share']:.2%}。"
        return Answer("structured", None, text, ["metric:project_cost", "table:cost"])

    if "增幅最大" in q:
        r = analytics.growth_top(layer, period, prev)
        text = (f"{r['project']}（{layer.project_name(r['project'])}）环比 {r['growth']:+.2%}，"
                f"从 ¥{r['prev']:,.0f} 增至 ¥{r['now']:,.0f}。")
        return Answer("structured", None, text, ["metric:recognized_revenue", "table:revenue"])

    if "降幅最大" in q:
        r = analytics.margin_drop_top(layer, period, prev)
        d = analytics.margin_decompose(layer, r["project"], period, prev)
        text = (f"{d['project']}（{layer.project_name(d['project'])}）毛利率 "
                f"{d['margin_prev']:.2%} → {d['margin_now']:.2%}；"
                f"收入端 {d['revenue_effect']:+.2%}，成本端 {d['cost_effect']:+.2%}。")
        return Answer("structured", None, text,
                      ["metric:gross_margin", "table:revenue", "table:cost"])

    if "收入端" in q or "各贡献" in q:
        p = project
        if p:
            d = analytics.margin_decompose(layer, p, period, prev)
        else:
            d = analytics.company_decompose(layer, period, prev)
        text = (f"毛利率 {d['margin_prev']:.2%} → {d['margin_now']:.2%}；"
                f"收入端 {d['revenue_effect']:+.2%}，成本端 {d['cost_effect']:+.2%}。")
        return Answer("structured", None, text,
                      ["metric:gross_margin", "table:revenue", "table:cost"])

    if "四类成本" in q:
        s = analytics.cost_structure(layer, ["2025Q1", "2025Q2", "2025Q3", "2025Q4"])
        text = (f"{s['order'][0]} 占比 {s['shares'][s['order'][0]]:.2%}，"
                f"四类合计 ¥{s['total']:,.0f}。")
        return Answer("structured", None, text, ["metric:project_cost", "table:cost"])

    if region and "收入" in q and ("造成" in q or "贡献" in q):
        c = analytics.region_contribution(layer, region, period, prev)
        code, delta = c["top"]
        text = (f"{region} 收入 ¥{c['revenue_prev']:,.0f} → ¥{c['revenue_now']:,.0f}；"
                f"下降贡献最大的是 {code}（{layer.project_name(code)}），环比 ¥{delta:,.0f}。")
        return Answer("structured", None, text, ["metric:recognized_revenue", "table:revenue"])

    if "成本" in q and ("变化" in q or "哪一类" in q):
        now_ct = layer.cost_by_type([period])
        prev_ct = layer.cost_by_type([prev])
        diffs = {k: now_ct.get(k, 0) - prev_ct.get(k, 0)
                 for k in set(now_ct) | set(prev_ct)}
        key = max(diffs, key=lambda k: abs(diffs[k]))
        total_now, total_prev = sum(now_ct.values()), sum(prev_ct.values())
        text = (f"总成本 ¥{total_prev:,.0f} → ¥{total_now:,.0f}"
                f"（{(total_now / total_prev - 1):+.1%}）；变化最大的是「{key}」"
                f"¥{diffs[key]:+,.0f}。")
        return Answer("structured", None, text, ["metric:project_cost", "table:cost"])

    if "拖累" in q or ("区域" in q and "最低" in q):
        rm = analytics.region_margin(layer, period)
        worst = rm[0]
        rows = [r for r in layer.project_table([period]) if r["region_name"] == worst["region"]]
        worst_p = min(rows, key=lambda r: r["gross_margin"])
        text = (f"{worst['region']} 区域毛利率 {worst['margin']:.2%} 最低；"
                f"拖累最大的是 {worst_p['project_code']}（{worst_p['project_name']}），"
                f"毛利率 {worst_p['gross_margin']:.2%}。")
        return Answer("structured", None, text,
                      ["metric:gross_margin", "table:revenue", "table:cost"])

    if "收入端" in q or "各贡献" in q:
        d = analytics.company_decompose(layer, period, prev)
        text = (f"公司毛利率 {d['margin_prev']:.2%} → {d['margin_now']:.2%}；"
                f"收入端 {d['revenue_effect']:+.2%}，成本端 {d['cost_effect']:+.2%}。")
        return Answer("structured", None, text,
                      ["metric:gross_margin", "table:revenue", "table:cost"])

    if "区域" in q and "贡献" in q:
        c = analytics.region_contribution(layer, region, period, prev)
        code, delta = c["top"]
        text = (f"{region} 收入 ¥{c['revenue_prev']:,.0f} → ¥{c['revenue_now']:,.0f}；"
                f"下降贡献最大的是 {code}，环比 ¥{delta:,.0f}。")
        return Answer("structured", None, text, ["metric:recognized_revenue", "table:revenue"])

    # 默认：项目毛利率两因素拆解
    p = project
    if not p:
        r = analytics.margin_drop_top(layer, period, prev)
        p = r["project"]
    d = analytics.margin_decompose(layer, p, period, prev)
    text = (f"{p}（{layer.project_name(p)}）毛利率 {d['margin_prev']:.2%} → "
            f"{d['margin_now']:.2%}；收入端 {d['revenue_effect']:+.2%}，"
            f"成本端 {d['cost_effect']:+.2%}；成本变化主要来自「{d['driver']}」"
            f"¥{d['driver_delta']:,.0f}。")
    return Answer("structured", None, text,
                  ["metric:gross_margin", "table:revenue", "table:cost"])


# ------------------------------------------------------------------ policy
def _policy(q):
    top = PolicyIndex().search(q)[0]
    text = f"【{top['id']} {top['title']}】\n{top['text']}"
    return Answer("text", None, text, [f"policy:{top['id']}"])


# ------------------------------------------------------------------ report
def _report(layer, q):
    period = _periods(q)[0]
    project = _project(q)
    region = _region(q)
    year = "全年" in q or "年度" in q

    if "一页纸" in q:
        text, cites = report.one_pager(layer, period)
    elif "回款" in q:
        text, cites = report.collection_risk(layer)
    elif "进度" in q or "滞后" in q:
        text, cites = report.lag_special(layer, period)
    elif "合同" in q:
        text, cites = report.contract_structure(layer)
    elif "低毛利" in q or ("毛利率低于" in q and "专项" in q):
        text, cites = report.low_margin_special(layer, period)
    elif region and year:
        text, cites = report.region_annual(layer, region)
    elif region:
        text, cites = report.region_report(layer, region, period, PREV[period])
    elif year:
        text, cites = report.annual_summary(layer)
    elif project:
        text, cites = report.project_brief(layer, project, period, PREV[period])
    else:
        text, cites = report.one_pager(layer, period)
    return Answer("report", None, text, cites, sql=layer.last_sql)


# ------------------------------------------------------------------ entry
def ask(layer, question: str) -> Answer:
    kind = classify(question)
    if kind == "filter":
        return _filter(layer, question)
    if kind == "attribution":
        return _attribution(layer, question)
    if kind == "policy":
        return _policy(question)
    if kind == "report":
        return _report(layer, question)

    p = router.plan(question)
    data = router.execute(layer, p)
    from common import yamlish
    import os
    doc = yamlish.loads(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "metrics.yaml"), encoding="utf-8").read())
    tables = {m["id"]: (m.get("source_tables") or []) for m in doc["metrics"]}
    cites = [f"metric:{p.metric}"] + [f"table:{t}" for t in tables.get(p.metric, [])]
    return Answer("table" if isinstance(data, list) else "value", data, cites=cites,
                  sql=layer.last_sql)
