#!/bin/sh
# Fail the Docker build early when DNS cannot resolve PyPI.
set -eu

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "python missing; skipping DNS probe"
  exit 0
fi

if ! $PY -c "import socket; socket.getaddrinfo('pypi.org', 443)"; then
  echo "DNS lookup failed for pypi.org"
  echo "Hint: set Docker Desktop DNS to your router IP (see deploy/docker-daemon.dns.example.json)"
  exit 1
fi
