<p align="center">
  <a href="./README.md">Русский</a> · <strong>English</strong>
</p>

<p align="center">
  <img src="./assets/readme/hero-en.svg" alt="Extella SEO Employee: a verifiable SEO audit from data collection to prioritized tasks" width="1200">
</p>

**Extella SEO Employee 2.0** is a self-hosted SEO worker for agencies and specialists who need repeatable technical audits across multiple websites. On request or once a day, it collects data, checks it with two independent engines, and turns confirmed problems into clear tasks.

Status: **`SHIP_CLOSED_PILOT`**. The system is ready for a closed pilot, but is not yet presented as a public production service.

## What is verified

| Check | Result |
|---|---|
| Local tests | Python `168/168`; Node `62/64`, with 2 Linux-only tests skipped on Windows |
| Clean CT160 run | Python `168/168`; Node `64/64`; 7 services healthy |
| Full API E2E | CrawlSEO `25/25`; SEOmator `25/25` + 5 browser samples; Agent Zero `10/10`; 10 tasks created |
| Target safety | Private addresses are blocked at the actual fetch boundary; site ownership is confirmed before an audit |
| Independent review | OpenExecutive: `SHIP_CLOSED_PILOT`, severity `low`; final Sol review: `REVIEW_APPROVED`, no P0/P1 |

Full verification record: [`dist/ct160-verification-v2.json`](./dist/ct160-verification-v2.json). Final audit: [`reviews/universal-seo-employee-v2-final-review.md`](./reviews/universal-seo-employee-v2-final-review.md).

## What the worker does

- Manages multiple isolated targets with separate history, baselines, and a FIFO queue.
- Runs an audit on demand or on a daily schedule.
- Supports `service_b2b`, `ecommerce`, `local_business`, `content_media`, and `saas_marketplace` profiles.
- Crawls from 1 to 100 pages, with 25 as the default.
- Combines deterministic CrawlSEO and SEOmator results into one evidence package.
- Gives Agent Zero sanitized facts only; the agent explains impact and the smallest fix without site-access tools.
- Preserves results when the model is unavailable: facts do not depend on generated text.

## Architecture

<p align="center">
  <img src="./assets/readme/architecture-en.svg" alt="Extella SEO Employee architecture with a deterministic core and an isolated model boundary" width="1200">
</p>

1. The user adds an authorized public website and selects a profile.
2. `AuditPlan` freezes the page budget, rules, and sources before execution.
3. CrawlSEO and SEOmator independently produce evidence; queueing and state remain deterministic.
4. Agent Zero explains confirmed facts only. If the model is unavailable, the system preserves deterministic tasks and evidence without a model explanation.

Google Search Console and DataForSEO are optional data sources. Their absence does not block the free technical audit.

## Quick start

Requirements: Linux `amd64`, Docker Engine with Compose v2, Python 3.11+, and Git. From the project directory:

```sh
python3 tools/selfcheck.py
cp deploy/.env.example deploy/.env
python3 deploy/prepare.py \
  --device-id '<Extella device id>' \
  --hosting-profile client_server \
  --agent-id '<agent_... from Extella>'
docker compose --project-name extella-seo-release -f deploy/compose.yaml up -d
```

After startup, the owner opens Agent Zero at `http://127.0.0.1:50081`, connects a provider, and selects a model. Check the services with:

```sh
docker compose --project-name extella-seo-release -f deploy/compose.yaml ps
python3 deploy/probe.py health
python3 deploy/probe.py state
```

The product API listens only on `http://127.0.0.1:8088`. Hosting requires a separate TLS reverse proxy and external authentication. See [`deploy/README.md`](./deploy/README.md) for the complete procedure, including an existing Agent Zero deployment.

## Release boundaries

- The worker does **not** change the website, publish content, perform outreach, or buy links.
- Only `agy/gemini-3.7-flash-high` has a verified route. Arbitrary models, consumer subscriptions, and BYOK remain unverified until a separate E2E; there is no automatic model fallback.
- Google Search Console and DataForSEO OAuth, design-partner domain results, demand, ROI, revenue, and production SLA are not verified yet.
- The verdict covers a closed pilot, not a public production launch.

## Documentation

- [Feature Contract](./docs/contracts/feature-seo-audit-monitoring.md)
- [Service Contract](./docs/contracts/service-seo-runner.md)
- [UI Contract](./docs/contracts/ui-seo-panel.md)
- [System Blueprint](./docs/blueprints/system.md)
- [Universal Employee Blueprint](./docs/blueprints/universal-seo-employee-v2.md)
- [Requirements and test traceability](./docs/verification/traceability.md)
- [Investor one-page](./docs/investor/one-page.md)
- [Investor deck](./docs/investor/deck.md)
- [Third-party notices](./THIRD_PARTY_NOTICES.md)

## 2.0.0 artifacts

| Artifact | SHA-256 |
|---|---|
| [`extella-seo-employee-runtime-2.0.0.zip`](./dist/extella-seo-employee-runtime-2.0.0.zip) | `fe460f2b2e7a282d1ba6c367c55d601ada416ce69a20f57c740551e277c253a4` |
| [`extella-seo-employee-page-2.0.0.zip`](./dist/extella-seo-employee-page-2.0.0.zip) | `2e1530cc531b6ce3000b7193d3f89ccfc8b7974d4becfd2fcd387ec15befc832` |

Component licenses and terms are governed by their upstream projects. Required notices are preserved in [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md). A separate license review of the complete release bundle is required before commercial distribution.
