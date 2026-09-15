# P2 follow-ups

## Owner decision required: hard wall-clock cancellation

The current probe path has a result-acceptance deadline and per-stage socket idle
timeouts. They prevent a late successful result from entering the report, but they do
not interrupt a slow-drip response while bytes continue arriving. `_get_json` provider
reads likewise retain an idle timeout without an aggregate read deadline in this scope.

Sol's exact reproduction is preserved in `sol-f219de2-deadline.raw.txt`; it came from the
repeat-review WIP `71d2b1f8d6d9853f10bef862e8d35e537d46e3fd`. The source reproduction is
`f219de2_deadline_repro.py` in the Sol review worktree. It demonstrates:

- `_get_json(timeout=0.15)` returning success after `0.307s` for a one-byte/50ms drip;
- pinned `fetch_bytes(timeout=0.15)` rejecting after `0.302s`, while the blocking body read
  itself remains active until the socket operation returns;
- `source_proxy` being killed at its caller timeout while the separate remote probe worker
  remains alive with an active origin request.

The existing outer source timeout bounds product wait and kills `source_proxy` only. It
does not kill a remote Docker worker or its handler thread. No per-request child isolation,
nonblocking/select transport, or cancellable resolver is added here; those are an
architecture choice for the owner. Until that choice, do not describe the deadline as a
hard cancellation or claim that the remote worker lifetime is bounded by `source_proxy`.
