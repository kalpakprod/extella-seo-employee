# Universal SEO Employee 2.0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Превратить проверенный одностраничный Extella SEO Employee 1.0.0 в универсальный, multi-site и multi-page SEO-сотрудник 2.0 с бесплатным техническим ядром, опциональной поисковой аналитикой и подтверждением любых внешних действий.

**Architecture:** Существующий детерминированный сервис остаётся владельцем фактов, состояния и безопасности. Новые модули Profiles, Rule Catalog, Source Adapters и Targets формируют `AuditPlan`; CrawlSEO и SEOmator исполняют его, а Agent Zero только объясняет подтверждённые данные и составляет рабочий план. GSC/DataForSEO остаются опциональными и не блокируют бесплатный аудит.

**Tech Stack:** Python 3.12 stdlib, Node.js 24, TypeScript upstream workers, Agent Zero 2.11, CrawlSEO, SEOmator, PostgreSQL, Docker Compose, vanilla HTML/CSS/JS Extella panel.

**Design:** `docs/blueprints/universal-seo-employee-v2.md`.

---

## Execution rules

- Работать из отдельного Git worktree, если проект будет импортирован в Git. Текущий каталог Git-репозиторием не является; до отдельного разрешения пользователя не выполнять `git init`, commit или publish.
- Сначала обновить Feature/Component Contracts, затем писать тесты из новых норм, затем реализацию.
- Один файл имеет одного владельца в каждой волне. Sol владеет архитектурой, `experts/seo_employee_service.py`, интеграцией и финальной проверкой.
- Не перезаписывать текущие ZIP и `dist/ct160-verification.json` до финального release task.
- Не добавлять dependency в продуктовый Python-код. Для новых Python-модулей использовать stdlib `dataclasses`, `enum`, `json`, `pathlib` и существующие функции.
- Не ослаблять текущие SSRF, secret, no-tools, cancellation, baseline и state tests.
- Любое отклонение от Blueprint сначала изменяет Blueprint и соответствующий Contract.

## Parallel ownership map

| Lane | Exclusive ownership | Route |
|---|---|---|
| Sol integration | `experts/seo_employee_service.py`, `experts/seo_employee_run.py`, `runtime/product/server.py`, final docs synchronization | root/Sol |
| Domain worker | `experts/seo_employee_profiles.py`, `experts/seo_employee_rules.py`, `experts/seo_employee_targets.py`, matching new Python tests | bounded implementation worker |
| Source/runtime worker | `experts/seo_employee_sources.py`, `runtime/source_proxy.py`, `runtime/worker_server.mjs`, both source entrypoints, `tools/crawlseo_once.mjs`, runtime tests | complex implementation worker |
| UI worker | `app/index.html`, `app/app.js`, `app/styles.css`, `tests/ui/ui_contract.test.mjs` | bounded implementation worker |
| Docs worker | Feature/Service/UI Contracts, traceability, passport, listing | bounded implementation worker |

Waves are sequential where ownership intersects: Contracts → domain modules/source protocol/UI skeleton in parallel → Sol integration → GSC lane → packaging/E2E → independent review.

### Task 0: Capture the immutable 1.0 baseline

**Files:**
- Create: `docs/verification/v2-progress.md`
- Read only: `dist/build.json`
- Read only: `dist/ct160-verification.json`

**Step 1: Run the existing Python suite**

Run from the product root:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Expected: all existing tests pass. Record the exact count and command output summary; do not copy secrets or full environment output.

**Step 2: Run the existing Node suite**

```powershell
node --test tests/safe_fetch.test.mjs tests/ui/ui_contract.test.mjs
```

Expected: all existing tests pass.

**Step 3: Verify existing release artifacts without rebuilding**

```powershell
Get-FileHash dist/extella-seo-employee-runtime-1.0.0.zip -Algorithm SHA256
Get-FileHash dist/extella-seo-employee-page-1.0.0.zip -Algorithm SHA256
```

Expected: hashes match `dist/build.json`.

**Step 4: Record the baseline**

Write `docs/verification/v2-progress.md` with date, exact test counts, hashes, and a statement that 1.0 evidence remains historical after v2 modifications.

### Task 1: Upgrade the normative contracts to v2 before code

**Files:**
- Modify: `docs/contracts/feature-seo-audit-monitoring.md`
- Modify: `docs/contracts/service-seo-runner.md`
- Modify: `docs/contracts/ui-seo-panel.md`
- Modify: `docs/verification/traceability.md`
- Modify: `automation_passport.yaml`
- Modify: `listing.json`

**Step 1: Replace obsolete v1 promises**

Update `FC-SEO-003..013`, `SC-SEO-001..031`, and `UC-SEO-003..016` where one URL, one rule, one site, fixed two-source readiness, GSC prohibition, and absolute no-action UI are no longer correct.

**Step 2: Add explicit v2 norms**

Add at minimum:

- `FC-SEO-017`: five deterministic site profiles;
- `FC-SEO-018`: coverage and unmapped rules;
- `FC-SEO-019`: multiple isolated targets;
- `FC-SEO-020`: work plan, content brief, and action proposal;
- `FC-SEO-021`: ownership confirmation and explicit write approval;
- `SC-SEO-032`: `AuditPlan` construction;
- `SC-SEO-033`: rule-specific corroboration;
- `SC-SEO-034`: config v1 → v2 migration;
- `SC-SEO-035`: per-target storage;
- `SC-SEO-036`: persistent FIFO queue;
- `SC-SEO-037`: multi-page limits and sampling;
- `SC-SEO-038`: required/optional source policy;
- `SC-SEO-039`: GSC/DataForSEO `not_configured` behavior;
- `SC-SEO-040`: model-failure deterministic result;
- `SC-SEO-041`: action proposal without implicit execution;
- `UC-SEO-022`: target list/profile form;
- `UC-SEO-023`: coverage panel;
- `UC-SEO-024`: queue state;
- `UC-SEO-025`: ownership gate;
- `UC-SEO-026`: action proposal confirmation.

**Step 3: Derive contract-test IDs**

Extend traceability with `CT-SC-015..024` and `CT-UC-010..014`. Every new FC appears exactly once in the correspondence section; every SC/UC is the source of at least one CT.

**Step 4: Update the passport and listing conservatively**

Set version `2.0.0`, schemas v2, five profiles, multi-target configuration, read-only optional integrations, and action proposals. Do not claim GSC live readiness until its live E2E exists.

**Step 5: Validate documentation**

Run:

```powershell
python ../../work/extella-agent-standards/tools/check_writing_style.py docs/contracts/feature-seo-audit-monitoring.md docs/contracts/service-seo-runner.md docs/contracts/ui-seo-panel.md docs/blueprints/universal-seo-employee-v2.md
python ../../work/extella-agent-standards/tools/check_automation_passport.py automation_passport.yaml --json
```

Expected: zero style and passport errors.

### Task 2: Add deterministic profiles and audit plans

**Files:**
- Create: `experts/seo_employee_profiles.py`
- Create: `tests/test_seo_employee_profiles.py`

**Step 1: Write failing profile tests**

```python
def test_every_profile_builds_a_bounded_plan() -> None:
    for profile in IndustryProfile:
        plan = build_audit_plan(profile, requested_max_pages=25, mode="full_audit")
        assert plan.max_pages == 25
        assert 1 <= plan.performance_sample_pages <= 5
        assert plan.required_sources == ("CrawlSEO", "SEOmator")


def test_pilot_cap_cannot_be_bypassed() -> None:
    with self.assertRaises(ProfileError):
        build_audit_plan(IndustryProfile.ECOMMERCE, requested_max_pages=101, mode="full_audit")
```

**Step 2: Run and prove RED**

```powershell
python -m unittest tests.test_seo_employee_profiles -v
```

Expected: import failure because the module does not exist.

**Step 3: Implement the minimal typed model**

```python
class IndustryProfile(StrEnum):
    SERVICE_B2B = "service_b2b"
    ECOMMERCE = "ecommerce"
    LOCAL_BUSINESS = "local_business"
    CONTENT_MEDIA = "content_media"
    SAAS_MARKETPLACE = "saas_marketplace"


@dataclass(frozen=True)
class AuditPlan:
    profile: IndustryProfile
    mode: str
    max_pages: int
    categories: tuple[str, ...]
    required_sources: tuple[str, ...]
    optional_sources: tuple[str, ...]
    performance_sample_pages: int
    overall_timeout_seconds: int
    source_timeout_seconds: int
```

Use exact constants: default 25, hard cap 100, single-page deadlines 180/120, multi-page 900/720. Plans are pure data; no I/O or model calls.

**Step 4: Run GREEN**

```powershell
python -m unittest tests.test_seo_employee_profiles -v
```

Expected: pass.

### Task 3: Add a versioned rule catalog without hand-copying 251 rules into code

**Files:**
- Create: `experts/seo_employee_rules.py`
- Create: `experts/rule_catalog.v2.json`
- Create: `tools/export_rule_catalog.mjs`
- Create: `tests/test_seo_employee_rules.py`

**Step 1: Write failing catalog tests**

Test that:

- every entry has `rule_key`, category, severity, profiles, verification text, source mappings, and corroboration;
- all five profiles have rules;
- the old `meta-description-missing` mapping remains canonical;
- duplicate source mappings and unknown severity fail loading;
- two arbitrary sources do not automatically produce `verified`;
- the checked-in JSON is deterministic and contains all upstream SEOmator rule IDs exported from the pinned commit.

**Step 2: Run and prove RED**

```powershell
python -m unittest tests.test_seo_employee_rules -v
```

Expected: missing module/catalog failure.

**Step 3: Export upstream metadata deterministically**

`tools/export_rule_catalog.mjs` must import SEOmator's registry from the pinned source, sort by rule ID, and write stable JSON. It must enrich only project-owned fields: applicable profiles, canonical alias, verification template, and corroboration policy. Do not parse TypeScript with regex.

Example output entry:

```json
{
  "rule_key": "meta-description-missing",
  "category": "core",
  "severity": "warning",
  "profiles": ["service_b2b", "ecommerce", "local_business", "content_media", "saas_marketplace"],
  "source_rules": {"CrawlSEO": "MISSING_DESCRIPTION", "SEOmator": "core-description-present"},
  "corroboration": {"verified": [["CrawlSEO", "SEOmator"]]},
  "verification": "Повторить проверку и убедиться, что правило больше не срабатывает."
}
```

**Step 4: Implement the loader**

```python
@dataclass(frozen=True)
class RuleDefinition:
    rule_key: str
    category: str
    severity: str
    profiles: frozenset[IndustryProfile]
    source_rules: Mapping[str, str]
    verified_source_sets: tuple[frozenset[str], ...]
    verification: str
```

Expose `load_rule_catalog()`, `canonical_rule(source, source_rule)`, and `evidence_level(definition, sources)`. Cache the immutable catalog with `functools.lru_cache(maxsize=1)`.

**Step 5: Regenerate and verify determinism**

Run exporter twice and compare SHA-256. Expected: identical output.

### Task 4: Introduce config v2 and atomic v1 migration

**Files:**
- Create: `experts/seo_employee_targets.py`
- Create: `tests/test_seo_employee_targets.py`
- Modify later by Sol: `experts/seo_employee_service.py:288-318`

**Step 1: Write failing migration tests**

```python
def test_v1_migrates_to_one_service_b2b_target() -> None:
    migrated = migrate_config(V1_FIXTURE)
    assert migrated["schema"] == "extella.seo_employee_config.v2"
    assert migrated["targets"][0]["profile"] == "service_b2b"
    assert migrated["targets"][0]["site_url"] == V1_FIXTURE["site_url"]


def test_target_paths_do_not_overlap() -> None:
    assert target_paths(ROOT, "target-a") != target_paths(ROOT, "target-b")
```

Also test idempotency, stable `target_id`, invalid profile/language/region, missing ownership consent, and preservation of the original v1 file until v2 validation succeeds.

**Step 2: Run RED**

```powershell
python -m unittest tests.test_seo_employee_targets -v
```

**Step 3: Implement pure migration and path construction**

Use a stable target ID derived from normalized site identity plus a collision-safe suffix only when required. Store per-target files under:

```text
state/targets/<target_id>/state.json
state/targets/<target_id>/baseline.json
state/targets/<target_id>/daily_runs.json
state/targets/<target_id>/locks/
reports/<target_id>/latest.json
history/<target_id>/
evidence/<target_id>/<run_id>/
```

`migrate_config_file()` writes `.v1.backup`, validates the new object, then atomically replaces `config.json`.

**Step 4: Run GREEN**

Expected: all target tests pass without touching real product state.

### Task 5: Move source parsing behind explicit adapters

**Files:**
- Create: `experts/seo_employee_sources.py`
- Create: `tests/test_seo_employee_sources.py`
- Modify later by Sol: `experts/seo_employee_service.py:336-595`

**Step 1: Write failing adapter tests**

Fixtures must cover CrawlSEO and SEOmator success, partial output, WAF, CAPTCHA, 403, 429, 503, timeout, invalid payload, unknown rule, and incomplete coverage.

```python
def test_unknown_rule_is_counted_not_emitted_as_task() -> None:
    result = SEOmatorAdapter(CATALOG).parse(UNKNOWN_RULE_FIXTURE, PLAN)
    assert result.occurrences == ()
    assert result.coverage.unmapped_rules == ("future-rule",)
```

**Step 2: Implement minimal adapter protocol**

```python
class SourceAdapter(Protocol):
    name: str
    def validate(self, payload: Mapping[str, object], plan: AuditPlan) -> None: ...
    def parse(self, payload: Mapping[str, object], plan: AuditPlan) -> SourceResult: ...
```

Only two implementations are created now. Adapter errors use fixed machine codes; they never include upstream exception text in user reports.

**Step 3: Implement exact readiness helpers**

Add `required_sources_satisfied(plan, results)` and `missing_sources(plan, results)`. Do not compare source counts or literal names in the service.

**Step 4: Run tests**

```powershell
python -m unittest tests.test_seo_employee_sources -v
```

Expected: pass.

### Task 6: Integrate v2 modules into the deterministic service

**Files:**
- Modify: `experts/seo_employee_service.py`
- Modify: `tests/test_seo_employee_service.py`
- Test additionally: Tasks 2–5 test files

**Step 1: Add failing integration cases**

Add tests for:

- v1 fixture migration preserving `meta-description-missing`;
- report/state schema v2;
- 25-page `ready` and incomplete 25-page `partial`;
- rule-specific `verified`/`supported`;
- coverage and unmapped rules;
- per-target baseline isolation;
- model failure retaining deterministic tasks;
- full report still capped at 10 tasks.

**Step 2: Run focused RED tests**

```powershell
python -m unittest tests.test_seo_employee_service -v
```

**Step 3: Replace embedded registries and fixed paths**

Remove `RULE_REGISTRY`, `SOURCE_RULE_REGISTRY`, `SOURCE_EVIDENCE_FACTS`, fixed `BASELINE_PATH`/`DAILY_INDEX_PATH` assumptions, and literal readiness `len(...) == 2`. Import the new modules.

`run_seo_employee()` must resolve `target_id`, build the plan, acquire the target lock, collect sources, normalize evidence, persist coverage, enrich at most 10 tasks, and update the compatible baseline.

**Step 4: Emit explicit v2 envelopes**

```python
report = {
    "schema": "extella.seo_employee_report.v2",
    "target": target.public_dict(),
    "plan": plan.public_dict(),
    "coverage": coverage.as_dict(),
    "run": run_metadata,
    "sources": source_statuses,
    "tasks": tasks,
    "comparison": comparison,
}
```

**Step 5: Run all Python domain tests**

Expected: new tests pass and all prior security/state tests remain green.

### Task 7: Carry a validated AuditPlan through both workers

**Files:**
- Modify: `runtime/source_proxy.py`
- Modify: `runtime/worker_server.mjs`
- Modify: `runtime/seomator/entrypoint.mjs`
- Modify: `runtime/crawlseo/entrypoint.mjs`
- Modify: `tools/crawlseo_once.mjs`
- Modify: `runtime/run_crawlseo`
- Modify: `runtime/run_seomator`
- Modify: `runtime/container/run_crawlseo`
- Modify: `runtime/container/run_seomator`
- Modify: `tests/test_container_runtime.py`
- Create: `tests/worker_plan.test.mjs`

**Step 1: Write failing worker protocol tests**

Accept only:

```json
{
  "site_url": "https://example.com/",
  "plan": {
    "max_pages": 25,
    "categories": ["core", "links"],
    "performance_sample_pages": 5,
    "timeout_ms": 720000
  }
}
```

Reject unknown keys, `max_pages > 100`, unknown category, timeout above 720000, credentials in URL, and bodies above the fixed cap.

**Step 2: Run RED**

```powershell
node --test tests/worker_plan.test.mjs
python -m unittest tests.test_container_runtime -v
```

**Step 3: Pass plan via a temporary JSON file**

The HTTP worker validates the body, writes a plan file inside its private temp directory, and spawns:

```javascript
spawn("node", ["/app/extella_entrypoint.mjs", siteUrl, outputPath, planPath], ...)
```

Keep the existing detached process group and cancellation behavior. Hard worker timeout becomes 900000 ms; the plan may only lower it.

**Step 4: Remove one-page restrictions safely**

- CrawlSEO: call `run_crawl` with `maxPages: plan.max_pages`, require `pagesFound <= maxPages`, and emit requested/actual coverage.
- SEOmator: use `--crawl --max-pages`, all 20 categories, and run CWV/JS only for the deterministic sample. Preserve JSON output and atomic rename.
- Keep the pinned source commits and SSRF preload.

**Step 5: Run GREEN and cancellation regressions**

Expected: protocol tests, source proxy tests, disconnect tests, and prior safe-fetch tests pass.

### Task 8: Add a persistent single-worker FIFO queue

**Files:**
- Create: `experts/seo_employee_queue.py`
- Create: `tests/test_seo_employee_queue.py`
- Modify: `experts/seo_employee_schedule.py`
- Modify later by Sol: `experts/seo_employee_service.py`

**Step 1: Write failing queue tests**

Test FIFO order across targets, same-target deduplication, atomic persistence, restart recovery, cancellation, one active item, and no loss when the process dies between dequeue and completion.

**Step 2: Implement a minimal file-backed queue**

```python
@dataclass(frozen=True)
class QueueItem:
    queue_id: str
    target_id: str
    trigger: str
    requested_at: str
    status: str
```

Use one atomic `state/queue.json`, one process-local condition, and one consumer thread. No broker or database is added. Mark interrupted `running` items back to `queued` once on startup.

**Step 3: Change schedule behavior**

The scheduler iterates due targets and enqueues them; it does not call source workers directly. Manual run returns `202 queued` or the existing active/queued run ID.

**Step 4: Run queue and service tests**

Expected: no `worker_busy` reaches the user path.

### Task 9: Upgrade state reader, Expert API, and product HTTP API

**Files:**
- Modify: `experts/seo_employee_state.py`
- Modify: `experts/seo_employee_run.py`
- Modify: `experts/seo_employee_schedule.py`
- Modify: `runtime/product/server.py`
- Modify: `tests/test_agent_zero_transport.py`
- Modify: `tests/test_container_runtime.py`

**Step 1: Add failing API tests**

Cover:

- `GET /api/state?target_id=...`;
- `GET /api/targets`;
- `POST /api/configure` with profile/language/region/budget/ownership;
- `POST /api/run` with target and mode;
- queue response `202`;
- rejection of unknown request keys and budget 101;
- preservation of singular Extella `target` transport.

**Step 2: Implement additive request fields**

Keep Expert names and `method=run|state|configure`. Old configure input migrates to one target. New API returns state/report v2 only after migration succeeds.

**Step 3: Keep auth and error semantics**

Token remains mandatory. User-facing errors remain fixed messages; raw source/model errors stay out of responses.

**Step 4: Run focused tests**

Expected: API and transport suites pass.

### Task 10: Expand Agent Zero output without giving it tools

**Files:**
- Modify: `experts/seo_employee_run.py`
- Modify: `experts/seo_employee_service.py`
- Create: `tests/test_seo_employee_agent_output.py`
- Modify: `deploy/agent-zero-profile/usr/agents/seo_employee_no_tools/plugins/_skills/config.json`
- Keep unchanged unless format requires: `deploy/agent-zero-profile/usr/agents/seo_employee_no_tools/plugins/_tool_access/config.json`

**Step 1: Write schema tests**

Input may contain only profile enum, language, region enum, mode, coverage summary, and up to 10 sanitized findings. Output may contain only per-task explanation fields, optional evidence-backed `content_brief`, and `action_proposals`.

**Step 2: Preserve the no-tools assertion**

Tests must still prove zero available tool contexts. Do not add shell, browser, MCP, filesystem, network, or skill execution to Agent Zero.

**Step 3: Implement deterministic degradation**

If Agent Zero fails or returns invalid JSON, keep factual tasks and set `model_enrichment.status = "unavailable"`; do not return `failed` solely because of the model.

**Step 4: Validate action proposals**

```python
proposal = {
    "proposal_id": stable_id,
    "target_id": target_id,
    "task_id": task_id,
    "operation": "manual_change",
    "preview": sanitized_preview,
    "rollback": rollback_text,
    "expires_at": expires_at,
    "status": "proposed",
}
```

There is no execution adapter in this task.

### Task 11: Build the multi-target Extella panel

**Files:**
- Modify: `app/index.html`
- Modify: `app/app.js`
- Modify: `app/styles.css`
- Modify: `tests/ui/ui_contract.test.mjs`

**Step 1: Add failing UI contract tests**

Cover `UC-SEO-022..026`: target list, profile form, language/region/budget, ownership checkbox, coverage, queue state, optional sources, action proposal, confirmation separation, keyboard access, `aria-live`, RU/EN, host theme, and one bronze primary action.

**Step 2: Add the minimal form**

Fields: name, URL, profile, language, region, business goal, daily time, timezone, max pages (1–100), ownership consent. Default max pages is 25.

**Step 3: Add target selection and coverage**

Do not build a dashboard framework. Reuse existing task cards and comparison groups. Add a simple target list and one selected-target detail view.

**Step 4: Add action proposal UI**

The UI may copy a manual instruction immediately. Any future external execution button remains absent until a contracted adapter exists.

**Step 5: Run Node tests**

Expected: all old bridge/design/accessibility tests and new v2 UI tests pass.

### Task 12: Add the optional CrawlSEO search-performance lane

**Files:**
- Create: `runtime/crawlseo/metrics_once.mjs`
- Modify: `runtime/crawlseo/entrypoint.mjs`
- Modify: `runtime/crawlseo/Dockerfile`
- Modify: `runtime/worker_server.mjs`
- Modify: `experts/seo_employee_sources.py`
- Modify: `deploy/compose.yaml`
- Create: `tests/test_search_performance.py`
- Modify: `tests/test_container_runtime.py`

**Step 1: Write tests with no credentials**

`search_performance` must return `not_configured`, not `failed`, when no CrawlSEO site binding/GSC property exists. Technical audit remains `ready` when its required sources succeed.

**Step 2: Add an allowlisted worker operation**

The worker accepts `operation: audit|search_performance`. `metrics_once.mjs` calls only CrawlSEO's existing MCP read tools: `get_keywords`, `get_pages`, `get_traffic`, `get_vitals`, and `get_opportunities`. It returns structured JSON, never formatted MCP prose.

**Step 3: Bind an existing CrawlSEO site explicitly**

Target config gets optional `crawlseo_site_id`. Never guess a site by domain when multiple records exist. A missing or invalid binding is `not_configured`.

**Step 4: Make OAuth setup optional and isolated**

If the closed pilot needs live OAuth, add a Compose profile using the same pinned CrawlSEO image and its existing NextAuth/GSC UI, bound to loopback. Secrets come from Docker secret files. Do not expose the app publicly and do not copy OAuth values into product reports.

**Step 5: Gate live claims**

Mocked contract tests are mandatory. A real GSC property E2E is recorded separately; if unavailable, release notes keep GSC under `not_verified` while the free core can still ship.

### Task 13: Version packaging and operations for 2.0

**Files:**
- Modify: `MANIFEST.yaml`
- Modify: `tools/build_release.py`
- Modify: `tools/build_runtime.py`
- Modify: `runtime/product/server.py`
- Modify: `app/app.js`
- Modify: `deploy/README.md`
- Modify: `deploy/OPERATIONS.md`
- Modify: `deploy/backup.py`
- Modify: `tests/test_packaging.py`
- Modify: `tests/test_backup_restore.py`
- Generate only at the end: `release-manifest.json`, `selfcheck.json`, `dist/*2.0.0*`

**Step 1: Add failing packaging tests**

Require v2 schemas, new modules/catalog, config migration backup, per-target state, queue, and exact source commits. Reject secrets, generated bindings, local env, and v1-only manifests.

**Step 2: Change image and archive tags to 2.0.0**

Do not use `latest`. Keep upstream commits pinned and run `git apply --check` for any retained CrawlSEO patch. Replace the one-page patch with the smallest security/limit patch required by the v2 worker protocol.

**Step 3: Extend backup scope**

Backup queue, targets, histories, reports, evidence, CrawlSEO DB, and config-v1 backup. Agent Zero state, provider credentials, secrets, and bindings stay outside the snapshot and are recovered by re-provisioning; restore-check remains temporary and non-destructive.

**Step 4: Build twice**

Run `python tools/build_release.py` twice from clean payload state. Expected: identical archive SHA-256 values.

### Task 14: Run the complete local release gate

**Files:**
- Update: `docs/verification/v2-progress.md`
- Generate: `selfcheck.json`
- Generate: `dist/build.json`

**Step 1: Run all tests**

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
node --test tests/safe_fetch.test.mjs tests/worker_plan.test.mjs tests/ui/ui_contract.test.mjs
```

Expected: zero failures and no skipped security/contract tests.

**Step 2: Run syntax and source checks**

```powershell
python -m compileall experts runtime/product deploy tools
node --check runtime/worker_server.mjs
node --check runtime/seomator/entrypoint.mjs
node --check runtime/crawlseo/entrypoint.mjs
```

**Step 3: Run Extella gates**

Run the canonical automation passport, ready-for-publish, UI/API, scopes, listing, state-contract, and self-check validators from `work/extella-agent-standards/tools/` against the product root. Record each exact command and result.

**Step 4: Scan release archives**

List archive members, reject secret paths/values, verify manifest hashes, and ensure investor/review/internal planning documents are excluded from runtime payload.

### Task 15: Perform clean CT160 deployment and E2E

**Files:**
- Update after run: `dist/ct160-verification-v2.json`
- Update: `docs/verification/v2-progress.md`

**Step 1: Preserve recovery**

Create and verify an external backup of the current `/opt/extella-seo-release` deployment. Do not overwrite its data volumes until restore-check passes.

**Step 2: Deploy to a separate path and Compose project**

Use `/opt/extella-seo-release-v2` and a distinct project name. Validate `docker compose config -q` before starting.

**Step 3: Run profile E2E fixtures**

Exercise all five profiles with public/synthetic controlled sites, page limits 1/25/100, multi-target isolation, queue ordering, daily deduplication, partial/failure, WAF codes, restart, SIGKILL, stale lock, disconnect cancellation, SSRF, backup, and restore-check.

**Step 4: Verify Agent Zero and model route**

Confirm no-tools context, structured output, invalid-output degradation, and the exact configured provider/model/auth route. Do not claim any untested route.

**Step 5: Record evidence**

`dist/ct160-verification-v2.json` contains image IDs, archive hashes, run IDs, test counts, explicit passes, and `not_verified`; no secrets or OAuth identities.

### Task 16: Independent review and release decision

**Files:**
- Create: `reviews/universal-seo-employee-v2-final-review.md`
- Update: `README.md`
- Update: investor claim/evidence documents only after verified facts exist

**Step 1: Sol inspects the complete changed-file set**

Check unexpected files, public API changes, generated artifacts, source pins, secret exposure, contract traceability, and every original requirement.

**Step 2: Sol reruns the full local suite**

Fresh results are mandatory after integration; worker claims are not accepted as final evidence.

**Step 3: Dispatch a fresh read-only strict reviewer**

Reviewer receives the Blueprint, plan, complete diff/current tree, local output, and CT160 evidence. It must report P0/P1 only and must not edit files.

**Step 4: Correct findings and repeat evidence**

Each correction reruns the narrow failing check, then the full affected gate. After three failures from the same assumption, stop and reconsider architecture.

**Step 5: Declare one honest release state**

- `SHIP_CLOSED_PILOT` only if all v2 criteria pass;
- `FIX_BEFORE_PILOT` when a bounded P1 remains;
- `BLOCK` for security, data-loss, auth, migration, or unverified core-path failure.

Public Extella publication, investor numbers, and production rollout remain separate user-authorized actions.

## Final acceptance checklist

- Feature, Service, UI Contracts and traceability match Blueprint v2.
- Config v1 migrates atomically and preserves the old verified finding.
- Five profiles create deterministic plans.
- Free audit works with no OAuth/API key.
- Up to 100 pages obey limits, deadlines, memory and cancellation.
- Multiple targets never mix queue, state, history or baseline.
- Unknown rules are visible in coverage, not fabricated as tasks.
- GSC/DataForSEO are optional and honestly `not_configured`/`not_verified`.
- Agent Zero sees sanitized facts only and has no tools.
- Model failure preserves deterministic findings.
- Action proposals cannot execute without a separate contracted adapter and confirmation.
- All local, Extella, packaging, backup, clean-host and CT160 checks pass.
- Fresh independent reviewer reports no P0/P1.
