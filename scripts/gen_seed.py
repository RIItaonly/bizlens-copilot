"""生成 BizLens 的合成种子数据（确定性，可复现）。

用法：
    python scripts/gen_seed.py

输出：data/seed/*.csv
说明：全部为虚构数据，仅用于演示与评测，不含任何真实企业信息。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

SEED = 20251103
rng = np.random.default_rng(SEED)

PERIODS = ["2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4"]
REGIONS = [("R1", "华东"), ("R2", "华南"), ("R3", "华北")]
COST_TYPES = ["人工", "材料", "分包", "其他"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "seed")

PREFIX = ["滨江", "云岭", "星河", "锦泰", "恒越", "长兴", "万象", "启元",
          "鼎盛", "天工", "嘉禾", "瑞成", "海纳", "中科", "新华"]
SUFFIX = ["智造园一期", "数据中心二期", "物流枢纽", "产业园三期", "研发中心",
          "医院改扩建", "学校新建", "水务工程", "变电站", "综合体"]


def build_projects() -> pd.DataFrame:
    # 指定毛利率分布，保证评测里有"低毛利"和"负毛利"样本
    margins = [-0.06, -0.03, 0.02, 0.04, 0.06, 0.08, 0.09, 0.11, 0.12, 0.13,
               0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.20, 0.21, 0.22, 0.23,
               0.24, 0.25, 0.26, 0.27, 0.28, 0.30, 0.31, 0.33, 0.35, 0.38]
    rng.shuffle(margins)

    rows = []
    for i in range(30):
        code = f"P-{101 + i}"
        name = PREFIX[i % len(PREFIX)] + SUFFIX[(i * 7) % len(SUFFIX)]
        region_code, region_name = REGIONS[i % 3]
        start = int(rng.choice([0, 0, 1, 1, 2]))  # 约 40% 的项目覆盖 2024Q4
        duration = int(rng.integers(3, 6))       # 3..5
        end = min(start + duration, len(PERIODS))
        contract = float(np.round(rng.uniform(6e6, 6e7), -3))
        status = "已结项" if end == len(PERIODS) and rng.random() < 0.25 else "在建"
        rows.append(dict(
            project_code=code,
            project_name=name,
            region_code=region_code,
            region_name=region_name,
            customer_code=f"C-{rng.integers(1, 13):02d}",
            status=status,
            start_period=PERIODS[start],
            end_period=PERIODS[end - 1],
            contract_amount=contract,
            margin_base=margins[i],
            start_idx=start,
            end_idx=end,
        ))
    return pd.DataFrame(rows)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    projects = build_projects()

    pd.DataFrame(
        [(c, n, "交付事业部") for c, n in REGIONS],
        columns=["region_code", "region_name", "division"],
    ).to_csv(os.path.join(OUT, "org.csv"), index=False)

    plan_rows, rev_rows, cost_rows, contract_rows = [], [], [], []

    for _, p in projects.iterrows():
        n = p.end_idx - p.start_idx
        w = np.array([0.12, 0.22, 0.30, 0.24, 0.12])[:n]
        w = w / w.sum()
        planned = np.round(p.contract_amount * w, -2)

        factor = rng.uniform(0.70, 1.10)
        noise = np.clip(rng.normal(1.0, 0.08, n), 0.80, 1.20)
        revenue = np.round(planned * factor * noise, -2)
        revenue = np.minimum(revenue, p.contract_amount * w * 1.6)

        margin_t = np.clip(p.margin_base + rng.normal(0, 0.03, n), -0.35, 0.60)
        cost_total = revenue * (1 - margin_t)

        split = rng.dirichlet(np.array([5.0, 4.0, 3.5, 1.5]))
        inv_factor = rng.uniform(0.85, 1.05)
        coll_target = rng.uniform(0.55, 1.00)

        first_ratio = float(rng.choice([0.20, 0.25, 0.30, 0.30, 0.40, 0.50]))

        for k in range(n):
            period = PERIODS[p.start_idx + k]
            plan_rows.append(dict(project_code=p.project_code, period=period,
                                  planned_revenue=float(planned[k])))
            invoiced = round(revenue[k] * inv_factor, -2)
            collected = round(invoiced * coll_target, -2)
            rev_rows.append(dict(project_code=p.project_code, period=period,
                                 recognized_revenue=float(revenue[k]),
                                 invoiced_amount=float(invoiced),
                                 collected_amount=float(collected)))
            for t, share in zip(COST_TYPES, split):
                cost_rows.append(dict(project_code=p.project_code, period=period,
                                      cost_type=t,
                                      amount=float(round(cost_total[k] * share, -2))))

        contract_rows.append(dict(
            contract_code=f"HT-{p.project_code[2:]}-01",
            project_code=p.project_code,
            contract_type="施工" if rng.random() < 0.7 else "采购",
            counterparty_code=p.customer_code,
            signed_period=PERIODS[p.start_idx],
            amount=float(p.contract_amount),
            first_payment_ratio=first_ratio,
            status="有效",
        ))
        # 约 1/3 的项目在后续期间再签一份补充协议（含 2025Q4 新签）
        if rng.random() < 0.33:
            later = int(rng.integers(p.start_idx + 1, len(PERIODS)))
            contract_rows.append(dict(
                contract_code=f"HT-{p.project_code[2:]}-02",
                project_code=p.project_code,
                contract_type="补充协议",
                counterparty_code=p.customer_code,
                signed_period=PERIODS[later],
                amount=float(np.round(p.contract_amount * rng.uniform(0.03, 0.08), -3)),
                first_payment_ratio=0.0,
                status="有效" if rng.random() < 0.7 else "无效",
            ))

    pd.DataFrame(plan_rows).to_csv(os.path.join(OUT, "plan.csv"), index=False)
    pd.DataFrame(rev_rows).to_csv(os.path.join(OUT, "revenue.csv"), index=False)
    pd.DataFrame(cost_rows).to_csv(os.path.join(OUT, "cost.csv"), index=False)
    pd.DataFrame(contract_rows).to_csv(os.path.join(OUT, "contract.csv"), index=False)

    head_rows = []
    base = {"R1": 168, "R2": 142, "R3": 121}
    for idx, period in enumerate(PERIODS):
        for code, _ in REGIONS:
            head_rows.append(dict(region_code=code, period=period,
                                  headcount=base[code] + int(rng.integers(-6, 7))))
    pd.DataFrame(head_rows).to_csv(os.path.join(OUT, "headcount.csv"), index=False)

    projects.drop(columns=["margin_base", "start_idx", "end_idx"]).to_csv(
        os.path.join(OUT, "project.csv"), index=False)

    print(f"written to {OUT}")
    print(f"projects={len(projects)} revenue_rows={len(rev_rows)} "
          f"cost_rows={len(cost_rows)} contracts={len(contract_rows)}")


if __name__ == "__main__":
    main()
