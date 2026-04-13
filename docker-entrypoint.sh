#!/bin/sh
set -eu

mkdir -p /app/cache

if [ ! -f /app/web/index.html ]; then
  echo "[fatal] Missing /app/web/index.html. Docker image/source bundle is incomplete." >&2
  echo "[fatal] Rebuild from a complete project folder that includes web/." >&2
  exit 1
fi

if [ ! -f /app/tools/ffdec_full/ffdec.sh ]; then
  echo "[fatal] Missing /app/tools/ffdec_full/ffdec.sh. This full Docker bundle requires FFDec files." >&2
  exit 1
fi

if ! command -v java >/dev/null 2>&1; then
  echo "[fatal] Java runtime not found in container. FFDec frame rendering will not work." >&2
  exit 1
fi

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
export FFDEC_BIN="${FFDEC_BIN:-/app/tools/ffdec_full/ffdec.sh}"

exec python server.py --host "$HOST_VALUE" --port "$PORT_VALUE"
