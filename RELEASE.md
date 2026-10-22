# NBA-SIM 1.0.2

Live-run hotfix on 1.0.1: real Top 5 market edges, `--date` hang fix, drop ESPN core injuries 404. Tag: `v1.0.2`.

## Install / run

```bash
pip install -e .
python -m nba_sim doctor
python -m nba_sim --home POR --away SAC --date 2026-10-23 --n 2000 --seed 42
# Optional live props / richer ML:
export ODDS_API_KEY=your_key   # or THE_ODDS_API_KEY
```

## Hotfix notes

- **Odds:** ESPN scoreboard always; The Odds API when env key set. Top 5 edges use de-vigged two-way implied probs.
- **Date path:** `prior_cutoff` = min(tip−1, Eastern yesterday). Inactives only near tip.
- **Injuries:** ESPN site + team + scoreboard (core dropped).

Prior tags: `v1.0.1` (CI), `v1.0.0` (feature freeze).

# NBA-SIM 1.0.1

Patch on firm 1.0.0: green CI (`pytest` in Actions). Tag: `v1.0.1` (preferred). Prior tag `v1.0.0` remains at the feature freeze SHA.

# NBA-SIM 1.0.0

Firm first major release. Tag: `v1.0.0`.

## Install / run

```bash
pip install -e .
python -m nba_sim doctor
python -m nba_sim --home BOS --away NYK --n 5000 --seed 42
python -m nba_sim --home OKC --away DEN --date 2027-05-12 --n 5000   # Playoffs
python -m nba_sim --home MIL --away PHI --date 2027-04-15 --n 5000   # Play-In
python -m nba_sim --slate --n 2000
```

After upgrading from ≤0.6, run once with `--no-cache` (schema **`blendv10`** from 0.7; still current at 1.0.0).

## Doctor

`python -m nba_sim doctor` (or `--check-data`) probes required NBA Stats boards plus optional ESPN injuries/odds, PtDefend, lineups, clutch, on/off, refs. Required feeds hard-fail; optional feeds warn and degrade. NBA.com CDN injury/liveData may 403 — best-effort multi-source path remains (waiver below).

## Anti-leak (v1 bar)

- Tip-game box/final never used as model input.
- Dated tips: DateTo = tip−1 as `MM/DD/YYYY` for core **and** enrichment (**C1**), clamped to Eastern yesterday for future tips.
- Undated runs: priors cut at Eastern **yesterday** (**H1**).
- Play-In Apr 11–18 → PlayIn regime; Playoffs from Apr 19 (**H3**).
- Defense: DRtg primary; secondary stack damped ±2% (**H2**).

## Top 5 wagers

Every single-game and slate text/JSON report ends with **Top 5 wagers** (`build_top_wagers` / `format_top_wagers_block`). Edges vs de-vigged market when priced; **no market** otherwise. Advisory only — not financial advice.

## Deferred / waived (not blocking 1.0)

| Item | Reason |
|------|--------|
| M1 market→pace calibration | Light nudge kept; cover/over always reported; not tip-box |
| M2 dual HCA (FG + mild TOV) | Documented ~2.5–3pt budget; FG primary |
| M3 SoS/lineup ORtg half unused | DRtg half intentional; ORtg already in player rates |
| M4 undated injury/odds cache | Best-effort; skip Final tip boxes where status known |
| Guaranteed NBA.com CDN | Third-party 403 → ESPN + BoxScoreSummary inactive best-effort |
| Perfect market-beating calibration | Out of scope |
| Live mid-game state product | Out of scope |

## Audit

`docs/realism-audit-current.md` + store `internal/audit-findings.json`. Critical open = 0; High open = 0 after #4 (`e40993e`) + this tag.
