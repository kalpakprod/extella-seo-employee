# P2 verification lanes

P2 adds three best-effort source lanes to the existing required CrawlSEO and
SEOmator audit: Nu HTML validation, direct security headers plus a single
cached SSL Labs read, and Common Crawl historical evidence. A source may be
unavailable without turning a required-source-complete report into failure.

## Included in P2

| Check | Command | Expected evidence |
|---|---|---|
| Unit and integration suite | `python3 -m unittest discover -s tests -p 'test_*.py'` | `test_p2_probe_integration` runs the complete `collect_sources` path through subprocess source proxies and real loopback `/run` HTTP handlers. Its Nu, SSL Labs, and Common Crawl provider responses are deterministic mocks at the outbound boundary. |
| Node suite | `node --test tests/safe_fetch.test.mjs tests/worker_plan.test.mjs tests/ui/ui_contract.test.mjs` | Existing worker, safe-fetch, and UI contracts remain green. |
| Compose syntax | `docker compose -f deploy/compose.yaml config -q` | `nu`, `tls`, and `cc` are isolated internal services with no host ports or secrets. |
| Pinned socket/TLS regressions | `python3 -m unittest discover -s tests -p 'test_probe_worker.py'` | Real loopback HTTP and TLS sockets exercise numeric-address pinning, Host/SNI identity, absolute stage deadlines, bounded address fallback, malformed-response containment, IPv6 authority formatting, and NAT64 embedded-address rejection. |
| Isolated live workers | `python3 .orchestra/tasks/1/isolated_live_workers.py .orchestra/tasks/1/isolated-live-workers.json` | Three fresh processes execute the current `runtime/probe/entrypoint.py` against `https://books.toscrape.com/`; no existing Compose worker is reused. |
| Isolated Docker workers | `docker build --no-cache -f runtime/probe/Dockerfile -t extella-p2-luna-probe:20260915 .` followed by the bounded `/run` calls recorded in `.orchestra/tasks/1/isolated-docker-workers.raw.txt` | A fresh image copied the current worker and three temporary containers returned bounded Nu, SecurityProbe, and CommonCrawl payloads. The image was removed with the temporary containers; no Compose project was changed. |
| Gate definition | `.github/workflows/release-gate.yml` | CI runs the Python and Node commands above, deterministic build, self-check, manifest, Compose config, and pinned upstream gates. |

## What the integration test proves

`tests/test_p2_probe_integration.py` starts the production `runtime/probe/entrypoint.py` HTTP handler once for each of `nu`, `tls`, and `cc`. `collect_sources` invokes real executable wrappers, which invoke `runtime/source_proxy.py`, which POSTs the bounded `{"timeout_ms": 20000}` plans over loopback HTTP. The returned payloads are parsed by the production adapters and become a report.

The assertion checks the user-facing distinction: required sources plus all three probes yield `state=ready`; unavailable PSI is listed in `missing_data`; Nu, SecurityProbe, and Common Crawl do not become missing data; Common Crawl keeps `crawled_pages=0` and a historical-evidence note. The mocked provider responses also produce the Nu error, HSTS/CSP, and TLS-grade source occurrences. These probe-only observations are retained as source evidence; the normalizer does not present an uncorroborated observation as an actionable report task.

The worker keeps the complete validated public DNS answer set (up to four addresses) and tries
those numeric addresses in order under one monotonic deadline. It retains the origin hostname
for HTTPS SNI and certificate verification, and emits bracketed IPv6 Host authorities. HTTP
origins and HTTPS origins on a non-default port return `ssl_labs.status=unavailable` with no
grade, because the cached provider query cannot establish that it assessed the audited endpoint.
Malformed remote HTTP responses are converted to a bounded optional-source `unavailable`
payload. The NAT64 well-known prefix is accepted only when its embedded IPv4 address is global;
this closes the conditional embedded-private route without claiming that the deployment uses
NAT64.

## Verification artifacts

The final run stores raw command output and the full handoff report under
`.orchestra/tasks/1/`:

| Artifact | Contents |
|---|---|
| `python-suite.txt` | Full Python unittest discovery output (`279` tests). |
| `node-suite.txt` | Node worker, safe-fetch, and UI contract output (`64` tests). |
| `compose-config.txt` | Compose syntax check output. |
| `test_probe_worker.txt` | Focused socket/TLS and containment output. |
| `collect-sources-report.raw.txt` | End-to-end `collect_sources` → source proxies → probe handlers → adapters → report test. |
| `isolated-live-workers.json` and `isolated-live-workers.raw.txt` | Fresh-process public-site probe payloads and terminal output. |
| `probe-image-build.txt`, `probe-image-id.txt`, `probe-image-remove.txt`, and `isolated-docker-workers.raw.txt` | Fresh worker image build/identity/removal and temporary-container payloads. |
| `report.md` | Full verification report, exact HEAD, commands, results, limits, and restrictions. |

## Deliberately deferred

| Item | Why it is deferred / limitation |
|---|---|
| Google Search Console OAuth | No GSC credentials or approved OAuth integration are configured. Search-performance mode stays explicitly `not_configured`. |
| DataForSEO paid API | No paid API account or authorization is configured. It remains an optional not-configured source. |
| SecurityHeaders.com API | This lane does not use it. SecurityProbe evaluates direct response headers and requests SSL Labs cached assessment once, without polling. |
| Full release gate for `2.1.0` | P2 verification does not claim a release: current product `VERSION` is `2.0.3`, and committed `dist/` / release manifest were intentionally not regenerated. Run the workflow commands only after an approved release-version and artifact update. |
| Live provider determinism | External Nu, SSL Labs, and Common Crawl availability and contents vary. CI mocks only their outbound replies; the live probe command records current results and may report a lane unavailable. |

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
