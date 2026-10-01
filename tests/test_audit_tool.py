"""Unit tests for the article-fact-check audit tool."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from io import BytesIO
from email.message import Message
from pathlib import Path

from article_fact_check.audit_tool import (
    build_parser,
    calculate_reliability_score,
    validate_audit,
    load_audit,
    atomic_write_json,
    atomic_write_text,
    audit_reliability_score,
    sha256_file,
    validate_frozen_input,
    generate_html_report,
    safe_html_url,
    fetch_url_text,
    read_text,
)


class TestReliabilityScore(unittest.TestCase):
    def test_empty_claims(self):
        result = calculate_reliability_score([])
        self.assertIsNone(result["score"])

    def test_unresearched_claim_has_no_score(self):
        result = calculate_reliability_score([
            {"id": "C0001", "risk": "high", "verdict": "SUPPORTED"},
            {"id": "C0002", "risk": "high", "verdict": "NOT_RESEARCHED"},
        ])
        self.assertIsNone(result["score"])

    def test_all_supported(self):
        claims = [
            {"id": "C0001", "risk": "high", "verdict": "SUPPORTED"},
            {"id": "C0002", "risk": "medium", "verdict": "SUPPORTED"},
        ]
        result = calculate_reliability_score(claims)
        self.assertEqual(result["score"], 100.0)
        self.assertIn("Отлична", result["grade"])

    def test_contradicted_penalty(self):
        claims = [
            {"id": "C0001", "risk": "critical", "verdict": "CONTRADICTED"},  # 4.0 * 1.0 = 4.0
            {"id": "C0002", "risk": "critical", "verdict": "SUPPORTED"},     # 4.0 * 0.0 = 0.0
        ]
        # Total weight = 8.0, penalties = 4.0 -> score = 100 * (1 - 4/8) = 50.0%
        result = calculate_reliability_score(claims)
        self.assertEqual(result["score"], 50.0)
        self.assertIn("Ниска", result["grade"])

    def test_partly_supported(self):
        claims = [
            {"id": "C0001", "risk": "medium", "verdict": "PARTLY_SUPPORTED"},  # 1.0 * 0.5 = 0.5
            {"id": "C0002", "risk": "medium", "verdict": "SUPPORTED"},         # 1.0 * 0.0 = 0.0
        ]
        # Total weight = 2.0, penalties = 0.5 -> score = 100 * (1 - 0.5/2) = 75.0%
        result = calculate_reliability_score(claims)
        self.assertEqual(result["score"], 75.0)


    def test_english_grade_labels(self):
        claims = [
            {"id": "C0001", "risk": "high", "verdict": "SUPPORTED"},
            {"id": "C0002", "risk": "medium", "verdict": "SUPPORTED"},
        ]
        result = calculate_reliability_score(claims, language="en")
        self.assertEqual(result["score"], 100.0)
        self.assertIn("Excellent", result["grade"])
        self.assertIn("No checkable", calculate_reliability_score([], language="en")["grade"])


class TestAuditWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.parser = build_parser()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_audit_lifecycle(self):
        # 1. Create a sample article
        article_text = (
            "# Заглавие на тестовата статия\n\n"
            "През 2024 г. производството на зелен водород в ЕС нарасна с 40% спрямо 2023 г.\n\n"
            "Според Европейската комисия това е най-големият скок в историята на сектора.\n"
        )
        article_path = self.temp_dir / "article.md"
        article_path.write_text(article_text, encoding="utf-8")

        audit_dir = self.temp_dir / "audit_run"

        # 2. Test 'init'
        init_args = self.parser.parse_args(["init", str(article_path), "--out", str(audit_dir), "--language", "bg"])
        ret = init_args.func(init_args)
        self.assertEqual(ret, 0)

        audit_json_path = audit_dir / "audit.json"
        self.assertTrue(audit_json_path.is_file())
        audit = load_audit(audit_json_path)
        self.assertEqual(len(audit["paragraphs"]), 3)
        self.assertEqual(audit["paragraphs"][0]["id"], "P001")
        self.assertEqual(audit["paragraphs"][0]["type"], "heading")

        # 3. Test 'extract-markers'
        markers_args = self.parser.parse_args(["extract-markers", str(audit_json_path)])
        ret = markers_args.func(markers_args)
        self.assertEqual(ret, 0)

        # 4. Test 'add-source'
        source_args = self.parser.parse_args([
            "add-source", str(audit_json_path),
            "--id", "S001",
            "--title", "Eurostat Energy Report 2024",
            "--url", "https://ec.europa.eu/eurostat/energy",
            "--author-org", "Eurostat",
            "--source-type", "report",
            "--source-tier", "primary",
            "--access-status", "full_text",
            "--pub-date", "2024-05-01",
            "--contribution", "Official energy statistics for EU member states"
        ])
        ret = source_args.func(source_args)
        self.assertEqual(ret, 0)

        # 5. Test 'add-search'
        search_args = self.parser.parse_args([
            "add-search", str(audit_json_path),
            "--id", "Q001",
            "--claim-ids", "C0001",
            "--query", "Eurostat 2024 green hydrogen production growth",
            "--kind", "primary",
            "--language", "en",
            "--status", "completed"
        ])
        ret = search_args.func(search_args)
        self.assertEqual(ret, 0)

        # 6. Test 'add-claim'
        claim_args = self.parser.parse_args([
            "add-claim", str(audit_json_path),
            "--id", "C0001",
            "--paragraphs", "P002",
            "--text", "производството на зелен водород в ЕС нарасна с 40%",
            "--normalized", "Производството на чист водород в ЕС през 2024 г. бележи ръст от 40%",
            "--risk", "high",
            "--verdict", "SUPPORTED",
            "--rationale", "Потвърдено от данните на Евростат.",
        ])
        ret = claim_args.func(claim_args)
        self.assertEqual(ret, 0)

        # 7. Test 'update-claim' (linking evidence)
        update_args = self.parser.parse_args([
            "update-claim", str(audit_json_path),
            "--id", "C0001",
            "--verdict", "SUPPORTED",
            "--confidence", "high",
            "--source-id", "S001",
            "--stance", "supports",
            "--locator", "Table 4, p. 12",
            "--finding", "Измерен ръст от 40.2% през 2024 г."
        ])
        ret = update_args.func(update_args)
        self.assertEqual(ret, 0)

        # Mark exclusion on P001 (heading) and add claim for P003
        audit = load_audit(audit_json_path)
        audit["paragraphs"][0]["exclusion"] = "heading_or_navigation"
        audit["metadata"]["decomposition_complete"] = True
        audit["metadata"]["omission_pass_complete"] = True
        audit["metadata"]["supplied_sources_audited"] = True
        audit["metadata"]["status"] = "complete"

        # Also add claim C0002 for P003 as PARTLY_SUPPORTED
        audit["claims"].append({
            "id": "C0002",
            "paragraph_ids": ["P003"],
            "original_text": "най-големият скок в историята на сектора",
            "normalized_claim": "Ръстът през 2024 г. е най-големият исторически скок за сектора",
            "claim_types": ["superlative"],
            "risk": "medium",
            "verdict": "PARTLY_SUPPORTED",
            "confidence": "high",
            "research_complete": True,
            "rationale": "Ръстът е рекорден в абсолютни обеми, но през 2021 г. процентният скок беше 45%.",
            "recommended_edit": "Заменете „най-големият скок“ с „най-големият ръст в абсолютен обем“.",
            "editor_comment": "Процентният ръст е бил по-висок през 2021 г.",
            "evidence_links": [
                {
                    "source_id": "S001",
                    "stance": "supports",
                    "locator": "стр. 15",
                    "finding": "През 2021 г. процентният скок е 45%, докато през 2024 г. е 40%."
                }
            ],
            "search_ids": [],
            "notes": ""
        })
        audit["paragraphs"][2]["claim_ids"] = ["C0002"]
        atomic_write_json(audit_json_path, audit)

        # 8. Test 'validate'
        errors, warnings = validate_audit(load_audit(audit_json_path))
        self.assertEqual(errors, [])

        val_args = self.parser.parse_args(["validate", str(audit_json_path)])
        ret = val_args.func(val_args)
        self.assertEqual(ret, 0)

        # 9. Test 'render'
        out_render_dir = audit_dir / "output"
        render_args = self.parser.parse_args(["render", str(audit_json_path), "--out-dir", str(out_render_dir)])
        ret = render_args.func(render_args)
        self.assertEqual(ret, 0)

        # Check rendered artifacts
        self.assertTrue((out_render_dir / "report.html").is_file())
        self.assertTrue((out_render_dir / "annotated-article.md").is_file())
        self.assertTrue((out_render_dir / "fact-check-report.md").is_file())
        self.assertTrue((out_render_dir / "claims.csv").is_file())
        self.assertTrue((out_render_dir / "sources.csv").is_file())
        self.assertTrue((out_render_dir / "searches.csv").is_file())
        self.assertTrue((out_render_dir / "validation.json").is_file())

        html_content = (out_render_dir / "report.html").read_text(encoding="utf-8")
        self.assertIn("C0002", html_content)
        self.assertIn("Eurostat Energy Report 2024", html_content)
        self.assertIn("filterClaims", html_content)

        report_md = (out_render_dir / "fact-check-report.md").read_text(encoding="utf-8")
        self.assertIn("C0001", report_md)
        self.assertIn("C0002", report_md)
        self.assertIn("audit_status,demo_data", (out_render_dir / "claims.csv").read_text(encoding="utf-8"))

        broken = load_audit(audit_json_path)
        broken["claims"][0]["evidence_links"][0]["source_id"] = "S999"
        broken["claims"][0]["original_text"] = "Несъществуващ пасаж"
        broken["metadata"]["omission_pass_complete"] = False
        errors, _ = validate_audit(broken)
        self.assertTrue(any("unknown source ID S999" in error for error in errors))
        self.assertTrue(any("original_text is not present" in error for error in errors))
        self.assertTrue(any("omission_pass_complete" in error for error in errors))

    def test_cp1251_input_hash_matches_frozen_file(self):
        article = self.temp_dir / "article.txt"
        article.write_bytes("Това е проверим факт.".encode("cp1251"))
        out = self.temp_dir / "encoded"
        args = self.parser.parse_args(["init", str(article), "--out", str(out)])
        self.assertEqual(args.func(args), 0)
        audit = load_audit(out / "audit.json")
        self.assertEqual(audit["metadata"]["document_sha256"], sha256_file(out / "frozen-article.txt"))
        self.assertEqual(audit["metadata"]["original_document_sha256"], sha256_file(article))
        self.assertEqual(validate_frozen_input(audit, out / "audit.json"), [])

    def test_explicit_encoding_and_utf16_bom(self):
        article = self.temp_dir / "legacy.txt"
        article.write_bytes("Café".encode("cp1252"))
        self.assertEqual(read_text(article, language="en"), "Café")
        self.assertEqual(read_text(article, encoding="cp1252"), "Café")
        article.write_bytes("Текст".encode("utf-16"))
        self.assertEqual(read_text(article), "Текст")

    def test_cli_defaults_do_not_claim_evidence_was_inspected(self):
        article = self.temp_dir / "article.md"
        article.write_text("Checkable claim.", encoding="utf-8")
        out = self.temp_dir / "defaults"
        init = self.parser.parse_args(["init", str(article), "--out", str(out), "--language", "en"])
        self.assertEqual(init.func(init), 0)
        audit_path = out / "audit.json"
        source = self.parser.parse_args(["add-source", str(audit_path), "--title", "Unopened record", "--url", "https://example.org"])
        self.assertEqual(source.func(source), 0)
        audit = load_audit(audit_path)
        self.assertEqual(audit["sources"][0]["access_status"], "not_inspected")
        self.assertEqual(audit["sources"][0]["source_tier"], "limited")

    def test_incomplete_audit_cannot_claim_final_score(self):
        audit = {
            "metadata": {"status": "partial"},
            "claims": [{"id": "C0001", "risk": "high", "verdict": "SUPPORTED"}],
        }
        self.assertIsNone(audit_reliability_score(audit)["score"])

        article = self.temp_dir / "draft.md"
        article.write_text("A claim awaiting research.", encoding="utf-8")
        out = self.temp_dir / "partial"
        init = self.parser.parse_args(["init", str(article), "--out", str(out), "--language", "en"])
        self.assertEqual(init.func(init), 0)
        render = self.parser.parse_args(["render", str(out / "audit.json"), "--out-dir", str(out / "output")])
        self.assertEqual(render.func(render), 0)
        report = (out / "output" / "fact-check-report.md").read_text(encoding="utf-8")
        self.assertIn("**N/A**", report)
        self.assertNotIn("100.0%", report)
        self.assertIn("Partial audit", (out / "output" / "annotated-article.md").read_text(encoding="utf-8"))

    def test_html_links_reject_script_scheme_and_escape_attributes(self):
        self.assertEqual(safe_html_url("javascript:alert(1)"), "#")
        self.assertEqual(safe_html_url('https://example.org/?q="x"'), "https://example.org/?q=&quot;x&quot;")
        page = generate_html_report(
            {"metadata": {"title": "Test", "status": "partial", "language": "en"},
             "claims": [], "paragraphs": [],
             "sources": [{"id": "S001", "title": "Test", "url": "javascript:alert(1)", "source_type": "report", "access_status": "full_text"}]},
            {"score": None, "grade": "Partial"},
        )
        self.assertNotIn("javascript:alert(1)", page)
        self.assertIn("N/A", page)

    def test_plain_text_url_is_read_without_html_parser(self):
        headers = Message()
        headers.add_header("Content-Type", "text/plain; charset=utf-8")
        class Response(BytesIO):
            pass
        response = Response("Факт от URL".encode("utf-8"))
        response.headers = headers
        with patch("article_fact_check.audit_tool.urllib.request.urlopen", return_value=response):
            text, title = fetch_url_text("https://example.org/article.txt")
        self.assertEqual(text, "Факт от URL")
        self.assertEqual(title, "")

    def test_merge_batch(self):
        audit_dir = self.temp_dir / "audit_merge"
        audit_dir.mkdir(parents=True)
        audit_json_path = audit_dir / "audit.json"

        # Base audit
        base_audit = {
            "schema_version": "2.0",
            "metadata": {
                "title": "Merge Test",
                "source_path": "frozen.txt",
                "document_sha256": "abc",
                "language": "bg",
                "analysis_date": "2026-09-29",
                "scope": "exhaustive",
                "status": "partial",
                "decomposition_complete": False,
            },
            "paragraphs": [
                {"id": "P001", "type": "paragraph", "locator": "block:1", "text": "Текст 1", "claim_ids": [], "exclusion": ""}
            ],
            "claims": [],
            "sources": [],
            "searches": []
        }
        atomic_write_json(audit_json_path, base_audit)

        # Batch JSON
        batch_path = self.temp_dir / "batch_01.json"
        batch_data = {
            "claims": [
                {
                    "id": "C0001",
                    "paragraph_ids": ["P001"],
                    "original_text": "Текст 1",
                    "normalized_claim": "Нормализиран текст 1",
                    "claim_types": ["factual"],
                    "risk": "medium",
                    "verdict": "SUPPORTED",
                    "confidence": "high",
                    "research_complete": True,
                    "rationale": "Доказано от източник.",
                    "evidence_links": [{"source_id": "S001", "stance": "supports", "locator": "стр. 1", "finding": "Факт"}],
                    "search_ids": ["Q001"],
                    "recommended_edit": "",
                    "editor_comment": "",
                    "notes": ""
                }
            ],
            "sources": [
                {
                    "id": "S001",
                    "title": "Batch Source 1",
                    "url": "https://example.org",
                    "source_type": "report",
                    "source_tier": "primary",
                    "access_status": "full_text",
                    "origin_cluster": "Cluster 1"
                }
            ],
            "searches": [
                {
                    "id": "Q001",
                    "claim_ids": ["C0001"],
                    "query": "batch search query",
                    "kind": "primary",
                    "status": "completed"
                }
            ]
        }
        atomic_write_json(batch_path, batch_data)

        merge_args = self.parser.parse_args(["merge-batch", str(audit_json_path), "--batch", str(batch_path)])
        ret = merge_args.func(merge_args)
        self.assertEqual(ret, 0)

        updated_audit = load_audit(audit_json_path)
        self.assertEqual(len(updated_audit["claims"]), 1)
        self.assertEqual(updated_audit["claims"][0]["id"], "C0001")
        self.assertEqual(len(updated_audit["sources"]), 1)
        self.assertEqual(updated_audit["sources"][0]["id"], "S001")
        self.assertIn("C0001", updated_audit["paragraphs"][0]["claim_ids"])

        conflict = {"sources": [{"id": "S001", "url": "https://different.example.org"}]}
        atomic_write_json(batch_path, conflict)
        before = audit_json_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Conflicting source ID"):
            merge_args.func(merge_args)
        self.assertEqual(audit_json_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
