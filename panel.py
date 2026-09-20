#!/usr/bin/env python3
"""
Railway Xray panel
  nginx  (PORT)      -> routes by path: ws / xhttp / httpupgrade / vmess / trojan / panel / decoy
  xray               -> listens on 127.0.0.1 for the HTTP transports + 0.0.0.0:TCP_APP_PORT for Reality
  panel.py (10003)   -> web panel (JSON API) + subscription endpoint
"""
import base64
import collections
import hashlib
import hmac
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
START = time.time()
PANEL_VERSION = "2.1.0"


# ───────────────────────── settings from environment ─────────────────────────
def env(name, default=""):
    return os.environ.get(name) or default


def env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def norm_path(value, default):
    p = (value or default).strip()
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/") or default
    # only safe characters: the value ends up inside the nginx config
    return p if re.fullmatch(r"/[A-Za-z0-9/_\-.]*", p) else default


PORT = env_int("PORT", 8080)
PANEL_PORT = 10003
WS_PATH = norm_path(os.environ.get("WS_PATH"), "/ws")
XHTTP_PATH = norm_path(os.environ.get("XHTTP_PATH"), "/xhttp")
HU_PATH = norm_path(os.environ.get("HU_PATH"), "/hu")
VMESS_PATH = norm_path(os.environ.get("VMESS_PATH"), "/vmess")
TROJAN_PATH = norm_path(os.environ.get("TROJAN_PATH"), "/trojan")

# Railway TCP Proxy: the port Xray/Reality listens on + the public host/port Railway gives you
TCP_APP_PORT = env_int("RAILWAY_TCP_APPLICATION_PORT", env_int("TCP_APP_PORT", 9000))
TCP_HOST = env("TCP_HOST") or env("RAILWAY_TCP_PROXY_DOMAIN")
TCP_PUBLIC_PORT = env("TCP_PORT") or env("RAILWAY_TCP_PROXY_PORT")
API_PORT = 10085  # Xray stats API (127.0.0.1 only)
BUILTIN_PORTS = {PORT, 10003, TCP_APP_PORT, API_PORT, 10001, 10002, 10004, 10005, 10006}

CONF = "/tmp/xray.json"
NGINX_CONF = "/tmp/nginx.conf"
PANEL_HTML = os.path.join(APP_DIR, "panel.html")
PUBLIC_DIR = os.path.join(APP_DIR, "public")

DATA_DIR = env("DATA_DIR", "/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    _t = os.path.join(DATA_DIR, ".t")
    open(_t, "w").close()
    os.remove(_t)
except Exception:
    DATA_DIR = "/tmp"
STATE_FILE = os.path.join(DATA_DIR, "state.json")

PRIVATE_NETS = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
]

lock = threading.RLock()
state_lock = threading.RLock()
sessions = {}
login_fails = {}
SESSION_TTL = 12 * 3600
LOGIN_FAIL_WINDOW = 600


def prune_sessions():
    """Sessions/login_fails only ever grew (never pruned), leaking memory over long
    uptimes. Drop anything that's already expired/stale."""
    now = time.time()
    for t, exp in list(sessions.items()):
        if exp <= now:
            sessions.pop(t, None)
    for ip, times in list(login_fails.items()):
        recent = [x for x in times if now - x < LOGIN_FAIL_WINDOW]
        if recent:
            login_fails[ip] = recent
        else:
            login_fails.pop(ip, None)


# ───────────────────────── persistent state ─────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        st = {}
    st.setdefault("uuid", env("UUID") or str(uuid.uuid4()))
    main_id = env("UUID") or st["uuid"]
    st.setdefault("admin_user", env("ADMIN_USER", "admin"))
    st.setdefault("admin_pass", env("ADMIN_PASSWORD") or main_id)
    st.setdefault("address", "")
    st.setdefault("reality_sni", "www.microsoft.com")
    st.setdefault("clients", [])
    st.setdefault("inbounds", [])
    st["inbounds"] = [i for i in st["inbounds"] if i.get("tag")]
    st["clients"] = [c for c in st["clients"] if c.get("id") and c.get("name")]
    if not any(c["id"] == main_id for c in st["clients"]):
        st["clients"].insert(0, {"name": "main", "id": main_id})
    return st


state = load_state()
MAIN_UUID = env("UUID") or state["uuid"]
SECRET = env("SECRET") or MAIN_UUID


def save_state():
    with state_lock:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)


try:
    save_state()
except Exception as e:  # read-only disk etc. - keep running
    print("could not write state:", e, flush=True)


def sub_token(cid):
    """Stable per-user subscription token, derived from SECRET so it survives redeploys."""
    return hmac.new(SECRET.encode(), b"sub:" + cid.encode(), hashlib.sha256).hexdigest()[:32]


def client_status(c, now=None):
    """active | expired | quota  (quota/expire 0 = unlimited)."""
    now = now or time.time()
    exp, quota = c.get("expire_at", 0), c.get("quota", 0)
    if exp and now >= exp:
        return "expired"
    if quota and c.get("up", 0) + c.get("down", 0) >= quota:
        return "quota"
    return "active"


def active_clients():
    with state_lock:
        return [c for c in state["clients"] if client_status(c) == "active"]


# ───────────────────────── Reality keys ─────────────────────────
REALITY = {"priv": None, "pub": None, "sid": ""}


def x25519(priv=None):
    cmd = ["xray", "x25519"] + (["-i", priv] if priv else [])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except Exception as e:
        print("xray x25519 failed:", e, flush=True)
        return None, None
    vals = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            vals[k.strip().lower()] = v.strip()
    p = next((v for k, v in vals.items() if "private" in k), None)
    pub = next((v for k, v in vals.items() if "public" in k or "password" in k), None)
    return p, pub


def init_reality():
    priv = env("REALITY_PRIVATE_KEY")
    if not priv:
        seed = hashlib.sha256((SECRET + ":reality").encode()).digest()
        priv = base64.urlsafe_b64encode(seed).decode().rstrip("=")
    _, pub = x25519(priv)
    sid = env("REALITY_SHORT_ID")
    if not re.fullmatch(r"([0-9a-fA-F]{2}){1,8}", sid or ""):
        sid = hashlib.sha256((SECRET + ":sid").encode()).hexdigest()[:16]
    REALITY.update(priv=priv, pub=pub, sid=sid)
    if not pub:
        print("WARNING: could not derive the Reality public key; TCP link disabled", flush=True)


# ───────────────────────── xray config + process ─────────────────────────
def user_entries(proto, clients, flow=None):
    # email = client id: it is the key Xray's per-user traffic counters use
    if proto == "trojan":
        return [{"password": c["id"], "email": c["id"]} for c in clients]
    out = [{"id": c["id"], "email": c["id"]} for c in clients]
    if flow:
        for u in out:
            u["flow"] = flow
    return out


def custom_inbound(ib, clients, sniff):
    proto, net, sec = ib["protocol"], ib["network"], ib["security"]
    flow = "xtls-rprx-vision" if (sec == "reality" and net == "tcp") else None
    settings = {"clients": user_entries(proto, clients, flow)}
    if proto == "vless":
        settings["decryption"] = "none"
    stream = {"network": net}
    if net == "ws":
        stream["wsSettings"] = {"path": ib["path"]}
    elif net == "xhttp":
        stream["xhttpSettings"] = {"path": ib["path"], "mode": "auto"}
    elif net == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": ib["path"]}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": ib["path"].strip("/")}
    if sec == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "show": False, "dest": f"{ib['sni']}:443", "xver": 0, "serverNames": [ib["sni"]],
            "privateKey": REALITY["priv"], "shortIds": [REALITY["sid"]]}
        listen, port = "0.0.0.0", ib["port"]
    else:  # TLS is terminated by the platform edge, nginx routes the path here
        listen, port = "127.0.0.1", ib["lport"]
    return {"tag": ib["tag"], "listen": listen, "port": port, "protocol": proto,
            "settings": settings, "streamSettings": stream, "sniffing": sniff}


def build_config():
    with state_lock:
        clients = active_clients()          # expired / over-quota users are left out
        sni = state["reality_sni"]
        customs = [dict(i) for i in state["inbounds"]]
    xray_info["active"] = frozenset(c["id"] for c in clients)
    ids = [{"id": c["id"], "email": c["id"]} for c in clients]
    # routeOnly: the sniffed domain is used for routing only; destination is not rewritten (less DNS work)
    sniff = {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": True}

    def local(tag, port, proto, settings, stream):
        return {"tag": tag, "listen": "127.0.0.1", "port": port, "protocol": proto,
                "settings": settings, "streamSettings": stream, "sniffing": sniff}

    inbounds = [
        local("vless-ws", 10001, "vless", {"clients": ids, "decryption": "none"},
              {"network": "ws", "wsSettings": {"path": WS_PATH}}),
        # XHTTP behind an HTTP proxy: the server accepts every mode, the client uses packet-up
        local("vless-xhttp", 10002, "vless", {"clients": ids, "decryption": "none"},
              {"network": "xhttp", "xhttpSettings": {"path": XHTTP_PATH, "mode": "auto"}}),
        local("vless-hu", 10006, "vless", {"clients": ids, "decryption": "none"},
              {"network": "httpupgrade", "httpupgradeSettings": {"path": HU_PATH}}),
        local("vmess-ws", 10004, "vmess", {"clients": ids},
              {"network": "ws", "wsSettings": {"path": VMESS_PATH}}),
        local("trojan-ws", 10005, "trojan", {"clients": user_entries("trojan", clients)},
              {"network": "ws", "wsSettings": {"path": TROJAN_PATH}}),
    ]
    if REALITY["priv"]:
        inbounds.append({
            "tag": "vless-reality", "listen": "0.0.0.0", "port": TCP_APP_PORT, "protocol": "vless",
            "settings": {"clients": user_entries("vless", clients, "xtls-rprx-vision"), "decryption": "none"},
            "streamSettings": {
                "network": "tcp", "security": "reality",
                "realitySettings": {
                    "show": False, "dest": f"{sni}:443", "xver": 0, "serverNames": [sni],
                    "privateKey": REALITY["priv"], "shortIds": [REALITY["sid"]],
                },
            },
            "sniffing": sniff,
        })
    for ib in customs:
        if ib["security"] == "reality" and not REALITY["priv"]:
            continue
        inbounds.append(custom_inbound(ib, clients, sniff))
    inbounds.append({"tag": "api", "listen": "127.0.0.1", "port": API_PORT, "protocol": "dokodemo-door",
                     "settings": {"address": "127.0.0.1"}})
    return {
        "log": {"loglevel": "warning"},
        "stats": {},
        "api": {"tag": "api", "services": ["StatsService"]},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
        # cached DoH lookups (falls back to the system resolver) + IPv4 first = faster connection setup
        "dns": {"servers": ["https+local://1.1.1.1/dns-query", "localhost"], "queryStrategy": "UseIPv4"},
        "inbounds": inbounds,
        "outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}},
                      {"tag": "block", "protocol": "blackhole"}],
        # users must not be able to reach Railway's private network or this container itself
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
            {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
            {"type": "field", "domain": ["domain:railway.internal", "domain:localhost"], "outboundTag": "block"},
            {"type": "field", "ip": PRIVATE_NETS, "outboundTag": "block"},
        ]},
    }


xray_proc = None
xray_info = {"restarts": 0, "since": 0.0, "active": None}


def _spawn_xray():
    global xray_proc
    with open(CONF, "w") as f:
        json.dump(build_config(), f)
    try:
        xray_proc = subprocess.Popen(["xray", "run", "-c", CONF])
        xray_info["since"] = time.time()
        xray_info["restarts"] += 1
    except Exception as e:
        print("cannot start xray:", e, flush=True)
        xray_proc = None


def restart_xray():
    global xray_proc
    with lock:
        if xray_proc and xray_proc.poll() is None:
            try:
                collect_usage()  # don't lose the counters that die with the process
            except Exception:
                pass
            xray_proc.terminate()
            try:
                xray_proc.wait(5)
            except Exception:
                xray_proc.kill()
        _spawn_xray()


def xray_running():
    return bool(xray_proc and xray_proc.poll() is None)


# ───────────────────────── traffic quota / expiry ─────────────────────────
def xray_stats():
    """{email: {"up": bytes, "down": bytes}} from Xray's stats API; counters reset on read."""
    try:
        r = subprocess.run(["xray", "api", "statsquery", f"--server=127.0.0.1:{API_PORT}",
                            "-pattern", "user>>>", "-reset"], capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return {}
    res = {}
    for st in data.get("stat") or []:
        p = str(st.get("name", "")).split(">>>")
        if len(p) == 4 and p[0] == "user":
            try:
                v = int(st.get("value") or 0)
            except (TypeError, ValueError):
                continue
            res.setdefault(p[1], {"up": 0, "down": 0})["up" if p[3] == "uplink" else "down"] += v
    return res


def collect_usage():
    if not xray_running():
        return
    stats = xray_stats()
    if not stats:
        return
    with state_lock:
        for c in state["clients"]:
            s_ = stats.get(c["id"])
            if s_:
                c["up"] = c.get("up", 0) + s_["up"]
                c["down"] = c.get("down", 0) + s_["down"]
        save_state()


def sync_xray():
    """Restart Xray only when the set of allowed users changed (limit hit, expiry, renewal)."""
    with state_lock:
        now_active = frozenset(c["id"] for c in active_clients())
    if now_active != xray_info.get("active"):
        restart_xray()


def usage_loop():
    while True:
        time.sleep(30)
        try:
            collect_usage()
            sync_xray()
            traffic_snapshot()
        except Exception as e:
            print("usage:", e, flush=True)


# ───────────────────────── custom inbounds ─────────────────────────
PROTOS = ("vless", "vmess", "trojan")
NETS = ("ws", "xhttp", "httpupgrade", "grpc", "tcp")


def validate_inbound(d):
    """Caller holds state_lock. Returns (inbound, None) or (None, error message)."""
    name = str(d.get("name", "")).strip()[:30]
    proto, net, sec = (str(d.get(k, "")).strip().lower() for k in ("protocol", "network", "security"))
    if not name:
        return None, "نام اینباند را وارد کنید"
    if len(state["inbounds"]) >= 20:
        return None, "حداکثر ۲۰ اینباند سفارشی مجاز است"
    if proto not in PROTOS or net not in NETS or sec not in ("tls", "reality"):
        return None, "انتخاب نامعتبر است"
    ib = {"tag": "ib-" + uuid.uuid4().hex[:8], "name": name, "protocol": proto, "network": net, "security": sec}
    if sec == "reality":
        if proto != "vless" or net not in ("tcp", "xhttp", "grpc"):
            return None, "Reality فقط با VLESS و ترنسپورت TCP یا XHTTP یا gRPC کار می‌کند"
        if not REALITY["priv"]:
            return None, "کلید Reality آماده نیست"
        try:
            port = int(d.get("port"))
        except (TypeError, ValueError):
            return None, "پورت را وارد کنید"
        taken = BUILTIN_PORTS | {i.get("port") for i in state["inbounds"]}
        if not 1024 <= port <= 65535 or port in taken or 10100 <= port <= 10999:
            return None, "این پورت نامعتبر است یا قبلاً استفاده شده"
        sni = str(d.get("sni") or state["reality_sni"]).strip().lower()
        if not HOST_RE.fullmatch(sni):
            return None, "SNI نامعتبر است"
        ib.update(port=port, sni=sni)
    else:
        if net == "tcp":
            return None, "TCP خام فقط با Reality ممکن است (TLS را دامنه انجام می‌دهد)"
        if proto == "vmess" and net == "xhttp":
            return None, "VMess با XHTTP پشتیبانی نمی‌شود؛ VLESS یا Trojan را انتخاب کنید"
        lports = {i.get("lport") for i in state["inbounds"]}
        ib["lport"] = next(p for p in range(10100, 11000) if p not in lports)
    if net != "tcp":
        raw = str(d.get("path") or "").strip()
        if net == "grpc":
            svc = raw.strip("/") or uuid.uuid4().hex[:8]
            if not re.fullmatch(r"[A-Za-z0-9_\-]{3,40}", svc):
                return None, "Service name فقط حروف انگلیسی/عدد/خط تیره (۳ تا ۴۰ نویسه)"
            path = "/" + svc
        else:
            path = norm_path(raw, "") if raw else "/" + uuid.uuid4().hex[:8]
            if len(path) < 3:
                return None, "مسیر نامعتبر است (حداقل ۲ نویسه؛ فقط حروف انگلیسی، عدد، - _ . /)"
        low = path.lower()
        if low.startswith(("/panel", "/sub")):
            return None, "این مسیر رزرو شده است"
        if sec == "tls":
            used = {p.lower() for p in (WS_PATH, XHTTP_PATH, HU_PATH, VMESS_PATH, TROJAN_PATH)}
            used |= {i["path"].lower() for i in state["inbounds"] if i["security"] == "tls" and i.get("path")}
            if low in used:
                return None, "این مسیر قبلاً استفاده شده است"
        ib["path"] = path
    return ib, None


def apply_inbounds():
    reload_nginx()
    restart_xray()


# ───────────────────────── nginx ─────────────────────────
NGINX_TEMPLATE = r"""
user root;
worker_processes 2;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 8192; }
http {
  include /etc/nginx/mime.types;
  default_type text/html;
  charset utf-8;
  access_log off;
  server_tokens off;
  sendfile on;
  tcp_nodelay on;
  client_max_body_size 0;
  client_body_temp_path /tmp/nginx/client;
  proxy_temp_path /tmp/nginx/proxy;
  fastcgi_temp_path /tmp/nginx/fastcgi;
  uwsgi_temp_path /tmp/nginx/uwsgi;
  scgi_temp_path /tmp/nginx/scgi;
  map $http_upgrade $connection_upgrade { default upgrade; "" close; }

  server {
    listen @@PORT@@ default_server;
    @@H2@@
    server_name _;
    proxy_http_version 1.1;
    proxy_socket_keepalive on;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;

@@LOCATIONS@@
    location ^~ /panel { proxy_pass http://127.0.0.1:@@PANEL@@; proxy_set_header Connection ""; }
    location ^~ /sub/  { proxy_pass http://127.0.0.1:@@PANEL@@; proxy_set_header Connection ""; }
    location / {
      root @@PUBLIC@@;
      try_files /index.html =404;
    }
  }
}
"""

WS_LOCATION = """    location ^~ @@PATH@@ {
      proxy_pass http://127.0.0.1:@@UP@@;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_read_timeout 86400s;
      proxy_send_timeout 86400s;
      proxy_buffering off;
    }
"""

XHTTP_LOCATION = """    location ^~ @@PATH@@ {
      proxy_pass http://127.0.0.1:@@UP@@;
      proxy_set_header Connection "";
      proxy_buffering off;
      proxy_request_buffering off;
      proxy_cache off;
      gzip off;
      proxy_read_timeout 3600s;
      proxy_send_timeout 3600s;
    }
"""

GRPC_LOCATION = """    location ^~ @@PATH@@/ {
      grpc_pass grpc://127.0.0.1:@@UP@@;
      grpc_read_timeout 3600s;
      grpc_send_timeout 3600s;
      client_body_timeout 3600s;
      grpc_set_header Host $http_host;
    }
"""


def write_nginx():
    for d in ("client", "proxy", "fastcgi", "uwsgi", "scgi"):
        os.makedirs(f"/tmp/nginx/{d}", exist_ok=True)
    locs = ""
    for path, up in ((WS_PATH, 10001), (HU_PATH, 10006), (VMESS_PATH, 10004), (TROJAN_PATH, 10005)):
        locs += WS_LOCATION.replace("@@PATH@@", path).replace("@@UP@@", str(up))
    locs += XHTTP_LOCATION.replace("@@PATH@@", XHTTP_PATH).replace("@@UP@@", "10002")
    with state_lock:
        customs = [dict(i) for i in state["inbounds"] if i["security"] == "tls"]
    grpc = False
    for ib in customs:
        tpl = {"grpc": GRPC_LOCATION, "xhttp": XHTTP_LOCATION}.get(ib["network"], WS_LOCATION)
        grpc = grpc or ib["network"] == "grpc"
        locs += tpl.replace("@@PATH@@", ib["path"]).replace("@@UP@@", str(ib["lport"]))
    conf = (NGINX_TEMPLATE.replace("@@PORT@@", str(PORT)).replace("@@PANEL@@", str(PANEL_PORT))
            .replace("@@PUBLIC@@", PUBLIC_DIR).replace("@@LOCATIONS@@", locs)
            .replace("@@H2@@", "http2 on;" if grpc else ""))  # h2c is only needed for gRPC
    with open(NGINX_CONF, "w") as f:
        f.write(conf)


def reload_nginx():
    write_nginx()
    t = subprocess.run(["nginx", "-t", "-c", NGINX_CONF], capture_output=True, text=True)
    if t.returncode != 0:
        print("nginx config test failed:", t.stderr, flush=True)
        return False
    subprocess.run(["nginx", "-c", NGINX_CONF, "-s", "reload"], capture_output=True, text=True)
    return True


nginx_proc = None


def start_nginx():
    global nginx_proc
    write_nginx()
    t = subprocess.run(["nginx", "-t", "-c", NGINX_CONF], capture_output=True, text=True)
    print((t.stdout + t.stderr).strip(), flush=True)
    nginx_proc = subprocess.Popen(["nginx", "-c", NGINX_CONF, "-g", "daemon off;"])


def watchdog():
    last_prune = 0.0
    while True:
        time.sleep(4)
        try:
            if nginx_proc is None or nginx_proc.poll() is not None:
                print("nginx exited - restarting", flush=True)
                start_nginx()
            with lock:
                if not xray_running():
                    print("xray exited - restarting", flush=True)
                    _spawn_xray()
            if time.time() - last_prune > 300:
                prune_sessions()
                last_prune = time.time()
        except Exception as e:
            print("watchdog:", e, flush=True)


# ───────────────────────── links / subscription ─────────────────────────
def q(s):
    return urllib.parse.quote(s, safe="")


def tcp_ready():
    return bool(TCP_HOST and TCP_PUBLIC_PORT and REALITY["pub"])


def custom_link(ib, c, host, addr):
    cid, proto, net, sec = c["id"], ib["protocol"], ib["network"], ib["security"]
    frag = q(f"{c['name']}-{ib['name']}")
    path = ib.get("path", "")
    enc = lambda p: urllib.parse.urlencode(p, quote_via=urllib.parse.quote)
    if sec == "reality":
        p = {"encryption": "none", "security": "reality", "sni": ib["sni"], "fp": "chrome",
             "pbk": REALITY["pub"], "sid": REALITY["sid"], "type": net}
        if net == "tcp":
            p.update(flow="xtls-rprx-vision", headerType="none")
        elif net == "xhttp":
            p.update(path=path, mode="auto")
        else:
            p.update(serviceName=path.strip("/"), mode="gun")
        return f"vless://{cid}@{addr}:{ib['port']}?{enc(p)}#{frag}"
    alpn = {"ws": "http/1.1", "httpupgrade": "http/1.1", "xhttp": "h2,http/1.1", "grpc": "h2"}[net]
    if proto == "vmess":
        v = {"v": "2", "ps": f"{c['name']}-{ib['name']}", "add": addr, "port": "443", "id": cid, "aid": "0",
             "scy": "auto", "net": net, "type": "gun" if net == "grpc" else "none", "host": host,
             "path": path.strip("/") if net == "grpc" else path, "tls": "tls", "sni": host, "alpn": alpn, "fp": "chrome"}
        return "vmess://" + base64.b64encode(json.dumps(v).encode()).decode()
    p = {"encryption": "none"} if proto == "vless" else {}
    p.update(security="tls", sni=host, fp="chrome", alpn=alpn, type=net, host=host)
    if net == "grpc":
        p.update(serviceName=path.strip("/"), mode="gun")
    else:
        p["path"] = path
    if net == "xhttp":
        p["mode"] = "packet-up"
    return f"{proto}://{cid}@{addr}:443?{enc(p)}#{frag}"


def build_links(c, host):
    with state_lock:
        addr = state["address"] or host
        sni = state["reality_sni"]
    cid, n = c["id"], q(c["name"])
    tls = f"security=tls&sni={host}&fp=chrome"
    links = [
        {"key": "vless-ws", "label": "VLESS · WebSocket",
         "url": f"vless://{cid}@{addr}:443?encryption=none&{tls}&alpn=http%2F1.1&type=ws&host={host}&path={q(WS_PATH)}#{n}-VLESS-WS"},
        {"key": "vless-xhttp", "label": "VLESS · XHTTP",
         "url": f"vless://{cid}@{addr}:443?encryption=none&{tls}&alpn=h2%2Chttp%2F1.1&type=xhttp&host={host}&path={q(XHTTP_PATH)}&mode=packet-up#{n}-VLESS-XHTTP"},
        {"key": "vless-hu", "label": "VLESS · HTTPUpgrade",
         "url": f"vless://{cid}@{addr}:443?encryption=none&{tls}&alpn=http%2F1.1&type=httpupgrade&host={host}&path={q(HU_PATH)}#{n}-VLESS-HTTPUpgrade"},
    ]
    vmess = {"v": "2", "ps": f"{c['name']}-VMess-WS", "add": addr, "port": "443", "id": cid, "aid": "0",
             "scy": "auto", "net": "ws", "type": "none", "host": host, "path": VMESS_PATH,
             "tls": "tls", "sni": host, "alpn": "http/1.1", "fp": "chrome"}
    links.append({"key": "vmess-ws", "label": "VMess · WebSocket",
                  "url": "vmess://" + base64.b64encode(json.dumps(vmess).encode()).decode()})
    links.append({"key": "trojan-ws", "label": "Trojan · WebSocket",
                  "url": f"trojan://{cid}@{addr}:443?security=tls&sni={host}&fp=chrome&alpn=http%2F1.1&type=ws&host={host}&path={q(TROJAN_PATH)}#{n}-Trojan-WS"})
    if tcp_ready():
        links.append({"key": "vless-reality", "label": "VLESS · Reality (TCP Proxy)",
                      "url": f"vless://{cid}@{TCP_HOST}:{TCP_PUBLIC_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni={sni}&fp=chrome&pbk={REALITY['pub']}&sid={REALITY['sid']}&type=tcp&headerType=none#{n}-VLESS-Reality-TCP"})
    with state_lock:
        customs = [dict(i) for i in state["inbounds"]]
    for ib in customs:
        if ib["security"] == "reality" and not REALITY["pub"]:
            continue
        links.append({"key": ib["tag"], "label": f"{ib['name']} · {ib['protocol'].upper()} · {ib['network']} · {ib['security']}",
                      "url": custom_link(ib, c, host, addr)})
    return links


# ───────────────────────── system metrics ─────────────────────────
def read_file(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except Exception:
        return None


def kv_file(p):
    d = {}
    txt = read_file(p)
    for line in (txt or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            d[parts[0]] = int(parts[1])
    return d


def host_mem_total():
    for line in (read_file("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal"):
            return int(line.split()[1]) * 1024
    return 0


def mem_info():
    host_total = host_mem_total()
    cur = read_file("/sys/fs/cgroup/memory.current")
    lim = read_file("/sys/fs/cgroup/memory.max")
    stat = kv_file("/sys/fs/cgroup/memory.stat")
    inactive = stat.get("inactive_file", 0)
    if cur is None:  # cgroup v1
        cur = read_file("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        lim = read_file("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        inactive = kv_file("/sys/fs/cgroup/memory/memory.stat").get("total_inactive_file", 0)
    if cur is not None and cur.isdigit():
        used = max(int(cur) - inactive, 0)
        total = int(lim) if lim and lim.isdigit() and 0 < int(lim) < (host_total or 1 << 62) else host_total
    else:  # plain /proc/meminfo
        avail = 0
        for line in (read_file("/proc/meminfo") or "").splitlines():
            if line.startswith("MemAvailable"):
                avail = int(line.split()[1]) * 1024
        total, used = host_total, max(host_total - avail, 0)
    pct = round(used / total * 100, 1) if total else 0
    return {"used": used // 2**20, "total": total // 2**20, "percent": min(pct, 100)}


def disk_info():
    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks
        used = total - st.f_frsize * st.f_bavail
        return {"used": round(used / 2**30, 1), "total": round(total / 2**30, 1),
                "percent": round(used / total * 100, 1) if total else 0}
    except Exception:
        return {"used": 0, "total": 0, "percent": 0}


def net_bytes():
    rx = tx = 0
    for line in (read_file("/proc/net/dev") or "").splitlines()[2:]:
        if ":" not in line:
            continue
        iface, data = line.split(":", 1)
        if iface.strip() == "lo":
            continue
        f = data.split()
        if len(f) >= 9:
            rx += int(f[0])
            tx += int(f[8])
    return rx, tx


def os_info():
    """Pretty OS name from /etc/os-release (present on Alpine & Ubuntu)."""
    m = re.search(r'PRETTY_NAME="?([^"\n]+)"?', read_file("/etc/os-release") or "")
    if m:
        return m.group(1).strip()
    kernel = read_file("/proc/sys/kernel/osrelease")
    return f"Linux {kernel}" if kernel else "Linux"


def cpu_model():
    for line in (read_file("/proc/cpuinfo") or "").splitlines():
        if line.lower().startswith("model name") and ":" in line:
            return line.split(":", 1)[1].strip()
    return ""


def traffic_snapshot():
    """Daily total-traffic snapshot (sum of all users' counters) for the 30-day delta."""
    day = int(time.time() // 86400)
    with state_lock:
        total = sum(c.get("up", 0) + c.get("down", 0) for c in state["clients"])
        snaps = state.setdefault("tsnaps", [])
        changed = False
        if snaps and snaps[-1]["d"] == day:
            if snaps[-1]["t"] != total:
                snaps[-1]["t"] = total
                changed = True
        else:
            snaps.append({"d": day, "t": total})
            del snaps[:-40]
            changed = True
        if changed:
            save_state()


def traffic_30d():
    """{bytes, pct}: traffic in the last ~30 days and its change vs the previous 30 days."""
    day = int(time.time() // 86400)
    with state_lock:
        total = sum(c.get("up", 0) + c.get("down", 0) for c in state["clients"])
        snaps = [dict(s) for s in state.get("tsnaps", [])]

    def ref(days_ago):
        best = None
        for s in snaps:
            age = day - s["d"]
            if days_ago - 1 <= age <= days_ago + 1 and (best is None or age < day - best["d"]):
                best = s
        return best

    r30, r60 = ref(30), ref(60)
    b30 = max(total - r30["t"], 0) if r30 else None
    b60 = max(total - r60["t"], 0) if r60 else None
    pct = round((b30 - b60) / b60 * 100) if (b30 is not None and b60) else None
    return {"bytes": b30, "pct": pct}


class Sampler(threading.Thread):
    daemon = True
    INTERVAL = 2.0

    def __init__(self):
        super().__init__()
        self.hist = collections.deque(maxlen=90)
        self.latest = {}
        self.prev = None          # (time, cpu counter, rx, tx)
        self.cores = os.cpu_count() or 1

    def cpu_counter(self):
        """(counter in seconds, capacity in cores) - cgroup first, /proc/stat as fallback."""
        usage = kv_file("/sys/fs/cgroup/cpu.stat").get("usage_usec")
        if usage is not None:
            cores = float(self.cores)
            mx = (read_file("/sys/fs/cgroup/cpu.max") or "max 100000").split()
            if len(mx) == 2 and mx[0].isdigit() and mx[1].isdigit() and int(mx[1]):
                cores = int(mx[0]) / int(mx[1])
            return usage / 1e6, cores
        first = (read_file("/proc/stat") or "cpu 0 0 0 0").splitlines()[0].split()[1:]
        vals = list(map(int, first))
        busy = sum(vals) - vals[3] - (vals[4] if len(vals) > 4 else 0)
        return busy / 100.0, float(self.cores)  # USER_HZ = 100

    def run(self):
        while True:
            try:
                self.sample()
            except Exception as e:
                print("sampler:", e, flush=True)
            time.sleep(self.INTERVAL)

    def sample(self):
        now = time.time()
        cpu_c, cores = self.cpu_counter()
        rx, tx = net_bytes()
        cpu = rx_s = tx_s = 0.0
        if self.prev:
            dt = max(now - self.prev[0], 0.001)
            cpu = max(0.0, min((cpu_c - self.prev[1]) / dt / max(cores, 0.01) * 100, 100.0))
            rx_s = max(rx - self.prev[2], 0) / dt
            tx_s = max(tx - self.prev[3], 0) / dt
        self.prev = (now, cpu_c, rx, tx)
        m = mem_info()
        point = {"t": int(now), "cpu": round(cpu, 1), "mem": m["percent"], "rx": int(rx_s), "tx": int(tx_s)}
        self.hist.append(point)
        self.latest = {**point, "mem_used": m["used"], "mem_total": m["total"], "rx_total": rx, "tx_total": tx}


sampler = Sampler()


def stats_payload():
    return {
        **sampler.latest,
        "disk": disk_info(),
        "uptime": int(time.time() - START),
        "cores": sampler.cores,
        "hist": {k: [p[k] for p in sampler.hist] for k in ("cpu", "mem", "rx", "tx")},
        "interval": Sampler.INTERVAL,
        "os": os_info(),
        "cpu_model": cpu_model(),
        "version": PANEL_VERSION,
        "traffic30": traffic_30d(),
    }


# ───────────────────────── web panel ─────────────────────────
HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class H(BaseHTTPRequestHandler):
    server_version = "nginx"
    sys_version = ""

    def log_message(self, *a):
        pass

    # ---- helpers
    def send_bytes(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def send_json(self, code, obj, extra=None):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False), extra=extra)

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                return {}
            d = json.loads(self.rfile.read(n) or b"{}")
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def host(self):
        h = env("DOMAIN") or self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        return h.split(",")[0].split(":")[0].strip()

    def client_ip(self):
        xr = self.headers.get("X-Real-IP")
        if xr:
            return xr.strip()
        xf = self.headers.get("X-Forwarded-For", "")
        return xf.split(",")[-1].strip() if xf else self.client_address[0]

    def token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            part = part.strip()
            if part.startswith("sid="):
                return part[4:]
        return None

    def authed(self):
        t = self.token()
        exp = sessions.get(t)
        if exp and exp > time.time():
            sessions[t] = time.time() + SESSION_TTL
            self._sid = t
            return True
        return False

    def cookie(self, value, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"sid={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def auth_headers(self, extra=None):
        # authed() slides the *server-side* session forward on every request, but the
        # cookie's Max-Age was only ever set once at login - so the browser deleted the
        # cookie exactly 12h after login even if the session was still active server-side,
        # silently logging the user out. Re-issue the cookie with every authed response
        # so the two stay in sync.
        h = dict(extra or {})
        if getattr(self, "_sid", None):
            h["Set-Cookie"] = self.cookie(self._sid, SESSION_TTL)
        return h

    # ---- GET
    def do_GET(self):
        path, _, _qs = self.path.partition("?")
        if path in ("/panel", "/panel/"):
            try:
                with open(PANEL_HTML, "rb") as f:
                    return self.send_bytes(200, f.read(), "text/html; charset=utf-8")
            except Exception:
                return self.send_bytes(500, "panel.html is missing", "text/plain; charset=utf-8")

        if path.startswith("/sub/"):
            tok = path[5:].strip("/")
            with state_lock:
                clients = list(state["clients"])
            for c in clients:
                if hmac.compare_digest(sub_token(c["id"]), tok):
                    body = base64.b64encode("\n".join(l["url"] for l in build_links(c, self.host())).encode())
                    title = base64.b64encode(f"{c['name']}".encode()).decode()
                    return self.send_bytes(200, body, "text/plain; charset=utf-8", {
                        "Profile-Title": f"base64:{title}",
                        "Profile-Update-Interval": "12",
                        "Subscription-Userinfo": f"upload={c.get('up', 0)}; download={c.get('down', 0)}; total={c.get('quota', 0)}; expire={c.get('expire_at', 0)}",
                    })
            return self.send_bytes(404, "not found", "text/plain; charset=utf-8")

        if path.startswith("/panel/api/"):
            if not self.authed():
                return self.send_json(401, {"error": "unauthorized"})
            if path == "/panel/api/stats":
                return self.send_json(200, stats_payload(), self.auth_headers())
            if path == "/panel/api/state":
                return self.send_json(200, self.state_payload(), self.auth_headers())
        self.send_bytes(404, "not found", "text/plain; charset=utf-8")

    def state_payload(self):
        host = self.host()
        with state_lock:
            clients = list(state["clients"])
            settings = {"user": state["admin_user"], "address": state["address"], "reality_sni": state["reality_sni"]}
            customs = [dict(i) for i in state["inbounds"]]
        now = time.time()
        out = []
        for c in clients:
            exp = c.get("expire_at", 0)
            out.append({"name": c["name"], "id": c["id"], "main": c["id"] == MAIN_UUID,
                        "sub_url": f"https://{host}/sub/{sub_token(c['id'])}",
                        "links": build_links(c, host),
                        "quota": c.get("quota", 0), "up": c.get("up", 0), "down": c.get("down", 0),
                        "expire_at": exp, "status": client_status(c, now),
                        "days_left": int((exp - now) // 86400) + 1 if exp > now else 0})
        protocols = [
            {"key": "vless-ws", "name": "VLESS · WebSocket", "path": WS_PATH, "note": "سازگارترین گزینه؛ روی اغلب کلاینت‌ها کار می‌کند"},
            {"key": "vless-xhttp", "name": "VLESS · XHTTP", "path": XHTTP_PATH, "note": "حالت packet-up؛ مخصوص عبور از پروکسی‌های HTTP"},
            {"key": "vless-hu", "name": "VLESS · HTTPUpgrade", "path": HU_PATH, "note": "سبک و سریع، شبیه WebSocket"},
            {"key": "vmess-ws", "name": "VMess · WebSocket", "path": VMESS_PATH, "note": "برای کلاینت‌های قدیمی‌تر"},
            {"key": "trojan-ws", "name": "Trojan · WebSocket", "path": TROJAN_PATH, "note": "رمز = UUID کاربر"},
        ]
        return {
            "host": host, "clients": out, "protocols": protocols, "settings": settings,
            "version": PANEL_VERSION,
            "tcp": {"enabled": tcp_ready(), "host": TCP_HOST, "port": TCP_PUBLIC_PORT,
                    "app_port": TCP_APP_PORT, "sni": settings["reality_sni"],
                    "key_ok": bool(REALITY["pub"])},
            "xray": {"running": xray_running(), "restarts": xray_info["restarts"]},
            "inbounds": [{"tag": i["tag"], "name": i["name"], "protocol": i["protocol"], "network": i["network"],
                          "security": i["security"], "path": i.get("path", ""), "port": i.get("port")} for i in customs],
        }

    # ---- POST
    def do_POST(self):
        path = self.path.partition("?")[0]
        d = self.read_json()

        if path == "/panel/api/login":
            ip = self.client_ip()
            now = time.time()
            recent = [t for t in login_fails.get(ip, []) if now - t < LOGIN_FAIL_WINDOW]
            login_fails[ip] = recent
            if len(recent) >= 10:
                return self.send_json(429, {"error": "تلاش‌های ناموفق زیاد بود؛ چند دقیقه بعد دوباره امتحان کنید"})
            u, p = str(d.get("user", "")).encode(), str(d.get("pass", "")).encode()
            with state_lock:
                ok_u = hmac.compare_digest(u, state["admin_user"].encode())
                ok_p = hmac.compare_digest(p, state["admin_pass"].encode())
            if ok_u and ok_p:
                tok = uuid.uuid4().hex + uuid.uuid4().hex
                sessions[tok] = now + SESSION_TTL
                login_fails.pop(ip, None)
                return self.send_json(200, {"ok": True}, {"Set-Cookie": self.cookie(tok, SESSION_TTL)})
            recent.append(now)
            return self.send_json(401, {"error": "نام کاربری یا رمز عبور اشتباه است"})

        if not self.authed():
            return self.send_json(401, {"error": "unauthorized"})

        if path == "/panel/api/logout":
            sessions.pop(self.token(), None)
            return self.send_json(200, {"ok": True}, {"Set-Cookie": self.cookie("", 0)})

        if path == "/panel/api/clients":
            name = str(d.get("name", "")).strip()[:30]
            if not name:
                return self.send_json(400, {"error": "نام کاربر را وارد کنید"})
            try:
                gb = float(d.get("quota_gb") or 0)
                days = int(float(d.get("days") or 0))
            except (TypeError, ValueError):
                return self.send_json(400, {"error": "حجم یا روز نامعتبر است"})
            if not (0 <= gb <= 100000 and 0 <= days <= 3650):
                return self.send_json(400, {"error": "حجم یا روز نامعتبر است"})
            with state_lock:
                if len(state["clients"]) >= 100:
                    return self.send_json(400, {"error": "حداکثر ۱۰۰ کاربر مجاز است"})
                state["clients"].append({"name": name, "id": str(uuid.uuid4()), "quota": int(gb * 2**30),
                                         "expire_at": int(time.time() + days * 86400) if days else 0,
                                         "up": 0, "down": 0})
                save_state()
            restart_xray()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        if path == "/panel/api/clients/update":
            cid = str(d.get("id", ""))
            try:
                gb = float(d["quota_gb"]) if d.get("quota_gb") not in (None, "") else None
                days = int(float(d["days"])) if d.get("days") not in (None, "") else None
            except (TypeError, ValueError):
                return self.send_json(400, {"error": "مقدار نامعتبر است"})
            if (gb is not None and not 0 <= gb <= 100000) or (days is not None and not 0 <= days <= 3650):
                return self.send_json(400, {"error": "مقدار نامعتبر است"})
            with state_lock:
                c = next((x for x in state["clients"] if x["id"] == cid), None)
                if not c:
                    return self.send_json(404, {"error": "کاربر پیدا نشد"})
                if gb is not None:
                    c["quota"] = int(gb * 2**30)
                if days is not None:  # renew: N days counted from today, 0 = unlimited
                    c["expire_at"] = int(time.time() + days * 86400) if days else 0
                if d.get("reset_usage"):
                    c["up"] = c["down"] = 0
                save_state()
            sync_xray()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        if path == "/panel/api/inbounds":
            with state_lock:
                ib, err = validate_inbound(d)
                if err:
                    return self.send_json(400, {"error": err})
                state["inbounds"].append(ib)
                save_state()
            apply_inbounds()
            return self.send_json(200, {"ok": True, "tag": ib["tag"]}, self.auth_headers())

        if path == "/panel/api/inbounds/delete":
            tag = str(d.get("tag", ""))
            with state_lock:
                before = len(state["inbounds"])
                state["inbounds"] = [i for i in state["inbounds"] if i["tag"] != tag]
                changed = len(state["inbounds"]) != before
                if changed:
                    save_state()
            if changed:
                apply_inbounds()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        if path == "/panel/api/clients/delete":
            cid = str(d.get("id", ""))
            if cid == MAIN_UUID:
                return self.send_json(400, {"error": "کاربر اصلی قابل حذف نیست"})
            with state_lock:
                before = len(state["clients"])
                state["clients"] = [c for c in state["clients"] if c["id"] != cid]
                changed = len(state["clients"]) != before
                if changed:
                    save_state()
            if changed:
                restart_xray()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        if path == "/panel/api/settings":
            need_restart = False
            with state_lock:
                u = str(d.get("user", "")).strip()
                if u:
                    state["admin_user"] = u[:40]
                p = str(d.get("pass", ""))
                if p:
                    if len(p) < 6:
                        return self.send_json(400, {"error": "رمز عبور باید حداقل ۶ نویسه باشد"})
                    state["admin_pass"] = p
                if "address" in d:
                    a = str(d["address"]).strip()
                    if a and not HOST_RE.fullmatch(a):
                        return self.send_json(400, {"error": "آدرس نامعتبر است"})
                    state["address"] = a
                if "reality_sni" in d:
                    s = str(d["reality_sni"]).strip().lower()
                    if not HOST_RE.fullmatch(s):
                        return self.send_json(400, {"error": "SNI نامعتبر است"})
                    if s != state["reality_sni"]:
                        state["reality_sni"] = s
                        need_restart = True
                save_state()
            if need_restart:
                restart_xray()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        if path == "/panel/api/restart":
            restart_xray()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        self.send_bytes(404, "not found", "text/plain; charset=utf-8")


# ───────────────────────── main ─────────────────────────
def shutdown(*_):
    for p in (nginx_proc, xray_proc):
        try:
            if p and p.poll() is None:
                p.terminate()
        except Exception:
            pass
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    init_reality()
    start_nginx()
    restart_xray()
    sampler.start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=usage_loop, daemon=True).start()
    with state_lock:
        print(f"NullGate: https://<your-domain>/panel   user={state['admin_user']}", flush=True)
    print(f"TCP proxy: app port {TCP_APP_PORT}, public {TCP_HOST or '-'}:{TCP_PUBLIC_PORT or '-'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PANEL_PORT), H).serve_forever()


if __name__ == "__main__":
    main()
