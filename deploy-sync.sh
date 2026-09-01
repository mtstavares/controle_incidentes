#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${DATA_ROOT:-$HOME/apps/divciber-data}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/apps/divciber-backups}"
INSTANCE_DIR="$DATA_ROOT/instance"
LOG_DIR="$DATA_ROOT/logs"
CONTAINER_NAME="${CONTAINER_NAME:-divciber}"
DEPLOY_USE_WIFI="${DEPLOY_USE_WIFI:-0}"
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

  echo "Nao foi possivel alterar rota/DNS automaticamente. Execute como root ou use o deploy-sync sem DEPLOY_USE_WIFI." >&2
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
  if [ "$DEPLOY_USE_WIFI" != "1" ]; then
    return
  fi

  echo "Ativando rota/DNS temporarios para sincronizacao pela Wi-Fi (${DEPLOY_WIFI_IFACE})..."
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

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker nao encontrado. Instale o Docker antes de executar o deploy." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin nao encontrado. Instale docker-compose-plugin." >&2
  exit 1
fi

set_internet_network

if [ "${SKIP_GIT_PULL:-0}" != "1" ] && [ -d .git ]; then
  CURRENT_BRANCH="$(git branch --show-current)"
  echo "Atualizando branch ${CURRENT_BRANCH}..."
  git fetch origin "$CURRENT_BRANCH"
  git pull --ff-only origin "$CURRENT_BRANCH"
fi

restore_intranet_network

mkdir -p "$INSTANCE_DIR" "$LOG_DIR" "$BACKUP_ROOT"

if ! docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  echo "Container ${CONTAINER_NAME} nao esta em execucao. Subindo com imagem local, sem build..."
  docker compose up -d --no-build
fi

echo "Sincronizando codigo para o container ${CONTAINER_NAME}..."
docker cp app/. "$CONTAINER_NAME:/app/app"
docker cp migrations/. "$CONTAINER_NAME:/app/migrations"
docker cp scripts/. "$CONTAINER_NAME:/app/scripts"
docker cp config.py "$CONTAINER_NAME:/app/config.py"
docker cp database.py "$CONTAINER_NAME:/app/database.py"
docker cp insert_db.py "$CONTAINER_NAME:/app/insert_db.py"
docker cp run.py "$CONTAINER_NAME:/app/run.py"

echo "Aplicando migrations com a imagem atual..."
docker compose exec -T divciber flask db upgrade

echo "Reiniciando container..."
docker compose restart divciber

echo "Status:"
docker compose ps

echo "Logs recentes:"
docker compose logs --tail=80 divciber

echo
echo "Sincronizacao concluida sem rebuild de imagem."
