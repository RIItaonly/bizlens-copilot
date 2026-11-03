"""LLM 意图解析器（OpenAI 兼容接口）。

⚠️ 状态：**接口已实现，未联调**。本机没有 API Key，也没有外网，
所以这份代码没有跑过真实模型。仓库里不能声称它有效。

它做的事只有一件：把问题翻译成 `router.Plan`。
校验失败就直接报错，绝不"猜一个"——宁可答不了，不可答错。

配置（环境变量）：
    BIZLENS_LLM_BASE_URL   例如 https://api.deepseek.com/v1
    BIZLENS_LLM_API_KEY
    BIZLENS_LLM_MODEL      例如 deepseek-chat

用法：
    python src/intent/llm_parser.py --dry-run "2025 年 Q4 华东的确认收入是多少？"
    python src/intent/llm_parser.py "2025 年 Q4 华东的确认收入是多少？"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

from intent.prompt import load_catalog, messages  # noqa: E402
from router import Plan                          # noqa: E402

VALID_KINDS = {"value", "rank", "qoq_rank"}
VALID_BY = {"project", "region"}


def payload_for(question: str, model: str) -> dict:
    return {
        "model": model,
        "messages": messages(question),
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


def _post(url: str, key: str, payload: dict, timeout: int = 30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _validate(raw: dict) -> dict:
    metric_ids = {m["id"] for m in load_catalog()}
    if raw.get("kind") not in VALID_KINDS:
        raise ValueError(f"非法 kind：{raw.get('kind')}")
    if raw.get("metric") not in metric_ids:
        raise ValueError(f"指标不在目录中：{raw.get('metric')}")
    if raw.get("by") not in VALID_BY:
        raise ValueError(f"非法 by：{raw.get('by')}")
    periods = raw.get("periods") or []
    if not periods:
        raise ValueError("periods 不能为空")
    return raw


def parse(question: str, dry_run: bool = False) -> Plan | dict:
    base = os.environ.get("BIZLENS_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("BIZLENS_LLM_API_KEY", "")
    model = os.environ.get("BIZLENS_LLM_MODEL", "deepseek-chat")

    payload = payload_for(question, model)
    if dry_run:
        return payload

    if not base or not key:
        raise RuntimeError("未配置 BIZLENS_LLM_BASE_URL / BIZLENS_LLM_API_KEY，"
                           "无法调用模型；可先用 --dry-run 查看请求体。")

    resp = _post(f"{base}/chat/completions", key, payload)
    content = resp["choices"][0]["message"]["content"]
    raw = _validate(json.loads(_strip_fence(content)))

    return Plan(
        kind=raw["kind"],
        metric=raw["metric"],
        periods=raw["periods"],
        region=raw.get("region"),
        project=raw.get("project"),
        n=int(raw.get("n") or 1),
        asc=bool(raw.get("asc")),
        by=raw["by"],
        raw_question=question,
        notes=["parsed by llm"],
    )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    q = args[0] if args else "2025 年 Q4 华东区域的确认收入是多少？"
    result = parse(q, dry_run=dry)
    if dry:
        print("=== 请求体（未发送）===")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    else:
        print(result)
