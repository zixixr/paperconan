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

`paperconan` 是一个 **论文源数据 sanity check** 工具。你给它一个目录（`.xlsx` / `.csv` / `.tsv`，也可以混放补充材料 `.pdf` / `.docx` 里的结构化表格），它跑一组数值取证检测器，把"值得人工复核的位置"找出来。

**它输出的是 statistical signal，不是 misconduct verdict。** 最终判断仍要看原表、figure legend、Methods、作者回应和期刊/机构核实。

它最常见、也最被推荐的用法，是 **让 AI agent（Claude Code / Codex 等）搭配本仓库的 skill 来跑**：你用自然语言提需求，agent 调真实的 Python 检测器、解析结果、按规则解读，而不是肉眼猜数字。下面的文档就以这个场景为主线。纯 CLI / Python 库用法放在文末的[命令行参考](#命令行参考)。

**适合谁：**

- 研究生 / 青椒：引用论文前先 sanity check 一遍
- 实验室 / 课题组 / 院系：做公开 source data 初筛
- PubPeer 准备：先定位具体表格、行列和规则，再决定怎么提问
- 批量审计：扫一个期刊 / 一个作者组 / 一批 DOI，再用 agent 分级

**不做什么：**

- 不判断"是不是造假"，也不替代统计学审稿
- 不扫 Western blot、显微镜图、凝胶图或图像拼接
- 不从柱状图 / 折线图像素里数字化数据点
- 不绕付费墙，也不把"没找到公开数据"当成"论文干净"

---

## 快速开始（推荐：Agent + Skill）

### 1. 装好 CLI（skill 在背后调它）

需要 Python >= 3.10。

```bash
pip install "paperconan[all]"   # [all] 含 PDF / Word 表格抽取，建议直接装
paperconan --version            # 确认可用
```

> 只装基础版、加 `[fast]` 更快读 xlsx、本地开发等其它安装变体，见文末[命令行参考 › 安装](#安装)。

### 2. 把 skill 接到你的 agent 上

最简单的方式：用跨 agent 安装器 [`npx skills`](https://github.com/vercel-labs/skills)，一条命令搞定 Claude Code / Codex / Cursor 等：

```bash
npx skills add zixixr/paperconan                          # 自动装到检测到的 agent

# 也可以指定 agent 或安装范围：
npx skills add zixixr/paperconan -a claude-code -a codex  # 只装这两个
npx skills add zixixr/paperconan -g                       # 装到全局（用户级）
```

它会克隆 repo、发现其中的 `paperconan` skill，并按各 agent 自己的目录约定接好 —— 你不用关心 `~/.claude/skills`、Codex 等各家路径差异。

**手动方式（fallback）：** 不想用 npx、或想让 skill 跟着 `git pull` 一起更新，可以软链 Claude Code 的个人 skill 目录：

```bash
git clone https://github.com/zixixr/paperconan.git
mkdir -p ~/.claude/skills
ln -s "$(pwd)/paperconan/skills/paperconan" ~/.claude/skills/paperconan
```

如果你的 agent 还不支持 skill 目录发现，可以退而求其次在项目指令里引用 `SKILL.md`：

```bash
echo '@'"$(pwd)"'/paperconan/skills/paperconan/SKILL.md' >> AGENTS.md
```

安装或更新 skill 后，重启 agent 会话，让它重新发现 skill。

[`skills/paperconan/SKILL.md`](skills/paperconan/SKILL.md) 是 agent 的入口，它强制 agent 跑真实检测器、按 `references/` 里的规则解读，并守住 **signal-not-verdict** 红线。

### 3. 直接用自然语言提需求

接好后你不用记任何命令，直接说话即可，例如：

- "帮我查一下这篇论文的源数据有没有问题：`10.1038/sxxxxx`"
- "扫一下 `~/Downloads/source_data/` 这个目录，挑出最该人工复核的几条"
- "这条 cross-sheet 命中是不是误报？帮我对照原表看一下"

agent 会自己判断该 `fetch` 还是直接 `scan`、解析 `scan.json`、加载对应 reference、必要时打开原表，再给你一份带证据、带良性解释、带"还需要什么人工背景"的回答。

> 没有可用 Python 环境的 agent，应当请你本地跑命令，**绝不能把肉眼猜测冒充成 paperconan 输出**。

---

## 它能找出什么

| 检测器 | 寻找的模式 | 典型证据形态 |
|--------|-----------|------------|
| `identical_column` / `constant_offset` / `constant_ratio` / `exact_linear` | 同一 block 内两列存在精确数值关系 | `col B = col A + 2.13` 出现在所有 10 行 |
| `sum_constant` / complementary relations | 两列或两类比例严格相加成常数 | 两个百分比列逐行加和为 100 |
| `small_diff_set` | 两列差值只取少数离散值 | col_b − col_a 只在 2–6 个值里跳 |
| `arithmetic_progression` | 整列等差 / 等比 | 一列完美 0, 3, 6, 9... |
| `within_col_value_duplication` | 单列里同一个高精度值反复出现 | `0.208975` 在独立样本里出现 8 次 |
| `within_col_decimal_repetition` | 同一列末两位高度重复 | 大量值都以 `.37` 结尾 |
| `rounded_to_half_or_int` | 整列被舍入到固定刻度 | 全部落在整数、0.5 或 0.25 网格 |
| `identical_after_rounding` | 多个 cell 舍到 1 位小数后相同但精确值不同 | 先写概数再反向补精度 |
| `missing_last_digits` | 某些末位数字从不出现 | 编造者偏好"漂亮"尾数 |
| `many_equal_pairs` | 两个本该独立的列里大量 byte-identical | 9/10 一致，只手改一格 |
| `row_pair_digit_coupling` | 两行之间高位改变但小数/个位异常保留 | `197.2 → 167.2`、`165.5 → 155.5` 成串出现 |
| `cross_sheet_position_identical` | 两张 sheet 同位置数值完全一样 | 同一份样本被复制到另一张表 |
| `cross_sheet_value_overlap` | 两张表共享大量小数值（不要求同位置） | 池化后重新洗牌伪造独立实验 |
| `cross_sheet_decimal_tail_reuse` | 跨 sheet 多个值保留长小数尾、只改前导数字 | `14.70300997 → 6.70300997` 成串出现 |
| `grim_inconsistent` / `grimmer_inconsistent` | 报告的均值 / SD 对整数数据不可能 | 计数均值或 SD 与 n 不自洽 |
| `last_digit_chi_square` | 末位数字偏离均匀分布（χ² 检验） | 整张 sheet 的末位数字集中 |
| `repeated_two_decimal_endings` | 末两位高度集中 | 批量编造数字的尾数指纹 |

每条 finding 都带 `severity`、文件、sheet、block 行列范围、规则字符串和 evidence。**跨表类（`cross_sheet_*`）优先级最高** —— 既能抓同一文件内两张 sheet，也能抓两个独立文件之间的数据复用。每个检测器的原理、典型命中、常见误报详见 [`detectors.md`](skills/paperconan/references/detectors.md)。

---

## 批量扫描推荐工作流

下面这套是我们扫 Nature 系列 / 多期刊回填时沉淀出来的流水线。核心思路是 **确定性的归确定性、判断的归 agent**：paperconan 负责拉取/初筛/过滤这些可重复、可审计的步骤；AI agent 只在已经收窄的候选集上做语义判定，并且每一步都要能被反向质疑。

```
① 拉取        ② 初筛        ③ 过滤            ④ 立卷           ⑤ 子 agent 判定      ⑥ 分级           ⑦ 对抗
fetch    →    scan    →    profile/     →   保存问题文档   →   subagent 写        →   tier-1/2/3   →   红队反向
(DOI)        (检测器)      prefilter        全量资料+发现       详细审核报告           needs-human       refute 验证
                                                                                    drop
─────── paperconan(确定性) ───────         ──────────────── AI agent 编排 ────────────────
```

### ① 拉取 — `paperconan fetch`

对一批 DOI / 标题，用 `fetch` 找开放源（Zenodo / Figshare / Europe PMC / Dryad / nature.com ESM）并下载到各自目录。`--auto` 只在 DOI 命中或标题高度一致时才下载，弱匹配会被标出来拒绝自动下载 —— 这一步要诚实对待"没找到数据 ≠ 论文干净"。

```bash
paperconan fetch "<DOI>" --auto --out runs/<paper-id>/data/
```

### ② 初筛 — `paperconan <dir>`

逐篇跑检测器，产出 `scan.json`。批量时建议 `evidence=False`（库调用）或只读 metadata，先拿到"哪些篇有信号"。

### ③ 过滤 — profile / prefilter

`--profile triage` 直接隐藏疑似误报，拿到最短候选清单；拿不准时对单篇重跑 `--profile forensic` 看原始 severity。这一步把"结构性误报海洋"砍掉，留下值得人工/agent 看的少数篇。**到这里为止都是 paperconan 自己的确定性输出，可复现、可 diff。**

### ④ 立卷 — 保存问题文档全量资料

对每一篇过滤后仍有信号的论文，整理成一个**独立卷宗目录**，把判定要用的东西一次性备齐：

- 原始表（`.xlsx` / `.csv` / 抽出来的 PDF/Word 表）
- 该篇所有 finding，按检测器和位置**列清楚**：kind、文件/sheet、行列范围、`rule`、`n`、`col_a_sample` / `col_b_sample` 或 value sample
- `report.html` 链接，方便人随时回看高亮

立卷的意义：判定子 agent 只看卷宗就能工作，不必反复回到大目录，也便于事后审计"当时基于什么材料下的结论"。

### ⑤ 子 agent 判定 — 每篇一份详细审核报告

主 agent 把卷宗分发给子 agent（一篇/一组 finding 一个），每个子 agent **写一份详细的审核报告**，而不是只丢一个标签。报告里要回答：

- 这条信号在原表里到底长什么样（引用具体 cell）
- 最可能的良性解释（shared control、单位换算、公式派生、归一化、固定分母、边界值洪泛……）有没有被排除
- 还缺哪些人工背景（行是否独立样本、是不是原始测量、Methods/legend 怎么说）

判定纪律见 [`judgment-rubric.md`](skills/paperconan/references/judgment-rubric.md) 和 [`interpretation.md`](skills/paperconan/references/interpretation.md)：`within_col_*` 默认按高误报处理；优先看跨表/跨列；拿不准一律 `needs human context`，**宁可保守也不要把 severity 当成不端结论**。

### ⑥ 分级 — tier-1 → tier-3 / needs-human / drop

子 agent 的报告汇总后定级：

- **tier-1（铁案 / airtight）**：证据强度、影响、可复核性都过硬，原表里能直接指认的 copy-then-tweak、严格线性重构、跨独立表复用等
- **tier-2 / tier-3**：信号真实但需要更多上下文，或单条不足以独立成案
- **needs-human**：必须有领域专家或原始数据才能判
- **drop**：经判定为良性 / 误报（要写明良性理由，便于回归）

### ⑦ 对抗 — 红队反向判定

对每一条进入 tier 的结论，再派**独立的红队 agent 专门去 refute**：默认假设"它其实是误报"，去找能解释掉信号的良性机制。只有扛得住反向质疑的才保留等级，被驳倒的降级或 drop。这一步是把"看起来对、其实站不住"的结论挡在外面的关键闸门 —— 我们的经验是单向判定很容易出现 default-FP 或 default-KEEP 偏置，对抗一遍才稳。

> **为什么要这么分工**：拉取/初筛/过滤交给确定性工具，保证可复现、可回归；判定/分级/对抗交给 agent，处理工具处理不了的语义和上下文。两边都留痕，整条链路可被第三方复核 —— 这正是 paperconan 的 signal-not-verdict 立场在批量场景下的落地方式。

---

## 误报控制：profiles 和 prefilter

检测器先产出原始 signal，`--profile` 再决定怎么处理常见误报。默认 `review`。

| profile | 行为 | 什么时候用 |
|---------|------|------------|
| `review` | 降级疑似误报，但保留可见 | 日常审计默认 |
| `forensic` | 不做降级，保留原始 severity | 怀疑默认过滤太保守、要复核原始信号 |
| `triage` | 同 `review` 判断，但把疑似误报隐藏 | 批量初筛、只想要最短清单 |

降级 / 隐藏的 finding 会带 `profile_action`（`kept` / `demoted` / `hidden`）、`false_positive_context`（如 `axis_or_scan_column`、`derived_or_unit_conversion`、`same_data_replot_or_duplicate_upload`、`omics_or_large_matrix_boundary_flood`）、`prefilter_reason` 和 `prefilter_flags`。

**重要：`review` 下的 low severity 可能是过滤器的意见，不是检测器原始判断。** 拿不准时重跑 `--profile forensic`。标签到检测器的反查表见 [`detectors.md`](skills/paperconan/references/detectors.md)。

---

## 报告怎么读

`report.html` 是首选入口（顶部摘要 + 左侧 severity/detector/文件/关键词过滤 + finding 卡片 + last-digit histogram + cross-sheet 专段）。建议顺序：

1. 先看 `scan_errors` —— 解析失败或超大文件被跳过时，不能解读成"没问题"。
2. 先看跨 sheet / 跨文件重复，再看列关系，最后才看 within-column。
3. 对降级为 low 的 finding，核 `likely_benign` / `false_positive_context` / `prefilter_reason` 是否成立。
4. 打开原始表，按 evidence 的文件、sheet、行列复核。
5. 再读 figure legend 和 Methods，确认 shared control / 重复展示 / 单位换算 / 派生指标。

`scan.json` 完整结构见 [`output-schema.md`](skills/paperconan/references/output-schema.md)。

---

## ⚠️ 重要声明

`paperconan` 输出的是 **算法标注的可疑模式**，不是学术不端结论。最终判定需由原作者澄清、期刊编辑部核实，或经独立同行复议。

**请走正规渠道：** 把可疑 signal 提交 PubPeer / 联系期刊 ethics inquiry / 涉及本单位走 research integrity office。

**请不要：** 在社交媒体直接指控具体作者 / 把 paperconan 截图当"实锤" / 跳过作者澄清直接定性。

工具是中立的，使用方式不能。

---

## FAQ

**Q: 它会漏掉哪些造假？** 会。它只看表格形式的数值。图像取证、图表像素数字化、未公开的原始数据、实验完全没做、p-hacking、引用造假、同行评议造假都不覆盖。

**Q: 它会误报吗？** 会。时间轴、剂量轴、单位换算、百分比互补、shared cohort、相关矩阵、omics 边界值、p 值校正列都可能触发原始检测器。默认 `review` 会尽量识别并降级，但规则也会误判 —— high severity 仍必须人工读原文原表。

**Q: 发现一篇看似有问题的论文，下一步？** 打开原表核对高亮位置 → 读 legend/Methods 看有没有 shared control / 重复展示 / 派生列 → 仍值得问就整理成具体、克制的问题发 PubPeer → 等作者回应，必要时联系期刊或机构。

**Q: 会不会让普通硕博更难毕业？** 不会。它抓的是高精度值复用、跨独立表 copy-then-tweak、严格线性重构、异常尾数集中这类模式；正常实验的 messy data、negative result、记录不齐不是它的目标。

---

## 命令行参考

> agent 用户通常不需要这一节 —— skill 会自动调这些命令。下面是给纯 CLI / 脚本 / 库集成用户的参考。

### 安装

```bash
pip install paperconan              # 基础
pip install "paperconan[all]"       # + PDF / Word 表格抽取
pip install "paperconan[fast]"      # + python-calamine（更快的 xlsx 读取，缺失自动回退 openpyxl）
pip install -e ".[dev,all,fast]"    # 本地开发
```

### 扫描

```bash
paperconan path/to/source_data_dir/                 # 默认输出 <dir>/audit/{scan.json,report.html}
paperconan path/to/dir/ --out /tmp/audit-this-paper
paperconan path/to/dir/ --md                        # 额外生成 REPORT.md
paperconan path/to/dir/ --no-html
paperconan path/to/dir/ --profile forensic
paperconan path/to/dir/ --doi "10.xxxx/..." --title "Paper title"
python -m paperconan path/to/dir/                   # 等价 module 形式
```

### 拉取开放源数据

```bash
paperconan fetch "10.xxxx/your.doi"
paperconan fetch "10.xxxx/your.doi" --json
paperconan fetch "10.xxxx/your.doi" --download zenodo:123456 --out data/
paperconan fetch "10.xxxx/your.doi" --auto --out data/
paperconan data/
```

覆盖 Zenodo / Figshare（keyless 检索下载）、Europe PMC / NCBI PMC OA（自动抽 supplementary 里的表）、nature.com ESM、Dryad。`--auto` 仅在 DOI 命中或标题高度一致时下载，弱匹配会被拒绝（需 `--download ... --force` 显式确认）。`fetch --download` / `--auto` 会写 `paperconan_source.json`，随后扫描会把 DOI/标题/来源写进 `scan.json.paper` 做溯源。

### PDF / Word 补充材料表格

装 `paperconan[all]` 后目录里的 `.pdf` / `.docx` 也会被扫描。PDF 表 sheet 名形如 `<文件名>!p<页>_t<表号>`，Word 表形如 `<文件名>!t<表号>`，与 xlsx/csv 走同一套检测器。**不做 OCR，不从图表像素读数。**

### 作为 Python 库

```python
from paperconan import audit_dir

scan = audit_dir(
    "path/to/source_data_dir",
    "/tmp/audit-this-paper",
    write_html=False,   # 不生成 HTML
    write_json=False,   # 只拿返回 dict，不落盘
    evidence=False,     # 跳过 evidence blob，适合批处理只要 metadata
    # profile="forensic",
)
```

`write_html=True` 需要 evidence，会强制打开。CLI 入口是 `paperconan._audit:main`，库入口推荐 `paperconan.audit_dir()`。

### 内存 / 输出保护

当前引擎用 columnar `Sheet` 底座（数值 dense array、文本稀疏）。为避免大文件撑爆机器或 `scan.json`，超限对象会被**记录并跳过**（不当作"干净"）：

| 环境变量 | 默认值 | 作用 |
|----------|--------|------|
| `PAPERCONAN_MAX_FILE_MB` | `200` | 单文件读取前体积上限 |
| `PAPERCONAN_MAX_CELLS` | `10000000` | 单 sheet / workbook 累计 cell 预算 |
| `PAPERCONAN_MAX_BLOCK_COLS` | `120` | 宽 block 跳过 O(col²) 关系 / equal-pair 检测 |
| `PAPERCONAN_MAX_REPORT_BLOCKS` | `2000` | 最多收集多少个带 finding 的 block |
| `PAPERCONAN_MAX_EVIDENCE_ROWS` | `50` | 单条 evidence 片段最多行数 |
| `PAPERCONAN_MAX_EVIDENCE_COLS` | `30` | 单条 evidence 片段最多列数 |
| `PAPERCONAN_MAX_PAPER_MB` | `1500` | `fetch` 下载/解压到一个 paper 目录的总量上限 |

---

## 示例

[`examples/`](examples/) 有完整合成 demo：两份伪造 source data、已生成的 `audit/scan.json` + `report.html`、截图和逐条解读。先看 [examples/README.md](examples/README.md) 和 [examples/report-preview.png](examples/report-preview.png)，或自己跑：

```bash
cd examples
paperconan demo_paper
open demo_paper/audit/report.html
```

---

## 路线图

已完成：`.xlsx` / `.csv` / `.tsv` 输入 · HTML 报告与 evidence 高亮 · PDF / Word 表格输入 · `paperconan fetch` 开放源检索下载 · Agent skill bundle · Columnar engine + fast xlsx 可选路径 + 内存/输出保护 · `review` / `forensic` / `triage` profiles 与确定性 prefilter。

未完成：跨论文扫描（一个 lab / 作者组多篇一起看复用）· 图表像素数字化 · 图像取证（Western blot / 显微镜重复拼接）· 与 PubPeer Public API 联动。

欢迎 PR —— 加检测器模式、补文档、做 demo 都欢迎。

---

## 诞生背景

这个工具最早是为做一期 YouTube / 抖音 / B 站视频造的：用公开 source data 扫 Nature 及子刊论文，定位可疑数值模式。开源给所有人，希望它能帮认真做实验的人减少被编造数据挤占空间的概率。

## License

MIT.

## Acknowledgments

- 名侦探柯南 / Detective Conan © 青山刚昌 / TMS Entertainment。借了一下片头叙事结构。
- PubPeer。paperconan 的输出最终应该服务于具体、克制、可复核的公开质疑。
