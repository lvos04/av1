#!/usr/bin/env bash
# Installeert alleen de al gebouwde LAN-gatewayservice en herstart de twee
# applicatiediensten. Account en wachtwoord worden daarna in de UI gekozen.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Draai dit script met sudo." >&2
    exit 1
fi

project_dir=${1:-/opt/av1-pipeline}
service_source="$project_dir/systemd/av1-pipeline-frontend.service"
frontend_server="$project_dir/frontend/server.mjs"
if [[ ! -f $service_source || ! -f $frontend_server ]]; then
    echo "LAN-gatewaybestanden ontbreken onder: $project_dir" >&2
    exit 1
fi

temporary_service=$(mktemp)
trap 'rm -f "$temporary_service"' EXIT
escaped_project=$(printf '%s' "$project_dir" | sed 's/[&|]/\\&/g')
sed "s|__PROJECT_DIR__|$escaped_project|g" "$service_source" > "$temporary_service"
install -o root -g root -m 0644 \
    "$temporary_service" /etc/systemd/system/av1-pipeline-frontend.service

systemctl daemon-reload
systemctl restart av1-pipeline-backend.service av1-pipeline-frontend.service
systemctl is-active --quiet av1-pipeline-backend.service
systemctl is-active --quiet av1-pipeline-frontend.service

echo "LAN-gateway geïnstalleerd. Open lokaal: http://127.0.0.1:3000/instellingen"
