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
| Live optional workers | `python3 tools/probe_p2.py --project extella-p2-check --url https://books.toscrape.com/ --output /tmp/extella-p2-integration-probe-live.log` | Direct Compose-exec HTTP calls save parsed payload evidence; this is read-only against the running project. |
| Gate definition | `.github/workflows/release-gate.yml` | CI runs the Python and Node commands above, deterministic build, self-check, manifest, Compose config, and pinned upstream gates. |

## What the integration test proves

`tests/test_p2_probe_integration.py` starts the production `runtime/probe/entrypoint.py` HTTP handler once for each of `nu`, `tls`, and `cc`. `collect_sources` invokes real executable wrappers, which invoke `runtime/source_proxy.py`, which POSTs the bounded `{"timeout_ms": 20000}` plans over loopback HTTP. The returned payloads are parsed by the production adapters and become a report.

The assertion checks the user-facing distinction: required sources plus all three probes yield `state=ready`; unavailable PSI is listed in `missing_data`; Nu, SecurityProbe, and Common Crawl do not become missing data; Common Crawl keeps `crawled_pages=0` and a historical-evidence note. The mocked provider responses also produce the Nu error, HSTS/CSP, and TLS-grade source occurrences. These probe-only observations are retained as source evidence; the normalizer does not present an uncorroborated observation as an actionable report task.

## Deliberately deferred

| Item | Why it is deferred / limitation |
|---|---|
| Google Search Console OAuth | No GSC credentials or approved OAuth integration are configured. Search-performance mode stays explicitly `not_configured`. |
| DataForSEO paid API | No paid API account or authorization is configured. It remains an optional not-configured source. |
| SecurityHeaders.com API | This lane does not use it. SecurityProbe evaluates direct response headers and requests SSL Labs cached assessment once, without polling. |
| Full release gate for `2.1.0` | P2 verification does not claim a release: current product `VERSION` is `2.0.3`, and committed `dist/` / release manifest were intentionally not regenerated. Run the workflow commands only after an approved release-version and artifact update. |
| Live provider determinism | External Nu, SSL Labs, and Common Crawl availability and contents vary. CI mocks only their outbound replies; the live probe command records current results and may report a lane unavailable. |

## Running without changing the Compose project

Do not run `up`, `restart`, or `down` against `extella-p2-check` for this check. The live command uses `docker compose exec -T` only. Save the terminal transcript separately when needed:

```sh
python3 tools/probe_p2.py --project extella-p2-check --url https://books.toscrape.com/ \
  --output /tmp/extella-p2-integration-p2-probes.json \
  > /tmp/extella-p2-integration-probe-live.log 2>&1
```
