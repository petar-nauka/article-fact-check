---
name: article-fact-check
description: Fact-checks the factual claims in a supplied article or report (English or Bulgarian), traces the evidence back to sources, and produces a verifiable audit with reports. Use when asked for a thorough check of a text; the tool does not find or judge sources on its own.
---

# Article fact-check

This skill guides the research and uses the local CLI to freeze the text, validate the data, and generate reports. The CLI does not perform automatic fact-checking. Never promise 100% coverage or accuracy: note inaccessible passages, unverified claims, and source limitations.

## Workflow

1. Accept a local `.txt`, `.md`, `.html`, `.docx`, `.pdf` file or a public URL. For a PDF, check the extracted text; if it is scanned or only partly readable, run OCR with an available tool or ask for a readable file. Choose an audit folder in a writable location. Never overwrite an existing audit.
2. Run `python "<skill-root>/scripts/audit_tool.py" init "<file-or-url>" --out "<audit-dir>" --language en` (or `bg`). For local text in a legacy encoding, add `--encoding cp1251` or the actual encoding. Read `frozen-article.*`, `numbered-article.md`, and `audit.json`. Compare the frozen text with the original, especially tables, footnotes, quotes, and missing sections. If the input is incomplete, record the problem in `limitations`/`unchecked_items` and do not mark the audit as complete.
3. Extract the checkable atomic claims from **every** passage. Keep the exact `original_text` as a substring of the passage and link `paragraphs[].claim_ids` and `claims[].paragraph_ids` in both directions. The `extract-markers` scan is only a helper and can miss claims. Apply the [claim protocol](references/claim-protocol.md).
4. Check every fact with the available web and file tools. Open the sources themselves: the exact page, table, or record. A search result is only a lead. Document `sources`, `searches`, `evidence_links`, the access date, and limitations. By default a new source is `not_inspected` and a new search is `not_run`; change these values only after a real review. Compare numbers by unit, denominator, period, scope, and method. For facts that change over time, establish the date the article refers to. Follow the [evidence protocol](references/evidence-protocol.md).
5. Write `audit.json` according to the [schema](references/data-and-output-contract.md). You can edit the file directly or use the CLI commands `add-claim`, `add-source`, `add-search`, `update-claim`; choose whichever suits the size of the task. Never fill in fields for research done or evidence found by guesswork. Every unchecked item stays `NOT_RESEARCHED`; a lack of sufficient evidence is `UNRESOLVED_NO_EVIDENCE`, not `CONTRADICTED`.
6. Do a second pass for missed claims and for sources supplied by the author. Set `decomposition_complete`, `omission_pass_complete`, `supplied_sources_audited`, and `status: complete` **only** when this has really been done and no `NOT_RESEARCHED` remains. Otherwise leave `status: partial` and describe the remaining work.
7. Run `python "<skill-root>/scripts/audit_tool.py" validate "<audit-dir>/audit.json"`, fix the errors, then run `python "<skill-root>/scripts/audit_tool.py" render "<audit-dir>/audit.json" --out-dir "<audit-dir>/output"`. Check that the output files match the evidence. Validation checks structure and some logical links; it does not prove that the recorded findings are true.

## Verdicts and presentation

Use only the verdicts from the [evidence protocol](references/evidence-protocol.md). `SUPPORTED` requires directly inspected supporting data; `CONTRADICTED` requires a directly inspected refutation. Cite the exact locator and explain how the evidence relates to the claim. Give the minimal safe edit when there is a factual mismatch.

At the end, state the number of checked and unchecked claims, the most important corrections, the specific limitations, and links to `report.html`, `fact-check-report.md`, and `annotated-article.md`. The weighted audit index is a rule-based score with fixed weights, **not** the probability that the text is true. For a partial audit or one with unresearched claims, never present a numeric index as a final score.

Use the [batch procedure](references/subagent-workflow.md) only if the user or the active instructions explicitly allow parallel agents; otherwise work sequentially. Follow the tools and permissions available in the environment. The skill itself grants no extra access to files, websites, or external services.
