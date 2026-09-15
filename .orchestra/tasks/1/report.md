# Extella P2 verification report

Date: 2026-09-15

Implementation commits: `3a40c5fe22f0b72d9632196aff6581e0434fb8aa` (supervisor),
`6d4c7cbec4a6b849ba027021ed27e6920dd52317` (slot ownership),
`6d5a861b94e134d9699c6bd5b9d3c4c92b3c942b` (uncapped IPC deadline), and
`227d4fcd4deb155fdc88253d94e497c0368e622c` (public routing).

Documentation/evidence commit: the final HEAD reported with this task.

Integrated external acceptance harness commit: `e734abd` (`#3: add external probe supervisor acceptance harness`).

## Result

The endpoint, malformed-response, IPv6, NAT64, validated-address, and hard-cancellation findings
are implemented in `runtime/probe/entrypoint.py` under the owner-approved architecture in
`architecture.md`:

1. SSL Labs is queried only for the default HTTPS origin. HTTP and HTTPS origins on a
   non-default port return `ssl_labs.status=unavailable` and `grade=null`, without asking
   the provider for a potentially foreign endpoint grade.
2. `http.client.HTTPException`, including `BadStatusLine`, is contained by every optional
   lane and by `handle_run`; the HTTP worker contract returns bounded JSON unavailable data.
3. IPv6 literal authorities use `[address]` and `[address]:port` in the HTTP Host header.
4. The long-lived worker is a supervisor. Each accepted probe launches a fresh same-entrypoint
   child in a new process group; selectors carry one bounded UTF-8 JSON request/response over
   closed stdin/stdout pipes. The supervisor kills only that child process group at the absolute
   child deadline and reaps it within the bounded cleanup window; slow-drip site/provider reads
   and blocking child DNS cannot survive the request deadline. Parent health survives. The
   supervisor IPC wait uses uncapped `D - monotonic()`; the network runner retains its 60-second
   per-stage idle cap.
5. The NAT64 well-known prefix `64:ff9b::/96` is rejected when its embedded IPv4 address is
   non-global. This closes the conditional embedded-private route without claiming that the
   deployment uses NAT64.
6. Up to four deduplicated, already-validated public numeric addresses are tried in DNS order
   under the same result-acceptance budget and per-stage idle timeout. A connection/setup
   failure may advance to the next address; an HTTP response never does. The original hostname
   remains HTTPS SNI and certificate identity.

The obsolete `NoRedirect` helper was removed. Manual same-origin redirect handling remains in
the pinned connection path, with a fresh public-address validation on every redirect.

The process boundary addresses the confirmed threat: source_proxy caller death or a slow remote
origin must not leave a probe child doing outbound work. The supervisor keeps at most two active
children and four handler threads, returns unavailable JSON on timeout/busy/failure, and keeps
the existing 128 MiB/32-PID lane limits. Rollback is the previous runtime commit series
(`4fa02ad` through `5a725ad`) if the owner chooses to revert the architecture; no Compose,
release, version, or dist change is required. The remaining platform limit is ordinary OS
scheduling/kernel behavior: the measured cleanup grace is bounded, not real-time.

## Verification commands

All output below is committed under `.orchestra/tasks/1/`.

| Check | Result | Raw output |
|---|---:|---|
| Python discovery: `python3 -m unittest discover -s tests -p 'test_*.py' -v` | 284 passed | `python-suite.txt` |
| Node contracts: `node --test tests/safe_fetch.test.mjs tests/worker_plan.test.mjs tests/ui/ui_contract.test.mjs` | 64 passed | `node-suite.txt` |
| Focused probe regressions: `python3 -m unittest discover -s tests -p 'test_probe_worker.py' -v` | 38 passed | `test_probe_worker.txt` |
| Service budget regressions: `python3 -m unittest discover -s tests -p 'test_seo_employee_service_v2.py' -v` | 27 passed | `python-suite.txt` |
| `collect_sources` → source proxies → real supervisor children → adapters → `_build_v2_report` | 1 passed; report state `ready`; `missing_data` includes unavailable PSI and not-configured GSC/DataForSEO | `collect-sources-report.raw.txt` |
| Compose syntax: `docker compose -f deploy/compose.yaml config -q` | OK | `compose-config.txt` |
| Diff whitespace: `git diff --check` | OK | `diff-check.txt` |
| Corrected Sol runtime reproductions | all assertions passed | `sol-followup-runtime.raw.txt` |
| External process-boundary acceptance | 6 passed, exit 0, 11.2s | `.orchestra/tasks/3/raw/acceptance-suite-latest.raw.txt` and NDJSON raw files |
| Failed optional model-review route | dedicated executor refused requester; Sol is the only reviewer | `luna-review-tool-error.raw.txt` |

The focused suite includes actual loopback HTTP and TLS sockets plus a scaled real-child IPC
deadline regression. The TLS regression generates a
short-lived certificate, verifies the numeric pinned connection, preserves SNI, and checks the
bracket-correct Host authority. The corrected runtime reproduction records the exact 1.0-second
deadline result-acceptance check (`elapsed=1.35` → `TimeoutError`), malformed HTTP JSON responses
for all three lanes, both IPv6 Host forms, NAT64 rejection, and first-address fallback order.
The prior Sol slow-drip reproduction remains preserved in `sol-f219de2-deadline.raw.txt`; the
new process-boundary acceptance transcript records parent/child PIDs, D/response/cleanup timing,
dripper close events, DNS-child cancellation, caller death, overload, health, process-group
membership, and no live children after cleanup.

## Isolated live worker evidence

`isolated_live_workers.py` starts three fresh supervisor subprocesses from the checked-out
`runtime/probe/entrypoint.py`, then calls each `/run` endpoint over loopback for
`https://books.toscrape.com/`; each request launches a fresh child. It does not reuse
`extella-p2-check` or any existing worker.
The parsed payloads and process cleanup evidence are in `isolated-live-workers.json` and
`isolated-live-workers.raw.txt`. Nu and SecurityProbe returned successful bounded payloads;
the Common Crawl provider was transiently unavailable in this run, which is represented as
`status=unavailable` rather than hidden.

The current worker was also copied into a fresh image with:

```text
docker build --no-cache -f runtime/probe/Dockerfile -t extella-p2-luna-probe:20260915 .
```

Image identity: `sha256:7e795439db94a0c564fdbcbbcd3fcf23e5812622d5dc9a922886851902cae6c`.
Three temporary containers returned HTTP 200 bounded Nu, SecurityProbe, and Common Crawl
payloads for the same public URL; their raw replies are in `isolated-docker-workers.raw.txt`.
The temporary containers and image were removed (`probe-image-remove.txt`). No Compose service
was started, stopped, restarted, or changed.

## Restrictions honored

No main service was modified or restarted. No release/store/publish action, `dist/` or version
change, GSC OAuth change, or external publication was performed. The only implementation files
changed are the probe worker, its focused tests, and the P2 verification documentation; the
remaining committed files are bounded verification artifacts for this task.

## Pre-mortem and remaining uncertainty

- DNS rebinding remains blocked: the request connects only to the numeric address returned by
  the immediately preceding all-public validation; same-origin redirects repeat that step;
  mixed public/private answers are rejected before `create_connection`. This is covered by the
  committed resolver/fallback tests and the corrected runtime reproductions.
- A failed first public address can no longer turn a live multi-address origin into an optional
  false negative unless all four bounded attempts fail or the deadline expires. No hostname is
  resolved between attempts.
- A malformed provider/site response becomes an optional unavailable payload, so a handler
  thread does not escape without JSON. The child process group is killed at its worker deadline;
  the outer source timeout remains a product-side race safety margin and kills `source_proxy`
  only if the worker fails to return in time.
- Historical Common Crawl records remain exact-URL evidence only; an old record cannot become a
  current-site defect or an instruction. Unsupported SSL Labs origins remain explicit missing
  enrichment rather than a foreign grade.
- Native libc `getaddrinfo` remains blocking inside the child, but the supervisor kills its
  process group at D; no resolver thread or descendant survives a confirmed reap. The prior
  source_proxy-only raw reproduction is retained as threat evidence, while the dedicated
  process-boundary harness is the acceptance evidence for the new design.
- Public provider contents and availability vary. The live Common Crawl miss above is retained
  verbatim; the fresh Docker run independently exercised the same current worker and returned
  all three payloads. GSC and DataForSEO remain deliberately not configured.

The architecture is implemented; final acceptance remains gated on the dedicated external
process-boundary harness and Sol's exact-HEAD review. The owner-approved residual limitation is
ordinary OS scheduling/kernel behavior, not an intentionally surviving remote probe worker.
