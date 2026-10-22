# Sample run — LAC @ SAC

**Command**

```bash
cd sim
PYTHONPATH=. python3 -m nba_sim --home SAC --away LAC --n 5000 --seed 42 --fixtures
```

**Data note:** Live `stats.nba.com` timed out from this Cloud Agent environment. The **default code path is live 2026-27** (no silent fixture swap). This captured table used `--fixtures` only to exercise the engine offline. On Grok Bot / any host with NBA Stats access, omit `--fixtures` — see `GROK_RUN.md` and `nba-sim-grok.zip`.

**Captured output** (N=5000, seed=42):

```
Loading priors for LAC @ SAC (season 2026-27, latest) ...
USING FIXTURE DATA (bundled sample — not live NBA Stats)
Running 5000 sims (SAC pace=100.2, LAC pace=97.8) ...
================================================================
NBA Sim  |  LAC @ SAC  |  N=5000
Season 2024-25  |  data=fixture  |  game_date=(latest)  |  priors_through=2025-04-01
================================================================

Team results
----------------------------------------------------------------
Team       AvgPts     Std     Win%
LAC (away)    105.6    12.0    36.4%
SAC (home)    111.1    12.1    63.6%
Win% check: home 63.6% + away 36.4% + tie 0.0% = 100.0%
OT rate: 2.4% of sims (regulation ties go to OT; pushes≈0.0%)

LAC — avg player box (PTS / REB / AST)
----------------------------------------------------------------
Player                      MIN    PTS    REB    AST
Kawhi Leonard              31.7   20.5    6.8    3.4
Paul George                33.0   19.6    6.7    4.0
James Harden               33.7   17.2    7.2    8.6
Norman Powell              29.3   15.3    3.2    1.8
Ivica Zubac                29.8   12.4   12.1    1.5
...

SAC — avg player box (PTS / REB / AST)
----------------------------------------------------------------
Player                      MIN    PTS    REB    AST
De'Aaron Fox               34.7   22.6    5.7    6.6
DeMar DeRozan              33.0   18.7    4.9    4.5
Domantas Sabonis           34.1   17.5   16.2    7.1
Keegan Murray              32.5   14.5    6.6    1.8
Malik Monk                 27.6   13.7    3.4    4.7
...

Consistency
----------------------------------------------------------------
Σ player PTS vs team avg — LAC: 105.6 vs 105.6  |  SAC: 111.1 vs 111.1
Audit sample: max |ΣplayerPTS−team|=0.00  mean team MIN=240.0  negatives=0
================================================================
```

## Summary

| | Avg score | Win% |
|--|-----------|------|
| **SAC (home)** | **111.1** (±12.1) | **63.6%** |
| **LAC (away)** | **105.6** (±12.0) | **36.4%** |
| Ties / pushes | — | **0.0%** (OT model) |

**Top scorers (avg PTS):** Fox 22.6, Kawhi 20.5, PG 19.6, DeRozan 18.7, Harden 17.2, Sabonis 17.5.
