.PHONY: test proof boundary check up demo

test:
	python -m pytest -q

proof:
	python -m agentic_memory_cascade.cli

boundary:
	python scripts/check_boundary.py

check: test proof boundary

up:
	python -m uvicorn agentic_memory_cascade.service:app --host 127.0.0.1 --port 8090

demo:
	./demo.sh
