#!/usr/bin/env bash
set -e

# Agentic Memory Cascade — One-command demo
# Starts the service, opens the dashboard, and replays synthetic signals
# so you can watch memory form in real time.
#
# Usage:
#   ./demo.sh                   # FSI domain, normal speed
#   ./demo.sh healthcare fast   # Healthcare domain, fast replay
#   ./demo.sh telecom slow      # Telecom domain, slow replay

DOMAIN="${1:-fsi}"
# Accept the human-friendly domain names the README uses.
case "$DOMAIN" in
  finance|financial|fsi) DOMAIN=fsi ;;
  health|healthcare)     DOMAIN=healthcare ;;
esac
SPEED="${2:-normal}"
MODEL="${CASCADE_LLM_MODEL:-granite3.2:8b-instruct}"
# The service writes learned state back to CASCADE_STATE_FILE. Point it at a
# scratch copy so a demo run never overwrites the tracked seed state.
DEMO_STATE="$(mktemp -t cascade-demo-state)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$SPEED" in
  fast) DELAY=0.1 ;;
  slow) DELAY=2.0 ;;
  *) DELAY=0.5 ;;
esac

cleanup() {
  echo ""
  echo "Shutting down..."
  [ -n "$SERVICE_PID" ] && kill "$SERVICE_PID" 2>/dev/null || true
  [ -n "$UI_PID" ] && kill "$UI_PID" 2>/dev/null || true
  [ -n "$DEMO_STATE" ] && rm -f "$DEMO_STATE" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "========================================"
echo "  Agentic Memory Cascade — Live Demo"
echo "========================================"
echo ""
echo "  Domain: $DOMAIN"
echo "  Speed:  $SPEED ($DELAY s between signals)"
echo "  Model:  $MODEL (via Ollama)"
echo ""

# -- Check Ollama --
if ! command -v ollama &>/dev/null; then
  echo "ERROR: Ollama is required for the demo."
  echo "  Install: https://ollama.com"
  echo "  Then:    ollama pull $MODEL"
  exit 1
fi

# -- Pull the model if needed --
if ! ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
  echo "Pulling model $MODEL (this takes a few minutes on first run)..."
  ollama pull "$MODEL"
fi

# -- Start Ollama if not running --
if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
  echo "Starting Ollama..."
  ollama serve &>/dev/null &
  sleep 2
fi

echo "Model ready: $MODEL"
echo ""

# -- Start the FastAPI service (with pre-learned seed state) --
# Work from a scratch copy: the service persists learned state back to this
# path, and the tracked seed must stay pristine so every run starts the same.
cp "$SCRIPT_DIR/data/demo-seed-state.json" "$DEMO_STATE"
echo "Starting cascade service (with seed state — 3 pre-learned agents)..."
CASCADE_DOMAIN="$DOMAIN" \
  CASCADE_LLM_URL="http://localhost:11434" \
  CASCADE_LLM_KEY="ollama" \
  CASCADE_LLM_MODEL="$MODEL" \
  CASCADE_LLM_BATCH="10" \
  CASCADE_STATE_FILE="$DEMO_STATE" \
  python3 -m uvicorn cascade_compression.service:app \
  --port 8090 --log-level warning &
SERVICE_PID=$!

echo -n "Waiting for service"
for i in $(seq 1 30); do
  if curl -sf http://localhost:8090/health >/dev/null 2>&1; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 1
done

if ! curl -sf http://localhost:8090/health >/dev/null 2>&1; then
  echo " FAILED. Check logs."
  kill "$SERVICE_PID" 2>/dev/null || true
  exit 1
fi

# -- Start the Gradio dashboard --
echo "Starting dashboard..."
SCORER_URL=http://localhost:8090 python3 "$SCRIPT_DIR/src/ui.py" &
UI_PID=$!
sleep 3

echo ""
echo "  Dashboard: http://localhost:7860"
echo "  API:       http://localhost:8090/stats"
echo ""

# -- Find the corpus --
CORPUS="$SCRIPT_DIR/benchmarks/corpora/${DOMAIN}-100.json"
if [ ! -f "$CORPUS" ]; then
  CORPUS="$SCRIPT_DIR/benchmarks/corpora/fsi-100.json"
  echo "  Note: No corpus for '$DOMAIN', using FSI"
fi

echo "Replaying signals from $(basename "$CORPUS")..."
echo "Watch the dashboard to see memory form."
echo ""
echo "------+----------+------------+-----+--------------------------------------------"
printf " %4s | %8s | %10s | %3s | %s\n" "#" "SEVERITY" "VERDICT" "CMP" "SIGNAL"
echo "------+----------+------------+-----+--------------------------------------------"

# -- Replay signals through the cascade API --
python3 << PYEOF
import json, random, sys, time
import httpx

corpus = json.load(open("$CORPUS"))
delay = $DELAY
url = "http://localhost:8090/cascade"

# Signal types the seed state has already learned to treat as routine. These
# arrive at "low" severity: suppressible by a learned agent, but NOT dropped
# wholesale by the severity gate (which only drops "info"). Everything else —
# fraud scoring, incidents — arrives high and must survive. That contrast is
# the demo: watch the learned types compress while the important ones don't.
NOISE_TYPES = {
    "dispute-classification", "compliance-screening", "loan-document-extraction",
    "campaign-performance", "inventory-sync", "network-telemetry",
    "claims-intake", "vitals-monitoring",
}

# The dominant-noise suppressor keys on (signal_type, source, namespace) and
# fires on repeats within its window. A corpus that gives every record a unique
# id therefore never repeats and never compresses — bucket into a small set of
# sources so the same key recurs, the way real signals from a finite set of
# accounts or hosts do.
SOURCE_BUCKETS = 5


def _severity(task):
    return "low" if task in NOISE_TYPES else "high"


# Flatten corpus: tasks -> list of signals
signals = []
if "tasks" in corpus:
    for task_name, task_signals in corpus["tasks"].items():
        for i, s in enumerate(task_signals):
            task = s.get("task", task_name)
            signals.append({
                "signal_type": task,
                "severity": s.get("severity") or _severity(task),
                "source": f"account-{i % SOURCE_BUCKETS}",
                "content": {"message": s.get("text", "")},
                "labels": {"domain": corpus.get("industry", "$DOMAIN")},
            })
else:
    for i, s in enumerate(corpus):
        task = s.get("signal_type", s.get("task", ""))
        signals.append({
            "signal_type": task,
            "severity": s.get("severity") or _severity(task),
            "source": s.get("source") or f"account-{i % SOURCE_BUCKETS}",
            "content": {"message": s.get("text", s.get("message", ""))},
            "labels": s.get("labels", {}),
        })

# Interleave the types so compression climbs as agents see repeats, rather
# than stair-stepping one task at a time.
random.seed(7)
random.shuffle(signals)

total_compressed = 0

for i, signal in enumerate(signals):
    try:
        r = httpx.post(url, json={"signals": [signal]}, timeout=10)
        data = r.json()
        compressed = data.get("compressed", 0)
        # The per-request ratio is 0 or 1 for a single signal — show the running
        # total instead, so the column tracks the dashboard's compression gauge.
        total_compressed += compressed
        ratio = total_compressed / (i + 1)
        verdict = "COMPRESSED" if compressed else "SURVIVED"
        severity = signal.get("severity", "info")
        text = signal.get("content", {}).get("message", "")[:55]
        pct = f"{ratio:.0%}"
        print(f" {i+1:4d} | {severity:>8} | {verdict:>10} | {pct:>3} | {text}")
    except Exception as e:
        print(f" {i+1:4d} | {'ERR':>8} | {'---':>10} | --- | {str(e)[:55]}", file=sys.stderr)
    time.sleep(delay)

print()
print(f"Replay complete — {total_compressed}/{len(signals)} signals compressed ({total_compressed/max(1,len(signals)):.0%}).")
print("Dashboard still running at http://localhost:7860")
print("Press Ctrl+C to stop.")
PYEOF

# Keep running until Ctrl+C
wait "$UI_PID" 2>/dev/null
