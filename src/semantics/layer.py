"""语义层：把 metrics.yaml 的口径编译成 SQL，并在 SQLite 上执行。

设计要点（对应方案书 D1）：
- 模型/路由只能"选指标 + 选维度"，不能自己写 SQL；
- 指标表达式集中在这里，改口径只改一处；
- 维度过滤（期间/区域/项目）由编译器统一拼接，避免每处手写。
"""

from __future__ import annotations

import os
import sqlite3

import pandas as pd

# 口径 → SQL 表达式。只有这里可以出现聚合逻辑。
EXPR = {
    "recognized_revenue": "SUM(f.revenue)",
    "project_cost": "SUM(f.cost)",
    "gross_margin": "SUM(f.revenue - f.cost) * 1.0 / NULLIF(SUM(f.revenue), 0)",
    "collection_rate": "SUM(f.collected) * 1.0 / NULLIF(SUM(f.invoiced), 0)",
    "schedule_variance": "SUM(f.revenue - f.planned) * 1.0 / NULLIF(SUM(f.planned), 0)",
}

CONST = {"expense_ratio": 0.13}

YEAR_PERIODS = ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]

# 项目级聚合字段（project_table 的输出列名）
AGG_COL = {
    "recognized_revenue": "revenue",
    "project_cost": "cost",
    "gross_margin": "gross_margin",
    "collection_rate": "collection_rate",
    "schedule_variance": "schedule_variance",
}


class SemanticLayer:
    def __init__(self, seed_dir: str):
        self.seed_dir = seed_dir
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.last_sql = ""
        self._build()

    # ---------------------------------------------------------------- build
    def _read(self, name):
        return pd.read_csv(os.path.join(self.seed_dir, f"{name}.csv"))

    def _build(self):
        rev = self._read("revenue")
        cost = self._read("cost")
        plan = self._read("plan")
        proj = self._read("project")
        org = self._read("org")
        head = self._read("headcount")
        cont = self._read("contract")

        cost_sum = cost.groupby(["project_code", "period"], as_index=False)["amount"].sum()
        cost_sum = cost_sum.rename(columns={"amount": "cost"})

        fact = rev.merge(proj[["project_code", "project_name", "region_name", "status"]],
                         on="project_code", how="left")
        fact = fact.merge(cost_sum, on=["project_code", "period"], how="left")
        fact = fact.merge(plan, on=["project_code", "period"], how="left")
        fact[["cost", "planned_revenue"]] = fact[["cost", "planned_revenue"]].fillna(0)
        fact = fact.rename(columns={"planned_revenue": "planned"})
        fact = fact[["project_code", "project_name", "region_name", "status", "period",
                     "recognized_revenue", "invoiced_amount", "collected_amount",
                     "cost", "planned"]]
        fact.columns = ["project_code", "project_name", "region_name", "status", "period",
                        "revenue", "invoiced", "collected", "cost", "planned"]

        head = head.merge(org[["region_code", "region_name"]], on="region_code", how="left")
        cont = cont.merge(proj[["project_code", "region_name"]], on="project_code", how="left")

        fact.to_sql("fact", self.conn, index=False)
        head.to_sql("headcount", self.conn, index=False)
        cont.to_sql("contract", self.conn, index=False)
        proj.to_sql("project", self.conn, index=False)
        cost.to_sql("cost_detail", self.conn, index=False)

    # ---------------------------------------------------------------- utils
    @staticmethod
    def _where(period=None, region=None, project=None, alias="f"):
        clauses, params = [], []
        if period:
            ps = list(period) if isinstance(period, (list, tuple)) else [period]
            clauses.append(f"{alias}.period IN ({','.join('?' * len(ps))})")
            params.extend(ps)
        if region:
            clauses.append(f"{alias}.region_name = ?")
            params.append(region)
        if project:
            clauses.append(f"{alias}.project_code = ?")
            params.append(project)
        sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return sql, params

    def _one(self, sql, params):
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    # ---------------------------------------------------------------- metrics
    def value(self, metric: str, period=None, region=None, project=None):
        if metric in CONST:
            return CONST[metric]

        if metric == "headcount":
            ps = list(period) if isinstance(period, (list, tuple)) else [period]
            sql = ("SELECT headcount FROM headcount WHERE period = ?"
                   + (" AND region_name = ?" if region else ""))
            params = [ps[-1]]
            if region:
                params.append(region)
            return self._one(sql, params)

        if metric == "contract_amount":
            where, params = [], []
            if period:
                ps = list(period) if isinstance(period, (list, tuple)) else [period]
                where.append(f"signed_period IN ({','.join('?' * len(ps))})")
                params.extend(ps)
            if region:
                where.append("region_name = ?")
                params.append(region)
            if project:
                where.append("project_code = ?")
                params.append(project)
            cond = " AND ".join(where) if where else "1=1"
            sql = f"SELECT SUM(amount) FROM contract WHERE status = '有效' AND {cond}"
            return self._one(sql, params)

        if metric == "revenue_per_head":
            ps = list(period) if isinstance(period, (list, tuple)) else [period]
            rev = self.value("recognized_revenue", ps, region, project)
            head = self.value("headcount", ps, region, None)
            return None if not head else rev / head

        expr = EXPR[metric]
        where, params = self._where(period, region, project)
        return self._one(f"SELECT {expr} FROM fact f{where}", params)

    # ---------------------------------------------------------------- ranking
    def rank(self, metric: str, period, n: int, asc: bool, by: str = "project"):
        order = "ASC" if asc else "DESC"
        if metric == "contract_amount":
            group = "project_code" if by == "project" else "region_name"
            ps = list(period) if isinstance(period, (list, tuple)) else [period]
            sql = (f"SELECT {group} AS k, SUM(amount) AS v FROM contract "
                   f"WHERE status='有效' AND signed_period IN ({','.join('?' * len(ps))}) "
                   f"GROUP BY k ORDER BY v {order} LIMIT ?")
            params = list(ps) + [n]
            return [dict(r) for r in self.conn.execute(sql, params)]
        elif metric == "revenue_per_head":
            ps = list(period) if isinstance(period, (list, tuple)) else [period]
            rev = pd.read_sql(
                f"SELECT region_name AS k, {EXPR['recognized_revenue']} AS v FROM fact f "
                f"WHERE period IN ({','.join('?' * len(ps))}) GROUP BY region_name",
                self.conn, params=ps)
            head = pd.read_sql(
                f"SELECT region_name AS k, headcount AS h FROM headcount "
                f"WHERE period = ?", self.conn, params=[ps[-1]])
            merged = rev.merge(head, on="k")
            merged["v"] = merged.v / merged.h
            merged = merged.sort_values("v", ascending=asc).head(n)
            return merged[["k", "v"]].to_dict("records")
        elif metric == "headcount":
            ps = list(period) if isinstance(period, (list, tuple)) else [period]
            sql = ("SELECT region_name AS k, headcount AS v FROM headcount "
                   "WHERE period = ? ORDER BY v " + order + " LIMIT ?")
            return [dict(r) for r in self.conn.execute(sql, [ps[-1], n])]

        else:
            expr = EXPR[metric]
            group = "f.project_code" if by == "project" else "f.region_name"
            where, params = self._where(period)
            sql = (f"SELECT {group} AS k, {expr} AS v FROM fact f{where} "
                   f"GROUP BY k HAVING v IS NOT NULL ORDER BY v {order} LIMIT ?")
            params.append(n)
            return [dict(r) for r in self.conn.execute(sql, params)]

    def qoq_change(self, metric: str, period: str, prev: str, n: int, asc: bool,
                   by: str = "project"):
        """按环比变化率排名（只在两期都有值的对象上比较）。"""
        if by == "project":
            keys = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT f.project_code FROM fact f").fetchall()]
            kw = "project"
        else:
            keys = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT f.region_name FROM fact f").fetchall()]
            kw = "region"

        rows = []
        for k in keys:
            v_now = self.value(metric, period, **{kw: k})
            v_prev = self.value(metric, prev, **{kw: k})
            if not v_now or not v_prev:
                continue
            rows.append({"k": k, "v": v_now / v_prev - 1, "now": v_now, "prev": v_prev})
        rows.sort(key=lambda r: r["v"], reverse=not asc)
        return rows[:n]

    def project_name(self, code: str):
        row = self.conn.execute(
            "SELECT project_name FROM project WHERE project_code = ?", [code]).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- 结构查询
    def project_table(self, periods, conditions=None, status=None, order=None):
        """按项目聚合到指标层，再按条件过滤。

        conditions: [(指标id, 比较符, 阈值)]，例如
            [("gross_margin", "<", 0.10), ("collection_rate", "<", 0.70)]
        order: (列名, 是否升序)
        """
        ps = list(periods)
        marks = ",".join("?" * len(ps))
        sql = f"""
            WITH agg AS (
                SELECT f.project_code, f.project_name, f.region_name, f.status,
                       SUM(f.revenue)  AS revenue,
                       SUM(f.cost)     AS cost,
                       SUM(f.invoiced) AS invoiced,
                       SUM(f.collected) AS collected,
                       SUM(f.planned)  AS planned
                FROM fact f
                WHERE f.period IN ({marks})
                GROUP BY f.project_code
            )
            SELECT project_code, project_name, region_name, status, revenue, cost,
                   CASE WHEN revenue > 0 THEN (revenue - cost) * 1.0 / revenue END
                       AS gross_margin,
                   CASE WHEN invoiced > 0 THEN collected * 1.0 / invoiced END
                       AS collection_rate,
                   CASE WHEN planned > 0 THEN (revenue - planned) * 1.0 / planned END
                       AS schedule_variance
            FROM agg
        """
        clauses, params = [], list(ps)
        for metric, op, value in conditions or []:
            col = AGG_COL.get(metric, metric)
            clauses.append(f"t.{col} {op} ?")
            params.append(value)
        if status:
            clauses.append("t.status = ?")
            params.append(status)

        sql = f"SELECT * FROM ({sql}) t"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if order:
            col, asc = order
            sql += f" ORDER BY t.{AGG_COL.get(col, col)} {'ASC' if asc else 'DESC'}"
        self.last_sql = sql
        return [dict(r) for r in self.conn.execute(sql, params)]

    def contracts_over_ratio(self, threshold: float):
        sql = ("SELECT contract_code, project_code, region_name, first_payment_ratio, amount "
               "FROM contract WHERE status = '有效' AND first_payment_ratio > ? "
               "ORDER BY first_payment_ratio DESC")
        self.last_sql = sql
        return [dict(r) for r in self.conn.execute(sql, [threshold])]

    def period_totals(self, periods, region=None, project=None) -> dict:
        ps = list(periods)
        where, params = self._where(ps, region, project)
        sql = (f"SELECT SUM(f.revenue) AS revenue, SUM(f.cost) AS cost, "
               f"SUM(f.invoiced) AS invoiced, SUM(f.collected) AS collected, "
               f"SUM(f.planned) AS planned FROM fact f{where}")
        self.last_sql = sql
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else {}

    def cost_by_type(self, periods, region=None, project=None) -> dict:
        ps = list(periods)
        marks = ",".join("?" * len(ps))
        clauses = [f"period IN ({marks})"]
        params = list(ps)
        if region:
            clauses.append("region_name = ?")
            params.append(region)
        if project:
            clauses.append("project_code = ?")
            params.append(project)
        sql = (f"SELECT cost_type, SUM(amount) AS amount FROM cost_detail "
               f"WHERE {' AND '.join(clauses)} GROUP BY cost_type")
        self.last_sql = sql
        return {r[0]: r[1] for r in self.conn.execute(sql, params)}

    def count_projects(self, periods, conditions=None, status=None) -> int:
        return len(self.project_table(periods, conditions, status))

    def projects_without_revenue(self, period: str, status: str = "在建"):
        """数据完整性检查：状态为在建、但当期没有确认收入的项目。"""
        sql = """
            SELECT p.project_code, p.project_name, p.status
            FROM project p
            LEFT JOIN fact f
              ON f.project_code = p.project_code AND f.period = ?
            WHERE p.status = ? AND COALESCE(f.revenue, 0) = 0
            ORDER BY p.project_code
        """
        self.last_sql = sql
        return [dict(r) for r in self.conn.execute(sql, [period, status])]
