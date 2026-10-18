#!/bin/bash
# Boot script of the staging-temporal VM (Debian 12), run by the guest agent on every boot.
# Terraform passes it as `startup-script` and the two config files as metadata.
#
# 1. Mount the data disk (formatted on first boot only) at /mnt/disks/temporal-data.
# 2. Install Docker Engine and the compose plugin from Docker's apt repository (first boot).
# 3. Generate the Temporal database password on the data disk (first boot) - it never
#    leaves the VM.
# 4. Write compose.yaml and dynamicconfig.yaml from metadata and `docker compose up -d`.
#
# Idempotent: a reboot or a metadata change followed by a reboot re-applies the files and
# brings the stack to the pinned images without touching the data.
set -euo pipefail

MOUNT=/mnt/disks/temporal-data
DEVICE=/dev/disk/by-id/google-temporal-data
APP=/opt/temporal
MD=http://metadata.google.internal/computeMetadata/v1/instance/attributes

metadata() { curl -sf -H "Metadata-Flavor: Google" "$MD/$1"; }

# 1. Data disk
if ! blkid "$DEVICE" >/dev/null 2>&1; then
  mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard "$DEVICE"
fi
mkdir -p "$MOUNT"
if ! mountpoint -q "$MOUNT"; then
  mount -o discard,defaults "$DEVICE" "$MOUNT"
fi
grep -q "$MOUNT" /etc/fstab || echo "$DEVICE $MOUNT ext4 discard,defaults,nofail 0 2" >> /etc/fstab

# 2. Docker (https://docs.docker.com/engine/install/debian/)
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
# Container logs go to Cloud Logging (the VM's service account has logging.logWriter).
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
  echo '{"log-driver": "gcplogs", "log-opts": {"gcp-log-cmd": "false"}}' > /etc/docker/daemon.json
  systemctl restart docker
fi
systemctl enable --now docker

# 3. Database password, generated once and kept with the data
if [ ! -f "$MOUNT/temporal.env" ]; then
  (umask 077 && echo "TEMPORAL_DB_PASSWORD=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
    > "$MOUNT/temporal.env")
fi

# 4. Compose stack
mkdir -p "$APP"
metadata temporal-compose > "$APP/compose.yaml"
metadata temporal-dynamicconfig > "$APP/dynamicconfig.yaml"
# The server container runs as a non-root user and reads the dynamic config.
chmod 644 "$APP/compose.yaml" "$APP/dynamicconfig.yaml"
INTERNAL_IP=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/ip)
{
  cat "$MOUNT/temporal.env"
  echo "DATA_DIR=$MOUNT"
  echo "BIND_ADDRESS=$INTERNAL_IP"
  echo "TEMPORAL_NAMESPACE=$(metadata temporal-namespace)"
} > "$APP/.env"
chmod 600 "$APP/.env"
cd "$APP"
docker compose pull --quiet
docker compose up -d --remove-orphans
