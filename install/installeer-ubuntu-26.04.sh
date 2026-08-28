#!/usr/bin/env bash
# Eenmalige hostvoorbereiding. Dit script start geen applicaties en raakt geen
# mediabestanden aan.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Draai dit script met sudo of als root." >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    echo "Kan de Linux-distributie niet vaststellen." >&2
    exit 1
fi
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 26.04 ]]; then
    echo "Dit pakket is bedoeld voor Ubuntu Server 26.04; gevonden: ${PRETTY_NAME:-onbekend}." >&2
    exit 1
fi

PROJECT_DIR=${1:-/opt/av1-pipeline}
if [[ ! -f "$PROJECT_DIR/systemd/av1-pipeline-backend.service" ]]; then
    echo "Geen AV1-projectmap gevonden op: $PROJECT_DIR" >&2
    exit 1
fi
if ! mountpoint -q /srv/av1-pipeline; then
    echo "De project-SSD moet eerst vast aangekoppeld zijn op /srv/av1-pipeline." >&2
    echo "Installeer niet verder op een gewone map op de systeemschijf." >&2
    exit 1
fi

apt-get update
apt-get install -y --no-install-recommends software-properties-common
add-apt-repository -y universe
add-apt-repository -y multiverse
apt-get update
apt-get install -y --no-install-recommends \
    ffmpeg mkvtoolnix mediainfo vainfo intel-media-va-driver-non-free \
    libvpl2 libmfx-gen1.2 intel-gpu-tools \
    python3 python3-venv python3-pip nodejs npm \
    ca-certificates curl git jq sqlite3 openssl

# Profiel 7 mag alleen worden verwerkt als de vaste, gecontroleerde upstream-
# binary aanwezig is; stil half uitvoeren is niet toegestaan.
bash "$PROJECT_DIR/install/installeer-dovi-tool.sh"

if ! id -u av1pipeline >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /var/lib/av1-pipeline \
        --shell /usr/sbin/nologin av1pipeline
fi
if ! id -u av1frontend >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /var/lib/av1-frontend \
        --shell /usr/sbin/nologin av1frontend
fi
for group in render video; do
    if getent group "$group" >/dev/null; then
        usermod -aG "$group" av1pipeline
    else
        echo "Waarschuwing: groep '$group' bestaat niet; controleer /dev/dri na herstart." >&2
    fi
done

install -d -o av1pipeline -g av1pipeline -m 0750 /var/lib/av1-pipeline /var/log/av1-pipeline
install -d -o av1frontend -g av1frontend -m 0750 /var/lib/av1-frontend

escaped_project=$(printf '%s' "$PROJECT_DIR" | sed 's/[&|]/\\&/g')
sed "s|__PROJECT_DIR__|$escaped_project|g" \
    "$PROJECT_DIR/systemd/av1-pipeline-backend.service" \
    > /etc/systemd/system/av1-pipeline-backend.service
sed "s|__PROJECT_DIR__|$escaped_project|g" \
    "$PROJECT_DIR/systemd/av1-pipeline-frontend.service" \
    > /etc/systemd/system/av1-pipeline-frontend.service
if [[ ! -e /etc/av1-pipeline.env ]]; then
    install -m 0640 -o root -g av1pipeline \
        "$PROJECT_DIR/systemd/av1-pipeline.env.example" /etc/av1-pipeline.env
fi
if [[ ! -e /etc/av1-pipeline-secrets.key ]]; then
    umask 027
    openssl rand -base64 48 > /etc/av1-pipeline-secrets.key
    chown root:av1pipeline /etc/av1-pipeline-secrets.key
    chmod 0640 /etc/av1-pipeline-secrets.key
fi

install -d -o av1pipeline -g av1pipeline -m 0750 \
    /srv/av1-pipeline/cache /srv/av1-pipeline/work \
    /srv/av1-pipeline/config /srv/av1-pipeline/backups

systemctl daemon-reload
echo "Hostvoorbereiding klaar. Bouw nu de applicatie; start de services pas wanneer fase 0 groen is."
