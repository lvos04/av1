#!/usr/bin/env bash
# Installeert één gecontroleerde upstream-release voor Dolby Vision-profiel 7.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Draai dit script met sudo of als root." >&2
    exit 1
fi

DOVI_VERSION=2.3.3
DOVI_ARCHIVE="dovi_tool-${DOVI_VERSION}-x86_64-unknown-linux-musl.tar.gz"
DOVI_SHA256=5dae82cb2becd3b9fd726127f936a8d32635e60746d16238fdfded12aa05988c
DOVI_URL="https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_VERSION}/${DOVI_ARCHIVE}"
DOVI_TMP=$(mktemp -d /var/tmp/av1-pipeline-dovi.XXXXXX)

case "$DOVI_TMP" in
    /var/tmp/av1-pipeline-dovi.*) ;;
    *) echo "Onverwacht tijdelijk pad: $DOVI_TMP" >&2; exit 1 ;;
esac
trap 'rm -rf -- "$DOVI_TMP"' EXIT

curl -fL --proto '=https' --tlsv1.2 -o "$DOVI_TMP/$DOVI_ARCHIVE" "$DOVI_URL"
printf '%s  %s\n' "$DOVI_SHA256" "$DOVI_TMP/$DOVI_ARCHIVE" | sha256sum -c -
tar -xzf "$DOVI_TMP/$DOVI_ARCHIVE" -C "$DOVI_TMP" ./dovi_tool
"$DOVI_TMP/dovi_tool" --version | grep -Fx "dovi_tool $DOVI_VERSION"
install -o root -g root -m 0755 "$DOVI_TMP/dovi_tool" /usr/local/bin/dovi_tool

echo "dovi_tool $DOVI_VERSION gecontroleerd en geïnstalleerd."
