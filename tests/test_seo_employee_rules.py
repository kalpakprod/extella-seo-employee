from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from experts.seo_employee_rules import (
    RuleCatalogError,
    RuleDefinition,
    canonical_rule,
    evidence_level,
    load_rule_catalog,
)


PINNED_SEOMATOR_REVISION = "bbca017b56086a2959382d8260b97021736ca18f"


class RuleCatalogTests(unittest.TestCase):
    @staticmethod
    def _entry(**changes: object) -> dict[str, object]:
        entry: dict[str, object] = {
            "rule_key": "first", "category": "core", "severity": "warning",
            "source_name": "First rule", "source_description": "Source description.",
            "source_severity": "warn", "severity_policy": "source-status-v1",
            "confirmed_fact": "A finding exists.", "remediation": "Fix it.", "actionable": True,
            "profiles": ["service_b2b"], "verification": "Repeat the audit.",
            "source_rules": {"SEOmator": "same"}, "corroboration": {"verified": [["SEOmator"]]},
            "version": "2.0.0",
        }
        entry.update(changes)
        return entry

    def test_checked_in_catalog_is_sorted_complete_and_covers_all_profiles(self) -> None:
        catalog = load_rule_catalog()
        self.assertEqual(len(catalog), 251)
        self.assertEqual(tuple(catalog), tuple(sorted(catalog)))
        self.assertEqual({definition.category for definition in catalog.values()}, {
            "a11y", "content", "core", "crawl", "eeat", "geo", "htmlval", "i18n",
            "images", "js", "legal", "links", "mobile", "perf", "redirect", "schema",
            "security", "social", "technical", "url",
        })
        self.assertEqual(
            {profile.value for definition in catalog.values() for profile in definition.profiles},
            {"service_b2b", "ecommerce", "local_business", "content_media", "saas_marketplace"},
        )
        actionable = [definition for definition in catalog.values() if definition.actionable]
        self.assertEqual([definition.rule_key for definition in actionable], ["meta-description-missing"])
        self.assertEqual(catalog["core-title-present"].source_name, "Title Tag Present")
        self.assertIsNone(catalog["core-title-present"].remediation)
        self.assertEqual(catalog["core-title-present"].source_severity, "fail")
        self.assertEqual(catalog["core-title-present"].severity, "critical")
        self.assertEqual(catalog["core-title-present"].severity_policy, "seomator-documentation-status-v1")

    def test_legacy_meta_description_mapping_remains_canonical_and_rule_specific(self) -> None:
        definition = canonical_rule("CrawlSEO", "MISSING_DESCRIPTION")
        self.assertIsNotNone(definition)
        assert definition is not None
        self.assertEqual(definition.rule_key, "meta-description-missing")
        self.assertEqual(canonical_rule("SEOmator", "core-description-present"), definition)
        self.assertEqual(evidence_level(definition, {"CrawlSEO", "SEOmator"}), "verified")
        self.assertEqual(evidence_level(definition, {"CrawlSEO", "SEOmator", "Extra"}), "supported")
        self.assertEqual(evidence_level(definition, {"SEOmator"}), "supported")
        self.assertEqual(evidence_level(definition, {"CrawlSEO", "DataForSEO"}), "supported")

    def test_known_coverage_rule_is_canonical_and_supported_by_its_observed_source(self) -> None:
        definition = canonical_rule("SEOmator", "core-title-present")
        self.assertIsNotNone(definition)
        assert definition is not None
        self.assertFalse(definition.actionable)
        self.assertEqual(evidence_level(definition, {"SEOmator"}), "supported")
        self.assertEqual(evidence_level(definition, {"CrawlSEO", "SEOmator"}), "supported")

    def test_unknown_rule_has_no_canonical_definition(self) -> None:
        self.assertIsNone(canonical_rule("SEOmator", "not-a-real-rule"))

    def test_rule_definition_normalizes_mutable_public_inputs(self) -> None:
        sources = {"SEOmator": "core-title-present"}
        definition = RuleDefinition("key", "core", "critical", "Name", "Description", "fail", "policy", None, None, True, frozenset(), sources, (frozenset({"SEOmator"}),), "verify", "2.0.0")
        sources["CrawlSEO"] = "MISSING_TITLE"
        self.assertEqual(dict(definition.source_rules), {"SEOmator": "core-title-present"})
        with self.assertRaises(TypeError):
            definition.source_rules["CrawlSEO"] = "MISSING_TITLE"  # type: ignore[index]

    def test_exporter_is_byte_repeatable_and_records_registry_provenance(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = root.parent.parent / "work" / "seo-audit-skill"
        dependency_root = Path(tempfile.gettempdir()) / "seo-registry-deps"
        checked_in = root / "experts" / "rule_catalog.v2.json"
        if not source.is_dir() or not (dependency_root / "node_modules" / "cheerio").is_dir():
            catalog = json.loads(checked_in.read_text(encoding="utf-8"))
            self.assertEqual(catalog["upstream"]["mode"], "registry")
            self.assertEqual(catalog["upstream"]["revision"], PINNED_SEOMATOR_REVISION)
            return
        environment = {**os.environ, "SEOMATOR_DEPENDENCY_ROOT": str(dependency_root)}
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            command = ["node", "tools/export_rule_catalog.mjs", "--source", str(source)]
            subprocess.run([*command, "--output", str(first)], cwd=root, env=environment, check=True, capture_output=True, text=True)
            subprocess.run([*command, "--output", str(second)], cwd=root, env=environment, check=True, capture_output=True, text=True)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes(), checked_in.read_bytes())
            self.assertEqual(
                json.loads(first.read_text(encoding="utf-8")),
                json.loads(checked_in.read_text(encoding="utf-8")),
            )
            provenance = json.loads(first.read_text(encoding="utf-8"))["upstream"]
            self.assertEqual(provenance["mode"], "registry")
            self.assertEqual(provenance["revision"], PINNED_SEOMATOR_REVISION)
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
                PINNED_SEOMATOR_REVISION,
            )

    def test_loader_rejects_unknown_severity(self) -> None:
        base = {
            "catalog_version": "2.0.0",
            "rules": [self._entry(rule_key="second", severity="unknown")],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(RuleCatalogError, "rule second has unknown severity"):
                load_rule_catalog(path)

    def test_loader_rejects_duplicate_source_mapping(self) -> None:
        base = {
            "catalog_version": "2.0.0",
            "rules": [self._entry(), self._entry(rule_key="second")],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(RuleCatalogError, "source rule mappings must be unique"):
                load_rule_catalog(path)

    def test_loader_rejects_corroboration_source_absent_from_mapping(self) -> None:
        base = {"catalog_version": "2.0.0", "rules": [self._entry(corroboration={"verified": [["DataForSEO"]]})]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(RuleCatalogError, "rule first has invalid corroboration source"):
                load_rule_catalog(path)


if __name__ == "__main__":
    unittest.main()
