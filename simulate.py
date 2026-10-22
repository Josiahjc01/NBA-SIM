#!/usr/bin/env python3
"""Thin wrapper: python simulate.py --home SAC --away LAC --n 5000"""

from nba_sim.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
