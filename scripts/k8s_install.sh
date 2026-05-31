#!/usr/bin/env bash
# =============================================================================
# Kubernetes 1.36.1 Node Preparation Script
# Supports: Ubuntu 26.04 | Architectures: x86_64 (Intel Xeon) & aarch64 (ARM64)
# Container runtime: containerd
# CNI: Flannel (prerequisites only — kubeadm init/join handles CNI deployment)
# =============================================================================
# Usage:
#   sudo bash k8s_install.sh
#
# After this script completes on the CONTROL PLANE node:
#   sudo kubeadm init --pod-network-cidr=10.244.0.0/16 [--apiserver-advertise-address=<IP>]
#
# After this script completes on each WORKER node:
#   sudo kubeadm join <control-plane-host>:<port> --token <token> \
#       --discovery-token-ca-cert-hash sha256:<hash>
# =============================================================================

set -euo pipefail

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}   $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}══════════════════════════════════════════════${RESET}"; \
            echo -e "${BOLD}  $*${RESET}"; \
            echo -e "${BOLD}══════════════════════════════════════════════${RESET}"; }

# ── Versions ────────────────────────────────────────────────────────────────
K8S_VERSION="1.36.1"
K8S_MINOR="1.36"                      # used for apt repo path
CONTAINERD_VERSION="2.1.1"            # latest stable at time of writing
RUNC_VERSION="1.2.6"
CNI_PLUGINS_VERSION="1.7.1"

# ── File descriptor limits ───────────────────────────────────────────────────
FD_LIMIT=100000

# ── Flannel pod CIDR (must match --pod-network-cidr in kubeadm init) ─────────
FLANNEL_CIDR="10.244.0.0/16"

# ─────────────────────────────────────────────────────────────────────────────
section "0 — Pre-flight checks"
# ─────────────────────────────────────────────────────────────────────────────

# Must run as root
[[ $EUID -eq 0 ]] || error "This script must be run as root (sudo bash $0)"

# Detect OS
. /etc/os-release
info "Detected OS: ${PRETTY_NAME}"
[[ "${ID}" == "ubuntu" ]] || error "This script targets Ubuntu. Detected: ${ID}"

# Detect architecture
ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64)  DEB_ARCH="amd64";  CONTAINERD_ARCH="amd64"  ;;
  aarch64) DEB_ARCH="arm64";  CONTAINERD_ARCH="arm64"  ;;
  *)       error "Unsupported architecture: ${ARCH}" ;;
esac
info "Architecture: ${ARCH} → package arch: ${DEB_ARCH}"

# ─────────────────────────────────────────────────────────────────────────────
section "1 — System update & essential packages"
# ─────────────────────────────────────────────────────────────────────────────

export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get upgrade -y
apt-get install -y \
  apt-transport-https \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  software-properties-common \
  jq \
  wget \
  socat \
  conntrack \
  ipset \
  ipvsadm \
  nfs-common \
  open-iscsi \
  util-linux

success "Base packages installed."

# ── Ubuntu 26.04 DNS Fix for CoreDNS ──────────────────────────────────────────
info "Fixing systemd-resolved loop configuration for CoreDNS stability..."
if [ -f /run/systemd/resolve/resolv.conf ]; then
  ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
  success "Symlinked upstream resolv.conf directly to /etc/resolv.conf."
else
  warn "Upstream resolv.conf not found at standard path. Skipping DNS link tweak."
fi

# ─────────────────────────────────────────────────────────────────────────────
section "2 — File descriptor limits (soft & hard → ${FD_LIMIT})"
# ─────────────────────────────────────────────────────────────────────────────

LIMITS_CONF="/etc/security/limits.d/99-k8s-fd.conf"
cat > "${LIMITS_CONF}" <<EOF
# Raised for Kubernetes workloads
* soft nofile ${FD_LIMIT}
* hard nofile ${FD_LIMIT}
root soft nofile ${FD_LIMIT}
root hard nofile ${FD_LIMIT}
EOF
success "Written ${LIMITS_CONF}"

# Also raise for the current session and systemd services
SYSCTL_CONF="/etc/sysctl.d/99-k8s.conf"

# systemd default file limit
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/99-fd-limit.conf <<EOF
[Manager]
DefaultLimitNOFILE=${FD_LIMIT}
EOF
success "systemd DefaultLimitNOFILE set to ${FD_LIMIT}"

# Apply to current shell session
ulimit -n "${FD_LIMIT}" 2>/dev/null || warn "Could not raise ulimit for current session (will apply after re-login)"

# ─────────────────────────────────────────────────────────────────────────────
section "3 — Kernel parameters & modules required by Kubernetes"
# ─────────────────────────────────────────────────────────────────────────────

# Load required modules at boot
cat > /etc/modules-load.d/k8s.conf <<EOF
overlay
br_netfilter
ip_vs
ip_vs_rr
ip_vs_wrr
ip_vs_sh
nf_conntrack
EOF

modprobe overlay
modprobe br_netfilter
modprobe ip_vs
modprobe ip_vs_rr
modprobe ip_vs_wrr
modprobe ip_vs_sh
modprobe nf_conntrack 2>/dev/null || true

success "Kernel modules loaded."

# sysctl settings
cat > "${SYSCTL_CONF}" <<EOF
# ── Kubernetes required ────────────────────────────────────────────
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
net.ipv6.conf.all.forwarding        = 1

# ── IPVS / conntrack ──────────────────────────────────────────────
net.netfilter.nf_conntrack_max      = 1048576

# ── File descriptors ──────────────────────────────────────────────
fs.file-max                         = ${FD_LIMIT}
fs.inotify.max_user_instances       = 8192
fs.inotify.max_user_watches         = 524288

# ── Performance / stability ───────────────────────────────────────
kernel.panic                        = 10
kernel.panic_on_oops                = 1
vm.overcommit_memory                = 1
vm.panic_on_oom                     = 0
EOF

sysctl --system
success "Kernel parameters applied."

# ─────────────────────────────────────────────────────────────────────────────
section "4 — Disable swap (required by kubelet)"
# ─────────────────────────────────────────────────────────────────────────────

swapoff -a
success "Swap disabled for current session."

# Persist across reboots
if grep -qE '^\s*[^#].*\s+swap\s+' /etc/fstab 2>/dev/null; then
  sed -i.bak -E 's|^([^#].*\s+swap\s+.*)$|# \1  # disabled by k8s-install.sh|' /etc/fstab
  success "Swap entries commented out in /etc/fstab (backup saved as /etc/fstab.bak)."
else
  info "No active swap entries found in /etc/fstab."
fi

# Disable systemd swap units if present
systemctl --type swap --all --no-legend 2>/dev/null | awk '{print $1}' | while read -r unit; do
  systemctl mask "${unit}" 2>/dev/null || true
done

# ─────────────────────────────────────────────────────────────────────────────
section "5 — Install containerd ${CONTAINERD_VERSION}"
# ─────────────────────────────────────────────────────────────────────────────

CONTAINERD_URL="https://github.com/containerd/containerd/releases/download/v${CONTAINERD_VERSION}/containerd-${CONTAINERD_VERSION}-linux-${CONTAINERD_ARCH}.tar.gz"
CONTAINERD_SHA_URL="${CONTAINERD_URL}.sha256sum"

info "Downloading containerd from GitHub releases…"
TMP_DIR="$(mktemp -d)"

CONTAINERD_TARBALL="containerd-${CONTAINERD_VERSION}-linux-${CONTAINERD_ARCH}.tar.gz"
curl -fsSL "${CONTAINERD_URL}"     -o "${TMP_DIR}/${CONTAINERD_TARBALL}"
curl -fsSL "${CONTAINERD_SHA_URL}" -o "${TMP_DIR}/${CONTAINERD_TARBALL}.sha256sum"

pushd "${TMP_DIR}" > /dev/null
sha256sum -c "${CONTAINERD_TARBALL}.sha256sum"
popd > /dev/null
success "containerd archive checksum verified."

tar -C /usr/local -xzf "${TMP_DIR}/${CONTAINERD_TARBALL}"
success "containerd binaries extracted to /usr/local/bin."

# systemd service unit
mkdir -p /usr/local/lib/systemd/system
curl -fsSL \
  "https://raw.githubusercontent.com/containerd/containerd/v${CONTAINERD_VERSION}/containerd.service" \
  -o /usr/local/lib/systemd/system/containerd.service

# ── runc ─────────────────────────────────────────────────────────────────────
info "Installing runc ${RUNC_VERSION}…"
RUNC_URL="https://github.com/opencontainers/runc/releases/download/v${RUNC_VERSION}/runc.${CONTAINERD_ARCH}"
curl -fsSL "${RUNC_URL}" -o /usr/local/sbin/runc
chmod 755 /usr/local/sbin/runc
success "runc installed."

# ── CNI plugins ───────────────────────────────────────────────────────────────
info "Installing CNI plugins ${CNI_PLUGINS_VERSION}…"
CNI_URL="https://github.com/containernetworking/plugins/releases/download/v${CNI_PLUGINS_VERSION}/cni-plugins-linux-${CONTAINERD_ARCH}-v${CNI_PLUGINS_VERSION}.tgz"
mkdir -p /opt/cni/bin
curl -fsSL "${CNI_URL}" | tar -C /opt/cni/bin -xz
success "CNI plugins installed to /opt/cni/bin."

# ── containerd configuration ─────────────────────────────────────────────────
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml

# Enable systemd cgroup driver
sed -i 's|SystemdCgroup = false|SystemdCgroup = true|g' /etc/containerd/config.toml

# Point sandbox image to a version consistent with K8s 1.36
PAUSE_IMAGE="registry.k8s.io/pause:3.10"
sed -i "s|sandbox_image = \".*\"|sandbox_image = \"${PAUSE_IMAGE}\"|g" /etc/containerd/config.toml
success "containerd configured (systemd cgroup, pause image: ${PAUSE_IMAGE})."

# Enable & start containerd
systemctl daemon-reload
systemctl enable --now containerd
success "containerd service enabled and started."

rm -rf "${TMP_DIR}"

# ─────────────────────────────────────────────────────────────────────────────
section "6 — Install kubeadm, kubelet & kubectl ${K8S_VERSION}"
# ─────────────────────────────────────────────────────────────────────────────

# Add Kubernetes apt repository
K8S_KEYRING="/etc/apt/keyrings/kubernetes-apt-keyring.gpg"
mkdir -p /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_MINOR}/deb/Release.key" \
  | gpg --dearmor -o "${K8S_KEYRING}"
chmod 644 "${K8S_KEYRING}"

cat > /etc/apt/sources.list.d/kubernetes.list <<EOF
deb [signed-by=${K8S_KEYRING}] https://pkgs.k8s.io/core:/stable:/v${K8S_MINOR}/deb/ /
EOF

apt-get update -y

# Pin exact version
K8S_PKG_VER="${K8S_VERSION}-1.1"

apt-get install -y \
  "kubelet=${K8S_PKG_VER}" \
  "kubeadm=${K8S_PKG_VER}" \
  "kubectl=${K8S_PKG_VER}"

# Prevent unintended upgrades
apt-mark hold kubelet kubeadm kubectl
success "kubelet, kubeadm, kubectl ${K8S_VERSION} installed and held."

# ─────────────────────────────────────────────────────────────────────────────
section "7 — Enable kubelet Service"
# ─────────────────────────────────────────────────────────────────────────────
# NOTE: Old KUBELET_EXTRA_ARGS overrides have been completely removed.
# kubeadm naturally auto-configures the modern kubelet runtime configuration flags.

systemctl daemon-reload
systemctl enable kubelet
success "kubelet service enabled (will transition to running fully after kubeadm init/join)."

# ─────────────────────────────────────────────────────────────────────────────
section "8 — Firewall / iptables configuration"
# ─────────────────────────────────────────────────────────────────────────────

update-alternatives --set iptables  /usr/sbin/iptables-legacy  2>/dev/null || true
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true
success "iptables backend set to legacy (compatible with kube-proxy)."

# ─────────────────────────────────────────────────────────────────────────────
section "9 — Flannel prerequisites"
# ─────────────────────────────────────────────────────────────────────────────

info "Flannel pod CIDR will be: ${FLANNEL_CIDR}"
info "Ensuring VXLAN module is available…"
modprobe vxlan 2>/dev/null || warn "vxlan module not loadable; it may be built-in."
grep -qxF 'vxlan' /etc/modules-load.d/k8s.conf || echo 'vxlan' >> /etc/modules-load.d/k8s.conf
success "vxlan module ensured."

# ─────────────────────────────────────────────────────────────────────────────
section "10 — Pull Kubernetes control-plane images"
# ─────────────────────────────────────────────────────────────────────────────

info "Pre-pulling kubeadm images…"
kubeadm config images pull --kubernetes-version "v${K8S_VERSION}" 2>&1 \
  && success "Control-plane images pulled." \
  || warn "Image pull failed (possibly a worker node or no internet). Continuing."

# ─────────────────────────────────────────────────────────────────────────────
section "11 — Final verification"
# ─────────────────────────────────────────────────────────────────────────────

echo ""
info "── Swap status ──────────────────────────────────────────────"
swapon --show && warn "Swap is still active on some device!" || success "Swap is off."

info "── Kernel modules ───────────────────────────────────────────"
for mod in overlay br_netfilter ip_vs vxlan; do
  if lsmod | grep -q "^${mod}"; then
    success "  ${mod}: loaded"
  else
    warn "  ${mod}: NOT loaded (may be built-in)"
  fi
done

info "── sysctl spot-check ─────────────────────────────────────────"
for key in net.bridge.bridge-nf-call-iptables net.ipv4.ip_forward fs.file-max; do
  val=$(sysctl -n "${key}" 2>/dev/null || echo "unknown")
  success "  ${key} = ${val}"
done

info "── containerd ────────────────────────────────────────────────"
systemctl is-active containerd && success "  containerd: active" || warn "  containerd: NOT active"
containerd --version

info "── Kubernetes tools ─────────────────────────────────────────"
kubelet  --version
kubeadm  version
kubectl  version --client

# ─────────────────────────────────────────────────────────────────────────────
section "✅  Installation complete"
# ─────────────────────────────────────────────────────────────────────────────

cat <<'NEXT_STEPS'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  NEXT STEPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ① CONTROL PLANE — run on the first/master node:

      sudo kubeadm init \
        --kubernetes-version v1.36.1 \
        --pod-network-cidr 10.244.0.0/16 \
        --cri-socket unix:///run/containerd/containerd.sock

      Then (as your normal user):
        mkdir -p $HOME/.kube
        sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
        sudo chown $(id -u):$(id -g) $HOME/.kube/config

      Deploy Flannel CNI:
        kubectl apply -f \
          https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml

  ② WORKER NODES — run the kubeadm join command printed by kubeadm init:

      sudo kubeadm join <control-plane-ip>:6443 \
        --token <token> \
        --discovery-token-ca-cert-hash sha256:<hash> \
        --cri-socket unix:///run/containerd/containerd.sock

  ③ If the join token expires (24 h), regenerate it on the control plane:
        sudo kubeadm token create --print-join-command

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT_STEPS
