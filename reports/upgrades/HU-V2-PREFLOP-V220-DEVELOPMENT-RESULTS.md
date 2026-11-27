# HU v2.2.0 preflop slice — development results

Date: 2026-10-15  
Candidate: `.work/hu-v2-strength-v2.2.0`  
Baseline: frozen `.work/hu-v2-strength-v2.1.0` final-r2; v2.1.0, root `submission/`, and v1.10.1 were not modified.

## Scope and design

This slice uses the reviewed six-page preflop PDF as a teaching/reference pressure test only. It does not copy the PDF's nominal frequencies as online truth, and it is not a matched-event GTO or tournament-strength result. The online slice is enabled only for zero-ante, history-consistent, 80–120BB nodes. 60/20/8BB fixtures retain the conservative legacy path and are only legality smoke inputs.

The facts layer now records explicit integer raise-to keys for `BTN_FIRST_IN`, `BB_VS_OPEN`, `BTN_VS_3BET`, `BB_VS_4BET`, plus `VS_LIMP_RAISE`. Keys include effective depth, open/3bet/4bet amounts, history validity, off-grid status, legal actions, and the full action line. The policy has separate branch functions; limp/iso is not routed through BB-vs-open.

The simplified default range is deterministic and resource-bounded:

- BTN first-in: the 148 yellow classes from the reviewed PDF visual are a uniform 2.5BB raise-to range; the 21 offsuit grey classes are split into a small limp-to-1BB realization tail and an extreme offsuit fold tail. This yields 1,074 raises, 204 limps, and 48 folds over 1,326 combos. It is a pressure-test abstraction, not an OCR or a copied mixed strategy.
- BB versus open: strong value and a small fixed structural mix 3bet to approximately 4× the real open-to amount; the remaining defendable high-card/structured classes call at the current price, while the 21 BTN grey classes are treated only as an explicitly named engineering proxy boundary. The proxy is not the PDF's BB response table.
- BTN versus 3bet: strong value plus a small fixed Axs/pair/suited-connector structural mix uses a legal approximately 2.2× 4bet target. At 10BB 3bet, the legal candidate is 22BB; no non-all-in 4bet is invented when the SDK says it is unavailable.
- BB versus 4bet: premium value exposes both the legal approximately 2.2× non-all-in 5bet target and the all-in target. 65s and similar suited structures do not auto-call the 2.5→10→22BB price without a conditioned Q model.

All size changes use integer chip amounts and are legalized against SDK min/max. Off-grid prices (2.4BB open, 7.5BB 3bet, and a non-2.2× 4bet) are explicitly marked and routed conservatively; they are not mapped to a neighboring table.

## Full-combo audit

`tools/v2_preflop_1326_audit.py` constructed real ChipZen `GameState` objects with consistent no-ante histories and all 1,326 combinations. The main grid contains 17,238 rows: 13 price configurations × 1,326 combos, all at 100BB.

| Node/configuration | Fold | Call | Raise | Raise-to candidates |
|---|---:|---:|---:|---|
| BTN first-in | 48 | 204 | 1,074 | 250 |
| BB vs 2.00BB open | 252 | 854 | 220 | 600 / 800 |
| BB vs 2.25BB open | 252 | 854 | 220 | 675 / 900 |
| BB vs 2.50BB open | 252 | 854 | 220 | 750 / 1,000 |
| BB vs 2.60BB open | 252 | 854 | 220 | 780 / 1,040 |
| BTN vs 7/8/9/10BB 3bet | 636 or 1,134 | 498 or 0 | 220 | 1,540 / 1,760 / 1,980 / 2,200 |
| BB vs 2.2× 7/8/9/10BB 4bet | 1,216 | 70 | 40 | 3,388 / 3,872 / 4,356 / 4,840 plus 10,000 jam |

All 17,238 rows were legal and had zero runtime fallback. The BB 2.5BB row is 19.0% fold / 64.4% call / 16.6% raise, compared directionally with the PDF's nominal 20.4% / 55.22% / 24.38% only; the denominators and strategy semantics are not claimed equivalent. The 21 BTN grey classes are also inspected separately as a proxy boundary (for example Q2o/J4o/T4o/94o fold in the BB proxy); this must not be read as the PDF BB matrix.

BTN sentinels: `K4o`, `97o`, `Q8o`, and `AA` raise to 2.5BB; `72o` and `32o` fold. BB sentinels `Q8o`, `K4o`, `K8o`, `A2o`, `J8o`, and `97o` defend by call at both 2.0BB and 2.5BB in this simplified baseline.

The 5BB iso reverse sentinel is intentionally a risk boundary, not a passed strategy claim: Q2o may limp in the gray tail and folds against the explicit `VS_LIMP_RAISE` iso branch. Relative to folding immediately after posting the 0.5BB blind, that response loses an additional 0.5BB whenever the iso is reached. Its unmodeled sensitivity is `ΔQ = p_iso × (-0.5BB) + (1-p_iso) × ΔQ_checked/realized`; no positive value is asserted. Premium limp protection is not claimed because the current default range opens AA/KK/AK rather than mixing them into the gray limp tail.

## Engineering verification

- Full regression: **376 passed, 1 skipped**; Ruff passed.
- SDK validator/build: passed, with the existing restricted-`os` import warning.
- Targeted v2.2 tests: 15 passed, including node-key, price, depth/ante, limp/iso, 4bet, non-all-in 5bet/jam, and reverse sentinels.
- Short paired smoke: 12 fixed 100BB hands across seat swaps and tight/call/aggressive scripted profiles, completed with zero fallbacks. This is legal/completion evidence only, not strength evidence.
- WebSocket wire smoke: passed.
- Restricted endpoint: 0.5 CPU/256MiB, 1,000 samples at 50ms timeout, 1,000/1,000 legal; p50 0.486ms, p95 0.656ms, p99 1.089ms, max 41.372ms. A separate 10,000-sample run at 2,000ms also returned 10,000/10,000 legal with p50 0.389ms, p95 0.636ms, p99 1.179ms, max 45.651ms. These are local server-send/client-receive observations, not platform SLA.
- Archive: 9 layers, 125,952,000 uncompressed layer bytes, SHA/load round-trip passed.

## Package and limits

Final package is unuploaded and not platform-active. The final-r5 manifest is the authoritative record for the image ID and source/archive SHA256 values.

Action value remains model-limited: no conditioned opponent range/Q, same-deal profile EV, platform non-equal settlement, or event-strength result was established. The six requested normal/wide-call/strong-iso/linear-3bet/strong-4bet/reverse images are therefore not reported as performance wins; only the explicit static sentinels and the short legal smoke are recorded.
