.PHONY: test test-memory test-cascade test-routing test-contracts test-all audit-claims up demo

## ── Memory tests ───────────────────────────────────────────────────
test-memory:
	python -m pytest tests/test_memory.py tests/test_memory_contracts.py tests/test_recall.py tests/test_consolidation.py tests/test_priming.py tests/test_federation.py -v

## ── Cascade engine tests ────────────────────────────────────────────
test-cascade:
	python -m pytest tests/test_cascade.py tests/test_cascade_safety.py tests/test_promotion.py -v

## ── Routing tests ───────────────────────────────────────────────────
test-routing:
	python -m pytest tests/test_corpora.py tests/test_strategy_router.py tests/test_bootstrapper.py tests/test_task_mapping.py tests/test_synthetic_routing.py -v

## ── Contract tests ──────────────────────────────────────────────────
test-contracts:
	python -m pytest tests/test_contracts.py -v

## ── All tests ───────────────────────────────────────────────────────
test-all:
	python -m pytest tests/ -v

test: test-all

## ── Claim provenance ────────────────────────────────────────────────
## Prints every claim in tests/claim_registry.yaml that has no source,
## so unbacked numbers can't quietly accumulate in the README.
audit-claims:
	@python3 -c "import yaml,sys; \
	claims=yaml.safe_load(open('tests/claim_registry.yaml'))['claims']; \
	un=[c for c in claims if not c.get('verified')]; \
	[print(f\"  UNVERIFIED  {c['id']}: {c['value']}\") for c in un]; \
	print(f\"\n{len(claims)-len(un)}/{len(claims)} claims verified\"); \
	sys.exit(0)"

## ── Run the app ─────────────────────────────────────────────────────
up:
	python -m uvicorn cascade_compression.service:app --host 0.0.0.0 --port 8090 --reload

## ── Run the demo ────────────────────────────────────────────────────
demo:
	./demo.sh
