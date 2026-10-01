# Data and output contract

## 1. `audit.json` schema (version 2.0)

`audit.json` is the central store of the audit state:

```json
{
  "schema_version": "2.0",
  "metadata": {
    "title": "Article title",
    "source_path": "frozen-article.txt",
    "original_source_path": "https://example.com/article or path/to/file.docx",
    "document_sha256": "4f53...",
    "original_document_sha256": "hash of the original local file, if there is one",
    "language": "en",
    "article_reference_date": "2024-01-01",
    "analysis_date": "2026-08-14",
    "scope": "exhaustive",
    "status": "partial|complete",
    "decomposition_complete": false,
    "omission_pass_complete": false,
    "supplied_sources_audited": false
  },
  "paragraphs": [
    {
      "id": "P001",
      "type": "paragraph|heading|table_or_structured_block",
      "locator": "block:1",
      "text": "Paragraph text...",
      "claim_ids": ["C0001", "C0002"],
      "exclusion": ""
    }
  ],
  "claims": [
    {
      "id": "C0001",
      "paragraph_ids": ["P001"],
      "original_text": "cuts costs by 40%",
      "normalized_claim": "The technology cuts production costs by 40% compared with the conventional method",
      "claim_types": ["quantitative", "factual"],
      "risk": "high",
      "verdict": "PARTLY_SUPPORTED",
      "confidence": "high",
      "research_complete": true,
      "rationale": "The agency's official report shows a 28% reduction, not 40%.",
      "evidence_links": [
        {
          "source_id": "S001",
          "stance": "supports",
          "locator": "Table 4, p. 18",
          "finding": "The actual measured reduction is 28% under standard conditions."
        }
      ],
      "search_ids": ["Q001"],
      "recommended_edit": "Replace \"40%\" with \"28%\" in line with the report data.",
      "editor_comment": "The value is 12 percentage points higher than the official source.",
      "notes": ""
    }
  ],
  "sources": [
    {
      "id": "S001",
      "title": "Annual Energy Efficiency Report 2024",
      "url": "https://agency.example.org/report2024.pdf",
      "source_uri": "https://agency.example.org/report2024.pdf",
      "author_or_org": "European Energy Agency",
      "publication_date": "2024-05-12",
      "access_date": "2026-08-14",
      "source_type": "official|dataset|paper|review|law|report|journalism|company|archive|other",
      "source_tier": "primary|strong_synthesis|reputable_secondary|limited|discovery_only",
      "access_status": "not_inspected|full_text|official_record|abstract_only|snippet_only|paywalled|blocked|captcha|dead_link|not_found",
      "origin_cluster": "EA-Energy-2024",
      "supplied_by_author": false,
      "contribution": "Provides exact technical and economic measurements.",
      "limitations": ""
    }
  ],
  "searches": [
    {
      "id": "Q001",
      "claim_ids": ["C0001"],
      "query": "\"Energy Agency\" 2024 cost reduction hydrogen technology table",
      "kind": "primary",
      "language": "en",
      "run_date": "2026-08-14",
      "surface": "web",
      "status": "not_run|completed|cached|zero_results|blocked|paywalled|captcha|error|not_found",
      "result_note": "Found the full PDF report with tables."
    }
  ],
  "limitations": [],
  "unchecked_items": []
}
```

---

## 2. Subagent batch schema (`batch_XX.json`)
Subagents return a lightweight JSON file with their cluster of data:
```json
{
  "claims": [ ... ],
  "sources": [ ... ],
  "searches": [ ... ]
}
```
The file is merged into the main `audit.json` with:
```bash
python audit_tool.py merge-batch AUDIT-DIR/audit.json --batch AUDIT-DIR/research/batch_01.json
```

---

## 3. Output artifacts from `render`

1. **`report.html`**:
   - A standalone, interactive dashboard;
   - A colour-coded view of the article with annotations;
   - The weighted audit index only for a completed audit, otherwise `N/A`;
   - Interactive filters by verdict and risk.
2. **`annotated-article.md`**:
   - The full original text with inline quotes and editorial notes after the paragraphs, only where factual mismatches were found (`CONTRADICTED`, `PARTLY_SUPPORTED`, `OUTDATED`, `UNRESOLVED_CONFLICTING`).
3. **`fact-check-report.md`**:
   - A summary structured Markdown report for authors and editors.
4. **`claims.csv`**, **`sources.csv`**, **`searches.csv`**:
   - Tabular registers for export to Excel or databases;
   - Every row carries `audit_status` and `demo_data`, so a partial or demonstration audit is never presented as final once it is separated from the report.
