# Markdown Restoration Lab

将 PDF（含扫描页和混合页面）、Office、HTML 与 OCR 文本还原为可编辑的 Markdown，并生成来源映射、删除记录和全文审计。详见 [SKILL.md](SKILL.md)。

## 安装

将此仓库克隆到 Codex 的 Skills 目录，并将目录命名为 `markdown-restoration-lab`，使入口位于 `~/.codex/skills/markdown-restoration-lab/SKILL.md`。随后可在 Codex 中调用 `$markdown-restoration-lab`。

## 运行环境

当前版本的 Windows 包装脚本使用本机预装的 `D:\CodexTools\markitdown\.venv` 和 `D:\CodexTools\pdf-layout\.venv`，Marker 模型与字体缓存在 `D:\CodexTools\pdf-layout\cache`。在其他电脑上使用前，需要安装对应依赖，并调整 `scripts/convert.ps1`、`scripts/marker_adapter.py` 和 `SKILL.md` 中的环境路径。扫描页 OCR 使用 Windows 本地语言包。

## 用法

```powershell
& .\scripts\convert.ps1 -InputPath source.pdf -OutputPath output.md -ReviewDir work-review
```

法律文档可加 `-LegalNumbering`。输出审计文件位于指定的复核目录；请按 [SKILL.md](SKILL.md) 的步骤检查发现项并复核结果。转换与复核规则见 [references/conversion-rules.md](references/conversion-rules.md) 和 [references/restoration-modes.md](references/restoration-modes.md)。
