"""Source-specific parsing with explicit coverage and safe failure reasons."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from types import MappingProxyType
import json
import math
import re
from typing import Literal, Mapping, Protocol, Sequence
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ElementTree

from seo_employee_profiles import AuditPlan
from seo_employee_rules import canonical_rule


SourceStatus = Literal["ok", "not_configured", "unavailable", "failed", "unsupported"]
_BLOCKING_REASONS = ("waf", "captcha", "http_403", "http_429", "http_503", "robots_denied", "timeout")
_SECRET_MATERIAL = re.compile(
    r"(?i)(bearer\s+\S+|sk-[a-z0-9_-]{8,}|api[_-]?key\s*[:=]|"
    r"(?:secret|token|cookie|authorization|password|oauth)\s*[:=])"
)


class SourceAdapterError(ValueError):
    """A fixed, non-sensitive source payload validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Coverage:
    planned_pages: int
    crawled_pages: int
    sampled_pages: int
    categories: tuple[str, ...]
    completed_sources: tuple[str, ...]
    unavailable_sources: tuple[str, ...]
    unmapped_rules: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "planned_pages": self.planned_pages,
            "crawled_pages": self.crawled_pages,
            "sampled_pages": self.sampled_pages,
            "categories": list(self.categories),
            "completed_sources": list(self.completed_sources),
            "unavailable_sources": list(self.unavailable_sources),
            "unmapped_rules": list(self.unmapped_rules),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SourceOccurrence:
    source: str
    source_rule: str
    rule_key: str
    severity: str
    url: str
    fact: str
    status: str


_FAIL_SEVERITIES = frozenset({"ERROR", "CRITICAL", "FAIL", "FATAL"})


def _crawlseo_status(severity: object) -> str:
    if isinstance(severity, str) and severity.strip().upper() in _FAIL_SEVERITIES:
        return "fail"
    return "warn"


@dataclass(frozen=True)
class SourceResult:
    source: str
    status: SourceStatus
    coverage: Coverage
    occurrences: tuple[SourceOccurrence, ...] = ()
    reason: str | None = None
    mode_result: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.mode_result is not None:
            object.__setattr__(self, "mode_result", MappingProxyType(dict(self.mode_result)))


class SourceAdapter(Protocol):
    name: str

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None: ...

    def parse(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult: ...


def _plan_categories(plan: AuditPlan) -> tuple[str, ...]:
    categories = tuple(str(category) for category in plan.categories)
    if not categories:
        raise SourceAdapterError("invalid_plan")
    return categories


def _coverage(
    source: str,
    plan: AuditPlan,
    *,
    crawled_pages: int = 0,
    sampled_pages: int = 0,
    status: SourceStatus = "ok",
    unmapped_rules: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> Coverage:
    categories = _plan_categories(plan)
    return Coverage(
        planned_pages=plan.max_pages,
        crawled_pages=crawled_pages,
        sampled_pages=sampled_pages,
        categories=categories,
        completed_sources=(source,) if status == "ok" else (),
        unavailable_sources=(source,) if status == "unavailable" else (),
        unmapped_rules=tuple(sorted(set(unmapped_rules))),
        notes=tuple(notes),
    )


def _result(source: str, plan: AuditPlan, status: SourceStatus, reason: str) -> SourceResult:
    return SourceResult(source=source, status=status, reason=reason, coverage=_coverage(source, plan, status=status))


def _coverage_from_payload(payload: Mapping[str, object], plan: AuditPlan, crawled_pages: int) -> tuple[int, int]:
    coverage = payload.get("coverage")
    if not isinstance(coverage, Mapping):
        raise SourceAdapterError("invalid_payload")
    planned_pages = _required_int(coverage.get("planned_pages"))
    actual_pages = _required_int(coverage.get("crawled_pages"))
    sampled_pages = _required_int(coverage.get("sampled_pages"))
    categories = coverage.get("categories")
    if (
        planned_pages != plan.max_pages
        or actual_pages != crawled_pages
        or not isinstance(categories, list)
        or tuple(categories) != _plan_categories(plan)
        or any(not isinstance(category, str) for category in categories)
        or not 0 <= sampled_pages <= min(plan.performance_sample_pages, crawled_pages)
    ):
        raise SourceAdapterError("invalid_payload")
    sampled_urls = coverage.get("sampled_urls")
    if sampled_urls is not None and (
        not isinstance(sampled_urls, list)
        or len(sampled_urls) != sampled_pages
        or any(not _safe_audit_url(url) for url in sampled_urls)
    ):
        raise SourceAdapterError("invalid_payload")
    return actual_pages, sampled_pages


def _safe_audit_url(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None


def _normalize_source_fact(value: object) -> str:
    if not isinstance(value, str) or any(unicodedata.category(character) == "Cc" for character in value):
        raise SourceAdapterError("invalid_payload")
    normalized = " ".join(value.split())
    if not normalized or _SECRET_MATERIAL.search(normalized):
        raise SourceAdapterError("invalid_payload")
    return normalized[:500]


def _blocking_reason(payload: Mapping[str, object]) -> str | None:
    error = payload.get("error")
    fields: list[object] = [
        payload.get("status"), payload.get("reason"), payload.get("status_code"), payload.get("statusCode"), error,
    ]
    if isinstance(error, Mapping):
        fields.extend((error.get("code"), error.get("reason"), error.get("status"), error.get("status_code")))
    values = {str(value).lower() for value in fields if isinstance(value, (str, int)) and not isinstance(value, bool)}
    if any(value.startswith("captcha") for value in values):
        return "captcha"
    if any(value.startswith("waf") or value.startswith("cloudflare") for value in values):
        return "waf"
    if any(value.startswith("robots") for value in values):
        return "robots_denied"
    if any(value in {"timeout", "timed_out", "request_timeout"} for value in values):
        return "timeout"
    for status, reason in ((403, "http_403"), (429, "http_429"), (503, "http_503")):
        if str(status) in values or reason in values:
            return reason
    return None


def _declared_status(payload: Mapping[str, object]) -> tuple[SourceStatus, str] | None:
    error = payload.get("error")
    if error is not None and not isinstance(error, Mapping):
        raise SourceAdapterError("invalid_payload")
    code = error.get("code") if isinstance(error, Mapping) else None
    status = payload.get("status")
    reason = payload.get("reason")
    for value in (status, reason, code):
        if value is not None and not isinstance(value, str):
            raise SourceAdapterError("invalid_payload")
    declared = status if status in {"unavailable", "failed", "not_configured", "unsupported"} else None
    if declared is None:
        return None
    if not isinstance(reason, str) or not reason:
        raise SourceAdapterError("invalid_payload")
    if declared == "unavailable" and reason not in _BLOCKING_REASONS:
        raise SourceAdapterError("invalid_payload")
    if declared == "failed" and reason != "audit_failed":
        raise SourceAdapterError("invalid_payload")
    if declared == "not_configured" and reason != "not_configured":
        raise SourceAdapterError("invalid_payload")
    if declared == "unsupported" and reason not in {"seomator_sample_selection_unsupported", "seomator_sample_output_unsupported"}:
        raise SourceAdapterError("invalid_payload")
    if code is not None and code != reason:
        raise SourceAdapterError("invalid_payload")
    if declared:
        return declared, reason
    return None


def _result_urls(result: Mapping[str, object], payload: Mapping[str, object]) -> tuple[str, ...]:
    if "urls" in result:
        urls = result["urls"]
        if not isinstance(urls, list) or not urls or any(not _safe_audit_url(url) for url in urls):
            raise SourceAdapterError("invalid_payload")
        return tuple(urls)
    details = result.get("details")
    if details is not None and not isinstance(details, Mapping):
        raise SourceAdapterError("invalid_payload")
    page_url = details.get("pageUrl") if isinstance(details, Mapping) else None
    if _safe_audit_url(page_url):
        return (page_url,)
    if _safe_audit_url(payload.get("url")):
        return (payload["url"],)
    raise SourceAdapterError("invalid_payload")


def _required_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SourceAdapterError("invalid_payload")
    return value


def _known_rule(source: str, source_rule: object) -> tuple[str, str] | None:
    if not isinstance(source_rule, str) or not source_rule:
        return None
    definition = canonical_rule(source, source_rule)
    if definition is None:
        return None
    rule_key = definition if isinstance(definition, str) else getattr(definition, "rule_key", None)
    severity = getattr(definition, "severity", "warning")
    if not isinstance(rule_key, str) or not rule_key or not isinstance(severity, str):
        return None
    return rule_key, severity


def _search_performance(value: object) -> Mapping[str, object]:
    if value is None:
        return MappingProxyType({
            "status": "not_configured",
            "reason": "not_configured",
            "next_action": "Connect Google Search Console in CrawlSEO.",
        })
    if not isinstance(value, Mapping):
        raise SourceAdapterError("invalid_payload")
    status = value.get("status")
    if status == "not_configured":
        if value.get("reason") != "not_configured" or not isinstance(value.get("instruction"), str):
            raise SourceAdapterError("invalid_payload")
        return MappingProxyType({
            "status": "not_configured",
            "reason": "not_configured",
            "next_action": value["instruction"],
        })
    if status != "ready" or set(value) != {
        "status", "period_days", "metrics", "keywords", "pages", "traffic", "vitals", "opportunities",
    }:
        raise SourceAdapterError("invalid_payload")
    if value.get("period_days") != 28 or not isinstance(value.get("metrics"), Mapping):
        raise SourceAdapterError("invalid_payload")
    result: dict[str, object] = {"status": "ready", "period_days": 28, "metrics": dict(value["metrics"])}
    for field, limit in (("keywords", 25), ("pages", 25), ("traffic", 90), ("vitals", 20), ("opportunities", 30)):
        items = value.get(field)
        if not isinstance(items, list) or len(items) > limit or any(not isinstance(item, Mapping) for item in items):
            raise SourceAdapterError("invalid_payload")
        result[field] = [dict(item) for item in items]
    return MappingProxyType(result)


class CrawlSEOAdapter:
    name = "CrawlSEO"
    capabilities = ("crawl", "technical", "performance")

    def __init__(self, _catalog: object | None = None) -> None:
        self._catalog = _catalog

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None:
        crawl = payload.get("crawl")
        if (
            payload.get("schema") != "extella.crawlseo_source.v1"
            or payload.get("source") != self.name
            or payload.get("tool") != "run_crawl"
            or payload.get("tool_calls") != 1
            or payload.get("requested_max_pages") != plan.max_pages
            or not isinstance(crawl, Mapping)
            or crawl.get("status") != "COMPLETED"
            or not isinstance(payload.get("issues"), list)
        ):
            raise SourceAdapterError("invalid_payload")
        max_pages = _required_int(crawl.get("maxPages"))
        pages_found = _required_int(crawl.get("pagesFound"))
        if max_pages != plan.max_pages or not 0 < pages_found <= plan.max_pages:
            raise SourceAdapterError("invalid_payload")
        _coverage_from_payload(payload, plan, pages_found)
        for issue in payload["issues"]:
            if (
                not isinstance(issue, Mapping)
                or not isinstance(issue.get("type"), str)
                or not issue["type"]
                or not isinstance(issue.get("url"), str)
                or not issue["url"]
                or ("message" in issue and not isinstance(issue["message"], str))
                or ("severity" in issue and not isinstance(issue["severity"], str))
            ):
                raise SourceAdapterError("invalid_payload")
        _search_performance(payload.get("search_performance"))

    def parse(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult:
        try:
            declared_status = _declared_status(payload)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        if declared_status is not None:
            return _result(self.name, plan, declared_status[0], declared_status[1])
        blocking_reason = _blocking_reason(payload)
        if blocking_reason is not None:
            return _result(self.name, plan, "unavailable", blocking_reason)
        try:
            self.validate(payload, plan)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        crawl = payload["crawl"]
        assert isinstance(crawl, Mapping)
        issues = payload["issues"]
        assert isinstance(issues, list)
        occurrences: list[SourceOccurrence] = []
        unmapped_rules: list[str] = []
        for issue in issues:
            assert isinstance(issue, Mapping)
            source_rule = issue.get("type")
            known = _known_rule(self.name, source_rule)
            if known is None:
                if isinstance(source_rule, str) and source_rule:
                    unmapped_rules.append(source_rule)
                continue
            url = issue["url"]
            assert isinstance(url, str)
            fact = issue.get("message")
            occurrences.append(
                SourceOccurrence(
                    source=self.name,
                    source_rule=str(source_rule),
                    rule_key=known[0],
                    severity=known[1],
                    url=url,
                    fact=fact if isinstance(fact, str) else "",
                    status=_crawlseo_status(issue.get("severity")),
                )
            )
        pages_found = _required_int(crawl["pagesFound"])
        _actual_pages, sampled_pages = _coverage_from_payload(payload, plan, pages_found)
        return SourceResult(
            source=self.name,
            status="ok",
            coverage=_coverage(
                self.name, plan, crawled_pages=pages_found, sampled_pages=sampled_pages, unmapped_rules=unmapped_rules
            ),
            occurrences=tuple(occurrences),
            mode_result=_search_performance(payload.get("search_performance")),
        )


class SEOmatorAdapter:
    name = "SEOmator"
    capabilities = (
        "core", "technical", "perf", "links", "images", "security", "crawl", "schema", "a11y", "content",
        "social", "eeat", "url", "mobile", "i18n", "legal", "js", "redirect", "htmlval", "geo",
    )

    def __init__(self, _catalog: object | None = None) -> None:
        self._catalog = _catalog

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None:
        crawled_pages = _required_int(payload.get("crawledPages"))
        categories = payload.get("categoryResults")
        if not 0 < crawled_pages <= plan.max_pages or not isinstance(categories, list):
            raise SourceAdapterError("invalid_payload")
        category_ids: set[str] = set()
        for category in categories:
            if (
                not isinstance(category, Mapping)
                or not isinstance(category.get("categoryId"), str)
                or not category["categoryId"]
                or not isinstance(category.get("results"), list)
                or category["categoryId"] in category_ids
            ):
                raise SourceAdapterError("invalid_payload")
            category_id = category["categoryId"]
            assert isinstance(category_id, str)
            category_ids.add(category_id)
            for result in category["results"]:
                if (
                    not isinstance(result, Mapping)
                    or result.get("status") not in {"pass", "warn", "fail"}
                    or ("message" in result and not isinstance(result["message"], str))
                ):
                    raise SourceAdapterError("invalid_payload")
                if result["status"] == "pass":
                    continue
                urls = result.get("urls")
                if (
                    not isinstance(result.get("ruleId"), str)
                    or not result["ruleId"]
                ):
                    raise SourceAdapterError("invalid_payload")
                _normalize_source_fact(result.get("message"))
                _result_urls(result, payload)
        if not set(_plan_categories(plan)).issubset(category_ids):
            raise SourceAdapterError("incomplete_coverage")
        _coverage_from_payload(payload, plan, crawled_pages)

    def parse(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult:
        if any(category not in self.capabilities for category in _plan_categories(plan)):
            return _result(self.name, plan, "unsupported", "unsupported")
        try:
            declared_status = _declared_status(payload)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        if declared_status is not None:
            return _result(self.name, plan, declared_status[0], declared_status[1])
        blocking_reason = _blocking_reason(payload)
        if blocking_reason is not None:
            return _result(self.name, plan, "unavailable", blocking_reason)
        try:
            self.validate(payload, plan)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        categories = payload["categoryResults"]
        assert isinstance(categories, list)
        occurrences: list[SourceOccurrence] = []
        unmapped_rules: list[str] = []
        for category in categories:
            assert isinstance(category, Mapping)
            results = category["results"]
            assert isinstance(results, list)
            for result in results:
                assert isinstance(result, Mapping)
                status = result.get("status")
                if status not in {"fail", "warn"}:
                    continue
                assert isinstance(status, str)
                source_rule = result.get("ruleId")
                known = _known_rule(self.name, source_rule)
                if known is None:
                    if isinstance(source_rule, str) and source_rule:
                        unmapped_rules.append(source_rule)
                    continue
                candidates = _result_urls(result, payload)
                fact = _normalize_source_fact(result.get("message"))
                for url in candidates:
                    assert isinstance(url, str) and url
                    occurrences.append(
                        SourceOccurrence(
                            source=self.name,
                            source_rule=str(source_rule),
                            rule_key=known[0],
                            severity=known[1],
                            url=url,
                            fact=fact,
                            status=status,
                        )
                    )
        crawled_pages = _required_int(payload["crawledPages"])
        _actual_pages, sampled_pages = _coverage_from_payload(payload, plan, crawled_pages)
        return SourceResult(
            source=self.name,
            status="ok",
            coverage=_coverage(
                self.name, plan, crawled_pages=crawled_pages, sampled_pages=sampled_pages, unmapped_rules=unmapped_rules
            ),
            occurrences=tuple(occurrences),
        )


_PSI_THRESHOLDS = {"lcp": (2500.0, 4000.0), "inp": (200.0, 500.0), "cls": (0.1, 0.25)}
_PSI_METRIC_NAMES = {"lcp": "LCP", "inp": "INP", "cls": "CLS"}
_PSI_SITEFILE_BYTES = {"robots_txt": 65_536, "sitemap_xml": 65_536, "homepage_html": 262_144}
_ROBOTS_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*\s*:")
_JSON_LD_MAX_BLOCKS = 50


def _psi_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise SourceAdapterError("invalid_payload")
    return float(value)


def _psi_format(metric: str, value: float) -> str:
    if metric == "cls":
        return f"{value:.2f}"
    return f"{value / 1000:.1f}s"


def _psi_grade(metric: str, value: float) -> str:
    warn_over, fail_over = _PSI_THRESHOLDS[metric]
    if value > fail_over:
        return "fail"
    if value > warn_over:
        return "warn"
    return "pass"


def _psi_metric_verdict(metric: str, lab: float | None, field: float | None) -> tuple[str, str] | None:
    grades = {"fail": 2, "warn": 1, "pass": 0}
    lab_grade = _psi_grade(metric, lab) if lab is not None else "pass"
    field_grade = _psi_grade(metric, field) if field is not None else "pass"
    status = lab_grade if grades[lab_grade] >= grades[field_grade] else field_grade
    if status == "pass":
        return None
    parts = []
    if lab is not None:
        parts.append(f"{_psi_format(metric, lab)} lab")
    if field is not None:
        parts.append(f"{_psi_format(metric, field)} field p75")
    warn_over, fail_over = _PSI_THRESHOLDS[metric]
    threshold = fail_over if status == "fail" else warn_over
    band = "poor" if status == "fail" else "needs-improvement"
    fact = f"{_PSI_METRIC_NAMES[metric]} {' / '.join(parts)} exceeds {band} threshold {_psi_format(metric, threshold)}"
    return status, fact


def _check_robots(content: str, truncated: bool) -> str | None:
    text = content
    if truncated:
        if "\n" not in text:
            return None
        text = text.rsplit("\n", 1)[0]
    seen_agent = False
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not _ROBOTS_FIELD_RE.match(line):
            return f"robots.txt line {number} is not a field rule"
        field, _, value = line.partition(":")
        normalized = field.strip().lower()
        if normalized == "user-agent":
            if not value.strip():
                return f"robots.txt line {number} has an empty User-agent"
            seen_agent = True
        elif normalized in {"allow", "disallow"} and not seen_agent:
            return f"robots.txt line {number} is a rule without a User-agent group"
    return None


def _check_sitemap(content: str, truncated: bool) -> str | None:
    if truncated:
        return None
    if "<!doctype" in content.lower():
        return "sitemap.xml contains a DOCTYPE; refusing entity expansion"
    try:
        root = ElementTree.fromstring(content.encode("utf-8"))
    except ElementTree.ParseError:
        return "sitemap.xml is not well-formed XML"
    local = root.tag.rsplit("}", 1)[-1].lower()
    if local not in {"urlset", "sitemapindex"}:
        return f"sitemap.xml root is <{local}>; expected urlset or sitemapindex"
    return None


class _JsonLdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script" or len(self.blocks) >= _JSON_LD_MAX_BLOCKS:
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if attributes.get("type", "").strip().lower() == "application/ld+json":
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None


def _check_schema(content: str, truncated: bool) -> str | None:
    if truncated:
        return None
    collector = _JsonLdCollector()
    collector.feed(content)
    collector.close()
    for index, block in enumerate(collector.blocks, 1):
        text = block.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except ValueError:
            return f"homepage JSON-LD block {index} is not valid JSON"
        if not isinstance(value, (dict, list)):
            return f"homepage JSON-LD block {index} is not an object or array"
    return None


class PSIAdapter:
    name = "PSI"
    capabilities = ("perf", "technical", "schema")

    def __init__(self, _catalog: object | None = None) -> None:
        self._catalog = _catalog

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None:
        site_url = payload.get("site_url")
        probed = payload.get("probed_urls")
        metrics = payload.get("metrics")
        sitefiles = payload.get("sitefiles")
        psi_api = payload.get("psi_api")
        crux = payload.get("crux")
        for block, allowed in (
            (psi_api, {"ok", "degraded"}),
            (crux, {"ok", "not_configured", "unavailable"}),
        ):
            if not isinstance(block, Mapping) or set(block) != {"status", "reason"}:
                raise SourceAdapterError("invalid_payload")
            status = block.get("status")
            reason = block.get("reason")
            if (
                status not in allowed
                or ((status in {"ok", "not_configured"}) != (reason is None))
                or (reason is not None and reason not in _BLOCKING_REASONS)
            ):
                raise SourceAdapterError("invalid_payload")
        if (
            payload.get("schema") != "extella.psi_source.v1"
            or payload.get("source") != self.name
            or not _safe_audit_url(site_url)
            or not isinstance(probed, list)
            or not probed
            or len(probed) > plan.psi_max_urls
            or any(not _safe_audit_url(url) for url in probed)
            or len(set(probed)) != len(probed)
            or not isinstance(metrics, list)
            or len(metrics) > 9
            or not isinstance(sitefiles, Mapping)
            or set(sitefiles) != set(_PSI_SITEFILE_BYTES)
        ):
            raise SourceAdapterError("invalid_payload")
        assert isinstance(site_url, str)
        origin = urllib.parse.urlsplit(site_url)[:3]
        for url in probed:
            assert isinstance(url, str)
            if urllib.parse.urlsplit(url)[:3] != origin:
                raise SourceAdapterError("invalid_payload")
        seen: set[tuple[str, str]] = set()
        for entry in metrics:
            if (
                not isinstance(entry, Mapping)
                or entry.get("url") not in probed
                or entry.get("metric") not in _PSI_THRESHOLDS
            ):
                raise SourceAdapterError("invalid_payload")
            lab = _psi_number(entry.get("lab"))
            field = _psi_number(entry.get("field_p75"))
            if lab is None and field is None:
                raise SourceAdapterError("invalid_payload")
            key = (str(entry["url"]), str(entry["metric"]))
            if key in seen:
                raise SourceAdapterError("invalid_payload")
            seen.add(key)
        for section, byte_cap in _PSI_SITEFILE_BYTES.items():
            body = sitefiles.get(section)
            if (
                not isinstance(body, Mapping)
                or isinstance(body.get("http_status"), bool)
                or not isinstance(body.get("http_status"), int)
                or not 0 <= int(body["http_status"]) <= 599
                or not isinstance(body.get("truncated"), bool)
                or not isinstance(body.get("content"), str)
                or len(str(body["content"])) > byte_cap
            ):
                raise SourceAdapterError("invalid_payload")

    def parse(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult:
        try:
            declared_status = _declared_status(payload)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        if declared_status is not None:
            return _result(self.name, plan, declared_status[0], declared_status[1])
        blocking_reason = _blocking_reason(payload)
        if blocking_reason is not None:
            return _result(self.name, plan, "unavailable", blocking_reason)
        try:
            self.validate(payload, plan)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        site_url = str(payload["site_url"])
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(site_url))
        occurrences: list[SourceOccurrence] = []
        for entry in payload["metrics"]:
            assert isinstance(entry, Mapping)
            metric = str(entry["metric"])
            lab = _psi_number(entry.get("lab"))
            field = _psi_number(entry.get("field_p75"))
            verdict = _psi_metric_verdict(metric, lab, field)
            if verdict is None:
                continue
            known = _known_rule(self.name, f"psi-{metric}")
            if known is None:
                continue
            occurrences.append(
                SourceOccurrence(
                    source=self.name,
                    source_rule=f"psi-{metric}",
                    rule_key=known[0],
                    severity=known[1],
                    url=str(entry["url"]),
                    fact=verdict[1],
                    status=verdict[0],
                )
            )
        sitefiles = payload["sitefiles"]
        assert isinstance(sitefiles, Mapping)
        checks = (
            ("robots_txt", f"{origin}/robots.txt", "ROBOTS_TXT_INVALID", _check_robots),
            ("sitemap_xml", f"{origin}/sitemap.xml", "SITEMAP_INVALID", _check_sitemap),
            ("homepage_html", site_url, "SCHEMA_INVALID", _check_schema),
        )
        for section, url, source_rule, check in checks:
            body = sitefiles[section]
            assert isinstance(body, Mapping)
            if body.get("http_status") != 200:
                continue
            finding = check(str(body["content"]), bool(body["truncated"]))
            if finding is None:
                continue
            known = _known_rule(self.name, source_rule)
            if known is None:
                continue
            occurrences.append(
                SourceOccurrence(
                    source=self.name,
                    source_rule=source_rule,
                    rule_key=known[0],
                    severity=known[1],
                    url=url,
                    fact=finding,
                    status="fail",
                )
            )
        probed = payload["probed_urls"]
        assert isinstance(probed, list)
        notes: list[str] = []
        psi_api = payload["psi_api"]
        crux = payload["crux"]
        assert isinstance(psi_api, Mapping) and isinstance(crux, Mapping)
        if psi_api.get("status") == "degraded":
            notes.append(f"psi api {psi_api['reason']}: lab metrics unavailable")
        if crux.get("status") == "not_configured":
            notes.append("crux not_configured: field data unavailable without a key")
        elif crux.get("status") == "unavailable":
            notes.append(f"crux {crux['reason']}: field data unavailable")
        return SourceResult(
            source=self.name,
            status="ok",
            coverage=_coverage(self.name, plan, crawled_pages=len(probed), sampled_pages=0, notes=notes),
            occurrences=tuple(occurrences),
        )


_NU_SOURCE_RULE = "NU_ERRORS"
_TLS_HSTS_RULE = "TLS_HSTS"
_TLS_CSP_RULE = "TLS_CSP"
_TLS_GRADE_RULE = "TLS_GRADE"
_TLS_FAIL_GRADES = frozenset({"D", "E", "F", "T", "M"})
_TLS_WARN_GRADES = frozenset({"C"})
_TLS_GRADES = ("A+", "A", "A-", "B", "C", "D", "E", "F", "T", "M")
_HSTS_MIN_MAX_AGE = 31_536_000
_NU_FETCH_BYTES = 262_144


class NuHTMLAdapter:
    name = "NuHTML"
    capabilities = ("htmlval", "technical")

    def __init__(self, _catalog: object | None = None) -> None:
        self._catalog = _catalog

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None:
        if payload.get("schema") != "extella.nu_source.v1" or payload.get("source") != self.name:
            raise SourceAdapterError("invalid_payload")
        site_url = payload.get("site_url")
        if not _safe_audit_url(site_url) or payload.get("probed_urls") != [site_url]:
            raise SourceAdapterError("invalid_payload")
        fetch = payload.get("fetch")
        if (
            not isinstance(fetch, Mapping)
            or set(fetch) != {"http_status", "truncated", "bytes"}
            or isinstance(fetch.get("http_status"), bool)
            or not isinstance(fetch.get("http_status"), int)
            or fetch["http_status"] != 200
            or not isinstance(fetch.get("truncated"), bool)
            or not isinstance(fetch.get("bytes"), int)
            or isinstance(fetch.get("bytes"), bool)
            or not 0 <= int(fetch["bytes"]) <= _NU_FETCH_BYTES
            or fetch.get("truncated") is not False
        ):
            raise SourceAdapterError("invalid_payload")
        for key in ("errors", "warnings"):
            value = payload.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise SourceAdapterError("invalid_payload")
        exemplars = payload.get("exemplars")
        if not isinstance(exemplars, list) or len(exemplars) > 3:
            raise SourceAdapterError("invalid_payload")
        for exemplar in exemplars:
            if (
                not isinstance(exemplar, Mapping)
                or set(exemplar) != {"kind", "line", "message"}
                or exemplar.get("kind") not in {"error", "warning"}
                or (
                    exemplar.get("line") is not None
                    and (not isinstance(exemplar.get("line"), int) or isinstance(exemplar.get("line"), bool))
                )
                or not isinstance(exemplar.get("message"), str)
                or len(str(exemplar["message"])) > 200
            ):
                raise SourceAdapterError("invalid_payload")

    def parse(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult:
        try:
            declared_status = _declared_status(payload)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        if declared_status is not None:
            return _result(self.name, plan, declared_status[0], declared_status[1])
        blocking_reason = _blocking_reason(payload)
        if blocking_reason is not None:
            return _result(self.name, plan, "unavailable", blocking_reason)
        try:
            self.validate(payload, plan)
        except SourceAdapterError as error:
            return _result(self.name, plan, "failed", error.code)
        site_url = str(payload["site_url"])
        errors = int(payload["errors"])
        warnings = int(payload["warnings"])
        occurrences: list[SourceOccurrence] = []
        unmapped: list[str] = []
        status = "fail" if errors else ("warn" if warnings else None)
        if status is not None:
            known = _known_rule(self.name, _NU_SOURCE_RULE)
            if known is None:
                unmapped.append(_NU_SOURCE_RULE)
            else:
                occurrences.append(
                    SourceOccurrence(
                        source=self.name,
                        source_rule=_NU_SOURCE_RULE,
                        rule_key=known[0],
                        severity=known[1],
                        url=site_url,
                        fact=f"W3C Nu validator reports {errors} error(s) and {warnings} warning(s) on the homepage",
                        status=status,
                    )
                )
        notes: list[str] = []
        fetch = payload["fetch"]
        assert isinstance(fetch, Mapping)
        if fetch.get("truncated"):
            notes.append(f"nu fetch truncated: validated the first {_NU_FETCH_BYTES} bytes")
        return SourceResult(
            source=self.name,
            status="ok",
            coverage=_coverage(self.name, plan, crawled_pages=1, notes=notes, unmapped_rules=unmapped),
            occurrences=tuple(occurrences),
        )


class _SinglePageProbeAdapter:
    schema: str
    name: str

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None:
        if (payload.get("schema") != self.schema or payload.get("source") != self.name
                or not _safe_audit_url(payload.get("site_url"))
                or payload.get("probed_urls") != [payload.get("site_url")]):
            raise SourceAdapterError("invalid_payload")

    def parse(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult:
        try:
            declared = _declared_status(payload)
            if declared is not None:
                return _result(self.name, plan, declared[0], declared[1])
            blocking = _blocking_reason(payload)
            if blocking is not None:
                return _result(self.name, plan, "unavailable", blocking)
            self.validate(payload, plan)
            return self._validated_result(payload, plan)
        except (SourceAdapterError, ValueError, TypeError, KeyError):
            return _result(self.name, plan, "failed", "invalid_payload")


class SecurityProbeAdapter(_SinglePageProbeAdapter):
    name = "SecurityProbe"
    schema = "extella.tls_source.v1"
    capabilities = ("security",)

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None:
        super().validate(payload, plan)
        headers = payload.get("headers")
        labs = payload.get("ssl_labs")
        if (payload.get("fetch") != {"http_status": 200}
                or not isinstance(headers, Mapping)
                or set(headers) != {"hsts", "hsts_max_age", "csp", "x_content_type_options", "referrer_policy", "frame_guard"}
                or any(not isinstance(headers[key], bool) for key in headers if key != "hsts_max_age")
                or not isinstance(labs, Mapping)
                or set(labs) != {"status", "detail", "grade"}
                or labs.get("status") not in {"ready", "pending", "unavailable"}
                or (labs.get("detail") is not None and (not isinstance(labs["detail"], str) or len(labs["detail"]) > 80))
                or (labs.get("grade") is not None and labs.get("grade") not in _TLS_GRADES)
                or (labs.get("status") != "ready" and labs.get("grade") is not None)):
            raise SourceAdapterError("invalid_payload")
        age = headers["hsts_max_age"]
        if age is not None and (isinstance(age, bool) or not isinstance(age, int) or age < 0):
            raise SourceAdapterError("invalid_payload")
        if not headers["hsts"] and age is not None:
            raise SourceAdapterError("invalid_payload")

    def _validated_result(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult:
        headers, labs = payload["headers"], payload["ssl_labs"]
        url = str(payload["site_url"])
        checks: list[tuple[str, str, str]] = []
        notes = ["security headers sampled on one page; SSL Labs is a single cached assessment read"]
        age = headers["hsts_max_age"]
        if urllib.parse.urlsplit(url).scheme == "https" and (not headers["hsts"] or age is None or age < _HSTS_MIN_MAX_AGE):
            checks.append((_TLS_HSTS_RULE, "fail", "Homepage HSTS is missing, invalid, or has max-age below 31536000 seconds"))
        if not headers["csp"]:
            checks.append((_TLS_CSP_RULE, "fail", "Homepage response has no Content-Security-Policy header"))
        grade = labs["grade"]
        if grade in _TLS_FAIL_GRADES | _TLS_WARN_GRADES:
            checks.append((_TLS_GRADE_RULE, "fail" if grade in _TLS_FAIL_GRADES else "warn", f"SSL Labs cached TLS grade is {grade}"))
        if labs["status"] != "ready" or grade is None:
            notes.append("ssl_labs unavailable: no completed cached TLS grade; no polling performed")
        occurrences, unmapped = [], []
        for rule, status, fact in checks:
            known = _known_rule(self.name, rule)
            if known is None:
                unmapped.append(rule)
            else:
                occurrences.append(SourceOccurrence(self.name, rule, known[0], known[1], url, fact, status))
        return SourceResult(self.name, "ok", _coverage(self.name, plan, sampled_pages=1, notes=notes, unmapped_rules=unmapped), tuple(occurrences))


class CommonCrawlAdapter(_SinglePageProbeAdapter):
    name = "CommonCrawl"
    schema = "extella.cc_source.v1"
    capabilities = ("content",)

    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None:
        super().validate(payload, plan)
        if (not isinstance(payload.get("index"), str)
                or not re.fullmatch(r"CC-MAIN-[0-9]{4}-[0-9]{2}", payload["index"])
                or not isinstance(payload.get("excerpt"), str) or len(payload["excerpt"]) > 500):
            raise SourceAdapterError("invalid_payload")
        record = payload.get("record")
        if record is None:
            if payload["excerpt"]:
                raise SourceAdapterError("invalid_payload")
            return
        if (not isinstance(record, Mapping)
                or set(record) != {"url", "filename", "offset", "length", "status"}
                or not _safe_audit_url(record.get("url"))
                or record["url"] != payload["site_url"]
                or not isinstance(record.get("filename"), str)
                or not re.fullmatch(r"crawl-data/[a-zA-Z0-9_./-]+\.warc\.gz", record["filename"])
                or ".." in record["filename"].split("/")
                or any(isinstance(record.get(k), bool) or not isinstance(record.get(k), int) for k in ("offset", "length"))
                or record["offset"] < 0 or record["length"] <= 0 or record.get("status") != "200"):
            raise SourceAdapterError("invalid_payload")
        if payload["excerpt"]:
            _normalize_source_fact(payload["excerpt"])

    def _validated_result(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult:
        # Historical text is evidence only, never a current-site defect or a model instruction.
        notes = ["common_crawl historical excerpt only; no live pages crawled"]
        if payload["record"] is None:
            notes.append("common_crawl no archived record found in the selected index")
        elif not payload["excerpt"]:
            notes.append("common_crawl excerpt unavailable within the archive byte cap")
        return SourceResult(self.name, "ok", _coverage(self.name, plan, notes=notes))


def required_sources_satisfied(plan: AuditPlan, results: Sequence[SourceResult]) -> bool:
    statuses = {result.source: result.status for result in results}
    return all(statuses.get(source) == "ok" for source in plan.required_sources)


def missing_sources(plan: AuditPlan, results: Sequence[SourceResult]) -> tuple[str, ...]:
    statuses = {result.source: result.status for result in results}
    return tuple(source for source in plan.required_sources if statuses.get(source) != "ok")
