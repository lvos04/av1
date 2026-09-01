#!/usr/bin/env bash
# Installeert de socket-geactiveerde SMB-helper. De backend behoudt
# NoNewPrivileges en krijgt geen sudo- of algemene mountrechten.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Draai dit script met sudo." >&2
    exit 1
fi

project_dir=${1:-/opt/av1-pipeline}
helper_source="$project_dir/install/av1-pipeline-smb-helper.py"
socket_source="$project_dir/systemd/av1-pipeline-smb-helper.socket"
service_source="$project_dir/systemd/av1-pipeline-smb-helper@.service"
if [[ ! -f $helper_source || ! -f $socket_source || ! -f $service_source ]]; then
    echo "Bibliotheekbronbestanden ontbreken onder: $project_dir" >&2
    exit 1
fi

apt-get update
apt-get install -y --no-install-recommends cifs-utils acl attr
install -d -o root -g root -m 0755 /library/netwerk
install -o root -g root -m 0755 "$helper_source" /usr/local/sbin/av1-pipeline-smb-helper
install -o root -g root -m 0644 \
    "$socket_source" /etc/systemd/system/av1-pipeline-smb-helper.socket
install -o root -g root -m 0644 \
    "$service_source" /etc/systemd/system/av1-pipeline-smb-helper@.service

# Verwijder uitsluitend de door een eerdere pakketversie geplaatste regel.
rm -f /etc/sudoers.d/av1-pipeline-smb-helper

systemctl daemon-reload
systemctl enable --now av1-pipeline-smb-helper.socket
systemctl restart av1-pipeline-backend.service av1-pipeline-frontend.service
mapfile -t failed_helper_units < <(
    systemctl --failed --plain --no-legend 'av1-pipeline-smb-helper@*.service' |
        awk '{print $1}'
)
if (( ${#failed_helper_units[@]} )); then
    systemctl reset-failed "${failed_helper_units[@]}"
fi
systemctl is-active --quiet av1-pipeline-smb-helper.socket
echo "Lokale en SMB-bibliotheekbronnen zijn gereed via de begrensde helpersocket."
