# Claim extraction and inventory protocol

## 1. Atomicity and splitting claims
Create one claim for one subject and one main checkable predicate. Split compound sentences when their parts can receive different verdicts (for example, the number is right but the scope or the year is wrong).
Keep negations, modality, the direction of cause and effect, and every limiting qualifier.

**Example:**
> "In 2024 the technology cuts production costs by 40% and is already in widespread use in the EU."

**Split into:**
- `C0001`: In 2024 the technology cuts costs by 40% (includes the baseline and the method).
- `C0002`: The technology is already in widespread use in the European Union (includes a definition of "widespread" and the territorial scope).

---

## 2. What counts as a claim
Extract explicit and materially implied claims that contain:
- **Quantitative data**: numbers, percentages, currencies, ranges, units, ratios, rankings.
- **Time and place**: years, dates, time periods, geographic locations, chronology.
- **Scientific and technical facts**: effects, mechanisms, consensus, efficacy, risks, technical specifications.
- **Legal and regulatory status**: legislation, directives, standards, certifications, funding, licences.
- **Quotes and attribution**: direct quotes, paraphrases, statements by officials, and bibliographic metadata.
- **Market and technology status**: deployment, commercial availability, real operation versus project or prototype.
- **Superlatives and absolute claims**: "the first", "the only", "the largest", "the fastest", "never", "all", "none".

---

## 3. Using automatic markers (`extract-markers`)
Before decomposing by hand, run:
```bash
python "<skill-root>/scripts/audit_tool.py" extract-markers AUDIT-DIR/audit.json
```
This scans the paragraphs for likely markers. It does not find every checkable fact and does not replace a manual review of each passage.

---

## 4. Recording a claim in `audit.json`
- `original_text`: the exact quote of the passage from the article.
- `normalized_claim`: a conservative, clear wording of the claim, suitable for searching online.
- `paragraph_ids`: every paragraph in which this claim appears.
- `claim_types`: one or more of `factual`, `quantitative`, `causal`, `comparative`, `superlative`, `current_status`, `scientific`, `modeled`, `legal`, `historical`, `quotation`, `bibliographic`, `implied`.
- `risk`:
  - `low`: peripheral, stable metadata;
  - `medium`: a significant supporting fact;
  - `high`: numerical data, causes, legislation, scientific conclusions, current status;
  - `critical`: a claim whose inaccuracy would cause legal, health, financial, or serious reputational harm.

---

## 5. Justified paragraph exclusions
Every paragraph of the text must either have linked claim IDs or one of these justified exclusions:
- `heading_or_navigation`
- `pure_opinion_or_value_judgment`
- `recommendation`
- `unfalsifiable_rhetoric`
- `duplicate_container`
- `bibliography_container`
- `formatting_only`
- `unreadable_or_missing`
- `out_of_scope`
