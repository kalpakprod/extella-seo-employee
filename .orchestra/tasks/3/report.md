# P2 external supervisor/child acceptance harness

## Tested runtime

The harness was run against the exact checked-out runtime commit
`227d4fcd4deb155fdc88253d94e497c0368e622c` (`runtime/probe/entrypoint.py`), after
the `6d5a861` IPC deadline fix. The runtime source blob in that checkout is
`93d436c052f21c48681c2a85cbb7d27ab1cef432`.

Exact command:

```text
env EXTELLA_ACCEPTANCE_RUNTIME=/home/kukuruza/orca/projects/orchestra/worktrees/home-kukuruza-orca-extella-seo-employee/extella-p2-luna/runtime/probe/entrypoint.py EXTELLA_P2_RAW_DIR=/home/kukuruza/orca/projects/orchestra/worktrees/home-kukuruza-orca-extella-seo-employee/extella-p2-luna-tests/.orchestra/tasks/3/raw node --test tests/probe_supervisor_acceptance.test.mjs
```

Result: exit `0`, six tests passed, zero failed, duration `11402.103366ms`.
The exact TAP output is in `raw/acceptance-suite-227d4fc.raw.txt`; per-event
timestamps, PIDs, process commands, responses and fixture socket events are in
the six scenario NDJSON files under `raw/`.

## Coverage

- `slow-site`: exact production entrypoint receives a local slow-drip site; the
  child PID is observed through `/proc`, returns HTTP 200 unavailable/timeout at
  D, the origin observes the closed socket, health returns 200, and the child
  process group has no live members after cleanup.
- `slow-provider`: a child-only `sitecustomize.py` redirects fixed provider URLs
  to the fixture; the fixture drips provider JSON, records body bytes and the
  client close, and the supervisor reaps the child at D.
- `caller-killed`: a real `runtime/source_proxy.py` caller is SIGKILLed after a
  child is observed. The remote supervisor still closes the origin connection
  at its own D, reaps the child/process group, and remains healthy.
- `overload-health`: two real child PIDs occupy the slots; health remains HTTP
  200 `ok`, a third request returns HTTP 200 unavailable/http_503, both children
  are reaped, and a following Nu request succeeds.
- `dns-and-cleanup`: only the test-local child fixture blocks
  `socket.getaddrinfo`; the production parent has no DNS test hook and the
  fixture origin sees no site request. The child times out, is reaped, and its
  process group is empty.
- `repeated-cleanup`: five concurrent requests produce the allowed child-slot
  HTTP 200 unavailable and pre-thread HTTP 503 forms, leave no live child or
  process-group member, and the next request succeeds.

`tests/fixtures/probe_acceptance_fixture.py` is a stdlib-only local origin and
provider server with raw wall/monotonic timestamps. `tests/fixtures/sitecustomize.py`
is supplied only through test `PYTHONPATH` and activates only for
`--probe-child`; it redirects fixed providers or blocks child DNS and does not
replace the supervisor, IPC, deadline, cancellation, admission, or exit path.
The harness also asserts that the production runtime contains no
`EXTELLA_ACCEPTANCE` backdoor.

This is an external process-boundary acceptance result, not a release or a
claim that all TLS/Common Crawl/network regressions or the independent Sol
review are complete.
