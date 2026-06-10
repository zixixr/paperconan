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

`paperconan` 是一个 **论文数据 sanity check** 小工具。

喂一份 supplementary source data（一个目录，里面装着 `.xlsx` / `.csv` / `.tsv`，**也可以是补充材料 `.pdf` / `.docx` 里的表格**）进去，它会跑十几组数值取证检测器，输出 **scan.json**（结构化的全部命中）+ **report.html**（自包含的可交互法证报告，每条 finding 直接嵌可疑表格片段并高亮可疑列/行），告诉你哪些位置值得人工再看一眼。

**适合谁**：

- 研究生 / 青椒：引用论文前 sanity check 一遍
- 实验室 / 课题组 / 院系：响应高校自查要求时的初筛工具
- 普通好奇心人士：跟着学术圈热点看看自己关心的论文

**不适合什么**：

- 不能给出 "是不是造假" 的结论 — 那是期刊编辑部和同行的事
- 不能扫图像 / 凝胶电泳 / Western blot 拼接 — 只看数值表格
- 不能替代专业的统计学审稿

---

## 它能找出什么

| 检测器 | 寻找的模式 | 典型证据形态 |
|--------|-----------|------------|
| `identical_column` / `constant_offset` / `constant_ratio` / `exact_linear` | 同一 block 内两列存在精确数值关系 | `col B = col A + 2.13` 出现在所有 10 行 |
| `arithmetic_progression` | 整列等差 / 等比 | "一组对照组 Y 是完美 1, 2, 3, 4… 整数" |
| `within_col_value_duplication` | 单列里同一个 6 位以上小数反复出现 | "0.208975 在'独立的'实验里出现 8 次" |
| `within_col_decimal_repetition` | 同一组的 N 个数字小数尾 2 位高度重复 | "全部以 .25 / .75 结尾，分布不正常" |
| `rounded_to_half_or_int` | 整列被舍入到固定刻度 (整数 / 0.5 / 0.25) | 自然测量不会全部精确落在某网格 |
| `identical_after_rounding` | 两列舍掉末位后完全相同 | 一列 = 另一列 × 小扰动 |
| `grim_inconsistent` | 报告的均值在该 n 下对整数数据不可能（GRIM，仅整数计数/评分类） | "n=10 的细胞计数均值出现 3.45——整数和除以 10 给不出这个值" |
| `grimmer_inconsistent` | 报告的 SD 在该均值与 n 下对整数数据不可能（GRIMMER） | "均值/n 自洽，但这个 SD 没有任何整数样本能产生" |
| `many_equal_pairs` | 两个本该独立的列里有 ≥40% 行 byte-identical | 9/10 一致 + 1 格手改的指纹 |
| `cross_sheet_position_identical` | 两张 sheet 在同行同列位置数值完全一样 | 同一份样本被分析了两次 |
| `last_digit_chi_square` | 整 sheet 末位数字偏离 χ² 均匀分布（BH-FDR 多重检验校正后 q ≤ 0.05） | 真实测量末位应近似均匀 |
| `repeated_two_decimal_endings` | 末两位高度集中 | 编造数字常见模式 |

每条命中都带 severity (`high` / `medium` / `low`) + 涉及的文件 / sheet / block 行范围 / 规则字符串，方便人工复核时直接定位。

> 密集 / 相关矩阵（相关系数表、归一化重复样本面板）会按 O(列²) 刷出成千上万条 `identical_column` / `exact_linear`——这类列关系是数据形态的结构性产物，不是造假指纹。工具按 **(file, sheet) 整张表**的关系总数判定洪泛，超阈值时整体**自动降级为 `low`** 并打 `dense_block` 标记（保留可见、不丢弃），不让它淹没真正的跨 sheet 重复信号。

**跨 sheet 重复**还会额外给出上下文，降低误读：

- **按图号分级**：解析 sheet 名里的图号（`Figure 5o` → `main:5`、`exFig.6i` → `ext:6`）。同一图号两个 panel 共享数据（合并曲线 vs 个体曲线的预期重画）自动**降级为 `low`**；只有跨图/跨文件的重复才保持 `high`/`medium`——让真正值得查的那条不被同图噪音淹没。
- **`delta` 形态**：区分 `perfect_dup`（干净重画）/ `superset`（多一列重复样本，n=5 vs n=6）/ `value_tweaked`（就地改值，copy-then-tweak 指纹）/ `value_divergent`。
- **`likely_benign`**：常见良性解释直接写进 finding（如"day/dose 轴""主图/扩展图共享 cohort，核对 legend"），报告里随卡片展示，避免把 signal 当 verdict。

---

## 想先看看效果？

[`examples/`](examples/) 里有一份完整的可跑示例：一份**合成的**"假论文" source data（埋了各类造假模式）+ paperconan 跑出来的报告 + 每条 finding 的逐条走查。直接看 [examples/README.md](examples/README.md) 和报告截图 [examples/report-preview.png](examples/report-preview.png)，不装也能感受输出长什么样。

---

## 安装 & 跑

需要 Python ≥ 3.10。

```bash
# 安装
git clone https://github.com/zixixr/paperconan.git
cd paperconan
pip install .

# 想顺带审补充材料里 .pdf / .docx 的表格，装上对应可选依赖（纯 Python、无 Java/系统依赖）：
pip install '.[all]'      # 或 .[pdf] / .[docx] 单装

# 跑一篇论文（指向其 source data 目录）
paperconan path/to/paper_dir/

# 也可以用 module 形式
python -m paperconan path/to/paper_dir/

# 输出（默认在 <in_dir>/audit/）：
#   scan.json      — 完整结构化 findings
#   report.html    — 自包含的人类可读 HTML 报告（浏览器打开即可）
#
# 可选：
paperconan path/to/paper_dir/ --md                  # 额外生成 REPORT.md
paperconan path/to/paper_dir/ --no-html             # 跳过 HTML（CI / 脚本场景）
paperconan path/to/paper_dir/ --out /tmp/audit-of-this-paper
paperconan path/to/paper_dir/ --profile forensic    # 关掉误报降级，看原始 severity（见下）
```

#### 误报降级档位 `--profile {review,forensic,triage}`

检测器只产出原始信号；要不要把"看着像误报"的命中降级，是另一层判断。`--profile` 控制这层，**默认 `review`** —— 也就是说你拿到的 scan.json 里，一部分命中已经被悄悄降级了：

| 档位 | 行为 | 什么时候用 |
|---|---|---|
| `review`（默认） | 把按列名/形态匹配上的疑似误报降为 `low`，但**保留可见**并标注原因 | 日常审计的平衡默认 |
| `forensic` | **不降级任何东西**，每条命中保留原始 severity | 想复核某条降级是否成立、或怀疑默认档把真信号藏了时 |
| `triage` | 和 `review` 同样的降级，但直接**隐藏**（不展示） | 只想要一份最干净的清单做汇总时 |

降级的命中会带 `profile_action`（`demoted`/`hidden`）和 `false_positive_context` 标签（如 `axis_or_scan_column`、`censoring_or_boundary_value`、`same_data_replot_or_duplicate_upload`），写明"为什么判它像误报"。**一条 `review` 档下被降成 `low` 的命中，那个 severity 是过滤器的意见，不是检测器的判定** —— 拿不准就重跑 `--profile forensic` 看原始严重度。

### 自动抓取论文数据（v0.4）

只有论文、没有本地数据时，可以让 paperconan 去开放数据源找：

```bash
paperconan fetch "10.xxxx/your.doi"            # 列出 Zenodo/Figshare/Dryad/Europe PMC 候选 + 匹配信号
paperconan fetch "10.xxxx/your.doi" --download zenodo:123456 --out data/
paperconan fetch "10.xxxx/your.doi" --auto --out data/   # 仅当有候选确属本文时才下载
paperconan data/                                # 再照常分析
```

只覆盖开放数据源、**不绕付费墙、不抓取出版商页面**：

- **Zenodo / Figshare**：keyless 直接下载。
- **Europe PMC**：keyless。开放获取论文（很多 NIH/Wellcome 资助的 Nature 论文都在内）的补充材料以一个 zip 提供，`fetch` 会自动下载并解压出其中的 `.xlsx/.csv/.tsv`。整包档案按 250MB 上限下载（单张表仍限 50MB），避免"体积大但内含小表格"的档案被截断丢弃。
- **Dryad**：仅做检索（下载接口需鉴权），命中后请到 Dryad 数据集页面手动下载。
- **匹配可信度门槛**：仓库全文检索（尤其 figshare/zenodo）常返回**完全无关的他人数据集**，所以 `--auto` 只在候选 DOI 命中或标题高度重合时才下载，否则拒绝并转期刊指引；`--download` 一个不匹配的候选需加 `--force`；列表里这类候选会标 `⚠ no DOI/title match`。绝不让你误把别人的数据当本文来审。
- **都没命中**：`fetch` 会按 DOI 输出一段期刊指引（出版商 + `doi.org` 文章链接 + 该刊 source data 的常见位置，如 Nature 的 `...MOESM<N>_ESM.xlsx`），而不是简单告诉你"没找到"——绝不暗示"查过=干净"。

`fetch --download/--auto` 还会在下载目录写一个 `paperconan_source.json`（记录 DOI/标题/来源），随后 `paperconan <dir>` 会自动把它写进 scan.json 的 `paper` 字段做溯源；也可手动 `paperconan <dir> --doi <DOI> --title <T>` 标注。

### 审补充材料 PDF / Word 里的表格（v0.5）

很多数据造假根本不在可下载的 `.xlsx` source data 里，而是**就摆在论文/补充材料本身**——补充 PDF 的附表、Word 附录表。这类"不需要单独 source data、看论文就能查"的数值，paperconan 现在能直接吃进来：

```bash
pip install '.[pdf]'                 # 或 .[docx] / .[all]
paperconan path/to/dir_with_si_pdf/  # 目录里有 .pdf / .docx 即可，和 xlsx 混放也行
```

- 从 PDF / Word 里抽出的**每一张表**变成一个 sheet，命名带溯源：PDF 是 `<文件名>!p<页>_t<第几张表>`、Word 是 `<文件名>!t<第几张表>`——报告里一眼看出"这条命中来自补充 PDF 第 3 页第 1 张表"。
- 抽出来的表走的是**和 xlsx 完全相同的那套数值检测器**（等差数列、末位偏置、列复制、跨表重复……），不是新算法。
- **只抽真正的结构化表格**：不从 bar chart / 曲线的像素里数字化数据点（数字化误差本身会触发等差/重复检测器造成假阳性），也不做扫描件 OCR。图表像素数字化 / 图像取证仍是未做项（见路线图）。
- 可选依赖缺失时给出明确提示（`pip install 'paperconan[pdf]'`），绝不影响只跑 xlsx 的基础安装。

**report.html** 长这样（单文件、无外部依赖、可直接邮件/PubPeer 附件分享）：

- 顶部摘要：n_files / n_sheets / high / medium / low 计数
- 左侧边栏：按 severity / detector 类型 / 文件 勾选过滤 + 关键字搜索
- 主区域：每条 finding 是一张可折叠卡片，里面直接渲染出**可疑表格片段**，高亮可疑列（黄底）和高亮行（红边）—— 用户不需要再打开 xlsx 翻位置；有常见良性解释的，卡片里直接给出 `likely_benign` 旁注
- 末位数字 χ² 异常（BH-FDR 校正后）自带 0-9 inline 直方图，并显示 q 值
- 跨 sheet bit-identical collisions 单独成段，最高优先级展示

---

## 作为 skill 使用（被 Claude Code / Codex / 其他 agent 调用）

[`skills/paperconan/SKILL.md`](skills/paperconan/SKILL.md) 是给 agent 看的入口，自带 YAML frontmatter（name + 触发关键词）和怎么解读 `scan.json` 的指引。同目录 `references/` 下有：

- [`detectors.md`](skills/paperconan/references/detectors.md) — 每个检测器的原理 / 典型命中 / **常见误报**
- [`interpretation.md`](skills/paperconan/references/interpretation.md) — severity 语义 + "signal not verdict" 红线 + 推荐回复话术

整个 skill 收在 `skills/paperconan/` 一个目录里，安装只需软链一次。

### 安装

```bash
# 1. 装 CLI
pip install -e /path/to/paperconan       # 本地开发；发到 PyPI 后改成 pip install paperconan

# 2a. Claude Code: 软链整个 skill 目录（一次搞定）
ln -s /path/to/paperconan/skills/paperconan ~/.claude/skills/paperconan

# 2b. Codex / 其他 agent: 在 AGENTS.md / CLAUDE.md / GEMINI.md 里引用
echo '@/path/to/paperconan/skills/paperconan/SKILL.md' >> AGENTS.md
```

之后在 agent 会话里说"我有一篇论文的 source data 在 /path/to/data，帮我做个 sanity check"，agent 会自动调用 paperconan 并按 SKILL.md 的解读规则向你汇报。

---

## ⚠️ 重要声明

`paperconan` 输出的是 **算法标注的可疑模式 (statistical anomalies)**，**不是学术不端结论**。

最终判定需由原作者澄清、期刊编辑部核实，或经独立同行复议。

**请走正规渠道**：

- 把可疑信号提交到 [PubPeer](https://pubpeer.com/)
- 联系期刊编辑部（每本期刊都有 ethics inquiry 渠道）
- 如果是你所在单位的论文，走单位 research integrity office

**请不要**：

- 在微博 / 微信 / 知乎 / 抖音直接喊话指控某个具体作者
- 用 paperconan 的输出截图作为 "实锤" 在社交媒体传播
- 跳过原作者澄清环节直接定性

工具是中立的，使用方式不能。

---

## FAQ

**Q: 它会漏掉哪些造假？**

会。`paperconan` 只看**数值表格**——无论它来自 `.xlsx/.csv/.tsv`，还是补充材料 `.pdf/.docx` 里的表格。下面这些它一概查不到：

- 图像 PS / Western blot 拼接 / 显微镜照片重复使用（图像取证是另一套，仍未做）
- **图表（bar chart / 曲线）里的数据**——只要数字没以表格形式给出，就读不到（不做像素数字化，详见路线图）
- 临床数据造假但 source data / 补充材料都没公开
- 实验完全没做但写进文章的部分
- 统计方法选择性使用 (p-hacking)
- 引用造假 / 同行评议造假

> 注：补充材料 PDF/Word 的**表格**现在能读了（v0.5，需 `pip install 'paperconan[all]'`）——这覆盖了"不需要单独 source data、数据就摆在论文里"的一大类。但"摆在图里"的数据（柱状图、折线图）和图像本身仍在覆盖之外。

**Q: 它会误报吗？**

会。比如：

- 同一篇论文里 **合理共享的对照组数据** 会被标记为 "跨 sheet 复用"
- **量化产生的终位偏置**（细胞计数 / 4 视野平均 → 多 0.25 步长）会被标记为 "末位异常"
- **共享的剂量轴 / 时间轴** 会被标记为 "跨列复制"
- **密集 / 相关矩阵** 里成千上万对相同/线性列会被标记为 "列关系"

不过这几类现在工具会主动帮你识别：同图重画的跨 sheet 复用会**自动降级**，密集矩阵的列关系洪泛会按整张表降级并打 `dense_block`，剂量/时间轴、量化偏置等会附 `likely_benign` 旁注，末位 χ² 也做了多重检验校正（看 q 值而非裸 p）。误报降级的力度可以用 `--profile` 调（默认 `review`，`forensic` 关掉全部降级看原始信号，`triage` 把疑似误报直接藏掉）。即便如此，报告里 high severity 的 anomaly **依然需要人工读 figure legend 和 Methods** 才能下判断。不要把 high = misconduct。

**Q: 我用它发现一篇看似有问题的论文，下一步该做什么？**

1. 仔细读 paper 的 figure legends 和 Methods — 看作者是否已经在文字里说明了 "shared control" 等情况
2. 自己用 Excel 复核 paperconan 标出的具体位置
3. 提交到 PubPeer（最常见的做法），等原作者回应
4. 如果是你所在单位，找 research integrity office

**Q: 这个工具会不会让普通硕博更难毕业？**

不会。它检测的是 "编造数据" — 也就是 9 位小数一字不差、跨独立实验值池只用 17 个数字、9/10 完全一致只改 1 格这种 **直接编数字** 的模式。

普通硕博正常做实验产生的 messy data（不规范、有 negative result、原始记录不齐），paperconan 一律不会标。

如果你的数据 paperconan 也标出来了 — 那可能要回去看看自己的实验记录和分析流程了。

---

## 同款诞生背景

这个工具最早是为做一期 YouTube / 抖音 / B 站视频造的 — 视频里我用它独立扫了 7 篇 Nature 及 Nature 子刊论文，全部找到了可疑模式。

工具开源给所有人。希望它能帮老实做实验的人，减少作弊者占用版面、占用帽子、占用博士点的概率。

---

## 路线图 (好玩的可以一起做)

短期：

- [x] CSV / TSV 输入 — v0.3 已实现（含跨文件数据复用检测）
- [x] HTML 报告（在 REPORT.md 之外）— v0.2 已实现，并取代 REPORT.md 成为默认人类可读输出
- [x] 把每条 finding 嵌入对应的表格片段 — v0.2 实现为可交互 HTML 表格（不是截图）
- [x] 作为 skill 给 agent 调用 — v0.2 已实现，见 skills/paperconan/SKILL.md
- [x] 补充材料 PDF / Word 表格输入 — v0.5 已实现（`pip install 'paperconan[all]'`，复用全部数值检测器）
- [ ] PubPeer 风格的 "为什么这值得复核" 旁注（LLM 生成的可选）

中长期：

- [ ] 跨论文扫描（一个 lab 的多篇论文一起跑，看跨论文数据复用）
- [ ] 图表像素数字化（从 bar chart / 曲线里提取数据点）— 精度与假阳性风险高，需谨慎做
- [ ] 图像取证检测（Western blot / 显微镜照片重复）— 这块需要专门做
- [ ] 与 [PubPeer Public API](https://pubpeer.com/api) 联动

欢迎 PR。给检测器加新模式，写 README 翻译，做 demo 都很欢迎。

---

## License

MIT.

## Acknowledgments

- 名侦探柯南 / Detective Conan © 青山刚昌 / TMS Entertainment — 借了一下片头叙事结构，致敬不致敬都得感谢一下。
- [PubPeer](https://pubpeer.com/) — 学术界最重要的公开质疑平台，paperconan 的输出最终都应该走这里。
