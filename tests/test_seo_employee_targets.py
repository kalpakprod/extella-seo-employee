from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from experts.seo_employee_targets import (
    TargetConfigError,
    migrate_config,
    migrate_config_file,
    target_paths,
    validate_config,
)


V1_FIXTURE = {
    "schema": "extella.seo_employee_config.v1",
    "site_id": "example.com",
    "site_url": "https://example.com/",
    "daily_run_time": "21:00",
    "timezone": "Asia/Tashkent",
}


class TargetConfigTests(unittest.TestCase):
    def test_v1_migrates_to_one_service_b2b_target_with_stable_id(self) -> None:
        migrated = migrate_config(V1_FIXTURE)
        target = migrated["targets"][0]
        self.assertEqual(migrated["schema"], "extella.seo_employee_config.v2")
        self.assertEqual(target["profile"], "service_b2b")
        self.assertEqual(target["site_url"], "https://example.com/")
        self.assertEqual(target["daily_run_time"], "21:00")
        self.assertEqual(target["timezone"], "Asia/Tashkent")
        self.assertEqual(target["language"], "ru")
        self.assertEqual(target["region"], "GLOBAL")
        self.assertEqual(target["max_pages"], 25)
        self.assertFalse(target["ownership_confirmed"])
        self.assertEqual(target["target_id"], "target-example-com-0f115db0")
        self.assertEqual(target["target_id"], migrate_config(V1_FIXTURE)["targets"][0]["target_id"])

    def test_v2_migration_is_idempotent(self) -> None:
        migrated = migrate_config(V1_FIXTURE)
        self.assertEqual(migrate_config(migrated), migrated)

    def test_v2_validation_rejects_invalid_profile_language_region_and_missing_consent(self) -> None:
        migrated = migrate_config(V1_FIXTURE)
        target = migrated["targets"][0]
        for field, value in (("profile", "agency"), ("language", "russian"), ("language", "ru-KZ"), ("region", "KAZ"), ("region", "ZZ"), ("ownership_confirmed", None)):
            invalid = json.loads(json.dumps(migrated))
            invalid["targets"][0][field] = value
            with self.subTest(field=field), self.assertRaises(TargetConfigError):
                validate_config(invalid)

    def test_language_and_region_accept_only_supported_values(self) -> None:
        migrated = migrate_config(V1_FIXTURE)
        for language, region in (("ru", "KZ"), ("en", "US"), ("en", "GLOBAL")):
            valid = json.loads(json.dumps(migrated))
            valid["targets"][0].update({"language": language, "region": region})
            self.assertEqual(validate_config(valid)["targets"][0]["language"], language)

    def test_target_id_survives_same_host_collision_lifecycle(self) -> None:
        original = migrate_config(V1_FIXTURE)
        first = original["targets"][0]
        second_url = "https://example.com/shop/"
        second = dict(first)
        second.update({
            "site_url": second_url,
            "target_id": "target-example-com-" + hashlib.sha256(second_url.encode()).hexdigest()[:8],
        })
        combined = {"schema": original["schema"], "targets": [first, second]}
        self.assertEqual(validate_config(combined)["targets"][0]["target_id"], first["target_id"])
        self.assertEqual(validate_config({"schema": original["schema"], "targets": [first]})["targets"][0]["target_id"], first["target_id"])

    def test_file_migration_creates_backup_only_after_validated_v2_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = json.dumps(V1_FIXTURE, sort_keys=True)
            path.write_text(original, encoding="utf-8")
            migrated = migrate_config_file(path)
            backup = Path(f"{path}.v1.backup")
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), V1_FIXTURE)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), migrated)
            self.assertEqual(migrate_config_file(path), migrated)
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), V1_FIXTURE)

    def test_invalid_v1_file_is_unchanged_and_has_no_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            invalid = {**V1_FIXTURE, "timezone": "Not/A_Timezone"}
            original = json.dumps(invalid, sort_keys=True)
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(TargetConfigError):
                migrate_config_file(path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse(Path(f"{path}.v1.backup").exists())

    def test_stale_v1_backup_refuses_to_overwrite_current_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = json.dumps(V1_FIXTURE, sort_keys=True).encode("utf-8")
            path.write_bytes(original)
            backup = Path(f"{path}.v1.backup")
            backup.write_bytes(b'{"stale":true}\n')
            with self.assertRaises(TargetConfigError):
                migrate_config_file(path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(backup.read_bytes(), b'{"stale":true}\n')

    def test_target_paths_reject_traversal_and_keep_target_state_isolated(self) -> None:
        root = Path("C:/audit-root")
        first = target_paths(root, "target-example-12345678")
        second = target_paths(root, "target-other-87654321")
        self.assertNotEqual(first, second)
        self.assertEqual(first["state"], root / "state" / "targets" / "target-example-12345678" / "state.json")
        self.assertEqual(second["report"], root / "reports" / "target-other-87654321" / "latest.json")
        for target_id in ("../escape", "target/escape", "target-..", ""):
            with self.subTest(target_id=target_id), self.assertRaises(TargetConfigError):
                target_paths(root, target_id)


if __name__ == "__main__":
    unittest.main()
