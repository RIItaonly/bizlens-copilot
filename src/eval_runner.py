"""评测执行器：跑任务集，比对答案与引用，生成报告。

支持六类任务的判定：
- scalar / object / table：结构化比对（T1–T3）
- structured：结论方向 + 引用（T4）
- text：条款引用 + 关键数字（T5）
- report：章节完整 + 关键数字 + 引用（T6）

用法：
    python src/eval_runner.py [tasks.yaml] [--split dev|holdout|all] [--tag 名称] [--out 路径]
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from common import yamlish          # noqa: E402
from semantics.layer import SemanticLayer  # noqa: E402
from engine import ask as engine_ask       # noqa: E402

NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*\s*%?")


def numbers_in(text: str) -> list[float]:
    out = []
    for tok in NUM_RE.findall(text):
        tok = tok.strip()
        is_pct = tok.endswith("%")
        raw = tok.rstrip("%").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.append(v / 100 if is_pct else v)
        out.append(v)
    return out


def has_number(text: str, value: float, tol: float = 0.01) -> bool:
    for v in numbers_in(text):
        if value == 0:
            if abs(v) < 1e-9:
                return True
        elif abs(v - value) / abs(value) <= tol:
            return True
    return False


def close(a, b, tol=0.005):
    if a is None:
        return False
    b = 0.0 if b is None else float(b)
    if b == 0:
        return abs(float(a)) < 1e-9
    return abs(float(a) - b) / abs(b) <= tol


def check(expected, got, order_sensitive=True):
    t = expected.get("type")
    tol = expected.get("tolerance", 0.005)

    if t == "scalar":
        ok = isinstance(got, (int, float)) and close(got, expected["value"], tol)
        return ok, f"期望 {expected['value']}，实际 {got}"

    if t == "object":
        if isinstance(got, list) and len(got) == 1:
            got = got[0]
        if not isinstance(got, dict):
            return False, f"期望对象，实际 {type(got).__name__}"
        for k, v in expected["properties"].items():
            if k not in got:
                return False, f"缺少字段 {k}"
            if isinstance(v, (int, float)):
                if not close(got[k], v, tol):
                    return False, f"{k} 期望 {v}，实际 {got[k]}"
            elif got[k] != v:
                return False, f"{k} 期望 {v}，实际 {got[k]}"
        return True, "OK"

    if t == "table":
        if not isinstance(got, list):
            return False, "期望表格"
        rows, cols = expected["rows"], expected["columns"]
        if len(got) != len(rows):
            return False, f"行数期望 {len(rows)}，实际 {len(got)}"
        key_col = cols[0]
        got_rows = got
        if not order_sensitive:
            keyed = {str(r.get(key_col)): r for r in got}
            if len(keyed) != len(got):
                return False, "结果存在重复对象"
            got_rows = [keyed.get(str(r[0])) for r in rows]
            if any(x is None for x in got_rows):
                missing = [r[0] for r, g in zip(rows, got_rows) if g is None]
                return False, f"缺少对象：{missing}"
        for i, (exp_row, got_row) in enumerate(zip(rows, got_rows)):
            for col, val in zip(cols, exp_row):
                g = got_row.get(col)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    if not close(g, val, tol):
                        return False, (f"第 {i+1} 行 {col} 期望 {val}，"
                                       f"实际 {g if g is None else round(g, 6)}")
                elif g != val:
                    return False, f"第 {i+1} 行 {col} 期望 {val}，实际 {g}"
        return True, "OK"

    return False, f"未知类型 {t}"


def grade(expected, answer, category):
    """按任务类型选择判定方式。"""
    t = expected.get("type")

    if t == "structured":
        hint = expected.get("answer_hint")
        if hint and hint not in answer.text:
            return False, f"结论方向不符：期望含「{hint}」"
        return True, "OK（结论方向）"

    if t == "text":
        points = expected.get("key_points") or []
        missing = []
        for p in points:
            for m in re.findall(r"\d+(?:\.\d+)?", p):
                if not has_number(answer.text, float(m)):
                    missing.append(m)
        if missing:
            return False, f"关键数字缺失：{sorted(set(missing))}"
        return True, "OK（关键数字齐全）"

    if t == "report":
        text = answer.text
        missing_sec = [s for s in expected.get("required_sections", []) if s not in text]
        if missing_sec:
            return False, f"缺少章节：{missing_sec}"
        for k, v in (expected.get("key_numbers") or {}).items():
            if not has_number(text, float(v)):
                return False, f"缺少关键数字 {k}={v}"
        return True, "OK（结构+数字）"

    return check(expected, answer.data, order_sensitive=(category == "T2"))


def load_metric_tables():
    doc = yamlish.loads(open(os.path.join(ROOT, "data", "metrics.yaml"),
                             encoding="utf-8").read())
    return {m["id"]: list(m.get("source_tables") or []) for m in doc["metrics"]}


def main():
    tasks_path = os.path.join(ROOT, "eval", "tasks_v1.yaml")
    split = "all"
    tag = ""
    out_path = os.path.join(ROOT, "eval", "report.md")
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--split" and i + 1 < len(args):
            split = args[i + 1]
        elif a == "--tag" and i + 1 < len(args):
            tag = args[i + 1]
        elif a == "--out" and i + 1 < len(args):
            out_path = args[i + 1]
        elif a.endswith(".yaml"):
            tasks_path = a

    doc = yamlish.loads(open(tasks_path, encoding="utf-8").read())
    by_id = {t["id"]: t for t in doc["tasks"]}
    base_doc = yamlish.loads(open(os.path.join(ROOT, "eval", "tasks_v1.yaml"),
                                  encoding="utf-8").read())
    by_id = {**{x["id"]: x for x in base_doc["tasks"]}, **by_id}
    layer = SemanticLayer(os.path.join(ROOT, "data", "seed"))

    rows, stats = [], {}
    for t in doc["tasks"]:
        if split != "all" and t.get("split") != split:
            continue

        expected = t.get("expected")
        must_cite = t.get("must_cite") or []
        if t.get("base"):
            base = by_id.get(t["base"])
            if base is None:
                rows.append((t, "失败", f"找不到基础任务 {t['base']}", False))
                continue
            expected = base["expected"]
            must_cite = must_cite or base.get("must_cite") or []
        if expected is None:
            rows.append((t, "失败", "缺少 expected", False))
            continue

        cite_ok = False
        try:
            ans = engine_ask(layer, t["question"])
            ok, msg = grade(expected, ans, t["category"])
            missing = [c for c in must_cite if c not in set(ans.cites)]
            cite_ok = not missing
            if ok and missing:
                msg = "答案对，但缺少引用：" + "、".join(missing)
                ok = False
        except Exception as e:                    # noqa: BLE001
            ok, msg = False, f"执行异常：{type(e).__name__}: {e}"

        rows.append((t, "通过" if ok else "失败", msg, cite_ok))
        key = (t.get("split", "dev"), t["category"])
        s = stats.setdefault(key, [0, 0, 0])
        s[0] += 1
        s[1] += 1 if ok else 0
        s[2] += 1 if cite_ok else 0

    print(f"{'ID':10} {'split':8} {'结果':6} {'引用':4} 说明")
    for t, status, msg, cite_ok in rows:
        print(f"{t['id']:10} {t.get('split','dev'):8} {status:6} "
              f"{'✓' if cite_ok else '✗':4} {msg}")

    total = sum(v[0] for v in stats.values())
    passed = sum(v[1] for v in stats.values())
    print(f"\n总计：{passed}/{total}")
    for (sp, cat), (tot, ps, ct) in sorted(stats.items()):
        print(f"  {sp:8} {cat}: {ps}/{tot}，引用 {ct}/{tot}")

    write_report(rows, stats, tag or os.path.basename(tasks_path), out_path)


def write_report(rows, stats, tag, out):
    total = sum(v[0] for v in stats.values())
    passed = sum(v[1] for v in stats.values())
    cited = sum(v[2] for v in stats.values())
    lines = [
        f"# 评测报告（{tag}）",
        "",
        "> 引擎：规则路由 + 语义层 + 归因/制度/报告模块（**不含大模型**）",
        "",
        "## 总体",
        "",
        f"- 任务数：{total}",
        f"- 通过：{passed}（{passed / total:.0%}）" if total else "- 通过：0",
        f"- 引用完整：{cited}/{total}",
        "",
        "## 分类",
        "",
        "| split | 类别 | 通过/总数 | 引用完整 |",
        "|---|---|---|---|",
    ]
    for (sp, cat), (tot, ps, ct) in sorted(stats.items()):
        lines.append(f"| {sp} | {cat} | {ps}/{tot} | {ct}/{tot} |")
    lines += ["", "## 逐条结果", "",
              "| ID | split | 结果 | 引用 | 说明 |", "|---|---|---|---|---|"]
    for t, status, msg, cite_ok in rows:
        lines.append(f"| {t['id']} | {t.get('split','dev')} | {status} | "
                     f"{'✓' if cite_ok else '✗'} | {msg} |")
    lines += [
        "",
        "## 判定口径",
        "",
        "- T1/T3：结构化比对（T3 不校验行序，只校验集合与数值）",
        "- T2：结构化比对 + 行序",
        "- T4：只校验结论方向与引用，**细节质量需人工按 rubric 评分**",
        "- T5：条款引用 + 关键数字齐全",
        "- T6：章节齐全 + 关键数字齐全 + 引用",
        "",
        "## 有效性说明",
        "",
        "1. 通过率高只说明**规则覆盖了这些模板问法**，不说明理解能力。",
        "2. 改写测试（paraphrase_v1）通过率远低于主集，那才是真实泛化水平。",
        "3. holdout 已被本轮调试污染，不能当作干净证据。",
        "4. T4/T6 的自动判定是“下限检查”，不评价文案质量。",
        "",
    ]
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n报告已写入：{out}")


if __name__ == "__main__":
    main()
