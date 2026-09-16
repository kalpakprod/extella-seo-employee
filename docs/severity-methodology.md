# Severity methodology

This document defines what a severity level means in the v2 SEO report and how an
occurrence gets one. It implements the severity requirement of `SC-SEO-013`
(versioned rule catalog with severity/verification/corroboration) and the honesty
requirement of `SC-SEO-021` (a partial or failed run must not overstate a defect).

## Levels

| Level | Meaning | User-facing consequence |
|---|---|---|
| `critical` | A confirmed failure of an SEO-critical rule, backed by verified evidence. | Blocking defect; the page or site loses a documented capability that search engines rely on. |
| `warning` | A confirmed non-blocking problem, or an unconfirmed occurrence of a warning-level rule. | Should be fixed, but the audit does not currently prove user-visible breakage. |
| `info` | An advisory observation with no confirmed failure and no blocking rule. | Optional improvement. |

The catalog (`experts/rule_catalog.v2.json`) stores the severity a rule has when it
is **confirmed to fail**. That catalog level, not the source message, is the unit
of meaning. `severity_policy` on each catalog entry records which source
documentation the level was derived from.

## Confirmation and downgrade

`effective_severity(catalog_severity, confirmed_failure=...)` in
`experts/seo_employee_service.py` maps an occurrence to `(severity, severity_basis)`:

- `confirmed_failure=True` → `(catalog_severity, "confirmed_failure")`.
- `confirmed_failure=False` → `(_SEVERITY_DOWNGRADE[catalog_severity], "unconfirmed_occurrence")`.

`_SEVERITY_DOWNGRADE` moves `critical → warning`, `warning → info`, and leaves
`info` at `info`. An occurrence is confirmed when at least one adapter that is
authoritative for the rule reported a failure state for it; a bare advisory or a
single unsupported observation is not confirmation. Corroboration is handled by
`evidence_level` (`verified` when at least two independent sources agree,
`supported` otherwise) and is reported separately so it is never conflated with
severity.

Two invariants are enforced by tests:

- Every stored report finding carries both `severity` and `severity_basis`.
- `validate_model_input` accepts only the documented pair: the catalog level (the
  confirmed interpretation) or the single downgraded level.

Errors are never hidden or re-graded to make a severity distribution look better:
grouping is the only operation that changes how many cards the user sees.

## Relationship to the rest of the report

- **Evidence** answers "how sure are we" (`evidence_level`, `evidence`); severity
  answers "how bad is it". They are independent fields.
- **Coverage** answers "what was actually measured" (`status`, `subchecks`,
  `missing_data`). A missing subcheck is a coverage fact, not a downgraded finding:
  an unavailable measurement must not silently become a low-severity defect, and a
  stale or historical observation (for example a Common Crawl excerpt) must not be
  presented as a current defect.
- **Grouping** collapses occurrences that share a `rule_key` into one card with a
  bounded `affected_pages` list and a true `affected_pages_count`. Grouping keeps
  the strongest severity and `severity_basis` seen in the group.

## Adding a rule

When a new source rule is added to the catalog, set the severity it deserves on a
confirmed failure and cite the source in `severity_policy`. If the mapping is not
known yet, leave the rule unmapped: it stays visible in
`coverage.unmapped_rules` and never becomes a user task.
