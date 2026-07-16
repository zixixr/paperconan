# PaperConan 自适应图像诊断设计

日期：2026-07-10
状态：已批准

## 1. 背景

PaperConan 当前的正式能力集中在表格和数值 finding。`paperconan-watch` 的
`lab/image_forensics/` 已验证了两个重要事实：

1. 多模态模型直接查看整张复合图时，可能因缩放和信息密度遗漏局部相似区域。
2. 当模型获得紧凑、清晰的局部区域对时，语义判断能力明显强于纯确定性规则。

同一批实验也说明，确定性面板分割、相似度排序或区域自相似算法会漏掉部分情况，
因此不能成为模型查看图像之前的强制过滤门槛。

本设计采用**自适应双路径**：PaperConan 负责合法获取、图像资产整理、可复现的辅助
诊断和统一报告；用户自己的多模态 AI Agent 负责直接看图、理解图注与实验语义并作出
审慎判断。PaperConan 不训练专用视觉模型，也不管理任何模型 API 或密钥。

## 2. 已确认的产品约束

1. **模型调用属于外部 Agent。** PaperConan CLI 不调用多模态模型，不持有模型配置、
   API key 或供应商适配器。
2. **图像 finding 不单独生成报告。** 数值 finding、确定性图像信号和 Agent 图像判断
   必须进入现有的 `scan.json`、`verdict.json findings[]` 和统一判定报告。
3. **不重建论文获取系统。** 图像获取扩展现有 `paperconan.fetch` 的 DOI 解析、来源
   搜索、下载、重试、容量限制和 provenance；只有现有接口无法表达图像资产时才补充。
4. **线上合法获取优先，本地文件兜底。** 可使用公开网页、开放仓库、公开 API、公开
   高分辨率资源以及用户有权访问的本地文件；不突破付费墙、验证码、访问控制或反爬
   限制。
5. **确定性诊断只提供线索。** 面板分割、区域相似度和变换鲁棒匹配不能隐藏、丢弃或
   自动判定其他图像。
6. **中性语言始终成立。** 所有 finding 都是统计相似性信号、数据不一致或待解释异常。

## 3. 目标

### 3.1 用户目标

用户向具备多模态能力的 AI Agent 提供 DOI、论文 PDF、补充材料或图像目录后，Agent
能够：

1. 使用现有 fetch 链路取得公开可访问的论文材料。
2. 调用 PaperConan 生成统一 `scan.json`，其中同时包含数值 finding、图像资产目录和
   可选的确定性图像信号。
3. 直接查看全部相关图像；需要时调用 PaperConan 生成面板、局部区域对或其他辅助证据。
4. 将每个图像判断写入现有 `verdict.json findings[]`。
5. 使用现有 `paperconan report` 生成一份同时包含数值与图像 finding 的判定报告。

如果 Agent 无法读取图片，Skill 必须明确说明图像语义复核未完成，不能把“未复核”
表述为“未发现信号”。

### 3.2 工程目标

- 原始图像和模型输入始终保留原始像素；报告缩略图只用于展示。
- 每个图像资产有稳定 ID、来源、尺寸、哈希和本地路径。
- 每个图像 finding 能引用一个或多个资产及其像素区域。
- Agent 可以产生没有确定性候选对应的图像 finding。
- 确定性图像信号与 Agent 判断能在同一 finding 中并列展示。
- 同一输入产生确定性的 manifest、信号 ID 和报告结构。
- 所有新路径遵守现有文件大小、总容量和证据数量限制。

## 4. 非目标

- 不训练、微调或托管专用视觉模型。
- 不在 PaperConan 中实现模型供应商 SDK。
- 不保证从所有出版商自动取得正文高清图。
- 不突破访问限制。
- 首版不做跨论文图像索引或大规模相似图搜索。
- 首版不做图表像素数字化。
- 不把 `paperconan-watch` 的实验脚本直接复制进正式包。
- 不创建第二套图像报告或第二套下载框架。

## 5. 总体架构

```text
DOI / title / local files
        |
        v
existing paperconan.fetch + local discovery
        |
        v
image_assets[] + numeric scan + optional image_findings[]
        |
        +-----------------------------+
        |                             |
        v                             v
Agent direct review             deterministic helpers
full figure / native crop       panel / pair / region signals
        |                             |
        +-------------+---------------+
                      v
             verdict.json findings[]
                      |
                      v
       existing paperconan report unified HTML
```

### 5.1 路径 A：Agent 直接看图

这是默认且不可被确定性规则阻断的路径。

1. Agent 先查看完整图，保留图注、面板标签和实验关系。
2. 小字、微小面板或局部结构无法判断时，再查看原始像素裁剪。
3. 即使 PaperConan 没有产生任何图像信号，Agent 仍可创建图像 finding。
4. 每个资产必须记录为 `reviewed`、`unresolved`、`unreadable` 或 `deferred`，不能静默
   跳过。

### 5.2 路径 B：确定性辅助诊断

PaperConan 可以提供：

- 面板区域建议；
- 面板对变换鲁棒相似度；
- 同图远距离区域相似性；
- 原始像素区域对和证据缩略图；
- 诊断参数、分数和定位框。

确定性配对只在一个登记资产内部进行。比较预处理会裁掉低信息边缘，并对归一化强度与
边缘一致性加权评分，覆盖恒等变换、水平/垂直翻转、90/180/270 度旋转、主对角线转置和
副对角线转置共八种二面体变换；`0.92` finding 阈值不降低。`regions` 与原始 evidence
仍保留 native proposal boxes，裁边不改写定位坐标。裁边结果过小或变化不足时使用确定性
fallback，继续比较原 proposal。跨资产比较由外部多模态 Agent 完成。

图像 `profile_action: "kept"` 仅作信息标记，不经过数值 prefilter。来源身份仍稳定但
evidence 预算或发布失败时，finding 以 `evidence: null` 保留并写入 `scan_errors`；评分后
来源身份改变时抑制 finding。

这些结果进入 `image_findings[]`，作用是帮助 Agent 放大、交叉检查和排序。它们不能：

- 从 `image_assets[]` 删除资产；
- 把未命中的图像标记为正常；
- 让 Skill 只查看 top-K 而不说明覆盖不完整；
- 取代图注、Methods、原始数据和人工复核。

## 6. 扩展现有 fetch

### 6.1 复用的正式模块

- `paperconan.fetch._resolve`：DOI/title 规范化和元数据补全。
- `paperconan.fetch._sources`：Zenodo、Figshare、Dryad、Europe PMC 等来源。
- `paperconan.fetch._nature`：Nature 文章页公开资源。
- `paperconan.fetch._http`：HTTP、超时和错误处理。
- `paperconan.fetch._download`：下载、重试、大小限制、归档解包和 provenance。
- `paperconan.fetch._files`：文件类型分类。

### 6.2 补充方式

1. 在 `_files.py` 增加图像扩展名与 `is_image()`，不改变 `is_tabular()` 行为。
2. `download_candidate()` 增加资产类型选择，默认仍只下载表格，保证旧 CLI 不变。
3. Europe PMC OA 包和补充压缩包使用现有安全解包函数，按调用方请求提取表格、图像或
   两者。
4. `_nature.py` 在现有文章页请求中解析公开 figure 资源；继续使用现有 `_http` 和
   `download_file()`，不增加独立抓取器。
5. 每个下载文件继续写入现有 provenance sidecar，并增加原始 URL、内容类型和资产类型。
6. HTTP 401/403、验证码页面或认证要求只记录为不可获取，并提示用户提供其有权访问的
   文件。

建议 CLI：

```bash
paperconan fetch "<DOI or title>" --images
paperconan fetch "<DOI>" --auto --images --out data/
```

`--images` 是对现有 fetch 的增量选项，不是新的 fetch 命令。

## 7. 图像资产层

正式代码放在新的内部子包：

```text
src/paperconan/image/
  __init__.py
  _assets.py       本地发现、哈希、尺寸、PDF 页面渲染、manifest
  _diagnostics.py  可选确定性辅助信号
  _evidence.py     原始裁剪和有界报告缩略图
```

职责边界：

- `_assets.py` 只负责把输入转换为可追踪资产，不做异常判断。
- `_diagnostics.py` 只消费资产并产生统计信号，不决定哪些资产值得模型查看。
- `_evidence.py` 保证模型裁剪保留原始像素，同时为 HTML 生成有界缩略图。

首版输入：

- PNG、JPEG、TIFF、WebP；
- PDF 页面渲染；
- fetch 下载的公开正文图与补充图。

PDF 使用已有 `pypdfium2` 依赖路径渲染页面；不再引入另一套 PDF 引擎。原始 PDF 仍保留，
页面图是派生资产，manifest 记录页码和渲染 DPI。

建议 CLI：

```bash
paperconan <input-dir> --images
```

该命令继续执行现有数值扫描，同时增加图像资产和可选图像信号。只有图片而没有表格时，
`--images` 模式仍可成功产生 `scan.json`。

## 8. `scan.json` 扩展

### 8.1 `image_assets[]`

```json
{
  "asset_id": "img:sha256-prefix",
  "file": "Fig3.png",
  "path": "images/native/img-....png",
  "preview_path": "images/preview/img-....jpg",
  "source_type": "local_image",
  "source_url": null,
  "parent_file": null,
  "page": null,
  "figure_label": "Fig. 3",
  "sha256": "...",
  "width": 2480,
  "height": 1760,
  "mime": "image/png"
}
```

约束：

- `path` 指向原始像素资产或无损复制。
- `preview_path` 只用于报告和快速浏览。
- `asset_id` 基于内容哈希，排序稳定。
- 不在 JSON 中嵌入完整原图。
- 复核状态属于 Agent verdict，不写回确定性资产记录。

### 8.2 `image_findings[]`

```json
{
  "finding_id": "image:pair:stable-id",
  "kind": "image_pair_similarity_signal",
  "severity": "medium",
  "rule": "two image regions retain high structural similarity under a horizontal flip",
  "asset_ids": ["img:a"],
  "regions": [
    {"asset_id": "img:a", "box": [120, 80, 740, 610]},
    {"asset_id": "img:a", "box": [820, 80, 1440, 610]}
  ],
  "method": "panel_pair_similarity",
  "score": 0.94,
  "transform": "flip",
  "evidence": {
    "preview_path": "images/evidence/image-pair-....jpg"
  },
  "profile_action": "kept"
}
```

`image_findings[]` 是确定性统计信号。Agent 的最终判断不回写这里，而是进入
`verdict.json findings[]`。确定性 finding 只引用一个资产；跨资产 observation 由外部
多模态 Agent 使用 `image_refs` 创建。所有数值与图像 finding 最终进入同一个统一报告。

## 9. 统一 verdict 与报告

### 9.1 混合 finding

现有 `verdict.json findings[]` 保持唯一用户判定入口。数组可以同时包含数值 finding 和
图像 finding。

图像 finding 新增：

```json
{
  "finding_type": "image",
  "title": "Fig. 3 panel pair requires clarification",
  "finding_ref": {"finding_id": "image:pair:stable-id"},
  "image_refs": [
    {"asset_id": "img:a", "box": [120, 80, 740, 610], "label": "A"},
    {"asset_id": "img:b", "box": [40, 55, 660, 585], "label": "B"}
  ],
  "review_status": "needs_human",
  "impact_scope": "supporting",
  "report_md": "..."
}
```

`finding_ref` 可选。Agent 直接看图发现、但确定性工具没有命中时，只写 `image_refs` 即可。

### 9.2 图像判断状态

图像 finding 只接受：

- `needs_human`：存在待解释相似性，需要原图、图注、Methods 或作者说明。
- `explained`：图内或上下文已有合理解释。
- `different`：区域不是同一底层图像。
- `unresolved`：分辨率、上下文或模型能力不足。

缺失、未知或格式错误的状态归一为 `unresolved`，绝不能自动归入 `explained`。

### 9.3 Agent 能力与覆盖

verdict 顶层新增可选字段：

```json
{
  "image_review": {
    "status": "completed",
    "reviewed_asset_ids": ["img:a", "img:b"],
    "unresolved_asset_ids": [],
    "unreadable_asset_ids": [],
    "deferred_asset_ids": [],
    "note": "all primary figures reviewed with a multimodal agent"
  }
}
```

`status` 取值：

- `completed`
- `partial`
- `unavailable_no_multimodal`
- `not_requested`

报告必须显示该覆盖状态。没有多模态能力时，数值报告仍可生成，但必须明确图像语义复核未
完成。

### 9.4 现有报告渲染器的扩展

扩展 `_html._all_findings()` 和 `_adjudicated_html.py`，不创建新 renderer：

1. 确定性 `audit/report.html` 将 `image_findings[]` 作为新的 finding scope 展示。
2. `paperconan report` 的现有 finding block 同时支持数值证据表和图像证据。
3. 图像 finding 有 `image_refs` 时，证据缩略图紧跟该 finding 的文字判断。
4. 模型输入仍使用原始图像；HTML 只内嵌有大小上限的预览。
5. 图像 finding 没有 `finding_ref` 时，不再错误回退到最强数值 finding。
6. 数值 finding 的旧 schema 和报告外观保持兼容。
7. renderer 只按 `asset_id` 读取 `scan.json image_assets[]` 已登记的文件；verdict 不能提供
   任意本机路径。
8. verdict 中准备进入报告的模型文本必须通过中性语言校验；不符合要求时拒绝渲染并提示
   Agent 改写，错误信息不回显原始不当文本。

## 10. Skill 自适应流程

`skills/paperconan/SKILL.md` 从“只支持表格”升级为统一审查流程。

### 10.1 能力检查

Skill 在图像步骤开始前确认当前 Agent 能否查看本地或已下载图片：

- 可以：继续图像复核。
- 不可以：写入 `unavailable_no_multimodal`，明确说明限制，继续数值流程。

PaperConan CLI 不尝试推断模型类型。

### 10.2 自适应复核顺序

1. 读取 `image_assets[]`，核对资产数量、尺寸、来源和可读性。
2. 先看完整图，理解图注、面板标签、通道、处理步骤和共享模板。
3. 对小面板或细节不足的图使用原始像素裁剪。
4. 查看 `image_findings[]` 作为辅助线索，但不只检查这些资产。
5. 对每个待解释区域尝试寻找多通道、merge、before/after、处理步骤、共享对照、模板或
   inset 等解释。
6. 写入混合 `findings[]` 和 `image_review` 覆盖信息。
7. 使用现有 `paperconan report` 生成唯一用户报告。

对于图像数量过多的论文，Agent 可以分批和优先排序，但所有未查看资产必须进入
`deferred_asset_ids`；报告状态为 `partial`，不能声称完成。

## 11. 依赖与资源限制

图像能力使用可选 extra，基础数值安装保持轻量：

```toml
image = [
  "pillow>=12",
  "pypdfium2>=5",
  "opencv-python-headless>=4.10"
]
all = [
  "...existing extras...",
  "...image dependencies..."
]
```

新增环境上限：

- `PAPERCONAN_MAX_IMAGE_MB`：单图最大读取大小。
- `PAPERCONAN_MAX_IMAGE_PIXELS`：单图解码像素上限。
- `PAPERCONAN_MAX_IMAGE_ASSETS`：单次扫描资产上限。
- `PAPERCONAN_MAX_IMAGE_TOTAL_MB`：单次扫描图像产物总磁盘预算，默认 `1500` MiB，
  覆盖原图副本、预览、PDF 渲染暂存、诊断原始裁剪和拼图证据；确定性替换抵扣已有最终
  文件体积。
- `PAPERCONAN_MAX_IMAGE_FINDINGS`：确定性图像信号上限。
- `PAPERCONAN_MAX_IMAGE_COMPARISONS`：整次扫描最多尝试的区域比较数，默认 `100000`。
- `PAPERCONAN_MAX_IMAGE_EVIDENCE_MB`：报告内嵌预览总上限。

达到上限时必须写入 `scan_errors` 或 coverage 状态，不能静默丢弃。

## 12. 错误处理

- 缺少图像 extra：`--images` 返回可执行的安装提示；数值扫描不受影响。
- 图像损坏：记录资产路径和读取错误，继续其他文件。
- PDF 页面渲染失败：记录页码和错误，保留原 PDF。
- 在线资源受限：记录来源和 HTTP 状态，提示用户提供本地文件。
- 模型输出状态未知：归一为 `unresolved`。
- 图像引用不存在：报告显示“图像证据引用未命中”，不回退到无关数值证据。
- verdict 试图引用未登记路径：拒绝读取，只接受 `image_assets[]` 中的 `asset_id`。
- 模型文字不符合中性语言规则：拒绝生成用户报告并要求改写。
- 同名文件：使用内容哈希 ID 和稳定输出名，避免覆盖。

## 13. 测试与 benchmark

### 13.1 正式仓库测试

全部使用合成图片和本地 HTML/PDF fixture：

- 图像发现、稳定 ID、同名文件不覆盖。
- PDF 页面渲染和 provenance。
- fetch 公开 figure 链接解析及受限访问处理。
- 原始裁剪不缩放，预览单独缩放。
- 确定性信号不删除资产。
- Agent-only image finding 能进入统一报告。
- 数值与图像 finding 混合渲染。
- 未知状态变成 `unresolved`。
- 不符合中性语言规则的模型文字被报告入口拒绝。
- verdict 不能让 renderer 读取 `image_assets[]` 之外的路径。
- 无多模态能力的 coverage 提示。
- 资源上限有显式错误记录。
- 相同输入输出顺序和 ID 完全一致。

### 13.2 `paperconan-watch` benchmark

实验仓库继续保存真实数据 benchmark，但不进入正式包：

1. builder 改为 CLI 参数，删除会话临时路径。
2. 结果区分：
   - 整图模型能力；
   - oracle 局部区域模型能力；
   - PaperConan 确定性辅助的端到端覆盖。
3. 每次运行输出机器可读 JSON，保存模型、提示版本和时间。
4. 真实论文图片和 DOI 继续留在 gitignored 本地目录。
5. benchmark 结果只用于评估和回归，不作为对具体论文的最终判断。

## 14. 分阶段交付与 commit 边界

### 阶段 0：设计与基线

仓库：`paperconan`

- 本设计文档。
- 基线测试命令和结果。

Commit：

```text
docs(spec): design adaptive multimodal image diagnostics
```

### 阶段 1：统一 schema 与报告

仓库：`paperconan`

- `image_assets[]`、`image_findings[]` TypedDict 和稳定引用。
- `_all_findings()` 支持 image scope。
- 现有判定报告支持 `image_refs` 和 `image_review`。
- 严格图像状态归一化。
- 报告入口校验中性语言，图像引用只允许已登记 `asset_id`。
- 图像 finding 不匹配时不回退无关数值 finding。

Commit：

```text
feat(report): integrate image findings into unified adjudication
```

### 阶段 2：扩展现有 fetch 与资产准备

仓库：`paperconan`

- 在现有 fetch 中增加 `--images`。
- 公开 figure、OA 包和补充压缩包图像提取。
- 本地图像和 PDF 页面资产 inventory。
- 原始图、预览、哈希、provenance 和容量限制。
- `paperconan <dir> --images` 生成统一 scan。

Commit：

```text
feat(image): add image assets through existing fetch and scan flows
```

### 阶段 3：可选确定性辅助工具

仓库：`paperconan`

- 从实验代码中重新实现并测试最小必要的面板和区域相似性工具。
- 原始像素证据裁剪。
- 所有资产保持可见，信号不作为过滤器。
- 合成 oracle 与端到端回归测试。

Commit：

```text
feat(image): add non-gating deterministic image diagnostics
```

### 阶段 4：Skill 与统一工作流

仓库：`paperconan`

- 多模态能力检查。
- 自适应双路径。
- 图像 coverage 记录。
- 混合 verdict 示例和统一报告说明。
- README 和路线图更新。

Commit：

```text
docs(skill): orchestrate adaptive multimodal image review
```

### 阶段 5：实验 benchmark 修复

仓库：`paperconan-watch`

- 可配置 builder。
- oracle、整图和端到端指标分离。
- 机器可读结果和环境记录。
- 删除实验性独立报告路径，统一验证正式 schema。

Commit：

```text
test(image-forensics): make multimodal benchmark reproducible
```

阶段 5 可以与阶段 1-4 并行，但正式发布前必须完成一次 benchmark 回归。

## 15. 验收标准

1. 一个同时包含数值表和论文图片的目录，通过一次扫描生成一个 `scan.json`。
2. `scan.json` 同时包含数值 finding、`image_assets[]` 和可选 `image_findings[]`。
3. 多模态 Agent 可以不依赖确定性候选，直接引用资产创建图像 finding。
4. `paperconan report` 在同一份 HTML 中展示数值与图像 finding。
5. 图像证据展示使用预览，但模型和裁剪工具使用原始像素。
6. 未知模型状态不被自动标记为已解释。
7. 无多模态能力时，报告明确标识图像复核未完成。
8. DOI 图像获取复用现有 fetch、下载、重试、容量和 provenance。
9. 确定性图像信号的缺失不会隐藏任何资产。
10. 全部既有测试、图像合成测试和 benchmark 回归通过。
