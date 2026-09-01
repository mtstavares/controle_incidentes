#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_WIFI_IFACE="${DEPLOY_WIFI_IFACE:-wlp2s0b1}"
DEPLOY_WIFI_GATEWAY="${DEPLOY_WIFI_GATEWAY:-192.168.1.1}"
DEPLOY_WIRED_IFACE="${DEPLOY_WIRED_IFACE:-enp12s0}"
DEPLOY_WIRED_GATEWAY="${DEPLOY_WIRED_GATEWAY:-10.44.44.1}"
DEPLOY_INTRANET_DNS_SERVERS="${DEPLOY_INTRANET_DNS_SERVERS:-10.61.255.62 10.61.255.63}"
DEPLOY_INTERNET_DNS="${DEPLOY_INTERNET_DNS:-nameserver 192.168.1.1
nameserver 8.8.8.8
nameserver 1.1.1.1}"
DEPLOY_INTRANET_DNS="${DEPLOY_INTRANET_DNS:-nameserver 10.61.255.62
nameserver 10.61.255.63
search intranet.policiamilitar.sp.gov.br policiamilitar.sp.gov.br mgmt.policiamilitar.sp.gov.br}"
NETWORK_RESTORE_NEEDED=0

cd "$APP_DIR"

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi

  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo "$@"
    return
  fi

  echo "Execute este script como root ou com sudo sem senha para permitir troca temporaria de rota/DNS." >&2
  exit 1
}

write_resolv_conf() {
  local content="$1"
  run_privileged sh -c "printf '%s\n' \"\$1\" > /etc/resolv.conf" sh "$content"
}

ensure_intranet_dns_routes() {
  local dns_server
  for dns_server in $DEPLOY_INTRANET_DNS_SERVERS; do
    run_privileged ip route replace "${dns_server}/32" via "$DEPLOY_WIRED_GATEWAY" dev "$DEPLOY_WIRED_IFACE"
  done
}

set_internet_network() {
  echo "Ativando rota/DNS temporarios para deploy pela Wi-Fi (${DEPLOY_WIFI_IFACE})..."
  run_privileged ip route replace default via "$DEPLOY_WIFI_GATEWAY" dev "$DEPLOY_WIFI_IFACE" metric 50
  run_privileged ip route replace default via "$DEPLOY_WIRED_GATEWAY" dev "$DEPLOY_WIRED_IFACE" metric 200
  write_resolv_conf "$DEPLOY_INTERNET_DNS"
  NETWORK_RESTORE_NEEDED=1

  if ! getent hosts github.com >/dev/null 2>&1; then
    echo "DNS externo nao resolveu github.com apos troca temporaria para Wi-Fi." >&2
    exit 1
  fi
}

restore_intranet_network() {
  if [ "$NETWORK_RESTORE_NEEDED" != "1" ]; then
    return
  fi

  echo "Restaurando rota/DNS da intranet..."
  run_privileged ip route replace default via "$DEPLOY_WIRED_GATEWAY" dev "$DEPLOY_WIRED_IFACE" metric 50
  ensure_intranet_dns_routes
  run_privileged ip route replace default via "$DEPLOY_WIFI_GATEWAY" dev "$DEPLOY_WIFI_IFACE" metric 600
  write_resolv_conf "$DEPLOY_INTRANET_DNS"
  NETWORK_RESTORE_NEEDED=0
}

trap 'restore_intranet_network || true' EXIT

set_internet_network
"$APP_DIR/deploy.sh"
restore_intranet_network
