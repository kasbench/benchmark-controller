#!/usr/bin/env bash
# =============================================================================
# Flannel CNI Installation Script
# Run on the CONTROL PLANE node after: kubeadm init --pod-network-cidr=10.244.0.0/16
#
# Flannel version: v0.28.4 (latest as of May 2026)
# Kubernetes:      1.36.x
# =============================================================================
# Usage:
#   bash flannel-install.sh [--cidr <CIDR>] [--iface <interface>] [--dry-run]
#
# Options:
#   --cidr   <CIDR>        Pod network CIDR (default: 10.244.0.0/16)
#                          Must match what was passed to kubeadm init
#   --iface  <interface>   Network interface Flannel should bind to
#                          (optional; useful for multi-NIC nodes)
#   --dry-run              Print what would be applied without applying it
# =============================================================================

set -euo pipefail

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}══════════════════════════════════════════════${RESET}"; \
            echo -e "${BOLD}  $*${RESET}"; \
            echo -e "${BOLD}══════════════════════════════════════════════${RESET}"; }

# ── Defaults ────────────────────────────────────────────────────────────────
FLANNEL_VERSION="v0.28.4"
FLANNEL_MANIFEST_URL="https://github.com/flannel-io/flannel/releases/download/${FLANNEL_VERSION}/kube-flannel.yml"
POD_CIDR="10.244.0.0/16"
IFACE=""
DRY_RUN=false
MANIFEST_FILE="/tmp/kube-flannel.yml"

# ── Argument parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cidr)    POD_CIDR="$2";  shift 2 ;;
    --iface)   IFACE="$2";    shift 2 ;;
    --dry-run) DRY_RUN=true;  shift   ;;
    -h|--help)
      sed -n '/^# Usage/,/^# =====/p' "$0" | grep -v '^# =====' | sed 's/^# \?//'
      exit 0 ;;
    *) error "Unknown option: $1" ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────────
section "1 — Pre-flight checks"
# ─────────────────────────────────────────────────────────────────────────────

# kubectl must be available and pointing at a reachable cluster
if ! command -v kubectl &>/dev/null; then
  error "kubectl not found. Install it or run this script on the control plane node."
fi

info "Testing cluster connectivity…"
if ! kubectl cluster-info &>/dev/null; then
  error "Cannot reach the cluster API server. Ensure KUBECONFIG is set correctly and the control plane is running."
fi
success "Cluster is reachable."

# Confirm control plane node is present
CP_NODE=$(kubectl get nodes --no-headers \
  -l node-role.kubernetes.io/control-plane 2>/dev/null | awk '{print $1}' | head -1)
[[ -n "${CP_NODE}" ]] || \
  CP_NODE=$(kubectl get nodes --no-headers \
    -l node-role.kubernetes.io/master 2>/dev/null | awk '{print $1}' | head -1)

[[ -n "${CP_NODE}" ]] || error "No control-plane node found. Is kubeadm init complete?"
info "Control-plane node: ${CP_NODE}"

# Check that no CNI is already installed (flannel namespace should not exist)
if kubectl get namespace kube-flannel &>/dev/null; then
  warn "Namespace 'kube-flannel' already exists — Flannel may already be installed."
  read -r -p "Continue anyway? [y/N] " CONFIRM
  [[ "${CONFIRM,,}" == "y" ]] || { info "Aborting."; exit 0; }
fi

# Validate CIDR format (basic)
if ! echo "${POD_CIDR}" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$'; then
  error "Invalid CIDR format: ${POD_CIDR}"
fi

# Cross-check CIDR against what kubeadm actually configured
CLUSTER_CIDR=$(kubectl get cm kubeadm-config -n kube-system -o jsonpath='{.data.ClusterConfiguration}' 2>/dev/null \
  | grep podSubnet | awk '{print $2}' || true)

if [[ -n "${CLUSTER_CIDR}" && "${CLUSTER_CIDR}" != "${POD_CIDR}" ]]; then
  warn "Mismatch detected!"
  warn "  kubeadm podSubnet : ${CLUSTER_CIDR}"
  warn "  Script --cidr arg : ${POD_CIDR}"
  warn "Flannel's CIDR MUST match kubeadm's podSubnet or pods will not route correctly."
  read -r -p "Override with kubeadm's value '${CLUSTER_CIDR}'? [Y/n] " CONFIRM
  if [[ "${CONFIRM,,}" != "n" ]]; then
    POD_CIDR="${CLUSTER_CIDR}"
    info "Using CIDR from cluster config: ${POD_CIDR}"
  fi
elif [[ -n "${CLUSTER_CIDR}" ]]; then
  success "CIDR matches cluster config: ${POD_CIDR}"
fi

info "Flannel version : ${FLANNEL_VERSION}"
info "Pod CIDR        : ${POD_CIDR}"
[[ -n "${IFACE}" ]] && info "Bind interface  : ${IFACE}" || info "Bind interface  : (auto-detect)"
info "Dry run         : ${DRY_RUN}"

# ─────────────────────────────────────────────────────────────────────────────
section "2 — Download Flannel manifest"
# ─────────────────────────────────────────────────────────────────────────────

info "Downloading ${FLANNEL_MANIFEST_URL}…"
curl -fsSL "${FLANNEL_MANIFEST_URL}" -o "${MANIFEST_FILE}"
success "Manifest saved to ${MANIFEST_FILE}"

# ─────────────────────────────────────────────────────────────────────────────
section "3 — Patch manifest"
# ─────────────────────────────────────────────────────────────────────────────

# ── 3a. Set the correct pod CIDR in the ConfigMap ────────────────────────────
DEFAULT_CIDR="10.244.0.0/16"
if [[ "${POD_CIDR}" != "${DEFAULT_CIDR}" ]]; then
  info "Replacing default CIDR ${DEFAULT_CIDR} → ${POD_CIDR} in ConfigMap…"
  sed -i "s|\"Network\": \"${DEFAULT_CIDR}\"|\"Network\": \"${POD_CIDR}\"|g" "${MANIFEST_FILE}"
  # Also handle the case where it might appear without quotes in some versions
  sed -i "s|network: ${DEFAULT_CIDR}|network: ${POD_CIDR}|g" "${MANIFEST_FILE}"
  success "CIDR patched."
else
  info "CIDR is the Flannel default (${DEFAULT_CIDR}) — no patch needed."
fi

# ── 3b. Add --iface flag to the flanneld container args (if requested) ───────
if [[ -n "${IFACE}" ]]; then
  info "Patching manifest to bind Flannel to interface: ${IFACE}…"
  # Insert --iface arg after the existing args block for the flannel container
  # The upstream manifest has:  args: [ "--ip-masq", "--kube-subnet-mgr" ]
  sed -i "s|\"--kube-subnet-mgr\"|\"--kube-subnet-mgr\", \"--iface=${IFACE}\"|g" "${MANIFEST_FILE}"
  success "Interface patched."
fi

# ── 3c. Show diff of any changes made ────────────────────────────────────────
info "Summary of manifest patches applied:"
grep -n "Network\|iface\|ip-masq\|kube-subnet" "${MANIFEST_FILE}" | head -20 || true

# ─────────────────────────────────────────────────────────────────────────────
section "4 — Apply Flannel"
# ─────────────────────────────────────────────────────────────────────────────

if [[ "${DRY_RUN}" == "true" ]]; then
  warn "DRY RUN — showing what would be applied:"
  kubectl apply --dry-run=client -f "${MANIFEST_FILE}"
  info "Re-run without --dry-run to apply."
  exit 0
fi

kubectl apply -f "${MANIFEST_FILE}"
success "Flannel manifest applied."

# ─────────────────────────────────────────────────────────────────────────────
section "5 — Wait for Flannel pods to become Ready"
# ─────────────────────────────────────────────────────────────────────────────

info "Waiting for Flannel DaemonSet pods to be scheduled…"
sleep 5   # give the API server a moment to create the pods

TIMEOUT=120
INTERVAL=5
ELAPSED=0

while true; do
  TOTAL=$(kubectl get ds kube-flannel-ds -n kube-flannel \
    --no-headers -o custom-columns='DESIRED:.status.desiredNumberScheduled' 2>/dev/null || echo "0")
  READY=$(kubectl get ds kube-flannel-ds -n kube-flannel \
    --no-headers -o custom-columns='READY:.status.numberReady' 2>/dev/null || echo "0")

  if [[ "${TOTAL}" -gt 0 && "${TOTAL}" == "${READY}" ]]; then
    success "All ${READY}/${TOTAL} Flannel pod(s) are Ready."
    break
  fi

  if [[ ${ELAPSED} -ge ${TIMEOUT} ]]; then
    warn "Timed out waiting for Flannel pods after ${TIMEOUT}s."
    warn "Check pod status with: kubectl get pods -n kube-flannel -o wide"
    break
  fi

  info "  Flannel pods ready: ${READY}/${TOTAL} — waiting… (${ELAPSED}s / ${TIMEOUT}s)"
  sleep "${INTERVAL}"
  ELAPSED=$((ELAPSED + INTERVAL))
done

# ─────────────────────────────────────────────────────────────────────────────
section "6 — Wait for control-plane node to become Ready"
# ─────────────────────────────────────────────────────────────────────────────

info "Waiting for node ${CP_NODE} to reach Ready state…"
ELAPSED=0

while true; do
  NODE_STATUS=$(kubectl get node "${CP_NODE}" --no-headers \
    -o custom-columns='STATUS:.status.conditions[-1].type,READY:.status.conditions[-1].status' 2>/dev/null \
    | awk '{print $1, $2}')

  if echo "${NODE_STATUS}" | grep -q "Ready True"; then
    success "Node ${CP_NODE} is Ready."
    break
  fi

  if [[ ${ELAPSED} -ge ${TIMEOUT} ]]; then
    warn "Timed out waiting for node Ready state after ${TIMEOUT}s."
    warn "Check with: kubectl get nodes && kubectl describe node ${CP_NODE}"
    break
  fi

  info "  Node status: ${NODE_STATUS:-unknown} — waiting… (${ELAPSED}s / ${TIMEOUT}s)"
  sleep "${INTERVAL}"
  ELAPSED=$((ELAPSED + INTERVAL))
done

# ─────────────────────────────────────────────────────────────────────────────
section "7 — Verification"
# ─────────────────────────────────────────────────────────────────────────────

echo ""
info "── Nodes ────────────────────────────────────────────────────"
kubectl get nodes -o wide

echo ""
info "── Flannel pods ─────────────────────────────────────────────"
kubectl get pods -n kube-flannel -o wide

echo ""
info "── All system pods ──────────────────────────────────────────"
kubectl get pods -n kube-system -o wide

echo ""
info "── Flannel ConfigMap (network config) ───────────────────────"
kubectl get cm kube-flannel-cfg -n kube-flannel -o jsonpath='{.data.net-conf\.json}' \
  | python3 -m json.tool 2>/dev/null || \
  kubectl get cm kube-flannel-cfg -n kube-flannel -o jsonpath='{.data.net-conf\.json}'

# ─────────────────────────────────────────────────────────────────────────────
section "✅  Flannel installation complete"
# ─────────────────────────────────────────────────────────────────────────────

cat <<NEXT_STEPS

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ① Join worker nodes using the kubeadm join command from init output.
    If the token has expired (24h), regenerate it:
      sudo kubeadm token create --print-join-command

  ② Watch all nodes reach Ready state:
      kubectl get nodes -w

  ③ Each worker node gets its own Flannel pod automatically via
    DaemonSet — no additional CNI steps needed on worker nodes.

  ④ Verify pod-to-pod networking with a quick smoke test:
      kubectl run test-pod --image=busybox:1.36 --restart=Never \
        -- sleep 3600
      kubectl exec test-pod -- ip addr show eth0
      kubectl delete pod test-pod

  ⑤ Useful diagnostic commands:
      kubectl get pods -n kube-flannel -o wide
      kubectl logs -n kube-flannel -l app=flannel
      kubectl describe node <node-name>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Manifest saved at: ${MANIFEST_FILE}
  Flannel version:   ${FLANNEL_VERSION}
  Pod CIDR:          ${POD_CIDR}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT_STEPS
