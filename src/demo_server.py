"""BizLens 演示界面：单页应用 + 轻量 API。

- 零第三方依赖（标准库 http.server），本地启动即可用
- 前端按答案类型渲染：数值卡 / 表格 / 报告 / 结论
- 每条答案展示引用与 SQL，可复制

用法：
    python src/demo_server.py [端口]
    打开 http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from common import yamlish                 # noqa: E402
from engine import ask, classify           # noqa: E402
from semantics.layer import SemanticLayer  # noqa: E402

LAYER = SemanticLayer(os.path.join(ROOT, "data", "seed"))


def load_metrics():
    doc = yamlish.loads(open(os.path.join(ROOT, "data", "metrics.yaml"),
                             encoding="utf-8").read())
    out = {}
    for m in doc["metrics"]:
        out[m["id"]] = {"name": m["name"], "unit": m["unit"]}
    return out


METRICS = load_metrics()


def fmt_kind(metric_id):
    unit = METRICS.get(metric_id, {}).get("unit", "")
    if unit == "%":
        return "percent"
    if unit in ("元", "元/人"):
        return "money"
    if unit == "人":
        return "int"
    return "text"


def columns_for(ans):
    if not ans.data:
        return [], {}
    rows = ans.data
    ids = [c for c in ["contract_code", "project_code", "project_name", "region_name",
                       "status"] if c in rows[0]]
    metrics = [c.split(":", 1)[1] for c in ans.cites if c.startswith("metric:")]
    cols = ids + [m for m in metrics if m in rows[0] and m not in ids]
    formats = {c: "text" for c in ids}
    for m in metrics:
        if m in cols:
            formats[m] = fmt_kind(m)
    for extra in ("qoq_change", "first_payment_ratio"):
        if rows and extra in rows[0]:
            cols.append(extra)
            formats[extra] = "percent"
    return cols, formats


def payload(question: str):
    t0 = time.time()
    ans = ask(LAYER, question)
    elapsed = int((time.time() - t0) * 1000)
    cols, formats = columns_for(ans) if ans.kind == "table" else ([], {})

    body = {
        "kind": ans.kind,
        "text": ans.text,
        "data": ans.data if ans.kind in ("table", "value") else None,
        "columns": cols,
        "formats": formats,
        "cites": ans.cites,
        "sql": (ans.sql or "").strip(),
        "elapsed_ms": elapsed,
        "intent": "value" if ans.kind == "value" else classify(question),
        "metric": None,
        "unit": "",
    }

    if ans.kind == "table":
        body["count"] = len(ans.data or [])
    if ans.kind == "value":
        metric = next((c.split(":", 1)[1] for c in ans.cites if c.startswith("metric:")), None)
        body["metric"] = METRICS.get(metric, {}).get("name", metric)
        body["unit"] = METRICS.get(metric, {}).get("unit", "")
        body["format"] = fmt_kind(metric or "")
    return body


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BizLens · 企业经营分析 Copilot</title>
<style>
  :root{
    --bg:#f6f7f9; --card:#ffffff; --line:#e6e8eb; --text:#0f172a; --muted:#64748b;
    --blue:#2563eb; --blue-soft:#eff6ff; --green:#16a34a; --red:#dc2626;
    --amber:#b45309; --amber-soft:#fffbeb; --radius:14px;
    --shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px -12px rgba(15,23,42,.18);
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0;background:var(--bg);color:var(--text);
    font-family:system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    font-size:14px;line-height:1.6;
  }
  .app{display:grid;grid-template-columns:288px 1fr;min-height:100vh}
  /* ---------- sidebar ---------- */
  aside{
    border-right:1px solid var(--line);background:#fbfbfc;
    padding:22px 18px;position:sticky;top:0;height:100vh;overflow:auto;
  }
  .brand{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .mark{
    width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,#2563eb,#7c3aed);
    display:grid;place-items:center;color:#fff;font-weight:700;font-size:15px;
  }
  .brand h1{font-size:15px;margin:0;letter-spacing:.2px}
  .brand p{margin:0;font-size:12px;color:var(--muted)}
  .stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:18px 0}
  .stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
  .stat b{display:block;font-size:17px;font-variant-numeric:tabular-nums}
  .stat span{font-size:11px;color:var(--muted)}
  .nav-title{font-size:11px;color:var(--muted);letter-spacing:.08em;margin:20px 0 8px;text-transform:uppercase}
  .grp{margin-bottom:14px}
  .grp h3{font-size:12px;margin:0 0 6px;color:#334155}
  .chip{
    display:block;width:100%;text-align:left;background:transparent;border:0;
    color:#475569;font-size:12.5px;padding:6px 8px;border-radius:8px;cursor:pointer;
    line-height:1.45;
  }
  .chip:hover{background:var(--blue-soft);color:var(--blue)}
  .side-note{
    margin-top:18px;padding:10px 12px;border:1px dashed #d8dee6;border-radius:10px;
    font-size:11.5px;color:var(--muted);background:#fff;
  }
  /* ---------- main ---------- */
  main{display:flex;flex-direction:column;min-height:100vh}
  header{
    padding:16px 32px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.85);
    backdrop-filter:blur(8px);position:sticky;top:0;z-index:5;
    display:flex;align-items:center;justify-content:space-between;gap:16px;
  }
  header .t{font-weight:600;font-size:14px}
  header .s{font-size:12px;color:var(--muted)}
  .badges{display:flex;gap:6px;flex-wrap:wrap}
  .badge{
    font-size:11px;padding:3px 8px;border-radius:999px;border:1px solid var(--line);
    background:#fff;color:#475569;white-space:nowrap;
  }
  .badge.good{background:#f0fdf4;border-color:#bbf7d0;color:#15803d}
  .badge.warn{background:var(--amber-soft);border-color:#fde68a;color:var(--amber)}
  .scroll{flex:1;overflow:auto;padding:28px 32px 140px}
  .wrap{max-width:900px;margin:0 auto}
  /* hero */
  .hero{padding:26px 0 8px}
  .hero h2{font-size:26px;margin:0 0 8px;letter-spacing:-.01em}
  .hero p{margin:0;color:var(--muted);max-width:640px}
  .cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:22px 0 6px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow)}
  .card b{display:block;font-size:13px;margin-bottom:4px}
  .card span{font-size:12px;color:var(--muted)}
  /* messages */
  .msg{display:flex;gap:10px;margin:22px 0}
  .msg.user{justify-content:flex-end}
  .bubble{
    background:var(--blue);color:#fff;padding:10px 14px;border-radius:14px 14px 4px 14px;
    max-width:70%;box-shadow:var(--shadow);font-size:13.5px;
  }
  .answer{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);width:100%;overflow:hidden}
  .ans-head{display:flex;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid var(--line);background:#fcfcfd}
  .ans-head .tag{font-size:11px;padding:2px 7px;border-radius:6px;background:var(--blue-soft);color:var(--blue)}
  .ans-head .meta{margin-left:auto;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
  .ans-body{padding:16px}
  /* value */
  .value{display:flex;align-items:baseline;gap:10px}
  .value .n{font-size:38px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
  .value .u{color:var(--muted);font-size:14px}
  .value-label{color:var(--muted);font-size:12.5px;margin-bottom:6px}
  /* table */
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;font-weight:600;color:#475569;font-size:11.5px;letter-spacing:.03em;
     border-bottom:1px solid var(--line);padding:8px 10px;background:#fafbfc}
  td{padding:9px 10px;border-bottom:1px solid #f1f3f5}
  tr:last-child td{border-bottom:0}
  tr:hover td{background:#fbfcfe}
  .num{text-align:right;font-variant-numeric:tabular-nums}
  .neg{color:var(--red)}
  .pos{color:var(--green)}
  .mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
  /* report */
  .report h1{font-size:19px;margin:0 0 12px}
  .report h2{font-size:13px;margin:18px 0 8px;color:#334155;letter-spacing:.02em}
  .report ul{margin:0;padding-left:18px}
  .report li{margin:4px 0}
  .report p{margin:6px 0;color:#334155}
  /* tabs */
  .tabs{display:flex;gap:4px;border-top:1px solid var(--line);background:#fcfcfd;padding:8px 12px}
  .tab{border:0;background:transparent;font-size:12px;color:var(--muted);padding:5px 9px;border-radius:7px;cursor:pointer}
  .tab[aria-selected="true"]{background:#fff;border:1px solid var(--line);color:var(--text)}
  .pane{padding:14px 16px;border-top:1px solid var(--line);display:none}
  .pane.on{display:block}
  pre{margin:0;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;
      font-size:12px;line-height:1.55;overflow:auto;max-height:260px}
  .cites{display:flex;flex-wrap:wrap;gap:6px}
  .cite{font-size:11.5px;padding:4px 9px;border-radius:999px;border:1px solid var(--line);background:#fff;color:#475569}
  .cite.metric{background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8}
  .cite.policy{background:#fffbeb;border-color:#fde68a;color:#92400e}
  .cite.table{background:#f8fafc}
  /* composer */
  .composer{position:fixed;left:288px;right:0;bottom:0;padding:14px 32px 20px;
            background:linear-gradient(to top,var(--bg) 60%,rgba(246,247,249,.6))}
  .composer .box{max-width:900px;margin:0 auto;display:flex;gap:8px;align-items:center;
                 background:#fff;border:1px solid var(--line);border-radius:14px;padding:8px 8px 8px 14px;box-shadow:var(--shadow)}
  .composer input{flex:1;border:0;outline:0;font-size:14px;background:transparent;color:var(--text)}
  .composer button{
    border:0;border-radius:10px;background:var(--blue);color:#fff;font-size:13px;
    padding:9px 16px;cursor:pointer;font-weight:500;
  }
  .composer button:disabled{opacity:.55;cursor:default}
  .hint{max-width:900px;margin:8px auto 0;font-size:11px;color:var(--muted);text-align:center}
  .loading{display:inline-block;width:12px;height:12px;border:2px solid #c7d2fe;border-top-color:var(--blue);
           border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px;margin-right:6px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .empty{color:var(--muted);font-size:13px;text-align:center;padding:40px 0}
  @media (max-width:900px){
    .app{grid-template-columns:1fr}
    aside{display:none}
    .composer{left:0}
    .cards{grid-template-columns:1fr}
    header,.scroll,.composer{padding-left:16px;padding-right:16px}
  }
</style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand">
      <div class="mark">B</div>
      <div><h1>BizLens</h1><p>企业经营分析 Copilot</p></div>
    </div>
    <div class="stats">
      <div class="stat"><b>60/60</b><span>主集通过</span></div>
      <div class="stat"><b>9</b><span>指标口径</span></div>
      <div class="stat"><b>10</b><span>制度条款</span></div>
      <div class="stat"><b>3/10</b><span>改写集</span></div>
    </div>

    <div class="nav-title">试试这些</div>
    <div id="examples"></div>

    <div class="side-note">
      当前是<b>规则路由</b>：模板问法稳定，自由表达会失败（改写集 3/10）。
      数字全部由 SQL 计算，模型不参与。
    </div>
  </aside>

  <main>
    <header>
      <div>
        <div class="t">经营分析工作台</div>
        <div class="s">问一句，拿回一个带证据的结论</div>
      </div>
      <div class="badges">
        <span class="badge good">主集 60/60</span>
        <span class="badge">引用完整 60/60</span>
        <span class="badge warn">改写集 3/10</span>
        <span class="badge">合成数据</span>
      </div>
    </header>

    <div class="scroll" id="scroll">
      <div class="wrap">
        <section class="hero" id="hero">
          <h2>把"哪个项目亏了、为什么、怎么办"变成一条可追溯的链路</h2>
          <p>自然语言问题 → 指标语义层 → SQL → 结构化结论。每个数字都能点回它来自哪张表、哪个口径。</p>
          <div class="cards">
            <div class="card"><b>数字不让模型算</b><span>聚合、除法、NULL 处理都在语义层，模型只做选择。</span></div>
            <div class="card"><b>结论必须带证据</b><span>每条答案附指标 / 表 / 条款引用，以及可复制的 SQL。</span></div>
            <div class="card"><b>评测先说边界</b><span>主集满分不等于泛化，改写集 3/10 才是现状。</span></div>
          </div>
        </section>
        <div id="thread"></div>
      </div>
    </div>

    <div class="composer">
      <div class="box">
        <input id="q" placeholder="问点什么，比如：2025 年 Q4 毛利率最低的 5 个项目是哪些？" autocomplete="off">
        <button id="send">提问</button>
      </div>
      <div class="hint">Enter 发送 · 数据为合成数据 · 规则路由覆盖受控问法</div>
    </div>
  </main>
</div>

<script>
const EXAMPLES = [
  ["查询", ["2025 年 Q4 华东区域的确认收入是多少？", "P-103 在 2025 年全年的确认收入是多少？"]],
  ["排名", ["2025 年 Q4 毛利率最低的 5 个项目是哪些？", "2025 年全年确认收入最高的 3 个项目是哪些？"]],
  ["异常", ["2025 年 Q4 毛利率低于 10% 的项目有哪些？", "2025 年 Q4 进度偏差低于 -30% 的项目有哪些？"]],
  ["归因", ["P-117 2025 年 Q4 毛利率下降，主要是收入端还是成本端造成的？", "2025 年 Q4 华东区域确认收入环比下降，主要是哪个项目造成的？"]],
  ["制度", ["合同首付款比例最高可以是多少？超过上限需要谁审批？", "项目毛利率低于多少会触发专项复盘？"]],
  ["报告", ["生成 2025 年 Q4 华东区域经营分析报告", "给管理层生成一页纸经营摘要（2025 年 Q4）"]],
];

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function buildExamples(){
  const box = $("#examples");
  box.innerHTML = EXAMPLES.map(([g, items]) => `
    <div class="grp"><h3>${g}</h3>
      ${items.map(q => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
    </div>`).join("");
  box.querySelectorAll(".chip").forEach(el => el.onclick = () => ask(el.dataset.q));
}

const money = (v) => "¥" + Number(v).toLocaleString("zh-CN", {maximumFractionDigits: 0});
const pct = (v) => (Number(v) * 100).toFixed(2) + "%";
function fmtCell(v, kind){
  if (v === null || v === undefined) return "—";
  if (kind === "money") return money(v);
  if (kind === "percent") return pct(v);
  if (kind === "int") return Number(v).toLocaleString("zh-CN");
  return esc(v);
}
function cls(v, kind){
  if (kind === "percent" && Number(v) < 0) return "num neg";
  if (kind === "money" && Number(v) < 0) return "num neg";
  if (kind === "percent" || kind === "money" || kind === "int") return "num";
  return "";
}

function renderTable(d){
  if (!d.data || !d.data.length) return `<div class="empty">没有符合条件的结果</div>`;
  const cols = d.columns;
  const head = cols.map(c => `<th>${esc(c)}</th>`).join("");
  const rows = d.data.map(r => `<tr>${cols.map(c => {
    const kind = d.formats[c] || "text";
    return `<td class="${cls(r[c], kind)}">${fmtCell(r[c], kind)}</td>`;
  }).join("")}</tr>`).join("");
  return `<div style="overflow:auto"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>
          <div style="margin-top:10px;font-size:12px;color:var(--muted)">共 ${d.data.length} 行</div>`;
}

function renderValue(d){
  const raw = d.data;
  let shown;
  if (d.format === "money") shown = money(raw);
  else if (d.format === "percent") shown = pct(raw);
  else if (d.format === "int") shown = Number(raw).toLocaleString("zh-CN");
  else shown = Number(raw).toLocaleString("zh-CN", {maximumFractionDigits: 4});
  return `<div class="value-label">${esc(d.metric || "")}</div>
          <div class="value"><span class="n">${shown}</span><span class="u">${esc(d.unit || "")}</span></div>`;
}

/* 极简 Markdown：标题 / 列表 / 表格 / 粗体 */
function renderMd(md){
  const lines = String(md).split("\n");
  let html = "", i = 0, inList = false;
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const closeList = () => { if (inList){ html += "</ul>"; inList = false; } };

  while (i < lines.length){
    const line = lines[i];
    if (/^\|/.test(line) && i + 1 < lines.length && /^\|[\s\-:|]+\|?$/.test(lines[i+1])){
      closeList();
      const cells = (l) => l.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      html += "<table><thead><tr>" + cells(line).map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
      i += 2;
      while (i < lines.length && /^\|/.test(lines[i])){
        html += "<tr>" + cells(lines[i]).map(c => `<td>${inline(c)}</td>`).join("") + "</tr>";
        i++;
      }
      html += "</tbody></table>";
      continue;
    }
    if (/^##\s+/.test(line)){ closeList(); html += `<h2>${inline(line.replace(/^##\s+/, ""))}</h2>`; }
    else if (/^#\s+/.test(line)){ closeList(); html += `<h1>${inline(line.replace(/^#\s+/, ""))}</h1>`; }
    else if (/^-\s+/.test(line)){
      if (!inList){ html += "<ul>"; inList = true; }
      html += `<li>${inline(line.replace(/^-\s+/, ""))}</li>`;
    }
    else if (!line.trim()){ closeList(); }
    else { closeList(); html += `<p>${inline(line)}</p>`; }
    i++;
  }
  closeList();
  return `<div class="report">${html}</div>`;
}

function renderAnswer(d){
  if (d.kind === "value") return renderValue(d);
  if (d.kind === "table") return renderTable(d);
  if (d.kind === "report") return renderMd(d.text);
  return renderMd(d.text);
}

function citesHtml(cites){
  if (!cites || !cites.length) return `<span style="color:var(--muted);font-size:12px">无</span>`;
  return cites.map(c => {
    const cls = c.startsWith("metric:") ? "cite metric" : c.startsWith("policy:") ? "cite policy" : "cite table";
    return `<span class="${cls}">${esc(c)}</span>`;
  }).join("");
}

function answerCard(d){
  const id = "a" + Math.random().toString(36).slice(2, 9);
  const intentMap = {value:"取数", table:"表格", filter:"异常", attribution:"归因", policy:"制度", report:"报告"};
  return `
  <div class="msg">
    <div class="answer">
      <div class="ans-head">
        <span class="tag">${intentMap[d.intent] || d.intent}</span>
        <span style="font-size:12px;color:var(--muted)">${d.kind === "table" ? (d.count ?? 0) + " 行" : d.kind === "value" ? "单值" : "结构化输出"}</span>
        <span class="meta">${d.elapsed_ms} ms</span>
      </div>
      <div class="ans-body">${renderAnswer(d)}</div>
      <div class="tabs">
        <button class="tab" aria-selected="true" data-pane="${id}-cite">引用 ${d.cites.length}</button>
        <button class="tab" aria-selected="false" data-pane="${id}-sql">SQL</button>
      </div>
      <div class="pane on" id="${id}-cite"><div class="cites">${citesHtml(d.cites)}</div></div>
      <div class="pane" id="${id}-sql"><pre>${esc(d.sql || "（该类型不生成 SQL）")}</pre></div>
    </div>
  </div>`;
}

let busy = false;
async function ask(q){
  if (!q || busy) return;
  busy = true;
  $("#send").disabled = true;
  $("#hero").style.display = "none";
  const thread = $("#thread");
  thread.insertAdjacentHTML("beforeend", `<div class="msg user"><div class="bubble">${esc(q)}</div></div>`);
  const holder = document.createElement("div");
  holder.innerHTML = `<div class="msg"><div class="answer"><div class="ans-body"><span class="loading"></span>查询中…</div></div></div>`;
  thread.appendChild(holder);
  const scroll = $("#scroll");
  scroll.scrollTop = scroll.scrollHeight;

  try{
    const r = await fetch("/api/ask?q=" + encodeURIComponent(q));
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    holder.innerHTML = answerCard(d);
    holder.querySelectorAll(".tab").forEach(t => t.onclick = () => {
      holder.querySelectorAll(".tab").forEach(x => x.setAttribute("aria-selected", x === t));
      holder.querySelectorAll(".pane").forEach(p => p.classList.toggle("on", p.id === t.dataset.pane));
    });
  }catch(e){
    holder.innerHTML = `<div class="msg"><div class="answer"><div class="ans-body" style="color:var(--red)">出错了：${esc(e.message)}</div></div></div>`;
  }finally{
    busy = false;
    $("#send").disabled = false;
    scroll.scrollTop = scroll.scrollHeight;
  }
}

buildExamples();
$("#send").onclick = () => { const q = $("#q").value.trim(); $("#q").value = ""; ask(q); };
$("#q").addEventListener("keydown", e => { if (e.key === "Enter"){ const q = e.target.value.trim(); e.target.value = ""; ask(q); } });
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/ask":
            q = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not q:
                self._send(400, json.dumps({"error": "缺少问题参数 q"}, ensure_ascii=False))
                return
            try:
                body = payload(q)
            except Exception as e:                # noqa: BLE001
                body = {"error": f"{type(e).__name__}: {e}"}
            self._send(200, json.dumps(body, ensure_ascii=False))
            return
        self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))

    def log_message(self, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"BizLens demo: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
