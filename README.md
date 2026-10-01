# article-fact-check

**Fact-check articles claim by claim, with every verdict traced back to a source.**

[![CI](https://github.com/petar-nauka/article-fact-check/actions/workflows/ci.yml/badge.svg)](https://github.com/petar-nauka/article-fact-check/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%E2%80%933.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/AI%20agent-skill-purple.svg)](SKILL.md)
[![No dependencies](https://img.shields.io/badge/dependencies-standard%20library%20only-green.svg)](#how-it-works)

`article-fact-check` is an AI agent skill plus a small Python CLI for auditing the facts in an article, a report, or an AI-written draft. The agent breaks the text into atomic claims, opens the real sources, and records a verdict with the exact locator for each claim. The CLI freezes the text, validates the audit ledger, and renders reports an editor can act on.

It works with English and Bulgarian articles and accepts Markdown, DOCX, PDF, HTML, plain text, and public URLs.

> [!IMPORTANT]
> The CLI does **not** fact-check by itself and does not find sources on its own. The research is done by the agent (or a human researcher) with the tools it has. The CLI makes that research structured, checkable, and hard to fake: unchecked claims stay visibly unchecked.

---

## Why

AI-written and fast-produced articles often get the big picture right and the details wrong: a number from a forecast presented as today's figure, planned capacity reported as operational, a budget off by an order of magnitude. This skill makes the agent check every claim, show its evidence, and say clearly what it could not verify, instead of returning a vague "looks accurate".

## How it works

1. **Freeze** the article (`init`): the text is saved with a SHA-256 fingerprint and split into numbered paragraphs (`P001`, `P002`, ...).
2. **Extract claims**: every checkable statement becomes an atomic claim (`C0001`, ...) linked to its paragraph. Every paragraph must have claims or a justified exclusion (heading, opinion, and so on).
3. **Research**: the agent opens primary sources, records searches and sources, and links evidence to each claim with a precise locator (page, table, article of law).
4. **Judge**: each claim gets one verdict, such as `SUPPORTED`, `PARTLY_SUPPORTED`, `CONTRADICTED`, `OUTDATED`, or one of the `UNRESOLVED_*` states. Not finding evidence never counts as `CONTRADICTED`.
5. **Validate and render**: the CLI checks the structure and the links, then produces the reports.

The core engine uses only the Python 3.9+ standard library. `pypdf` is an optional extra for PDF text extraction.

## Outputs

| File | What it is |
|---|---|
| `report.html` | Standalone interactive dashboard with verdict filters |
| `annotated-article.md` | The article with editorial correction notes where facts are wrong |
| `fact-check-report.md` | Formal report for editors and authors |
| `claims.csv`, `sources.csv`, `searches.csv` | Registers ready for Excel or a database |

See a complete (illustrative) example in [`examples/sample_audit/`](examples/sample_audit/). Its verdicts are demonstration data and have not been independently verified.

---

## Installation

### As an agent skill

Keep the whole folder together: `SKILL.md`, `scripts/`, `src/`, and `references/`.

**Claude Code**

```bash
git clone https://github.com/petar-nauka/article-fact-check.git ~/.claude/skills/article-fact-check
```

Then ask Claude to fact-check an article, or call the skill by name.

**Claude apps (desktop, web)**: download the repository as a ZIP and upload it in the Skills section of Claude's settings.

**Codex**

```bash
git clone https://github.com/petar-nauka/article-fact-check.git "${CODEX_HOME:-$HOME/.codex}/skills/article-fact-check"
```

Then invoke `$article-fact-check`. `agents/openai.yaml` provides optional Codex UI metadata.

The skill does not grant any extra network or file access. The agent uses whatever tools and permissions its environment already has.

### As a standalone CLI

```bash
git clone https://github.com/petar-nauka/article-fact-check.git
cd article-fact-check
pip install -e .          # or: pip install -e ".[pdf]" for PDF support
article-fact-check --help
```

You can also run it without installing:

```bash
python scripts/audit_tool.py --help
```

---

## CLI commands

```bash
# 1. Freeze an article and create audit.json
article-fact-check init "https://example.com/article" --out audit-01 --language en
article-fact-check init "path/to/article.docx" --out audit-01 --language bg

# 2. Show likely claim markers (numbers, dates, quotes, superlatives) as a helper
article-fact-check extract-markers audit-01/audit.json

# 3. Record claims, sources, and searches (or edit audit.json directly)
article-fact-check add-claim --help
article-fact-check add-source --help
article-fact-check add-search --help
article-fact-check update-claim --help

# 4. Check progress
article-fact-check status audit-01/audit.json

# 5. Merge batches researched by separate agents (only when parallel agents are allowed)
article-fact-check merge-batch audit-01/audit.json --batch audit-01/research/batch_01.json

# 6. Validate structure, links, and evidence requirements
article-fact-check validate audit-01/audit.json

# 7. Render all reports
article-fact-check render audit-01/audit.json --out-dir audit-01/output
```

For local text in a legacy encoding, add `--encoding cp1251` or `--encoding cp1252` to `init`. Scanned PDFs need OCR first.

---

## Weighted audit index

For a **completed** audit the CLI computes a rule-based 0-100% index. It summarises the recorded verdicts under fixed weights. It is **not** a probability that the article is true. Partial audits show `N/A`.

$$\text{Index} = 100 \times \left( 1 - \frac{\sum_{c} w(\text{risk}_c) \times p(\text{verdict}_c)}{\sum_{c} w(\text{risk}_c)} \right)$$

| Risk | Weight $w$ | | Verdict | Penalty $p$ |
|---|---:|---|---|---:|
| `critical` | 4.0 | | `CONTRADICTED` | 1.0 |
| `high` | 2.0 | | `PARTLY_SUPPORTED` | 0.5 |
| `medium` | 1.0 | | `OUTDATED`, `UNRESOLVED_CONFLICTING` | 0.4 |
| `low` | 0.5 | | `UNRESOLVED_NO_EVIDENCE` | 0.3 |
| | | | `UNRESOLVED_ACCESS_LIMITED` | 0.2 |
| | | | `SUPPORTED`, `NOT_CHECKABLE` | 0.0 |

---

## Repository structure

```
article-fact-check/
├── SKILL.md                       # Instructions for the AI agent
├── references/
│   ├── claim-protocol.md          # How to split text into atomic claims
│   ├── evidence-protocol.md       # Verdicts, source tiers, research routing
│   ├── data-and-output-contract.md # audit.json v2.0 schema and outputs
│   └── subagent-workflow.md       # Optional parallel batch procedure
├── scripts/audit_tool.py          # Run the CLI from a checkout
├── src/article_fact_check/        # Installable Python package
├── tests/                         # Unit tests
├── examples/                      # Sample article and a rendered sample audit
├── agents/openai.yaml             # Optional Codex UI metadata
├── .github/workflows/ci.yml       # Tests on Python 3.9-3.13
├── pyproject.toml
├── CONTRIBUTING.md
└── LICENSE                        # MIT
```

## Testing

```bash
python -m unittest discover -s tests -v
# or
pytest tests/ -v
```

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Created by [Petar Teodosiev](https://github.com/petar-nauka).

---

## На български

**article-fact-check** е умение за ИИ агенти (Claude, Codex и други) и малък Python инструмент за проверка на фактите в статии, доклади и текстове, написани с ИИ. Агентът разделя текста на отделни проверими твърдения, отваря реалните източници и за всяко твърдение записва присъда с точно посочено място в източника. Инструментът пази проверката подредена и проверима и създава интерактивен отчет, анотирана статия и доклад за редактора. Работи със статии на български и английски език.
