#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${DATA_ROOT:-$HOME/apps/divciber-data}"
INSTANCE_DIR="$DATA_ROOT/instance"
LOG_DIR="$DATA_ROOT/logs"

cd "$APP_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker nao encontrado. Instale o Docker antes de executar o deploy." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin nao encontrado. Instale docker-compose-plugin." >&2
  exit 1
fi

if [ "${SKIP_GIT_PULL:-0}" != "1" ] && [ -d .git ]; then
  CURRENT_BRANCH="$(git branch --show-current)"
  echo "Atualizando branch ${CURRENT_BRANCH}..."
  git fetch origin "$CURRENT_BRANCH"
  git pull --ff-only origin "$CURRENT_BRANCH"
fi

mkdir -p "$INSTANCE_DIR" "$LOG_DIR"

if [ ! -f .env ]; then
  if command -v openssl >/dev/null 2>&1; then
    SECRET_KEY="$(openssl rand -hex 32)"
  else
    SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  fi

  cat > .env <<EOF
SECRET_KEY=${SECRET_KEY}
DATABASE_URL=sqlite:////app/instance/divciber.db
TIMEZONE=America/Sao_Paulo
DIVCIBER_PORT=5005
GUNICORN_WORKERS=1
PM_API_VERIFY_TLS=0
EOF
  chmod 600 .env
  echo "Arquivo .env criado com SECRET_KEY gerada automaticamente."
fi

echo "Construindo imagem Docker..."
docker compose build

echo "Subindo container..."
docker compose up -d

echo "Status:"
docker compose ps

echo "Logs recentes:"
docker compose logs --tail=80 divciber

echo
echo "Aplicacao disponivel na porta configurada em DIVCIBER_PORT."
