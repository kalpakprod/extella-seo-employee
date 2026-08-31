# DonSeTch and Extella Universal SEO Employee 2.0

## Verdict

**Verdict: reject now.** DonSeTch must not become either the Extella core or an optional runtime sidecar in the current closed pilot.

It is a capable independent web MCP, not a deterministic SEO evidence engine. Its `web_fetch`, `web_search` and `web_crawl` surface overlaps with the existing CrawlSEO and SEOmator acquisition layer, but it does not supply Extella's SEO rule catalog, source-status semantics, coverage contract, per-target state/history or proposal boundary. Replacing the established core would therefore discard the product's deterministic evidence model and add a second crawler to reconcile.

The candidate-changing question is license and trust-boundary fit. DonSeTch is `AGPL-3.0-only`, while the current Extella release already has a narrower no-tools Agent Zero boundary and deterministic CrawlSEO plus SEOmator core. There is no branch of that question in which core adoption is justified for this pilot. A network boundary is not an automatic legal compatibility decision.

## Scope and evidence

Reviewed on 2026-08-31:

- GitHub metadata and releases for [dondai44423/donsetch](https://github.com/dondai44423/donsetch): created 2026-08-07; at review time GitHub reported 538 stars, 34 forks and 10 contributors. The root snapshot reported 536 stars, which had already changed by this read.
- [v3.4.4 release](https://github.com/dondai44423/donsetch/releases/tag/v3.4.4), published 2026-08-31. The release list shows more than 20 releases during August.
- A fresh shallow source clone at `master` commit `6c065420415c9fc66ef869a6cb56fc5c3b856267`; its `Cargo.toml` declares Rust, version `3.4.4` and `AGPL-3.0-only`.
- [MCP dispatch](https://github.com/dondai44423/donsetch/blob/master/src/mcp/server.rs), [HTTP transport](https://github.com/dondai44423/donsetch/blob/master/src/mcp/http.rs), [Docker Compose](https://github.com/dondai44423/donsetch/blob/master/docker-compose.yml), [Dockerfile](https://github.com/dondai44423/donsetch/blob/master/Dockerfile), [SSRF guards](https://github.com/dondai44423/donsetch/blob/master/src/fetch/guards.rs) and [BYOK storage](https://github.com/dondai44423/donsetch/blob/master/src/search/byok/store.rs).

The supplied maturity classification is **A**. The source supports a mature release workflow, Docker image, stdio and HTTP MCP transports, tests and recent security fixes. It does not convert rapid release cadence into compatibility with Extella.

## Wheel score

Scores are directional review estimates, not financial calculations. `gain` measures useful coverage after Extella's existing core is considered; `cost` includes implementation, operations, license review and new trust boundaries. `score = gain / cost`.

| Option | Gain | Cost | Score | Decision |
|---|---:|---:|---:|---|
| Replace CrawlSEO plus SEOmator with DonSeTch core | 1 | 5 | 0.2 | Reject. DonSeTch supplies generic acquisition, not SEO evidence contracts or rule evaluation. |
| Add DonSeTch as an optional runtime sidecar now | 2 | 4 | 0.5 | Reject for the pilot. It creates an AGPL, egress, cache, cookie, Chrome and data-normalization boundary before there is a measured gap. |
| Preserve the current core and do not integrate | 5 | 1 | 5.0 | Choose. CrawlSEO plus SEOmator already provide the deterministic SEO evidence path; Agent Zero remains no-tools. |

## What DonSeTch adds, and why it is not enough

DonSeTch offers Rust-based fetch, search and crawl through MCP stdio or HTTP. It has cache persistence, optional Chrome escalation, cookie handling and optional BYOK search providers. Its HTTP Compose profile binds `127.0.0.1:8765` by default and supports optional bearer authentication for `/mcp`.

Those are useful capabilities for a future, separately bounded public-web acquisition experiment. They are not a substitute for Extella's deterministic SEO rule mapping, independent evidence contracts, coverage semantics, target isolation or state/history lifecycle. Passing DonSeTch output directly to Agent Zero would also violate the current design principle that the model receives only sanitized, pre-selected facts and has no tools.

## License, security and operations gaps

| Area | Observed fact | Required conclusion for this pilot |
|---|---|---|
| License | `Cargo.toml` and [LICENSE](https://github.com/dondai44423/donsetch/blob/master/LICENSE) specify `AGPL-3.0-only`. | Do not package, link, modify or embed it in the Extella release without an explicit license decision and legal review. A sidecar does not make the question disappear. |
| Network authority | The MCP server exposes fetch, search and crawl. Extella's Agent Zero is deliberately no-tools. | Never connect Agent Zero directly to DonSeTch. Any future adapter must have a fixed input/output contract and one-way sanitized results. |
| SSRF | Guards reject private addresses and revalidate redirects, but `DONSETCH_ALLOW_PRIVATE_EGRESS` disables the chain. The source documents a residual DNS TOCTOU window because full DNS pinning is not implemented. | The escape hatch must remain unset. An integration would still need its own allowlist, DNS policy and egress isolation. |
| Cached data and cookies | Compose persists fetch/search cache and a ghost browser profile. BYOK keys are stored in `~/.cache/donsetch/byok-keys.json`, with restrictive Unix file permissions but as serialized key material. | No shared volume, browser profile, cookies or BYOK configuration with Extella. Retention, purge and secret ownership would need an explicit contract. |
| Chrome | The image can install Chrome and sets `DONGHOST_NO_SANDBOX=1` for container use. | Chrome escalation is out of scope for the pilot. Do not enable it in an Extella deployment. |
| HTTP MCP | HTTP auth is optional; `/health` remains open. Default Compose binding is loopback. | Do not publish the port. A future service would require an explicit secret file, network policy and authenticated integration test. |
| Supply chain and release churn | The Dockerfile builds from mutable base-image tags and downloads build dependencies. More than 20 August releases show active maintenance but a fast-changing contract. | Do not use `latest`. A future evaluation must pin an exact upstream release artifact or source commit, verify hashes and run a compatibility suite before every update. |
| Resource envelope | Upstream Compose documents a 2 GiB memory limit for OCR and reranking workloads, plus a 45-second stop grace period. | This is a separate resource and lifecycle budget, not covered by the existing Extella core capacity assumptions. |

## Already in the box: no new component to enable

The current CrawlSEO plus SEOmator core already supplies the required deterministic acquisition and SEO evidence path. Extella's no-tools Agent Zero boundary, source-status handling, per-target history and queue should remain unchanged.

## Later: do not build yet

Reconsider DonSeTch only if a measured pilot gap requires web retrieval that CrawlSEO and SEOmator cannot cover. Before any optional-sidecar proposal, all of these gates must pass:

1. The owner makes an explicit AGPL distribution and deployment decision after legal review.
2. The intended gap is recorded with a representative target, expected evidence shape and success/failure criteria.
3. DonSeTch runs in a separate, non-privileged container without Docker socket, host networking, Chrome, cookies, persisted BYOK keys or private-egress override.
4. A deterministic adapter validates a fixed public URL input and converts only bounded, sanitized results into the existing Extella evidence schema. It cannot call Agent Zero or execute a recommendation.
5. A fresh isolated E2E proves SSRF policy, timeout/cancellation, cache purge, no secret transfer, evidence preservation on model failure and no regression of the no-tools boundary.

Until then, the correct adoption mode is **reject**, not `core`, `optional sidecar`, `extend-core` or `hard-fork`.
