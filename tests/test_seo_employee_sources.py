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
    SEOmatorAdapter,
    missing_sources,
    required_sources_satisfied,
)


PLAN = SimpleNamespace(
    max_pages=25,
    categories=("core", "links"),
    performance_sample_pages=5,
    required_sources=("CrawlSEO", "SEOmator"),
)
RULE = SimpleNamespace(rule_key="meta-description-missing", severity="warning")


def crawlseo_payload(
    *, pages: int = 25, max_pages: int = 25, issue_type: str = "MISSING_DESCRIPTION"
) -> dict[str, object]:
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
        "issues": [{"type": issue_type, "severity": "warning", "url": "https://example.com/"}],
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
