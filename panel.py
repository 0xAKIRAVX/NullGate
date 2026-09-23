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
PANEL_VERSION = "2.3.1"

# Protocol keys: built-in inbounds + the Reality TCP link. Each can
# be turned off globally (Settings) or per-user (empty override = follow globals).
PROTO_KEYS = ("vless-ws", "vless-xhttp", "vless-hu", "vmess-ws", "trojan-ws", "vless-reality")
PROTO_LABELS = {
    "vless-ws": "VLESS · WebSocket", "vless-xhttp": "VLESS · XHTTP",
    "vless-hu": "VLESS · HTTPUpgrade", "vmess-ws": "VMess · WebSocket",
    "trojan-ws": "Trojan · WebSocket", "vless-reality": "VLESS · Reality (TCP)",
}
# inbound tag -> protocol key for the built-in inbounds
BUILTIN_TAGS = {"vless-ws": "vless-ws", "vless-xhttp": "vless-xhttp", "vless-hu": "vless-hu",
                "vmess-ws": "vmess-ws", "trojan-ws": "trojan-ws", "vless-reality": "vless-reality"}


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
XRAY_LOG = "/tmp/xray.log"
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
DEFAULT_SESSION_HOURS = 12
LOGIN_FAIL_WINDOW = 600


def sess_ttl():
    """Admin-configurable session lifetime (Settings -> 'مدیریت نشست')."""
    try:
        h = int(state.get("session_hours", DEFAULT_SESSION_HOURS))
    except Exception:
        h = DEFAULT_SESSION_HOURS
    return max(1, min(h, 336)) * 3600


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
    st.setdefault("cfg_fmt", "{name}-{label}")
    st.setdefault("protocols", {k: True for k in PROTO_KEYS})
    st.setdefault("session_hours", DEFAULT_SESSION_HOURS)
    st.setdefault("clients", [])
    st.setdefault("inbounds", [])
    st["inbounds"] = [i for i in st["inbounds"] if i.get("tag")]
    st["clients"] = [c for c in st["clients"] if c.get("id") and c.get("name")]
    for c in st["clients"]:
        # per-user protocol override: None/[] = follow the global toggles
        p = c.get("protocols")
        if not isinstance(p, list):
            c["protocols"] = []
        else:
            c["protocols"] = [k for k in p if k in PROTO_KEYS]
        if not isinstance(c.get("note"), str):
            c["note"] = ""
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


def user_protocols(c):
    """Set of enabled protocol keys for a user (per-user override > global toggles)."""
    with state_lock:
        ov = c.get("protocols") or []
        if ov:
            return {k for k in ov if k in PROTO_KEYS}
        g = state.get("protocols") or {}
        return {k for k in PROTO_KEYS if g.get(k, True)}


def proto_enabled(key):
    with state_lock:
        return bool((state.get("protocols") or {}).get(key, True))


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
def user_entries(proto, clients, flow=None, key=None):
    # email = client id: it is the key Xray's per-user traffic counters use
    # `key` filters by per-user/global protocol toggles (built-in inbounds only)
    if key is not None:
        clients = [c for c in clients if key in user_protocols(c)]
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
        stream["wsSettings"] = {"path": ib["path"], "maxEarlyData": 2048,
                                "earlyDataHeaderName": "Sec-WebSocket-Protocol"}
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
    # routeOnly: the sniffed domain is used for routing only; destination is not rewritten (less DNS work)
    sniff = {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": True}

    def local(tag, port, proto, settings, stream):
        return {"tag": tag, "listen": "127.0.0.1", "port": port, "protocol": proto,
                "settings": settings, "streamSettings": stream, "sniffing": sniff}

    # per-protocol credential lists: global toggles + per-user overrides both apply
    def ids_for(key, trojan=False):
        return [{"password" if trojan else "id": c["id"], "email": c["id"]}
                for c in clients if key in user_protocols(c)]

    ws_stream = lambda p: {"network": "ws", "wsSettings": {"path": p, "maxEarlyData": 2048,
                                                           "earlyDataHeaderName": "Sec-WebSocket-Protocol"}}
    inbounds = [
        # XHTTP behind an HTTP proxy: the server accepts every mode, the client uses packet-up
        local("vless-xhttp", 10002, "vless", {"clients": ids_for("vless-xhttp"), "decryption": "none"},
              {"network": "xhttp", "xhttpSettings": {"path": XHTTP_PATH, "mode": "auto"}}),
        local("vless-hu", 10006, "vless", {"clients": ids_for("vless-hu"), "decryption": "none"},
              {"network": "httpupgrade", "httpupgradeSettings": {"path": HU_PATH}}),
        local("vless-ws", 10001, "vless", {"clients": ids_for("vless-ws"), "decryption": "none"},
              ws_stream(WS_PATH)),
        local("vmess-ws", 10004, "vmess", {"clients": ids_for("vmess-ws")}, ws_stream(VMESS_PATH)),
        local("trojan-ws", 10005, "trojan", {"clients": ids_for("trojan-ws", True)}, ws_stream(TROJAN_PATH)),
    ]
    # global protocol toggles: a disabled inbound is not even created
    inbounds = [i for i in inbounds if proto_enabled(BUILTIN_TAGS[i["tag"]])]
    if REALITY["priv"] and proto_enabled("vless-reality"):
        inbounds.append({
            "tag": "vless-reality", "listen": "0.0.0.0", "port": TCP_APP_PORT, "protocol": "vless",
            "settings": {"clients": ids_for("vless-reality"), "decryption": "none"},
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
        # tuned policy: longer idle window keeps tunnels warm (stable ping, fewer
        # re-handshakes) and a larger per-connection buffer maximises throughput
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True,
                                    "handshake": 4, "connIdle": env_int("XRAY_IDLE", 600),
                                    "bufferSize": env_int("XRAY_BUFFER", 1024)}}},
        # cached DoH lookups (falls back to the system resolver) + IPv4 first = faster connection setup
        "dns": {"servers": ["https+local://1.1.1.1/dns-query", "localhost"], "queryStrategy": "UseIPv4"},
        "inbounds": inbounds,
        # tcpNoDelay + keepalive + fast-open on the direct outbound: lower TTFB on
        # the first request, long-lived connections survive idle NAT windows
        "outbounds": [{"tag": "direct", "protocol": "freedom",
                       "settings": {"domainStrategy": "UseIPv4",
                                    "sockopt": {"tcpNoDelay": True, "tcpKeepAliveIdle": 300,
                                                "tcpFastOpen": True}}},
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
        # keep Xray's stderr for the in-panel log viewer (rotate at ~512KB)
        try:
            if os.path.exists(XRAY_LOG) and os.path.getsize(XRAY_LOG) > 512 * 1024:
                os.remove(XRAY_LOG)
        except Exception:
            pass
        lf = open(XRAY_LOG, "ab")
        xray_proc = subprocess.Popen(["xray", "run", "-c", CONF], stdout=subprocess.DEVNULL, stderr=lf)
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


def log_tail(n=120):
    """Last n lines of Xray's stderr, newest last. Empty list if the log does not exist."""
    try:
        with open(XRAY_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", "replace")
        return data.splitlines()[-int(n):]
    except Exception:
        return []


# ───────────────────────── nginx ─────────────────────────
NGINX_TEMPLATE = r"""
user root;
worker_processes auto;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 16384; multi_accept on; }
http {
  include /etc/nginx/mime.types;
  default_type text/html;
  charset utf-8;
  access_log off;
  server_tokens off;
  sendfile on;
  tcp_nopush on;
  tcp_nodelay on;
  keepalive_timeout 120s;
  keepalive_requests 100000;
  reset_timedout_connection on;
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
    proxy_set_header X-Real-IP $remote_addr;

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
    # a globally disabled protocol gets no nginx location at all
    for path, up, key in ((WS_PATH, 10001, "vless-ws"), (HU_PATH, 10006, "vless-hu"),
                          (VMESS_PATH, 10004, "vmess-ws"), (TROJAN_PATH, 10005, "trojan-ws")):
        if proto_enabled(key):
            locs += WS_LOCATION.replace("@@PATH@@", path).replace("@@UP@@", str(up))
    if proto_enabled("vless-xhttp"):
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


def fmt_name(fmt, c, label, proto="", net="", sec="", server=""):
    """Config display name from the admin's custom template. A broken template
    (unknown/empty placeholders) falls back to the classic name so links never break."""
    try:
        out = str(fmt).format(
            name=c.get("name", ""), label=label, proto=proto, net=net, sec=sec,
            server=server, uuid8=str(c.get("id", ""))[:8], date=time.strftime("%Y%m%d"))
        out = " ".join(out.split())  # collapse whitespace / newlines
        return out.strip("-_ ") or f"{c.get('name', '')}-{label}"
    except Exception:
        return f"{c.get('name', '')}-{label}"


def custom_link(ib, c, addr, fmt):
    cid, proto, net, sec = c["id"], ib["protocol"], ib["network"], ib["security"]
    nm = fmt_name(fmt, c, ib["name"], proto.upper(), net.upper(), sec.upper(), addr)
    frag = q(nm)
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
    # SNI/Host must always match the address the client connects to, otherwise
    # TLS certificate validation fails and the config cannot connect at all.
    if proto == "vmess":
        v = {"v": "2", "ps": nm, "add": addr, "port": "443", "id": cid, "aid": "0",
             "scy": "auto", "net": net, "type": "gun" if net == "grpc" else "none", "host": addr,
             "path": path.strip("/") if net == "grpc" else path, "tls": "tls", "sni": addr, "alpn": alpn, "fp": "chrome"}
        return "vmess://" + base64.b64encode(json.dumps(v).encode()).decode()
    p = {"encryption": "none"} if proto == "vless" else {}
    p.update(security="tls", sni=addr, fp="chrome", alpn=alpn, type=net, host=addr)
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
        fmt = state.get("cfg_fmt") or "{name}-{label}"
    cid = c["id"]
    # FIX: SNI/Host must match the address the client connects to. They used to
    # follow the panel host while the connection went to the custom "address",
    # so the TLS certificate never matched and NO config could connect.
    tls = f"security=tls&sni={addr}&fp=chrome"
    # per-user protocol set (global toggles are already respected inside user_protocols)
    allowed = user_protocols(c)
    # ?ed=2048: WebSocket 0-RTT early data - the first request leaves the client
    # inside the TLS handshake, cutting one round-trip on every cold connection.
    links = []
    if "vless-ws" in allowed:
        n1 = fmt_name(fmt, c, "VLESS-WS", "VLESS", "WS", "TLS", addr)
        links.append({"key": "vless-ws", "label": "VLESS · WebSocket",
                      "url": f"vless://{cid}@{addr}:443?encryption=none&{tls}&alpn=http%2F1.1&type=ws&host={addr}&path={q(WS_PATH + '?ed=2048')}#{q(n1)}"})
    if "vless-xhttp" in allowed:
        n2 = fmt_name(fmt, c, "VLESS-XHTTP", "VLESS", "XHTTP", "TLS", addr)
        links.append({"key": "vless-xhttp", "label": "VLESS · XHTTP",
                      "url": f"vless://{cid}@{addr}:443?encryption=none&{tls}&alpn=h2%2Chttp%2F1.1&type=xhttp&host={addr}&path={q(XHTTP_PATH)}&mode=packet-up#{q(n2)}"})
    if "vless-hu" in allowed:
        n3 = fmt_name(fmt, c, "VLESS-HTTPUpgrade", "VLESS", "HTTPUpgrade", "TLS", addr)
        links.append({"key": "vless-hu", "label": "VLESS · HTTPUpgrade",
                      "url": f"vless://{cid}@{addr}:443?encryption=none&{tls}&alpn=http%2F1.1&type=httpupgrade&host={addr}&path={q(HU_PATH)}#{q(n3)}"})
    if "vmess-ws" in allowed:
        n4 = fmt_name(fmt, c, "VMess-WS", "VMess", "WS", "TLS", addr)
        vmess = {"v": "2", "ps": n4, "add": addr, "port": "443", "id": cid, "aid": "0",
                 "scy": "auto", "net": "ws", "type": "none", "host": addr,
                 "path": VMESS_PATH + "?ed=2048",
                 "tls": "tls", "sni": addr, "alpn": "http/1.1", "fp": "chrome"}
        links.append({"key": "vmess-ws", "label": "VMess · WebSocket",
                      "url": "vmess://" + base64.b64encode(json.dumps(vmess).encode()).decode()})
    if "trojan-ws" in allowed:
        n5 = fmt_name(fmt, c, "Trojan-WS", "Trojan", "WS", "TLS", addr)
        links.append({"key": "trojan-ws", "label": "Trojan · WebSocket",
                      "url": f"trojan://{cid}@{addr}:443?security=tls&sni={addr}&fp=chrome&alpn=http%2F1.1&type=ws&host={addr}&path={q(TROJAN_PATH + '?ed=2048')}#{q(n5)}"})
    if tcp_ready() and "vless-reality" in allowed:
        n6 = fmt_name(fmt, c, "VLESS-Reality-TCP", "VLESS", "TCP", "Reality", TCP_HOST or addr)
        # spx=%2F (spiderX) keeps Xray's web fingerprint probing on the standard path
        links.append({"key": "vless-reality", "label": "VLESS · Reality (TCP Proxy)",
                      "url": f"vless://{cid}@{TCP_HOST}:{TCP_PUBLIC_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni={sni}&fp=chrome&pbk={REALITY['pub']}&sid={REALITY['sid']}&spx=%2F&type=tcp&headerType=none#{q(n6)}"})
    with state_lock:
        customs = [dict(i) for i in state["inbounds"]]
    for ib in customs:
        if ib["security"] == "reality" and not REALITY["pub"]:
            continue
        links.append({"key": ib["tag"], "label": f"{ib['name']} · {ib['protocol'].upper()} · {ib['network']} · {ib['security']}",
                      "url": custom_link(ib, c, addr, fmt)})
    return links


# ───────────────────────── QR codes (pure stdlib, no deps) ─────────────────────────
# Minimal but spec-correct QR encoder: byte mode, versions 1..15, ECC levels M/L,
# best-mask selection with the four standard penalty rules. Output = compact SVG.
_QR_EC = {  # (ec_per_block, g1_blocks, g1_data, g2_blocks, g2_data) per version
    "M": [(10,1,16,0,0),(16,1,28,0,0),(26,1,44,0,0),(18,2,32,0,0),(24,2,43,0,0),
          (16,4,27,0,0),(18,4,31,0,0),(22,2,38,2,39),(22,3,36,2,37),(26,4,43,1,44),
          (30,1,50,4,51),(22,6,36,2,37),(22,8,37,1,38),(24,4,40,5,41),(24,5,41,5,42)],
    "L": [(7,1,19,0,0),(10,1,34,0,0),(15,1,55,0,0),(20,1,80,0,0),(26,1,108,0,0),
          (18,2,68,0,0),(20,2,78,0,0),(24,2,97,0,0),(30,2,116,0,0),(18,2,68,2,69),
          (20,4,81,0,0),(24,2,92,2,93),(26,4,107,0,0),(30,3,115,1,116),(22,5,87,1,88)],
}
_QR_ALIGN = [[], [6,18],[6,22],[6,26],[6,30],[6,34],[6,22,38],[6,24,42],[6,26,46],
             [6,28,50],[6,30,54],[6,32,58],[6,34,62],[6,26,46,66],[6,26,48,70]]
_QR_FMT_LEVEL = {"M": 0, "L": 1}  # 2-bit level indicator (M=00, L=01)


def _gf_mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        b >>= 1
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
    return r


def _gf_pow(a, n):
    r = 1
    for _ in range(n):
        r = _gf_mul(r, a)
    return r


def _rs_ec(data, n):
    """Reed-Solomon EC codewords for `data` (bytes) with generator degree n."""
    gen = [1]
    for i in range(n):
        # gen *= (x + a^i):  x*P shifts P up one degree, a^i*P stays in place
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _gf_pow(2, i))
        gen = nxt
    rem = list(data) + [0] * n
    for i in range(len(data)):
        f = rem[i]
        if f:
            for j in range(len(gen)):
                rem[i + j] ^= _gf_mul(gen[j], f)
    return rem[len(data):]


def _qr_bit_capacity(v, level):
    ec, b1, d1, b2, d2 = _QR_EC[level][v - 1]
    codewords = b1 * d1 + b2 * d2
    bits = codewords * 8 - 4 - (8 if v < 10 else 16)
    return bits // 8


def _qr_encode(data):
    """Returns (version, level, final codeword bytes)."""
    raw = data.encode("utf-8")
    level = "M"
    if _qr_bit_capacity(15, "M") < len(raw) and _qr_bit_capacity(15, "L") >= len(raw):
        level = "L"
    for v in range(1, 16):
        if _qr_bit_capacity(v, level) >= len(raw):
            break
    else:
        raise ValueError("data too long for QR")
    nbits = 4 + (8 if v < 10 else 16) + len(raw) * 8
    bits = ["0100"]
    bits.append(format(len(raw), "016b" if v >= 10 else "08b"))
    for by in raw:
        bits.append(format(by, "08b"))
    ec, b1, d1, b2, d2 = _QR_EC[level][v - 1]
    cap = (b1 * d1 + b2 * d2) * 8
    # terminator (up to 4 bits) + pad to byte boundary
    term = min(4, cap - nbits)
    if term > 0:
        bits.append("0" * term)
    stream = "".join(bits)
    stream += "0" * ((8 - len(stream) % 8) % 8)
    cws = [int(stream[i:i + 8], 2) for i in range(0, len(stream), 8)]
    pad = [0xEC, 0x11]
    pi = 0
    while len(cws) < b1 * d1 + b2 * d2:
        cws.append(pad[pi % 2]); pi += 1
    # split into blocks
    blocks, dcws = [], []
    idx = 0
    for nb, dsz in ((b1, d1), (b2, d2)):
        for _ in range(nb):
            blocks.append(cws[idx:idx + dsz]); idx += dsz
    ecws = [_rs_ec(b, ec) for b in blocks]
    maxd = max(len(b) for b in blocks)
    out = []
    for i in range(maxd):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec):
        for e in ecws:
            out.append(e[i])
    return v, level, bytes(out)


def _qr_matrix(v, level, cws):
    size = 17 + 4 * v
    m = [[None] * size for _ in range(size)]  # None = empty, True = dark

    def set_(r, c, val):
        m[r][c] = bool(val)

    def finder(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    if 0 <= dr <= 6 and 0 <= dc <= 6:
                        dark = dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)
                        set_(rr, cc, dark)
                    else:
                        set_(rr, cc, False)  # separator (light)

    finder(0, 0); finder(0, size - 7); finder(size - 7, 0)
    # alignment patterns: drawn BEFORE the timing pattern - centers that already
    # hold finder/separator modules are skipped, timing-row/col ones are merged
    centers = _QR_ALIGN[v - 1]
    for r in centers:
        for c in centers:
            if m[r][c] is not None:   # center already taken = finder area
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    ring = max(abs(dr), abs(dc))
                    set_(r + dr, c + dc, ring != 1)
    # timing (values coincide with any alignment they cross)
    for i in range(8, size - 8):
        set_(6, i, i % 2 == 0); set_(i, 6, i % 2 == 0)
    # dark module
    set_(size - 8, 8, True)
    # reserve format areas (light for now, rewritten later)
    fmt_cells = []
    for i in range(9):
        if i != 6:
            fmt_cells += [(8, i), (i, 8)]
    for i in range(8):
        fmt_cells.append((8, size - 1 - i))
    for i in range(8):
        fmt_cells.append((size - 1 - i, 8))
    for r, c in fmt_cells:
        if m[r][c] is None:
            m[r][c] = False
    # version info areas (v >= 7): two 3x6 blocks
    if v >= 7:
        for i in range(6):
            for j in range(3):
                set_(i, size - 11 + j, False)     # top-right block (rows 0-5)
        for j in range(6):
            for i in range(3):
                set_(size - 11 + i, j, False)     # bottom-left block (cols 0-5)

    # data placement (zigzag)
    bits = "".join(format(b, "08b") for b in cws)
    # cells still empty right now are the data cells - the mask applies ONLY to these
    data_cells = [(r, c) for r in range(size) for c in range(size) if m[r][c] is None]
    bit_i = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if m[r][c] is None:
                    if bit_i < len(bits):
                        set_(r, c, bits[bit_i] == "1")
                        bit_i += 1
                    else:
                        set_(r, c, False)
        upward = not upward
        col -= 2

    # masks + penalty
    def mask_ok(mask, r, c):
        if mask == 0:
            return (r + c) % 2 == 0
        if mask == 1:
            return r % 2 == 0
        if mask == 2:
            return c % 3 == 0
        if mask == 3:
            return (r + c) % 3 == 0
        if mask == 4:
            return (r // 2 + c // 3) % 2 == 0
        if mask == 5:
            return (r * c) % 2 + (r * c) % 3 == 0
        if mask == 6:
            return ((r * c) % 2 + (r * c) % 3) % 2 == 0
        return ((r + c) % 2 + (r * c) % 3) % 2 == 0

    def penalty(mat):
        p = 0
        # rule 1: runs of same colour >= 5
        for row in list(mat) + [list(t) for t in zip(*mat)]:
            run, prev = 1, None
            for cell in row:
                if cell == prev:
                    run += 1
                    if run == 5:
                        p += 3
                    elif run > 5:
                        p += 1
                else:
                    run, prev = 1, cell
        # rule 2: 2x2 blocks
        for r in range(size - 1):
            for c in range(size - 1):
                if mat[r][c] == mat[r][c + 1] == mat[r + 1][c] == mat[r + 1][c + 1]:
                    p += 3
        # rule 3: finder-like patterns 1011101 with 4 light on either side
        pat1 = [True, False, True, True, True, False, True]
        for row in list(mat) + [list(t) for t in zip(*mat)]:
            for i in range(len(row) - 6):
                if row[i:i + 7] == pat1:
                    light_before = i >= 4 and not any(row[i - 4:i])
                    light_after = i + 11 <= len(row) and not any(row[i + 7:i + 11])
                    if light_before:
                        p += 40
                    if light_after:
                        p += 40
        # rule 4: dark ratio (5% steps away from 50% cost 10 each)
        dark = sum(sum(1 for cell in row if cell) for row in mat)
        p += abs(dark * 100 - 50 * size * size) // (5 * size * size) * 10
        return p

    best, best_score = None, None
    for mask in range(8):
        mat = [[(m[r][c] if m[r][c] is not None else False) for c in range(size)] for r in range(size)]
        for r, c in data_cells:
            if mask_ok(mask, r, c):
                mat[r][c] = not mat[r][c]
        sc = penalty(mat)
        if best_score is None or sc < best_score:
            best, best_score = (mat, mask), sc
    mat, mask = best

    # format info: BCH(15,5) then XOR 0x5412 - placed LSB-first (ISO 18004 fig.25)
    # NOTE: written onto `mat` (the masked copy), not the pre-mask matrix `m`
    fdata = (_QR_FMT_LEVEL[level] << 3) | mask
    fmt = fdata << 10
    for i in range(14, 9, -1):
        if fmt >> i & 1:
            fmt ^= 0x537 << (i - 10)
    fmt = ((fdata << 10) | fmt) ^ 0x5412

    def put(r, c, bit):
        mat[r][c] = bool(bit)

    # vertical strip (column 8): rows 0-5, 7, 8, then size-7..size-1
    for i in range(15):
        bit = (fmt >> i) & 1
        if i < 6:
            put(i, 8, bit)
        elif i < 8:
            put(i + 1, 8, bit)
        else:
            put(size - 15 + i, 8, bit)
    # horizontal strip (row 8): cols size-1..size-8, then 7, then 5..0
    for i in range(15):
        bit = (fmt >> i) & 1
        if i < 8:
            put(8, size - 1 - i, bit)
        elif i == 8:
            put(8, 7, bit)
        else:
            put(8, 14 - i, bit)
    # version info (v >= 7): BCH(18,6) generator 0x1f25, LSB-first
    if v >= 7:
        ver = v << 12
        for i in range(17, 11, -1):
            if ver >> i & 1:
                ver ^= 0x1F25 << (i - 12)
        ver = (ver | (v << 12))
        for i in range(18):
            bit = (ver >> i) & 1
            put(i // 3, size - 11 + i % 3, bit)      # top-right block
            put(size - 11 + i % 3, i // 3, bit)      # bottom-left block
    return mat


def qr_svg(data):
    """QR code as a compact SVG string (quiet zone included)."""
    v, level, cws = _qr_encode(data)
    mat = _qr_matrix(v, level, cws)
    n = len(mat)
    scale, quiet = 1, 4
    size_px = (n + 2 * quiet)
    rects = []
    for r in range(n):
        run_start = None
        row = mat[r]
        for c in range(n + 1):
            dark = c < n and row[c]
            if dark and run_start is None:
                run_start = c
            elif not dark and run_start is not None:
                rects.append(f'<rect x="{quiet + run_start}" y="{quiet + r}" width="{c - run_start}" height="1"/>')
                run_start = None
    body = "".join(rects)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size_px} {size_px}" '
            f'shape-rendering="crispEdges" width="{size_px * 12}" height="{size_px * 12}">'
            f'<rect width="{size_px}" height="{size_px}" fill="#ffffff"/>'
            f'<g fill="#0b0e16">{body}</g></svg>')


# ───────────────────────── optimized client config ─────────────────────────
def client_config_json(c):
    """Full, ready-to-import Xray client config for one user - tuned for Iranian
    users: ads/malware blocked, Iranian domains & IPs bypass the proxy (direct),
    DNS over HTTPS, mux off (XHTTP/Vision handle multiplexing natively)."""
    with state_lock:
        addr = state["address"] or ""
        sni = state["reality_sni"]
    allowed = user_protocols(c)
    cid = c["id"]
    outbounds, extra = [], []

    def tls_stream(net, path, alpn):
        transport = ({"wsSettings": {"path": path + "?ed=2048", "headers": {"Host": addr}}} if net == "ws" else
                     {"httpupgradeSettings": {"path": path, "host": addr}} if net == "httpupgrade" else
                     {"xhttpSettings": {"path": path, "mode": "packet-up", "host": addr}})
        return {"network": net, "security": "tls",
                "tlsSettings": {"serverName": addr, "fingerprint": "chrome", "alpn": alpn, "allowInsecure": False},
                # TCP_NODELAY removes Nagle latency, fast-open saves one RTT on the
                # first request, keepalive keeps the tunnel through NAT windows
                "sockopt": {"tcpNoDelay": True, "tcpFastOpen": True, "tcpKeepAliveIdle": 300},
                **transport}

    def vless_out(tag, port, stream, flow=None):
        return {"tag": tag, "protocol": "vless",
                "settings": {"vnext": [{"address": addr, "port": port,
                                        "users": [{"id": cid, "encryption": "none", "flow": flow or "", "level": 0}]}]},
                "streamSettings": stream, "mux": {"enabled": False, "concurrency": -1}}

    order = []
    if tcp_ready() and "vless-reality" in allowed:
        outbounds.append({
            "tag": "proxy", "protocol": "vless",
            "settings": {"vnext": [{"address": TCP_HOST, "port": int(TCP_PUBLIC_PORT or 443),
                                    "users": [{"id": cid, "encryption": "none",
                                               "flow": "xtls-rprx-vision", "level": 0}]}]},
            "streamSettings": {"network": "tcp", "security": "reality",
                               "realitySettings": {"show": False, "fingerprint": "chrome",
                                                   "serverName": sni, "publicKey": REALITY["pub"],
                                                   "shortId": REALITY["sid"], "spiderX": "/"},
                               "sockopt": {"tcpNoDelay": True, "tcpFastOpen": True, "tcpKeepAliveIdle": 300}},
            "mux": {"enabled": False, "concurrency": -1}})
        order.append("proxy")
    if "vless-ws" in allowed:
        tag = "proxy" if "proxy" not in order else "alt-vless-ws"
        outbounds.append(vless_out(tag, 443, tls_stream("ws", WS_PATH, ["http/1.1"])))
        order.append(tag)
    if "vless-hu" in allowed:
        tag = "proxy" if "proxy" not in order else "alt-vless-hu"
        outbounds.append(vless_out(tag, 443, tls_stream("httpupgrade", HU_PATH, ["http/1.1"])))
        order.append(tag)
    if "vless-xhttp" in allowed:
        tag = "proxy" if "proxy" not in order else "alt-vless-xhttp"
        outbounds.append(vless_out(tag, 443, tls_stream("xhttp", XHTTP_PATH, ["h2", "http/1.1"])))
        order.append(tag)
    if "vmess-ws" in allowed:
        tag = "proxy" if "proxy" not in order else "alt-vmess-ws"
        outbounds.append({"tag": tag, "protocol": "vmess",
                          "settings": {"vnext": [{"address": addr, "port": 443,
                                                  "users": [{"id": cid, "alterId": 0, "security": "auto", "level": 0}]}]},
                          "streamSettings": tls_stream("ws", VMESS_PATH, ["http/1.1"]),
                          "mux": {"enabled": False, "concurrency": -1}})
        order.append(tag)
    if "trojan-ws" in allowed:
        tag = "proxy" if "proxy" not in order else "alt-trojan-ws"
        outbounds.append({"tag": tag, "protocol": "trojan",
                          "settings": {"servers": [{"address": addr, "port": 443, "password": cid, "level": 0}]},
                          "streamSettings": tls_stream("ws", TROJAN_PATH, ["http/1.1"]),
                          "mux": {"enabled": False, "concurrency": -1}})
        order.append(tag)
    if not outbounds:
        return {"error": "no protocol enabled for this user"}
    outbounds += [{"tag": "direct", "protocol": "freedom",
                   "settings": {"domainStrategy": "UseIPv4",
                                "sockopt": {"tcpNoDelay": True, "tcpKeepAliveIdle": 300}}},
                  {"tag": "block", "protocol": "blackhole"}]
    return {
        "log": {"loglevel": "warning"},
        # DoH first; plain resolvers as fallback. Iranian domains are routed direct below,
        # so they never depend on these foreign resolvers.
        "dns": {"queryStrategy": "UseIPv4",
                "servers": ["https://1.1.1.1/dns-query", "8.8.8.8", "localhost"]},
        "inbounds": [
            {"tag": "socks", "listen": "127.0.0.1", "port": 10808, "protocol": "socks",
             "settings": {"auth": "noauth", "udp": True, "userLevel": 0}, "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False}},
            {"tag": "http", "listen": "127.0.0.1", "port": 10809, "protocol": "http",
             "settings": {"userLevel": 0}, "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False}},
        ],
        "outbounds": outbounds,
        "routing": {"domainStrategy": "IPIfNonMatch",
                    "rules": [
                        # block QUIC: browsers fall back to HTTP/2 over TCP, which is
                        # far faster and steadier inside a TCP-based tunnel (less
                        # packet-loss amplification -> stable ping, higher throughput)
                        {"type": "field", "port": "443", "network": "udp", "outboundTag": "block"},
                        {"type": "field", "domain": ["geosite:category-ads-all"], "outboundTag": "block"},
                        # Iranian sites stay direct: faster and does not burn the user's quota
                        {"type": "field", "domain": ["geosite:category-ir", "regexp:.*\\.ir$"], "outboundTag": "direct"},
                        {"type": "field", "ip": ["geoip:ir", "geoip:private"], "outboundTag": "direct"},
                    ]},
    }


def health_payload():
    """Self-diagnostics: process liveness + a real round-trip to Xray's stats API."""
    t0 = time.time()
    if xray_running():
        xray_stats()
    lat = round((time.time() - t0) * 1000)
    return {"ok": xray_running() and bool(nginx_proc and nginx_proc.poll() is None),
            "xray": xray_running(), "nginx": bool(nginx_proc and nginx_proc.poll() is None),
            "latency_ms": lat, "uptime": int(time.time() - START),
            "version": PANEL_VERSION, "restarts": xray_info["restarts"]}


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
            if n > 262144:  # restore payloads can be large; anything bigger is abuse
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
            sessions[t] = time.time() + sess_ttl()
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
            h["Set-Cookie"] = self.cookie(self._sid, sess_ttl())
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
                    # clients like v2rayNG read quota/expire from this header;
                    # zero values mean "unlimited" and are simply omitted
                    info = [f"upload={c.get('up', 0)}", f"download={c.get('down', 0)}"]
                    if c.get("quota"):
                        info.append(f"total={c['quota']}")
                    if c.get("expire_at"):
                        info.append(f"expire={c['expire_at']}")
                    return self.send_bytes(200, body, "text/plain; charset=utf-8", {
                        "Profile-Title": f"base64:{title}",
                        "Profile-Update-Interval": "12",
                        "Subscription-Userinfo": "; ".join(info),
                    })
            return self.send_bytes(404, "not found", "text/plain; charset=utf-8")

        if path.startswith("/panel/api/"):
            if not self.authed():
                return self.send_json(401, {"error": "unauthorized"})
            if path == "/panel/api/stats":
                return self.send_json(200, stats_payload(), self.auth_headers())
            if path == "/panel/api/state":
                return self.send_json(200, self.state_payload(), self.auth_headers())
            if path == "/panel/api/log":
                return self.send_json(200, {"lines": log_tail(150)}, self.auth_headers())
            if path == "/panel/api/health":
                return self.send_json(200, health_payload(), self.auth_headers())
            if path == "/panel/api/server-config":
                body = json.dumps(build_config(), ensure_ascii=False, indent=2)
                return self.send_bytes(200, body, "application/json; charset=utf-8",
                                       {**self.auth_headers(),
                                        "Content-Disposition": "attachment; filename=xray-server.json"})
            if path == "/panel/api/backup":
                with state_lock:
                    snap = {"ng_backup": 2, "version": PANEL_VERSION, "exported": int(time.time()),
                            "state": {"address": state["address"], "reality_sni": state["reality_sni"],
                                      "cfg_fmt": state.get("cfg_fmt") or "{name}-{label}",
                                      "protocols": {k: bool((state.get("protocols") or {}).get(k, True)) for k in PROTO_KEYS},
                                      "session_hours": state.get("session_hours", DEFAULT_SESSION_HOURS),
                                      "clients": [dict(c) for c in state["clients"]],
                                      "inbounds": [dict(i) for i in state["inbounds"]]}}
                # NOTE: admin credentials are intentionally NOT exported
                body = json.dumps(snap, ensure_ascii=False, indent=2)
                return self.send_bytes(200, body, "application/json; charset=utf-8",
                                       {**self.auth_headers(),
                                        "Content-Disposition": f"attachment; filename=nullgate-backup-{time.strftime('%Y%m%d')}.json"})
            if path == "/panel/api/qr":
                qs = urllib.parse.parse_qs(_qs)
                data = (qs.get("data") or [""])[0]
                if not data or len(data) > 800:
                    return self.send_json(400, {"error": "متن QR نامعتبر یا طولانی است"})
                try:
                    svg = qr_svg(data)
                except Exception:
                    return self.send_json(400, {"error": "ساخت QR ممکن نشد"})
                return self.send_bytes(200, svg, "image/svg+xml; charset=utf-8", self.auth_headers())
            if path.startswith("/panel/api/client/"):
                cid = path[len("/panel/api/client/"):].strip("/")
                with state_lock:
                    c = next((x for x in state["clients"] if x["id"] == cid), None)
                if not c:
                    return self.send_json(404, {"error": "کاربر پیدا نشد"})
                safe = re.sub(r"[^A-Za-z0-9_.\-]", "", c["name"])[:24] or "user"
                cfg = client_config_json(c)
                if cfg.get("error"):
                    return self.send_json(400, cfg, self.auth_headers())
                return self.send_bytes(200, json.dumps(cfg, ensure_ascii=False, indent=2),
                                       "application/json; charset=utf-8",
                                       {**self.auth_headers(),
                                        "Content-Disposition": f"attachment; filename=nullgate-{safe}.json"})
        self.send_bytes(404, "not found", "text/plain; charset=utf-8")

    def state_payload(self):
        host = self.host()
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "https").split(",")[0].strip() == "https" else "http"
        with state_lock:
            clients = list(state["clients"])
            settings = {"user": state["admin_user"], "address": state["address"],
                        "reality_sni": state["reality_sni"], "cfg_fmt": state.get("cfg_fmt") or "{name}-{label}",
                        "protocols": {k: bool((state.get("protocols") or {}).get(k, True)) for k in PROTO_KEYS},
                        "session_hours": state.get("session_hours", DEFAULT_SESSION_HOURS)}
            customs = [dict(i) for i in state["inbounds"]]
        now = time.time()
        out = []
        for c in clients:
            exp = c.get("expire_at", 0)
            out.append({"name": c["name"], "id": c["id"], "main": c["id"] == MAIN_UUID,
                        "sub_url": f"{scheme}://{host}/sub/{sub_token(c['id'])}",
                        "links": build_links(c, host),
                        "quota": c.get("quota", 0), "up": c.get("up", 0), "down": c.get("down", 0),
                        "expire_at": exp, "status": client_status(c, now),
                        "days_left": int((exp - now) // 86400) + 1 if exp > now else 0,
                        "note": c.get("note", ""), "protocols": sorted(user_protocols(c))})
        protocols = [
            {"key": "vless-ws", "name": "VLESS · WebSocket", "path": WS_PATH, "note": "سازگارترین گزینه؛ روی اغلب کلاینت‌ها کار می‌کند"},
            {"key": "vless-xhttp", "name": "VLESS · XHTTP", "path": XHTTP_PATH, "note": "حالت packet-up؛ مخصوص عبور از پروکسی‌های HTTP"},
            {"key": "vless-hu", "name": "VLESS · HTTPUpgrade", "path": HU_PATH, "note": "سبک و سریع، شبیه WebSocket"},
            {"key": "vmess-ws", "name": "VMess · WebSocket", "path": VMESS_PATH, "note": "برای کلاینت‌های قدیمی‌تر"},
            {"key": "trojan-ws", "name": "Trojan · WebSocket", "path": TROJAN_PATH, "note": "رمز = UUID کاربر"},
        ]
        if tcp_ready():
            protocols.append({"key": "vless-reality", "name": "VLESS · Reality (TCP)", "path": f"{TCP_HOST}:{TCP_PUBLIC_PORT}",
                              "note": "اتصال مستقیم TCP بدون دامنه؛ سریع‌ترین حالت"})
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
                sessions[tok] = now + sess_ttl()
                login_fails.pop(ip, None)
                return self.send_json(200, {"ok": True}, {"Set-Cookie": self.cookie(tok, sess_ttl())})
            recent.append(now)
            time.sleep(0.6)  # constant-ish delay makes online brute force painfully slow
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
            name = str(d.get("name", "")).strip()[:30] if "name" in d else None
            if name is not None and not name:
                return self.send_json(400, {"error": "نام کاربر نمی‌تواند خالی باشد"})
            note = str(d.get("note", ""))[:140] if "note" in d else None
            protos = d.get("protocols")
            if protos is not None:
                if not isinstance(protos, list) or any(p not in PROTO_KEYS for p in protos):
                    return self.send_json(400, {"error": "لیست پروتکل‌ها نامعتبر است"})
                protos = sorted(set(protos))
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
                if name:
                    c["name"] = name
                if note is not None:
                    c["note"] = note
                if protos is not None:
                    c["protocols"] = protos  # [] = follow the global toggles
                save_state()
            sync_xray()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        if path == "/panel/api/clients/bulk":
            ids = d.get("ids") if isinstance(d.get("ids"), list) else []
            action = str(d.get("action", ""))
            ids = [str(i) for i in ids if str(i) != MAIN_UUID][:100]
            if not ids or action not in ("delete", "reset", "extend"):
                return self.send_json(400, {"error": "درخواست نامعتبر است"})
            try:
                days = int(float(d.get("days") or 0))
            except (TypeError, ValueError):
                return self.send_json(400, {"error": "روز نامعتبر است"})
            if action == "extend" and not 0 < days <= 3650:
                return self.send_json(400, {"error": "روز نامعتبر است"})
            now = time.time()
            with state_lock:
                touched = 0
                keep = []
                for c in state["clients"]:
                    if c["id"] in ids:
                        touched += 1
                        if action == "delete":
                            continue  # drop from the new list
                        if action == "reset":
                            c["up"] = c["down"] = 0
                        elif action == "extend":
                            base = max(c.get("expire_at") or 0, now)
                            c["expire_at"] = int(base + days * 86400)
                    keep.append(c)
                state["clients"] = keep
                if touched:
                    save_state()
            if touched:
                restart_xray()
            return self.send_json(200, {"ok": True, "affected": touched}, self.auth_headers())

        if path == "/panel/api/reset-traffic-all":
            with state_lock:
                for c in state["clients"]:
                    c["up"] = c["down"] = 0
                save_state()
            return self.send_json(200, {"ok": True}, self.auth_headers())

        if path == "/panel/api/clients/regen":
            cid = str(d.get("id", ""))
            if cid == MAIN_UUID:
                return self.send_json(400, {"error": "UUID کاربر اصلی قابل تغییر نیست"})
            with state_lock:
                c = next((x for x in state["clients"] if x["id"] == cid), None)
                if not c:
                    return self.send_json(404, {"error": "کاربر پیدا نشد"})
                c["id"] = str(uuid.uuid4())
                c["up"] = c["down"] = 0
                save_state()
            restart_xray()
            return self.send_json(200, {"ok": True, "id": c["id"]}, self.auth_headers())

        if path == "/panel/api/restore":
            snap = d.get("state") if isinstance(d.get("state"), dict) else None
            if not snap:
                return self.send_json(400, {"error": "فایل پشتیبان نامعتبر است"})
            raw_clients = snap.get("clients") if isinstance(snap.get("clients"), list) else []
            if len(raw_clients) > 100:
                return self.send_json(400, {"error": "تعداد کاربران بیش از حد مجاز است"})
            new_clients = []
            seen_ids = set()
            for rc in raw_clients:
                if not isinstance(rc, dict) or not rc.get("id") or not rc.get("name"):
                    return self.send_json(400, {"error": "داده کاربر نامعتبر است"})
                rid = str(rc["id"])[:64]
                if rid in seen_ids:
                    return self.send_json(400, {"error": "UUID تکراری در فایل پشتیبان"})
                seen_ids.add(rid)
                try:
                    quota = max(0, int(rc.get("quota") or 0))
                    expire = max(0, int(rc.get("expire_at") or 0))
                    up = max(0, int(rc.get("up") or 0))
                    down = max(0, int(rc.get("down") or 0))
                except (TypeError, ValueError):
                    return self.send_json(400, {"error": "اعداد کاربر نامعتبر است"})
                protos = rc.get("protocols") if isinstance(rc.get("protocols"), list) else []
                new_clients.append({"name": str(rc["name"])[:30], "id": rid, "quota": quota,
                                    "expire_at": expire, "up": up, "down": down,
                                    "note": str(rc.get("note") or "")[:140],
                                    "protocols": [p for p in protos if p in PROTO_KEYS]})
            if not any(c["id"] == MAIN_UUID for c in new_clients):
                new_clients.insert(0, {"name": "main", "id": MAIN_UUID, "quota": 0, "expire_at": 0,
                                       "up": 0, "down": 0, "note": "", "protocols": []})
            new_inbounds = []
            used_paths = {p.lower() for p in (WS_PATH, XHTTP_PATH, HU_PATH, VMESS_PATH, TROJAN_PATH)}
            used_ports = set(BUILTIN_PORTS)
            for ri in (snap.get("inbounds") if isinstance(snap.get("inbounds"), list) else [])[:20]:
                if not isinstance(ri, dict):
                    continue
                name = str(ri.get("name") or "").strip()[:30]
                proto = str(ri.get("protocol") or "").lower()
                net = str(ri.get("network") or "").lower()
                sec = str(ri.get("security") or "").lower()
                if not name or proto not in PROTOS or net not in NETS or sec not in ("tls", "reality"):
                    continue
                ib = {"tag": "ib-" + uuid.uuid4().hex[:8], "name": name, "protocol": proto,
                      "network": net, "security": sec}
                if sec == "reality":
                    if proto != "vless" or net not in ("tcp", "xhttp", "grpc") or not REALITY["priv"]:
                        continue
                    try:
                        port = int(ri.get("port"))
                    except (TypeError, ValueError):
                        continue
                    if not 1024 <= port <= 65535 or port in used_ports or 10100 <= port <= 10999:
                        continue
                    sni = str(ri.get("sni") or snap.get("reality_sni") or state["reality_sni"]).strip().lower()
                    if not HOST_RE.fullmatch(sni):
                        continue
                    ib.update(port=port, sni=sni)
                    used_ports.add(port)
                else:
                    if net == "tcp" or (proto == "vmess" and net == "xhttp"):
                        continue
                    raw = str(ri.get("path") or "").strip()
                    path_v = norm_path(raw, "") if raw else "/" + uuid.uuid4().hex[:8]
                    if len(path_v) < 3 or path_v.lower().startswith(("/panel", "/sub")) or path_v.lower() in used_paths:
                        continue
                    ib["path"] = path_v
                    used_paths.add(path_v.lower())
                    ib["lport"] = next(p for p in range(10100, 11000) if p not in
                                       {x.get("lport") for x in new_inbounds} - {None} and p not in used_ports)
                new_inbounds.append(ib)
            with state_lock:
                s = snap.get("protocols") if isinstance(snap.get("protocols"), dict) else {}
                state["address"] = str(snap.get("address") or "").strip()[:253]
                if state["address"] and not HOST_RE.fullmatch(state["address"]):
                    state["address"] = ""
                sni = str(snap.get("reality_sni") or "").strip().lower()
                if HOST_RE.fullmatch(sni) and sni != state["reality_sni"]:
                    state["reality_sni"] = sni
                fmt = " ".join(str(snap.get("cfg_fmt") or "").split())[:100]
                state["cfg_fmt"] = fmt or "{name}-{label}"
                state["protocols"] = {k: bool(s.get(k, True)) for k in PROTO_KEYS}
                try:
                    state["session_hours"] = max(1, min(336, int(snap.get("session_hours") or DEFAULT_SESSION_HOURS)))
                except (TypeError, ValueError):
                    state["session_hours"] = DEFAULT_SESSION_HOURS
                state["clients"] = new_clients
                state["inbounds"] = new_inbounds
                save_state()
            apply_inbounds()
            return self.send_json(200, {"ok": True, "clients": len(new_clients), "inbounds": len(new_inbounds)},
                                  self.auth_headers())

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
                    # a new password invalidates every other session
                    sessions.clear()
                    sessions[self._sid] = time.time() + sess_ttl()
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
                if "cfg_fmt" in d:
                    fmt = " ".join(str(d["cfg_fmt"]).split())
                    if len(fmt) > 100:
                        return self.send_json(400, {"error": "قالب نام کانفیگ حداکثر ۱۰۰ نویسه است"})
                    state["cfg_fmt"] = fmt or "{name}-{label}"
                if "protocols" in d:
                    req = d.get("protocols")
                    if not isinstance(req, dict):
                        return self.send_json(400, {"error": "پروتکل‌ها نامعتبر است"})
                    newp = {k: bool(req.get(k, False)) for k in PROTO_KEYS}
                    if not any(newp.values()):
                        return self.send_json(400, {"error": "حداقل یک پروتکل باید فعال بماند"})
                    if dict(state.get("protocols") or {}) != newp:
                        state["protocols"] = newp
                        need_restart = True  # inbounds appear/disappear
                if "session_hours" in d:
                    try:
                        h = int(float(d.get("session_hours")))
                    except (TypeError, ValueError):
                        return self.send_json(400, {"error": "ساعت نشست نامعتبر است"})
                    if not 1 <= h <= 336:
                        return self.send_json(400, {"error": "نشست باید بین ۱ تا ۳۳۶ ساعت باشد"})
                    state["session_hours"] = h
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
