.PHONY: test smoke install lint-ish

install:
	python3 -m pip install -r requirements.txt

test:
	PYTHONPATH=. python3 -m unittest discover -s tests -v

smoke:
	PYTHONPATH=. python3 -m nba_sim --home SAC --away LAC --fixtures --n 50 --seed 1 --quiet-priors
	PYTHONPATH=. python3 -m nba_sim --slate-date 2026-10-22 --fixtures --n 20 --seed 1 --json >/dev/null

lint-ish:
	python3 -m compileall -q nba_sim tests
