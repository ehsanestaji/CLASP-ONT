.PHONY: setup jars test lint phase0 phase1-public run-frontier-oracles certify-from-cache reproduce clean

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

phase1-public:
	uv run clasp-ont fetch-suite --suite OAEI_BioML_2026
	uv run clasp-ont fetch-suite --suite Plant_Biodiv_Hub
	uv run clasp-ont fetch-suite --suite Disease_Phenotype_Hub
	uv run clasp-ont run-public-suites

# Human-launched terminal target for zero-API-spend commercial CLI oracle sweep (3s pacing, temp-dir isolated)
run-frontier-oracles:
	uv run clasp-ont run-frontier-oracles --human-terminal-confirm

certify-from-cache:
	uv run clasp-ont reproduce-from-cache

reproduce:
	uv run snakemake reproduce_from_cache --cores 4 --forceall

clean:
	rm -rf data/toy data/interim .snakemake
