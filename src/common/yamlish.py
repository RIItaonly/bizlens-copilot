"""极简 YAML 子集解析器。

为什么不用 PyYAML：本项目要求"克隆即可运行"，不引入额外依赖。
只支持我们自己会写的语法，够用即可：

- 块映射 / 块序列（`- key: value` 与后续同缩进键）
- 流式序列 `[a, b]`、流式映射 `{k: v}`
- 双引号字符串（JSON 转义）、单引号字符串
- 折行块 `>` 与保留块 `|`
- 整数 / 浮点 / true / false / null
- 整行注释 `#`

不支持：锚点、标签、多文档、复杂嵌套流式结构。
"""

from __future__ import annotations

import json
import re


class YamlError(ValueError):
    pass


def _strip_comments(text: str) -> list[tuple[int, str, int]]:
    out = []
    for no, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        stripped = raw.lstrip(" ")
        if stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)
        if "\t" in raw[:indent]:
            raise YamlError(f"第 {no} 行使用了制表符缩进")
        out.append((indent, stripped.rstrip(), no))
    return out


def _split_flow(s: str) -> list[str]:
    parts, depth, buf, quote = [], 0, [], None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _find_colon(s: str) -> int:
    depth, quote = 0, None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            if i + 1 == len(s) or s[i + 1] == " ":
                return i
    return -1


def parse_scalar(s: str):
    s = s.strip()
    if s == "":
        return None
    if s.startswith('"') and s.endswith('"'):
        return json.loads(s)
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [] if not inner else [parse_scalar(p) for p in _split_flow(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        d = {}
        if inner:
            for part in _split_flow(inner):
                k = _find_colon(part)
                if k < 0:
                    raise YamlError(f"流式映射项缺少冒号: {part}")
                d[parse_scalar(part[:k])] = parse_scalar(part[k + 1:])
        return d
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+([eE][-+]?\d+)?", s):
        return float(s)
    return s


class _Parser:
    def __init__(self, lines):
        self.lines = lines
        self.i = 0

    def peek(self):
        return self.lines[self.i] if self.i < len(self.lines) else None

    def parse_block(self, indent: int):
        line = self.peek()
        if line is None or line[0] < indent:
            return None
        if line[1].startswith("- "):
            return self.parse_seq(line[0])
        return self.parse_map(line[0])

    def parse_map(self, indent: int, seed: dict | None = None):
        result = dict(seed or {})
        while True:
            line = self.peek()
            if line is None or line[0] < indent:
                break
            if line[0] > indent:
                raise YamlError(f"第 {line[2]} 行缩进异常")
            if line[1].startswith("- "):
                break
            key_at = _find_colon(line[1])
            if key_at < 0:
                raise YamlError(f"第 {line[2]} 行不是 key: value")
            key = parse_scalar(line[1][:key_at])
            rest = line[1][key_at + 1:].strip()
            self.i += 1
            if rest in (">", "|"):
                result[key] = self.parse_block_scalar(line[0], rest)
            elif rest:
                result[key] = parse_scalar(rest)
            else:
                nxt = self.peek()
                if nxt is not None and nxt[0] > indent:
                    result[key] = self.parse_block(nxt[0])
                elif nxt is not None and nxt[0] == indent and nxt[1].startswith("- "):
                    result[key] = self.parse_seq(indent)
                else:
                    result[key] = None
        return result

    def parse_block_scalar(self, indent: int, style: str) -> str:
        parts = []
        start_no = self.lines[self.i - 1][2]
        while True:
            line = self.peek()
            if line is None or line[0] <= indent:
                break
            parts.append(line[1])
            self.i += 1
        if not parts:
            return ""
        joiner = "\n" if style == "|" else " "
        return joiner.join(parts)

    def parse_seq(self, indent: int):
        items = []
        while True:
            line = self.peek()
            if line is None or line[0] != indent or not line[1].startswith("- "):
                break
            body = line[1][2:].strip()
            self.i += 1
            if body == "":
                nxt = self.peek()
                items.append(self.parse_block(nxt[0]) if nxt and nxt[0] > indent else None)
                continue
            if body.startswith("{") or body.startswith("["):
                items.append(parse_scalar(body))
                continue
            key_at = _find_colon(body)
            if key_at < 0:
                items.append(parse_scalar(body))
                continue
            # - key: value  → 从这里开始是一个映射，后续行缩进为 indent+2
            key = parse_scalar(body[:key_at])
            rest = body[key_at + 1:].strip()
            seed = {}
            if rest in (">", "|"):
                seed[key] = self.parse_block_scalar(indent, rest)
            elif rest:
                seed[key] = parse_scalar(rest)
            else:
                nxt = self.peek()
                seed[key] = self.parse_block(nxt[0]) if nxt and nxt[0] > indent + 2 else None
            items.append(self.parse_map(indent + 2, seed))
        return items


def loads(text: str):
    lines = _strip_comments(text)
    if not lines:
        return None
    parser = _Parser(lines)
    value = parser.parse_block(lines[0][0])
    if parser.i != len(lines):
        raise YamlError(f"第 {lines[parser.i][2]} 行未能解析")
    return value
