#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute com sudo: sudo scripts/install_backup_share_sync.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SOURCE_SCRIPT="$SCRIPT_DIR/sync_backups_to_share.sh"

if [ ! -f "$SYNC_SOURCE_SCRIPT" ]; then
  echo "Script de sincronizacao nao encontrado: $SYNC_SOURCE_SCRIPT" >&2
  exit 1
fi

apt-get update
apt-get install -y cifs-utils rsync util-linux

install -d -m 700 /etc/divciber
install -m 755 "$SYNC_SOURCE_SCRIPT" /usr/local/sbin/divciber-backup-sync

ENV_FILE="/etc/divciber/backup-sync.env"
CREDENTIALS_FILE="/etc/divciber/backup-sync.smb"
SYNC_SOURCE="${DIVCIBER_BACKUP_SYNC_SOURCE:-/home/divciber-prod-test/apps/divciber-backups}"
SYNC_SHARE="${DIVCIBER_BACKUP_SYNC_SHARE:-//SERVIDOR/COMPARTILHAMENTO}"
SYNC_SUBDIR="${DIVCIBER_BACKUP_SYNC_SUBDIR:-SUBDIRETORIO_OPCIONAL}"
SYNC_MOUNT="${DIVCIBER_BACKUP_SYNC_MOUNT:-/mnt/divciber-backup-sync}"
SYNC_OPTIONS="${DIVCIBER_BACKUP_SYNC_OPTIONS:-vers=3.0,iocharset=utf8,uid=1000,gid=1000,file_mode=0600,dir_mode=0700}"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
DIVCIBER_BACKUP_SYNC_SOURCE=${SYNC_SOURCE}
DIVCIBER_BACKUP_SYNC_SHARE=${SYNC_SHARE}
DIVCIBER_BACKUP_SYNC_SUBDIR=${SYNC_SUBDIR}
DIVCIBER_BACKUP_SYNC_MOUNT=${SYNC_MOUNT}
DIVCIBER_BACKUP_SYNC_CREDENTIALS=/etc/divciber/backup-sync.smb
DIVCIBER_BACKUP_SYNC_OPTIONS=${SYNC_OPTIONS}
EOF
  chmod 600 "$ENV_FILE"
fi

if [ ! -f "$CREDENTIALS_FILE" ]; then
  cat > "$CREDENTIALS_FILE" <<'EOF'
username=INFORME_USUARIO
password=INFORME_SENHA
domain=WORKGROUP
EOF
  chmod 600 "$CREDENTIALS_FILE"
  echo "Edite $CREDENTIALS_FILE com o usuario e senha do compartilhamento antes de iniciar o timer."
fi

cat > /etc/systemd/system/divciber-backup-sync.service <<'EOF'
[Unit]
Description=Sincroniza backups DivCiber para compartilhamento SMB
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/divciber/backup-sync.env
ExecStart=/usr/local/sbin/divciber-backup-sync
EOF

cat > /etc/systemd/system/divciber-backup-sync.timer <<'EOF'
[Unit]
Description=Executa sincronizacao dos backups DivCiber a cada 12 horas

[Timer]
OnBootSec=10min
OnUnitActiveSec=12h
Persistent=true
Unit=divciber-backup-sync.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable divciber-backup-sync.timer

echo "Instalacao concluida."
echo "1) Confira as credenciais em: $CREDENTIALS_FILE"
echo "2) Teste com: sudo systemctl start divciber-backup-sync.service"
echo "3) Ative agora com: sudo systemctl start divciber-backup-sync.timer"
echo "4) Veja status com: systemctl status divciber-backup-sync.timer"
