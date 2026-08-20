#!/usr/bin/env bash
# Deploy federated cascade to infra01 (Oberon cluster)
#
# Run this ON infra01:
#   chmod +x deploy/deploy-infra01.sh
#   ./deploy/deploy-infra01.sh
#
# Prerequisites:
#   - oc logged in as cluster-admin
#   - podman available
#   - CASCADE_LLM_URL and CASCADE_LLM_KEY set (or edit below)

set -euo pipefail

REPO_URL="https://github.com/jkershawrh/cascade-compression.git"
WORK_DIR="/tmp/cascade-compression-deploy"
REGISTRY=$(oc registry info 2>/dev/null | head -1)
NAMESPACE="cascade-compression"
IMAGE="${REGISTRY}/${NAMESPACE}/cascade-compression:latest"

LLM_URL="${CASCADE_LLM_URL:-}"
LLM_KEY="${CASCADE_LLM_KEY:-}"

echo "=== Cascade as Memory — Federated Deploy ==="
echo "Registry: ${REGISTRY}"
echo "Namespace: ${NAMESPACE}"
echo "Image: ${IMAGE}"
echo ""

# ── Step 1: Clone / pull latest ─────────────────────────────────────
if [ -d "${WORK_DIR}" ]; then
    echo "Updating existing clone..."
    cd "${WORK_DIR}"
    git pull --ff-only origin main
else
    echo "Cloning repo..."
    git clone "${REPO_URL}" "${WORK_DIR}"
    cd "${WORK_DIR}"
fi
echo ""

# ── Step 2: Run tests ───────────────────────────────────────────────
echo "Running tests..."
python3 -m pytest tests/ --tb=short -q
echo ""

# ── Step 3: Build container image ───────────────────────────────────
echo "Building container image (linux/amd64)..."
podman build -f Containerfile -t "${IMAGE}" --platform linux/amd64 .
echo ""

# ── Step 4: Create namespace ────────────────────────────────────────
echo "Creating namespace..."
oc create namespace "${NAMESPACE}" 2>/dev/null || echo "  (namespace already exists)"
echo ""

# ── Step 5: Push image to internal registry ─────────────────────────
echo "Logging into internal registry..."
podman login "${REGISTRY}" -u "$(oc whoami)" -p "$(oc whoami -t)" --tls-verify=false

echo "Pushing image..."
podman push "${IMAGE}" --tls-verify=false
echo ""

# ── Step 6: Create LLM secret ──────────────────────────────────────
if [ -n "${LLM_URL}" ] && [ -n "${LLM_KEY}" ]; then
    echo "Creating LLM secret..."
    oc create secret generic cascade-llm \
        --from-literal=url="${LLM_URL}" \
        --from-literal=key="${LLM_KEY}" \
        -n "${NAMESPACE}" \
        --dry-run=client -o yaml | oc apply -f -
else
    echo "WARNING: CASCADE_LLM_URL and CASCADE_LLM_KEY not set."
    echo "  Create the secret manually:"
    echo "  oc create secret generic cascade-llm \\"
    echo "    --from-literal=url=https://your-llm/v1 \\"
    echo "    --from-literal=key=sk-... \\"
    echo "    -n ${NAMESPACE}"
fi
echo ""

# ── Step 7: Update image reference in manifest ─────────────────────
echo "Updating image reference in manifest..."
INTERNAL_IMAGE="image-registry.openshift-image-registry.svc:5000/${NAMESPACE}/cascade-compression:latest"
sed "s|quay.io/redhat-ai-incubation/cascade-compression:latest|${INTERNAL_IMAGE}|g" \
    deploy/openshift-federated.yaml > /tmp/cascade-federated-resolved.yaml
echo ""

# ── Step 8: Apply manifests ─────────────────────────────────────────
echo "Applying federated deployment..."
oc apply -f /tmp/cascade-federated-resolved.yaml
echo ""

# ── Step 9: Wait for rollout ────────────────────────────────────────
echo "Waiting for pods..."
for deploy in cascade-k8s cascade-aap cascade-memory; do
    echo "  Waiting for ${deploy}..."
    oc rollout status deployment/${deploy} -n "${NAMESPACE}" --timeout=120s || true
done
echo ""

# ── Step 10: Verify ─────────────────────────────────────────────────
echo "=== Deployment Status ==="
oc get pods -n "${NAMESPACE}"
echo ""

echo "=== Health Checks ==="
for svc in cascade-k8s cascade-aap cascade-memory; do
    URL=$(oc get route ${svc} -n "${NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
    if [ -n "${URL}" ]; then
        echo "${svc}: https://${URL}/health"
        curl -sk "https://${URL}/health" 2>/dev/null || echo "  (not ready yet)"
    else
        echo "${svc}: (no route — internal only)"
    fi
done
echo ""

echo "=== Memory Aggregator ==="
MEM_URL=$(oc get route cascade-memory -n "${NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
if [ -n "${MEM_URL}" ]; then
    echo "Dashboard: https://${MEM_URL}/"
    echo "Memory stats: https://${MEM_URL}/memories/stats"
    echo "Recall: POST https://${MEM_URL}/recall"
fi
echo ""

echo "=== Done ==="
echo "Federation CronJob runs every 5 minutes."
echo "Monitor: oc get cronjob cascade-federate -n ${NAMESPACE}"
echo "Logs:    oc logs -l app=cascade-federate -n ${NAMESPACE} --tail=50"
