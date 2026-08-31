# Universal SEO Employee 2.0: verification progress

## Baseline 1.0.0

- Captured: 30.08.2026 before v2 implementation.
- Python: `python -m unittest discover -s tests -p "test_*.py" -v`, 51 passed.
- Node: `node --test tests/safe_fetch.test.mjs tests/ui/ui_contract.test.mjs`, 33 passed.
- Runtime ZIP SHA-256: `3185640f4dee808cf9ce95b9fceed6e7c32870d631fbfa57eb06f199c402a12b`.
- Page ZIP SHA-256: `4b0cde453459104f4877992bcd111242ad6f00053262aeb76af7171893b00da8`.
- `dist/ct160-verification.json` remains historical v1 evidence and MUST NOT be presented as v2 evidence.

## V2 progress

- Release evidence recorded at `2026-08-31T06:37:52Z` (UTC).

### Local release gate

- Python: `python -m unittest discover -s tests -p "test_*.py"`, 168 passed.
- Node: `node --test tests/safe_fetch.test.mjs tests/worker_plan.test.mjs tests/ui/ui_contract.test.mjs`, 62 passed and 2 Windows-only Linux cancellation tests skipped.
- The final runtime and page archive hashes independently matched the files: runtime `extella-seo-employee-runtime-2.0.0.zip`, SHA-256 `fe460f2b2e7a282d1ba6c367c55d601ada416ce69a20f57c740551e277c253a4`; page `extella-seo-employee-page-2.0.0.zip`, SHA-256 `2e1530cc531b6ce3000b7193d3f89ccfc8b7974d4becfd2fcd387ec15befc832`.

### Clean CT160 runtime

- v2 was deployed separately at `/opt/extella-seo-release-v2` with Compose project `extella-seo-release-v2`, loopback API `127.0.0.1:18092`, and 7 healthy services.
- Image IDs: product `sha256:903b86f292611677d1c14452cca2827d472eaacdf17d9970e84697aa2c912255`, CrawlSEO `sha256:435e67a16301c6ce5d6b2d216b8600b72e3cda220e0b76aa4bb9b74fbbad3010`, SEOmator `sha256:4b27ac6d0ecd941c6958cb59c02a7b19dd02b5359bcee9f3ed2b826809c88842`.
- Clean runtime checks passed: Python 168; Node 64/64, including Linux cancellation.

### Recovery and runtime probes

- The v1 backup snapshot `/mnt/usbdata/extella-seo-backups-v1/20260831T033842Z-d77364cb` verified successfully and returned `restore-check-ok` with temporary-only staging.
- `/mnt/usbdata/extella-seo-backups-v1/20260831T033708Z-cb9891ec` is retained but invalid as v1 evidence because it has the wrong scope. It was not deleted.
- Agent Zero no-tools preflight passed. The configured chat and utility model is `agy/gemini-3.7-flash-high`; its authentication mechanism and subscription remain `not_verified`.
- In the direct `https://example.com/` worker probe, CrawlSEO and SEOmator each crawled 1 page. CrawlSEO reported `search_performance=not_configured`.
- The ownership-confirmed API gate correctly blocks a run without confirmation.
- Both workers completed 25/25 pages on `https://quotes.toscrape.com/`. Both workers reject a private target after the Playwright SSRF patch, while the public path remains operational.
- The final v2 project-scoped snapshot `/mnt/usbdata/extella-seo-backups-v2/20260831T063614Z-15ebb195` verified and returned `restore-check-ok` with temporary-only staging.
- The published gateway now passes `/api/targets` and one canonical `target_id` query for `/api/state`; extra or unsafe query forms remain rejected.
- Full API E2E on the public `books.toscrape.com` fixture finished `ready` in 569 seconds after the final redirect/cache corrections: CrawlSEO 25/25, SEOmator 25/25 plus 5 browser samples, Agent Zero 10/10 enrichments, 10 tasks, queue `completed`.

### Release boundary

- An actual end-to-end audit of a user-owned domain remains `not_verified`.
- The v1 `dist/ct160-verification.json` remains historical evidence and must not be presented as v2 evidence.
