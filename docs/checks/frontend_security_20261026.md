# Browser auth, privacy, XSS and Quality check

Tested application: `http://localhost:8080`, Docker image built from main `3293368`. Actual run timestamp: `2026-10-26T17:16:23.188Z`. Machine-readable results: [frontend_security_20261026.json](frontend_security_20261026.json).

```sh
OKNO_SECURITY_REPORT=/tmp/okno-live-security-combined.json \
  node frontend/scripts/security-smoke.mjs --live-auth
npm run --prefix frontend lint
```

Both commands passed. The script uses the pinned frontend Playwright dependency and installed Chrome; it downloads no browser. Default mode runs isolated synthetic XSS checks. `--live-auth` additionally exercises the real same-origin API and UI with the public demo identities. No tokens, private run IDs, guest passport UUIDs or guest text are saved in this report.

## Real API and UI results

- Cascade and Brevis: login role `hotelier`, one selector option each, own hotel selected and cabinet HTTP 200. Demo admin: role `demo_admin`, all 250 options. Five successful cabinet GET responses in the run.
- 23 explicit HTTP boundary checks: **7 × 200, 7 × 403, 9 × 401**, all expected. Cross-tenant cabinet and passport requests returned 403. Anonymous passport listing returned 401; tenant listing contained only its own hotel.
- Native EventSource used the real HttpOnly cookie to receive existing private interview traces: Cascade 2 steps, Brevis 4 steps, both completed. Anonymous private run/stream requests returned 401; foreign tenant streams returned 403. An explicit tenant Bearer did not fall back to a valid admin cookie; invalid Bearer returned 401.
- Cookie attributes: HttpOnly, SameSite=Strict, Path=/api/runs. UI logout for all three identities deleted cookie, local token and session metadata. Subsequent private SSE requests returned 401.
- Public Changes contained **1 event**, **0 individual notifications**, and aggregate count **5**. Admin could read the actual 5 notifications. Public JSON did not contain passport_id/item_ids or any of the 6 existing guest passport UUIDs. Guest simulation and refresh controls were disabled and the login prompt was visible. No change mutation was invoked.
- Real Quality API and the served baseline artifact matched. UI displayed **132** radius candidates, **60** compatible sea+sunset model candidates and **20** outside the radius, including **4** partial results. Runtime provenance: known 24,413; non_synthetic 24,329; synthetic 84 including mixed 20; unknown/stale/disputed/empty 1,721. Synthetic share: 0.3441% of known materialized values, not hotels.
- Desktop and 390px mobile Quality layouts were visually inspected; document width remained 390px. The whole Quality check took 5,673 ms, including artifact comparison, screenshots and viewport checks; this is not an isolated API latency measurement.
- Uncaught browser errors and unexpected external requests: **0**. The live context rejected any attempted write other than login/logout; no forbidden request was attempted.

## Synthetic XSS context

Eight checks passed: literal hotel names in React, map-marker title/aria-label, hover, evidence quote dialog, raw-answer textarea, parsed quote/JSON preview, hotel website URL and evidence URL. HTML/script strings created no injected elements and ran no code. Both `javascript:` URLs were neutralized by React before actual clicks; the network block was not used to mask execution. Dialogs, uncaught errors and external requests: 0.

Synthetic search/evaluate/answer responses were fulfilled only inside this isolated test context. These three test POSTs never reached the server. The legitimate adversarial instruction text remains in the regression source.

## Permitted side effect and limits

The real cabinet GET invokes existing idempotent pending-question generation. That behavior and login/logout were explicitly authorized for this live smoke; pending questions may have been materialized. No answer, confirm, passport/action, Studio, collect-run, simulated building or refresh request was made, and the demo video was not recorded or modified.

These are access-boundary regressions with intentionally public demo accounts, not production IAM or a penetration test. Logout removes this browser's cookie/token; copied stateless Bearer tokens remain valid until expiry or process restart. The filter comparison describes saved model outputs, not actual windows, conversion or field accuracy.
