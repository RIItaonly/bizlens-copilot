"""根据合成种子数据生成 30 条评测任务（W1 初稿）。

ground truth 全部由数据计算得出，不由人手工填写，避免"自己出题自己满分"。

用法：
    python scripts/gen_seed.py      # 先生成数据
    python scripts/build_tasks.py   # 再生成 tasks_v0.yaml
"""

from __future__ import annotations

import json
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "data", "seed")
OUT = os.path.join(ROOT, "eval", "tasks_v1.yaml")


# ---------------------------------------------------------------- helpers
def yaml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, float):
        return repr(round(v, 6))
    return str(v)


def yaml_dump(obj, indent=0):
    sp = "  " * indent
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{sp}{k}:")
                lines.append(yaml_dump(v, indent + 1))
            elif isinstance(v, (dict, list)):
                lines.append(f"{sp}{k}: " + ("{}" if isinstance(v, dict) else "[]"))
            else:
                lines.append(f"{sp}{k}: {yaml_scalar(v)}")
        return "\n".join(lines)
    if isinstance(obj, list):
        lines = []
        for item in obj:
            if isinstance(item, dict):
                sub = yaml_dump(item, indent + 1)
                lines.append(f"{sp}- " + sub.lstrip())
            else:
                lines.append(f"{sp}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{sp}{yaml_scalar(obj)}"


def money(x):
    return int(round(float(x)))


def ratio(x):
    return round(float(x), 4)


# ---------------------------------------------------------------- data
def load():
    rev = pd.read_csv(os.path.join(SEED, "revenue.csv"))
    cost = pd.read_csv(os.path.join(SEED, "cost.csv"))
    plan = pd.read_csv(os.path.join(SEED, "plan.csv"))
    proj = pd.read_csv(os.path.join(SEED, "project.csv"))
    head = pd.read_csv(os.path.join(SEED, "headcount.csv"))
    cont = pd.read_csv(os.path.join(SEED, "contract.csv"))

    rev = rev.merge(proj[["project_code", "project_name", "region_name", "status"]],
                    on="project_code")
    cost = cost.merge(proj[["project_code", "region_name"]], on="project_code")
    cont = cont.merge(proj[["project_code", "region_name"]], on="project_code", how="left")
    return rev, cost, plan, proj, head, cont


def main():
    rev, cost, plan, proj, head, cont = load()
    Q4, Q3 = "2025Q4", "2025Q3"

    # 项目 × 期间 收入/成本
    pr = rev.pivot_table(index="project_code", columns="period",
                         values="recognized_revenue", aggfunc="sum").fillna(0)
    pc = cost.pivot_table(index="project_code", columns="period",
                          values="amount", aggfunc="sum").fillna(0)
    meta = proj.set_index("project_code")[["project_name", "region_name", "status"]]

    def margin(period):
        r, c = pr[period], pc[period]
        return (r - c) / r.replace(0, pd.NA)

    m4, m3 = margin(Q4), margin(Q3)

    # 2025 累计回款率
    r25 = rev[rev.period.str.startswith("2025")]
    coll = r25.groupby("project_code").agg(inv=("invoiced_amount", "sum"),
                                           col=("collected_amount", "sum"))
    coll["rate"] = coll.col / coll.inv.replace(0, pd.NA)

    # Q4 进度偏差
    p4 = plan[plan.period == Q4].groupby("project_code")["planned_revenue"].sum()
    sv = (pr[Q4] - p4.reindex(pr.index).fillna(0)) / p4.reindex(pr.index).replace(0, pd.NA)

    def dfm(series, cols=("project_code", "project_name", "region_name")):
        d = series.rename("v").to_frame().join(meta)
        d = d.reset_index().rename(columns={"index": "project_code"})
        return d

    # Q4 有新收入的项目（避免把未开工项目当成 0 毛利）
    active4 = pr[Q4][pr[Q4] > 0].index
    q4 = dfm(m4).set_index("project_code").loc[active4].reset_index()

    tasks = []

    def add(tid, cat, cname, diff, q, expected, cite, focus, notes):
        # must_cite 统一放在任务层，避免 expected 内重复定义
        if isinstance(expected, dict) and "must_cite" in expected:
            expected = {k: v for k, v in expected.items() if k != "must_cite"}
        tasks.append(dict(id=tid, category=cat, category_name=cname, difficulty=diff,
                          question=q, expected=expected, must_cite=cite,
                          rubric_focus=focus, notes=notes))

    # ---------------- T1 单指标查询 ----------------
    east = rev[(rev.period == Q4) & (rev.region_name == "华东")].recognized_revenue.sum()
    add("T1-001", "T1", "单指标查询", "easy",
        "2025 年 Q4 华东区域的确认收入是多少？",
        {"type": "scalar", "value": money(east), "unit": "元", "tolerance": 0.005},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性", "引用可追溯"],
        "口径：不含税、按项目归属区域汇总。答成含税或按客户归属均判错。")

    total25 = rev[rev.period.str.startswith("2025")].recognized_revenue.sum()
    add("T1-002", "T1", "单指标查询", "easy",
        "2025 年全年公司的确认收入合计是多少？",
        {"type": "scalar", "value": money(total25), "unit": "元", "tolerance": 0.005},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性", "引用可追溯"],
        "四个季度相加；不能把 2024Q4 算进去。")

    hc = int(head[(head.period == Q4) & (head.region_code == "R2")].headcount.iloc[0])
    add("T1-003", "T1", "单指标查询", "easy",
        "2025 年 Q4 华南区域的期末在职人数是多少？",
        {"type": "scalar", "value": hc, "unit": "人", "tolerance": 0},
        ["metric:headcount", "table:headcount"],
        ["数值正确性"],
        "人数是人均产值指标的分母，必须与指标口径一致（期末，不是平均）。")

    newc = cont[(cont.signed_period == Q4) & (cont.status == "有效")].amount.sum()
    add("T1-004", "T1", "单指标查询", "medium",
        "2025 年 Q4 新签合同额是多少？",
        {"type": "scalar", "value": money(newc), "unit": "元", "tolerance": 0.005},
        ["metric:contract_amount", "table:contract"],
        ["数值正确性", "引用可追溯"],
        "只统计 Q4 签署且状态为有效的合同；无效的补充协议不能计入。")

    top_proj = pr[Q4].sort_values(ascending=False).index[0]
    top_name = meta.loc[top_proj, "project_name"]
    add("T1-005", "T1", "单指标查询", "easy",
        f"{top_proj}（{top_name}）在 2025 年 Q4 的确认收入是多少？",
        {"type": "scalar", "value": money(pr.loc[top_proj, Q4]), "unit": "元",
         "tolerance": 0.005},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性", "引用可追溯"],
        "考察指代消解：用户用项目名而非编码提问时能否正确映射。")

    # ---------------- T2 对比与排名 ----------------
    low5 = q4.sort_values("v").head(5)
    add("T2-001", "T2", "对比与排名", "medium",
        "2025 年 Q4 毛利率最低的 5 个项目是哪些？给出项目编码、名称和毛利率。",
        {"type": "table", "columns": ["project_code", "project_name", "gross_margin"],
         "order": "按毛利率升序", "tolerance": 0.001,
         "rows": [[r.project_code, r.project_name, ratio(r.v)] for r in low5.itertuples()]},
        ["metric:gross_margin", "table:revenue", "table:cost"],
        ["数值正确性", "归因有效性"],
        "负毛利项目必须排在前面；未开工项目不应出现。")

    rev25 = r25.groupby("project_code").recognized_revenue.sum().sort_values(ascending=False).head(3)
    add("T2-002", "T2", "对比与排名", "easy",
        "2025 年全年确认收入最高的 3 个项目是哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "recognized_revenue"],
         "order": "按收入降序",
         "rows": [[c, meta.loc[c, "project_name"], money(v)] for c, v in rev25.items()]},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性"],
        "全年累计，不是单季度。")

    cl = coll.dropna(subset=["rate"]).sort_values("rate").head(3)
    add("T2-003", "T2", "对比与排名", "medium",
        "2025 年累计回款率最低的 3 个项目是哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "collection_rate"],
         "order": "按回款率升序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(r.rate)] for c, r in cl.iterrows()]},
        ["metric:collection_rate", "table:revenue"],
        ["数值正确性", "引用可追溯"],
        "分母是开票不是收入；统计区间为 2025 年初至 Q4。")

    q3q4 = pd.DataFrame({"q3": pr[Q3], "q4": pr[Q4]})
    q3q4 = q3q4[(q3q4.q3 > 0) & (q3q4.q4 > 0)]
    q3q4["chg"] = q3q4.q4 / q3q4.q3 - 1
    drop3 = q3q4.sort_values("chg").head(3)
    add("T2-004", "T2", "对比与排名", "medium",
        "2025 年 Q4 确认收入环比降幅最大的 3 个项目是哪些？给出环比变化率。",
        {"type": "table", "columns": ["project_code", "project_name", "qoq_change"],
         "order": "按环比变化率升序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(r.chg)] for c, r in drop3.iterrows()]},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性", "归因有效性"],
        "只在 Q3、Q4 都有收入的项目里比较；Q3 无收入的项目不参与排名。")

    by_region = r25.groupby("region_name").recognized_revenue.sum()
    h4 = head[head.period == Q4].set_index("region_code").headcount
    rc = {"华东": "R1", "华南": "R2", "华北": "R3"}
    per_head = {k: by_region[k] / h4[rc[k]] for k in by_region.index}
    best = max(per_head, key=per_head.get)
    add("T2-005", "T2", "对比与排名", "hard",
        "2025 年人均产值最高的区域是哪个？人均产值是多少？",
        {"type": "object", "properties": {"region_name": best,
                                          "revenue_per_head": money(per_head[best])},
         "unit": "元/人", "tolerance": 0.01},
        ["metric:revenue_per_head", "table:headcount"],
        ["数值正确性", "引用可追溯"],
        "分子是全年收入，分母是 Q4 期末人数；两者时点不同是口径规定，不是错误。")

    # ---------------- T3 异常识别 ----------------
    low10 = q4[q4.v < 0.10].sort_values("v")
    add("T3-001", "T3", "异常识别", "medium",
        "2025 年 Q4 毛利率低于 10% 的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "gross_margin"],
         "order": "按毛利率升序", "tolerance": 0.001,
         "rows": [[r.project_code, r.project_name, ratio(r.v)] for r in low10.itertuples()]},
        ["metric:gross_margin", "policy:P-04"],
        ["数值正确性", "引用可追溯"],
        "边界值等于 10% 的项目不计入（严格小于）。")

    lowc = coll[coll.rate < 0.60].sort_values("rate")
    add("T3-002", "T3", "异常识别", "medium",
        "2025 年累计回款率低于 60% 的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "collection_rate"],
         "order": "按回款率升序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(r.rate)] for c, r in lowc.iterrows()]},
        ["metric:collection_rate", "policy:P-05"],
        ["数值正确性", "引用可追溯"],
        "低于 50% 的项目按制度应计提坏账，回答里最好指出这一点（加分项，非必须）。")

    neg = q4[q4.v < 0].sort_values("v")
    add("T3-003", "T3", "异常识别", "easy",
        "2025 年 Q4 出现负毛利的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "gross_margin"],
         "order": "按毛利率升序", "tolerance": 0.001,
         "rows": [[r.project_code, r.project_name, ratio(r.v)] for r in neg.itertuples()]},
        ["metric:gross_margin", "policy:P-04"],
        ["数值正确性"],
        "负毛利必须保留负号；不能四舍五入成 0 或显示为 100%。")

    lag = sv.dropna()
    lag = lag[lag < -0.30].sort_values()
    add("T3-004", "T3", "异常识别", "hard",
        "2025 年 Q4 进度偏差低于 -30% 的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "schedule_variance"],
         "order": "按进度偏差升序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(v)] for c, v in lag.items()]},
        ["metric:schedule_variance", "policy:P-06"],
        ["数值正确性", "引用可追溯"],
        "计划收入为 0 的项目不参与（尚未排期，不能算滞后）。")

    both = q4.set_index("project_code").join(coll["rate"])
    cross = both[(both.v < 0.15) & (both.rate < 0.70)].sort_values("v")
    add("T3-005", "T3", "异常识别", "hard",
        "2025 年 Q4 毛利率低于 15%、且 2025 年累计回款率低于 70% 的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "gross_margin",
                                      "collection_rate"],
         "order": "按毛利率升序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(r.v), ratio(r.rate)]
                  for c, r in cross.iterrows()]},
        ["metric:gross_margin", "metric:collection_rate"],
        ["数值正确性", "引用可追溯"],
        "两个条件同时满足；这是“双高风险”清单，缺一个条件都算错。")

    # ---------------- T4 归因分析 ----------------
    # 先找"确实在下降"的项目，避免把毛利率上升的项目说成下降
    q3q4m_all = pd.DataFrame({"m3": m3, "m4": m4}).dropna()
    q3q4m_all = q3q4m_all.loc[q3q4m_all.index.isin(active4)]
    q3q4m_all["dm"] = q3q4m_all.m4 - q3q4m_all.m3
    decl = q3q4m_all[q3q4m_all.dm < 0].sort_values("dm")
    worst_drop = decl.index[0]
    target = decl.index[1] if len(decl) > 1 else decl.index[0]

    r3v, r4v = pr.loc[target, Q3], pr.loc[target, Q4]
    c3v, c4v = pc.loc[target, Q3], pc.loc[target, Q4]
    rev_eff = c3v * (1 / r3v - 1 / r4v) if r3v and r4v else 0
    cost_eff = -(c4v - c3v) / r4v if r4v else 0
    ctype4 = cost[(cost.period == Q4) & (cost.project_code == target)].groupby("cost_type").amount.sum()
    ctype3 = cost[(cost.period == Q3) & (cost.project_code == target)].groupby("cost_type").amount.sum()
    dc_type = (ctype4 - ctype3)
    pick_type = dc_type.abs().idxmax()
    pick_sign = "增加" if dc_type[pick_type] > 0 else "减少"

    add("T4-001", "T4", "归因分析", "hard",
        f"{target}（{meta.loc[target, 'project_name']}）2025 年 Q4 毛利率下降，"
        "主要是收入端还是成本端造成的？请给出拆解。",
        {"type": "structured",
         "key_facts": [
             f"Q3 毛利率 {ratio(m3[target])}，Q4 毛利率 {ratio(m4[target])}，变化 {ratio(m4[target] - m3[target])}",
             f"收入端影响 {ratio(rev_eff)}，成本端影响 {ratio(cost_eff)}",
             f"成本变化最大的是「{pick_type}」：{pick_sign} {money(abs(dc_type[pick_type]))} 元",
         ],
         "answer_hint": "成本端" if abs(cost_eff) >= abs(rev_eff) else "收入端"},
        ["metric:gross_margin", "table:cost", "table:revenue"],
        ["数值正确性", "归因有效性", "引用可追溯"],
        "必须做量价/成本拆解，不能只给结论；结论与拆解方向不一致即判错。")

    e3 = rev[(rev.period == Q3) & (rev.region_name == "华东")].groupby("project_code").recognized_revenue.sum()
    e4 = rev[(rev.period == Q4) & (rev.region_name == "华东")].groupby("project_code").recognized_revenue.sum()
    ed = (e4.reindex(e3.index.union(e4.index)).fillna(0)
          - e3.reindex(e3.index.union(e4.index)).fillna(0)).sort_values()
    top_down = ed.index[0]
    add("T4-002", "T4", "归因分析", "hard",
        "2025 年 Q4 华东区域确认收入环比下降，主要是哪个项目造成的？",
        {"type": "structured",
         "key_facts": [
             f"华东 Q3 收入 {money(e3.sum())}，Q4 收入 {money(e4.sum())}，"
             f"环比 {ratio(e4.sum() / e3.sum() - 1)}",
             f"下降贡献最大的项目是 {top_down}（{meta.loc[top_down, 'project_name']}），"
             f"环比减少 {money(-ed.iloc[0])} 元",
         ]},
        ["metric:recognized_revenue", "table:revenue"],
        ["归因有效性", "引用可追溯"],
        "要做项目级贡献度排序，不能只给区域汇总。")

    lowc_proj = coll.dropna(subset=["rate"]).rate.idxmin()
    inv_p = coll.loc[lowc_proj, "inv"]
    col_p = coll.loc[lowc_proj, "col"]
    add("T4-003", "T4", "归因分析", "medium",
        f"{lowc_proj}（{meta.loc[lowc_proj, 'project_name']}）2025 年累计回款率偏低，"
        "主要差在开票还是回款？还差多少钱？",
        {"type": "structured",
         "key_facts": [
             f"累计开票 {money(inv_p)} 元，累计回款 {money(col_p)} 元，回款率 {ratio(col_p / inv_p)}",
             f"未回款金额 {money(inv_p - col_p)} 元",
             "缺口在回款端：开票已发生但未到账",
         ]},
        ["metric:collection_rate", "table:revenue"],
        ["归因有效性", "数值正确性"],
        "区分“没开票”和“开了票没收到钱”，这是回款分析的关键。")

    ct4 = cost[cost.period == Q4].groupby("cost_type").amount.sum()
    ct3 = cost[cost.period == Q3].groupby("cost_type").amount.sum()
    cdelta = (ct4 - ct3)
    t_type = cdelta.abs().idxmax()
    t_sign = "上升" if cdelta[t_type] > 0 else "下降"
    add("T4-004", "T4", "归因分析", "medium",
        "2025 年 Q4 公司整体项目成本环比变化如何？哪一类成本变化幅度最大？",
        {"type": "structured",
         "key_facts": [
             f"Q4 总成本 {money(ct4.sum())}，Q3 总成本 {money(ct3.sum())}，"
             f"环比 {ratio(ct4.sum() / ct3.sum() - 1)}",
             f"变化幅度最大的是「{t_type}」：{t_sign} {money(abs(cdelta[t_type]))} 元",
         ]},
        ["metric:project_cost", "table:cost"],
        ["数值正确性", "归因有效性"],
        "四类成本要分别对比，不能只看总额；注意总额下降不等于每一类都下降。")

    r3w, r4w = pr.loc[worst_drop, Q3], pr.loc[worst_drop, Q4]
    c3w, c4w = pc.loc[worst_drop, Q3], pc.loc[worst_drop, Q4]
    rev_e = c3w * (1 / r3w - 1 / r4w) if r3w and r4w else 0
    cost_e = -(c4w - c3w) / r4w if r4w else 0
    add("T4-005", "T4", "归因分析", "hard",
        "2025 年 Q4 毛利率环比降幅最大的项目是哪个？降幅中收入端和成本端各占多少？",
        {"type": "structured",
         "key_facts": [
             f"项目 {worst_drop}（{meta.loc[worst_drop, 'project_name']}），"
             f"毛利率 {ratio(m3[worst_drop])} → {ratio(m4[worst_drop])}",
             f"收入端影响 {ratio(rev_e)}，成本端影响 {ratio(cost_e)}",
         ],
         "answer_hint": "成本端" if abs(cost_e) >= abs(rev_e) else "收入端"},
        ["metric:gross_margin", "table:revenue", "table:cost"],
        ["数值正确性", "归因有效性"],
        "先找降幅最大的项目，再做两因素拆解，两步都对才得分。")

    # ---------------- T5 制度与口径问答 ----------------
    add("T5-001", "T5", "制度与口径", "easy",
        "合同首付款比例最高可以是多少？超过上限需要谁审批？",
        {"type": "text",
         "key_points": ["不得超过合同金额的 30%", "超过需报分管副总经理审批",
                        "并在合同审批单中说明理由"],
         "must_cite": ["policy:P-07"]},
        ["policy:P-07"],
        ["引用可追溯", "数值正确性"],
        "制度型问题必须引用条款号，不能只凭常识回答。")

    add("T5-002", "T5", "制度与口径", "easy",
        "管理费用和销售费用是怎么分摊到区域的？",
        {"type": "text",
         "key_points": ["管理费用按当期确认收入的 8% 分摊",
                        "销售费用按当期确认收入的 5% 分摊",
                        "分摊仅用于管理分析，不改变法定报表"],
         "must_cite": ["policy:P-03", "metric:expense_ratio"]},
        ["policy:P-03"],
        ["引用可追溯", "数值正确性"],
        "注意区分“分摊值”与“实际发生额”，回答里说成实际支出即扣分。")

    add("T5-003", "T5", "制度与口径", "medium",
        "项目收入按什么方法确认？跨期调整怎么处理？",
        {"type": "text",
         "key_points": ["按完工百分比法，以累计已发生成本占预计总成本比例确认",
                        "跨期调整计入调整发生期间，不追溯重述历史报表"],
         "must_cite": ["policy:P-01", "metric:recognized_revenue"]},
        ["policy:P-01"],
        ["引用可追溯"],
        "考察是否理解“不追溯”这条口径决策，以及它对同比的影响。")

    add("T5-004", "T5", "制度与口径", "medium",
        "项目毛利率低于多少会触发专项复盘？低于多少需要书面说明？",
        {"type": "text",
         "key_points": ["低于 15% 需在月度经营会提交书面说明",
                        "低于 5%（含负毛利）触发专项复盘，30 日内出报告",
                        "连续两个季度低于 5% 暂停新签同类项目"],
         "must_cite": ["policy:P-04"]},
        ["policy:P-04"],
        ["数值正确性", "引用可追溯"],
        "两个阈值容易混（15% 与 5%），必须都答对。")

    add("T5-005", "T5", "制度与口径", "medium",
        "项目累计回款率低于 70% 会有什么后果？低于 50% 呢？",
        {"type": "text",
         "key_points": ["低于 70% 暂停该项目新签合同审批",
                        "低于 50% 按未回款金额的 10% 计提坏账准备"],
         "must_cite": ["policy:P-05", "metric:collection_rate"]},
        ["policy:P-05"],
        ["数值正确性", "引用可追溯"],
        "两个阈值分别对应“暂停新签”与“计提坏账”，措施不能张冠李戴。")

    # ---------------- T6 报告生成 ----------------
    rp = q4.set_index("project_code").loc[top_proj]
    add("T6-001", "T6", "报告生成", "medium",
        f"为 {top_proj}（{top_name}）生成 2025 年 Q4 经营简报。",
        {"type": "report",
         "required_sections": ["结论", "关键指标", "异常与风险", "归因", "建议动作"],
         "key_numbers": {"recognized_revenue": money(pr.loc[top_proj, Q4]),
                         "project_cost": money(pc.loc[top_proj, Q4]),
                         "gross_margin": ratio(m4[top_proj])},
         "must_cite": ["metric:recognized_revenue", "metric:gross_margin"]},
        ["metric:recognized_revenue", "metric:gross_margin", "table:cost"],
        ["可执行性", "数值正确性", "引用可追溯"],
        "建议动作必须包含动作、责任人角色、时限三要素，否则可执行性只能给 1 分。")

    east3 = rev[(rev.period == Q3) & (rev.region_name == "华东")].recognized_revenue.sum()
    east4 = rev[(rev.period == Q4) & (rev.region_name == "华东")].recognized_revenue.sum()
    add("T6-002", "T6", "报告生成", "hard",
        "生成 2025 年 Q4 华东区域经营分析报告。",
        {"type": "report",
         "required_sections": ["结论", "关键指标", "异常与风险", "归因", "建议动作"],
         "key_numbers": {"revenue_q3": money(east3), "revenue_q4": money(east4),
                         "qoq_change": ratio(east4 / east3 - 1)},
         "must_cite": ["metric:recognized_revenue"]},
        ["metric:recognized_revenue", "table:revenue"],
        ["可执行性", "数值正确性", "引用可追溯"],
        "区域报告必须包含项目级归因，只报区域汇总视为归因缺失。")

    add("T6-003", "T6", "报告生成", "hard",
        "针对 2025 年 Q4 毛利率低于 10% 的项目，生成一份专项说明。",
        {"type": "report",
         "required_sections": ["问题清单", "原因分析", "制度依据", "处置建议"],
         "key_numbers": {"project_count": int(len(low10)), "threshold": 0.10},
         "must_cite": ["metric:gross_margin", "policy:P-04"]},
        ["metric:gross_margin", "policy:P-04"],
        ["可执行性", "引用可追溯"],
        "必须引用毛利率红线制度，并说明触发了哪一档处置。")

    add("T6-004", "T6", "报告生成", "hard",
        "生成 2025 年度经营总结（公司层面）。",
        {"type": "report",
         "required_sections": ["年度概览", "区域表现", "项目结构", "风险项", "下一年度建议"],
         "key_numbers": {"revenue_2025": money(total25),
                         "new_contracts_2025": money(
                             cont[(cont.signed_period.str.startswith("2025"))
                                  & (cont.status == "有效")].amount.sum())},
         "must_cite": ["metric:recognized_revenue", "metric:contract_amount"]},
        ["metric:recognized_revenue", "metric:contract_amount"],
        ["可执行性", "数值正确性"],
        "年度总结必须区分“收入（不含税）”与“合同额（含税）”，混用即扣分。")

    add("T6-005", "T6", "报告生成", "hard",
        "针对 2025 年 Q4 进度偏差低于 -30% 的项目，提出下季度改善建议。",
        {"type": "report",
         "required_sections": ["滞后项目清单", "滞后程度", "可能原因", "改善动作"],
         "key_numbers": {"project_count": int(len(lag))},
         "must_cite": ["metric:schedule_variance", "policy:P-06"]},
        ["metric:schedule_variance", "policy:P-06"],
        ["可执行性", "归因有效性"],
        "改善动作要对应制度要求（10 个工作日内提交纠偏计划）。")

    # ================================================================
    # 第二批：扩到 60 条（每类 10 条），并划出 12 条留出集
    # ================================================================

    # ---------------- T1 追加 ----------------
    north3 = rev[(rev.period == "2025Q3") & (rev.region_name == "华北")].recognized_revenue.sum()
    add("T1-006", "T1", "单指标查询", "easy",
        "2025 年 Q3 华北区域的确认收入是多少？",
        {"type": "scalar", "value": money(north3), "unit": "元", "tolerance": 0.005},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性"], "换季度、换区域，检验维度过滤是否真正参数化。")

    p103 = rev[(rev.project_code == "P-103") & (rev.period.str.startswith("2025"))]
    add("T1-007", "T1", "单指标查询", "easy",
        "P-103 在 2025 年全年的确认收入是多少？",
        {"type": "scalar", "value": money(p103.recognized_revenue.sum()), "unit": "元",
         "tolerance": 0.005},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性"], "项目 + 全年组合，检验“项目维度 + 期间区间”能否叠加。")

    cost25 = cost[cost.period.str.startswith("2025")].amount.sum()
    add("T1-008", "T1", "单指标查询", "easy",
        "2025 年公司整体项目成本合计是多少？",
        {"type": "scalar", "value": money(cost25), "unit": "元", "tolerance": 0.005},
        ["metric:project_cost", "table:cost"],
        ["数值正确性"], "成本口径：只含直接成本四类，不含期间费用。")

    cont25 = cont[(cont.signed_period.str.startswith("2025")) & (cont.status == "有效")].amount.sum()
    add("T1-009", "T1", "单指标查询", "medium",
        "2025 年全年新签合同额合计是多少？",
        {"type": "scalar", "value": money(cont25), "unit": "元", "tolerance": 0.005},
        ["metric:contract_amount", "table:contract"],
        ["数值正确性"], "合同额含税，且只算有效合同；与收入不可相加。")

    hc_q1 = int(head[(head.period == "2025Q1") & (head.region_code == "R1")].headcount.iloc[0])
    add("T1-010", "T1", "单指标查询", "easy",
        "2025 年 Q1 华东区域的期末在职人数是多少？",
        {"type": "scalar", "value": hc_q1, "unit": "人", "tolerance": 0},
        ["metric:headcount", "table:headcount"], ["数值正确性"],
        "季度切片下的人数是时点值，不能被 SUM 聚合。")

    # ---------------- T2 追加 ----------------
    cc4 = pc[Q4].rename("cost").to_frame().join(meta).reset_index()
    cc4 = cc4[cc4.cost > 0].sort_values("cost", ascending=False).head(5)
    add("T2-006", "T2", "对比与排名", "medium",
        "2025 年 Q4 项目成本最高的 5 个项目是哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "project_cost"],
         "order": "按成本降序", "tolerance": 0.005,
         "rows": [[r.project_code, r.project_name, money(r.cost)] for r in cc4.itertuples()]},
        ["metric:project_cost", "table:cost"], ["数值正确性"],
        "成本排名与毛利率排名不一定一致，这是考点。")

    reg_asc = by_region.sort_values().head(3)
    add("T2-007", "T2", "对比与排名", "medium",
        "2025 年全年确认收入最低的 3 个区域是哪些？",
        {"type": "table", "columns": ["region_name", "recognized_revenue"],
         "order": "按收入升序",
         "rows": [[k, money(v)] for k, v in reg_asc.items()]},
        ["metric:recognized_revenue", "table:revenue"], ["数值正确性"],
        "区域只有三个，排名即全量；注意不要漏掉最后一名。")

    sv_top = sv.dropna().sort_values(ascending=False).head(3)
    add("T2-008", "T2", "对比与排名", "medium",
        "2025 年 Q4 进度偏差最大的 3 个项目是哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "schedule_variance"],
         "order": "按进度偏差降序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(v)] for c, v in sv_top.items()]},
        ["metric:schedule_variance", "table:plan"], ["数值正确性"],
        "“最大”指代数值最大（最超前），不是绝对值；方向答反即错。")

    yr = r25.groupby("project_code").recognized_revenue.sum()
    yc = cost[cost.period.str.startswith("2025")].groupby("project_code").amount.sum()
    ym = ((yr - yc) / yr).dropna().sort_values(ascending=False).head(3)
    add("T2-009", "T2", "对比与排名", "medium",
        "2025 年全年毛利率最高的 3 个项目是哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "gross_margin"],
         "order": "按毛利率降序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(v)] for c, v in ym.items()]},
        ["metric:gross_margin", "table:revenue", "table:cost"], ["数值正确性"],
        "全年毛利率要用全年收入与全年成本，不能取四个季度毛利率的平均。")

    c4r = cont[(cont.signed_period == Q4) & (cont.status == "有效")].groupby("region_name").amount.sum()
    top2r = c4r.sort_values(ascending=False).head(2)
    add("T2-010", "T2", "对比与排名", "medium",
        "2025 年 Q4 新签合同额最高的 2 个区域是哪些？",
        {"type": "table", "columns": ["region_name", "contract_amount"],
         "order": "按合同额降序", "tolerance": 0.005,
         "rows": [[k, money(v)] for k, v in top2r.items()]},
        ["metric:contract_amount", "table:contract"], ["数值正确性"],
        "合同按项目归属区域汇总，不是按签约主体。")

    # ---------------- T3 追加 ----------------
    drop20 = q3q4[q3q4.chg < -0.20].sort_values("chg")
    add("T3-006", "T3", "异常识别", "hard",
        "2025 年 Q4 确认收入环比下滑超过 20% 的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "qoq_change"],
         "order": "按环比变化率升序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(r.chg)] for c, r in drop20.iterrows()]},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性", "引用可追溯"],
        "严格小于 -20%；Q3 无收入的项目不参与比较。")

    big_low = q4[(q4.v < 0.15) & (q4.project_code.map(
        lambda c: pc.loc[c, Q4]) > 10_000_000)].sort_values("v")
    add("T3-007", "T3", "异常识别", "hard",
        "2025 年 Q4 项目成本超过 1000 万、且毛利率低于 15% 的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "gross_margin"],
         "order": "按毛利率升序", "tolerance": 0.001,
         "rows": [[r.project_code, r.project_name, ratio(r.v)] for r in big_low.itertuples()]},
        ["metric:gross_margin", "metric:project_cost"],
        ["数值正确性", "引用可追溯"],
        "金额条件与比率条件同时满足；只看毛利率会漏掉大额低毛利项目。")

    lag_margin = pd.DataFrame({"sv": sv, "m": m4}).dropna()
    lag_margin = lag_margin[(lag_margin.sv < -0.20) & (lag_margin.m < 0.15)]
    add("T3-008", "T3", "异常识别", "hard",
        "2025 年 Q4 进度滞后超过 20%、且毛利率低于 15% 的项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "schedule_variance",
                                      "gross_margin"],
         "order": "按进度偏差升序", "tolerance": 0.001,
         "rows": [[c, meta.loc[c, "project_name"], ratio(r.sv), ratio(r.m)]
                  for c, r in lag_margin.iterrows()]},
        ["metric:schedule_variance", "metric:gross_margin"],
        ["数值正确性", "引用可追溯"],
        "“又慢又不赚钱”的组合，是两个指标的交集。")

    active_status = proj[proj.status == "在建"].project_code
    no_rev = [c for c in active_status if pr[Q4].get(c, 0) == 0]
    add("T3-009", "T3", "异常识别", "medium",
        "2025 年 Q4 没有确认收入的在建项目有哪些？",
        {"type": "table", "columns": ["project_code", "project_name", "status"],
         "order": "按项目编码升序",
         "rows": [[c, meta.loc[c, "project_name"], "在建"] for c in sorted(no_rev)]},
        ["metric:recognized_revenue", "table:project"],
        ["数值正确性"],
        "这不是财务异常而是数据完整性检查：在建却零收入，通常意味着未报量。")

    bad_pay = cont[(cont.status == "有效") & (cont.first_payment_ratio > 0.30)]
    add("T3-010", "T3", "异常识别", "medium",
        "2025 年新签合同中，首付款比例超过 30% 的合同有哪些？",
        {"type": "table", "columns": ["contract_code", "project_code", "first_payment_ratio"],
         "order": "按首付款比例降序", "tolerance": 0.001,
         "rows": [[r.contract_code, r.project_code, ratio(r.first_payment_ratio)]
                  for r in bad_pay.sort_values("first_payment_ratio", ascending=False).itertuples()]},
        ["policy:P-07", "table:contract"],
        ["数值正确性", "引用可追溯"],
        "制度扫描题：不只看数，还要引用 P-07 说明违规点。")

    # ---------------- T4 追加 ----------------
    reg_m = pd.DataFrame({
        "rev": rev[rev.period == Q4].groupby("region_name").recognized_revenue.sum(),
        "cost": cost[cost.period == Q4].groupby("region_name").amount.sum()})
    reg_m["m"] = (reg_m.rev - reg_m.cost) / reg_m.rev
    worst_region = reg_m.m.idxmin()
    wr_proj = q4[q4.region_name == worst_region].sort_values("v").iloc[0]
    add("T4-006", "T4", "归因分析", "hard",
        "2025 年 Q4 哪个区域毛利率最低？主要拖累项目是哪个？",
        {"type": "structured",
         "key_facts": [
             f"最低的是{worst_region}，区域毛利率 {ratio(reg_m.m[worst_region])}",
             f"拖累最大的是 {wr_proj.project_code}（{wr_proj.project_name}），"
             f"毛利率 {ratio(wr_proj.v)}",
         ]},
        ["metric:gross_margin", "table:revenue", "table:cost"],
        ["数值正确性", "归因有效性", "引用可追溯"],
        "先算区域层，再下钻项目层；顺序反了容易把个体问题说成区域问题。")

    R4 = rev[rev.period == Q4].recognized_revenue.sum()
    R3 = rev[rev.period == Q3].recognized_revenue.sum()
    C4 = cost[cost.period == Q4].amount.sum()
    C3 = cost[cost.period == Q3].amount.sum()
    rev_eff_all = C3 * (1 / R3 - 1 / R4)
    cost_eff_all = -(C4 - C3) / R4
    add("T4-007", "T4", "归因分析", "hard",
        "2025 年 Q4 公司整体毛利率环比变化，收入端和成本端各贡献多少？",
        {"type": "structured",
         "key_facts": [
             f"毛利率 {ratio(1 - C3 / R3)} → {ratio(1 - C4 / R4)}",
             f"收入端贡献 {ratio(rev_eff_all)}，成本端贡献 {ratio(cost_eff_all)}",
         ]},
        ["metric:gross_margin", "table:revenue", "table:cost"],
        ["数值正确性", "归因有效性"],
        "公司层拆解用的是总量，不能用各项目贡献的简单平均。")

    labor = cost[cost.period.str.startswith("2025")]
    labor_share = (labor[labor.cost_type == "人工"].groupby("project_code").amount.sum()
                   / labor.groupby("project_code").amount.sum()).dropna()
    lp = labor_share.idxmax()
    add("T4-008", "T4", "归因分析", "medium",
        "2025 年人工成本占项目成本比例最高的项目是哪个？占比多少？",
        {"type": "structured",
         "key_facts": [
             f"{lp}（{meta.loc[lp, 'project_name']}）人工成本占比 {ratio(labor_share[lp])}",
         ]},
        ["metric:project_cost", "table:cost"],
        ["数值正确性", "归因有效性"],
        "结构分析：占比高不等于金额大，两个维度都要看。")

    inc = q3q4.sort_values("chg", ascending=False).head(1)
    inc_p = inc.index[0]
    add("T4-009", "T4", "归因分析", "medium",
        "2025 年 Q4 确认收入环比增幅最大的项目是哪个？增幅多少？",
        {"type": "structured",
         "key_facts": [
             f"{inc_p}（{meta.loc[inc_p, 'project_name']}）环比 "
             f"{ratio(inc.iloc[0].chg)}，从 {money(inc.iloc[0].q3)} 增至 "
             f"{money(inc.iloc[0].q4)}",
         ]},
        ["metric:recognized_revenue", "table:revenue"],
        ["数值正确性", "归因有效性"],
        "增幅要用相对值排名；基数很小的项目容易冲到第一，需说明绝对增量。")

    ct_all = cost[cost.period.str.startswith("2025")].groupby("cost_type").amount.sum()
    share = (ct_all / ct_all.sum()).sort_values(ascending=False)
    add("T4-010", "T4", "归因分析", "medium",
        "2025 年全年四类成本中，占比最高的是哪一类？占比多少？",
        {"type": "structured",
         "key_facts": [f"{share.index[0]} 占比 {ratio(share.iloc[0])}",
                       f"四类合计 {money(ct_all.sum())}"]},
        ["metric:project_cost", "table:cost"],
        ["数值正确性", "归因有效性"],
        "结构占比题，分母是四类成本合计，不是收入。")

    # ---------------- T5 追加 ----------------
    add("T5-006", "T5", "制度与口径", "medium",
        "进度偏差低于多少算重大滞后？需要做什么？",
        {"type": "text",
         "key_points": ["进度偏差低于 -20% 判定为重大滞后",
                        "项目负责人 10 个工作日内提交纠偏计划",
                        "连续两个季度重大滞后纳入公司级风险清单"],
         "must_cite": ["policy:P-06"]},
        ["policy:P-06"], ["数值正确性", "引用可追溯"],
        "阈值与动作要成对回答，只答阈值不给动作算半分。")

    add("T5-007", "T5", "制度与口径", "medium",
        "经营数据什么时候出具？发布之后还能修改吗？",
        {"type": "text",
         "key_points": ["财务月结后 T+3 个工作日内出具",
                        "数据一经发布当期不再修改",
                        "后续调整在下一期体现"],
         "must_cite": ["policy:P-09"]},
        ["policy:P-09"], ["引用可追溯", "数值正确性"],
        "这条决定了“为什么同一指标两期口径会变”，是理解跨期调整的前提。")

    add("T5-008", "T5", "制度与口径", "medium",
        "指标口径如果要变更，走什么流程？会重算历史数据吗？",
        {"type": "text",
         "key_points": ["由财务分析负责人提出，经营管理部评审后发布",
                        "口径变更须版本化并登记变更记录",
                        "不追溯重算历史，新旧口径并行一个季度后切换"],
         "must_cite": ["policy:P-10", "metric:recognized_revenue"]},
        ["policy:P-10"], ["引用可追溯"],
        "考察对“口径治理”的理解：变更可控，但历史不重算。")

    add("T5-009", "T5", "制度与口径", "medium",
        "合同变更什么时候计入合同额？框架协议算不算？",
        {"type": "text",
         "key_points": ["变更须双方盖章后生效，未生效不计入",
                        "框架协议不计入，以下达的具体订单为准",
                        "合同额按含税金额统计"],
         "must_cite": ["policy:P-08", "metric:contract_amount"]},
        ["policy:P-08"], ["引用可追溯", "数值正确性"],
        "三个小点都要答到，漏一个扣一分。")

    add("T5-010", "T5", "制度与口径", "hard",
        "项目收入确认有没有上限？跨年是怎么处理的？",
        {"type": "text",
         "key_points": ["同一项目年度内确认收入不得超过合同不含税金额",
                        "未开工项目不确认收入",
                        "跨期调整计入调整发生期间，不追溯重述"],
         "must_cite": ["policy:P-01", "metric:recognized_revenue"]},
        ["policy:P-01"], ["数值正确性", "引用可追溯"],
        "上限与跨期两条规则常被忽略，是“收入为什么不会无限增长”的答案。")

    # ---------------- T6 追加 ----------------
    south25 = rev[(rev.period.str.startswith("2025")) & (rev.region_name == "华南")]
    add("T6-006", "T6", "报告生成", "hard",
        "生成 2025 年华南区域年度经营报告。",
        {"type": "report",
         "required_sections": ["年度概览", "关键指标", "异常项目", "归因", "下一年度建议"],
         "key_numbers": {"revenue_2025": money(south25.recognized_revenue.sum())},
         "must_cite": ["metric:recognized_revenue", "metric:gross_margin"]},
        ["metric:recognized_revenue", "metric:gross_margin"],
        ["可执行性", "数值正确性", "引用可追溯"],
        "区域年度报告要覆盖四个季度，不能只写 Q4。")

    risk_cnt = int((coll.rate < 0.70).sum())
    add("T6-007", "T6", "报告生成", "hard",
        "生成一份回款风险专项报告（累计回款率低于 70% 的项目）。",
        {"type": "report",
         "required_sections": ["风险项目清单", "回款缺口", "制度依据", "催收建议"],
         "key_numbers": {"project_count": risk_cnt, "threshold": 0.70},
         "must_cite": ["metric:collection_rate", "policy:P-05"]},
        ["metric:collection_rate", "policy:P-05"],
        ["可执行性", "引用可追溯"],
        "要区分“开票未回款”与“未开票”，两者的处置动作不同。")

    lag_cnt = int((sv < -0.30).sum())
    add("T6-008", "T6", "报告生成", "hard",
        "生成一份进度滞后专项报告（Q4 进度偏差低于 -30% 的项目）。",
        {"type": "report",
         "required_sections": ["滞后项目清单", "滞后程度", "可能原因", "改善动作"],
         "key_numbers": {"project_count": lag_cnt},
         "must_cite": ["metric:schedule_variance", "policy:P-06"]},
        ["metric:schedule_variance", "policy:P-06"],
        ["可执行性", "归因有效性"],
        "纠偏计划要写到责任人角色和时限，否则可执行性不给满分。")

    c25 = cont[(cont.signed_period.str.startswith("2025")) & (cont.status == "有效")]
    type_cnt = c25.groupby("contract_type").size().to_dict()
    add("T6-009", "T6", "报告生成", "hard",
        "生成 2025 年新签合同结构分析。",
        {"type": "report",
         "required_sections": ["总量", "按类型", "按区域", "异常合同", "建议"],
         "key_numbers": {"contract_count": int(len(c25)),
                         "total_amount": money(c25.amount.sum())},
         "must_cite": ["metric:contract_amount", "policy:P-07"]},
        ["metric:contract_amount", "policy:P-07"],
        ["可执行性", "数值正确性"],
        "结构分析要说明“哪类合同占比变化”，只报总数没有洞察。")

    add("T6-010", "T6", "报告生成", "hard",
        "给管理层生成一页纸经营摘要（2025 年 Q4）。",
        {"type": "report",
         "required_sections": ["一句话结论", "三个关键数字", "两个风险", "一个建议"],
         "key_numbers": {"revenue_q4": money(R4), "gross_margin_q4": ratio(1 - C4 / R4),
                         "new_contracts_q4": money(
                             cont[(cont.signed_period == Q4) & (cont.status == "有效")]
                             .amount.sum())},
         "must_cite": ["metric:recognized_revenue", "metric:gross_margin",
                       "metric:contract_amount"]},
        ["metric:recognized_revenue", "metric:gross_margin", "metric:contract_amount"],
        ["可执行性", "数值正确性"],
        "一页纸的难点是取舍：三个数字必须是最能说明问题的，不能平均用力。")

    # 留出集：每类 2 条，共 12 条（20%），用于检验泛化
    HOLDOUT = {
        "T1-006", "T1-007",
        "T2-007", "T2-010",
        "T3-007", "T3-010",
        "T4-007", "T4-009",
        "T5-008", "T5-010",
        "T6-007", "T6-009",
    }
    for t in tasks:
        t["split"] = "holdout" if t["id"] in HOLDOUT else "dev"

    # ---------------- write ----------------
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    header = (
        "# BizLens 评测任务集 v1（60 条，含留出集）\n"
        "# 60 条 = 6 类 × 10 条；其中 12 条为 holdout，不参与调优\n"
        "# ground truth 由 scripts/build_tasks.py 从合成数据计算\n"
        "# 数据：data/seed/*.csv（全部虚构）；口径：data/metrics.yaml；制度：data/policy.md\n"
        "# 评测规则与指标定义见 docs/02-评测设计.md\n"
        "# 注意：仍未做双人标注，正式发布前需人工复核 20%\n\n"
        "meta:\n"
        f"  count: {len(tasks)}\n"
        f"  holdout: {len(HOLDOUT)}\n"
        "  version: \"1.0.0\"\n"
        "  data_snapshot: \"seed@20251103\"\n"
        "  categories: [T1, T2, T3, T4, T5, T6]\n\n"
        "tasks:\n"
    )
    body = yaml_dump(tasks, 1)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(header + body + "\n")

    from collections import Counter
    print("tasks:", len(tasks), Counter(t["category"] for t in tasks))
    print("written:", OUT)


if __name__ == "__main__":
    main()
