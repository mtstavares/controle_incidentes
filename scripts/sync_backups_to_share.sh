#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${DIVCIBER_BACKUP_SYNC_SOURCE:-/home/divciber-prod-test/apps/divciber-backups}"
REMOTE_SHARE="${DIVCIBER_BACKUP_SYNC_SHARE:-}"
REMOTE_SUBDIR="${DIVCIBER_BACKUP_SYNC_SUBDIR:-}"
MOUNT_DIR="${DIVCIBER_BACKUP_SYNC_MOUNT:-/mnt/divciber-backup-sync}"
CREDENTIALS_FILE="${DIVCIBER_BACKUP_SYNC_CREDENTIALS:-/etc/divciber/backup-sync.smb}"
MOUNT_OPTIONS="${DIVCIBER_BACKUP_SYNC_OPTIONS:-vers=3.0,iocharset=utf8,uid=1000,gid=1000,file_mode=0600,dir_mode=0700}"
LOCK_FILE="${DIVCIBER_BACKUP_SYNC_LOCK:-/tmp/divciber-backup-sync.lock}"

log() {
  logger -t divciber-backup-sync "$*"
  printf '%s\n' "$*"
}

fail() {
  log "FALHA: $*"
  exit 1
}

case "$SOURCE_DIR" in
  /*) ;;
  *) fail "Diretorio de origem deve ser absoluto." ;;
esac

case "$MOUNT_DIR" in
  /*) ;;
  *) fail "Diretorio de montagem deve ser absoluto." ;;
esac

case "$REMOTE_SHARE" in
  //*) ;;
  *) fail "Compartilhamento SMB deve usar formato //servidor/share e estar configurado." ;;
esac

if [ ! -d "$SOURCE_DIR" ]; then
  fail "Diretorio de origem nao existe: $SOURCE_DIR"
fi

if [ ! -f "$CREDENTIALS_FILE" ]; then
  fail "Arquivo de credenciais SMB nao encontrado: $CREDENTIALS_FILE"
fi

if ! command -v mount.cifs >/dev/null 2>&1; then
  fail "mount.cifs nao encontrado. Instale cifs-utils."
fi

if ! command -v flock >/dev/null 2>&1; then
  fail "flock nao encontrado."
fi

mkdir -p "$MOUNT_DIR"

(
  flock -n 9 || fail "Outra sincronizacao de backup ja esta em andamento."

  mounted_here=0
  if ! mountpoint -q "$MOUNT_DIR"; then
    mount -t cifs "$REMOTE_SHARE" "$MOUNT_DIR" \
      -o "credentials=${CREDENTIALS_FILE},${MOUNT_OPTIONS}"
    mounted_here=1
  fi

  cleanup() {
    if [ "$mounted_here" = "1" ] && mountpoint -q "$MOUNT_DIR"; then
      umount "$MOUNT_DIR"
    fi
  }
  trap cleanup EXIT

  TARGET_DIR="$MOUNT_DIR"
  if [ -n "$REMOTE_SUBDIR" ]; then
    TARGET_DIR="$MOUNT_DIR/$REMOTE_SUBDIR"
  fi
  mkdir -p "$TARGET_DIR"

  if command -v rsync >/dev/null 2>&1; then
    rsync -rt --omit-dir-times "$SOURCE_DIR"/ "$TARGET_DIR"/
  else
    cp -a "$SOURCE_DIR"/. "$TARGET_DIR"/
  fi

  log "SUCESSO: backups sincronizados para ${REMOTE_SHARE}/${REMOTE_SUBDIR}."
) 9>"$LOCK_FILE"
