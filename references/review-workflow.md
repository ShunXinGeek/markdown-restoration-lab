# Local review and timing

## Prepare evidence once

In PowerShell 7, run `scripts/prepare_review.ps1 -ReviewDir DIR -MarkdownPath FILE.md -Screenshot` after conversion. It uses the existing Python environment, PowerShell's `ConvertFrom-Markdown` and a local Chrome/Edge executable. No Node packages, downloads, hosted preview or OCR installation is needed.

- `review-context.json` includes source accounting, engines, every finding with adjacent source lines, deletions and all page transitions.
- `source-pN.png` and `contact-N.png` cover the selected physical pages. By default the helper renders first/last/representative feature pages followed by finding pages, capped at 12 pages. `unrendered_finding_pages` explicitly lists remaining pages; inspect them with another call using `-Pages '7,12,21'`. Increase `-MaxPages` when appropriate. Rendering a page is not proof it was inspected.
- `review-summary.html` presents the source evidence; `markdown-preview.html` renders the full current Markdown; `markdown-sample.html` and `markdown-preview.png` show the beginning/end and representative structure. A very long sample can extend below the screenshot: inspect the full HTML or another portion when needed.
- `review.template.json` contains current byte hashes and empty evidence fields. No finding is resolved automatically; sample pages and targeted locations stay empty until actual review. `review.json` is never overwritten by the helper.

In this machine's restricted execution environment, sandboxed Chrome can fail to start its GPU process. The tested local-only fallback is:

```powershell
& <skill-directory>\scripts\prepare_review.ps1 -ReviewDir DIR -MarkdownPath FILE.md -Screenshot -BrowserNoSandbox
```

`-BrowserNoSandbox` applies only to this isolated preview process. Its temporary profile stays under the review directory; JavaScript and background networking are disabled, source HTML is treated as data except structural tables, and a content security policy blocks remote resources. It changes no browser settings. Browser execution is limited to 15 seconds; a failure leaves the HTML and source evidence available rather than marking the review complete. Omit `-Screenshot` for HTML/source evidence only. `-NoSourceImages` refreshes the summary/template without rendering source pages.

## Inspect deletions as well as retained text

Review every `footnote_deletion` page against source images. `note_candidate` and `note_continuation_candidate` mean ambiguous small bottom text was retained for review; do not delete it solely because it is small or numbered. Check numbered legal requirements, quotations, mathematical scripts and unlabelled note continuations explicitly. Use document context to resolve uncertain numbering and paragraph boundaries; never use file-specific replacements as universal rules.

The audit hard-fails unaccounted raw lines, overlapping keep/delete assignments, duplicate deletion assignments, missing/duplicate retained-line mappings and unknown source IDs. Text alignment, edits, fragmented prose and all removed footnotes still require evidence. These checks support review; they cannot determine semantic fidelity on their own.

## Hashes and final audit

Converters write UTF-8 with LF newlines. `audit.py` and the review helper read the actual Markdown bytes for SHA-256, then normalize line endings only for structural checks. A legitimately edited CRLF file can therefore be reviewed without rewriting it. After edits, refresh the current hashes with the helper and transfer only still-valid review evidence. Rerun `scripts/audit.py DIR FILE.md` and inspect its JSON status; exit zero also includes `review_required`.

## Measure without weakening checks

`timings.json` separates extraction, OCR, note/furniture classification, layout engine, reconstruction, audit, Markdown writing and total CLI program time. `prepare-timings.json` reports review preparation time; tool/session timestamps are needed to measure the full task including inspection, corrections, screenshots, approval waits and delivery. Keep these measurements separate.

For before/after comparisons, preserve old code, original audit/source maps, the corrected Markdown and their hashes. Run each version into a fresh review directory with the same engine/OCR settings, only when the user has authorized the benchmark. Compare text-line inventories and deletion decisions against the source, not only a previously corrected output or the count of audit findings. Synthetic regressions are useful during development; they do not establish quality or speed on a user's document.
