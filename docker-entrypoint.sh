#!/bin/sh
set -eu

mkdir -p /app/cache

if [ ! -f /app/auth.json ]; then
  cat > /app/auth.json <<'EOF'
{
  "version": 2,
  "accounts": [
    {
      "username": "admin",
      "password": "giahuy2712",
      "role": "admin"
    }
  ]
}
EOF
fi

HOST_VALUE="${APP_HOST:-0.0.0.0}"
PORT_VALUE="${APP_PORT:-8080}"

exec python server.py --host "$HOST_VALUE" --port "$PORT_VALUE"
