# Cycle 2026-06-12 06:00 — Hermes Self-Improvement Run

## Tasks Picked Up
1. `review_thresholds` — Review MIN_WHALE_SCORE and MIN_SIGNAL_USD thresholds
2. M2 fix from audit_20260611_1500 — Add status IN filter to signal history endpoint

## Task 1: Threshold Review

### Current Thresholds
| Threshold | Value | Purpose |
|-----------|-------|---------|
| `MIN_WHALE_SCORE` | 0.20 | Minimum whale score for signal generation |
| `MIN_SIGNAL_USD_BTC` | $10,000 | Minimum BTC tx value for signal |
| `MIN_SIGNAL_USD_ETH` | $5,000 | Minimum ETH tx value for signal |
| `MIN_SIGNAL_USD_SOL` | $2,000 | Minimum SOL tx value for signal |
| `MIN_SIGNAL_USD_DEFAULT` | $5,000 | Fallback for unknown chains |

### Analysis

#### MIN_WHALE_SCORE = 0.20
- **Current**: 0.20 (20%) — lenient enough for new/cold-start whales
- **Risk**: Too lenient. A wallet with whale_score=0.20 means it barely qualifies as a whale. Signals from these wallets have low reliability.
- **Confidence impact**: `C_final = 0.5 * C_tx + 0.5 * whale_score`. At whale_score=0.20, max C_final = 0.5*1.0 + 0.5*0.20 = 0.60. This means low-whale-score signals are inherently capped at 0.60 confidence.
- **Recommendation**: **Raise to 0.30**. This filters out the weakest whale wallets while still capturing mid-tier whales. At 0.30, max C_final = 0.5*1.0 + 0.5*0.30 = 0.65, still reasonable.
- **Counter-argument**: Raising too high excludes new whales that haven't built track records yet. 0.30 is a good balance.

#### MIN_SIGNAL_USD_BTC = $10,000
- **Current**: $10,000 — filters dust BTC transactions
- **Analysis**: BTC whales typically move $10K+ for meaningful trades. At current BTC prices (~$65K), $10K ≈ 0.15 BTC. This is reasonable.
- **Recommendation**: **Keep** — no change needed. The threshold is well-calibrated.

#### MIN_SIGNAL_USD_ETH = $5,000
- **Current**: $5,000 — filters dust ETH transactions
- **Analysis**: ETH gas costs mean sub-$5K txns are often internal transfers or dust. At ETH ~$3,500, $5K ≈ 1.4 ETH. Reasonable.
- **Recommendation**: **Keep** — no change needed.

#### MIN_SIGNAL_USD_SOL = $2,000
- **Current**: $2,000 — filters dust SOL transactions
- **Analysis**: SOL txns are cheaper, so the floor is lower. $2K filters dust while capturing meaningful SOL whale activity. At SOL ~$170, $2K ≈ 12 SOL. Reasonable.
- **Recommendation**: **Keep** — no change needed.

#### MIN_SIGNAL_USD_DEFAULT = $5,000
- **Current**: $5,000 — fallback for any future chain
- **Analysis**: Sensible default that matches ETH threshold. New chains added in the future will use this unless explicitly configured.
- **Recommendation**: **Keep** — no change needed.

### Threshold Review Verdict
**One change recommended**: Raise `MIN_WHALE_SCORE` from 0.20 to 0.30. This improves signal quality by filtering out the weakest whale wallets while still capturing mid-tier whales with reasonable confidence scores.

### Implementation
Applied in signal_generator.py line 30.

---

## Task 2: Signal History Status Filter (M2 Fix)

### Finding
The `/api/signals/history` endpoint currently filters by `closed_at IS NOT NULL` but does NOT restrict to terminal statuses (`executed`, `failed`, `stale`). This means signals in other states that happen to have a `closed_at` value could leak into the history endpoint.

### Fix
Added `AND cts.status IN ('executed', 'failed', 'stale')` as a mandatory filter condition (not just optional status_filter). This ensures the history endpoint only returns signals in terminal states.

### Change
In `backend/main.py` line 1779, changed:
```python
conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL"]
```
to:
```python
conditions = ["w.user_id = $1", "cts.closed_at IS NOT NULL", "cts.status IN ('executed', 'failed', 'stale')"]
```

---

## Deferred Tasks (require DB access)
- M1: Store confidence_final in DB at signal creation time — needs DB migration
- signal-stats-integration-test-001 — needs DB access
- db-connectivity-infra-002 — investigating DB DNS issue
