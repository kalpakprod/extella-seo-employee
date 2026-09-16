# P2 verification lanes

P2 adds three best-effort source lanes to the existing required CrawlSEO and
SEOmator audit: Nu HTML validation, direct security headers plus a single
cached SSL Labs read, and Common Crawl historical evidence. A source may be
unavailable without turning a required-source-complete report into failure.

## Included in P2

| Check | Command | Expected evidence |
|---|---|---|
| Python unit suite | `python3 -m unittest discover -s tests -p 'test_*.py'` | All Python contracts remain green. |
| Subprocess successful collection integration | `python3 -m unittest discover -s tests -p 'test_p2_probe_integration.py' -v` | `collect_sources` runs through real source-proxy subprocesses, supervisor/child `/run` handlers, local fixture responses, adapters, and report building; the success baseline is `state=ready` with exact `missing_data={PSI,GoogleSearchConsole,DataForSEO}`. |
| Service timeout/report regression (mocked runner) | `python3 -m unittest discover -s tests -p 'test_seo_employee_service_v2.py' -v` | `test_optional_workers_have_bounded_plans_and_fail_without_downgrading_report` uses a mocked runner to make optional workers unavailable; required results remain ready and missing optional/not-configured sources are surfaced. |
| Node suite | `node --test tests/safe_fetch.test.mjs tests/worker_plan.test.mjs tests/ui/ui_contract.test.mjs` | Existing worker, safe-fetch, and UI contracts remain green. |
| Compose syntax | `docker compose -f deploy/compose.yaml config -q` | `nu`, `tls`, and `cc` are isolated internal services with no host ports or secrets. |
| Pinned socket/TLS and IPC regressions | `python3 -m unittest discover -s tests -p 'test_probe_worker.py'` | Real loopback HTTP/TLS plus a real child IPC deadline test exercise numeric pinning, Host/SNI identity, bounded selectors, result-acceptance/idle timeouts, fallback, malformed responses, IPv6, and NAT64. |
| PSI pinning and supervisor regressions (R2) | `python3 -m unittest discover -s tests -p 'test_psi_worker.py'` | Real loopback fetches plus forked children exercise numeric DNS pinning, Host/SNI identity, NAT64, bounded bodies, absolute deadlines, bounded child slots, deadline kill/reap, child-envelope validation, and the `/health` degraded state. |
| Data-quality contracts (R3) | `python3 -m unittest discover -s tests -p 'test_seo_employee_service*.py'` and `python3 -m unittest discover -s tests -p 'test_seo_employee_sources.py'` | Typed per-measurement `subchecks` drive `missing_data`, catalog severity is reserved for confirmed failures (with `severity_basis`), identical `rule_key` occurrences collapse into one bounded recommendation, enriched/total/attempts are honest, and the approved corpus has no unmapped rules. |
| External process-boundary acceptance | `env EXTELLA_ACCEPTANCE_RUNTIME=$PWD/runtime/probe/entrypoint.py EXTELLA_P2_RAW_DIR=$PWD/.orchestra/tasks/3/raw node --test tests/probe_supervisor_acceptance.test.mjs` | Fresh supervisor parent/child PIDs, slow-drip site/provider, blocking DNS child, caller death, overload, `/proc` cleanup, dripper EOF/reset, and health. This harness does not call `collect_sources` or build a report. |
| Isolated live workers | `python3 .orchestra/tasks/1/isolated_live_workers.py .orchestra/tasks/1/isolated-live-workers.json` | Three fresh processes execute the current `runtime/probe/entrypoint.py` against `https://books.toscrape.com/`; no existing Compose worker is reused. |
| Isolated Docker workers | `docker build --no-cache -f runtime/probe/Dockerfile -t extella-p2-luna-probe:20260915 .` followed by the bounded `/run` calls recorded in `.orchestra/tasks/1/isolated-docker-workers.raw.txt` | A fresh image copied the current worker and three temporary containers returned bounded Nu, SecurityProbe, and CommonCrawl payloads. The image was removed with the temporary containers; no Compose project was changed. |
| Gate definition | `.github/workflows/release-gate.yml` | CI runs the Python and Node commands above, deterministic build, self-check, manifest, Compose config, and pinned upstream gates. |

## What the integration test proves

`tests/test_p2_probe_integration.py` starts the production `runtime/probe/entrypoint.py`
supervisor once for each of `nu`, `tls`, and `cc`. `collect_sources` invokes real executable
wrappers, which invoke `runtime/source_proxy.py`, which POSTs bounded plans over loopback HTTP;
each supervisor launches a real probe child. A child-only `sitecustomize` fixture redirects
provider URLs to a local server without adding an acceptance hook to production runtime. The
returned payloads are parsed by the production adapters and become a report.

The assertion checks the user-facing distinction: required sources plus all three probes yield
`state=ready`; unavailable PSI and not-configured Google Search Console/DataForSEO are listed in
`missing_data`; Nu, SecurityProbe, and Common Crawl do not become missing data; Common Crawl
keeps `crawled_pages=0` and a historical-evidence note. The mocked provider responses also
produce the Nu error, HSTS/CSP, and TLS-grade source occurrences. These probe-only observations
are retained as source evidence; the normalizer does not present an uncorroborated observation
as an actionable report task.

The worker keeps the complete validated public DNS answer set (up to four addresses) and tries
those numeric addresses in order under one monotonic result-acceptance budget with per-stage
idle timeouts. It retains the origin hostname
for HTTPS SNI and certificate verification, and emits bracketed IPv6 Host authorities. HTTP
origins and HTTPS origins on a non-default port return `ssl_labs.status=unavailable` with no
grade, because the cached provider query cannot establish that it assessed the audited endpoint.
Malformed remote HTTP responses are converted to a bounded optional-source `unavailable`
payload. The NAT64 well-known prefix is accepted only when its embedded IPv4 address is global;
this closes the conditional embedded-private route without claiming that the deployment uses
NAT64.

The deadline boundary is a supervisor process boundary: the parent computes D after the bounded
request body, launches a fresh same-entrypoint child, and kills/reaps only that child process
group at D. Source_proxy retains one second of the B budget for IPC/cleanup/response; its outer
timeout is a safety margin and does not serve as the worker cancellation mechanism. The owner
architecture and rollback/limits are recorded in `.orchestra/tasks/1/architecture.md` and
`TODO.md`.

## PSI lane network hardening (R2)

`runtime/psi/entrypoint.py` is the only egress point of the PSI lane, so it carries the same
process and address discipline as the probes:

- DNS is resolved once per hop, every answer must be global (NAT64 `64:ff9b::/96` is accepted only
  when its embedded IPv4 address is global), and the validated numeric address is pinned into the
  socket connect. HTTPS keeps the origin hostname for SNI and certificate verification, and the
  Host header preserves the URI authority including IPv6 brackets.
- Google API calls are pinned the same way and are restricted to the two fixed Google hosts.
- Each `/run` request runs in a fresh child process from the same entrypoint under a bounded
  supervisor (`MAX_CHILDREN` slots, `CHILD_IPC_BYTES` cap, absolute deadline, `SIGKILL` of the
  child process group and bounded reap). `source_proxy` keeps its outer timeout only as a safety
  margin. `/health` reports `degraded` while a child cleanup is pending.
- `PSI_ALLOW_PRIVATE` exists only for tests and is not set in `deploy/compose.yaml`; the
  production path rejects private answers. In-process `handle_run` remains the direct execution
  path used by focused tests and the child.

Verify with `python3 -m unittest discover -s tests -p 'test_psi_worker.py'`.

## Data-quality contracts (R3)

R3 makes the report describe what was actually measured instead of grepping notes:

- Every optional source publishes typed `coverage.subchecks` (`ok`, `partial`, `not_configured`,
  `unavailable`, `unsupported`) with a machine `reason` and, where it applies, a `scope` URL.
  `missing_data` is derived from those states (unavailable/partial subchecks are listed as
  `Source.subcheck`), so "main audit ready" stays distinct from "some measurements missing".
  Human-readable `notes` remain, but they no longer drive report logic.
- Severity follows `docs/severity-methodology.md`: the catalog level means a confirmed failure,
  and an occurrence that was never confirmed downgrades one step and records `severity_basis`.
  Errors are never hidden just to improve a severity distribution.
- Occurrences with the same `rule_key` collapse into one card with a bounded `affected_pages`
  list and a true `affected_pages_count`; one mass problem no longer fills the recommendation
  list. Per-URL comparison cards stay per-URL.
- Enrichment reports `status`/`limitation`/`enriched`/`total`/`unavailable`/`attempts`/`reasons`
  and stops retrying once the run deadline is exhausted.
- Unknown rules remain visible in `coverage.unmapped_rules` and never become a user task; the
  approved corpus (CrawlSEO documented issue types, SEOmator documented rules, PSI lab/field and
  site-file rules, Nu, TLS, Common Crawl) is asserted to have no unexplained unmapped rules.

## Verification artifacts

The final run stores raw command output and the full handoff report under
`.orchestra/tasks/1/`:

| Artifact | Contents |
|---|---|
| `python-suite.txt` | Full Python unittest discovery output (`284` tests). |
| `node-suite.txt` | Node worker, safe-fetch, and UI contract output (`64` tests). |
| Post-R1/R3 rerun (this branch) | `python3 -m unittest discover -s tests -p 'test_*.py'` → `320` tests OK; `node --test tests/safe_fetch.test.mjs tests/worker_plan.test.mjs tests/ui/ui_contract.test.mjs` → `64` pass. |
| `compose-config.txt` | Compose syntax check output. |
| `test_probe_worker.txt` | Focused socket/TLS and containment output. |
| `collect-sources-report.raw.txt` | End-to-end `collect_sources` → source proxies → probe handlers → adapters → report test. |
| `isolated-live-workers.json` and `isolated-live-workers.raw.txt` | Fresh-process public-site probe payloads and terminal output. |
| `probe-image-build.txt`, `probe-image-id.txt`, `probe-image-remove.txt`, and `isolated-docker-workers.raw.txt` | Fresh worker image build/identity/removal and temporary-container payloads. |
| `sol-f219de2-deadline.raw.txt` and `TODO.md` | Prior Sol threat reproduction plus selected process-boundary architecture/rollback limits. |
| `.orchestra/tasks/3/raw/` | External acceptance raw transcript/NDJSON; latest rerun is `acceptance-suite-final.raw.txt`. |
| `report.md` | Full verification report, exact HEAD, commands, results, limits, and restrictions. |

## Deliberately deferred

| Item | Why it is deferred / limitation |
|---|---|
| Google Search Console OAuth | No GSC credentials or approved OAuth integration are configured. Search-performance mode stays explicitly `not_configured`. |
| DataForSEO paid API | No paid API account or authorization is configured. It remains an optional not-configured source. |
| SecurityHeaders.com API | This lane does not use it. SecurityProbe evaluates direct response headers and requests SSL Labs cached assessment once, without polling. |
| Hard wall-clock cancellation | Implemented by fresh child process groups, uncapped supervisor IPC D, SIGKILL and bounded reap. OS scheduling/kernel behavior is not real-time; slow ingress is bounded by four handler slots and two-second idle timeout. |
| Full release gate for `2.1.0` | P2 verification does not claim a release: current product `VERSION` is `2.0.3`, and committed `dist/` / release manifest were intentionally not regenerated. Run the workflow commands only after an approved release-version and artifact update. |
| Live provider determinism | External Nu, SSL Labs, and Common Crawl availability and contents vary. CI mocks only their outbound replies; the live probe command records current results and may report a lane unavailable. |
| R3 live E2E re-baseline ("стало/было") | The checked-in `evidence/e2e-2026-09-14-books-25pages-phase2.json` predates R1/R3 semantics (`missing_data: []`, uncorroborated `critical` tasks). A fresh live run needs network and workers, so the R3 re-baseline is recorded as pending; only the code and unit-test halves of item 6 are closed here. |

## Running without changing the Compose project

Do not run `up`, `restart`, or `down` against `extella-p2-check` for this check. The isolated
live command starts temporary worker processes from the checked-out source. For a container
packaging check, build a temporary image and use temporary names/ports; do not attach to an
existing Compose worker. Save the terminal transcript separately when needed:

```sh
python3 .orchestra/tasks/1/isolated_live_workers.py \
  .orchestra/tasks/1/isolated-live-workers.json \
  > .orchestra/tasks/1/isolated-live-workers.raw.txt 2>&1
```
