#!/bin/sh
# Build-time DNS diagnostic (debug session 359384).
set -eu

echo "[agent-debug] hypothesisId=DNS-A,B,C location=build-dns-check.sh:start message=build_dns_probe"
echo "[agent-debug] resolv.conf:"
cat /etc/resolv.conf 2>/dev/null || echo "[agent-debug] resolv.conf missing"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  PY=""
fi

if [ -n "$PY" ]; then
  if $PY -c "import socket; print('[agent-debug] pypi.org ->', socket.getaddrinfo('pypi.org', 443)[0][4][0])"; then
    echo "[agent-debug] dns_status=OK"
  else
    echo "[agent-debug] dns_status=FAIL host=pypi.org"
    echo "[agent-debug] fix_hint=Set Docker Desktop DNS to your router IP (see deploy/docker-daemon.dns.example.json)"
    exit 1
  fi
else
  echo "[agent-debug] python missing; skipping DNS probe"
fi
