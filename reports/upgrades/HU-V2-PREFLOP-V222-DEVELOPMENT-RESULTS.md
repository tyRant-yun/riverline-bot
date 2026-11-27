# HU v2.2.2 preflop limp/iso and off-grid price repair — development results

Date: 2026-10-15  
Candidate: `.work/hu-v2-strength-v2.2.2`  
Parent: frozen `.work/hu-v2-strength-v2.2.1` final-r8. The parent candidate and
root `submission/` were not modified.

## Scope and implementation

This is a narrow engineering candidate. It keeps the v2.2.1 2.5BB BTN open
size and its 7–10BB BTN-vs-3bet action mix, then adds three bounded changes:

1. BTN first-in limp closure now uses two playable gray offsuit classes
   (`Q3o`, `J4o`); the weaker gray classes are folds. A small,
   deterministic card-combination hash selects a limited protection mix from
   `AA`, `KK`, `AKs`, `A5s`, selected pairs, and selected suited structures.
   `A5s` is labeled as blocker/semi-bluff protection rather than a value
   class. This is a stable baseline mix, not a random runtime decision and not
   a manually inserted AA fixture.
2. A natural BTN limp followed by BB isolation uses the actual integer
   `iso_to` amount from the node key. At 3/5/6BB it can fold, call at a
   price-aware boundary, or make a legal limp-reraise for selected value
   protection. The audit conditions the denominator on the candidate's natural
   limp range rather than presenting all 1,326 hands as a reachable limp event.
3. The BTN-vs-3bet continuous corridor is extended to actual 6–11BB
   `three_bet_to` values. Off-grid values retain `off_grid=true` and use the
   same safe action family as the keyed prices; values outside this bounded
   corridor remain on the legacy safety path.

The candidate remains `model_limited`. No action-EV, opponent-conditioned Q,
match-strength, or upload claim is made.

## Natural 1,326-combination audit

All rows use real ChipZen `GameState` objects, 100BB effective stacks, zero
ante, integer chip amounts, and legal action menus.

### BTN first-in

| Action | Combos |
|---|---:|
| 2.5BB raise | 1,052 |
| limp/call | 46 |
| fold | 228 |
| total | 1,326 |

The 46 natural limp combinations consist of 24 gray playable combinations and
22 stable protection combinations, including one naturally selected AA
combination. `T4o`, `J3o`, `J2o`, `Q2o`, `T2o`, and `T3o` are no longer
automatic limps. Raises use the single integer 250-chip target.

### Natural limp versus actual BB isolation

The rows below reuse exactly those 46 natural BTN limp combinations. Each price
has legal actions, zero fallback rows, and a consistent cash-flow ledger.

| BB actual iso-to | Fold | Call | Legal limp-reraise | Total |
|---:|---:|---:|---:|---:|
| 3.0BB / 300 chips | 0 | 42 | 4 | 46 |
| 5.0BB / 500 chips | 24 | 18 | 4 | 46 |
| 6.0BB / 600 chips | 31 | 11 | 4 | 46 |

The 3BB path contains call/reraise behavior without making weak gray cards
continue automatically; fold behavior appears at the larger 5BB and 6BB
prices. Those larger-price paths are more selective: suited structure and pair
calls are retained only where the price rule permits them, and selected
value/blocker protection uses a legal raise-to target based on the actual
`iso_to`.

This is a finite-range pressure repair, not a completed limp-protection
solution. The 6BB result is 15 continues out of 46 natural limps; an aggressive
or frequently isolating BB could still pressure this baseline more often than
it can prove profitable. The downside of removing the two weaker gray classes
against a passive BB, and the continuation value of every limp branch, remain
unmeasured because no opponent-conditioned Q or action-EV model is available.

## BTN versus 3bet price continuity

For every price inside the bounded 6–11BB corridor, all 1,326 combinations
were legal and had zero fallback rows. The counts remain the v2.2.1 bounded
mix:

| Actual 3bet-to | Fold | Call | Raise | Off-grid rows |
|---:|---:|---:|---:|---:|
| 6.4BB | 872 | 400 | 54 | 1,326 |
| 6.5BB | 872 | 400 | 54 | 1,326 |
| 6.9BB | 872 | 400 | 54 | 1,326 |
| 7.0BB | 872 | 400 | 54 | 0 |
| 8.0BB | 872 | 400 | 54 | 0 |
| 9.0BB | 872 | 400 | 54 | 0 |
| 10.0BB | 872 | 400 | 54 | 0 |
| 10.1BB | 872 | 400 | 54 | 1,326 |
| 10.5BB | 872 | 400 | 54 | 1,326 |
| 10.6BB | 872 | 400 | 54 | 1,326 |
| 11.0BB | 872 | 400 | 54 | 0 |

Representative class behavior is stable across this corridor: `97o` and
`65o` fold, `65s` and `KJo` call, and `AA`/`AKs` raise. The result verifies
route continuity and legality; it does not establish that the mix is optimal.

The two immediate outside-boundary probes use an explicit conservative safety
route rather than the legacy price-defense branch:

| Actual 3bet-to | Fold | Call | Raise | Off-grid rows |
|---:|---:|---:|---:|---:|
| 5.9BB | 1,272 | 0 | 54 | 1,326 |
| 11.1BB | 1,272 | 0 | 54 | 1,326 |

At both probes `97o`, `65o`, and `65s` fold while `AA`/`AKs` retain the legal
2.2× raise-to path. This closes the observed boundary action failure without
claiming that prices outside 6–11BB have a calibrated strategy; the bounded
domain and its conservative outside route remain separate by design.

## Verification receipt

- Full regression: **382 passed, 1 skipped** with `pytest -q
  --import-mode=importlib`.
- Ruff: passed for `submission`, `tests`, and `tools`.
- Natural v2.2.2 audit: 1,326 BTN first-in rows; 46 natural limp rows; 3/5/6BB
  iso rows all legal with zero fallback and consistent ledger status.
- SDK validator: all checks passed; the pre-existing restricted `os` import
  warning remains the only warning.
- Docker build: `linux/amd64` image built as `hu-bot:2.2.2`.
- Wire smoke: passed with `READY`, authentication, request-id echo, and legal
  response.
- Archive verification: passed; 9 layers and Docker load round-trip image id
  matched the manifest. The final manifest is authoritative for exact archive
  bytes and SHA because Docker save metadata can vary between identical image
  builds.
- Restricted local endpoint probe: 1,000/1,000 legal responses at a 50ms
  local deadline; mean 0.631ms, p50 0.370ms, p95 0.563ms, p99 0.894ms,
  max 44.082ms. This is a local observation, not a platform SLA.

The final packaged artifact is recorded separately in the candidate manifest.
It is not uploaded and is not active on the platform. Independent acceptance,
opponent-conditioned value, match strength, platform settlement, and platform
upload remain pending.
