"""Source-specific parsing with explicit coverage and safe failure reasons."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Literal, Mapping, Protocol, Sequence
import unicodedata
import urllib.parse

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

    def as_dict(self) -> dict[str, object]:
        return {
            "planned_pages": self.planned_pages,
            "crawled_pages": self.crawled_pages,
            "sampled_pages": self.sampled_pages,
            "categories": list(self.categories),
            "completed_sources": list(self.completed_sources),
            "unavailable_sources": list(self.unavailable_sources),
            "unmapped_rules": list(self.unmapped_rules),
        }


@dataclass(frozen=True)
class SourceOccurrence:
    source: str
    source_rule: str
    rule_key: str
    severity: str
    url: str
    fact: str


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
                if result["status"] != "fail":
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
                if result.get("status") != "fail":
                    continue
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


def required_sources_satisfied(plan: AuditPlan, results: Sequence[SourceResult]) -> bool:
    statuses = {result.source: result.status for result in results}
    return all(statuses.get(source) == "ok" for source in plan.required_sources)


def missing_sources(plan: AuditPlan, results: Sequence[SourceResult]) -> tuple[str, ...]:
    statuses = {result.source: result.status for result in results}
    return tuple(source for source in plan.required_sources if statuses.get(source) != "ok")
