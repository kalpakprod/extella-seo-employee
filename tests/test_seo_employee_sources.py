from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "experts"))

from seo_employee_sources import (
    CrawlSEOAdapter,
    PSIAdapter,
    SEOmatorAdapter,
    missing_sources,
    required_sources_satisfied,
)


PLAN = SimpleNamespace(
    max_pages=25,
    categories=("core", "links"),
    performance_sample_pages=5,
    required_sources=("CrawlSEO", "SEOmator"),
    psi_max_urls=3,
)
RULE = SimpleNamespace(rule_key="meta-description-missing", severity="warning")


def crawlseo_payload(
    *,
    pages: int = 25,
    max_pages: int = 25,
    issue_type: str = "MISSING_DESCRIPTION",
    severity: str | None = "warning",
) -> dict[str, object]:
    issue: dict[str, object] = {"type": issue_type, "url": "https://example.com/"}
    if severity is not None:
        issue["severity"] = severity
    return {
        "schema": "extella.crawlseo_source.v1",
        "source": "CrawlSEO",
        "tool": "run_crawl",
        "tool_calls": 1,
        "requested_max_pages": max_pages,
        "crawl": {"status": "COMPLETED", "maxPages": max_pages, "pagesFound": pages},
        "coverage": {
            "planned_pages": max_pages,
            "crawled_pages": pages,
            "sampled_pages": 0,
            "categories": ["core", "links"],
        },
        "issues": [issue],
    }


def seomator_payload(
    *, pages: int = 25, rule_id: str = "core-description-present", message: str = "Description is missing."
) -> dict[str, object]:
    return {
        "url": "https://example.com/",
        "crawledPages": pages,
        "coverage": {
            "planned_pages": 25,
            "crawled_pages": pages,
            "sampled_pages": 1,
            "sampled_urls": ["https://example.com/"],
            "categories": ["core", "links"],
        },
        "categoryResults": [
            {
                "categoryId": "core",
                "results": [{
                    "status": "fail", "ruleId": rule_id, "message": message,
                    "details": {"pageUrl": "https://example.com/"},
                }],
            },
            {"categoryId": "links", "results": []},
        ],
    }


def psi_payload(
    *,
    urls: list[str] | None = None,
    metrics: list[dict[str, object]] | None = None,
    robots: str | None = "User-agent: *\nDisallow:\n",
    sitemap: str | None = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/</loc></url></urlset>',
    html: str | None = '<html><head><script type="application/ld+json">{"@type": "WebSite"}</script></head></html>',
) -> dict[str, object]:
    probed = urls if urls is not None else ["https://example.com/"]
    return {
        "schema": "extella.psi_source.v1",
        "source": "PSI",
        "site_url": "https://example.com/",
        "probed_urls": probed,
        "metrics": metrics if metrics is not None else [],
        "psi_api": {"status": "ok", "reason": None},
        "crux": {"status": "not_configured", "reason": None},
        "sitefiles": {
            "robots_txt": {"http_status": 200, "truncated": False, "content": robots or ""},
            "sitemap_xml": {"http_status": 200, "truncated": False, "content": sitemap or ""},
            "homepage_html": {"http_status": 200, "truncated": False, "content": html or ""},
        },
    }


class SourceAdaptersTest(unittest.TestCase):
    def test_crawlseo_success_reports_actual_coverage_and_known_occurrence(self) -> None:
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = CrawlSEOAdapter().parse(crawlseo_payload(), PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.coverage.planned_pages, 25)
        self.assertEqual(result.coverage.crawled_pages, 25)
        self.assertEqual(result.coverage.sampled_pages, 0)
        self.assertEqual(result.coverage.categories, ("core", "links"))
        self.assertEqual(result.occurrences[0].rule_key, "meta-description-missing")
        self.assertEqual(result.mode_result["status"], "not_configured")

    def test_crawlseo_exposes_bounded_structured_search_performance(self) -> None:
        payload = crawlseo_payload()
        payload["search_performance"] = {
            "status": "ready",
            "period_days": 28,
            "metrics": {"current": {"clicks": 12}, "previous": {"clicks": 9}, "deltas": {"clicks": 33.3}},
            "keywords": [{"query": "audit", "clicks": 4}],
            "pages": [{"url": "https://example.com/", "clicks": 12}],
            "traffic": [{"date": "2026-08-30", "clicks": 12}],
            "vitals": [{"url": "https://example.com/", "device": "MOBILE", "lcp": 2.1}],
            "opportunities": [{"type": "low_ctr", "title": "audit"}],
        }
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = CrawlSEOAdapter().parse(payload, PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.mode_result["status"], "ready")
        self.assertEqual(result.mode_result["metrics"]["current"]["clicks"], 12)

    def test_crawlseo_rejects_unbounded_search_performance(self) -> None:
        payload = crawlseo_payload()
        payload["search_performance"] = {
            "status": "ready", "period_days": 28, "metrics": {},
            "keywords": [{}] * 26, "pages": [], "traffic": [], "vitals": [], "opportunities": [],
        }
        result = CrawlSEOAdapter().parse(payload, PLAN)
        self.assertEqual((result.status, result.reason), ("failed", "invalid_payload"))

    def test_crawlseo_e2e_corpus_types_are_all_mapped(self) -> None:
        payload = crawlseo_payload()
        payload["issues"] = [
            {"type": issue_type, "severity": "warning", "url": "https://example.com/"}
            for issue_type in (
                "DUPLICATE_DESCRIPTION", "DUPLICATE_TITLE", "MISSING_CANONICAL",
                "MISSING_ROBOTS", "MISSING_SCHEMA", "MISSING_SITEMAP", "MIXED_CONTENT",
            )
        ]
        result = CrawlSEOAdapter().parse(payload, PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.coverage.unmapped_rules, ())
        self.assertEqual(len(result.occurrences), 7)
        self.assertEqual(
            {occurrence.rule_key for occurrence in result.occurrences},
            {
                "content-duplicate-description", "core-title-unique", "core-canonical-present",
                "technical-robots-txt-exists", "schema-present", "technical-sitemap-exists",
                "security-mixed-content",
            },
        )

    def test_crawlseo_issue_severity_maps_to_occurrence_status(self) -> None:
        cases = {
            "ERROR": "fail",
            "error": "fail",
            "CRITICAL": "fail",
            "WARNING": "warn",
            "warning": "warn",
            "INFO": "warn",
            "info": "warn",
            "mystery-grade": "warn",
        }
        for raw, expected in cases.items():
            with self.subTest(severity=raw):
                with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
                    result = CrawlSEOAdapter().parse(
                        crawlseo_payload(severity=raw), PLAN
                    )
                self.assertEqual(result.status, "ok")
                self.assertEqual(len(result.occurrences), 1)
                self.assertEqual(result.occurrences[0].status, expected)
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = CrawlSEOAdapter().parse(crawlseo_payload(severity=None), PLAN)
        self.assertEqual(result.occurrences[0].status, "warn")

    def test_seomator_warn_is_ingested_with_warn_status(self) -> None:
        payload = seomator_payload()
        payload["categoryResults"][0]["results"][0]["status"] = "warn"
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = SEOmatorAdapter().parse(payload, PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.occurrences), 1)
        self.assertEqual(result.occurrences[0].status, "warn")
        self.assertEqual(result.coverage.unmapped_rules, ())

    def test_seomator_warn_with_unusable_rule_fails_the_source(self) -> None:
        payload = seomator_payload()
        payload["categoryResults"][0]["results"][0]["status"] = "warn"
        del payload["categoryResults"][0]["results"][0]["ruleId"]
        result = SEOmatorAdapter().parse(payload, PLAN)
        self.assertEqual(result.status, "failed")

    def test_psi_clean_corpus_emits_no_occurrences(self) -> None:
        result = PSIAdapter().parse(psi_payload(), PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.occurrences, ())
        self.assertEqual(result.coverage.unmapped_rules, ())

    def test_psi_thresholds_emit_graded_occurrences(self) -> None:
        payload = psi_payload(metrics=[
            {"url": "https://example.com/", "metric": "lcp", "lab": 5000, "field_p75": 3100},
            {"url": "https://example.com/", "metric": "cls", "lab": 0.15, "field_p75": None},
            {"url": "https://example.com/", "metric": "inp", "lab": 100, "field_p75": 150},
        ])
        result = PSIAdapter().parse(payload, PLAN)
        self.assertEqual(result.status, "ok")
        by_rule = {item.rule_key: item for item in result.occurrences}
        self.assertEqual(set(by_rule), {"psi-lcp", "psi-cls"})
        self.assertEqual(by_rule["psi-lcp"].status, "fail")
        self.assertIn("5.0s", by_rule["psi-lcp"].fact)
        self.assertIn("3.1s", by_rule["psi-lcp"].fact)
        self.assertEqual(by_rule["psi-cls"].status, "warn")
        self.assertIn("0.15", by_rule["psi-cls"].fact)

    def test_psi_field_p75_can_fail_when_lab_passes(self) -> None:
        payload = psi_payload(metrics=[
            {"url": "https://example.com/", "metric": "lcp", "lab": 2000, "field_p75": 4500},
        ])
        result = PSIAdapter().parse(payload, PLAN)
        by_rule = {item.rule_key: item for item in result.occurrences}
        self.assertEqual(by_rule["psi-lcp"].status, "fail")
        self.assertIn("field p75", by_rule["psi-lcp"].fact)

    def test_psi_rejects_over_cap_foreign_and_unprobed_urls(self) -> None:
        over = psi_payload(urls=[f"https://example.com/{index}" for index in range(4)])
        self.assertEqual(PSIAdapter().parse(over, PLAN).status, "failed")
        foreign = psi_payload(urls=["https://example.com/", "https://other.test/"])
        self.assertEqual(PSIAdapter().parse(foreign, PLAN).status, "failed")
        stray = psi_payload(metrics=[
            {"url": "https://example.com/stray", "metric": "lcp", "lab": 5000, "field_p75": None},
        ])
        self.assertEqual(PSIAdapter().parse(stray, PLAN).status, "failed")

    def test_psi_robots_parser_flags_orphan_rule_and_bad_line(self) -> None:
        orphan = PSIAdapter().parse(psi_payload(robots="Disallow: /tmp/\n"), PLAN)
        self.assertEqual(
            [(item.source_rule, item.status) for item in orphan.occurrences],
            [("ROBOTS_TXT_INVALID", "fail")],
        )
        self.assertIn("line 1", orphan.occurrences[0].fact)
        bad_line = PSIAdapter().parse(psi_payload(robots="User-agent: *\njust some words\n"), PLAN)
        self.assertEqual(len(bad_line.occurrences), 1)
        self.assertIn("line 2", bad_line.occurrences[0].fact)

    def test_psi_sitemap_parser_flags_malformed_and_doctype(self) -> None:
        malformed = PSIAdapter().parse(psi_payload(sitemap="<urlset><oops"), PLAN)
        self.assertEqual(
            [(item.source_rule, item.status) for item in malformed.occurrences],
            [("SITEMAP_INVALID", "fail")],
        )
        doctype = PSIAdapter().parse(
            psi_payload(sitemap='<!DOCTYPE urlset [<!ENTITY x "y">]><urlset/>'), PLAN
        )
        self.assertEqual(len(doctype.occurrences), 1)
        self.assertIn("DOCTYPE", doctype.occurrences[0].fact)

    def test_psi_schema_parser_flags_broken_jsonld_only(self) -> None:
        broken = PSIAdapter().parse(
            psi_payload(html='<html><head><script type="application/ld+json">{oops</script></head></html>'),
            PLAN,
        )
        self.assertEqual(
            [(item.source_rule, item.status) for item in broken.occurrences],
            [("SCHEMA_INVALID", "fail")],
        )
        bare = PSIAdapter().parse(psi_payload(html="<html><head></head></html>"), PLAN)
        self.assertEqual(bare.occurrences, ())
        payload = psi_payload(html="<html><head>" + "x" * 100 + "</head></html>")
        payload["sitefiles"]["homepage_html"]["truncated"] = True
        truncated = PSIAdapter().parse(payload, PLAN)
        self.assertEqual(truncated.occurrences, ())

    def test_psi_skips_unfetched_sitefiles(self) -> None:
        payload = psi_payload()
        for section in payload["sitefiles"].values():
            section["http_status"] = 404
            section["content"] = ""
        result = PSIAdapter().parse(payload, PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.occurrences, ())

    def test_psi_degraded_sections_surface_as_coverage_notes(self) -> None:
        payload = psi_payload()
        payload["psi_api"] = {"status": "degraded", "reason": "http_429"}
        payload["crux"] = {"status": "unavailable", "reason": "timeout"}
        result = PSIAdapter().parse(payload, PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            result.coverage.notes,
            (
                "psi api http_429: lab metrics unavailable",
                "crux timeout: field data unavailable",
            ),
        )
        self.assertEqual(result.coverage.as_dict()["notes"], list(result.coverage.notes))
        clean = PSIAdapter().parse(psi_payload(), PLAN)
        self.assertEqual(
            clean.coverage.notes, ("crux not_configured: field data unavailable without a key",)
        )

    def test_psi_rejects_malformed_status_blocks(self) -> None:
        payload = psi_payload()
        payload["psi_api"] = {"status": "degraded", "reason": "bogus"}
        self.assertEqual(PSIAdapter().parse(payload, PLAN).status, "failed")
        payload = psi_payload()
        del payload["crux"]
        self.assertEqual(PSIAdapter().parse(payload, PLAN).status, "failed")

    def test_psi_declared_unavailable_passes_through(self) -> None:
        result = PSIAdapter().parse({"status": "unavailable", "reason": "timeout"}, PLAN)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "timeout")

    def test_seomator_unknown_rule_is_counted_not_emitted_as_task(self) -> None:
        with mock.patch("seo_employee_sources.canonical_rule", return_value=None):
            result = SEOmatorAdapter().parse(seomator_payload(rule_id="future-rule"), PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.occurrences, ())
        self.assertEqual(result.coverage.unmapped_rules, ("future-rule",))

    def test_seomator_known_coverage_rule_emits_a_supported_occurrence(self) -> None:
        result = SEOmatorAdapter().parse(seomator_payload(rule_id="core-title-present"), PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.occurrences), 1)
        self.assertEqual(result.occurrences[0].rule_key, "core-title-present")
        self.assertEqual(result.coverage.unmapped_rules, ())

    def test_seomator_normalizes_only_safe_upstream_message(self) -> None:
        result = SEOmatorAdapter().parse(
            seomator_payload(message="  В заголовке   отсутствует   текст.  "), PLAN
        )
        self.assertEqual(result.occurrences[0].fact, "В заголовке отсутствует текст.")

    def test_seomator_bounds_upstream_message_to_500_characters(self) -> None:
        result = SEOmatorAdapter().parse(seomator_payload(message="a" * 501), PLAN)
        self.assertEqual(result.occurrences[0].fact, "a" * 500)

    def test_seomator_rejects_empty_control_or_secret_like_message(self) -> None:
        for message in ("", "safe\x00message", "Bearer sample-value"):
            with self.subTest(message=message):
                result = SEOmatorAdapter().parse(seomator_payload(message=message), PLAN)
                self.assertEqual((result.status, result.reason), ("failed", "invalid_payload"))

    def test_seomator_coverage_uses_one_executed_sample_not_five_requested_samples(self) -> None:
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = SEOmatorAdapter().parse(seomator_payload(), PLAN)
        self.assertEqual(PLAN.performance_sample_pages, 5)
        self.assertEqual(result.coverage.sampled_pages, 1)

    def test_seomator_real_rule_result_uses_details_page_url_and_actual_sample_coverage(self) -> None:
        payload = json.loads((Path(__file__).parent / "fixtures" / "seomator-source.json").read_text(encoding="utf-8"))
        payload["url"] = "https://top-level.example/"
        payload["coverage"] = {
            "planned_pages": 1,
            "crawled_pages": 1,
            "sampled_pages": 1,
            "sampled_urls": ["https://example.com/"],
            "categories": ["core"],
        }
        plan = SimpleNamespace(
            max_pages=1,
            categories=("core",),
            performance_sample_pages=1,
            required_sources=("SEOmator",),
        )
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = SEOmatorAdapter().parse(payload, plan)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.occurrences[0].url, "https://example.com/")
        self.assertNotEqual(result.occurrences[0].url, payload["url"])
        self.assertEqual(result.coverage.sampled_pages, 1)

    def test_incomplete_coverage_is_explicit_not_a_missing_finding(self) -> None:
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = CrawlSEOAdapter().parse(crawlseo_payload(pages=3), PLAN)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.coverage.planned_pages, 25)
        self.assertEqual(result.coverage.crawled_pages, 3)

    def test_blocking_conditions_have_distinct_machine_reasons(self) -> None:
        cases = {
            "waf": {"error": {"code": "waf"}},
            "captcha": {"error": {"code": "captcha_required"}},
            "http_403": {"status_code": 403},
            "http_429": {"status_code": 429},
            "http_503": {"status_code": 503},
            "robots_denied": {"error": {"code": "robots_denied"}},
            "timeout": {"error": {"code": "timeout"}},
        }
        for expected, payload in cases.items():
            with self.subTest(expected=expected):
                result = CrawlSEOAdapter().parse(payload, PLAN)
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.reason, expected)
                self.assertEqual(result.occurrences, ())

    def test_robots_rule_id_does_not_override_a_successful_source_status(self) -> None:
        with mock.patch("seo_employee_sources.canonical_rule", return_value=RULE):
            result = CrawlSEOAdapter().parse(crawlseo_payload(issue_type="ROBOTS_BLOCKED"), PLAN)
        self.assertEqual(result.status, "ok")

    def test_malformed_source_elements_fail_instead_of_looking_clean(self) -> None:
        crawl_cases = []
        malformed_issue = deepcopy(crawlseo_payload())
        malformed_issue["issues"] = [None]
        crawl_cases.append(malformed_issue)
        malformed_url = deepcopy(crawlseo_payload())
        malformed_url["issues"][0]["url"] = None
        crawl_cases.append(malformed_url)
        for payload in crawl_cases:
            with self.subTest(source="CrawlSEO", payload=payload):
                result = CrawlSEOAdapter().parse(payload, PLAN)
                self.assertEqual((result.status, result.reason), ("failed", "invalid_payload"))

        seo_cases = []
        malformed_category = deepcopy(seomator_payload())
        malformed_category["categoryResults"] = [None]
        seo_cases.append(malformed_category)
        malformed_result = deepcopy(seomator_payload())
        malformed_result["categoryResults"][0]["results"] = [None]
        seo_cases.append(malformed_result)
        malformed_urls = deepcopy(seomator_payload())
        malformed_urls["categoryResults"][0]["results"][0]["urls"] = [""]
        seo_cases.append(malformed_urls)
        for payload in seo_cases:
            with self.subTest(source="SEOmator", payload=payload):
                result = SEOmatorAdapter().parse(payload, PLAN)
                self.assertEqual((result.status, result.reason), ("failed", "invalid_payload"))

    def test_non_string_machine_fields_are_failed_not_type_errors(self) -> None:
        for payload in ({"status": []}, {"reason": {}}, {"error": {"code": 403}}):
            with self.subTest(payload=payload):
                result = CrawlSEOAdapter().parse(payload, PLAN)
                self.assertEqual((result.status, result.reason), ("failed", "invalid_payload"))

    def test_invalid_payload_fails_with_fixed_reason(self) -> None:
        result = SEOmatorAdapter().parse({"crawledPages": 101, "categoryResults": []}, PLAN)
        self.assertEqual((result.status, result.reason), ("failed", "invalid_payload"))

    def test_declared_not_configured_and_unsupported_statuses_remain_distinct(self) -> None:
        not_configured = CrawlSEOAdapter().parse({"status": "not_configured", "reason": "not_configured"}, PLAN)
        unsupported = SEOmatorAdapter().parse(
            {"status": "unsupported", "reason": "seomator_sample_output_unsupported"}, PLAN
        )
        self.assertEqual((not_configured.status, not_configured.reason), ("not_configured", "not_configured"))
        self.assertEqual((unsupported.status, unsupported.reason), ("unsupported", "seomator_sample_output_unsupported"))

    def test_declared_transport_failure_preserves_the_fixed_reason(self) -> None:
        for reason in ("waf", "captcha", "http_403", "http_429", "http_503", "robots_denied", "timeout"):
            with self.subTest(reason=reason):
                result = CrawlSEOAdapter().parse({"status": "unavailable", "reason": reason}, PLAN)
                self.assertEqual((result.status, result.reason), ("unavailable", reason))

    def test_required_source_helpers_do_not_count_optional_or_failed_sources(self) -> None:
        crawl = CrawlSEOAdapter().parse(crawlseo_payload(pages=1, max_pages=1), SimpleNamespace(
            max_pages=1,
            categories=("core", "links"),
            performance_sample_pages=1,
            required_sources=("CrawlSEO", "SEOmator"),
        ))
        seo = SEOmatorAdapter().parse({"error": {"code": "timeout"}}, PLAN)
        self.assertFalse(required_sources_satisfied(PLAN, (crawl, seo)))
        self.assertEqual(missing_sources(PLAN, (crawl, seo)), ("SEOmator",))


if __name__ == "__main__":
    unittest.main()
