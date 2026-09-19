import os, json, uuid, subprocess, threading, secrets, time, html, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
WS_PATH = os.environ.get("WS_PATH", "/ws")
XHTTP_PATH = os.environ.get("XHTTP_PATH", "/xhttp")
ENV_UUID = os.environ.get("UUID") or str(uuid.uuid4())
CONF = "/etc/xray.json"

DATA_DIR = os.environ.get("DATA_DIR", "/data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    open(os.path.join(DATA_DIR, ".t"), "w").close()
except Exception:
    DATA_DIR = "/tmp"
STATE_FILE = os.path.join(DATA_DIR, "state.json")

lock = threading.Lock()
xray_proc = None
sessions = {}  # token -> expiry


def default_state():
    return {
        "admin_user": os.environ.get("ADMIN_USER", "admin"),
        "admin_pass": os.environ.get("ADMIN_PASSWORD") or ENV_UUID,
        "clients": [{"name": "main", "id": ENV_UUID}],
    }


def load_state():
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        st = default_state()
    st.setdefault("clients", [{"name": "main", "id": ENV_UUID}])
    st.setdefault("admin_user", os.environ.get("ADMIN_USER", "admin"))
    st.setdefault("admin_pass", os.environ.get("ADMIN_PASSWORD") or ENV_UUID)
    if not any(c["id"] == ENV_UUID for c in st["clients"]):
        st["clients"].insert(0, {"name": "main", "id": ENV_UUID})
    return st


def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


state = load_state()
save_state()


def write_config():
    cl = [{"id": c["id"]} for c in state["clients"]]
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
                "streamSettings": {
                    "network": "xhttp",
                    "xhttpSettings": {"path": XHTTP_PATH, "mode": "packet-up"},
                },
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
    q = lambda s: urllib.parse.quote(s, safe="")
    name = q(c["name"])
    base = f"vless://{c['id']}@{host}:443?encryption=none&security=tls&sni={host}&fp=chrome&host={host}"
    ws = f"{base}&type=ws&path={q(WS_PATH)}#{name}-WS"
    xh = f"{base}&type=xhttp&mode=packet-up&path={q(XHTTP_PATH)}#{name}-XHTTP"
    return ws, xh


def mem_info():
    d = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                d[k.strip()] = int(v.strip().split()[0])
    except Exception:
        return {"total": 0, "used": 0, "percent": 0}
    total = d.get("MemTotal", 0)
    avail = d.get("MemAvailable", total)
    used = max(total - avail, 0)
    pct = round(used / total * 100, 1) if total else 0
    return {"total": total // 1024, "used": used // 1024, "percent": pct}


_prev_cpu = {}


def cpu_percent():
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = list(map(int, parts))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        global _prev_cpu
        if _prev_cpu:
            dt = total - _prev_cpu["total"]
            di = idle - _prev_cpu["idle"]
            pct = round((1 - di / dt) * 100, 1) if dt else 0
        else:
            pct = 0
        _prev_cpu = {"total": total, "idle": idle}
        return pct
    except Exception:
        return 0


def disk_info():
    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = total - free
        pct = round(used / total * 100, 1) if total else 0
        return {"total": total // (1024**3), "used": used // (1024**3), "percent": pct}
    except Exception:
        return {"total": 0, "used": 0, "percent": 0}


START = time.time()


def uptime_str():
    s = int(time.time() - START)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}h {m}m {s}s"


DECOY = "<!doctype html><html><head><meta charset='utf-8'><title>Welcome</title></head><body style='font-family:sans-serif;text-align:center;margin-top:20vh'><h1>It works!</h1></body></html>"

FONT = "<link rel='preconnect' href='https://fonts.googleapis.com'><link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;700;900&display=swap' rel='stylesheet'>"

BASE_CSS = """
:root{--bg:#0a0a12;--panel:#14141f;--panel2:#1b1b2a;--accent:#8b5cf6;--accent2:#c084fc;--txt:#f1f0f7;--muted:#8a8aa3;--good:#34d399;--bad:#f87171;--border:#2a2a3d}
*{box-sizing:border-box}
body{margin:0;font-family:'Vazirmatn',sans-serif;background:radial-gradient(circle at 20% 0%,#1a0f2e 0%,#0a0a12 45%),radial-gradient(circle at 100% 100%,#0f1a2e 0%,#0a0a12 50%);color:var(--txt);min-height:100vh}
a{color:inherit;text-decoration:none}
.glass{background:linear-gradient(145deg,rgba(255,255,255,.04),rgba(255,255,255,.01));backdrop-filter:blur(14px);border:1px solid var(--border);border-radius:18px}
"""


def login_page(err=""):
    e = html.escape(err)
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{FONT}<title>ورود به پنل</title>
<style>{BASE_CSS}
body{{display:flex;align-items:center;justify-content:center;padding:16px}}
.box{{width:100%;max-width:380px;padding:36px 28px;text-align:center}}
.logo{{width:64px;height:64px;margin:0 auto 14px;border-radius:16px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-size:30px;box-shadow:0 10px 40px -8px rgba(139,92,246,.6)}}
h1{{font-weight:900;font-size:22px;margin:0 0 6px}}
p.sub{{color:var(--muted);margin:0 0 26px;font-size:13px}}
input{{width:100%;padding:13px 16px;margin-bottom:12px;border-radius:12px;border:1px solid var(--border);background:#0d0d18;color:var(--txt);font-family:inherit;font-size:14px}}
input:focus{{outline:none;border-color:var(--accent)}}
button{{width:100%;padding:13px;border:0;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font-weight:700;font-size:15px;cursor:pointer;margin-top:6px}}
.err{{color:var(--bad);font-size:13px;margin-bottom:10px;min-height:16px}}
</style></head><body><div class="box glass">
<div class="logo">🛡️</div><h1>پنل مدیریت</h1><p class="sub">برای ورود اطلاعات کاربری را وارد کنید</p>
<div class="err">{e}</div>
<form method="post" action="/panel/login">
<input name="user" placeholder="نام کاربری" autocomplete="username" required>
<input name="pass" type="password" placeholder="رمز عبور" autocomplete="current-password" required>
<button>ورود</button></form></div></body></html>"""


def dashboard(host, tab="home"):
    m = mem_info(); d = disk_info(); c = cpu_percent()
    rows = ""
    for cl in state["clients"]:
        ws, xh = links(cl, host)
        rows += f"""<div class="ccard glass">
<div class="ccard-h"><span class="dot"></span><b>{html.escape(cl['name'])}</b><small>{cl['id']}</small></div>
<div class="l"><input readonly value="{html.escape(ws)}"><button onclick="cp(this)">کپی WS</button></div>
<div class="l"><input readonly value="{html.escape(xh)}"><button onclick="cp(this)">کپی XHTTP</button></div>
<form method="post" action="/panel/del" onsubmit="return confirm('این کاربر حذف شود؟')">
<input type="hidden" name="id" value="{cl['id']}"><button class="d" {"disabled" if len(state['clients'])<=1 else ""}>حذف کاربر</button></form></div>"""

    inbounds_html = f"""
<div class="grid2">
<div class="glass ib"><h3>WS Inbound</h3><table>
<tr><td>Path</td><td dir="ltr">{WS_PATH}</td></tr>
<tr><td>Port</td><td>{PORT} (TLS 443 از Railway)</td></tr>
<tr><td>Network</td><td>ws</td></tr></table></div>
<div class="glass ib"><h3>XHTTP Inbound</h3><table>
<tr><td>Path</td><td dir="ltr">{XHTTP_PATH}</td></tr>
<tr><td>Mode</td><td>packet-up</td></tr>
<tr><td>Network</td><td>xhttp</td></tr></table></div>
</div>"""

    settings_html = f"""
<div class="glass set">
<h3>تغییر نام کاربری و رمز عبور</h3>
<form method="post" action="/panel/settings" class="settings-form">
<label>نام کاربری جدید</label><input name="user" value="{html.escape(state['admin_user'])}">
<label>رمز عبور جدید</label><input name="pass" type="password" placeholder="رمز جدید (خالی = بدون تغییر)">
<button>ذخیره تغییرات</button>
</form></div>"""

    panes = {
        "home": f"""
<div class="stats">
<div class="stat glass"><div class="si">🧠</div><div><div class="sv">{c}%</div><div class="sl">مصرف CPU</div></div>
<div class="bar"><div class="fill" style="width:{c}%"></div></div></div>
<div class="stat glass"><div class="si">💾</div><div><div class="sv">{m['used']}MB / {m['total']}MB</div><div class="sl">مصرف RAM ({m['percent']}%)</div></div>
<div class="bar"><div class="fill" style="width:{m['percent']}%"></div></div></div>
<div class="stat glass"><div class="si">🗄️</div><div><div class="sv">{d['used']}GB / {d['total']}GB</div><div class="sl">فضای دیسک ({d['percent']}%)</div></div>
<div class="bar"><div class="fill" style="width:{d['percent']}%"></div></div></div>
<div class="stat glass"><div class="si">⏱️</div><div><div class="sv">{uptime_str()}</div><div class="sl">مدت روشن بودن سرویس</div></div></div>
</div>
<div class="glass summary"><h3>خلاصه</h3>
<p>تعداد کاربران فعال: <b>{len(state['clients'])}</b> &nbsp;|&nbsp; دامنه: <b dir="ltr">{html.escape(host)}</b></p></div>
""",
        "inbounds": inbounds_html,
        "clients": f"""
<form class="n glass" method="post" action="/panel/add"><input name="name" placeholder="نام کاربر جدید" required><button>+ ساخت کانفیگ</button></form>
<div class="cgrid">{rows}</div>""",
        "settings": settings_html,
    }
    active = panes.get(tab, panes["home"])

    def item(key, icon, label):
        cls = "active" if key == tab else ""
        return f'<a class="mi {cls}" href="/panel?tab={key}"><span>{icon}</span>{label}</a>'

    menu = item("home", "🏠", "داشبورد") + item("inbounds", "🔌", "اینباندها") + \
        item("clients", "👥", "کلاینت‌ها") + item("settings", "⚙️", "تنظیمات") + \
        '<a class="mi logout" href="/panel/logout"><span>🚪</span>خروج</a>'

    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{FONT}<title>داشبورد پنل</title>
<style>{BASE_CSS}
.topbar{{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;padding:14px 18px;background:rgba(10,10,18,.7);backdrop-filter:blur(14px);border-bottom:1px solid var(--border)}}
.burger{{width:42px;height:42px;border-radius:12px;background:var(--panel2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:18px}}
.title{{font-weight:900;font-size:17px;background:linear-gradient(135deg,var(--accent2),#fff);-webkit-background-clip:text;background-clip:text;color:transparent}}
.wrap{{display:flex}}
.side{{position:fixed;top:0;right:-280px;width:260px;height:100vh;background:var(--panel);border-left:1px solid var(--border);padding:80px 14px 14px;transition:right .25s;z-index:30}}
.side.open{{right:0}}
.mi{{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:12px;color:var(--muted);font-weight:700;margin-bottom:6px}}
.mi span{{font-size:18px}}
.mi.active,.mi:hover{{background:var(--panel2);color:var(--txt)}}
.mi.logout{{color:var(--bad);margin-top:20px}}
.overlay{{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;z-index:25}}
.overlay.open{{display:block}}
main{{padding:20px 16px 60px;max-width:900px;margin:0 auto}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}}
.stat{{padding:16px;display:flex;flex-direction:column;gap:10px}}
.stat>div:first-child{{display:flex;align-items:center;gap:10px}}
.si{{font-size:22px}}
.sv{{font-weight:900;font-size:16px}}
.sl{{color:var(--muted);font-size:12px}}
.bar{{height:6px;border-radius:6px;background:#000;overflow:hidden}}
.fill{{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}}
.summary{{padding:16px}}
.summary h3{{margin-top:0}}
.grid2{{display:grid;grid-template-columns:1fr;gap:12px}}
.ib{{padding:16px}}
.ib table{{width:100%;border-collapse:collapse;font-size:13px}}
.ib td{{padding:8px 0;border-top:1px solid var(--border)}}
.ib td:first-child{{color:var(--muted);width:40%}}
.n{{display:flex;gap:8px;padding:14px;margin-bottom:16px}}
.n input{{flex:1;padding:11px 14px;border-radius:10px;border:1px solid var(--border);background:#0d0d18;color:var(--txt)}}
.n button,.set button{{background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;color:#fff;font-weight:700;border-radius:10px;padding:11px 16px;cursor:pointer}}
.cgrid{{display:flex;flex-direction:column;gap:12px}}
.ccard{{padding:14px}}
.ccard-h{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 8px var(--good)}}
.ccard-h small{{color:var(--muted);direction:ltr;font-size:11px;margin-right:auto}}
.l{{display:flex;gap:6px;margin:6px 0;direction:ltr}}
.l input{{flex:1;min-width:0;background:#0d0d18;color:#a7f3d0;border:1px solid var(--border);border-radius:8px;padding:9px;font-size:12px}}
.l button{{background:var(--panel2);border:1px solid var(--border);color:var(--txt);border-radius:8px;padding:0 12px;font-size:12px;cursor:pointer}}
.d{{margin-top:6px;width:100%;background:rgba(248,113,113,.12);border:1px solid #7f1d1d;color:var(--bad);border-radius:8px;padding:9px;cursor:pointer}}
.set{{padding:18px}}
.settings-form{{display:flex;flex-direction:column;gap:10px;margin-top:10px}}
.settings-form label{{font-size:12px;color:var(--muted)}}
.settings-form input{{padding:11px 14px;border-radius:10px;border:1px solid var(--border);background:#0d0d18;color:var(--txt)}}
</style></head><body>
<div class="topbar"><div class="burger" onclick="document.getElementById('side').classList.add('open');document.getElementById('ov').classList.add('open')">☰</div>
<div class="title">پنل مدیریت فیلترشکن</div></div>
<div class="overlay" id="ov" onclick="this.classList.remove('open');document.getElementById('side').classList.remove('open')"></div>
<div class="side" id="side">{menu}</div>
<main>{active}</main>
<script>function cp(b){{var i=b.parentNode.querySelector('input');i.select();i.setSelectionRange(0,999);
navigator.clipboard.writeText(i.value).then(()=>{{var t=b.textContent;b.textContent='✓ کپی شد';setTimeout(()=>b.textContent=t,1200)}})
.catch(()=>{{document.execCommand('copy')}})}}
setTimeout(()=>location.reload(),30000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def cookie_token(self):
        c = self.headers.get("Cookie", "")
        for part in c.split(";"):
            part = part.strip()
            if part.startswith("sid="):
                return part[4:]
        return None

    def authed(self):
        t = self.cookie_token()
        exp = sessions.get(t)
        if exp and exp > time.time():
            sessions[t] = time.time() + 3600 * 12
            return True
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
        path, _, qs = self.path.partition("?")
        if path == "/panel/logout":
            t = self.cookie_token()
            sessions.pop(t, None)
            return self.send(303, "", extra={"Location": "/panel", "Set-Cookie": "sid=; Max-Age=0; Path=/"})
        if path.startswith("/panel"):
            if not self.authed():
                return self.send(200, login_page())
            q = urllib.parse.parse_qs(qs)
            tab = q.get("tab", ["home"])[0]
            return self.send(200, dashboard(self.host(), tab))
        self.send(200, DECOY)

    def do_POST(self):
        path = self.path
        n = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(n).decode())

        if path == "/panel/login":
            u = form.get("user", [""])[0]
            p = form.get("pass", [""])[0]
            if secrets.compare_digest(u, state["admin_user"]) and secrets.compare_digest(p, state["admin_pass"]):
                token = secrets.token_hex(24)
                sessions[token] = time.time() + 3600 * 12
                return self.send(303, "", extra={"Location": "/panel", "Set-Cookie": f"sid={token}; Path=/; HttpOnly; Max-Age=43200"})
            return self.send(200, login_page("نام کاربری یا رمز عبور اشتباه است"))

        if not self.authed():
            return self.send(303, "", extra={"Location": "/panel"})

        if path == "/panel/add":
            name = (form.get("name", ["user"])[0].strip() or "user")[:30]
            state["clients"].append({"name": name, "id": str(uuid.uuid4())})
            save_state(); restart_xray()
            return self.send(303, "", extra={"Location": "/panel?tab=clients"})

        if path == "/panel/del":
            cid = form.get("id", [""])[0]
            if len(state["clients"]) > 1:
                state["clients"] = [c for c in state["clients"] if c["id"] != cid]
                save_state(); restart_xray()
            return self.send(303, "", extra={"Location": "/panel?tab=clients"})

        if path == "/panel/settings":
            newu = form.get("user", [""])[0].strip()
            newp = form.get("pass", [""])[0]
            if newu:
                state["admin_user"] = newu[:40]
            if newp:
                state["admin_pass"] = newp
            save_state()
            return self.send(303, "", extra={"Location": "/panel?tab=settings"})

        self.send(404, "not found")


if __name__ == "__main__":
    restart_xray()
    print(f"Panel: https://<your-domain>/panel  user={state['admin_user']}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 10003), H).serve_forever()
