import os, json, uuid, subprocess, threading, secrets, time, html, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
WS_PATH = os.environ.get("WS_PATH", "/ws")
XHTTP_PATH = os.environ.get("XHTTP_PATH", "/xhttp")
VMESS_PATH = os.environ.get("VMESS_PATH", "/vmess")
TROJAN_PATH = os.environ.get("TROJAN_PATH", "/trojan")
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
sessions = {}
START = time.time()


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
    trojan_cl = [{"password": c["id"]} for c in state["clients"]]
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
                        {"path": VMESS_PATH, "dest": 10004},
                        {"path": TROJAN_PATH, "dest": 10005},
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
                    "xhttpSettings": {"path": XHTTP_PATH, "mode": "stream-up"},
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "port": 10004, "listen": "127.0.0.1", "protocol": "vmess",
                "settings": {"clients": cl},
                "streamSettings": {"network": "ws", "wsSettings": {"path": VMESS_PATH}},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "port": 10005, "listen": "127.0.0.1", "protocol": "trojan",
                "settings": {"clients": trojan_cl},
                "streamSettings": {"network": "ws", "wsSettings": {"path": TROJAN_PATH}},
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


def q(s):
    return urllib.parse.quote(s, safe="")


def all_links(c, host):
    name = q(c["name"])
    vless_base = f"vless://{c['id']}@{host}:443?encryption=none&security=tls&sni={host}&fp=chrome&host={host}"
    ws = f"{vless_base}&type=ws&path={q(WS_PATH)}#{name}-VLESS-WS"
    xh = f"{vless_base}&type=xhttp&mode=stream-up&path={q(XHTTP_PATH)}#{name}-VLESS-XHTTP"

    vmess_obj = {
        "v": "2", "ps": f"{c['name']}-VMess-WS", "add": host, "port": "443",
        "id": c["id"], "aid": "0", "scy": "auto", "net": "ws", "type": "none",
        "host": host, "path": VMESS_PATH, "tls": "tls", "sni": host,
    }
    import base64
    vmess = "vmess://" + base64.b64encode(json.dumps(vmess_obj).encode()).decode()

    trojan = f"trojan://{c['id']}@{host}:443?security=tls&sni={host}&type=ws&host={host}&path={q(TROJAN_PATH)}#{name}-Trojan-WS"

    return [
        ("VLESS · WS", ws, "🟣"),
        ("VLESS · XHTTP", xh, "🟢"),
        ("VMess · WS", vmess, "🔵"),
        ("Trojan · WS", trojan, "🟠"),
    ]


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
        return max(0, min(pct, 100))
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


def load_avg():
    try:
        return os.getloadavg()
    except Exception:
        return (0, 0, 0)


def uptime_str():
    s = int(time.time() - START)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}h {m}m {s}s"


DECOY = "<!doctype html><html><head><meta charset='utf-8'><title>Welcome</title></head><body style='font-family:sans-serif;text-align:center;margin-top:20vh'><h1>It works!</h1></body></html>"

FONT = "<link rel='preconnect' href='https://fonts.googleapis.com'><link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800;900&display=swap' rel='stylesheet'>"

BASE_CSS = """
:root{
  --bg:#07070d;--panel:#12121e;--panel2:#191928;--panel3:#20202f;
  --accent:#8b5cf6;--accent2:#d946ef;--accent3:#06b6d4;
  --txt:#f3f1fb;--muted:#8d8aa6;--good:#34d399;--warn:#fbbf24;--bad:#f87171;--border:#282840;
}
*{box-sizing:border-box}
body{margin:0;font-family:'Vazirmatn',sans-serif;color:var(--txt);min-height:100vh;
  background:
    radial-gradient(circle at 15% -10%,#2a1250 0%,transparent 45%),
    radial-gradient(circle at 110% 10%,#0d2540 0%,transparent 45%),
    radial-gradient(circle at 50% 120%,#1a0a30 0%,transparent 50%),
    var(--bg);
  background-attachment:fixed;
}
a{color:inherit;text-decoration:none}
.glass{background:linear-gradient(155deg,rgba(255,255,255,.045),rgba(255,255,255,.008));
  backdrop-filter:blur(18px);border:1px solid var(--border);border-radius:20px;
  box-shadow:0 8px 30px -12px rgba(0,0,0,.5)}
::selection{background:var(--accent);color:#fff}
"""


def login_page(err=""):
    e = html.escape(err)
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{FONT}<title>ورود به پنل</title>
<style>{BASE_CSS}
body{{display:flex;align-items:center;justify-content:center;padding:16px;position:relative;overflow:hidden}}
.orb{{position:fixed;border-radius:50%;filter:blur(60px);opacity:.35;z-index:0}}
.o1{{width:280px;height:280px;background:var(--accent);top:-80px;right:-60px;animation:float 8s ease-in-out infinite}}
.o2{{width:220px;height:220px;background:var(--accent3);bottom:-60px;left:-60px;animation:float 10s ease-in-out infinite reverse}}
@keyframes float{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-25px)}}}}
.box{{position:relative;z-index:1;width:100%;max-width:380px;padding:38px 28px;text-align:center}}
.logo{{width:68px;height:68px;margin:0 auto 16px;border-radius:18px;background:linear-gradient(135deg,var(--accent),var(--accent2));
  display:flex;align-items:center;justify-content:center;font-size:32px;
  box-shadow:0 16px 45px -10px rgba(139,92,246,.65);animation:pulse 3s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{box-shadow:0 16px 45px -10px rgba(139,92,246,.65)}}50%{{box-shadow:0 16px 55px -6px rgba(217,70,239,.75)}}}}
h1{{font-weight:900;font-size:23px;margin:0 0 6px;background:linear-gradient(135deg,#fff,var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}}
p.sub{{color:var(--muted);margin:0 0 28px;font-size:13px}}
input{{width:100%;padding:14px 16px;margin-bottom:12px;border-radius:13px;border:1px solid var(--border);background:#0c0c16;color:var(--txt);font-family:inherit;font-size:14px;transition:.2s}}
input:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(139,92,246,.15)}}
button{{width:100%;padding:14px;border:0;border-radius:13px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font-weight:800;font-size:15px;cursor:pointer;margin-top:6px;transition:.2s}}
button:active{{transform:scale(.98)}}
.err{{color:var(--bad);font-size:13px;margin-bottom:10px;min-height:16px}}
.badge{{display:inline-block;margin-top:20px;font-size:11px;color:var(--muted);letter-spacing:.5px}}
</style></head><body><div class="orb o1"></div><div class="orb o2"></div>
<div class="box glass">
<div class="logo">🛡️</div><h1>پنل مدیریت VIP</h1><p class="sub">برای ورود، اطلاعات کاربری خود را وارد کنید</p>
<div class="err">{e}</div>
<form method="post" action="/panel/login">
<input name="user" placeholder="نام کاربری" autocomplete="username" required>
<input name="pass" type="password" placeholder="رمز عبور" autocomplete="current-password" required>
<button>ورود به داشبورد</button></form>
<div class="badge">SECURE ACCESS • ENCRYPTED SESSION</div>
</div></body></html>"""


def ring(pct, color, label, value, sub):
    pct = max(0, min(pct, 100))
    deg = pct * 3.6
    return f"""<div class="ringcard glass">
<div class="ring" style="background:conic-gradient({color} {deg}deg,#1c1c2c {deg}deg)">
<div class="ring-in"><b>{pct}%</b></div></div>
<div class="ring-meta"><div class="rv">{value}</div><div class="rl">{label}</div><div class="rs">{sub}</div></div>
</div>"""


def dashboard(host, tab="home"):
    m = mem_info(); d = disk_info(); c = cpu_percent(); la = load_avg()

    proto_rows = ""
    for cl in state["clients"]:
        links = all_links(cl, host)
        link_html = "".join(
            f'<div class="l"><span class="tag">{icon} {ptag}</span><input readonly value="{html.escape(url)}"><button onclick="cp(this)">کپی</button></div>'
            for ptag, url, icon in links
        )
        proto_rows += f"""<div class="ccard glass">
<div class="ccard-h"><span class="dot"></span><b>{html.escape(cl['name'])}</b><small>{cl['id']}</small></div>
{link_html}
<form method="post" action="/panel/del" onsubmit="return confirm('این کاربر حذف شود؟')">
<input type="hidden" name="id" value="{cl['id']}"><button class="d" {"disabled" if len(state['clients'])<=1 else ""}>حذف کاربر</button></form></div>"""

    inbounds_html = f"""
<div class="grid2">
<div class="glass ib"><div class="ib-h">🟣 VLESS · WebSocket</div><table>
<tr><td>Path</td><td dir="ltr">{WS_PATH}</td></tr><tr><td>Port</td><td>{PORT} (پشت TLS 443)</td></tr>
<tr><td>وضعیت</td><td><span class="pill good">فعال</span></td></tr></table></div>
<div class="glass ib"><div class="ib-h">🟢 VLESS · XHTTP</div><table>
<tr><td>Path</td><td dir="ltr">{XHTTP_PATH}</td></tr><tr><td>Mode</td><td>stream-up</td></tr>
<tr><td>وضعیت</td><td><span class="pill good">فعال</span></td></tr></table></div>
<div class="glass ib"><div class="ib-h">🔵 VMess · WebSocket</div><table>
<tr><td>Path</td><td dir="ltr">{VMESS_PATH}</td></tr><tr><td>AlterId</td><td>0</td></tr>
<tr><td>وضعیت</td><td><span class="pill good">فعال</span></td></tr></table></div>
<div class="glass ib"><div class="ib-h">🟠 Trojan · WebSocket</div><table>
<tr><td>Path</td><td dir="ltr">{TROJAN_PATH}</td></tr><tr><td>رمز</td><td>= UUID کاربر</td></tr>
<tr><td>وضعیت</td><td><span class="pill good">فعال</span></td></tr></table></div>
</div>
<div class="glass note">⚠️ Railway فقط HTTP/HTTPS پاس می‌دهد؛ به همین دلیل تمام پروتکل‌ها روی WS یا XHTTP سوار شده‌اند تا پشت دامنه با TLS کار کنند.</div>"""

    settings_html = f"""
<div class="glass set">
<div class="ib-h">👤 نام کاربری و رمز پنل</div>
<form method="post" action="/panel/settings" class="settings-form">
<label>نام کاربری جدید</label><input name="user" value="{html.escape(state['admin_user'])}">
<label>رمز عبور جدید</label><input name="pass" type="password" placeholder="رمز جدید (خالی = بدون تغییر)">
<button>ذخیره تغییرات</button>
</form></div>
<div class="glass set" style="margin-top:14px">
<div class="ib-h">🧭 مسیرهای پروتکل</div>
<p class="muted-p">این مسیرها از طریق Variables در Railway قابل تغییرند: <code>WS_PATH</code>, <code>XHTTP_PATH</code>, <code>VMESS_PATH</code>, <code>TROJAN_PATH</code></p>
</div>"""

    panes = {
        "home": f"""
<div class="hero glass">
<div><div class="hero-t">به پنل خوش آمدید ✨</div><div class="hero-s">وضعیت سرور شما به‌صورت لحظه‌ای</div></div>
<div class="hero-badge">🟢 آنلاین</div>
</div>
<div class="rings">
{ring(c, "var(--accent)", "مصرف پردازنده", f"{c}%", f"Load: {la[0]:.2f}")}
{ring(m['percent'], "var(--accent3)", "مصرف حافظه", f"{m['used']}MB", f"از {m['total']}MB")}
</div>
<div class="stats">
<div class="stat glass"><div class="si">🗄️</div><div><div class="sv">{d['used']}GB / {d['total']}GB</div><div class="sl">فضای دیسک ({d['percent']}%)</div></div>
<div class="bar"><div class="fill" style="width:{d['percent']}%"></div></div></div>
<div class="stat glass"><div class="si">⏱️</div><div><div class="sv">{uptime_str()}</div><div class="sl">مدت روشن بودن سرویس</div></div></div>
<div class="stat glass"><div class="si">👥</div><div><div class="sv">{len(state['clients'])}</div><div class="sl">کاربران فعال</div></div></div>
<div class="stat glass"><div class="si">🔌</div><div><div class="sv">4</div><div class="sl">پروتکل فعال</div></div></div>
</div>
<div class="glass summary"><div class="ib-h">🌐 اطلاعات دامنه</div>
<p>دامنه فعال: <b dir="ltr">{html.escape(host)}</b></p>
<p>پورت داخلی: <b>{PORT}</b> &nbsp;|&nbsp; پروتکل‌ها: <b>VLESS-WS, VLESS-XHTTP, VMess-WS, Trojan-WS</b></p></div>
""",
        "inbounds": inbounds_html,
        "clients": f"""
<form class="n glass" method="post" action="/panel/add"><input name="name" placeholder="نام کاربر جدید" required><button>+ ساخت کانفیگ</button></form>
<div class="cgrid">{proto_rows}</div>""",
        "settings": settings_html,
    }
    active = panes.get(tab, panes["home"])

    def item(key, icon, label, sub):
        cls = "active" if key == tab else ""
        return f"""<a class="mi {cls}" href="/panel?tab={key}">
<span class="mi-ic">{icon}</span><span class="mi-t"><b>{label}</b><small>{sub}</small></span></a>"""

    menu = (
        item("home", "🏠", "داشبورد", "وضعیت کلی سرور")
        + item("inbounds", "🔌", "اینباندها", "پروتکل‌های فعال")
        + item("clients", "👥", "کلاینت‌ها", "ساخت و مدیریت کانفیگ")
        + item("settings", "⚙️", "تنظیمات", "یوزر و رمز پنل")
        + '<a class="mi logout" href="/panel/logout"><span class="mi-ic">🚪</span><span class="mi-t"><b>خروج</b><small>پایان نشست</small></span></a>'
    )

    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{FONT}<title>داشبورد پنل</title>
<style>{BASE_CSS}
.topbar{{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:16px 18px;background:rgba(7,7,13,.7);backdrop-filter:blur(16px);border-bottom:1px solid var(--border)}}
.burger{{width:44px;height:44px;border-radius:13px;background:var(--panel2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:19px}}
.title{{font-weight:900;font-size:17px;background:linear-gradient(135deg,var(--accent2),#fff);-webkit-background-clip:text;background-clip:text;color:transparent}}
.side{{position:fixed;top:0;right:-290px;width:270px;height:100vh;background:linear-gradient(180deg,var(--panel),#0c0c16);border-left:1px solid var(--border);padding:80px 14px 14px;transition:right .28s cubic-bezier(.2,.8,.2,1);z-index:30}}
.side.open{{right:0}}
.mi{{display:flex;align-items:center;gap:12px;padding:13px 14px;border-radius:14px;color:var(--muted);margin-bottom:8px;transition:.15s}}
.mi-ic{{font-size:19px;width:34px;height:34px;border-radius:10px;background:var(--panel3);display:flex;align-items:center;justify-content:center;flex-shrink:0}}
.mi-t{{display:flex;flex-direction:column;gap:1px}}
.mi-t b{{font-size:13.5px;color:var(--txt)}}
.mi-t small{{font-size:11px;color:var(--muted)}}
.mi.active{{background:linear-gradient(135deg,rgba(139,92,246,.18),rgba(217,70,239,.1));border:1px solid rgba(139,92,246,.35)}}
.mi.active .mi-ic{{background:linear-gradient(135deg,var(--accent),var(--accent2))}}
.mi:hover{{background:var(--panel3)}}
.mi.logout .mi-t b{{color:var(--bad)}}
.overlay{{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;z-index:25;backdrop-filter:blur(2px)}}
.overlay.open{{display:block}}
main{{padding:20px 16px 60px;max-width:920px;margin:0 auto}}
.hero{{display:flex;align-items:center;justify-content:space-between;padding:20px;margin-bottom:16px}}
.hero-t{{font-weight:900;font-size:17px}}
.hero-s{{color:var(--muted);font-size:12.5px;margin-top:3px}}
.hero-badge{{background:rgba(52,211,153,.12);color:var(--good);border:1px solid rgba(52,211,153,.3);padding:7px 14px;border-radius:999px;font-size:12px;font-weight:700;white-space:nowrap}}
.rings{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}}
.ringcard{{padding:18px;display:flex;align-items:center;gap:14px}}
.ring{{width:72px;height:72px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0}}
.ring-in{{width:54px;height:54px;border-radius:50%;background:var(--panel);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:900}}
.ring-meta{{display:flex;flex-direction:column;gap:2px;min-width:0}}
.rv{{font-weight:900;font-size:15px}}
.rl{{color:var(--muted);font-size:11.5px}}
.rs{{color:var(--muted);font-size:10.5px;opacity:.75}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}}
.stat{{padding:16px;display:flex;flex-direction:column;gap:10px}}
.stat>div:first-child{{display:flex;align-items:center;gap:10px}}
.si{{font-size:22px}}
.sv{{font-weight:900;font-size:15px}}
.sl{{color:var(--muted);font-size:11.5px}}
.bar{{height:6px;border-radius:6px;background:#000;overflow:hidden}}
.fill{{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:6px}}
.summary{{padding:16px}}
.summary p{{margin:6px 0;font-size:13px;color:var(--muted)}}
.summary b{{color:var(--txt)}}
.ib-h{{font-weight:800;font-size:14.5px;margin-bottom:10px}}
.grid2{{display:grid;grid-template-columns:1fr;gap:12px}}
.ib{{padding:16px}}
.ib table{{width:100%;border-collapse:collapse;font-size:13px}}
.ib td{{padding:8px 0;border-top:1px solid var(--border)}}
.ib td:first-child{{color:var(--muted);width:40%}}
.pill{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700}}
.pill.good{{background:rgba(52,211,153,.12);color:var(--good);border:1px solid rgba(52,211,153,.3)}}
.note{{padding:14px 16px;font-size:12.5px;color:var(--warn);margin-top:6px;border-color:rgba(251,191,36,.25)}}
.n{{display:flex;gap:8px;padding:14px;margin-bottom:16px}}
.n input{{flex:1;padding:12px 14px;border-radius:11px;border:1px solid var(--border);background:#0c0c16;color:var(--txt)}}
.n button,.set button{{background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;color:#fff;font-weight:700;border-radius:11px;padding:12px 18px;cursor:pointer}}
.cgrid{{display:flex;flex-direction:column;gap:14px}}
.ccard{{padding:16px}}
.ccard-h{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 8px var(--good)}}
.ccard-h small{{color:var(--muted);direction:ltr;font-size:11px;margin-right:auto}}
.l{{display:flex;gap:6px;margin:7px 0;align-items:center}}
.tag{{font-size:10.5px;color:var(--muted);white-space:nowrap;width:100px;flex-shrink:0}}
.l input{{flex:1;min-width:0;background:#0c0c16;color:#a7f3d0;border:1px solid var(--border);border-radius:9px;padding:9px;font-size:11.5px;direction:ltr}}
.l button{{background:var(--panel3);border:1px solid var(--border);color:var(--txt);border-radius:9px;padding:0 12px;height:36px;font-size:12px;cursor:pointer;flex-shrink:0}}
.d{{margin-top:8px;width:100%;background:rgba(248,113,113,.1);border:1px solid #7f1d1d;color:var(--bad);border-radius:9px;padding:10px;cursor:pointer}}
.set{{padding:18px}}
.settings-form{{display:flex;flex-direction:column;gap:10px;margin-top:8px}}
.settings-form label{{font-size:12px;color:var(--muted)}}
.settings-form input{{padding:12px 14px;border-radius:11px;border:1px solid var(--border);background:#0c0c16;color:var(--txt)}}
.muted-p{{color:var(--muted);font-size:12.5px;line-height:2}}
code{{background:#0c0c16;padding:2px 6px;border-radius:6px;font-size:11.5px}}
</style></head><body>
<div class="topbar"><div class="title">پنل مدیریت فیلترشکن</div><div class="burger" onclick="document.getElementById('side').classList.add('open');document.getElementById('ov').classList.add('open')">☰</div></div>
<div class="overlay" id="ov" onclick="this.classList.remove('open');document.getElementById('side').classList.remove('open')"></div>
<div class="side" id="side">{menu}</div>
<main>{active}</main>
<script>function cp(b){{var i=b.parentNode.querySelector('input');i.select();i.setSelectionRange(0,999);
navigator.clipboard.writeText(i.value).then(()=>{{var t=b.textContent;b.textContent='✓';setTimeout(()=>b.textContent=t,1200)}})
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
            qd = urllib.parse.parse_qs(qs)
            tab = qd.get("tab", ["home"])[0]
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
