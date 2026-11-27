# HU v2.2.1 preflop continuity narrow fix — development results

Date: 2026-10-15  
Candidate: `.work/hu-v2-strength-v2.2.1`  
Parent: frozen `.work/hu-v2-strength-v2.2.0` final-r5. The parent candidate was not modified.

## Narrow issue and fix

The v2.2.0 final-r5 read-only check found a price-routing discontinuity in BTN versus 3bet: 8.9BB and 9.1BB were off-grid and used the legacy path with 858 calls, while exact 9BB and 10BB keys used the new path with 0 calls. This was a route/threshold discontinuity, not an SDK legality failure.

v2.2.1 adds a narrow continuous-price corridor for `BTN_VS_3BET`: zero ante, consistent history, 80–120BB effective depth, and 7–10BB actual 3bet-to. Values such as 8.9BB and 9.1BB remain `off_grid=true` in the node key and trace; they are not disguised as table hits. The conditional call composition is explicitly price/hand-class conditioned rather than setting the PDF's nominal 37.5% as a hard target. The 2.2× 4bet candidate remains an actual integer raise-to amount.

The same-node 4bet gate is also narrowed in this candidate to a simple,
explicit pressure mix: `TT+/AK` value core plus `A4s/A5s` blocker rows. Broad
`strong` profile membership is no longer sufficient to 4bet, so A2s, KJo and
77 enter the price-call branch rather than being deterministic 4bets. The
continue bucket is separately explicit: pairs, suitable suited hands, and
higher one-gap offsuit connectors; low offsuit gap hands such as 65o, 76o and
97o are not auto-called at the 10BB price. This is still a simplified
PDF-pressure baseline, not a calibrated mixed strategy.

## Read-only continuity result

Using real ChipZen `GameState` objects and all 1,326 hole-card combinations at each price:

| Actual 3bet-to | Fold | Call | Raise | 2.2× 4bet candidate | Off-grid rows |
|---:|---:|---:|---:|---:|---:|
| 8.9BB | 872 | 400 | 54 | 1,958 | 1,326 |
| 9.0BB | 872 | 400 | 54 | 1,980 | 0 |
| 9.1BB | 872 | 400 | 54 | 2,002 | 1,326 |
| 10.0BB | 872 | 400 | 54 | 2,200 | 0 |

All 5,304 rows were legal with zero fallback. The result removes the 858→0 call discontinuity without claiming a GTO or matched-event frequency. The 400 calls are a deterministic structural mix, not a copied 37.5% PDF target.

For the reachable-range denominator, the BTN first-in 2.5BB raise slice is
1,074 concrete combinations. Restricting the 3bet response to that RFI range
gives, at every tested price, 54 raises / 400 calls / 620 folds: 5.028% / 37.244%
/ 57.728%. This conditional rate is the relevant comparison for a prior BTN
raise followed by a 3bet; the all-1,326 distribution is retained only as a
coverage audit and must not be read as an event frequency. The RFI definition
and rates are in the continuity JSON.

## Frozen-parent same-state delta

The frozen v2.2.0 final-r5 raw 1,326-row audit was compared with v2.2.1 using the same integer histories, cards, stacks, and legal-action menus. The v2.2.0 summary table had a presentation error: its `220` raise figure belongs to `BB_VS_OPEN`; the raw `BTN_VS_3BET` rows have 192 raises at each exact 7/8/9/10BB price. v2.2.1 does not add 28 exact-price 4bets.

| Actual 3bet-to | v2.2.0 actions | v2.2.1 actions | Changed rows | Transition delta |
|---:|---|---|---:|---|
| 8.9BB off-grid | 858 call / 468 fold | 400 call / 872 fold / 54 raise | 522 | 436 call→fold; 54 call→raise; 32 fold→call |
| 9.0BB keyed | 0 call / 1,134 fold / 192 raise | 400 call / 872 fold / 54 raise | 424 | 286 fold→call; 114 raise→call; 24 raise→fold |
| 9.1BB off-grid | 858 call / 468 fold | 400 call / 872 fold / 54 raise | 522 | 436 call→fold; 54 call→raise; 32 fold→call |
| 10.0BB keyed | 0 call / 1,134 fold / 192 raise | 400 call / 872 fold / 54 raise | 424 | 286 fold→call; 114 raise→call; 24 raise→fold |

The 8.9/9.1 changes are intentional consequences of extending the new
`BTN_VS_3BET` branch across the requested 7–10BB continuous corridor; they
are not disguised table hits, because all 2,652 such rows retain
`off_grid=true`. The exact-price 2.2× integer targets are unchanged; the
4bet count is intentionally narrowed in this same-node range fix. The
machine-readable comparison is
[`v2-preflop-v221-delta-audit.json`](../../v2-preflop-v221-delta-audit.json).

## PDF direction audit and explicit non-goal

The exact 9/10BB candidate is now directionally narrower than v2.2.0. The
1326-weighted action counts are `54 raise / 400 call / 872 fold` (4.07% / 30.17%
/ 65.76%). On the reachable 1,074-combination RFI raise slice they are
`54 / 400 / 620` (5.03% / 37.24% / 57.73%), directionally close to the PDF's
nominal `5.1% 4bet / 37.5% call` labels. These percentages are not being fit
as ground truth; they document the denominator and the simplified range
direction.

Representative classes on the exact 9/10BB tree are:

| Class | PDF page-5 direction | v2.2.0 | v2.2.1 |
|---|---|---|---|
| A2s | approximately 2% 4bet / 98% call | 100% 4bet | 100% call |
| KJo | approximately 5% 4bet / 95% call | 100% 4bet | 100% call |
| 77 | approximately 0% 4bet / 100% call | 100% 4bet | 100% call |
| A4s/A5s | small blocker mix | 100% 4bet | 100% 4bet |
| TT+/AK | value-core direction | 100% 4bet | 100% 4bet |
| 65o/76o/97o | conservative low offsuit boundary | legacy call/fold varies | 100% fold |
| 65s/55 | structural/paired continue sentinel | fold at keyed 9/10BB | 100% call |

This is directionally improved but remains a simplified range, not a passed
PDF-alignment gate. A2s/KJo/77 are calls on off-grid 8.9/9.1 as well; A4s/A5s
and TT+/AK retain the 2.2× branch. 65o/76o/97o fold while 65s/55 call in the
10BB keyed slice. A future range-quality slice would need its own isolated
source, delta audit, and full verification; it is outside a solver or
action-value claim.

The parent acceptance record [HU-V2-PREFLOP-V220-INDEPENDENT-ACCEPTANCE-2026-10-15.md](../../../../reports/research/HU-V2-PREFLOP-V220-INDEPENDENT-ACCEPTANCE-2026-10-15.md)
is the reason for this isolated repair. The frozen parent remains identified
by image `sha256:8bf7fce628b2f8f25c7e058d2e82720796c75d6c4a4ed508110af7de6c7bc933`,
archive SHA `bcc18ed6207412aebcb411111594aa7959f2bb9b632906b9d30111667c3f050b`,
and source SHA
`cbfdd5dbb875dc0ac65923ae40cf60fb80ed81156161e7b3e2327f05de9fc91f`.

Same-combo sentinels `65s`, `76s`, `55`, and `Q8s` all remain calls at 8.9/9.0/9.1/10.0BB. Their 8.9/9.1 rows retain `off_grid=true`; exact 9/10 rows use the explicit table corridor. The value core and blocker rows retain the 2.2× 4bet branch.

## Verification

- Full regression: **378 passed, 1 skipped**; Ruff passed.
- Dedicated continuity audit: 5,304/5,304 legal, zero fallback; each price has 54 raise / 400 call / 872 fold, with the 1,074-combo RFI-conditioned slice at 54 / 400 / 620.
- SDK validator/build: all checks passed with the pre-existing restricted `os` import warning only.
- Wire smoke: passed (`READY`, authentication, request-id echo and legal response).
- Archive verification: passed; 9 layers, 125,953,024 uncompressed layer bytes, Docker load round-trip image id matched the manifest.
- Restricted local endpoint probe (`linux/amd64`, 0.5 CPU / 256 MiB, 1,000 requests, 50ms deadline): 1,000/1,000 legal; p50 0.462ms, p95 0.615ms, p99 1.329ms, max 46.882ms. This is a local observation, not a platform SLA.
- The parent v2.2.0 full 1326-combo audit, SDK validator, short smoke, wire, restricted endpoint, and archive evidence remain inherited unchanged; final-r5 was not edited.
- v2.2.1 package/SDK/container/wire/archive verification is recorded by the separate final-r8 manifest.

Action value, opponent-conditioned Q, profile-specific EV, Match strength, platform settlement, and platform upload remain unverified. This is a narrow BTN-vs-3bet price/range engineering fix, not a strength proof.
