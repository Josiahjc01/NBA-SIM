# Grok Bot — run NBA Sim (live 2026-27)

This package defaults to **live 2026-27** priors from `stats.nba.com` via `nba_api`, **blended for the beginning of the season**: last-season (2025-26) rates + current **preseason** + growing regular S2D, with **rookie expected production** (by position) mixed with preseason for first-year players. Do **not** use `--fixtures` unless you only need an offline smoke test.

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

## Doctor (recommended once per session)

```bash
source .venv/bin/activate
python -m nba_sim doctor
# or: python -m nba_sim --check-data
```

If required feeds fail: raise timeouts/retries, then retry.

```bash
export NBA_SIM_TIMEOUT=120 NBA_SIM_RETRIES=6 NBA_SIM_PAUSE=0.7
python -m nba_sim doctor
```

## Run a matchup (live 2026-27)

Works for **any of the 30 NBA teams**. One command pulls everything needed:

```bash
source .venv/bin/activate
python -m nba_sim --home BOS --away NYK --n 5000
# After upgrades / new cache schema:
python -m nba_sim --home DEN --away MIN --n 5000 --no-cache --seed 42
```

Other examples:

```bash
python -m nba_sim --home Celtics --away Knicks --n 5000
python -m nba_sim --home OKC --away DEN --date 2027-05-12 --n 5000   # Playoffs boards
python -m nba_sim --home MIL --away PHI --spread -3.5 --total 224.5 --n 5000
python simulate.py --home GSW --away LAL --n 5000 --season 2026-27
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
- `fetching last season 2025-26 priors ...` and `fetching 2026-27 Pre Season ...`
- `prior: ...` provenance lines with blend weights / rookies
- Team Win% + avg scores + player tables (or slate compact table)

If live fetch fails, the CLI **errors** (no silent fixture swap). Fix network / retry. Only then consider `--fixtures` for debugging the sim engine itself.

## Smoke tests (offline)

```bash
PYTHONPATH=. python3 -m unittest tests.test_fallback_and_slate -v
python -m nba_sim --home BOS --away NYK --fixtures --n 50   # still loads bundled smoke JSON
python -m nba_sim --slate-date 2026-10-22 --fixtures --n 20
```

## Notes

- `--date YYYY-MM-DD` / slate date → priors through **D−1** only (pre-tip; no that-game box score).
- Slate “today” = **US/Eastern** calendar date.
- Early-season blend: last season + preseason + rookie archetypes; shrinks toward regular S2D with GP. If still &lt;5 usable → whole-team last-season fill. Documented in output.
- Optional feeds that die print `feed:` warnings and continue; required feeds fail loudly.
- Default season constant: `2026-27` (`nba_sim.season.CURRENT_SEASON`).
- Cache: `.cache/` + `.cache/endpoints/` (safe to delete; TTL ~6h). `--no-cache` forces fresh.
- Env: `NBA_SIM_TIMEOUT`, `NBA_SIM_RETRIES`, `NBA_SIM_PAUSE`, `NBA_SIM_CACHE_TTL`, `NBA_SIM_MAX_WORKERS`.
- Repo: https://github.com/Josiahjc01/NBA-SIM
