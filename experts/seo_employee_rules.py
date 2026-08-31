"""Immutable canonical SEO rule catalog for Universal SEO Employee v2."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from experts.seo_employee_profiles import IndustryProfile


CATALOG_PATH = Path(__file__).with_name("rule_catalog.v2.json")
_SEVERITIES = frozenset({"critical", "warning", "info"})


class RuleCatalogError(ValueError):
    """Raised when the checked-in catalog violates its deterministic schema."""


@dataclass(frozen=True)
class RuleDefinition:
    rule_key: str
    category: str
    severity: str
    source_name: str
    source_description: str
    source_severity: str
    severity_policy: str
    confirmed_fact: str | None
    remediation: str | None
    actionable: bool
    profiles: frozenset[IndustryProfile]
    source_rules: Mapping[str, str]
    verified_source_sets: tuple[frozenset[str], ...]
    verification: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", frozenset(self.profiles))
        object.__setattr__(self, "source_rules", MappingProxyType(dict(sorted(self.source_rules.items()))))
        object.__setattr__(self, "verified_source_sets", tuple(frozenset(item) for item in self.verified_source_sets))


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RuleCatalogError(f"{field} must be a non-empty string")
    return value


def _parse_rule(value: object, catalog_version: str) -> RuleDefinition:
    if not isinstance(value, dict):
        raise RuleCatalogError("rule entry must be an object")
    rule_key = _require_string(value.get("rule_key"), "rule_key")
    category = _require_string(value.get("category"), "category")
    severity = _require_string(value.get("severity"), "severity")
    if severity not in _SEVERITIES:
        raise RuleCatalogError(f"rule {rule_key} has unknown severity")
    raw_profiles = value.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise RuleCatalogError(f"rule {rule_key} must declare profiles")
    try:
        profiles = frozenset(IndustryProfile(item) for item in raw_profiles)
    except (TypeError, ValueError) as error:
        raise RuleCatalogError(f"rule {rule_key} has invalid profile") from error
    if len(profiles) != len(raw_profiles):
        raise RuleCatalogError(f"rule {rule_key} has duplicate profiles")
    raw_source_rules = value.get("source_rules")
    if not isinstance(raw_source_rules, dict) or not raw_source_rules:
        raise RuleCatalogError(f"rule {rule_key} must map a source rule")
    source_rules = {
        _require_string(source, "source name"): _require_string(source_rule, "source rule")
        for source, source_rule in raw_source_rules.items()
    }
    if len(source_rules) != len(raw_source_rules):
        raise RuleCatalogError(f"rule {rule_key} has duplicate source names")
    raw_corroboration = value.get("corroboration")
    if not isinstance(raw_corroboration, dict) or set(raw_corroboration) != {"verified"}:
        raise RuleCatalogError(f"rule {rule_key} must declare verified corroboration")
    raw_verified = raw_corroboration["verified"]
    actionable = value.get("actionable")
    if not isinstance(actionable, bool):
        raise RuleCatalogError(f"rule {rule_key} must declare actionable")
    if not isinstance(raw_verified, list) or (actionable and not raw_verified):
        raise RuleCatalogError(f"rule {rule_key} has invalid verified source sets")
    source_sets: list[frozenset[str]] = []
    for source_set in raw_verified:
        if not isinstance(source_set, list) or not source_set:
            raise RuleCatalogError(f"rule {rule_key} has invalid corroboration set")
        normalized = frozenset(_require_string(source, "corroboration source") for source in source_set)
        if len(normalized) != len(source_set) or not normalized.issubset(source_rules):
            raise RuleCatalogError(f"rule {rule_key} has invalid corroboration source")
        source_sets.append(normalized)
    if len(set(source_sets)) != len(source_sets):
        raise RuleCatalogError(f"rule {rule_key} has duplicate corroboration sets")
    confirmed_fact = value.get("confirmed_fact")
    remediation = value.get("remediation")
    verification = value.get("verification")
    if actionable:
        confirmed_fact = _require_string(confirmed_fact, "confirmed_fact")
        remediation = _require_string(remediation, "remediation")
        verification = _require_string(verification, "verification")
    elif any(item is not None for item in (confirmed_fact, remediation, verification)):
        raise RuleCatalogError(f"coverage-only rule {rule_key} must not claim an action")
    version = _require_string(value.get("version", catalog_version), "version")
    return RuleDefinition(
        rule_key=rule_key,
        category=category,
        severity=severity,
        source_name=_require_string(value.get("source_name"), "source_name"),
        source_description=_require_string(value.get("source_description"), "source_description"),
        source_severity=_require_string(value.get("source_severity"), "source_severity"),
        severity_policy=_require_string(value.get("severity_policy"), "severity_policy"),
        confirmed_fact=confirmed_fact,
        remediation=remediation,
        actionable=actionable,
        profiles=profiles,
        source_rules=MappingProxyType(dict(sorted(source_rules.items()))),
        verified_source_sets=tuple(source_sets),
        verification=verification if isinstance(verification, str) else "",
        version=version,
    )


def _load_catalog(path: Path) -> Mapping[str, RuleDefinition]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuleCatalogError("rule catalog cannot be read") from error
    if not isinstance(raw, dict):
        raise RuleCatalogError("rule catalog must be an object")
    catalog_version = _require_string(raw.get("catalog_version"), "catalog_version")
    entries = raw.get("rules")
    if not isinstance(entries, list) or not entries:
        raise RuleCatalogError("rule catalog must contain rules")
    definitions = [_parse_rule(entry, catalog_version) for entry in entries]
    keys = [definition.rule_key for definition in definitions]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise RuleCatalogError("rule catalog keys must be unique and sorted")
    source_mapping: set[tuple[str, str]] = set()
    for definition in definitions:
        for item in definition.source_rules.items():
            if item in source_mapping:
                raise RuleCatalogError("source rule mappings must be unique")
            source_mapping.add(item)
    return MappingProxyType({definition.rule_key: definition for definition in definitions})


@lru_cache(maxsize=1)
def _default_catalog() -> Mapping[str, RuleDefinition]:
    return _load_catalog(CATALOG_PATH)


def load_rule_catalog(path: Path | None = None) -> Mapping[str, RuleDefinition]:
    """Load an immutable sorted catalog; an explicit path is useful for validation tests."""
    return _default_catalog() if path is None else _load_catalog(path)


def canonical_rule(source: str, source_rule: str) -> RuleDefinition | None:
    for definition in load_rule_catalog().values():
        if definition.source_rules.get(source) == source_rule:
            return definition
    return None


def evidence_level(definition: RuleDefinition | None, sources: Iterable[str]) -> str:
    """Classify only with this rule's corroboration policy, never source count."""
    if definition is None:
        return "unverified"
    observed = frozenset(sources)
    if any(required == observed for required in definition.verified_source_sets):
        return "verified"
    if observed.intersection(definition.source_rules):
        return "supported"
    return "unverified"
