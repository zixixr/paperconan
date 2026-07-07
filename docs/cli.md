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
python -m paperconan path/to/dir/                   # 等价 module 形式
```

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
    # profile="forensic",
)
```

`write_html=True` 需要 evidence，会强制打开。CLI 入口是 `paperconan._audit:main`，库入口推荐 `paperconan.audit_dir()`。

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
| `PAPERCONAN_MAX_BLOCK_COLS` | `120` | 宽 block 跳过 O(col²) 关系 / equal-pair 检测 |
| `PAPERCONAN_MAX_REPORT_BLOCKS` | `2000` | 最多收集多少个带 finding 的 block |
| `PAPERCONAN_MAX_FINDINGS_PER_BLOCK` | `150` | 单 block 最多保留多少条 finding（密集/高相关 block 的 O(col²) 成对信号会成千上万，取 severity 最高的 N 条，其余记入 `findings_omitted`）；`0` 关闭 |
| `PAPERCONAN_MAX_TOTAL_FINDINGS` | `5000` | 全部 block 合计 finding 上限（防病态语料把 `scan.json` / `report.html` 撑到 GB 级）；`0` 关闭 |
| `PAPERCONAN_MAX_EVIDENCE_ROWS` | `50` | 单条 evidence 片段最多行数 |
| `PAPERCONAN_MAX_EVIDENCE_COLS` | `30` | 单条 evidence 片段最多列数 |
| `PAPERCONAN_MAX_PAPER_MB` | `1500` | `fetch` 下载/解压到一个 paper 目录的总量上限 |
