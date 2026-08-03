#!/usr/bin/env bash
# Install a split-deploy systemd unit (edge on Pi, or server on LAN PC).
# Usage (from gate-system/):
#   sudo ./deploy/systemd/install.sh edge
#   sudo ./deploy/systemd/install.sh server
set -euo pipefail

ROLE="${1:-}"
if [[ "$ROLE" != "edge" && "$ROLE" != "server" ]]; then
  echo "Usage: sudo $0 edge|server" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UNIT_SRC="$SCRIPT_DIR/gate-system-${ROLE}.service"
UNIT_DST="/etc/systemd/system/gate-system-${ROLE}.service"

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing unit file: $UNIT_SRC" >&2
  exit 1
fi

if [[ "$ROLE" == "edge" && ! -f "$GATE_ROOT/.env.edge" ]]; then
  echo "Missing $GATE_ROOT/.env.edge (copy from deploy/.env.edge.example)" >&2
  exit 1
fi

if [[ "$ROLE" == "server" && ! -f "$GATE_ROOT/.env" ]]; then
  echo "Missing $GATE_ROOT/.env (copy from .env.example)" >&2
  exit 1
fi

tmp="$(mktemp)"
sed "s|WorkingDirectory=/opt/gate-system|WorkingDirectory=${GATE_ROOT}|g" "$UNIT_SRC" >"$tmp"
install -m 644 "$tmp" "$UNIT_DST"
rm -f "$tmp"

systemctl daemon-reload
systemctl enable "gate-system-${ROLE}.service"
systemctl start "gate-system-${ROLE}.service"
systemctl --no-pager --full status "gate-system-${ROLE}.service" || true

echo "Installed and started gate-system-${ROLE}.service (WorkingDirectory=${GATE_ROOT})"
