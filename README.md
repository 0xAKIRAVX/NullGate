<div align="center">

<img src="nullgate-logo.png" alt="NullGate — Lion & Sun emblem" width="640">

<br><br>

[![core](https://img.shields.io/badge/core-Xray--core-00ff66?style=flat-square&labelColor=000000)](https://github.com/XTLS/Xray-core)
[![platform](https://img.shields.io/badge/platform-Railway-7cff3f?style=flat-square&labelColor=000000)](https://railway.app)
[![ui](https://img.shields.io/badge/ui-RTL_Persian-d9b44a?style=flat-square&labelColor=000000)]()
[![version](https://img.shields.io/badge/version-2.3.1-d9b44a?style=flat-square&labelColor=000000)]()
[![fps](https://img.shields.io/badge/render-120_FPS-00d9ff?style=flat-square&labelColor=000000)]()
[![protocols](https://img.shields.io/badge/protocols-6_×_TCP%2FHTTPS-00ff66?style=flat-square&labelColor=000000)]()
[![use](https://img.shields.io/badge/use-personal_only-7cff3f?style=flat-square&labelColor=000000)]()

**NullGate** یک سرویس مبتنی بر **Xray-core** با پنل وب فارسی، لینک اشتراک، پشتیبانی از **TCP Proxy** ریلوی و لوگوی شیر و خورشید است.
نسخه **2.3.1** نسخه‌ای **سبک‌تر و سریع‌تر** از 2.3.0 است: پروتکل **WireGuard حذف شده**، سرعت دانلود/آپلود کانفیگ‌ها به سقف توان رسیده و real delay پایدارتر از همیشه است.

</div>

> [!IMPORTANT]
> این پروژه صرفاً برای استفاده شخصی طراحی شده است.

---

## تازه‌های نسخه 2.3.1

### 🗑 حذف WireGuard
- اینباند UDP، کلیدهای X25519، کانفیگ `.conf` و QR وایرگارد به‌طور کامل از هسته و UI حذف شد
- WG روی Railway اصلاً قابل استفاده نبود (Railway فقط TCP/HTTPS می‌دهد)؛ حالا پنل فقط پروتکل‌هایی را سرویس می‌کند که **واقعاً وصل می‌شوند**
- ۶ پروتکل باقی می‌ماند: **Reality · XHTTP · WS · HTTPUpgrade · VMess · Trojan**

### ⚡ حداکثر سرعت دانلود/آپلود
| لایه | بهینه‌سازی |
|---|---|
| سرور Xray | **بافر اتصال ۱۰۲۴KB** (دوبرابر نسخه قبل) — throughput بالاتر روی لینک‌های پُرتاخیر ایران↔اروپا؛ با `XRAY_BUFFER` قابل تغییر |
| سرور Xray | تونل‌ها تا **۶۰۰ ثانیه گرم** می‌مانند (`connIdle`، با `XRAY_IDLE` قابل تغییر) — re-handshake تقریباً صفر |
| سرور Xray | `TCP Fast Open` + `NoDelay` + keepalive روی خروجی مستقیم + `UseIPv4` (بدون معطلِ IPv6) |
| nginx | `sendfile` + `tcp_nodelay` + `worker_connections 16384` + `multi_accept` + `keepalive_requests 100000` + `proxy_buffering off` روی همه مسیرهای پروکسی |
| کانفیگ کلاینت | `sockopt` (FastOpen/NoDelay/keepalive 300s) روی همه خروجی‌ها از جمله Reality |
| کانفیگ کلاینت | **بلاک QUIC (UDP:443)** → مرورگر روی HTTP/2/TCP؛ داخل تونل TCP سریع‌تر و بدون افت ناگهانی |
| DNS | DoH کش‌شده (`https+local://1.1.1.1`) + `queryStrategy UseIPv4` → برقراری اتصال سریع‌تر |

### 🎯 پینگ پایدار (real delay در محدوده ثابت)
- **NoDelay روی تمام سوکت‌ها** → حذف جهش‌های ۴۰ms ناشی از الگوریتم Nagle (دلیل اصلی نوسان real delay)
- **Keepalive طولانی + connIdle ۶۰۰s** → اتصالِ گرم یعنی پینگ تست بعدی = پینگ تست قبلی، بدون re-handshake
- `routeOnly` در sniffing → بازنویسی مقصد انجام نمی‌شود؛ یک lookup کمتر در هر اتصال
- بلاک QUIC → مرورگرها روی یک مسیر TCP پایدار می‌مانند؛ پینگ ثابت‌تر از UDP های رقابتی

> [!NOTE]
> کف مطلق پینگ را فیزیک تعیین می‌کند (فاصله ایران تا دیتاسنتر Railway). این نسخه تمام منابع **نوسان** در سمت سرور را حذف می‌کند تا real delay همیشه در همان محدوده باریکِ RTT بماند.

---

## معماری

```
کلاینت ──HTTPS──▶ Railway ──▶ nginx (PORT)
                               ├─ /ws       → VLESS · WebSocket
                               ├─ /xhttp    → VLESS · XHTTP (packet-up)
                               ├─ /hu       → VLESS · HTTPUpgrade
                               ├─ /vmess    → VMess · WebSocket
                               ├─ /trojan   → Trojan · WebSocket
                               ├─ /panel    → پنل مدیریت
                               ├─ /sub/...  → لینک اشتراک
                               └─ /         → index.html (صفحه ظاهری)

کلاینت ──TCP──▶ Railway TCP Proxy ──▶ Xray (VLESS · Reality) روی پورت 9000
```

## محتوای این پکیج

```
nullgate-panel-v2.3.1/
├── panel.html            ← پنل وب (نسخه 2.3.1 — جایگزین فایل قبلی در ریپو)
├── panel.py              ← سرور پنل (نسخه 2.3.1 — جایگزین فایل قبلی در ریپو)
├── README.md             ← همین فایل
├── nullgate-logo.png     ← لوگوی شیر و خورشید
└── screenshots/
    ├── 01-login.png            صفحه ورود (تخت‌جمشید + پرچم شیر و خورشید)
    ├── 02-dashboard-full.png   داشبورد (تم مشکی + بنر تهران + بج‌ها)
    ├── 04-conns-full.png       اینباندها + پنل Reality
    ├── 06-settings-full.png    تنظیمات (پروتکل‌های فعال)
    ├── 09-client-open.png      جزئیات کاربر + کانفیگ‌های تکی
    └── 10-mobile-dash-full.png موبایل
```

> [!NOTE]
> این پکیج فقط **فایل‌های تغییرکرده** را شامل می‌شود. ریپوی کامل همچنان به `Dockerfile`، `index.html`، `LICENSE` و فایل‌های قبلی خودش نیاز دارد — دو فایل `panel.html` و `panel.py` را فقط جایگزین کنید.

## راه‌اندازی

**اگر قبلاً دیپلوی کرده‌اید:** فقط `panel.html` و `panel.py` را در ریپو جایگزین و Redeploy کنید — همین.

**نصب از صفر:**

1. فایل‌های ریپو (از جمله این دو فایل) را در یک ریپوی GitHub بگذارید.
2. Railway: `New Project → Deploy from GitHub repo`.
3. در **Variables** این‌ها را بگذارید:

| نام | مقدار | توضیح |
|---|---|---|
| `PORT` | `8080` | پورت HTTP سرویس |
| `UUID` | یک UUID ثابت | **حتماً بگذارید** تا لینک اشتراک و کلیدها با هر دیپلوی عوض نشود |
| `ADMIN_USER` | `admin` | نام کاربری پنل |
| `ADMIN_PASSWORD` | رمز دلخواه | خالی باشد، رمز همان `UUID` است |
| `DOMAIN` | *(اختیاری)* | اگر دامنه اختصاصی دارید |

4. `Settings → Networking → Generate Domain` با Target Port برابر `8080`.
5. (پیشنهادی) `Settings → Volumes` با مسیر `/data` تا کاربران و تنظیمات پاک نشوند.
6. پنل: `https://<دامنه>/panel`

## لینک اشتراک (Sub)

در تب **کاربران**، دکمه «کپی ساب» را بزنید و لینک را در کلاینت به‌عنوان Subscription اضافه کنید:

- **v2rayNG**: منو → Subscription group setting → + → لینک را بچسبانید → Update subscription
- **Hiddify / Streisand / V2Box**: افزودن پروفایل از کلیپ‌بورد

با یک لینک، همه پروتکل‌ها (WS، XHTTP، HTTPUpgrade، VMess، Trojan و Reality) یکجا وارد می‌شوند. توکن هر کاربر از `UUID` ساخته می‌شود و با دیپلوی دوباره ثابت می‌ماند.

## فعال‌کردن TCP Proxy (برای Reality)

1. Railway → سرویس → `Settings → Networking → TCP Proxy`
2. پورت را **9000** بگذارید.
3. دامنه و پورت عمومی که Railway می‌دهد را در متغیرها بگذارید:

| نام | مثال |
|---|---|
| `RAILWAY_TCP_PROXY_DOMAIN` | `shuttle.proxy.rlwy.net` |
| `RAILWAY_TCP_PROXY_PORT` | `45833` |

4. Redeploy → کارت Reality در پنل «فعال» می‌شود و لینکش وارد ساب می‌شود.

## متغیرهای اختیاری

`WS_PATH` `XHTTP_PATH` `HU_PATH` `VMESS_PATH` `TROJAN_PATH` · `SECRET` (منبع توکن‌ها؛ پیش‌فرض `UUID`) · `TCP_HOST` / `TCP_PORT` (معادل دو متغیر بالا) · `REALITY_PRIVATE_KEY` / `REALITY_SHORT_ID` · `XRAY_BUFFER` (بافر اتصال KB، پیش‌فرض 1024) · `XRAY_IDLE` (ثانیه گرم‌ماندن تونل، پیش‌فرض 600)

## نکات

- با ساخت یا حذف کاربر، Xray چند ثانیه ری‌استارت می‌شود.
- ترافیک کاربران به شبکه خصوصی Railway (`*.railway.internal`) و آدرس‌های داخلی مسدود است.
- اگر XHTTP روی کلاینتی وصل نشد، همان کلاینت را به‌روز کنید یا موقتاً از WS / HTTPUpgrade استفاده کنید.
- پلن رایگان Railway سقف مصرف دارد.
- **سنجش فریم‌ریت**: در کنسول مرورگر (F12) عبارت `document.documentElement.dataset.fps` را بزنید؛ روی نمایشگر 120Hz حدود `120` می‌بینید (داخل پنل هم در «اطلاعات سیستم → نرخ فریم پنل» زنده است).
- اگر از نسخه‌های قبلی بکاپ دارید، `wg_ip` داخل بکاپ‌ها در بازیابی جدید نادیده گرفته می‌شود — نگران سازگاری نباشید.
