#!/usr/bin/env bash
# Idempotent bootstrap for the "night-shift" k3d demo cluster.
# docker + k3d + kubectl + namespace city + 4 workloads.
# payments-api is SUPPOSED to show CrashLoopBackOff (OOMKilled) before the agent heals it.
set -euo pipefail

say() { echo "[bootstrap] $*"; }
CTX="k3d-night-shift"

if ! command -v docker >/dev/null 2>&1; then
  say "installing docker (apt)..."
  apt-get update -qq
  apt-get install -y -qq docker.io
  systemctl enable --now docker 2>/dev/null || true
fi
if ! docker info >/dev/null 2>&1; then
  say "starting docker daemon..."
  (systemctl start docker 2>/dev/null || service docker start 2>/dev/null || dockerd >/dev/null 2>&1 &)
  for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { say "ERROR: docker daemon not reachable"; exit 1; }
fi
say "docker ok: $(docker --version)"

if ! command -v k3d >/dev/null 2>&1; then
  say "installing k3d..."
  curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | TAG=v5.7.4 bash
fi
say "k3d ok: $(k3d version | head -1)"

if ! command -v kubectl >/dev/null 2>&1; then
  say "installing kubectl..."
  curl -sL "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    -o /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl
fi
say "kubectl ok: $(kubectl version --client 2>/dev/null | head -1)"

if ! k3d cluster list | grep -q '^night-shift '; then
  say "creating k3d cluster night-shift..."
  k3d cluster create night-shift --servers 1 --agents 0 --wait \
    --k3s-arg "--disable=traefik@server:0"
else
  say "cluster night-shift already exists"
fi

for i in $(seq 1 60); do
  kubectl --context "$CTX" get nodes 2>/dev/null | grep -q Ready && break
  sleep 2
done
kubectl --context "$CTX" get nodes

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
kubectl --context "$CTX" apply -f "${SCRIPT_DIR}/manifests/00-namespace.yaml"
kubectl --context "$CTX" apply -f "${SCRIPT_DIR}/manifests/01-city.yaml"

say "waiting for images to pull and pods to settle..."
sleep 30
kubectl --context "$CTX" get pods -n city -o wide || true
say "expected: payments-api CrashLoopBackOff (OOMKilled, BY DESIGN), web/redis/postgres Running."
say "the agent heals payments-api at runtime by raising its memory limit via kubectl_patch_limits."