from __future__ import annotations

import unittest

from experts.seo_employee_profiles import (
    AuditMode,
    IndustryProfile,
    ProfileError,
    build_audit_plan,
)


class AuditPlanTests(unittest.TestCase):
    def test_every_profile_builds_a_bounded_full_audit_plan(self) -> None:
        for profile in IndustryProfile:
            with self.subTest(profile=profile):
                plan = build_audit_plan(profile, requested_max_pages=25, mode="full_audit")
                self.assertEqual(plan.profile, profile)
                self.assertEqual(plan.max_pages, 25)
                self.assertEqual(plan.required_sources, ("CrawlSEO", "SEOmator"))
                self.assertEqual(plan.optional_sources, ("GoogleSearchConsole", "DataForSEO"))
                self.assertEqual(len(plan.categories), 20)
                self.assertTrue(1 <= plan.performance_sample_pages <= 5)

    def test_pilot_cap_cannot_be_bypassed(self) -> None:
        with self.assertRaises(ProfileError):
            build_audit_plan(IndustryProfile.ECOMMERCE, requested_max_pages=101, mode="full_audit")

    def test_supported_modes_have_exact_single_and_multi_page_deadlines(self) -> None:
        for mode in AuditMode:
            with self.subTest(mode=mode, pages=1):
                single = build_audit_plan("service_b2b", requested_max_pages=1, mode=mode)
                self.assertEqual((single.overall_timeout_seconds, single.source_timeout_seconds), (180, 120))
            with self.subTest(mode=mode, pages=2):
                multi = build_audit_plan("service_b2b", requested_max_pages=2, mode=mode)
                self.assertEqual((multi.overall_timeout_seconds, multi.source_timeout_seconds), (900, 720))

    def test_default_cap_and_performance_sample_are_bounded(self) -> None:
        default = build_audit_plan("local_business", mode="daily_monitor")
        maximum = build_audit_plan("local_business", requested_max_pages=100, mode="daily_monitor")
        self.assertEqual(default.max_pages, 25)
        self.assertEqual(default.performance_sample_pages, 5)
        self.assertEqual(maximum.max_pages, 100)
        self.assertEqual(maximum.performance_sample_pages, 5)

    def test_invalid_profile_mode_and_page_values_are_rejected(self) -> None:
        for kwargs in (
            {"profile": "agency"},
            {"profile": "service_b2b", "mode": "weekly"},
            {"profile": "service_b2b", "requested_max_pages": 0},
            {"profile": "service_b2b", "requested_max_pages": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProfileError):
                build_audit_plan(**kwargs)


if __name__ == "__main__":
    unittest.main()
