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

喂一份 supplementary source data（一个目录，里面装着 `.xlsx` / `.csv` / `.tsv`）进去，它会跑十几组数值取证检测器，输出 **scan.json**（结构化的全部命中）+ **report.html**（自包含的可交互法证报告，每条 finding 直接嵌可疑表格片段并高亮可疑列/行），告诉你哪些位置值得人工再看一眼。

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
| `many_equal_pairs` | 两个本该独立的列里有 ≥40% 行 byte-identical | 9/10 一致 + 1 格手改的指纹 |
| `cross_sheet_position_identical` | 两张 sheet 在同行同列位置数值完全一样 | 同一份样本被分析了两次 |
| `last_digit_chi_square` | 整 sheet 末位数字偏离 χ² 均匀分布 (p < 1e-6) | 真实测量末位应近似均匀 |
| `repeated_two_decimal_endings` | 末两位高度集中 | 编造数字常见模式 |

每条命中都带 severity (`high` / `medium` / `low`) + 涉及的文件 / sheet / block 行范围 / 规则字符串，方便人工复核时直接定位。

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

# 跑一篇论文（指向其 source data 目录）
paperconan path/to/paper_dir/

# 也可以用 module 形式
python -m paperconan path/to/paper_dir/

# 输出（默认在 <in_dir>/audit/）：
#   scan.json      — 完整结构化 findings
#   report.html    — 自包含的人类可读 HTML 报告（浏览器打开即可）
#
# 可选：
paperconan path/to/paper_dir/ --md            # 额外生成 REPORT.md
paperconan path/to/paper_dir/ --no-html       # 跳过 HTML（CI / 脚本场景）
paperconan path/to/paper_dir/ --out /tmp/audit-of-this-paper
```

### 自动抓取论文数据（v0.4）

只有论文、没有本地数据时，可以让 paperconan 去开放数据仓库找：

```bash
paperconan fetch "10.xxxx/your.doi"            # 列出 Zenodo/Figshare/Dryad 的候选数据集 + 匹配信号
paperconan fetch "10.xxxx/your.doi" --download zenodo:123456 --out data/
paperconan data/                                # 再照常分析
```

只覆盖开放仓库、不绕付费墙。Zenodo / Figshare 可直接下载；Dryad 仅做检索（其下载接口需鉴权，命中后请到 Dryad 数据集页面手动下载）。很多论文没把数据存进可机读仓库，抓不到会如实告知。

**report.html** 长这样（单文件、无外部依赖、可直接邮件/PubPeer 附件分享）：

- 顶部摘要：n_files / n_sheets / high / medium / low 计数
- 左侧边栏：按 severity / detector 类型 / 文件 勾选过滤 + 关键字搜索
- 主区域：每条 finding 是一张可折叠卡片，里面直接渲染出**可疑表格片段**，高亮可疑列（黄底）和高亮行（红边）—— 用户不需要再打开 xlsx 翻位置
- 末位数字 χ² 异常自带 0-9 inline 直方图
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

会。`paperconan` 只看数值表格 source data。下面这些它一概查不到：

- 图像 PS / Western blot 拼接 / 显微镜照片重复使用
- 临床数据造假但 source data 没公开
- 实验完全没做但写进文章的部分
- 统计方法选择性使用 (p-hacking)
- 引用造假 / 同行评议造假

**Q: 它会误报吗？**

会。比如：

- 同一篇论文里 **合理共享的对照组数据** 会被标记为 "跨 sheet 复用"
- **量化产生的终位偏置**（细胞计数 / 4 视野平均 → 多 0.25 步长）会被标记为 "末位异常"
- **共享的剂量轴 / 时间轴** 会被标记为 "跨列复制"

报告里 high severity 的 anomaly **依然需要人工读 figure legend 和 Methods** 才能下判断。不要把 high = misconduct。

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
- [ ] PubPeer 风格的 "为什么这值得复核" 旁注（LLM 生成的可选）

中长期：

- [ ] 跨论文扫描（一个 lab 的多篇论文一起跑，看跨论文数据复用）
- [ ] 图像取证检测（Western blot / 显微镜照片重复）— 这块需要专门做
- [ ] 与 [PubPeer Public API](https://pubpeer.com/api) 联动

欢迎 PR。给检测器加新模式，写 README 翻译，做 demo 都很欢迎。

---

## License

MIT.

## Acknowledgments

- 名侦探柯南 / Detective Conan © 青山刚昌 / TMS Entertainment — 借了一下片头叙事结构，致敬不致敬都得感谢一下。
- [PubPeer](https://pubpeer.com/) — 学术界最重要的公开质疑平台，paperconan 的输出最终都应该走这里。
