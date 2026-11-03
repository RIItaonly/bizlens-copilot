"""命令行入口：问一个问题，看答案、引用和 SQL。

用法：
    python src/cli.py "2025 年 Q4 华东区域的确认收入是多少？"
    python src/cli.py --sql "毛利率低于 10% 的项目有哪些？"   # 附带 SQL
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import json

from engine import ask                     # noqa: E402
from semantics.layer import SemanticLayer  # noqa: E402


def _columns(answer):
    ids = [c for c in ["contract_code", "project_code", "project_name", "region_name", "status"]
           if answer.data and c in answer.data[0]]
    metrics = [c.split(":", 1)[1] for c in answer.cites if c.startswith("metric:")]
    metrics = [m for m in metrics if answer.data and m in answer.data[0]]
    return ids + [m for m in metrics if m not in ids]


def render(answer) -> str:
    if answer.kind == "value":
        return f"{answer.data:,.2f}" if isinstance(answer.data, float) else str(answer.data)
    if answer.kind == "table":
        if not answer.data:
            return "（无符合条件的结果）"
        cols = _columns(answer)
        lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
        for row in answer.data:
            lines.append(" | ".join(
                f"{row[c]:,.4f}" if isinstance(row[c], float) else str(row[c]) for c in cols))
        return "\n".join(lines)
    return answer.text


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_sql = "--sql" in sys.argv
    if not args:
        print(__doc__)
        return
    layer = SemanticLayer(os.path.join(ROOT, "data", "seed"))
    answer = ask(layer, args[0])
    print(render(answer))
    print()
    print("引用：" + ", ".join(answer.cites))
    if show_sql and answer.sql:
        print("\nSQL：\n" + answer.sql.strip())


if __name__ == "__main__":
    main()
