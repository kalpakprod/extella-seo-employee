# Extella standards and bridges compliance

Audit baseline:

- `AnvarBakiyev/extella-agent-standards@b8a628cfcc1c5a4cbbf8faa5a1ac4c48590e2b58`
- `AnvarBakiyev/extella-bridges@0820ed471e32f45cdb931eb33f391ce13a46d5a4`
- product stage: `build` / closed pilot

## Product boundary

Extella SEO Employee is a hybrid page plus Linux Docker device payload. The page uses the canonical H62 transport: `etb_run_expert` to the Extella wrapper and `etb_expert_result` back. It requests only `expert.run` and `device.run`, passes one `target`, and never receives an account token.

The model boundary is Agent Zero in a separate Docker network. `extella-bridges` provides an account-wide, explicit user-initiated Claude/Codex delegation product; it is not a dependency of SEO Employee. Adding that bridge would create a second model transport and a new cost/credential boundary, so it remains out of scope unless a future feature explicitly requires Codex or Claude delegation.

If that feature is approved later, the bridge must remain pinned, loopback-only, HMAC-signed, nonce/timestamp protected, account-bound, credential-scrubbed, and inactive for setup, status and health checks. A paid model call requires a separate explicit user request and cost warning.

## Machine-gate status

The unmodified standards gates pass `check_code_canon`, `check_automation_passport`, `check_waiting_state`, `check_listing_meta`, `check_brand_copy`, and `check_state_contract`. The release archive passes `check_self_check`, including its negative control.

Two upstream contradictions block a fully green unmodified run:

1. `DEPLOY_REQUIREMENTS.md` H62 and `templates/app-recipe` require `etb_run_expert`, but `check_ui_api_contract.py` does not recognize that transport.
2. `check_app_scopes.py` does not read linked page scripts and therefore cannot see the canonical bridge or its singular `target`.

The minimal, self-tested upstream correction is recorded in `patches/extella-agent-standards-etb-gates.patch`. It makes both original self-tests and the SEO Employee gates pass without adding dead product code or weakening assertions.

`check_ready_for_publish.py` also classifies Markdown specifications under `docs/contracts/` as customer documents solely because of the folder name. The repository contains no customer contracts, secrets or personal data. The narrow path-classification correction is recorded in `patches/extella-agent-standards-public-docs-gate.patch`; content scanning remains active.

## External blocker

The documented Extella installer environment supplies `EXTELLA_AGENT_ID`, `EXTELLA_APP_NAME` and `EXTELLA_APP_VERSION`, but no stable device identifier. SEO Employee therefore cannot invent a first-device binding. Installation is fully automatic only after a verified `device_binding.json` exists; first-device enrollment remains an explicit `prepare.py --device-id ...` step until the platform supplies a documented device ID or another authoritative binding route.

The CT160 `2.0.2` deployment uses the explicitly verified local Extella value `7e99c478-d104-412c-aef0-adceca7b8718`. This closes that deployment's binding, but does not remove the generic first-install platform gap.

No product code claims that this external blocker is closed.
