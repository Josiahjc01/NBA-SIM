# Grok Bot — run NBA Sim (live 2026-27)

This package defaults to **live 2026-27** season-to-date rosters and stats from `stats.nba.com` via `nba_api`, with **last-season (2025-26) fallback** when a team has fewer than 5 usable current-season players. Do **not** use `--fixtures` unless you only need an offline smoke test.

## What Grok needs

1. **Python 3.10+**
2. **Outbound HTTPS** to `stats.nba.com` (and CDN as used by `nba_api`); ESPN host if using `--injuries`
3. Install deps from `requirements.txt` at the repo root (or `sim/requirements.txt` in the zip layout)

## Install

```bash
# From GitHub clone
git clone https://github.com/Josiahjc01/NBA-SIM.git
cd NBA-SIM
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run a matchup (live 2026-27)

```bash
source .venv/bin/activate
python -m nba_sim --home SAC --away LAC --n 5000
```

Other examples:

```bash
python -m nba_sim --home Kings --away Clippers --n 5000
python -m nba_sim --home SAC --away LAC --date 2026-10-22 --n 5000
python -m nba_sim --home SAC --away LAC --spread -3.5 --total 224.5 --n 5000
python simulate.py --home SAC --away LAC --n 5000 --season 2026-27
```

## Full-day slate (US/Eastern)

```bash
# Today Eastern
python -m nba_sim --slate --n 2000

# Specific Eastern calendar date
python -m nba_sim --slate-date 2026-10-22 --n 2000

# Optional lines CSV: away,home,spread,total
python -m nba_sim --slate-date 2026-10-22 --lines-csv lines.csv --n 2000
```

Expected log lines on success:

- `Loading LIVE priors ... (season 2026-27, ...)`
- `fetching live NBA Stats for season 2026-27 ...`
- `fetching fallback season 2025-26 priors ...` (when fallback enabled)
- `prior: ...` provenance lines
- Team Win% + avg scores + player tables (or slate compact table)

If live fetch fails, the CLI **errors** (no silent fixture swap). Fix network / retry. Only then consider `--fixtures` for debugging the sim engine itself.

## Smoke tests (offline)

```bash
PYTHONPATH=. python3 -m unittest tests.test_fallback_and_slate -v
python -m nba_sim --home SAC --away LAC --fixtures --n 50
python -m nba_sim --slate-date 2026-10-22 --fixtures --n 20
```

## Notes

- `--date YYYY-MM-DD` / slate date → priors through **D−1** only (pre-tip; no that-game box score).
- Slate “today” = **US/Eastern** calendar date.
- Fallback threshold: ≥5 usable players (`GP≥1`, `MIN≥1`); else fill from previous season. Documented in output.
- Default season constant: `2026-27` (`nba_sim.season.CURRENT_SEASON`).
- Cache: `.cache/` (safe to delete; TTL ~6h).
- Repo: https://github.com/Josiahjc01/NBA-SIM
