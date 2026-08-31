"""Pure, deterministic audit-plan policy for Universal SEO Employee v2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProfileError(ValueError):
    """Raised when a requested audit plan exceeds the closed-pilot contract."""


class IndustryProfile(StrEnum):
    SERVICE_B2B = "service_b2b"
    ECOMMERCE = "ecommerce"
    LOCAL_BUSINESS = "local_business"
    CONTENT_MEDIA = "content_media"
    SAAS_MARKETPLACE = "saas_marketplace"


class AuditMode(StrEnum):
    FULL_AUDIT = "full_audit"
    DAILY_MONITOR = "daily_monitor"
    SEARCH_PERFORMANCE = "search_performance"
    WORK_PLAN = "work_plan"


DEFAULT_MAX_PAGES = 25
HARD_MAX_PAGES = 100
_CATEGORIES = (
    "core", "technical", "perf", "links", "images", "security", "schema", "social",
    "content", "a11y", "i18n", "crawl", "url", "mobile", "legal", "eeat", "redirect",
    "geo", "htmlval", "js",
)
_PROFILE_PRIORITIES = {
    IndustryProfile.SERVICE_B2B: ("core", "technical", "content", "eeat", "schema"),
    IndustryProfile.ECOMMERCE: ("core", "technical", "images", "schema", "perf"),
    IndustryProfile.LOCAL_BUSINESS: ("core", "technical", "schema", "eeat", "social"),
    IndustryProfile.CONTENT_MEDIA: ("content", "eeat", "core", "schema", "social"),
    IndustryProfile.SAAS_MARKETPLACE: ("core", "technical", "perf", "js", "security"),
}


@dataclass(frozen=True)
class AuditPlan:
    profile: IndustryProfile
    mode: AuditMode
    max_pages: int
    categories: tuple[str, ...]
    required_sources: tuple[str, ...]
    optional_sources: tuple[str, ...]
    performance_sample_pages: int
    overall_timeout_seconds: int
    source_timeout_seconds: int


def _as_profile(value: IndustryProfile | str) -> IndustryProfile:
    try:
        return IndustryProfile(value)
    except (TypeError, ValueError) as error:
        raise ProfileError("profile is invalid") from error


def _as_mode(value: AuditMode | str) -> AuditMode:
    try:
        return AuditMode(value)
    except (TypeError, ValueError) as error:
        raise ProfileError("mode is invalid") from error


def _ordered_categories(profile: IndustryProfile) -> tuple[str, ...]:
    priority = _PROFILE_PRIORITIES[profile]
    return priority + tuple(category for category in _CATEGORIES if category not in priority)


def build_audit_plan(
    profile: IndustryProfile | str,
    *,
    requested_max_pages: int | None = None,
    mode: AuditMode | str = AuditMode.FULL_AUDIT,
) -> AuditPlan:
    """Return the bounded plan without I/O, source calls, or model decisions."""
    selected_profile = _as_profile(profile)
    selected_mode = _as_mode(mode)
    max_pages = DEFAULT_MAX_PAGES if requested_max_pages is None else requested_max_pages
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= HARD_MAX_PAGES:
        raise ProfileError("requested_max_pages must be between 1 and 100")
    single_page = max_pages == 1
    return AuditPlan(
        profile=selected_profile,
        mode=selected_mode,
        max_pages=max_pages,
        categories=_ordered_categories(selected_profile),
        required_sources=("CrawlSEO", "SEOmator"),
        optional_sources=("GoogleSearchConsole", "DataForSEO"),
        performance_sample_pages=min(max_pages, 5),
        overall_timeout_seconds=180 if single_page else 900,
        source_timeout_seconds=120 if single_page else 720,
    )
