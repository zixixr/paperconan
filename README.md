# 论文柯南 / paperconan

> **真相只有一个！**
>
> 现在学术界弊病丛生，
> 大家要小心 paperconan 的推理哦！
> 唯一看透论文数据真相的，
> 是这个外表看似 Python 小工具、
> 智慧却过于常人的——
>
> 名侦探，**论文柯南**！

---

## 它是什么

`paperconan` 是一个 **论文源数据 sanity check** 小工具。你给它一个目录，里面可以是 `.xlsx` / `.csv` / `.tsv`，也可以混放补充材料 `.pdf` / `.docx` 里的结构化表格；它会跑一组数值取证检测器，输出：

- `scan.json`：完整结构化结果，适合脚本或 agent 读取
- `report.html`：自包含 HTML 报告，每条 finding 都带可疑表格片段和高亮
- 可选 `REPORT.md`：给纯文本工作流用的摘要

它的用途是把“值得人工复核的位置”找出来。**它输出的是 statistical signal，不是 misconduct verdict**。最终判断仍然要看原表、figure legend、Methods、作者回应和期刊/机构核实。

**适合谁：**

- 研究生 / 青椒：引用论文前先 sanity check 一遍
- 实验室 / 课题组 / 院系：做公开 source data 初筛
- PubPeer 准备：先定位具体表格、行列和规则，再决定怎么提问
- agent 工作流：让 Claude Code / Codex 等工具跑真实检测器，而不是肉眼猜

**不适合什么：**

- 不判断“是不是造假”，也不替代统计学审稿
- 不扫 Western blot、显微镜图、凝胶图或图像拼接
- 不从柱状图 / 折线图像素里数字化数据点
- 不绕付费墙，也不把“没找到公开数据”当成“论文干净”

---

## 安装 & 运行

需要 Python >= 3.10。

```bash
# 普通用户：从 PyPI 安装
pip install paperconan

# 需要审 PDF / Word 补充材料里的表格
pip install "paperconan[all]"

# 可选：更快的 xlsx 读取器；没有它会自动回退 openpyxl
pip install "paperconan[fast]"

# 本地开发
git clone https://github.com/zixixr/paperconan.git
cd paperconan
pip install -e ".[dev,all,fast]"
```

跑一篇论文：

```bash
paperconan path/to/source_data_dir/

# 等价 module 形式
python -m paperconan path/to/source_data_dir/
```

默认输出在 `<input-dir>/audit/`：

```text
scan.json
report.html
```

常用参数：

```bash
paperconan path/to/source_data_dir/ --out /tmp/audit-this-paper
paperconan path/to/source_data_dir/ --md
paperconan path/to/source_data_dir/ --no-html
paperconan path/to/source_data_dir/ --profile forensic
paperconan path/to/source_data_dir/ --doi "10.xxxx/..." --title "Paper title"
paperconan --version
```

---

## 它能找出什么

| 检测器 | 寻找的模式 | 典型证据形态 |
|--------|-----------|------------|
| `identical_column` / `constant_offset` / `constant_ratio` / `exact_linear` | 同一 block 内两列存在精确数值关系 | `col B = col A + 2.13` 出现在所有 10 行 |
| `sum_constant` / complementary relations | 两列或两类比例严格相加成常数 | 两个百分比列逐行加和为 100 |
| `arithmetic_progression` | 整列等差 / 等比 | 一列完美 0, 3, 6, 9... |
| `within_col_value_duplication` | 单列里同一个高精度值反复出现 | `0.208975` 在独立样本里出现 8 次 |
| `within_col_decimal_repetition` | 同一列末两位高度重复 | 大量值都以 `.37` 结尾 |
| `rounded_to_half_or_int` | 整列被舍入到固定刻度 | 全部落在整数、0.5 或 0.25 网格 |
| `identical_after_rounding` | 两列舍掉末位后完全相同 | 一列像另一列乘小扰动后重排 |
| `many_equal_pairs` | 两个本该独立的列里大量 byte-identical | 9/10 一致，只手改一格 |
| `cross_sheet_position_identical` | 两张 sheet 同行同列位置数值完全一样 | 同一份样本被复制到另一张表 |
| `grim_inconsistent` / `grimmer_inconsistent` | 报告的均值 / SD 对整数数据不可能 | 计数均值或 SD 与 n 不自洽 |
| `last_digit_chi_square` | 末位数字偏离均匀分布，且 BH-FDR q <= 0.05 | 整张 sheet 的末位数字集中 |
| `repeated_two_decimal_endings` | 末两位高度集中 | 编造数字常见的尾数模式 |

每条 finding 都带 `severity`、文件、sheet、block 行列范围、规则字符串和 evidence。列关系 finding 还会给出 `col_a_sample` / `col_b_sample`，让报告和 agent 输出能先看一眼两列代表值，再决定是否打开原表。

---

## 误报控制：profiles 和 prefilter

检测器先产出原始 signal，随后 `--profile` 决定怎么处理常见误报。默认是 `review`。

| profile | 行为 | 什么时候用 |
|---------|------|------------|
| `review` | 降级疑似误报，但保留可见 | 日常审计默认 |
| `forensic` | 不做降级，保留原始 severity | 怀疑默认过滤太保守、需要复核原始信号 |
| `triage` | 和 `review` 同样判断，但把疑似误报隐藏 | 只想快速拿最短清单 |

降级或隐藏的 finding 会带这些字段：

- `profile_action`: `kept` / `demoted` / `hidden`
- `false_positive_context`: 为什么像误报，例如 `axis_or_scan_column`、`derived_or_unit_conversion`、`same_data_replot_or_duplicate_upload`、`omics_or_large_matrix_boundary_flood`
- `prefilter_reason`: 更具体的确定性规则，例如 `complement_percentage_sum_to_100`、`explicit_formula_or_unit_conversion`、`genomic_coordinate_table`、`count_to_probability_or_rate`、`baseline_correction_derived`、`search_engine_export_duplicate`
- `prefilter_flags`: 规则命中的机器可读细节，方便之后审计或调试

这套过滤现在覆盖两大类：

- **关系类 prefilter**：识别单位换算、百分比互补、坐标表、ID/时间戳求和、派生统计列、搜索引擎导出重复、baseline/blank correction、ImageJ 派生列、qPCR 公式列等，避免把“表格里本来就该严格相关”的列当成高危复制。
- **within-column prefilter**：识别 omics/大矩阵里的边界值洪泛、p 值/校正 p 值的 0/1 重复、低基数类别列、整张表单列 high finding 洪泛等，把结构性格式痕迹降级或隐藏。

重要规则：**`review` 下的 low severity 可能是过滤器的意见，不是检测器原始判断**。拿不准时重跑：

```bash
paperconan path/to/source_data_dir/ --profile forensic
```

---

## 报告怎么读

`report.html` 是首选入口：

- 顶部摘要：文件数、sheet 数、high / medium / low 计数
- 左侧过滤：按 severity、detector、文件、关键词筛选
- finding 卡片：规则、良性解释、表格 evidence、高亮列/行
- last-digit 异常：显示 BH-FDR q 值和 0-9 inline histogram
- cross-sheet collisions：单独成段，优先看 `value_tweaked`、跨图 / 跨文件重复

读报告时建议顺序：

1. 先看 `scan_errors`。解析失败或超大文件跳过时，不能把结果解读成“没问题”。
2. 先看跨 sheet / 跨文件重复，再看列关系和 within-column signal。
3. 对降级为 low 的 finding，看 `likely_benign`、`false_positive_context` 和 `prefilter_reason` 是否合理。
4. 打开原始 `.xlsx` / `.csv` / `.pdf` / `.docx`，按 evidence 的文件、sheet、行列复核。
5. 再读 figure legend 和 Methods，确认是否有 shared control、重复展示、单位换算或派生指标说明。

---

## 性能和内存保护

当前引擎用 columnar `Sheet` 底座：数值存在 dense array，文本稀疏保存，evidence 保留 int/float 形态。这比早期 list-of-lists 更省内存，也让 xlsx/csv/pdf/docx 走同一套检测路径。

可选加速：

```bash
pip install "paperconan[fast]"
```

装了 `python-calamine` 后会优先用 Rust xlsx reader；没有安装时自动回退 `openpyxl`，结果应保持一致。

为避免大文件把机器或 `scan.json` 撑爆，paperconan 会记录并跳过超限对象，而不是把它们当作“干净”：

| 环境变量 | 默认值 | 作用 |
|----------|--------|------|
| `PAPERCONAN_MAX_FILE_MB` | `200` | 单文件读取前体积上限 |
| `PAPERCONAN_MAX_CELLS` | `10000000` | 单 sheet / workbook 累计 cell 预算 |
| `PAPERCONAN_MAX_BLOCK_COLS` | `120` | 宽 block 跳过 O(col^2) 的关系 / equal-pair 检测 |
| `PAPERCONAN_MAX_REPORT_BLOCKS` | `2000` | 最多收集多少个带 finding 的 block |
| `PAPERCONAN_MAX_EVIDENCE_ROWS` | `50` | 单条 evidence 片段最多行数 |
| `PAPERCONAN_MAX_EVIDENCE_COLS` | `30` | 单条 evidence 片段最多列数 |
| `PAPERCONAN_MAX_PAPER_MB` | `1500` | `fetch` 下载/解压到一个 paper 目录的总量上限 |

---

## 自动找公开源数据

只有 DOI 或题名、还没有本地数据时，可以先让 `fetch` 找开放源：

```bash
paperconan fetch "10.xxxx/your.doi"
paperconan fetch "10.xxxx/your.doi" --json
paperconan fetch "10.xxxx/your.doi" --download zenodo:123456 --out data/
paperconan fetch "10.xxxx/your.doi" --auto --out data/
paperconan data/
```

覆盖范围：

- Zenodo / Figshare：keyless 检索和下载
- Europe PMC / NCBI PMC OA：开放获取论文的 supplementary package，自动抽取其中 `.xlsx` / `.csv` / `.tsv`
- nature.com ESM：对 DOI 对应页面的电子补充材料做解析
- Dryad：检索和版本链解析；需要鉴权的下载会明确提示

`--auto` 只有在 DOI 命中或标题高度一致时才会下载。候选看起来不属于这篇论文时，工具会标出来并拒绝自动下载；你如果确认要下，需要显式 `--download ... --force`。

`fetch --download` / `--auto` 会写 `paperconan_source.json`，随后 `paperconan <dir>` 会把 DOI、标题和来源写进 `scan.json.paper` 做溯源。

---

## PDF / Word 补充材料表格

安装 `paperconan[all]` 后，目录里的 `.pdf` / `.docx` 也会被扫描：

```bash
pip install "paperconan[all]"
paperconan path/to/dir_with_si_pdf_or_docx/
```

- PDF 表格 sheet 名类似 `<文件名>!p<页>_t<第几张表>`
- Word 表格 sheet 名类似 `<文件名>!t<第几张表>`
- 抽出来的表和 xlsx/csv/tsv 走同一套检测器
- 不做 OCR，不从图表像素里读数

---

## 作为 Python 库使用

```python
from paperconan import audit_dir

scan = audit_dir(
    "path/to/source_data_dir",
    "/tmp/audit-this-paper",
    write_html=False,
    write_json=False,
    evidence=False,
)
```

几个常见用法：

- `write_json=False`：只拿返回 dict，不落盘 `scan.json`
- `evidence=False`：跳过 evidence blob，适合下游只要 finding metadata 的批处理
- `write_html=False`：不生成 HTML；如果 `write_html=True`，HTML 需要 evidence，所以会强制打开 evidence
- `profile="forensic"`：拿原始 severity

CLI 的 public entry point 是 `paperconan._audit:main`；库入口推荐用 `paperconan.audit_dir()`。

---

## 作为 agent skill 使用

[`skills/paperconan/SKILL.md`](skills/paperconan/SKILL.md) 是给 Claude Code / Codex / 其他 agent 看的入口。它要求 agent 跑真实 Python 检测器，不能把肉眼猜测冒充成 paperconan 输出。

同目录 `references/` 里有：

- [`detectors.md`](skills/paperconan/references/detectors.md)：每个检测器的原理、典型命中、常见误报和 profile 降级映射
- [`interpretation.md`](skills/paperconan/references/interpretation.md)：severity 语义、signal-not-verdict 红线、推荐回复话术
- [`output-schema.md`](skills/paperconan/references/output-schema.md)：`scan.json` 完整结构

安装方式：

```bash
# 1. 确保 CLI 可用
pip install paperconan

# 2. Claude Code: 软链整个 skill 目录
ln -s /path/to/paperconan/skills/paperconan ~/.claude/skills/paperconan

# 3. Codex / 其他 agent: 在项目指令里引用
echo '@/path/to/paperconan/skills/paperconan/SKILL.md' >> AGENTS.md
```

---

## 示例

[`examples/`](examples/) 里有一份完整的合成 demo：两份伪造 source data、已生成的 `audit/scan.json` + `audit/report.html`、报告截图和逐条解读。可以先看 [examples/README.md](examples/README.md) 与 [examples/report-preview.png](examples/report-preview.png)，也可以自己跑：

```bash
cd examples
paperconan demo_paper
open demo_paper/audit/report.html
```

---

## ⚠️ 重要声明

`paperconan` 输出的是 **算法标注的可疑模式**，不是学术不端结论。

最终判定需由原作者澄清、期刊编辑部核实，或经独立同行复议。

**请走正规渠道：**

- 把可疑 signal 提交到 PubPeer
- 联系期刊编辑部的 ethics inquiry 渠道
- 如果涉及你所在单位，走 research integrity office

**请不要：**

- 在微博 / 微信 / 知乎 / 抖音直接指控具体作者
- 把 paperconan 截图当作“实锤”
- 跳过原作者澄清环节直接定性

工具是中立的，使用方式不能。

---

## FAQ

**Q: 它会漏掉哪些造假？**

会。它只看以表格形式出现的数值。图像取证、图表像素数字化、未公开临床原始数据、实验完全没做但写进文章、p-hacking、引用造假和同行评议造假都不在覆盖范围内。

**Q: 它会误报吗？**

会。时间轴、剂量轴、单位换算、百分比互补、同图 shared cohort、相关矩阵、omics 大表边界值、p 值校正列都可能触发原始检测器。默认 `review` profile 会尽量识别这些结构性模式并降级，但规则也可能误判。报告里的 high severity anomaly 仍然必须人工读原文和原表。

**Q: 我发现一篇看似有问题的论文，下一步做什么？**

1. 打开原始表，核对 paperconan 高亮的位置。
2. 读 figure legend 和 Methods，看是否有 shared control、重复展示或派生列说明。
3. 如果仍然觉得值得问，整理成具体、克制的问题发 PubPeer。
4. 等原作者回应；必要时再联系期刊或机构。

**Q: 这个工具会不会让普通硕博更难毕业？**

不会。它主要抓的是高精度值复用、跨独立表格 copy-then-tweak、严格线性重构、异常尾数集中这类模式。正常实验的 messy data、negative result、记录不齐，不是它的目标。

---

## 同款诞生背景

这个工具最早是为做一期 YouTube / 抖音 / B 站视频造的：用公开 source data 扫 Nature 及 Nature 子刊论文，定位可疑数值模式。工具开源给所有人，希望它能帮认真做实验的人减少被编造数据挤占空间的概率。

---

## 路线图

已完成：

- [x] `.xlsx` / `.csv` / `.tsv` 输入
- [x] HTML 报告和 evidence 表格高亮
- [x] PDF / Word 补充材料表格输入
- [x] `paperconan fetch` 开放数据源检索与下载
- [x] Agent skill bundle
- [x] Columnar engine、fast xlsx 可选路径、内存 / evidence 输出保护
- [x] `review` / `forensic` / `triage` profiles 与确定性 prefilter

未完成：

- [ ] 跨论文扫描：一个 lab / 作者组多篇论文一起跑，看跨论文数据复用
- [ ] 图表像素数字化：从 bar chart / 曲线中提取数据点，需谨慎控制误差和假阳性
- [ ] 图像取证检测：Western blot / 显微镜照片重复、拼接、增强痕迹
- [ ] 与 PubPeer Public API 联动

欢迎 PR。给检测器加新模式、补文档、做 demo 都很欢迎。

---

## License

MIT.

## Acknowledgments

- 名侦探柯南 / Detective Conan © 青山刚昌 / TMS Entertainment。借了一下片头叙事结构。
- PubPeer。paperconan 的输出最终应该服务于具体、克制、可复核的公开质疑。
