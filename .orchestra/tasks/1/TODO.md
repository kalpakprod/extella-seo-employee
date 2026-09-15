# P2 architecture handoff and residual limits

## Implemented owner-approved design

The supervisor architecture from `architecture.md` (owner commits `dae6adf`, `076211d`,
`fc9c00c`) is implemented in `runtime/probe/entrypoint.py`:

- parent validates request syntax without DNS, computes D after the bounded body, and starts
  `sys.executable entrypoint.py --probe-child <kind>` in a fresh process group;
- parent/child exchange one bounded UTF-8 JSON document over nonblocking selectors and closed
  stdin/stdout; stdout, envelope, and stderr are bounded;
- SIGKILL targets only the child process group at D, followed by bounded 0.25s reap; pending
  reaps retain their slot and degrade `/health`; confirmed reap releases the slot exactly once;
- at most two active children and four handler threads are admitted; child-slot busy is HTTP 200
  unavailable/http_503, while pre-thread overload is HTTP 503 with exact `{"status":"error","code":"http_503"}`;
- source budget B reserves one second for worker IPC/cleanup/response (`floor((B-1)*1000)` wire
  timeout; B≤1 skips the worker locally). Required audit/report schemas remain unchanged.

## Threat and rollback

This boundary stops slow-drip site/provider reads and blocking child DNS after D, including when
the caller/source_proxy dies. The previous source_proxy-only reproduction remains in
`sol-f219de2-deadline.raw.txt`; it is threat evidence, not the acceptance result for the new
design. Rollback is the pre-isolation runtime series ending at `5a725ad`; no Compose production
service, release artifact, version, or Orchestra restart is involved.

## Residual limits

OS scheduling and kernel SIGKILL/reap behavior are not real-time guarantees. Slow TCP ingress is
bounded by four handler slots and the two-second handler idle timeout, not by child D. A delayed
kernel reap retains the registry slot and returns degraded health; the supervisor does not spawn
unbounded cleanup threads or silently claim successful cancellation.

The exact Sol raw reproduction is preserved in `sol-f219de2-deadline.raw.txt`; the dedicated
external acceptance harness and its `/proc`/dripper transcript live under `.orchestra/tasks/3/`.
The latest exact-runtime rerun is `raw/acceptance-suite-final.raw.txt`; its six scenarios passed.
