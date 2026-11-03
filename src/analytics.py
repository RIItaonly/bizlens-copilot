"""归因分析：把"变了多少"拆成"谁造成的"。

所有函数只做算术，不做自然语言；措辞在 engine 里拼。
原则：每个结论都要能回指到一个数字，不能只有形容词。
"""

from __future__ import annotations


def margin_decompose(layer, project, period, prev):
    now = layer.period_totals([period], project=project)
    old = layer.period_totals([prev], project=project)
    R4, C4 = now.get("revenue") or 0, now.get("cost") or 0
    R3, C3 = old.get("revenue") or 0, old.get("cost") or 0
    if not R3 or not R4:
        return None
    rev_eff = C3 * (1 / R3 - 1 / R4)
    cost_eff = -(C4 - C3) / R4
    ct_now = layer.cost_by_type([period], project=project)
    ct_old = layer.cost_by_type([prev], project=project)
    types = set(ct_now) | set(ct_old)
    driver = max(types, key=lambda t: abs(ct_now.get(t, 0) - ct_old.get(t, 0)))
    return {
        "project": project,
        "margin_now": 1 - C4 / R4,
        "margin_prev": 1 - C3 / R3,
        "delta": (1 - C4 / R4) - (1 - C3 / R3),
        "revenue_effect": rev_eff,
        "cost_effect": cost_eff,
        "driver": driver,
        "driver_delta": ct_now.get(driver, 0) - ct_old.get(driver, 0),
        "revenue_now": R4,
        "revenue_prev": R3,
    }


def company_decompose(layer, period, prev):
    now = layer.period_totals([period])
    old = layer.period_totals([prev])
    R4, C4 = now["revenue"], now["cost"]
    R3, C3 = old["revenue"], old["cost"]
    return {
        "margin_now": 1 - C4 / R4,
        "margin_prev": 1 - C3 / R3,
        "revenue_effect": C3 * (1 / R3 - 1 / R4),
        "cost_effect": -(C4 - C3) / R4,
        "revenue_now": R4, "revenue_prev": R3,
        "cost_now": C4, "cost_prev": C3,
    }


def region_margin(layer, period):
    rows = layer.project_table([period])
    by = {}
    for r in rows:
        b = by.setdefault(r["region_name"], {"revenue": 0.0, "cost": 0.0})
        b["revenue"] += r["revenue"] or 0
        b["cost"] += r["cost"] or 0
    out = []
    for name, b in by.items():
        if b["revenue"]:
            out.append({"region": name, "margin": (b["revenue"] - b["cost"]) / b["revenue"],
                        "revenue": b["revenue"], "cost": b["cost"]})
    return sorted(out, key=lambda x: x["margin"])


def region_contribution(layer, region, period, prev):
    now = layer.project_table([period])
    old = layer.project_table([prev])
    now_map = {r["project_code"]: r["revenue"] or 0 for r in now}
    old_map = {r["project_code"]: r["revenue"] or 0 for r in old}
    r_now = sum(v for k, v in now_map.items() if any(
        r["project_code"] == k and r["region_name"] == region for r in now))
    r_prev = sum(v for k, v in old_map.items() if any(
        r["project_code"] == k and r["region_name"] == region for r in old))
    deltas = []
    for code in set(now_map) | set(old_map):
        if not any(r["project_code"] == code and r["region_name"] == region for r in now + old):
            continue
        deltas.append((code, now_map.get(code, 0) - old_map.get(code, 0)))
    deltas.sort(key=lambda x: x[1])
    return {"region": region, "revenue_now": r_now, "revenue_prev": r_prev,
            "deltas": deltas, "top": deltas[0] if deltas else None}


def collection_gap(layer, project, periods):
    t = layer.period_totals(periods, project=project)
    inv, col = t.get("invoiced") or 0, t.get("collected") or 0
    return {"invoiced": inv, "collected": col,
            "rate": (col / inv) if inv else None, "gap": inv - col}


def cost_structure(layer, periods):
    ct = layer.cost_by_type(periods)
    total = sum(ct.values())
    shares = {k: v / total for k, v in ct.items()} if total else {}
    order = sorted(shares, key=shares.get, reverse=True)
    return {"amount": ct, "total": total, "shares": shares, "order": order}


def labor_share(layer, periods):
    rows = layer.project_table(periods)
    best = None
    for r in rows:
        ct = layer.cost_by_type(periods, project=r["project_code"])
        total = sum(ct.values())
        if total:
            share = ct.get("人工", 0) / total
            if best is None or share > best[1]:
                best = (r["project_code"], share, total)
    return {"project": best[0], "share": best[1], "cost": best[2]} if best else None


def growth_top(layer, period, prev):
    now = {r["project_code"]: r for r in layer.project_table([period])}
    old = {r["project_code"]: r for r in layer.project_table([prev])}
    best = None
    for code, r in now.items():
        if code not in old:
            continue
        a, b = r["revenue"] or 0, old[code]["revenue"] or 0
        if a and b:
            g = a / b - 1
            if best is None or g > best[1]:
                best = (code, g, b, a)
    return {"project": best[0], "growth": best[1], "prev": best[2],
            "now": best[3]} if best else None


def margin_drop_top(layer, period, prev):
    now = {r["project_code"]: r for r in layer.project_table([period])}
    old = {r["project_code"]: r for r in layer.project_table([prev])}
    best = None
    for code, r in now.items():
        if code not in old:
            continue
        a, b = r["gross_margin"], old[code]["gross_margin"]
        if a is None or b is None:
            continue
        d = a - b
        if best is None or d < best[1]:
            best = (code, d, b, a)
    return {"project": best[0], "delta": best[1], "prev": best[2],
            "now": best[3]} if best else None
