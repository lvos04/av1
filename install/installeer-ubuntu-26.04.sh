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
if [[ -L /srv/av1-pipeline ]]; then
    echo "/srv/av1-pipeline mag geen symlink zijn." >&2
    exit 1
fi
install -d -o root -g root -m 0755 /srv/av1-pipeline
root_device=$(findmnt -n -o SOURCE -T /)
pipeline_device=$(findmnt -n -o SOURCE -T /srv/av1-pipeline)
if [[ $root_device != "$pipeline_device" ]]; then
    echo "De standaardinstallatie verwacht /srv/av1-pipeline op de systeemschijf." >&2
    echo "Kies een latere aparte werkschijf veilig via de interface." >&2
    exit 1
fi

apt-get update
apt-get install -y --no-install-recommends software-properties-common
add-apt-repository -y universe
add-apt-repository -y multiverse
apt-get update
apt-get install -y --no-install-recommends \
    ffmpeg mkvtoolnix mediainfo vainfo intel-media-va-driver-non-free \
    libvpl2 libmfx-gen1.2 intel-gpu-tools linux-tools-common \
    python3 python3-venv python3-pip nodejs npm \
    ca-certificates curl git jq sqlite3 openssl cifs-utils acl attr

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
install -d -o root -g root -m 0755 /library/netwerk

# De backend praat uitsluitend met deze root-owned, streng gevalideerde
# socketservice; NoNewPrivileges blijft aan en er zijn geen sudo-rechten.
install -o root -g root -m 0755 \
    "$PROJECT_DIR/install/av1-pipeline-smb-helper.py" \
    /usr/local/sbin/av1-pipeline-smb-helper
install -o root -g root -m 0644 \
    "$PROJECT_DIR/systemd/av1-pipeline-smb-helper.socket" \
    /etc/systemd/system/av1-pipeline-smb-helper.socket
install -o root -g root -m 0644 \
    "$PROJECT_DIR/systemd/av1-pipeline-smb-helper@.service" \
    /etc/systemd/system/av1-pipeline-smb-helper@.service
rm -f /etc/sudoers.d/av1-pipeline-smb-helper

systemctl daemon-reload
systemctl enable --now av1-pipeline-smb-helper.socket
# De applicaties starten pas wanneer de host daadwerkelijk boot of wanneer de
# beheerder ze nu start; enable maakt een stroomherstel niet afhankelijk van
# een later handmatig commando.
systemctl enable av1-pipeline-backend.service av1-pipeline-frontend.service
echo "Hostvoorbereiding klaar. Bouw nu de applicatie; start de services pas wanneer fase 0 groen is."
