# PDF restoration rules

## Noise and footnotes

Identify running furniture with page position, typography, repetition, alternating page patterns and changing folios. Preserve numbered clauses and repeated substantive text. Record deletions and changed reference marks with source IDs. Remove a watermark only when its text, orientation, recurrence or visible overlay supports that classification. Rotated notes and short variables are review candidates rather than blanket deletions. The automatic watermark filter handles explicit conventional rotated labels; other fragments need targeted repair.

Footnotes may be continued, repeated or misrecognized. Small font alone does not prove a note. Match note bodies to superscript reference candidates and keep mathematical exponents, subscripts, body citations and legal numbers. The code scopes labels by page and protects simple math contexts; inspect ambiguous or continued notes. Retain ordinary bibliography entries and unwrap each entry internally.

## Flow and coverage

Read columns as coherent regions with full-width elements at their logical boundaries. Preserve paragraph starts established by indentation, numbering, style, spacing and context. Rejoin continuation text within a page, across columns/pages and across processing batches. Remove pagination interruptions before deciding joins. A sentence can end while the paragraph continues; a short final line followed by a new first-line indent supports a real paragraph break.

Preserve the exact supplied excerpt. Detect missing/empty text pages, duplicated spans, lost lines, unexplained source/output changes and suspicious paragraph lengths. Compare retained source units with mapped output, accounting separately for declared deletions and table/LaTeX serialization. Trace candidates to source pages/coordinates. Matching page counts or high character similarity are coverage signals, not proof of correctness.

## Words, headings and citations

Normalize Unicode ligatures and soft hyphens when they are extraction artifacts. Use word evidence as a hint for hyphenation, including protected single-letter prefixes, numeric ranges and chemical names. Compound-word ambiguity must be checked against context. Do not apply document-specific spelling replacements globally.

Keep heading text and numbering as printed, and infer parent/child structure from the whole document. Treat fragmentary documents and multiple works according to the supplied structure rather than inventing a single paper title. Preserve paragraph labels without renderer renumbering. Escape literal brackets, omission asterisks and table pipes only where Markdown would change their meaning. Keep code/algorithm blocks as code, not prose-reflow candidates.

## Tables

Preserve cell values, units, row/column alignment and intentional blanks. Use text/table layout to distinguish a table from adjacent columns of prose. Validate GFM row widths and compare numeric tokens with the source. Preserve genuine merged cells in HTML. Keep printed emphasis only when supported by the source. Adjacent tables with the same header still need continuation evidence before merging. Repeated cross-page headers are recorded as declared structural deletions. An uncertain structure stays flagged instead of being replaced with invented cells.

## Math

Use inline `$...$` or display `$$...$$`, preserving original equation numbers (for example `\\tag{n}` where supported). Check variables, case, Greek letters, scalars versus bold vectors, scripts, signs, fractions, relation symbols, bracket nesting and multi-line alignment. Simple text-layer scripts are reconstructed from glyph geometry. Complex fractions/radicals and unknown glyphs remain explicit recovery findings. A syntactically valid LaTeX expression may still have the wrong symbol; review uncertain expressions against source regions. Do not interpret formula glyphs as footnote markers.

## Verification scope

Automatic analysis covers the full document and every page transition. Source-image inspection covers actual suspicious regions and representative samples. Do not claim page-by-page proofreading for this workflow. Exclude figure/image extraction and image links. Preserve text-layer captions and substantive references without interpreting missing figures. Report text that cannot be recovered from a usable source layer and any unresolved parsing limitation.
