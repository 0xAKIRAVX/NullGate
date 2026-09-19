#!/bin/bash
set -e

PORT="${PORT:-8080}"
WS_PATH="${WS_PATH:-/ws}"
XHTTP_PATH="${XHTTP_PATH:-/xhttp}"

if [ -z "$UUID" ]; then
  UUID="$(xray uuid)"
  echo "!! UUID تنظیم نشده بود؛ یکی موقت ساخته شد. بهتر است در Railway متغیر UUID را ثابت کنید."
fi

DOMAIN="${DOMAIN:-$RAILWAY_PUBLIC_DOMAIN}"

cat > /etc/xray.json <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "port": ${PORT},
      "listen": "0.0.0.0",
      "protocol": "vless",
      "settings": {
        "clients": [ { "id": "${UUID}" } ],
        "decryption": "none",
        "fallbacks": [
          { "path": "${WS_PATH}",    "dest": 10001 },
          { "path": "${XHTTP_PATH}", "dest": 10002 },
          { "dest": 10003 }
        ]
      },
      "streamSettings": { "network": "tcp" }
    },
    {
      "port": 10001,
      "listen": "127.0.0.1",
      "protocol": "vless",
      "settings": { "clients": [ { "id": "${UUID}" } ], "decryption": "none" },
      "streamSettings": { "network": "ws", "wsSettings": { "path": "${WS_PATH}" } },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls"] }
    },
    {
      "port": 10002,
      "listen": "127.0.0.1",
      "protocol": "vless",
      "settings": { "clients": [ { "id": "${UUID}" } ], "decryption": "none" },
      "streamSettings": { "network": "xhttp", "xhttpSettings": { "path": "${XHTTP_PATH}", "mode": "auto" } },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls"] }
    }
  ],
  "outbounds": [ { "protocol": "freedom" } ]
}
EOF

# decoy website on 10003
busybox httpd -f -p 127.0.0.1:10003 -h /web &

if [ -n "$DOMAIN" ]; then
  ENC_WS=$(echo "$WS_PATH" | sed 's|/|%2F|g')
  ENC_XH=$(echo "$XHTTP_PATH" | sed 's|/|%2F|g')
  echo "=================== CLIENT LINKS ==================="
  echo "vless://${UUID}@${DOMAIN}:443?encryption=none&security=tls&sni=${DOMAIN}&fp=chrome&type=ws&host=${DOMAIN}&path=${ENC_WS}#Railway-WS"
  echo "vless://${UUID}@${DOMAIN}:443?encryption=none&security=tls&sni=${DOMAIN}&fp=chrome&type=xhttp&mode=auto&host=${DOMAIN}&path=${ENC_XH}#Railway-XHTTP"
  echo "===================================================="
else
  echo "DOMAIN مشخص نیست. در Railway یک Public Domain بسازید و ری‌دیپلوی کنید."
fi

exec xray run -c /etc/xray.json
