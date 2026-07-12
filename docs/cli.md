# 命令行与库参考

> agent 用户通常不需要这一页 —— skill 会自动调这些命令。下面是给纯 CLI / 脚本 / 库集成用户的参考。

## 安装

```bash
pip install paperconan              # 基础（已含 python-calamine：读旧版 .xls / .xlsm / .xlsb，xlsx 也更快）
pip install "paperconan[all]"       # + PDF / Word 表格抽取
pip install -e ".[dev,all]"         # 本地开发
```

> `python-calamine` 现在是**基础依赖**（旧版 `.xls` 只有它能读，缺了就会被静默跳过）。`[fast]` 仍保留为向后兼容的别名，装不装都一样。

## 扫描

```bash
paperconan path/to/source_data_dir/                 # 默认输出 <dir>/audit/{scan.json,report.html}
paperconan path/to/dir/ --out /tmp/audit-this-paper
paperconan path/to/dir/ --md                        # 额外生成 REPORT.md
paperconan path/to/dir/ --no-html
paperconan path/to/dir/ --profile forensic
paperconan path/to/dir/ --doi "10.xxxx/..." --title "Paper title"
paperconan path/to/dir/ --runtime-metadata        # 显式记录扫描时间与耗时
python -m paperconan path/to/dir/                   # 等价 module 形式
```

默认 `scan.json` 是确定性的：相同输入重复扫描会产生逐字节一致的 JSON。
为保持 schema 兼容，`scanned_at` 以及 scan/file/sheet 层级的
`elapsed_ms` 键仍然存在，但默认值为 `null`。`scan_stats.files[].path`
相对于输入目录。只有显式传入 `--runtime-metadata` 才记录时间戳和耗时。

## 拉取开放源数据

```bash
paperconan fetch "10.xxxx/your.doi"
paperconan fetch "10.xxxx/your.doi" --json
paperconan fetch "10.xxxx/your.doi" --download zenodo:123456 --out data/
paperconan fetch "10.xxxx/your.doi" --auto --out data/
paperconan data/
```

覆盖 Zenodo / Figshare（keyless 检索下载）、Europe PMC / NCBI PMC OA（自动抽 supplementary 里的表）、nature.com ESM、Dryad。`--auto` 仅在 DOI 命中或标题高度一致时下载，弱匹配会被拒绝（需 `--download ... --force` 显式确认）。`fetch --download` / `--auto` 会写 `paperconan_source.json`，随后扫描会把 DOI/标题/来源写进 `scan.json.paper` 做溯源。

## PDF / Word 补充材料表格

装 `paperconan[all]` 后目录里的 `.pdf` / `.docx` 也会被扫描。PDF 表 sheet 名形如 `<文件名>!p<页>_t<表号>`，Word 表形如 `<文件名>!t<表号>`，与 xlsx/csv 走同一套检测器。**不做 OCR，不从图表像素读数。**

## 作为 Python 库

```python
from paperconan import audit_dir

scan = audit_dir(
    "path/to/source_data_dir",
    "/tmp/audit-this-paper",
    write_html=False,   # 不生成 HTML
    write_json=False,   # 只拿返回 dict，不落盘
    evidence=False,     # 跳过 evidence blob，适合批处理只要 metadata
    include_runtime=True,  # 可选：记录时间戳和 scan/file/sheet 耗时
    # profile="forensic",
)
```

`write_html=True` 需要 evidence，会强制打开。CLI 入口是 `paperconan._audit:main`，库入口推荐 `paperconan.audit_dir()`。
默认不记录运行时元数据；直接调用 `scan_dir()` 时也可使用同名
`include_runtime=True` 参数。已有时间戳或耗时值的归档扫描仍可正常渲染。

判定后报告也能直接从库里渲染（等价于 `paperconan report` 子命令）：

```python
from paperconan import write_adjudicated_report

write_adjudicated_report(scan, verdict, "adjudication.html")  # scan/verdict 均为 dict
```

## 内存 / 输出保护

当前引擎用 columnar `Sheet` 底座（数值 dense array、文本稀疏）。为避免大文件撑爆机器或 `scan.json`，超限对象会被**记录并跳过**（不当作"干净"）：

| 环境变量 | 默认值 | 作用 |
|----------|--------|------|
| `PAPERCONAN_MAX_FILE_MB` | `200` | 单文件读取前体积上限 |
| `PAPERCONAN_MAX_CELLS` | `10000000` | 单 sheet / workbook 累计 cell 预算 |
| `PAPERCONAN_MAX_SPARSE_CELLS` | `250000` | 单 sheet 保留的文本、日期/对象及超宽整数稀疏 cell 数上限；超限 sheet 会跳过并记录实际观测值 |
| `PAPERCONAN_MAX_SPARSE_BYTES` | `67108864` | 单 sheet 稀疏 payload 字节预算；超限 sheet 会跳过并记录实际观测值 |
| `PAPERCONAN_COLUMN_FINGERPRINT_MAX_COLUMNS` | `512` | 跨 sheet 列指纹按物理列顺序最多处理的列数；超出部分会记录精确覆盖限制 |
| `PAPERCONAN_MAX_BLOCK_COLS` | `120` | 宽 block 跳过 O(col²) 关系 / equal-pair 检测 |
| `PAPERCONAN_MAX_REPORT_BLOCKS` | `2000` | 最多收集多少个带 finding 的 block |
| `PAPERCONAN_MAX_FINDINGS_PER_BLOCK` | `150` | 单 block 最多保留多少条 finding（密集/高相关 block 的 O(col²) 成对信号会成千上万，取 severity 最高的 N 条，其余记入 `findings_omitted`）；`0` 关闭 |
| `PAPERCONAN_MAX_TOTAL_FINDINGS` | `5000` | 全部 block 合计 finding 上限（防病态语料把 `scan.json` / `report.html` 撑到 GB 级）；`0` 关闭 |
| `PAPERCONAN_MAX_EVIDENCE_ROWS` | `50` | 单条 evidence 片段最多行数 |
| `PAPERCONAN_MAX_EVIDENCE_COLS` | `30` | 单条 evidence 片段最多列数 |
| `PAPERCONAN_RECURRING_ROW_VECTOR_BUDGET` | `3000000` | recurring-row detector 的全局窗口工作预算；耗尽时记录精确跳过窗口数 |
| `PAPERCONAN_RECURRING_ROW_VECTOR_UNIQUE_BUDGET` | `100000` | recurring-row detector 全局保留的唯一向量数；已知向量仍继续更新，新向量遗漏以明确下界记录 |
| `PAPERCONAN_RECURRING_ROW_VECTOR_FINALIZATION_CANDIDATE_BUDGET` | `10000` | recurring-row finalization 最多保留的候选向量数；超限时记录候选遗漏数与 finding 遗漏下界 |
| `PAPERCONAN_RECURRING_ROW_VECTOR_FINALIZATION_PAIR_BUDGET` | `200000` | recurring-row finalization 最多执行的 indexed overlap 候选比较数 |
| `PAPERCONAN_RECURRING_ROW_VECTOR_FINALIZATION_CELL_BUDGET` | `1000000` | recurring-row finalization 最多保留的候选 cell 引用数 |
| `PAPERCONAN_FRACTION_REUSE_PAIR_BUDGET` | `10000` | 同一 sheet 内 fraction-reuse detector 最多检查的 block pair 数 |
| `PAPERCONAN_FRACTION_REUSE_CELL_BUDGET` | `1000000` | 同一 sheet 内 fraction-reuse detector 最多检查的位置 cell 数；与 pair 预算分别生效 |
| `PAPERCONAN_MAX_PAPER_MB` | `1500` | `fetch` 下载/解压到一个 paper 目录的总量上限 |
| `PAPERCONAN_ARCHIVE_MEMBER_LIMIT` | `10000` | 单个 ZIP/TAR 最多检查的 member 元数据数（包括非表格 member）；超限记录遗漏下界 |
| `PAPERCONAN_ARCHIVE_MEMBER_NAME_BYTES` | `8388608` | 单个 ZIP/TAR 已检查 member 名称的累计 UTF-8 字节预算 |
| `PAPERCONAN_ARCHIVE_METADATA_BYTES` | `8388608` | 单个 TAR 的 PAX、GNU long-name / long-link 等扩展元数据累计字节预算；在读取或解码超限 payload 前停止 |
| `PAPERCONAN_ARCHIVE_SPARSE_ENTRY_LIMIT` | `100000` | 单个 TAR 最多保留的 GNU sparse tuple 数；legacy、PAX 0.0/0.1/1.0 共用此累计预算 |
| `PAPERCONAN_ARCHIVE_TAR_TRAVERSAL_BYTES` | `1073741824` | 单个 TAR 最多遍历的解压后字节数，包括 header、padding、跳过的 member data、扩展元数据及 sparse field block；已知前向距离在 gzip seek/read 前检查 |
| `PAPERCONAN_ARCHIVE_OUTPUT_FILE_LIMIT` | `5000` | 单个 ZIP/TAR 最多写出的可扫描文件数；每个被此预算跳过的已保留 member 都会列入 `skipped` |
| `PAPERCONAN_SOURCE_SIDECAR_MAX_BYTES` | `2097152` | `paperconan_source.json` 的读取及增量编码字节上限；读取最多到 `limit + 1`，超限时保留原 sidecar 与既有 managed outputs |
| `PAPERCONAN_SOURCE_SIDECAR_ENTRY_LIMIT` | `10000` | provenance sidecar 允许检查及新写入的 managed-name 条目上限 |
| `PAPERCONAN_SOURCE_SIDECAR_NAME_BYTES` | `1048576` | provenance sidecar 保留的唯一 managed-name 累计 UTF-8 字节预算 |
| `PAPERCONAN_MANAGED_OUTPUT_NAME_BYTES` | `4096` | 单个 requested/source/base/candidate 输出名称的 UTF-8 字节上限；在 hash、路径 probe 或候选名称分配前检查 |
| `PAPERCONAN_MANAGED_OUTPUT_COLLISION_PROBE_LIMIT` | `128` | 单个 direct/archive 输出名称最多执行的 filesystem collision probe 数；包括 digest 与 numeric fallback |
