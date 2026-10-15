#!/usr/bin/env bash
# Move the standalone cloudflared connector onto the Compose network so
# origin http://nginx:80 resolves (fixes Cloudflare 502 / "lookup nginx").
set -euo pipefail
cd "$(dirname "$0")/.."

redact() { sed -E 's/(--token[[:space:]]+)[^[:space:]]+/\1<redacted>/g'; }

echo "== locate standalone cloudflared =="
mapfile -t CF_IDS < <(docker ps --filter ancestor=cloudflare/cloudflared --format '{{.ID}}' || true)
TOKEN=""
for id in "${CF_IDS[@]:-}"; do
  name="$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')"
  echo "running: $name ($id)"
  cmd="$(docker inspect -f '{{range .Config.Cmd}}{{.}} {{end}}' "$id")"
  echo "  cmd: $(printf '%s' "$cmd" | redact)"
  if [[ "$cmd" == *"--token "* ]]; then
    TOKEN="$(printf '%s' "$cmd" | sed -n 's/.*--token //p' | awk '{print $1}')"
  fi
done

if [[ -z "$TOKEN" ]]; then
  # Host-level docker run wrapper (seen via pgrep) may have exited; try compose env.
  if grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' .env 2>/dev/null; then
    TOKEN="$(grep -E '^CLOUDFLARE_TUNNEL_TOKEN=' .env | head -1 | cut -d= -f2-)"
  fi
fi

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: No tunnel token found. Paste CLOUDFLARE_TUNNEL_TOKEN into gate-system/.env, then re-run."
  exit 1
fi
echo "token_len=${#TOKEN}"

echo "== write CLOUDFLARE_TUNNEL_TOKEN into .env =="
python3 - <<PY
from pathlib import Path
import re
p = Path(".env")
token = """$TOKEN"""
text = p.read_text() if p.exists() else ""
line = f"CLOUDFLARE_TUNNEL_TOKEN={token}"
if re.search(r"^CLOUDFLARE_TUNNEL_TOKEN=.*$", text, re.M):
    text = re.sub(r"^CLOUDFLARE_TUNNEL_TOKEN=.*$", line, text, count=1, flags=re.M)
else:
    text = text.rstrip() + "\n\n# Cloudflare Tunnel (compose profile: tunnel)\n" + line + "\n"
p.write_text(text)
print("env_updated")
PY

echo "== stop standalone cloudflared containers (keep compose-managed names) =="
for id in $(docker ps --filter ancestor=cloudflare/cloudflared --format '{{.ID}}'); do
  name="$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')"
  case "$name" in
    gate-system-cloudflared-1|gate-system-cloudflared-quick-1)
      echo "keep $name"
      ;;
    *)
      echo "stop $name ($id)"
      docker stop "$id" >/dev/null
      ;;
  esac
done

echo "== start compose cloudflared on project network =="
docker compose --profile tunnel up -d cloudflared
sleep 4
docker compose --profile tunnel ps cloudflared

echo "== recent logs (should NOT contain 'lookup nginx') =="
docker compose --profile tunnel logs --tail=30 cloudflared | redact

echo "== origin check from inside cloudflared =="
if docker compose --profile tunnel exec -T cloudflared wget -qO- http://nginx:80/api/payments/healthz 2>/dev/null | head -c 240; then
  echo
else
  # cloudflared image may lack wget; try against published port from host
  curl -sS http://127.0.0.1/api/payments/healthz | head -c 240; echo
fi

echo "== public check =="
curl -sS -k -o /tmp/poaley_root.html -w "public_root:%{http_code}\n" --max-time 25 https://poaleyzedek.net/ || true
head -c 160 /tmp/poaley_root.html 2>/dev/null; echo
curl -sS -k -o /tmp/poaley_pay.json -w "public_pay:%{http_code}\n" --max-time 25 https://poaleyzedek.net/api/payments/healthz || true
head -c 220 /tmp/poaley_pay.json 2>/dev/null; echo

echo "DONE. Reload https://poaleyzedek.net — expect the dashboard, not Cloudflare 502."
