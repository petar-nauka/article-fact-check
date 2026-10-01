#!/usr/bin/env python3
"""Initialize, inspect, manipulate, validate, and render a resumable article claim audit."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


SCHEMA_VERSION = "2.0"
VERDICTS = {
    "SUPPORTED",
    "PARTLY_SUPPORTED",
    "CONTRADICTED",
    "UNRESOLVED_NO_EVIDENCE",
    "UNRESOLVED_CONFLICTING",
    "UNRESOLVED_ACCESS_LIMITED",
    "OUTDATED",
    "NOT_YET_VERIFIABLE",
    "NOT_RESEARCHED",
    "NOT_CHECKABLE",
}
PROBLEM_VERDICTS = VERDICTS - {"SUPPORTED", "NOT_CHECKABLE"}
ANNOTATION_VERDICTS = {"PARTLY_SUPPORTED", "CONTRADICTED", "UNRESOLVED_CONFLICTING", "OUTDATED"}
CONFIDENCE = {"none", "low", "medium", "high"}
RISKS = {"low", "medium", "high", "critical"}
STANCES = {"supports", "refutes", "context"}
QUERY_KINDS = {"exact", "primary", "independent", "counter", "scholarly"}
SEARCH_STATUSES = {
    "not_run",
    "completed",
    "cached",
    "zero_results",
    "blocked",
    "paywalled",
    "captcha",
    "error",
    "not_found",
}
INSPECTED_ACCESS = {"full", "full_text", "official_record", "abstract_only"}
WEAK_ACCESS = {"not_inspected", "snippet_only", "paywalled", "blocked", "captcha", "dead_link", "not_found"}
EXCLUSIONS = {
    "heading_or_navigation",
    "pure_opinion_or_value_judgment",
    "recommendation",
    "unfalsifiable_rhetoric",
    "duplicate_container",
    "bibliography_container",
    "formatting_only",
    "unreadable_or_missing",
    "out_of_scope",
}

BG_VERDICT = {
    "SUPPORTED": "ДОКАЗАНО",
    "PARTLY_SUPPORTED": "ЧАСТИЧНО ДОКАЗАНО",
    "CONTRADICTED": "ОПРОВЕРГАНО",
    "UNRESOLVED_NO_EVIDENCE": "НЕДОСТАТЪЧНО ДОКАЗАТЕЛСТВА",
    "UNRESOLVED_CONFLICTING": "ПРОТИВОРЕЧИВИ ДОКАЗАТЕЛСТВА",
    "UNRESOLVED_ACCESS_LIMITED": "ОГРАНИЧЕН ДОСТЪП ДО ДОКАЗАТЕЛСТВА",
    "OUTDATED": "ОСТАРЯЛО/ПРОМЕНЕНО",
    "NOT_YET_VERIFIABLE": "ВСЕ ОЩЕ НЕПРОВЕРИМО",
    "NOT_RESEARCHED": "НЕПРОВЕРЕНО",
    "NOT_CHECKABLE": "НЕПРОВЕРИМО ТВЪРДЕНИЕ",
}

RISK_WEIGHTS = {"critical": 4.0, "high": 2.0, "medium": 1.0, "low": 0.5}
VERDICT_PENALTIES = {
    "CONTRADICTED": 1.0,
    "PARTLY_SUPPORTED": 0.5,
    "OUTDATED": 0.4,
    "UNRESOLVED_CONFLICTING": 0.4,
    "UNRESOLVED_NO_EVIDENCE": 0.3,
    "UNRESOLVED_ACCESS_LIMITED": 0.2,
    "NOT_YET_VERIFIABLE": 0.1,
    "NOT_RESEARCHED": 0.0,
    "SUPPORTED": 0.0,
    "NOT_CHECKABLE": 0.0,
}


class BlockHTMLParser(HTMLParser):
    BLOCKS = {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "blockquote",
        "figcaption",
        "caption",
        "tr",
        "pre",
        "article",
        "section",
    }
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.buffer: list[str] = []
        self.skip_depth = 0

    def flush(self) -> None:
        text = " ".join("".join(self.buffer).split())
        if text:
            self.blocks.append(text)
        self.buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if not self.skip_depth and tag in self.BLOCKS:
            self.flush()
        if not self.skip_depth and tag == "br":
            self.buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
            return
        if not self.skip_depth and tag in self.BLOCKS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.buffer.append(data)

    def close(self) -> None:
        super().close()
        self.flush()


def read_text(path: Path, encoding: str = "", language: str = "bg") -> str:
    raw = path.read_bytes()
    if encoding:
        return raw.decode(encoding)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for candidate in ("utf-8-sig", "cp1251" if language.lower().startswith("bg") else "cp1252"):
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode {path}; specify --encoding explicitly")


def extract_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as docx:
            xml_content = docx.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            paragraphs = []
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for p in tree.iterfind(".//w:p", ns):
                texts = [node.text for node in p.iterfind(".//w:t", ns) if node.text]
                if texts:
                    line = "".join(texts).strip()
                    if line:
                        paragraphs.append(line)
            return "\n\n".join(paragraphs)
    except Exception as exc:
        raise ValueError(f"Failed to extract DOCX content from {path}: {exc}") from exc


def extract_pdf_text(path: Path) -> str:
    failures = []
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        pages = [text for page in reader.pages if (text := page.extract_text())]
        if pages:
            return "\n\n".join(pages)
    except ImportError:
        pass
    except Exception as exc:
        failures.append(f"pypdf: {exc}")

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        pages = [text for page in doc if (text := page.get_text())]
        if pages:
            return "\n\n".join(pages)
    except ImportError:
        pass
    except Exception as exc:
        failures.append(f"PyMuPDF: {exc}")

    raise ValueError(
        f"Cannot reliably extract PDF text from {path}. Install pypdf or convert/OCR the file first."
        + (f" Extractor errors: {'; '.join(failures)}" if failures else "")
    )


def fetch_url_text(url: str) -> tuple[str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read()
    if content_type == "application/pdf" or raw.startswith(b"%PDF-"):
        try:
            import pypdf
        except ImportError as exc:
            raise ValueError("PDF URL requires pypdf; install the pdf extra or provide extracted text") from exc
        try:
            pages = [text for page in pypdf.PdfReader(io.BytesIO(raw)).pages if (text := page.extract_text())]
        except Exception as exc:
            raise ValueError(f"Cannot extract PDF URL text: {exc}") from exc
        if not pages:
            raise ValueError("PDF URL has no extractable text; provide OCR text")
        return "\n\n".join(pages), ""
    try:
        decoded = raw.decode(charset, errors="replace")
    except LookupError as exc:
        raise ValueError(f"Unsupported URL character set: {charset}") from exc
    if content_type == "text/plain":
        return decoded, ""
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise ValueError(f"Unsupported URL content type: {content_type}")
    
    parser = BlockHTMLParser()
    parser.feed(decoded)
    parser.close()
    
    title_match = re.search(r"<title[^>]*>(.*?)</title>", decoded, re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1).strip()) if title_match else ""
    return "\n\n".join(parser.blocks), title


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, stream.getvalue())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_blocks(path: Path | None, text: str) -> list[str]:
    if path and path.suffix.lower() in {".html", ".htm"}:
        parser = BlockHTMLParser()
        parser.feed(text)
        parser.close()
        return parser.blocks

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    blocks = [part.strip() for part in re.split(r"\n[ \t]*\n+", normalized) if part.strip()]
    return blocks


def infer_block_type(text: str) -> str:
    first = text.lstrip()
    if re.match(r"^#{1,6}\s+", first):
        return "heading"
    if "|" in text and "\n" in text:
        return "table_or_structured_block"
    return "paragraph"


GRADE_LABELS = {
    "no_checkable": ("Няма проверими твърдения", "No checkable claims"),
    "unresearched": ("Има непроучени твърдения", "Some claims are not researched"),
    "A": ("Отлична надеждност (A)", "Excellent reliability (A)"),
    "B": ("Много добра надеждност (B)", "Very good reliability (B)"),
    "C": ("Средна надеждност / Необходими корекции (C)", "Moderate reliability / corrections needed (C)"),
    "D": ("Ниска надеждност / Сериозни фактически дефекти (D)", "Low reliability / serious factual defects (D)"),
    "F": ("Критично компрометирана фактология (F)", "Critically compromised factual basis (F)"),
    "partial": ("Частичен одит", "Partial audit"),
    "invalid": ("Невалиден одит", "Invalid audit"),
}


def grade_label(key: str, language: str = "bg") -> str:
    bg, en = GRADE_LABELS[key]
    return bg if str(language or "bg").lower().startswith("bg") else en


def calculate_reliability_score(claims: list[dict[str, Any]], language: str = "bg") -> dict[str, Any]:
    checkable = [c for c in claims if c.get("verdict") not in {"NOT_CHECKABLE", None}]
    if not checkable:
        return {"score": None, "total_weight": 0.0, "penalties": 0.0, "grade": grade_label("no_checkable", language)}
    if any(c.get("verdict") == "NOT_RESEARCHED" for c in checkable):
        return {"score": None, "total_weight": 0.0, "penalties": 0.0, "grade": grade_label("unresearched", language)}

    total_weight = 0.0
    penalties = 0.0
    for c in checkable:
        risk = str(c.get("risk", "medium")).lower()
        w = RISK_WEIGHTS.get(risk, 1.0)
        verdict = str(c.get("verdict", "NOT_RESEARCHED"))
        p = VERDICT_PENALTIES.get(verdict, 0.0)
        total_weight += w
        penalties += w * p

    if total_weight == 0:
        score = 100.0
    else:
        score = max(0.0, min(100.0, 100.0 * (1.0 - (penalties / total_weight))))

    if score >= 90.0:
        grade_key = "A"
    elif score >= 80.0:
        grade_key = "B"
    elif score >= 65.0:
        grade_key = "C"
    elif score >= 50.0:
        grade_key = "D"
    else:
        grade_key = "F"

    return {
        "score": round(score, 1),
        "total_weight": round(total_weight, 2),
        "penalties": round(penalties, 2),
        "grade": grade_label(grade_key, language),
    }


def audit_reliability_score(audit: dict[str, Any]) -> dict[str, Any]:
    """Only publish a numeric heuristic after the audit is marked complete."""
    language = str(audit.get("metadata", {}).get("language", "bg"))
    if audit.get("metadata", {}).get("status") != "complete":
        return {"score": None, "total_weight": 0.0, "penalties": 0.0, "grade": grade_label("partial", language)}
    errors, _ = validate_audit(audit)
    if errors:
        return {"score": None, "total_weight": 0.0, "penalties": 0.0, "grade": grade_label("invalid", language)}
    return calculate_reliability_score(audit.get("claims", []), language)


def score_label(result: dict[str, Any]) -> str:
    return f"{result['score']}%" if result["score"] is not None else "N/A"


def load_audit(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("audit.json must contain a JSON object")
    return value


def initialize(args: argparse.Namespace) -> int:
    source_str = args.article
    out_dir = Path(args.out).resolve()
    audit_path = out_dir / "audit.json"
    if audit_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing audit: {audit_path}")

    inferred_title = args.title or ""
    is_url = source_str.startswith("http://") or source_str.startswith("https://")

    if is_url:
        print(f"Fetching URL: {source_str} ...")
        text, fetched_title = fetch_url_text(source_str)
        if not inferred_title and fetched_title:
            inferred_title = fetched_title
        source_path_str = source_str
        frozen_filename = "frozen-article.txt"
    else:
        source = Path(source_str).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Article file not found: {source}")
        ext = source.suffix.lower()
        if ext == ".docx":
            print(f"Extracting DOCX: {source} ...")
            text = extract_docx_text(source)
            frozen_filename = "frozen-article.txt"
        elif ext == ".pdf":
            print(f"Extracting PDF: {source} ...")
            text = extract_pdf_text(source)
            frozen_filename = "frozen-article.txt"
        else:
            text = read_text(source, args.encoding, args.language)
            frozen_filename = f"frozen-article{ext or '.txt'}"
        source_path_str = str(source)
        if not inferred_title:
            inferred_title = source.stem

    blocks = split_blocks(Path(source_path_str) if not is_url else None, text)
    if not blocks:
        raise ValueError("No readable article blocks were found")

    out_dir.mkdir(parents=True, exist_ok=True)
    frozen = out_dir / frozen_filename
    atomic_write_text(frozen, text)
    doc_hash = sha256_file(frozen)

    paragraphs = []
    numbered = ["# Numbered article for claim mapping", ""]
    for index, block in enumerate(blocks, 1):
        paragraph_id = f"P{index:03d}"
        paragraphs.append(
            {
                "id": paragraph_id,
                "type": infer_block_type(block),
                "locator": f"block:{index}",
                "text": block,
                "claim_ids": [],
                "exclusion": "",
            }
        )
        numbered.extend([f"## {paragraph_id}", "", block, ""])

    for block in blocks[:3]:
        match = re.match(r"^#\s+(.+)$", block.strip())
        if match:
            inferred_title = match.group(1).strip()
            break

    audit = {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "title": inferred_title or "Untitled Document",
            "source_path": frozen.name,
            "original_source_path": source_path_str,
            "document_sha256": doc_hash,
            "original_document_sha256": sha256_file(source) if not is_url else "",
            "language": args.language,
            "article_reference_date": args.article_reference_date or "",
            "analysis_date": date.today().isoformat(),
            "scope": "exhaustive",
            "status": "partial",
            "decomposition_complete": False,
            "omission_pass_complete": False,
            "supplied_sources_audited": False,
        },
        "paragraphs": paragraphs,
        "claims": [],
        "sources": [],
        "searches": [],
        "limitations": [],
        "unchecked_items": [],
    }
    atomic_write_json(audit_path, audit)
    atomic_write_text(out_dir / "numbered-article.md", "\n".join(numbered).rstrip() + "\n")
    (out_dir / "research").mkdir(exist_ok=True)
    print(f"[OK] Initialized {audit_path}")
    print(f"[OK] Paragraph blocks: {len(paragraphs)}")
    print(f"[OK] SHA-256: {audit['metadata']['document_sha256']}")
    return 0


def extract_markers_command(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit).resolve()
    audit = load_audit(audit_path)
    paragraphs = audit.get("paragraphs", [])

    results = []
    num_re = re.compile(r"(\b\d+([.,]\d+)?\s*(%|процент|млрд|млн|хил|€|\$|лв|лева|USD|EUR|BGN|kW|MW|GW|TW|т|тона|kg|km|км|m²|кв\.м)\b|\b\d{1,3}(?:[ ,]\d{3})+(?:[.,]\d+)?\b)", re.IGNORECASE)
    date_re = re.compile(r"(\b(19\d\d|20\d\d)\s*(г\.|година)?\b|\b\d{1,2}\s+(януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b|\bпрез\s+\d{4}\b)", re.IGNORECASE)
    super_re = re.compile(r"(\b(първи(ят|я)?|единствен(ият|я)?|най-голям(ият|я)?|най-бърз(ият|я)?|най-висок(ият|я)?|никога|всички|нито един|first|only|largest|fastest|highest|never|none|unique)\b)", re.IGNORECASE)
    quote_re = re.compile(r'(„[^“]+“|"[^"]+"|\bказа\b|\bзаяви\b|\bспоред\b|\bтвърди\b|\bпотвърди\b|\bотбеляза\b|\bsaid\b|\baccording to\b|\bclaimed\b|\bstated\b)', re.IGNORECASE)
    law_re = re.compile(r"(\b(Закон за|Директива|Регламент|Наредба|Указ|ISO\s*\d+|Act|Directive|Regulation)\b[^\.\,\;]+)", re.IGNORECASE)
    link_re = re.compile(r"(https?://[^\s\)]+|doi:\s*10\.\d{4,9}/[^\s\)]+)", re.IGNORECASE)

    for p in paragraphs:
        pid = p.get("id")
        text = p.get("text", "")
        p_type = p.get("type", "paragraph")
        if p_type == "heading":
            continue

        markers: dict[str, list[str]] = {
            "numbers": [m.group(0) for m in num_re.finditer(text)],
            "dates": [m.group(0) for m in date_re.finditer(text)],
            "superlatives": [m.group(0) for m in super_re.finditer(text)],
            "quotes_attribution": [m.group(0) for m in quote_re.finditer(text)],
            "laws_regulations": [m.group(0) for m in law_re.finditer(text)],
            "links_identifiers": [m.group(0) for m in link_re.finditer(text)],
        }
        active_markers = {k: v for k, v in markers.items() if v}
        if active_markers:
            results.append({"paragraph_id": pid, "text_snippet": text[:120] + ("..." if len(text) > 120 else ""), "markers": active_markers})

    if args.json_out:
        atomic_write_json(Path(args.json_out).resolve(), results)

    print(f"=== Omission & Claim Markers Scan ({len(results)} paragraphs with potential factual claims) ===\n")
    for row in results[:30]:
        print(f"[{row['paragraph_id']}] {row['text_snippet']}")
        for mtype, vals in row["markers"].items():
            print(f"   -> {mtype}: {', '.join(set(vals))}")
        print()
    if len(results) > 30:
        print(f"... and {len(results) - 30} more paragraphs.")
    return 0


def status_command(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit).resolve()
    audit = load_audit(audit_path)
    meta = audit.get("metadata", {})
    paragraphs = audit.get("paragraphs", [])
    claims = audit.get("claims", [])
    sources = audit.get("sources", [])
    searches = audit.get("searches", [])

    counts = Counter(c.get("verdict", "NOT_RESEARCHED") for c in claims)
    checkable = [c for c in claims if c.get("verdict") != "NOT_CHECKABLE"]
    researched = [c for c in checkable if c.get("verdict") != "NOT_RESEARCHED"]
    not_researched = [c for c in checkable if c.get("verdict") == "NOT_RESEARCHED"]
    covered_paragraphs = [p for p in paragraphs if p.get("claim_ids") or p.get("exclusion")]

    rel = audit_reliability_score(audit)

    print(f"============================================================")
    print(f"  FACT-CHECK STATUS: {meta.get('title', 'Untitled')}")
    print(f"============================================================")
    print(f"Status:            {meta.get('status', 'partial').upper()}")
    print(f"Analysis Date:     {meta.get('analysis_date', '')}")
    print(f"Paragraphs:        {len(covered_paragraphs)} / {len(paragraphs)} covered")
    print(f"Total Claims:      {len(claims)} ({len(checkable)} checkable)")
    print(f"Research Progress: {len(researched)} / {len(checkable)} completed ({len(not_researched)} remaining)")
    print(f"Total Sources:     {len(sources)}")
    print(f"Total Searches:    {len(searches)}")
    print(f"------------------------------------------------------------")
    print(f"Weighted audit index: {score_label(rel)} -- {rel['grade']}")
    print(f"------------------------------------------------------------")
    print("Verdicts Breakdown:")
    for v in sorted(VERDICTS):
        if counts.get(v):
            print(f"  - {v:25s}: {counts[v]:3d} ({BG_VERDICT.get(v, v)})")
    print(f"============================================================")
    return 0


def add_claim_command(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit).resolve()
    audit = load_audit(audit_path)
    claims = audit.setdefault("claims", [])
    paragraphs = audit.get("paragraphs", [])
    para_by_id = {p["id"]: p for p in paragraphs}

    cid = args.id
    if not cid:
        cid = f"C{len(claims) + 1:04d}"
    if any(c.get("id") == cid for c in claims):
        raise ValueError(f"Claim ID {cid} already exists")

    para_ids = [p.strip() for p in args.paragraphs.split(",") if p.strip()]
    for pid in para_ids:
        if pid not in para_by_id:
            raise ValueError(f"Unknown paragraph ID {pid}")

    claim_types = [t.strip() for t in args.types.split(",") if t.strip()]
    if not claim_types:
        claim_types = ["factual"]

    new_claim = {
        "id": cid,
        "paragraph_ids": para_ids,
        "original_text": args.text,
        "normalized_claim": args.normalized or args.text,
        "claim_types": claim_types,
        "risk": args.risk,
        "verdict": args.verdict or "NOT_RESEARCHED",
        "confidence": "none" if (args.verdict or "NOT_RESEARCHED") == "NOT_RESEARCHED" else "medium",
        "research_complete": False if (args.verdict or "NOT_RESEARCHED") == "NOT_RESEARCHED" else True,
        "rationale": args.rationale or "",
        "evidence_links": [],
        "search_ids": [],
        "recommended_edit": args.recommended_edit or "",
        "editor_comment": args.editor_comment or "",
        "notes": "",
    }
    claims.append(new_claim)

    for pid in para_ids:
        p = para_by_id[pid]
        cids = p.setdefault("claim_ids", [])
        if cid not in cids:
            cids.append(cid)
        p["exclusion"] = ""

    atomic_write_json(audit_path, audit)
    print(f"[OK] Added claim {cid} linked to {', '.join(para_ids)}")
    return 0


def update_claim_command(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit).resolve()
    audit = load_audit(audit_path)
    claims = audit.get("claims", [])
    claim = next((c for c in claims if c.get("id") == args.id), None)
    if not claim:
        raise ValueError(f"Claim {args.id} not found")

    if args.verdict:
        if args.verdict not in VERDICTS:
            raise ValueError(f"Invalid verdict {args.verdict}")
        claim["verdict"] = args.verdict
        if args.verdict != "NOT_RESEARCHED":
            claim["research_complete"] = True
    if args.confidence:
        claim["confidence"] = args.confidence
    if args.rationale:
        claim["rationale"] = args.rationale
    if args.recommended_edit is not None:
        claim["recommended_edit"] = args.recommended_edit
    if args.editor_comment is not None:
        claim["editor_comment"] = args.editor_comment
    if args.notes is not None:
        claim["notes"] = args.notes

    if args.source_id:
        evidence_links = claim.setdefault("evidence_links", [])
        evidence_links.append(
            {
                "source_id": args.source_id,
                "stance": args.stance or "supports",
                "locator": args.locator or "",
                "finding": args.finding or "",
            }
        )
    if args.search_id:
        search_ids = claim.setdefault("search_ids", [])
        if args.search_id not in search_ids:
            search_ids.append(args.search_id)

    atomic_write_json(audit_path, audit)
    print(f"[OK] Updated claim {args.id} (verdict: {claim.get('verdict')})")
    return 0


def add_source_command(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit).resolve()
    audit = load_audit(audit_path)
    sources = audit.setdefault("sources", [])

    sid = args.id
    if not sid:
        sid = f"S{len(sources) + 1:03d}"

    source = next((s for s in sources if s.get("id") == sid), None)
    if not source:
        source = {"id": sid}
        sources.append(source)

    source.update(
        {
            "title": args.title,
            "url": args.url or "",
            "source_uri": args.source_uri or args.url or "",
            "author_or_org": args.author_org or "",
            "publication_date": args.pub_date or "",
            "access_date": args.access_date or date.today().isoformat(),
            "source_type": args.source_type or "report",
            "source_tier": args.source_tier or "limited",
            "access_status": args.access_status or "not_inspected",
            "origin_cluster": args.origin_cluster or (args.author_org or sid),
            "supplied_by_author": args.supplied if args.supplied is not None else False,
            "contribution": args.contribution or "",
            "limitations": args.limitations or "",
        }
    )

    atomic_write_json(audit_path, audit)
    print(f"[OK] Recorded source {sid}: {args.title}")
    return 0


def add_search_command(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit).resolve()
    audit = load_audit(audit_path)
    searches = audit.setdefault("searches", [])

    qid = args.id
    if not qid:
        qid = f"Q{len(searches) + 1:03d}"

    claim_ids = [c.strip() for c in args.claim_ids.split(",") if c.strip()]
    new_search = {
        "id": qid,
        "claim_ids": claim_ids,
        "query": args.query,
        "kind": args.kind or "primary",
        "language": args.language or "bg",
        "run_date": args.run_date or date.today().isoformat(),
        "surface": args.surface or "web",
        "status": args.status or "not_run",
        "result_note": args.result_note or "",
    }
    searches.append(new_search)

    claim_by_id = {c["id"]: c for c in audit.get("claims", [])}
    for cid in claim_ids:
        if cid in claim_by_id:
            s_ids = claim_by_id[cid].setdefault("search_ids", [])
            if qid not in s_ids:
                s_ids.append(qid)

    atomic_write_json(audit_path, audit)
    print(f"[OK] Recorded search {qid} for {', '.join(claim_ids)}")
    return 0


def merge_batch_command(args: argparse.Namespace) -> int:
    main_path = Path(args.audit).resolve()
    batch_path = Path(args.batch).resolve()

    main_audit = load_audit(main_path)
    batch_audit = load_audit(batch_path)

    main_claims = {c["id"]: c for c in main_audit.setdefault("claims", [])}
    main_sources = {s["id"]: s for s in main_audit.setdefault("sources", [])}
    main_searches = {q["id"]: q for q in main_audit.setdefault("searches", [])}

    # Reject ID collisions that refer to different records before modifying the audit.
    for kind, rows, existing in (
        ("source", batch_audit.get("sources", []), main_sources),
        ("search", batch_audit.get("searches", []), main_searches),
        ("claim", batch_audit.get("claims", []), main_claims),
    ):
        seen_batch: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"Batch {kind} is missing an ID")
            if row["id"] in seen_batch:
                raise ValueError(f"Duplicate {kind} ID {row['id']} in batch")
            seen_batch.add(row["id"])
            old = existing.get(row["id"])
            if old is None:
                continue
            if kind == "source":
                old_key = old.get("url") or old.get("source_uri")
                new_key = row.get("url") or row.get("source_uri")
            elif kind == "search":
                old_key, new_key = old.get("query"), row.get("query")
            else:
                old_key = (old.get("original_text"), old.get("paragraph_ids"))
                new_key = (row.get("original_text"), row.get("paragraph_ids"))
            if old_key and new_key and old_key != new_key:
                raise ValueError(f"Conflicting {kind} ID {row['id']}; assign a unique ID before merge")
    available_paragraphs = {p.get("id") for p in main_audit.get("paragraphs", [])}
    available_sources = set(main_sources) | {s["id"] for s in batch_audit.get("sources", [])}
    available_searches = set(main_searches) | {q["id"] for q in batch_audit.get("searches", [])}
    for claim in batch_audit.get("claims", []):
        for pid in claim.get("paragraph_ids", []):
            if pid not in available_paragraphs:
                raise ValueError(f"Batch claim {claim['id']} references unknown paragraph {pid}")
        for link in claim.get("evidence_links", []):
            if link.get("source_id") not in available_sources:
                raise ValueError(f"Batch claim {claim['id']} references unknown source {link.get('source_id')}")
        for qid in claim.get("search_ids", []):
            if qid not in available_searches:
                raise ValueError(f"Batch claim {claim['id']} references unknown search {qid}")

    updated_claims_count = 0
    new_sources_count = 0
    new_searches_count = 0

    for s in batch_audit.get("sources", []):
        sid = s.get("id")
        if sid:
            if sid not in main_sources:
                main_audit["sources"].append(s)
                main_sources[sid] = s
                new_sources_count += 1
            else:
                main_sources[sid].update(s)

    for q in batch_audit.get("searches", []):
        qid = q.get("id")
        if qid:
            if qid not in main_searches:
                main_audit["searches"].append(q)
                main_searches[qid] = q
                new_searches_count += 1
            else:
                main_searches[qid].update(q)

    # 3. Merge claims
    for c in batch_audit.get("claims", []):
        cid = c.get("id")
        pids = c.get("paragraph_ids", [])
        for pid in pids:
            para = next((p for p in main_audit.get("paragraphs", []) if p.get("id") == pid), None)
            if para:
                para_cids = para.setdefault("claim_ids", [])
                if cid not in para_cids:
                    para_cids.append(cid)
                para["exclusion"] = ""

        if cid in main_claims:
            main_claims[cid].update(
                {
                    "verdict": c.get("verdict", main_claims[cid].get("verdict")),
                    "confidence": c.get("confidence", main_claims[cid].get("confidence")),
                    "research_complete": c.get("research_complete", main_claims[cid].get("research_complete")),
                    "rationale": c.get("rationale", main_claims[cid].get("rationale")),
                    "recommended_edit": c.get("recommended_edit", main_claims[cid].get("recommended_edit")),
                    "editor_comment": c.get("editor_comment", main_claims[cid].get("editor_comment")),
                    "notes": c.get("notes", main_claims[cid].get("notes")),
                }
            )
            existing_links = main_claims[cid].setdefault("evidence_links", [])
            for link in c.get("evidence_links", []):
                if link not in existing_links:
                    existing_links.append(link)
            existing_searches = main_claims[cid].setdefault("search_ids", [])
            for qid in c.get("search_ids", []):
                if qid not in existing_searches:
                    existing_searches.append(qid)
            updated_claims_count += 1
        else:
            main_audit["claims"].append(c)
            main_claims[cid] = c
            updated_claims_count += 1

    atomic_write_json(main_path, main_audit)
    print(f"[OK] Batch merge complete from {batch_path.name}:")
    print(f"  - Updated claims: {updated_claims_count}")
    print(f"  - New sources: {new_sources_count}")
    print(f"  - New searches: {new_searches_count}")
    return 0


def duplicate_values(values: Iterable[str]) -> set[str]:
    counts = Counter(values)
    return {value for value, count in counts.items() if count > 1}


def normalize_space(value: str) -> str:
    return " ".join(value.split()).casefold()


def validate_audit(audit: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if audit.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    metadata = audit.get("metadata")
    if not isinstance(metadata, dict):
        return ["metadata must be an object"], warnings
    for field in ("title", "document_sha256", "language", "analysis_date", "scope", "status"):
        if not metadata.get(field):
            errors.append(f"metadata.{field} is required")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(metadata.get("document_sha256", ""))):
        errors.append("metadata.document_sha256 must be a SHA-256 hex digest")
    if metadata.get("language") not in {"bg", "en"}:
        errors.append("metadata.language must be bg or en")
    if metadata.get("status") not in {"partial", "complete"}:
        errors.append("metadata.status must be partial or complete")
    if metadata.get("status") == "complete":
        for field in ("decomposition_complete", "omission_pass_complete", "supplied_sources_audited"):
            if metadata.get(field) is not True:
                errors.append(f"complete audit requires metadata.{field}=true")
        if audit.get("unchecked_items"):
            errors.append("complete audit cannot have unchecked_items")

    paragraphs = audit.get("paragraphs", [])
    claims = audit.get("claims", [])
    sources = audit.get("sources", [])
    searches = audit.get("searches", [])
    for name, rows in (("paragraphs", paragraphs), ("claims", claims), ("sources", sources), ("searches", searches)):
        if not isinstance(rows, list):
            return [f"{name} must be an array"], warnings
    if not paragraphs:
        errors.append("paragraphs must not be empty")
    for name in ("limitations", "unchecked_items"):
        if not isinstance(audit.get(name, []), list):
            errors.append(f"{name} must be an array")

    paragraph_ids = [str(row.get("id", "")) for row in paragraphs if isinstance(row, dict)]
    claim_ids = [str(row.get("id", "")) for row in claims if isinstance(row, dict)]
    source_ids = [str(row.get("id", "")) for row in sources if isinstance(row, dict)]
    search_ids = [str(row.get("id", "")) for row in searches if isinstance(row, dict)]

    for label_name, values in (
        ("paragraph", paragraph_ids),
        ("claim", claim_ids),
        ("source", source_ids),
        ("search", search_ids),
    ):
        for duplicate in sorted(duplicate_values(values)):
            errors.append(f"Duplicate {label_name} ID: {duplicate}")

    claim_id_set = set(claim_ids)
    paragraph_id_set = set(paragraph_ids)
    source_by_id = {str(row.get("id")): row for row in sources if isinstance(row, dict)}
    search_by_id = {str(row.get("id")): row for row in searches if isinstance(row, dict)}

    for row in paragraphs:
        if not isinstance(row, dict):
            errors.append("Every paragraph must be an object")
            continue
        pid = str(row.get("id", ""))
        if not re.fullmatch(r"P\d{3,}", pid):
            errors.append(f"Invalid paragraph ID: {pid!r}")
        if not str(row.get("text", "")).strip():
            errors.append(f"{pid}: paragraph text is empty")
        linked = row.get("claim_ids", [])
        exclusion = str(row.get("exclusion", "") or "")
        if not isinstance(linked, list):
            errors.append(f"{pid}: claim_ids must be an array")
            linked = []
        for claim_id in linked:
            if claim_id not in claim_id_set:
                errors.append(f"{pid}: unknown claim ID {claim_id}")
            elif pid not in next(c for c in claims if isinstance(c, dict) and c.get("id") == claim_id).get("paragraph_ids", []):
                errors.append(f"{pid}: {claim_id} does not link back to this paragraph")
        if linked and exclusion:
            errors.append(f"{pid}: cannot have both claim_ids and exclusion")
        if exclusion and exclusion not in EXCLUSIONS:
            errors.append(f"{pid}: unsupported exclusion {exclusion}")
        if metadata.get("decomposition_complete") and not linked and not exclusion:
            errors.append(f"{pid}: missing claim mapping or justified exclusion")

    for source in sources:
        if not isinstance(source, dict):
            errors.append("Every source must be an object")
            continue
        sid = str(source.get("id", ""))
        if not re.fullmatch(r"S\d{3,}", sid):
            errors.append(f"Invalid source ID: {sid!r}")
        for field in ("title", "source_type", "source_tier", "access_status", "origin_cluster"):
            if not str(source.get(field, "")).strip():
                errors.append(f"{sid}: {field} is required")
        if source.get("access_status") not in INSPECTED_ACCESS | WEAK_ACCESS:
            errors.append(f"{sid}: invalid access_status {source.get('access_status')!r}")
        if not str(source.get("url", "")).strip() and not str(source.get("source_uri", "")).strip():
            errors.append(f"{sid}: url or source_uri is required")
        url = str(source.get("url", "")).strip()
        if url and not re.match(r"^https?://", url, re.I):
            errors.append(f"{sid}: url must use http or https")
        if not str(source.get("access_date", "")).strip():
            warnings.append(f"{sid}: access_date is empty")

    for search in searches:
        if not isinstance(search, dict):
            errors.append("Every search must be an object")
            continue
        qid = str(search.get("id", ""))
        if not re.fullmatch(r"Q\d{3,}", qid):
            errors.append(f"Invalid search ID: {qid!r}")
        if search.get("kind") not in QUERY_KINDS:
            errors.append(f"{qid}: invalid query kind {search.get('kind')!r}")
        if search.get("status") not in SEARCH_STATUSES:
            errors.append(f"{qid}: invalid search status {search.get('status')!r}")
        if not str(search.get("query", "")).strip():
            errors.append(f"{qid}: query is required")
        search_claim_ids = search.get("claim_ids", [])
        if not isinstance(search_claim_ids, list):
            errors.append(f"{qid}: claim_ids must be an array")
            search_claim_ids = []
        for cid in search_claim_ids:
            if cid not in claim_id_set:
                errors.append(f"{qid}: unknown claim ID {cid}")

    paragraph_by_id = {str(row.get("id")): row for row in paragraphs if isinstance(row, dict)}
    for claim in claims:
        if not isinstance(claim, dict):
            errors.append("Every claim must be an object")
            continue
        cid = str(claim.get("id", ""))
        if not re.fullmatch(r"C\d{4,}", cid):
            errors.append(f"Invalid claim ID: {cid!r}")
        verdict = claim.get("verdict")
        confidence = claim.get("confidence")
        risk = claim.get("risk")
        if verdict not in VERDICTS:
            errors.append(f"{cid}: invalid verdict {verdict!r}")
        if confidence not in CONFIDENCE:
            errors.append(f"{cid}: invalid confidence {confidence!r}")
        if risk not in RISKS:
            errors.append(f"{cid}: invalid risk {risk!r}")
        if not str(claim.get("normalized_claim", "")).strip():
            errors.append(f"{cid}: normalized_claim is required")
        if not isinstance(claim.get("claim_types"), list) or not claim.get("claim_types"):
            errors.append(f"{cid}: claim_types must be a nonempty array")

        linked_paragraphs = claim.get("paragraph_ids", [])
        if not isinstance(linked_paragraphs, list) or not linked_paragraphs:
            errors.append(f"{cid}: paragraph_ids must be a nonempty array")
            linked_paragraphs = []
        original_text = normalize_space(str(claim.get("original_text", "")))
        if not original_text:
            errors.append(f"{cid}: original_text is required")
        for pid in linked_paragraphs:
            if pid not in paragraph_id_set:
                errors.append(f"{cid}: unknown paragraph ID {pid}")
                continue
            paragraph = paragraph_by_id[pid]
            if cid not in paragraph.get("claim_ids", []):
                errors.append(f"{cid}: {pid} does not link back to this claim")
        if original_text and linked_paragraphs and not any(
            original_text in normalize_space(str(paragraph_by_id[pid].get("text", "")))
            for pid in linked_paragraphs if pid in paragraph_by_id
        ):
            errors.append(f"{cid}: original_text is not present in linked paragraphs")

        research_complete = claim.get("research_complete")
        if verdict == "NOT_RESEARCHED":
            if research_complete is True:
                errors.append(f"{cid}: NOT_RESEARCHED cannot be research_complete")
        elif verdict not in {None, "NOT_CHECKABLE"} and research_complete is not True:
            errors.append(f"{cid}: researched verdict requires research_complete=true")
        if metadata.get("status") == "complete" and verdict == "NOT_RESEARCHED":
            errors.append(f"{cid}: complete audit cannot contain NOT_RESEARCHED")
        if verdict not in {"NOT_RESEARCHED", "NOT_CHECKABLE", None} and not str(claim.get("rationale", "")).strip():
            errors.append(f"{cid}: researched verdict requires rationale")

        evidence_links = claim.get("evidence_links", [])
        if not isinstance(evidence_links, list):
            errors.append(f"{cid}: evidence_links must be an array")
            evidence_links = []
        inspected_stances: set[str] = set()
        for link in evidence_links:
            if not isinstance(link, dict):
                errors.append(f"{cid}: evidence link must be an object")
                continue
            sid = str(link.get("source_id", ""))
            stance = str(link.get("stance", ""))
            if sid not in source_by_id:
                errors.append(f"{cid}: unknown source ID {sid}")
            if stance not in STANCES:
                errors.append(f"{cid}: invalid evidence stance {stance!r}")
            if not str(link.get("locator", "")).strip() or not str(link.get("finding", "")).strip():
                errors.append(f"{cid}: evidence requires locator and finding")
            if sid in source_by_id and source_by_id[sid].get("access_status") in INSPECTED_ACCESS:
                inspected_stances.add(stance)

        linked_searches = claim.get("search_ids", [])
        if not isinstance(linked_searches, list):
            errors.append(f"{cid}: search_ids must be an array")
            linked_searches = []
        for qid in linked_searches:
            if qid not in search_by_id:
                errors.append(f"{cid}: unknown search ID {qid}")
            elif cid not in search_by_id[qid].get("claim_ids", []):
                errors.append(f"{cid}: search {qid} does not link back to this claim")

        if verdict == "SUPPORTED" and "supports" not in inspected_stances:
            errors.append(f"{cid}: SUPPORTED requires inspected supporting evidence")
        if verdict == "CONTRADICTED" and "refutes" not in inspected_stances:
            errors.append(f"{cid}: CONTRADICTED requires inspected refuting evidence")
        if verdict in {"PARTLY_SUPPORTED", "OUTDATED"} and not inspected_stances:
            errors.append(f"{cid}: {verdict} requires inspected evidence")
        if verdict == "UNRESOLVED_NO_EVIDENCE" and not any(
            search_by_id.get(qid, {}).get("status") in {"completed", "cached", "zero_results", "not_found"}
            for qid in linked_searches
        ):
            errors.append(f"{cid}: no-evidence verdict requires a completed search")
        if verdict == "UNRESOLVED_ACCESS_LIMITED" and not any(
            search_by_id.get(qid, {}).get("status") in {"blocked", "paywalled", "captcha"}
            for qid in linked_searches
        ):
            errors.append(f"{cid}: access-limited verdict requires a documented access barrier")
        if verdict in {"PARTLY_SUPPORTED", "CONTRADICTED", "OUTDATED"} and not str(claim.get("recommended_edit", "")).strip():
            errors.append(f"{cid}: factual mismatch requires recommended_edit")

    return errors, warnings


def validate_frozen_input(audit: dict[str, Any], audit_path: Path) -> list[str]:
    errors: list[str] = []
    metadata = audit.get("metadata", {})
    source_path = str(metadata.get("source_path", "")).strip()
    expected_hash = str(metadata.get("document_sha256", "")).strip().lower()
    if not source_path:
        return ["metadata.source_path is required"]
    frozen = Path(source_path)
    if not frozen.is_absolute():
        frozen = audit_path.parent / frozen
    frozen = frozen.resolve()
    if not frozen.is_file():
        return [f"Frozen article is missing: {frozen}"]
    actual_hash = sha256_file(frozen).lower()
    if expected_hash and actual_hash != expected_hash:
        errors.append(f"Frozen article SHA-256 mismatch: expected {expected_hash}, got {actual_hash}")
    return errors


def validate_command(args: argparse.Namespace) -> int:
    path = Path(args.audit).resolve()
    audit = load_audit(path)
    errors, warnings = validate_audit(audit)
    errors.extend(validate_frozen_input(audit, path))
    rel = audit_reliability_score(audit)
    result = {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "reliability_score": rel,
        "counts": {
            "paragraphs": len(audit.get("paragraphs", [])),
            "claims": len(audit.get("claims", [])),
            "sources": len(audit.get("sources", [])),
            "searches": len(audit.get("searches", [])),
        },
    }
    if args.json_out:
        atomic_write_json(Path(args.json_out).resolve(), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def md_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def label(verdict: str, language: str) -> str:
    return BG_VERDICT.get(verdict, verdict) if language.lower().startswith("bg") else verdict.replace("_", " ").title()


def source_link(source: dict[str, Any]) -> str:
    sid = str(source.get("id", ""))
    target = str(source.get("url") or source.get("source_uri") or "")
    if not re.match(r"^https?://", target, re.I):
        return sid
    target = target.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
    return f"[{sid}]({target})"


def safe_html_url(value: Any) -> str:
    target = str(value or "")
    return html.escape(target, quote=True) if re.match(r"^https?://", target, re.I) else "#"


def generate_html_report(audit: dict[str, Any], rel_score: dict[str, Any]) -> str:
    meta = audit.get("metadata", {})
    language = str(meta.get("language", "bg"))
    is_bg = language.lower().startswith("bg")
    claims = audit.get("claims", [])
    paragraphs = audit.get("paragraphs", [])
    sources = audit.get("sources", [])
    source_by_id = {str(s["id"]): s for s in sources}
    claim_by_id = {str(c["id"]): c for c in claims}
    limitations = audit.get("limitations", [])
    unchecked_items = audit.get("unchecked_items", [])

    checkable_claims = [c for c in claims if c.get("verdict") != "NOT_CHECKABLE"]
    problem_claims = [c for c in claims if c.get("verdict") in PROBLEM_VERDICTS]

    def verdict_badge_color(v: str) -> str:
        if v == "SUPPORTED":
            return "#10b981"
        if v in {"PARTLY_SUPPORTED", "OUTDATED"}:
            return "#f59e0b"
        if v == "CONTRADICTED":
            return "#ef4444"
        if v.startswith("UNRESOLVED"):
            return "#8b5cf6"
        return "#6b7280"

    def risk_badge_color(r: str) -> str:
        if r == "critical":
            return "#dc2626"
        if r == "high":
            return "#ea580c"
        if r == "medium":
            return "#3b82f6"
        return "#64748b"

    score_color = "#94a3b8" if rel_score["score"] is None else "#10b981" if rel_score["score"] >= 80 else "#f59e0b" if rel_score["score"] >= 60 else "#ef4444"
    html_content = f"""<!DOCTYPE html>
<html lang="{html.escape(language, quote=True)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(meta.get('title', 'Fact Check Report'))}</title>
<style>
:root {{
  --bg: #0f172a;
  --surface: #1e293b;
  --surface-alt: #334155;
  --text: #f8fafc;
  --text-muted: #94a3b8;
  --border: #475569;
  --primary: #38bdf8;
  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
body {{ background: var(--bg); color: var(--text); padding: 2rem 1rem; line-height: 1.6; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
.header {{ background: var(--surface); padding: 2rem; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 2rem; }}
.header h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; color: #fff; }}
.header .meta {{ color: var(--text-muted); font-size: 0.9rem; margin-bottom: 1.5rem; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-top: 1rem; }}
.stat-card {{ background: var(--surface-alt); padding: 1.2rem; border-radius: 8px; text-align: center; border: 1px solid rgba(255,255,255,0.05); }}
.stat-card .val {{ font-size: 2rem; font-weight: 700; color: var(--primary); }}
.stat-card .lbl {{ font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
.score-badge {{ font-size: 2.2rem; font-weight: 800; color: {score_color}; }}

.section-title {{ font-size: 1.4rem; margin: 2.5rem 0 1rem; border-bottom: 2px solid var(--border); padding-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center; }}
.filters {{ display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; }}
.btn {{ background: var(--surface-alt); color: var(--text); border: 1px solid var(--border); padding: 0.4rem 0.8rem; border-radius: 6px; cursor: pointer; font-size: 0.85rem; transition: all 0.2s; }}
.btn:hover, .btn.active {{ background: var(--primary); color: #0f172a; font-weight: 600; }}

.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.5rem; margin-bottom: 1rem; transition: transform 0.1s; }}
.card:hover {{ border-color: var(--primary); }}
.card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.8rem; }}
.badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.75rem; font-weight: 700; color: #fff; text-transform: uppercase; }}
.original-text {{ background: rgba(0,0,0,0.3); padding: 0.8rem; border-left: 3px solid var(--text-muted); border-radius: 4px; margin: 0.8rem 0; font-style: italic; }}
.edit-box {{ background: rgba(16, 185, 129, 0.1); border: 1px solid var(--success); border-radius: 6px; padding: 0.8rem; margin: 0.8rem 0; }}
.edit-box strong {{ color: var(--success); }}

.article-view {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 2rem; margin-bottom: 2rem; }}
.p-block {{ margin-bottom: 1.5rem; padding: 0.5rem; border-radius: 6px; transition: background 0.2s; }}
.p-block:hover {{ background: rgba(255,255,255,0.02); }}
.p-id {{ color: var(--text-muted); font-size: 0.75rem; font-family: monospace; margin-bottom: 0.2rem; }}
.annot-comment {{ background: rgba(245, 158, 11, 0.1); border-left: 4px solid var(--warning); padding: 0.8rem; border-radius: 4px; margin-top: 0.6rem; font-size: 0.9rem; }}

table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.9rem; }}
th, td {{ padding: 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: var(--surface-alt); color: var(--primary); font-weight: 600; }}
tr:hover {{ background: rgba(255,255,255,0.02); }}
a {{ color: var(--primary); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1>{html.escape(meta.get('title', 'Fact Check Report'))}</h1>
    <div class="meta">
      <strong>{'Статус' if is_bg else 'Status'}:</strong> {html.escape(str(meta.get('status', 'partial')).upper())} |
      <strong>{'Дата' if is_bg else 'Date'}:</strong> {html.escape(str(meta.get('analysis_date', '')))} |
      <strong>{'Файл' if is_bg else 'Source'}:</strong> {html.escape(meta.get('original_source_path', ''))}
    </div>
    {f'<div class="annot-comment"><strong>{"Демонстрационни данни: присъдите не са независимо потвърдени." if is_bg else "Demonstration data: verdicts have not been independently verified."}</strong></div>' if meta.get('demo_data') else ''}
    {''.join(f'<div class="annot-comment">{html.escape(str(item))}</div>' for item in limitations + unchecked_items)}
    <div class="stats-grid">
      <div class="stat-card">
        <div class="val score-badge">{score_label(rel_score)}</div>
        <div class="lbl">{'Претеглен одитен индекс' if is_bg else 'Weighted audit index'}</div>
      </div>
      <div class="stat-card">
        <div class="val">{len(checkable_claims)}</div>
        <div class="lbl">{'Проверими твърдения' if is_bg else 'Checkable Claims'}</div>
      </div>
      <div class="stat-card">
        <div class="val" style="color: {'#10b981' if len(problem_claims)==0 else '#ef4444'};">{len(problem_claims)}</div>
        <div class="lbl">{'Твърдения за преглед' if is_bg else 'Claims needing review'}</div>
      </div>
      <div class="stat-card">
        <div class="val">{len(sources)}</div>
        <div class="lbl">{'Регистрирани източници' if is_bg else 'Recorded sources'}</div>
      </div>
    </div>
  </div>

  <div class="section-title">
    <span>{'Твърдения за преглед' if is_bg else 'Claims needing review'} ({len(problem_claims)})</span>
    <div class="filters">
      <button class="btn active" onclick="filterClaims(this, 'all')">{'Всички' if is_bg else 'All'}</button>
      <button class="btn" onclick="filterClaims(this, 'CONTRADICTED')">{'Опровергани' if is_bg else 'Contradicted'}</button>
      <button class="btn" onclick="filterClaims(this, 'PARTLY_SUPPORTED')">{'Частично доказани' if is_bg else 'Partly Supported'}</button>
      <button class="btn" onclick="filterClaims(this, 'OUTDATED')">{'Остарели' if is_bg else 'Outdated'}</button>
    </div>
  </div>

  <div id="problem-claims-list">
"""
    for c in problem_claims:
        cid = str(c.get("id"))
        v = str(c.get("verdict", ""))
        r = str(c.get("risk", "medium"))
        recom = c.get("recommended_edit", "")
        rationale = c.get("rationale", "")
        evidence_items = []
        for el in c.get("evidence_links", []):
            s = source_by_id.get(str(el.get("source_id")))
            if s:
                s_url = safe_html_url(s.get("url") or s.get("source_uri"))
                evidence_items.append(f"<a href='{s_url}' target='_blank' rel='noopener noreferrer'><b>[{html.escape(str(s.get('id')))}]</b> {html.escape(str(s.get('title','')))}</a>: {html.escape(str(el.get('finding','')))} (<i>{html.escape(str(el.get('locator','')))}</i>)")

        html_content += f"""
    <div class="card claim-item" data-verdict="{v}">
      <div class="card-header">
        <div>
          <span style="font-weight: 700; font-size: 1.1rem; color: var(--primary);">{cid}</span>
          <span style="color: var(--text-muted); font-size: 0.85rem; margin-left: 0.5rem;">({', '.join(c.get('paragraph_ids', []))})</span>
        </div>
        <div>
          <span class="badge" style="background: {risk_badge_color(r)};">{r}</span>
          <span class="badge" style="background: {verdict_badge_color(v)};">{label(v, language)}</span>
        </div>
      </div>
      <div class="original-text">"{html.escape(c.get('original_text',''))}"</div>
      <p style="margin: 0.5rem 0;"><strong>{'Констатация' if is_bg else 'Finding'}:</strong> {html.escape(rationale)}</p>
      {f'<div class="edit-box"><strong>{"Препоръчана редакция" if is_bg else "Recommended Edit"}:</strong> {html.escape(recom)}</div>' if recom else ''}
      {f'<div style="font-size: 0.85rem; margin-top: 0.5rem;"><strong>{"Доказателства" if is_bg else "Evidence"}:</strong><ul style="margin-left: 1.2rem; margin-top: 0.2rem;">{"".join(f"<li>{item}</li>" for item in evidence_items)}</ul></div>' if evidence_items else ''}
    </div>
"""

    html_content += f"""
  </div>

  <div class="section-title">{'Анотиран текст на статията' if is_bg else 'Annotated Article View'}</div>
  <div class="article-view">
"""
    for p in paragraphs:
        pid = p.get("id")
        text = p.get("text", "")
        comments = []
        for cid in p.get("claim_ids", []):
            c = claim_by_id.get(str(cid))
            if c and c.get("verdict") in ANNOTATION_VERDICTS:
                v_label = label(str(c.get("verdict")), language)
                recom = c.get("recommended_edit", "")
                comm = c.get("editor_comment") or c.get("rationale") or ""
                comments.append(f"<strong>[{cid} - {v_label}]:</strong> {html.escape(comm)} " + (f"<b>{'Препоръка' if is_bg else 'Recommendation'}:</b> {html.escape(recom)}" if recom else ""))

        html_content += f"""
    <div class="p-block">
      <div class="p-id">{pid}</div>
      <div>{html.escape(text)}</div>
      {"".join(f'<div class="annot-comment">{c_html}</div>' for c_html in comments)}
    </div>
"""

    html_content += f"""
  </div>

  <div class="section-title">{'Одит на източниците' if is_bg else 'Source Register'} ({len(sources)})</div>
  <div style="overflow-x: auto;">
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>{'Заглавие / Връзка' if is_bg else 'Title / Link'}</th>
          <th>{'Тип' if is_bg else 'Type'}</th>
          <th>{'Достъп' if is_bg else 'Access'}</th>
          <th>{'Организация' if is_bg else 'Author/Org'}</th>
          <th>{'Принос' if is_bg else 'Contribution'}</th>
        </tr>
      </thead>
      <tbody>
"""
    for s in sources:
        s_url = safe_html_url(s.get("url") or s.get("source_uri"))
        html_content += f"""
        <tr>
          <td><b>{html.escape(str(s.get('id','')))}</b></td>
          <td><a href="{s_url}" target="_blank" rel="noopener noreferrer">{html.escape(str(s.get('title','')))}</a></td>
          <td>{html.escape(str(s.get('source_type','')))}</td>
          <td>{html.escape(str(s.get('access_status','')))}</td>
          <td>{html.escape(str(s.get('author_or_org','')))}</td>
          <td>{html.escape(str(s.get('contribution','')))}</td>
        </tr>
"""

    html_content += """
      </tbody>
    </table>
  </div>

</div>

<script>
function filterClaims(btn, verdict) {
  document.querySelectorAll('.filters .btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.querySelectorAll('.claim-item').forEach(card => {
    if (verdict === 'all' || card.getAttribute('data-verdict') === verdict) {
      card.style.display = 'block';
    } else {
      card.style.display = 'none';
    }
  });
}
</script>
</body>
</html>
"""
    return html_content


def render_command(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit).resolve()
    out_dir = Path(args.out_dir).resolve()
    audit = load_audit(audit_path)
    errors, warnings = validate_audit(audit)
    errors.extend(validate_frozen_input(audit, audit_path))
    if errors:
        print("Audit validation failed; render cancelled.", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    metadata = audit["metadata"]
    language = str(metadata.get("language", "bg"))
    is_bg = language.lower().startswith("bg")
    paragraphs = audit.get("paragraphs", [])
    claims = audit.get("claims", [])
    sources = audit.get("sources", [])
    searches = audit.get("searches", [])
    source_by_id = {str(row.get("id")): row for row in sources}
    claim_by_id = {str(row.get("id")): row for row in claims}

    verdict_counts = Counter(str(claim.get("verdict", "")) for claim in claims)
    checkable_claims = [claim for claim in claims if claim.get("verdict") != "NOT_CHECKABLE"]
    researched_claims = [claim for claim in checkable_claims if claim.get("verdict") != "NOT_RESEARCHED"]
    not_researched = [claim for claim in checkable_claims if claim.get("verdict") == "NOT_RESEARCHED"]
    problem_claims = [claim for claim in claims if claim.get("verdict") in PROBLEM_VERDICTS]
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    problem_claims.sort(key=lambda row: (risk_order.get(str(row.get("risk")), 9), str(row.get("id"))))

    rel = audit_reliability_score(audit)

    annotated: list[str] = []
    if metadata.get("demo_data"):
        annotated.extend([
            "> **Демонстрационни данни: присъдите не са независимо потвърдени.**" if is_bg
            else "> **Demonstration data: verdicts have not been independently verified.**",
            "",
        ])
    elif metadata.get("status") != "complete":
        annotated.extend(["> **Частичен одит.**" if is_bg else "> **Partial audit.**", ""])
    for paragraph in paragraphs:
        annotated.append(str(paragraph.get("text", "")))
        for cid in paragraph.get("claim_ids", []):
            claim = claim_by_id.get(str(cid))
            if not claim or claim.get("verdict") not in ANNOTATION_VERDICTS:
                continue
            evidence = []
            for link_item in claim.get("evidence_links", []):
                source = source_by_id.get(str(link_item.get("source_id")))
                if source:
                    rendered = source_link(source)
                    if rendered not in evidence:
                        evidence.append(rendered)
            comment = str(claim.get("editor_comment") or claim.get("rationale") or "").strip()
            recommendation = str(claim.get("recommended_edit") or "").strip()
            if is_bg:
                line = f"> **Фактологичен коментар -- {cid} ({label(str(claim.get('verdict')), language)}):** {comment}"
                if recommendation:
                    line += f" **Препоръчана редакция:** {recommendation}"
                if evidence:
                    line += f" **Доказателства:** {', '.join(evidence)}."
            else:
                line = f"> **Factual comment -- {cid} ({label(str(claim.get('verdict')), language)}):** {comment}"
                if recommendation:
                    line += f" **Recommended edit:** {recommendation}"
                if evidence:
                    line += f" **Evidence:** {', '.join(evidence)}."
            annotated.extend(["", line])
        annotated.append("")
    atomic_write_text(out_dir / "annotated-article.md", "\n".join(annotated).rstrip() + "\n")

    if is_bg:
        report = [
            f"# Фактологична проверка: {metadata.get('title', '')}",
            "",
            "## Кратък резултат",
            "",
            f"- Статус: **{'завършена' if metadata.get('status') == 'complete' else 'частична'} проверка**",
            f"- Дата на проверката: {metadata.get('analysis_date', '')}",
            f"- **Претеглен одитен индекс:** **{score_label(rel)}** ({rel['grade']}; оценка по зададени тегла, не вероятност за истинност)",
            f"- Проверими твърдения: {len(checkable_claims)}",
            f"- Проучени твърдения: {len(researched_claims)}",
            f"- Непроверени твърдения: {len(not_researched)}",
            f"- Пасажи с фактологични коментари: {len({pid for c in claims if c.get('verdict') in ANNOTATION_VERDICTS for pid in c.get('paragraph_ids', [])})}",
            "",
            "| Присъда | Брой |",
            "|---|---:|",
        ]
    else:
        report = [
            f"# Factual audit: {metadata.get('title', '')}",
            "",
            "## Summary",
            "",
            f"- Status: **{metadata.get('status', 'partial')} audit**",
            f"- Audit date: {metadata.get('analysis_date', '')}",
            f"- **Weighted audit index:** **{score_label(rel)}** ({rel['grade']}; rule-based index, not a probability of truth)",
            f"- Checkable claims: {len(checkable_claims)}",
            f"- Researched claims: {len(researched_claims)}",
            f"- Not researched: {len(not_researched)}",
            f"- Paragraphs with factual comments: {len({pid for c in claims if c.get('verdict') in ANNOTATION_VERDICTS for pid in c.get('paragraph_ids', [])})}",
            "",
            "| Verdict | Count |",
            "|---|---:|",
        ]
    for verdict in sorted(VERDICTS, key=lambda item: (0 if item == "SUPPORTED" else 1, item)):
        if verdict_counts.get(verdict):
            report.append(f"| {label(verdict, language)} | {verdict_counts[verdict]} |")

    if metadata.get("demo_data") or audit.get("limitations") or audit.get("unchecked_items"):
        notices = []
        if metadata.get("demo_data"):
            notices.append("Демонстрационни данни: присъдите не са независимо потвърдени." if is_bg else "Demonstration data: verdicts have not been independently verified.")
        notices.extend(str(item) for item in audit.get("limitations", []))
        notices.extend(str(item) for item in audit.get("unchecked_items", []))
        report.extend(["", "## " + ("Ограничения и непроверени въпроси" if is_bg else "Limitations and unchecked items"), ""])
        report.extend(f"- {item}" for item in notices)
    report.extend(["", "## " + ("Твърдения за преглед, редакция или уточнение" if is_bg else "Claims needing review, correction, or qualification"), ""])
    if not problem_claims:
        report.append("Няма установени проблемни твърдения." if is_bg else "No problematic claims were identified.")
    for claim in problem_claims:
        cid = str(claim.get("id"))
        report.extend(
            [
                f"### {cid} -- {label(str(claim.get('verdict')), language)}",
                "",
                f"- **{'Място' if is_bg else 'Location'}:** {', '.join(claim.get('paragraph_ids', []))}",
                f"- **{'Оригинал' if is_bg else 'Original'}:** {claim.get('original_text', '')}",
                f"- **{'Извод' if is_bg else 'Finding'}:** {claim.get('rationale', '')}",
                f"- **{'Препоръчана редакция' if is_bg else 'Recommended edit'}:** {claim.get('recommended_edit') or ('Няма безопасна конкретна редакция.' if is_bg else 'No safe exact edit is available.')}",
                "",
            ]
        )
        linked = []
        for item in claim.get("evidence_links", []):
            source = source_by_id.get(str(item.get("source_id")))
            if source:
                linked.append(f"{source_link(source)} -- {item.get('finding', '')} ({item.get('locator', '')})")
        if linked:
            report.append("**Доказателства:**" if is_bg else "**Evidence:**")
            report.append("")
            report.extend(f"- {item}" for item in linked)
            report.append("")

    report.extend(
        [
            "## " + ("Пълен регистър на твърденията" if is_bg else "Complete claim ledger"),
            "",
            "| ID | "
            + ("Място | Твърдение | Присъда | Увереност | Риск | Източници | Препоръка |" if is_bg else "Location | Claim | Verdict | Confidence | Risk | Sources | Recommendation |"),
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for claim in claims:
        source_ids_for_claim = sorted({str(item.get("source_id")) for item in claim.get("evidence_links", []) if item.get("source_id")})
        report.append(
            "| {id} | {location} | {claim} | {verdict} | {confidence} | {risk} | {sources} | {edit} |".format(
                id=md_escape(claim.get("id")),
                location=md_escape(", ".join(claim.get("paragraph_ids", []))),
                claim=md_escape(claim.get("original_text")),
                verdict=md_escape(label(str(claim.get("verdict")), language)),
                confidence=md_escape(claim.get("confidence")),
                risk=md_escape(claim.get("risk")),
                sources=md_escape(", ".join(source_ids_for_claim)),
                edit=md_escape(claim.get("recommended_edit")),
            )
        )

    atomic_write_text(out_dir / "fact-check-report.md", "\n".join(report).rstrip() + "\n")

    html_page = generate_html_report(audit, rel)
    atomic_write_text(out_dir / "report.html", html_page)

    claim_rows = []
    for claim in claims:
        claim_rows.append(
            {
                "id": claim.get("id", ""),
                "paragraph_ids": ";".join(claim.get("paragraph_ids", [])),
                "original_text": claim.get("original_text", ""),
                "normalized_claim": claim.get("normalized_claim", ""),
                "claim_types": ";".join(claim.get("claim_types", [])),
                "risk": claim.get("risk", ""),
                "verdict": claim.get("verdict", ""),
                "confidence": claim.get("confidence", ""),
                "research_complete": claim.get("research_complete", False),
                "rationale": claim.get("rationale", ""),
                "source_ids": ";".join(sorted({str(item.get("source_id")) for item in claim.get("evidence_links", []) if item.get("source_id")})),
                "search_ids": ";".join(claim.get("search_ids", [])),
                "recommended_edit": claim.get("recommended_edit", ""),
                "editor_comment": claim.get("editor_comment", ""),
                "notes": claim.get("notes", ""),
                "audit_status": metadata.get("status", "partial"),
                "demo_data": bool(metadata.get("demo_data", False)),
            }
        )
    claim_fields = [
        "id",
        "paragraph_ids",
        "original_text",
        "normalized_claim",
        "claim_types",
        "risk",
        "verdict",
        "confidence",
        "research_complete",
        "rationale",
        "source_ids",
        "search_ids",
        "recommended_edit",
        "editor_comment",
        "notes",
        "audit_status",
        "demo_data",
    ]
    atomic_write_csv(out_dir / "claims.csv", claim_rows, claim_fields)

    source_fields = [
        "id",
        "title",
        "url",
        "source_uri",
        "author_or_org",
        "publication_date",
        "access_date",
        "source_type",
        "source_tier",
        "access_status",
        "origin_cluster",
        "supplied_by_author",
        "contribution",
        "limitations",
        "audit_status",
        "demo_data",
    ]
    source_rows = [{**row, "audit_status": metadata.get("status", "partial"), "demo_data": bool(metadata.get("demo_data", False))} for row in sources]
    atomic_write_csv(out_dir / "sources.csv", source_rows, source_fields)
    search_fields = ["id", "claim_ids", "query", "kind", "language", "run_date", "surface", "status", "result_note", "audit_status", "demo_data"]
    search_rows = [{**row, "claim_ids": ";".join(row.get("claim_ids", [])), "audit_status": metadata.get("status", "partial"), "demo_data": bool(metadata.get("demo_data", False))} for row in searches]
    atomic_write_csv(out_dir / "searches.csv", search_rows, search_fields)
    atomic_write_json(
        out_dir / "validation.json",
        {"valid": True, "errors": [], "warnings": warnings, "audit_status": metadata.get("status", "partial"), "demo_data": bool(metadata.get("demo_data", False)), "limitations": audit.get("limitations", []), "unchecked_items": audit.get("unchecked_items", []), "reliability_score": rel, "rendered_on": date.today().isoformat()},
    )
    print(f"[OK] Rendered outputs in {out_dir}")
    print(f"  - report.html (Interactive Dashboard with Filters)")
    print(f"  - annotated-article.md")
    print(f"  - fact-check-report.md")
    print(f"  - claims.csv, sources.csv, searches.csv")
    print(f"[OK] Weighted audit index: {score_label(rel)} ({rel['grade']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Freeze and initialize an article (TXT, MD, HTML, DOCX, PDF, or URL)")
    init_parser.add_argument("article", help="Path to file or web URL (http/https)")
    init_parser.add_argument("--out", required=True)
    init_parser.add_argument("--language", default="bg", choices=["bg", "en"])
    init_parser.add_argument("--title", default="")
    init_parser.add_argument("--article-reference-date", default="")
    init_parser.add_argument("--encoding", default="", help="Explicit text encoding for local TXT/MD/HTML (for example cp1251 or cp1252)")
    init_parser.set_defaults(func=initialize)

    status_parser = subparsers.add_parser("status", help="Quick progress and score overview")
    status_parser.add_argument("audit")
    status_parser.set_defaults(func=status_command)

    markers_parser = subparsers.add_parser("extract-markers", help="Scan paragraphs for numbers, dates, quotes, and superlatives")
    markers_parser.add_argument("audit")
    markers_parser.add_argument("--json-out", default="")
    markers_parser.set_defaults(func=extract_markers_command)

    add_c_parser = subparsers.add_parser("add-claim", help="Add an atomic claim to audit.json")
    add_c_parser.add_argument("audit")
    add_c_parser.add_argument("--id", default="")
    add_c_parser.add_argument("--paragraphs", required=True, help="Comma separated paragraph IDs (e.g. P001,P002)")
    add_c_parser.add_argument("--text", required=True, help="Exact passage text")
    add_c_parser.add_argument("--normalized", default="")
    add_c_parser.add_argument("--types", default="factual")
    add_c_parser.add_argument("--risk", default="medium", choices=["low", "medium", "high", "critical"])
    add_c_parser.add_argument("--verdict", default="NOT_RESEARCHED")
    add_c_parser.add_argument("--rationale", default="")
    add_c_parser.add_argument("--recommended-edit", default="")
    add_c_parser.add_argument("--editor-comment", default="")
    add_c_parser.set_defaults(func=add_claim_command)

    up_c_parser = subparsers.add_parser("update-claim", help="Update claim verdict, evidence, and rationale")
    up_c_parser.add_argument("audit")
    up_c_parser.add_argument("--id", required=True)
    up_c_parser.add_argument("--verdict", choices=list(VERDICTS))
    up_c_parser.add_argument("--confidence", choices=list(CONFIDENCE))
    up_c_parser.add_argument("--rationale")
    up_c_parser.add_argument("--recommended-edit")
    up_c_parser.add_argument("--editor-comment")
    up_c_parser.add_argument("--notes")
    up_c_parser.add_argument("--source-id")
    up_c_parser.add_argument("--stance", choices=list(STANCES))
    up_c_parser.add_argument("--locator")
    up_c_parser.add_argument("--finding")
    up_c_parser.add_argument("--search-id")
    up_c_parser.set_defaults(func=update_claim_command)

    add_s_parser = subparsers.add_parser("add-source", help="Add or update an inspected source")
    add_s_parser.add_argument("audit")
    add_s_parser.add_argument("--id", default="")
    add_s_parser.add_argument("--title", required=True)
    add_s_parser.add_argument("--url", default="")
    add_s_parser.add_argument("--source-uri", default="")
    add_s_parser.add_argument("--author-org", default="")
    add_s_parser.add_argument("--pub-date", default="")
    add_s_parser.add_argument("--access-date", default="")
    add_s_parser.add_argument("--source-type", default="report")
    add_s_parser.add_argument("--source-tier", default="limited")
    add_s_parser.add_argument("--access-status", default="not_inspected")
    add_s_parser.add_argument("--origin-cluster", default="")
    add_s_parser.add_argument("--supplied", action="store_true")
    add_s_parser.add_argument("--contribution", default="")
    add_s_parser.add_argument("--limitations", default="")
    add_s_parser.set_defaults(func=add_source_command)

    add_q_parser = subparsers.add_parser("add-search", help="Add a search attempt record")
    add_q_parser.add_argument("audit")
    add_q_parser.add_argument("--id", default="")
    add_q_parser.add_argument("--claim-ids", required=True)
    add_q_parser.add_argument("--query", required=True)
    add_q_parser.add_argument("--kind", default="primary", choices=list(QUERY_KINDS))
    add_q_parser.add_argument("--language", default="bg")
    add_q_parser.add_argument("--run-date", default="")
    add_q_parser.add_argument("--surface", default="web")
    add_q_parser.add_argument("--status", default="not_run", choices=list(SEARCH_STATUSES))
    add_q_parser.add_argument("--result-note", default="")
    add_q_parser.set_defaults(func=add_search_command)

    merge_parser = subparsers.add_parser("merge-batch", help="Merge a subagent batch JSON into main audit.json")
    merge_parser.add_argument("audit")
    merge_parser.add_argument("--batch", required=True)
    merge_parser.set_defaults(func=merge_batch_command)

    val_parser = subparsers.add_parser("validate", help="Validate audit completeness and integrity")
    val_parser.add_argument("audit")
    val_parser.add_argument("--json-out", default="")
    val_parser.set_defaults(func=validate_command)

    ren_parser = subparsers.add_parser("render", help="Render HTML dashboard, Markdown reports, and CSV registers")
    ren_parser.add_argument("audit")
    ren_parser.add_argument("--out-dir", required=True)
    ren_parser.set_defaults(func=render_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
