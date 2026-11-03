"""冒烟自检：不依赖评测集，直接断言关键行为。

跑法：
    python scripts/selftest.py

它验证的是"系统没坏"，不是"模型很准"：
数值、筛选、归因方向、制度命中、报告结构各一条。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from engine import ask, classify            # noqa: E402
from semantics.layer import SemanticLayer   # noqa: E402

LAYER = SemanticLayer(os.path.join(ROOT, "data", "seed"))
FAILED = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    # 1. 单指标
    a = ask(LAYER, "2025 年 Q4 华东区域的确认收入是多少？")
    check("单指标数值", abs(a.data - 36645300) < 1, f"got {a.data}")

    # 2. 筛选
    a = ask(LAYER, "2025 年 Q4 毛利率低于 10% 的项目有哪些？")
    check("筛选行数", len(a.data) == 4, f"got {len(a.data)}")
    check("筛选排序", a.data[0]["gross_margin"] < a.data[1]["gross_margin"])

    # 3. 归因方向
    a = ask(LAYER, "P-117 2025 年 Q4 毛利率下降，主要是收入端还是成本端造成的？")
    check("归因方向", "收入端" in a.text and "成本端" in a.text)

    # 4. 制度命中
    a = ask(LAYER, "合同首付款比例最高可以是多少？超过上限需要谁审批？")
    check("制度条款", "policy:P-07" in a.cites and "30%" in a.text)

    # 5. 报告结构
    a = ask(LAYER, "生成 2025 年 Q4 华东区域经营分析报告")
    check("报告章节", all(s in a.text for s in ["结论", "关键指标", "建议动作"]))

    # 6. 路由分类
    check("路由-制度", classify("经营数据什么时候出具？") == "policy")
    check("路由-筛选", classify("毛利率低于 10% 的项目有哪些？") == "filter")
    check("路由-报告", classify("生成 2025 年度经营总结") == "report")

    print(f"\n{len(FAILED)} failed" if FAILED else "\nALL PASS")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
