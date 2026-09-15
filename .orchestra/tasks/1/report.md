# Extella P2 verification report

Date: 2026-09-15

Implementation commit: `4fa02ad8541cc4980731164998bca0daba8345ee`

Documentation/evidence commit: the final HEAD reported with this task.

## Result

The endpoint, malformed-response, IPv6, NAT64, and validated-address findings are corrected
in `runtime/probe/entrypoint.py`. The wall-clock cancellation finding remains an explicit
owner decision, recorded in `TODO.md`:

1. SSL Labs is queried only for the default HTTPS origin. HTTP and HTTPS origins on a
   non-default port return `ssl_labs.status=unavailable` and `grade=null`, without asking
   the provider for a potentially foreign endpoint grade.
2. `http.client.HTTPException`, including `BadStatusLine`, is contained by every optional
   lane and by `handle_run`; the HTTP worker contract returns bounded JSON unavailable data.
3. IPv6 literal authorities use `[address]` and `[address]:port` in the HTTP Host header.
4. The pinned site path uses one monotonic deadline for result acceptance and refreshes an
   idle socket timeout before each blocking stage/body chunk; an over-budget response is not
   reported as success. This is not hard wall-clock cancellation: a slow-drip body can keep a
   blocking read alive while bytes continue arriving, and provider `_get_json` has the same
   idle-timeout-only behavior. Per-request process isolation, nonblocking transport, and a
   cancellable resolver remain an owner decision and are not added in this scope.
5. The NAT64 well-known prefix `64:ff9b::/96` is rejected when its embedded IPv4 address is
   non-global. This closes the conditional embedded-private route without claiming that the
   deployment uses NAT64.
6. Up to four deduplicated, already-validated public numeric addresses are tried in DNS order
   under the same result-acceptance budget and per-stage idle timeout. A connection/setup
   failure may advance to the next address; an HTTP response never does. The original hostname
   remains HTTPS SNI and certificate identity.

The obsolete `NoRedirect` helper was removed. Manual same-origin redirect handling remains in
the pinned connection path, with a fresh public-address validation on every redirect.

## Verification commands

All output below is committed under `.orchestra/tasks/1/`.

| Check | Result | Raw output |
|---|---:|---|
| Python discovery: `python3 -m unittest discover -s tests -p 'test_*.py' -v` | 279 passed | `python-suite.txt` |
| Node contracts: `node --test tests/safe_fetch.test.mjs tests/worker_plan.test.mjs tests/ui/ui_contract.test.mjs` | 64 passed | `node-suite.txt` |
| Focused probe regressions: `python3 -m unittest discover -s tests -p 'test_probe_worker.py' -v` | 36 passed | `test_probe_worker.txt` |
| `collect_sources` → subprocess source proxies → real loopback probe handlers → adapters → `_build_v2_report` | 1 passed; report state `ready`; `missing_data` includes unavailable PSI and not-configured GSC/DataForSEO | `collect-sources-report.raw.txt` |
| Compose syntax: `docker compose -f deploy/compose.yaml config -q` | OK | `compose-config.txt` |
| Diff whitespace: `git diff --check` | OK | `diff-check.txt` |
| Corrected Sol runtime reproductions | all assertions passed | `sol-followup-runtime.raw.txt` |
| Sol slow-drip/process-boundary follow-up | WIP finding retained; hard cancellation unresolved | `sol-f219de2-deadline.raw.txt`, `TODO.md` |

The focused suite includes actual loopback HTTP and TLS sockets. The TLS regression generates a
short-lived certificate, verifies the numeric pinned connection, preserves SNI, and checks the
bracket-correct Host authority. The corrected runtime reproduction records the exact 1.0-second
deadline result-acceptance check (`elapsed=1.35` → `TimeoutError`), malformed HTTP JSON responses
for all three lanes, both IPv6 Host forms, NAT64 rejection, and first-address fallback order.
Sol's subsequent slow-drip reproduction is preserved in `sol-f219de2-deadline.raw.txt` and
documents the unresolved hard-cancellation boundary. It came from Sol repeat-review WIP
`71d2b1f8d6d9853f10bef862e8d35e537d46e3fd` (`.orchestra/tasks/2/review.md` in the Sol
worktree).

## Isolated live worker evidence

`isolated_live_workers.py` starts three fresh subprocesses from the checked-out
`runtime/probe/entrypoint.py`, then calls each `/run` endpoint over loopback for
`https://books.toscrape.com/`. It does not reuse `extella-p2-check` or any existing worker.
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
  thread does not escape without JSON. The outer source timeout bounds product-side waiting and
  kills `source_proxy` on expiry; it does not kill the remote Docker worker or its handler thread.
  A slow-drip remote request may therefore remain active until its body finishes or its idle
  socket timeout fires.
- Historical Common Crawl records remain exact-URL evidence only; an old record cannot become a
  current-site defect or an instruction. Unsupported SSL Labs origins remain explicit missing
  enrichment rather than a foreign grade.
- Native libc `getaddrinfo` has no cancellable timeout in Python's standard library. The worker
  checks the result-acceptance deadline before and after the resolver stage, but a resolver-level
  hang remains an environment-dependent limit. The existing outer source timeout stops product
  waiting and kills `source_proxy` only; it does not terminate a remote worker/handler.
- The hard wall-clock deadline is intentionally unresolved. Sol's slow-drip reproduction shows
  `_get_json(timeout=0.15)` completing at `0.307s`, pinned body reads returning only after
  `0.302s`, and `source_proxy` expiry leaving the remote worker alive with an active origin
  request. See `TODO.md` and `sol-f219de2-deadline.raw.txt`; per-request isolation,
  nonblocking I/O, or a cancellable resolver requires an owner architecture decision.
- Public provider contents and availability vary. The live Common Crawl miss above is retained
  verbatim; the fresh Docker run independently exercised the same current worker and returned
  all three payloads. GSC and DataForSEO remain deliberately not configured.

The implementation/report boundary is ready for owner review. This report makes no final PASS
claim for hard wall-clock cancellation; that item remains in `TODO.md` pending the architecture
decision.
