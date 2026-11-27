# v2.1.0 first-slice code receipt

This receipt freezes the code state used for the first independent-test handoff.
It covers the listed semantic/control-flow files and their current local
deadline wrapper. The terminal oracle is present in the candidate but is not
part of the preflop audit claim.

## Candidate and audit

- Candidate: `.work/hu-v2-strength-v2.1.0`
- Version: `2.1.0`
- Audit: `reports/v2-preflop-169-audit-v2.1.0.json`
- Coverage: 4,732 real `chipzen.GameState` rows; 169 classes; BTN first-in,
  BB versus 2.00/2.25/2.60BB open, BTN versus 6/7/8BB 3-bet; 100/60/20/8BB.
- Result: 4,732/4,732 legal; 0 fallback; BTN first-in 676/676
  `PREFLOP_BTN_FIRST_IN`, 0 price-defense routes; non-short BB-open
  ordinary call-masked raise candidates 0.
- Baseline comparison: frozen v2.0.0 had 676/676 BTN first-in nodes marked
  `PREFLOP_BTN_LIMP_OPTION`, 507 price-defense routes, and 270 ordinary BB-open
  call-masked raise candidates (the latter includes the audit's non-short
  target set).

## SHA-256

| File | SHA-256 |
|---|---|
| `submission/hu_bot/v2_contracts.py` | `26C83F55A0139ABF5C213528F6796A5D64C6855EDEE8CA186FFC3A499B1B6E39` |
| `submission/hu_bot/v2_policy.py` | `A08F6110E74A7B278479ACE48203C23A94F2C11E3B6FD41B18441E0946F9A091` |
| `submission/hu_bot/v2_runtime.py` | `D4A463DFC610EDA4AC4EE9BA482DB3F7E08C1E62BF40622F68798D2FDDDAE1A6` |
| `submission/hu_bot/__init__.py` | `F9C36E57443FD875B8AB818905FE947C56EDB35661F24392318BDC9C2CC97D69` |
| `tools/v2_preflop_169_audit.py` | `C8316DFA20ADF08934F84F746B4D312142F0CA2198A6DE2054B3CD5290A231C5` |
| `reports/v2-preflop-169-audit-v2.1.0.json` | `987A1A40DBD81BB39C25746E5D0B8EA074AEA38B3E83510BC69EBAA34905688D` |

This is a developer handoff receipt, not an independent correctness, action
value, Match-strength, package, or platform-upload claim.
