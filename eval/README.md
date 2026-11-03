# 评测资产说明

> 这里放"怎么判断它做得好不好"的资产，不是代码。
> 当前：**60 条主任务集 + 10 条改写集 + 8 条封存集**；六类能力已全部实现。

## 文件

| 文件 | 内容 |
|---|---|
| `tasks_v1.yaml` | 60 条任务 + ground truth，6 类 × 10 条，含 `split: dev/holdout` |
| `paraphrase_v1.yaml` | 10 条改写题，`base` 复用原题答案，只改问法 |
| `sealed_v2.yaml` | 8 条封存题，**尚未运行**，由使用者手动跑 |
| `report.md` | 主任务集评测结果（自动生成） |
| `report_paraphrase.md` | 改写测试结果（自动生成） |
| `tasks_v0.yaml` | W1 的 30 条初稿，保留作历史 |
| `../data/metrics.yaml` | 9 个指标口径（唯一事实来源） |
| `../data/policy.md` | 10 条制度条款，供 T5 引用 |
| `../data/seed/*.csv` | 合成数据，ground truth 由它算出 |

## 怎么复现

```bash
python scripts/gen_seed.py      # 生成合成数据（固定种子 20251103）
python scripts/build_tasks.py   # 计算并生成 60 条
python scripts/check_tasks.py   # 结构 / 引用 / 覆盖度自检
python src/eval_runner.py       # 主任务集 → eval/report.md
python src/eval_runner.py eval/paraphrase_v1.yaml --split paraphrase \
       --tag 改写测试 --out eval/report_paraphrase.md
```

零第三方依赖（除 pandas），不需要 API Key、不需要联网。

## 三类集子的分工

| 集子 | 干什么 | 什么时候看 |
|---|---|---|
| tasks_v1（dev/holdout） | 证明取数正确 | 每次改动 |
| paraphrase_v1 | 证明"换个说法还行不行" | 改路由/接 LLM 时 |
| sealed_v2 | 最终验收 | **只在发布前跑一次** |

### 封存集协议

1. 平时不跑、不看、不调；
2. 发布前跑一次，结果如实记录；
3. 跑了发现问题，**先记录，不立刻改**；改完要说明"这一版已经不算干净"。

命令：

```bash
python src/eval_runner.py eval/sealed_v2.yaml --split sealed --out eval/report_sealed.md
```

## 当前结果（截至 W3）

| 集子 | 通过 | 说明 |
|---|---|---|
| tasks_v1（60 条，6 类） | 60/60 | 规则覆盖模板问法，**不代表泛化** |
| paraphrase（10 条） | 3/10 | 规则路由在自由问法上明显不够 |
| sealed（8 条） | 未运行 | 等你跑 |

## 已知缺口

- 60 条全部出自一人，**没有双人标注**
- 六类都有引擎，但 T4 只看结论方向、T6 只看章节与数字，质量仍需人工评
- 规则路由器只认模板化问法（这正是改写集只有 3/10 的原因）
- LLM 解析器有代码但**未联调**
- 数据只有 30 个项目，够验证链路，不够证明泛化
