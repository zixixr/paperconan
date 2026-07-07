# paperconan 文档

面向读者的详细文档。主 [README](../README.md) 只放主干；细节在这里。

- [它能找出什么](detectors.md) —— 全部检测器概览表
- [报告与调参](reports.md) —— 报告怎么读、`--profile` 误报控制、判定后 HTML 报告
- [批量扫描推荐工作流](batch-workflow.md) —— fetch → scan → filter → 立卷 → agent 判定 → 分级 → 对抗
- [命令行与库参考](cli.md) —— 安装、扫描、fetch、PDF/Word、Python 库、内存/输出保护
- [FAQ](faq.md)

面向 **AI agent / skill** 的深入规则见 [`skills/paperconan/references/`](../skills/paperconan/references/)：
[detectors](../skills/paperconan/references/detectors.md) ·
[output-schema](../skills/paperconan/references/output-schema.md) ·
[judgment-rubric](../skills/paperconan/references/judgment-rubric.md) ·
[interpretation](../skills/paperconan/references/interpretation.md) ·
[adjudication-tiers](../skills/paperconan/references/adjudication-tiers.md) ·
[report-templates](../skills/paperconan/references/report-templates.md) ·
[adversarial-review](../skills/paperconan/references/adversarial-review.md) ·
[batch-workflow](../skills/paperconan/references/batch-workflow.md) ·
[case-patterns](../skills/paperconan/references/case-patterns.md)
