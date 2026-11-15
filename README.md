# restore-document-markdown

一个将 Word 文档、文本型或扫描型 PDF，以及 OCR 文本还原为 Markdown 的 Codex Skill。它清理脚注、页底数字行和疑似 OCR 乱码，保留法律段落编号、列表、表格及代码。长文写入完整的 UTF-8 `.md` 文件。

## 安装

将仓库克隆到 Codex 的 Skills 目录，使 `SKILL.md` 位于 `~/.codex/skills/restore-document-markdown/SKILL.md`。随后可在 Codex 中使用 `$restore-document-markdown`。

文件提取使用本地 Python 和 Node.js。PDF 需要 PyMuPDF。DOCX 转换和扫描页 OCR 需要本地安装的 `markdown-converter` Skill。扫描页还需要本地 `tesseract.js` 和对应语言数据。可通过 `--converter-dir`、`--node-modules-dir` 和 `--node` 指定安装位置。旧版 `.doc` 需先在本地另存为 `.docx`。

## 命令行

```powershell
python scripts/restore.py "source.pdf" --legal-numbering --ocr-language eng+chi_sim -o "restored.md"
python scripts/restore.py "source.docx" -o "restored.md"
python scripts/restore.py "ocr.txt" --chat
```

`--chat` 用于短文本，输出包裹在 Markdown 代码围栏中。文件模式直接写入 Markdown 源码。输出路径须是尚未存在的 `.md` 文件，原文件不会被修改。

脚本在标准错误输出中报告处理状态和 PDF 的逐页状态。不可读页会列出原始页号，并将已恢复内容写入 `.partial.md`。对重要法律编号、跨页段落和复杂表格，按 [复核指南](references/review.md) 对照原件检查。
