# Xray (VLESS + WS + XHTTP) on Railway

1. Push this folder to a GitHub repo.
2. Railway → New Project → Deploy from GitHub repo (Dockerfile is detected automatically).
3. Variables: `UUID` = a fixed UUID (generate at uuidgenerator.net).
4. Settings → Networking → **Generate Domain** (target port = the value of PORT, usually auto-detected).
5. Open the Deploy Logs: the two `vless://` links are printed there. Import them into v2rayNG / Hiddify / Streisand etc.

Optional variables: `WS_PATH` (default /ws), `XHTTP_PATH` (default /xhttp), `DOMAIN` (custom domain).

Tip: for better stability in Iran, use a custom domain behind Cloudflare or try different SNI/fragment settings in the client.
