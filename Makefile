.PHONY: run console test

run:        ## strcht on :8790
	./run.sh

console:    ## the translation console on :8788, with strcht as its beacon
	./scripts/console.sh

test:
	.venv/bin/pip install -q -r requirements-dev.txt
	.venv/bin/python -m pytest -q
