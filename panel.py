import os, json, uuid, subprocess, threading, base64, hmac, html, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
WS_PATH = os.environ.get("WS_PATH", "/ws")
XHTTP_PATH = os.environ.get("XHTTP_PATH", "/xhttp")
ENV_UUID = os.environ.get("UUID") or str(uuid.uuid4())
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or ENV_UUID
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
CONF = "/etc/xray.json"

DATA_DIR = os.environ.get("DATA_DIR", "/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    open(os.path.join(DATA_DIR, ".t"), "w").close()
except Exception:
    DATA_DIR = "/tmp"
DATA_FILE = os.path.join(DATA_DIR, "clients.json")

lock = threading.Lock()
xray_proc = None


def load_clients():
    try:
        with open(DATA_FILE) as f:
            clients = json.load(f)
    except Exception:
        clients = []
    if not any(c["id"] == ENV_UUID for c in clients):
        clients.insert(0, {"name": "main", "id": ENV_UUID})
    return clients


def save_clients(clients):
    with open(DATA_FILE, "w") as f:
        json.dump(clients, f)


clients = load_clients()
save_clients(clients)


def write_config():
    cl = [{"id": c["id"]} for c in clients]
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "port": PORT, "listen": "0.0.0.0", "protocol": "vless",
                "settings": {
                    "clients": cl, "decryption": "none",
                    "fallbacks": [
                        {"path": WS_PATH, "dest": 10001},
                        {"path": XHTTP_PATH, "dest": 10002},
                        {"dest": 10003},
                    ],
                },
                "streamSettings": {"network": "tcp"},
            },
            {
                "port": 10001, "listen": "127.0.0.1", "protocol": "vless",
                "settings": {"clients": cl, "decryption": "none"},
                "streamSettings": {"network": "ws", "wsSettings": {"path": WS_PATH}},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "port": 10002, "listen": "127.0.0.1", "protocol": "vless",
                "settings": {"clients": cl, "decryption": "none"},
                "streamSettings": {"network": "xhttp",
                                   "xhttpSettings": {"path": XHTTP_PATH, "mode": "auto"}},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
        ],
        "outbounds": [{"protocol": "freedom"}],
    }
    with open(CONF, "w") as f:
        json.dump(cfg, f)


def restart_xray():
    global xray_proc
    with lock:
        write_config()
        if xray_proc and xray_proc.poll() is None:
            xray_proc.terminate()
            try:
                xray_proc.wait(5)
            except Exception:
                xray_proc.kill()
        xray_proc = subprocess.Popen(["xray", "run", "-c", CONF])


def links(c, host):
    q = urllib.parse.quote
    name = q(c["name"])
    base = f"vless://{c['id']}@{host}:443?encryption=none&security=tls&sni={host}&fp=chrome&host={host}"
    ws = f"{base}&type=ws&path={q(WS_PATH, safe='')}#{name}-WS"
    xh = f"{base}&type=xhttp&mode=auto&path={q(XHTTP_PATH, safe='')}#{name}-XHTTP"
    return ws, xh


def page(host):
    e = html.escape
    rows = ""
    for c in clients:
        ws, xh = links(c, host)
        rows += f"""<div class="card"><b>{e(c['name'])}</b> <small>{c['id']}</small>
<div class="l"><input readonly value="{e(ws)}"><button onclick="cp(this)">WS کپی</button></div>
<div class="l"><input readonly value="{e(xh)}"><button onclick="cp(this)">XHTTP کپی</button></div>
<form method="post" action="/panel/del" onsubmit="return confirm('حذف شود؟')">
<input type="hidden" name="id" value="{c['id']}"><button class="d">حذف</button></form></div>"""
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Panel</title>
<style>body{{font-family:sans-serif;background:#111;color:#eee;margin:0;padding:12px}}
.card{{background:#1c1c24;border-radius:10px;padding:12px;margin:10px 0}}
small{{color:#888;display:block;direction:ltr;font-size:11px}}
.l{{display:flex;gap:6px;margin:8px 0;direction:ltr}}
input{{flex:1;min-width:0;background:#000;color:#9f9;border:1px solid #333;border-radius:6px;padding:8px}}
button{{background:#6d28d9;color:#fff;border:0;border-radius:6px;padding:8px 12px}}
.d{{background:#b91c1c}}form.n{{display:flex;gap:6px}}</style></head><body>
<h2>پنل کانفیگ</h2>
<form class="n" method="post" action="/panel/add"><input name="name" placeholder="نام کاربر" required><button>ساخت کانفیگ</button></form>
{rows}
<script>function cp(b){{var i=b.parentNode.querySelector('input');i.select();
navigator.clipboard.writeText(i.value).then(()=>{{b.textContent='✓'}}).catch(()=>{{document.execCommand('copy')}})}}</script>
</body></html>"""


DECOY = "<!doctype html><html><head><meta charset='utf-8'><title>Welcome</title></head><body style='font-family:sans-serif;text-align:center;margin-top:20vh'><h1>It works!</h1></body></html>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def authed(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode().partition(":")
                return hmac.compare_digest(u, ADMIN_USER) and hmac.compare_digest(p, ADMIN_PASSWORD)
            except Exception:
                return False
        return False

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def host(self):
        return (os.environ.get("DOMAIN") or self.headers.get("X-Forwarded-Host")
                or self.headers.get("Host", "")).split(":")[0]

    def do_GET(self):
        if self.path.startswith("/panel"):
            if not self.authed():
                return self.send(401, "Unauthorized", extra={"WWW-Authenticate": 'Basic realm="panel"'})
            return self.send(200, page(self.host()))
        self.send(200, DECOY)

    def do_POST(self):
        if not self.path.startswith("/panel") or not self.authed():
            return self.send(401, "Unauthorized", extra={"WWW-Authenticate": 'Basic realm="panel"'})
        n = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(n).decode())
        global clients
        if self.path == "/panel/add":
            name = (form.get("name", ["user"])[0].strip() or "user")[:30]
            clients.append({"name": name, "id": str(uuid.uuid4())})
        elif self.path == "/panel/del":
            cid = form.get("id", [""])[0]
            if len(clients) > 1:
                clients = [c for c in clients if c["id"] != cid]
        save_clients(clients)
        restart_xray()
        self.send(303, "", extra={"Location": "/panel"})


if __name__ == "__main__":
    restart_xray()
    print(f"Panel: https://<your-domain>/panel  user={ADMIN_USER}  password={'(ADMIN_PASSWORD)' if os.environ.get('ADMIN_PASSWORD') else ENV_UUID}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 10003), H).serve_forever()
