#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Bridge aktualisieren: git pull (lokale internal_config.yaml bleibt erhalten),
# Docker-Build mit Commit-SHA (build_commit in /api/diagnostics), Healthcheck.
# Aufruf im Bridge-Verzeichnis oder von überall: sh ops/deploy.sh
set -eu

cd "$(dirname "$0")/.."
KEEP="$(mktemp)"
cp config/internal_config.yaml "$KEEP"
restore() { cp "$KEEP" config/internal_config.yaml; rm -f "$KEEP"; }
trap restore EXIT

# Lokale Deployment-Einstellungen (Passwort-Hash, Log-Level) kurz beiseite, dann Fast-Forward
git checkout -- config/internal_config.yaml
git pull --ff-only
restore
trap - EXIT

BRIDGE_BUILD_SHA="$(git rev-parse --short HEAD)"
export BRIDGE_BUILD_SHA
echo "Baue Bridge ${BRIDGE_BUILD_SHA} …"
docker compose up -d --build

echo "Warte auf /healthz …"
i=0
while [ "$i" -lt 30 ]; do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" 2>/dev/null; then
        echo "Bridge ${BRIDGE_BUILD_SHA} läuft (healthz 200)."
        exit 0
    fi
    i=$((i + 1))
    sleep 3
done
echo "Bridge antwortet nach 90 s nicht mit healthz 200 – Logs: docker compose logs --tail 100" >&2
exit 1
