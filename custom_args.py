"""Custom starting constants for `dispatch.py --custom`.

When `dispatch.py` is run WITHOUT `--custom`, this file is ignored completely.
When `dispatch.py` is run WITH `--custom` (e.g. `python3 dispatch.py --level 8 --custom`),
`apply_custom_constants()` loads any constants defined here.

Note: Per-level constants (`PRICE_MULTIPLIER`, `SLACK_BUFFER`, `OPENING_FEE`, `MAX_FEE_MULTIPLE`)
accept either:
  - a per-level dict (e.g. `{8: 1.5}` or `{1: 1.0, ..., 8: 1.3}`), OR
  - a single number from a UI slider (e.g. `1.5`), applied across all levels.
"""

# Part A — Quoting & Order Selection
PRICE_MULTIPLIER = {1: 1.2, 2: 1.2, 3: 1.2, 4: 1.5, 5: 1.5, 6: 1.5, 7: 2.0, 8: 2.35}
SLACK_BUFFER = {1: 30, 2: 30, 3: 30, 4: 60, 5: 60, 6: 60, 7: 90, 8: 120}
MAX_QUOTE_MULTIPLIER = 1.5
PRICE_WAR_MULTIPLIER = 1.15
MAX_DEALER_TRUST_PCT = 25
TARGET_ONTIME_SHARE = 0.9

# Part B — Dispatch & Driver Fees
OPENING_FEE = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.4, 8: 1.3}
MAX_FEE_MULTIPLE = {7: 2.0, 8: 2.2}
DEFAULT_MAX_FEE_MULTIPLE = 2.5
DEADLINE_EARLY_MINUTES = 420
QUICK_BOOK_MINUTES = 60
STEEP_OPENING_MULTIPLE = 1.5
LOW_BATTERY_PERCENT = 30
