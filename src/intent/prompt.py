"""意图解析的提示词构造：把 metrics.yaml 变成模型可选的"封闭菜单"。

核心思路：**不给模型自由发挥的空间**。
- 指标只能从目录里选（id 白名单）；
- 维度只能是期间/区域/项目；
- 输出必须是固定 JSON；
- 模型不负责计算，计算发生在语义层。

这样即使模型说错话，最坏结果是"选错指标"，而不是"编一个数"。
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)

from common import yamlish  # noqa: E402

PERIODS = ["2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4"]
REGIONS = ["华东", "华南", "华北"]

PLAN_SCHEMA = {
    "kind": "value | rank | qoq_rank",
    "metric": "指标 id，必须来自目录",
    "periods": ["期间列表，如 ['2025Q4'] 或四个季度"],
    "region": "区域名或 null",
    "project": "项目编码或 null",
    "n": "排名取前几，value 时为 1",
    "asc": "true=升序（最低/降幅），false=降序（最高/增幅）",
    "by": "project | region",
}


def load_catalog() -> list[dict]:
    doc = yamlish.loads(open(os.path.join(ROOT, "data", "metrics.yaml"),
                             encoding="utf-8").read())
    out = []
    for m in doc["metrics"]:
        out.append({
            "id": m["id"],
            "name": m["name"],
            "unit": m["unit"],
            "synonyms": m.get("synonyms") or [],
            "dimensions": m.get("dimensions") or [],
            "caveats": (m.get("caveats") or [])[:2],
        })
    return out


def catalog_text() -> str:
    lines = []
    for m in load_catalog():
        lines.append(f"- {m['id']}（{m['name']}，单位 {m['unit']}）"
                     f" 同义词：{'、'.join(m['synonyms'])}")
        for c in m["caveats"]:
            lines.append(f"    注意：{c}")
    return "\n".join(lines)


def system_prompt() -> str:
    return f"""你是企业经管系统的意图解析器，不是分析师。

你的唯一任务：把用户问题转换成一条查询计划。你不计算任何数字，不编造数据。

可用指标（只能从这里选 id）：
{catalog_text()}

可用维度：
- 期间：{', '.join(PERIODS)}（"2025 年全年"对应四个季度）
- 区域：{', '.join(REGIONS)}
- 项目：形如 P-1xx 的编码

输出必须是 JSON，字段如下：
{json.dumps(PLAN_SCHEMA, ensure_ascii=False, indent=2)}

规则：
1. metric 必须是目录里的 id，禁止发明。
2. 问题里出现"最低/最差/降幅"时 asc=true；"最高/最好/增幅"时 asc=false。
3. 问"几个/前几"时填 n；只问一个数时 kind=value。
4. 问"哪个区域"时 by=region；问"哪个项目"时 by=project。
5. 不确定的成分留 null，不要猜。
6. 只输出 JSON，不要解释。"""


def user_prompt(question: str) -> str:
    return f"问题：{question}\n\n请输出 JSON 查询计划。"


def messages(question: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_prompt(question)},
    ]


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "2025 年 Q4 华东区域的确认收入是多少？"
    print("=== SYSTEM ===")
    print(system_prompt())
    print("\n=== USER ===")
    print(user_prompt(q))
