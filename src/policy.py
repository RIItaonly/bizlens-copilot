"""制度检索：条款级命中，不做语义向量，只用字面重叠 + 标题加权。

为什么不直接上向量库：制度只有 10 条，字面匹配已经够用；
更重要的是，制度问答的价值在"引对条款"，不在"答得流畅"。
"""

from __future__ import annotations

import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STOP = set("的了吗呢和与及或在对把被为是有无怎么如何请问哪些多少")


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。；：、“”‘’（）()【】\[\]?!！？、]", "", text)
    return text


def _bigrams(text: str) -> set[str]:
    t = _normalize(text)
    return {t[i:i + 2] for i in range(len(t) - 1)
            if not (t[i] in STOP and t[i + 1] in STOP)}


class PolicyIndex:
    def __init__(self, path: str | None = None):
        path = path or os.path.join(ROOT, "data", "policy.md")
        raw = open(path, encoding="utf-8").read()
        self.clauses = []
        for m in re.finditer(r"^## (P-\d+)\s+(.+?)$", raw, re.M):
            start = m.end()
            nxt = re.search(r"^## P-\d+", raw[start:], re.M)
            body = raw[start:start + nxt.start()] if nxt else raw[start:]
            self.clauses.append({
                "id": m.group(1),
                "title": m.group(2).strip(),
                "text": body.strip(),
            })

    def search(self, question: str, k: int = 2):
        q = _bigrams(question)
        scored = []
        for c in self.clauses:
            body = _bigrams(c["title"] + c["text"])
            title = _bigrams(c["title"])
            overlap = len(q & body)
            bonus = 2.0 * len(q & title)
            score = (overlap + bonus) / math.sqrt(max(len(q), 1))
            scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        return [{"score": s, **c} for s, c in scored[:k]]


if __name__ == "__main__":
    idx = PolicyIndex()
    for q in ["合同首付款比例最高可以是多少？", "管理费用怎么分摊？",
              "项目收入按什么方法确认？", "毛利率低于多少触发复盘？"]:
        top = idx.search(q)[0]
        print(f"{q} → {top['id']} {top['title']} ({top['score']:.2f})")
