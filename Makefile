.PHONY: setup jars test lint phase0 run-frontier-oracles reproduce clean

setup:
	uv sync --python 3.12
	uv run python vendor/fetch_jars.py

jars:
	uv run python vendor/fetch_jars.py

lint:
	uv run ruff check src/ tests/

test:
	uv run pytest -q

phase0:
	uv run clasp-ont phase0-smoke

# Human-launched terminal target for zero-API-spend commercial CLI oracle sweep (3s pacing, temp-dir isolated)
run-frontier-oracles:
	uv run clasp-ont run-frontier-oracles --human-terminal-confirm

reproduce:
	uv run snakemake reproduce_from_cache --cores 4 --forceall

clean:
	rm -rf data/toy data/interim .snakemake
