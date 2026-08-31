#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${DATA_ROOT:-$HOME/apps/divciber-data}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/apps/divciber-backups}"
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

mkdir -p "$INSTANCE_DIR" "$LOG_DIR" "$BACKUP_ROOT"

if [ ! -f .env ]; then
  if command -v openssl >/dev/null 2>&1; then
    SECRET_KEY="$(openssl rand -hex 32)"
    BACKUP_HMAC_KEY="$(openssl rand -hex 48)"
    BACKUP_ENCRYPTION_KEY="$(openssl rand -hex 32)"
  else
    SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
    BACKUP_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_hex(48))')"
    BACKUP_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  fi

  cat > .env <<EOF
SECRET_KEY=${SECRET_KEY}
DATABASE_URL=sqlite:////app/instance/divciber.db
TIMEZONE=America/Sao_Paulo
DIVCIBER_PORT=5005
GUNICORN_WORKERS=1
PM_API_BASE_URL=
PM_API_VERIFY_TLS=0
NETBOX_API_BASE_URL=
NETBOX_API_TOKEN=
NETBOX_API_VERIFY_TLS=0
DIVCIBER_BACKUP_DEFAULT_DIR=/app/backups
DIVCIBER_BACKUP_INTERVAL_HOURS=6
DIVCIBER_BACKUP_RETENTION_DAYS=30
DIVCIBER_BACKUP_MIN_FULL=4
DIVCIBER_BACKUP_HMAC_KEY=${BACKUP_HMAC_KEY}
DIVCIBER_BACKUP_ENCRYPTION_KEY=${BACKUP_ENCRYPTION_KEY}
EOF
  chmod 600 .env
  echo "Arquivo .env criado com SECRET_KEY gerada automaticamente."
fi

ensure_env_var() {
  local name="$1"
  local value="$2"
  if ! grep -q "^${name}=" .env; then
    printf '%s=%s\n' "$name" "$value" >> .env
  fi
}

replace_env_var_if_value() {
  local name="$1"
  local old_value="$2"
  local new_value="$3"
  if grep -q "^${name}=${old_value}$" .env; then
    sed -i "s|^${name}=.*$|${name}=${new_value}|" .env
  fi
}

if command -v openssl >/dev/null 2>&1; then
  ensure_env_var "DIVCIBER_BACKUP_HMAC_KEY" "$(openssl rand -hex 48)"
  ensure_env_var "DIVCIBER_BACKUP_ENCRYPTION_KEY" "$(openssl rand -hex 32)"
else
  ensure_env_var "DIVCIBER_BACKUP_HMAC_KEY" "$(python -c 'import secrets; print(secrets.token_hex(48))')"
  ensure_env_var "DIVCIBER_BACKUP_ENCRYPTION_KEY" "$(python -c 'import secrets; print(secrets.token_hex(32))')"
fi
replace_env_var_if_value "DIVCIBER_BACKUP_DEFAULT_DIR" "/app/instance/backups" "/app/backups"
ensure_env_var "DIVCIBER_BACKUP_DEFAULT_DIR" "/app/backups"
ensure_env_var "DIVCIBER_BACKUP_INTERVAL_HOURS" "6"
ensure_env_var "DIVCIBER_BACKUP_RETENTION_DAYS" "30"
ensure_env_var "DIVCIBER_BACKUP_MIN_FULL" "4"

echo "Construindo imagem Docker..."
docker compose build

echo "Aplicando migrations..."
docker compose run --rm divciber flask db upgrade

echo "Subindo container..."
docker compose up -d

echo "Status:"
docker compose ps

echo "Logs recentes:"
docker compose logs --tail=80 divciber

echo
echo "Aplicacao disponivel na porta configurada em DIVCIBER_PORT."
