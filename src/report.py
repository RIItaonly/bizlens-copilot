"""报告生成：模板 + 数字，不让模型自由发挥。

每份报告的结构固定（章节名固定），数字全部来自语义层，
所以报告可以 diff、可以审、可以回放。措辞是模板化的，这既是限制也是优点：
企业里没人希望"经营简报"的措辞每周都不一样。
"""

from __future__ import annotations

from analytics import (collection_gap, cost_structure, margin_drop_top,
                       region_contribution, region_margin)


def fmt_money(x) -> str:
    return f"¥{x:,.0f}" if x is not None else "—"


def pct(x) -> str:
    return f"{x:.1%}" if x is not None else "—"


def _project_rows(layer, periods, conditions=None, order=None):
    return layer.project_table(periods, conditions, order=order)


def _contract_total(layer, periods=("2025Q1", "2025Q2", "2025Q3", "2025Q4")):
    row = layer.conn.execute(
        "SELECT SUM(amount) FROM contract WHERE status='有效' "
        "AND signed_period IN (?,?,?,?)", periods).fetchone()
    return row[0] or 0


def project_brief(layer, project, period, prev):
    t = layer.period_totals([period], project=project)
    rev, cost = t.get("revenue") or 0, t.get("cost") or 0
    m = (rev - cost) / rev if rev else None
    d = margin_drop_top(layer, period, prev)
    lines = [
        f"# {project} 经营简报（{period}）",
        "",
        "## 结论",
        f"本期确认收入 {fmt_money(rev)}，毛利率 {pct(m)}。",
        "",
        "## 关键指标",
        f"- 确认收入：{fmt_money(rev)}",
        f"- 项目成本：{fmt_money(cost)}",
        f"- 毛利率：{pct(m)}",
        "",
        "## 异常与风险",
    ]
    if m is not None and m < 0.15:
        lines.append(f"- 毛利率低于 15% 红线，按 P-04 需提交书面说明。")
    if rev == 0:
        lines.append("- 本期无确认收入，需核实是否未报量。")
    if len(lines) == 9:
        lines.append("- 本期未触发异常阈值。")
    lines += ["", "## 归因"]
    if d and d["project"] == project:
        lines.append(f"- 毛利率环比 {pct(d['prev'])} → {pct(d['now'])}，"
                     f"变化 {d['delta']:+.1%}。")
    else:
        lines.append("- 与上期相比无显著变化。")
    lines += [
        "",
        "## 建议动作",
        "- 动作：核对本期收入确认与成本归集口径；责任人：项目财务；时限：5 个工作日。",
        "- 动作：若毛利率持续低于 15%，提交改善方案；责任人：项目负责人；时限：10 个工作日。",
    ]
    return "\n".join(lines), ["metric:recognized_revenue", "metric:gross_margin", "table:cost"]


def region_report(layer, region, period, prev):
    now = layer.period_totals([period], region=region)
    old = layer.period_totals([prev], region=region)
    rows = [r for r in layer.project_table([period]) if r["region_name"] == region]
    bad = [r for r in rows if r["gross_margin"] is not None and r["gross_margin"] < 0.15]
    cont = region_contribution(layer, region, period, prev)
    lines = [
        f"# {region}区域经营分析（{period}）",
        "",
        "## 结论",
        f"本期收入 {fmt_money(now['revenue'])}，环比 {(now['revenue']/old['revenue']-1):+.1%}；"
        f"毛利率 {pct(1 - now['cost']/now['revenue'])}。",
        "",
        "## 关键指标",
        f"- 收入：{fmt_money(now['revenue'])}（上期 {fmt_money(old['revenue'])}）",
        f"- 成本：{fmt_money(now['cost'])}",
        f"- 毛利率：{pct(1 - now['cost']/now['revenue'])}",
        "",
        "## 异常与风险",
    ]
    for r in sorted(bad, key=lambda x: x["gross_margin"])[:3]:
        lines.append(f"- {r['project_code']} 毛利率 {pct(r['gross_margin'])}")
    if not bad:
        lines.append("- 本期无低毛利项目。")
    lines += ["", "## 归因"]
    if cont["top"]:
        code, delta = cont["top"]
        lines.append(f"- 收入变化贡献最大的是 {code}，环比 {fmt_money(delta)}。")
    lines += [
        "",
        "## 建议动作",
        "- 动作：对低毛利项目逐个复盘成本结构；责任人：区域财务；时限：15 个工作日。",
        "- 动作：将收入缺口分解到项目并跟踪；责任人：区域负责人；时限：10 个工作日。",
    ]
    return "\n".join(lines), ["metric:recognized_revenue", "metric:gross_margin", "table:revenue"]


def low_margin_special(layer, period, threshold=0.10):
    rows = layer.project_table([period], [("gross_margin", "<", threshold)],
                               order=("gross_margin", True))
    lines = [
        f"# 低毛利项目专项说明（{period}）",
        "",
        f"筛选阈值：毛利率 < {pct(threshold)}。",
        "",
        "## 问题清单",
        "| 项目 | 名称 | 毛利率 |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['project_code']} | {r['project_name']} | {pct(r['gross_margin'])} |")
    lines += ["", "## 原因分析"]
    for r in rows[:3]:
        ct = layer.cost_by_type([period], project=r["project_code"])
        top = max(ct, key=ct.get) if ct else "—"
        lines.append(f"- {r['project_code']}：成本结构中占比最高的是「{top}」。")
    lines += [
        "",
        "## 制度依据",
        "- P-04：毛利率低于 15% 须书面说明；低于 5%（含负毛利）触发专项复盘。",
        "",
        "## 处置建议",
        "- 动作：30 日内出具复盘报告；责任人：经营管理部；时限：30 个工作日。",
    ]
    return "\n".join(lines), ["metric:gross_margin", "policy:P-04"]


def annual_summary(layer):
    periods = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
    t = layer.period_totals(periods)
    reg = []
    for name in ["华东", "华南", "华北"]:
        rt = layer.period_totals(periods, region=name)
        reg.append((name, rt["revenue"], 1 - rt["cost"] / rt["revenue"]))
    low = layer.count_projects(periods, [("gross_margin", "<", 0.15)])
    lines = [
        "# 2025 年度经营总结",
        "",
        "## 年度概览",
        f"- 确认收入 {fmt_money(t['revenue'])}，整体毛利率 {pct(1 - t['cost']/t['revenue'])}。",
        f"- 新签合同额 {fmt_money(_contract_total(layer))}（含税）。",
        "",
        "## 区域表现",
    ]
    for name, rev, m in sorted(reg, key=lambda x: -x[1]):
        lines.append(f"- {name}：收入 {fmt_money(rev)}，毛利率 {pct(m)}")
    lines += ["", "## 项目结构", f"- 毛利率低于 15% 的项目共 {low} 个。", "", "## 风险项"]
    lines.append(f"- 低毛利项目 {low} 个，需按 P-04 逐个说明。")
    lines += ["", "## 下一年度建议",
              "- 动作：把毛利率 15% 设为立项门槛；责任人：经营管理部；时限：下一年度。",
              "- 动作：对低毛利项目建立月度跟踪；责任人：区域财务；时限：30 个工作日。"]
    return "\n".join(lines), ["metric:recognized_revenue", "metric:contract_amount"]


def lag_special(layer, period, threshold=-0.30):
    rows = layer.project_table([period], [("schedule_variance", "<", threshold)],
                               order=("schedule_variance", True))
    lines = [
        f"# 进度滞后专项报告（{period}）",
        "",
        "## 滞后项目清单",
        "| 项目 | 名称 | 进度偏差 |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['project_code']} | {r['project_name']} | {pct(r['schedule_variance'])} |")
    lines += [
        "",
        "## 滞后程度",
        f"- 共 {len(rows)} 个项目偏差低于 {pct(threshold)}。",
        "",
        "## 可能原因",
        "- 报量不足或计划口径未同步，需与项目管理办公室核对。",
        "",
        "## 改善动作",
        "- 动作：10 个工作日内提交纠偏计划；责任人：项目负责人；时限：10 个工作日。",
    ]
    return "\n".join(lines), ["metric:schedule_variance", "policy:P-06"]


def collection_risk(layer, threshold=0.70):
    periods = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
    rows = layer.project_table(periods, [("collection_rate", "<", threshold)],
                               order=("collection_rate", True))
    lines = [
        "# 回款风险专项报告（2025 年）",
        "",
        "## 风险项目清单",
        "| 项目 | 名称 | 回款率 | 未回款 |",
        "|---|---|---|---|",
    ]
    for r in rows:
        gap = collection_gap(layer, r["project_code"], periods)
        lines.append(f"| {r['project_code']} | {r['project_name']} | "
                     f"{pct(r['collection_rate'])} | {fmt_money(gap['gap'])} |")
    lines += [
        "",
        "## 回款缺口",
        f"- 共 {len(rows)} 个项目回款率低于 {pct(threshold)}。",
        "",
        "## 制度依据",
        "- P-05：低于 70% 暂停新签审批；低于 50% 按未回款 10% 计提坏账。",
        "",
        "## 催收建议",
        "- 动作：按项目出具催收清单并明确责任人；责任人：区域商务；时限：15 个工作日。",
    ]
    return "\n".join(lines), ["metric:collection_rate", "policy:P-05"]


def contract_structure(layer, periods=None):
    periods = periods or ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
    rows = layer.conn.execute(
        "SELECT contract_type, region_name, COUNT(*) n, SUM(amount) amt "
        "FROM contract WHERE status='有效' AND signed_period IN (?,?,?,?) "
        "GROUP BY contract_type, region_name", periods).fetchall()
    by_type, by_region, total, count = {}, {}, 0.0, 0
    for r in rows:
        by_type[r["contract_type"]] = by_type.get(r["contract_type"], 0) + r["amt"]
        by_region[r["region_name"]] = by_region.get(r["region_name"], 0) + r["amt"]
        total += r["amt"]
        count += r["n"]
    bad = layer.contracts_over_ratio(0.30)
    lines = [
        "# 2025 年新签合同结构分析",
        "",
        "## 总量",
        f"- 合同 {count} 份，金额 {fmt_money(total)}（含税）。",
        "",
        "## 按类型",
    ]
    for k, v in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}：{fmt_money(v)}（{v/total:.1%}）")
    lines += ["", "## 按区域"]
    for k, v in sorted(by_region.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}：{fmt_money(v)}")
    lines += ["", "## 异常合同",
              f"- 首付款比例超过 30% 的合同 {len(bad)} 份，需按 P-07 补审批。",
              "", "## 建议",
              "- 动作：对超比例合同逐份补齐审批；责任人：商务管理部；时限：10 个工作日。"]
    return "\n".join(lines), ["metric:contract_amount", "policy:P-07"]


def one_pager(layer, period="2025Q4"):
    t = layer.period_totals([period])
    prev = {"2025Q4": "2025Q3"}.get(period, "2025Q3")
    old = layer.period_totals([prev])
    m = 1 - t["cost"] / t["revenue"]
    reg = region_margin(layer, period)
    low = layer.project_table([period], [("gross_margin", "<", 0.15)])
    cont = layer.conn.execute(
        "SELECT SUM(amount) FROM contract WHERE status='有效' AND signed_period=?", [period]
    ).fetchone()[0] or 0
    lines = [
        f"# 管理层一页纸（{period}）",
        "",
        "## 一句话结论",
        f"收入环比 {(t['revenue']/old['revenue']-1):+.1%}，毛利率 {pct(m)}，"
        f"增长质量取决于低毛利项目能否收敛。",
        "",
        "## 三个关键数字",
        f"- 收入 {fmt_money(t['revenue'])}",
        f"- 毛利率 {pct(m)}",
        f"- 新签合同额 {fmt_money(cont)}",
        "",
        "## 两个风险",
        f"- {low[0]['project_code']} 等 {len(low)} 个项目毛利率低于 15%。",
        f"- {reg[0]['region']} 区域毛利率最低（{pct(reg[0]['margin'])}）。",
        "",
        "## 一个建议",
        "- 动作：本月经营会只议题 = 低毛利项目处置方案；责任人：经营管理部；时限：下次经营会。",
    ]
    return "\n".join(lines), ["metric:recognized_revenue", "metric:gross_margin",
                              "metric:contract_amount"]


def region_annual(layer, region):
    periods = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
    t = layer.period_totals(periods, region=region)
    rows = [r for r in layer.project_table(periods) if r["region_name"] == region]
    top = sorted(rows, key=lambda r: -(r["revenue"] or 0))[:3]
    low = [r for r in rows if r["gross_margin"] is not None and r["gross_margin"] < 0.15]
    lines = [
        f"# {region}区域年度经营报告（2025）",
        "",
        "## 年度概览",
        f"- 收入 {fmt_money(t['revenue'])}，毛利率 {pct(1 - t['cost']/t['revenue'])}。",
        "",
        "## 关键指标",
        f"- 成本 {fmt_money(t['cost'])}；项目数 {len(rows)}。",
        "",
        "## 异常项目",
    ]
    for r in sorted(low, key=lambda x: x["gross_margin"])[:3]:
        lines.append(f"- {r['project_code']} 毛利率 {pct(r['gross_margin'])}")
    if not low:
        lines.append("- 无低毛利项目。")
    lines += ["", "## 归因"]
    for r in top:
        lines.append(f"- {r['project_code']} 收入 {fmt_money(r['revenue'])}，"
                     f"毛利率 {pct(r['gross_margin'])}")
    lines += ["", "## 下一年度建议",
              "- 动作：把区域毛利率目标拆到项目；责任人：区域负责人；时限：下一年度。"]
    return "\n".join(lines), ["metric:recognized_revenue", "metric:gross_margin", "table:revenue"]
