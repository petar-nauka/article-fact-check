# Evidence and online research protocol

Use the research tools and permissions that are actually available. This protocol does not grant any additional access to websites or files.

---

## 1. Verdicts

Every researched claim receives exactly one of these verdicts:

| Verdict | Meaning |
|---|---|
| `SUPPORTED` | Reliable direct evidence confirms the exact meaning, numbers, and qualifiers. |
| `PARTLY_SUPPORTED` | The core idea is right, but a specific number, year, scale, denominator, or scope is inaccurate or exaggerated. |
| `CONTRADICTED` | Credible direct evidence explicitly refutes the claim. |
| `UNRESOLVED_NO_EVIDENCE` | Thorough searches found neither enough evidence to confirm nor to refute the claim. |
| `UNRESOLVED_CONFLICTING` | Authoritative independent sources contain mutually exclusive data. |
| `UNRESOLVED_ACCESS_LIMITED` | The decisive source is paywalled, blocked, or unavailable. |
| `OUTDATED` | The claim was valid in the past, but the status, the law, or the data has changed. |
| `NOT_YET_VERIFIABLE` | The claim concerns a future event or a process that has not finished yet. |
| `NOT_RESEARCHED` | The claim has not been processed in the current audit yet. |
| `NOT_CHECKABLE` | The claim is too vague, subjective, or unmeasurable. |

> [!NOTE]
> Failing to find evidence **never** justifies `CONTRADICTED`. Use `UNRESOLVED_NO_EVIDENCE`.

Reports for Bulgarian-language audits (`--language bg`) show these verdicts with Bulgarian labels.

---

## 2. Source tiers and the evidence hierarchy

1. **Primary sources (`primary`)**:
   - Official registers, official gazettes, legal acts (EUR-Lex, the Bulgarian State Gazette at dv.parliament.bg).
   - Primary statistical databases (Eurostat, World Bank, OECD, national statistics offices such as Bulgaria's NSI).
   - Peer-reviewed scientific papers and their methods and supplements (DOI).
   - Official reports and company financial statements filed with regulators.
2. **Strong syntheses and reviews (`strong_synthesis`)**:
   - Systematic scientific reviews (meta-analyses, Cochrane, Nature Reviews).
   - Official reports of international institutions (EC, IPCC, IEA, WHO).
3. **Reputable secondary sources (`reputable_secondary`)**:
   - Leading news agencies and specialist publications with established editorial accountability.
4. **Discovery aids (`discovery_only`)**:
   - Wikipedia, aggregators, press releases, SEO blogs. Use them only to find the primary source, **never** as evidence.

---

## 3. Domain-specific routing

- **Bulgarian legislation and regulation**:
  - State Gazette (`dv.parliament.bg`), the Public Consultation Portal, and the websites of the relevant ministries.
- **EU directives, regulations, and policy**:
  - EUR-Lex (`eur-lex.europa.eu`), the European Commission, CORDIS.
- **Scientific publications and citations**:
  - DOI lookup, Crossref API, OpenAlex, Semantic Scholar, PubMed / NCBI, arXiv.
- **Economic and demographic statistics**:
  - Eurostat (`ec.europa.eu/eurostat`), national statistics offices (for Bulgaria: NSI at `nsi.bg`), central banks (for Bulgaria: BNB at `bnb.bg`).

---

## 4. Weighted audit index

`audit_tool.py` computes the index only for a completed audit with researched checkable claims. It is a rule-based score with fixed weights, not a probability of truth. A partial audit shows `N/A`.

$$\text{Reliability Score} = 100 \times \left( 1 - \frac{\sum_{c \in \text{Claims}} w(\text{risk}_c) \times p(\text{verdict}_c)}{\sum_{c \in \text{Claims}} w(\text{risk}_c)} \right)$$

### Risk weights ($w$):
- `critical`: **4.0**
- `high`: **2.0**
- `medium`: **1.0**
- `low`: **0.5**

### Penalty multipliers ($p$):
- `CONTRADICTED`: **1.0**
- `PARTLY_SUPPORTED`: **0.5**
- `OUTDATED`: **0.4**
- `UNRESOLVED_CONFLICTING`: **0.4**
- `UNRESOLVED_NO_EVIDENCE`: **0.3**
- `UNRESOLVED_ACCESS_LIMITED`: **0.2**
- `SUPPORTED`: **0.0**
- `NOT_CHECKABLE`: **0.0**
