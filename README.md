<div align="center">

<img src="nullgate-logo.png" alt="NullGate — Lion & Sun emblem" width="640">

<br><br>

[![core](https://img.shields.io/badge/core-Xray--core-00ff66?style=flat-square&labelColor=000000)](https://github.com/XTLS/Xray-core)
[![platform](https://img.shields.io/badge/platform-Railway-7cff3f?style=flat-square&labelColor=000000)](https://railway.app)
[![ui](https://img.shields.io/badge/ui-RTL_Persian-d9b44a?style=flat-square&labelColor=000000)]()
[![version](https://img.shields.io/badge/version-2.3.0-d9b44a?style=flat-square&labelColor=000000)]()
[![fps](https://img.shields.io/badge/render-120_FPS-00d9ff?style=flat-square&labelColor=000000)]()
[![wg](https://img.shields.io/badge/protocol-WireGuard-889199?style=flat-square&labelColor=000000)]()
[![use](https://img.shields.io/badge/use-personal_only-7cff3f?style=flat-square&labelColor=000000)]()

**NullGate** یک سرویس مبتنی بر **Xray-core** با پنل وب فارسی، لینک اشتراک، پشتیبانی از **TCP Proxy** ریلوی و لوگوی شیر و خورشید است.
در نسخه **2.3.0** پنل **تم مشکی خالص** گرفته، تصاویر با **پرچم پهلوی (شیر و خورشید)** بازتولید شده‌اند، پروتکل **WireGuard** اضافه شده و کانفیگ‌ها برای **حداکثر سرعت و پایداری پینگ** بهینه شده‌اند.

</div>

> [!IMPORTANT]
> این پروژه صرفاً برای استفاده شخصی طراحی شده است.

---

## تازه‌های نسخه 2.3.0

### 🖤 تم مشکی خالص
- تمام رنگ‌های **سرمه‌ای حذف** شدند؛ پس‌زمینه، کارت‌ها، خطوط، گرادیان‌ها و متن‌های خاکستری همه **خنثی و مشکی** شدند (تم روشن هم بی‌رنگ شد)
- بنر هیرو و پس‌زمینه لاگین با ترکیب **مشکی-طلایی** بازتولید شدند

### 🦁 پرچم پهلوی (شیر و خورشید)
- پرچم جمهوری اسلامی از تصاویر حذف شد؛ **بنر تهران** (برج میلاد + البرز + پرچم شیر و خورشید) و **پس‌زمینه لاگین** (تخت‌جمشید + شیر و خورشید) با پرچم پهلوی بازتولید شدند
- نشان شیر و خورشید در ورود، سایدبار، نوار بالا و فاوآیکون (از نسخه‌های قبل)

### 🔷 جزئیات بیشتر
- **بج‌های هیرو**: Reality · WireGuard · 120FPS · نسخه
- دو ردیف جدید در اطلاعات سیستم: **نرخ فریم زنده پنل** و **هسته پردازنده**
- چیپ‌های ویژگی در صفحه ورود + رفرش کامل ظاهر کارت‌ها و فرم‌ها

### 🛡️ پروتکل WireGuard
- اینباند WireGuard userspace (بدون نیاز به TUN/ماژول کرنل) روی پورت `UDP 51820` (متغیر `WG_PORT`)
- **کلید اختصاصی X25519 برای هر کاربر** (تولید داخلی با پیاده‌سازی RFC 7748 — تست‌شده با بردارهای استاندارد) + آدرس تونل پایدار `10.8.x.y`
- **کانفیگ `.conf` آماده + QR** برای هر کاربر در تب کاربران (اسکن مستقیم در اپ رسمی WireGuard) + دکمه دانلود فایل
- آمار مصرف هر کاربر روی WG (فیلد `email` در peers) → سهمیه و انقضا روی WG هم اعمال می‌شود
- کلید/خاموش سراسری در تنظیمات + استثنا برای هر کاربر، مثل بقیه پروتکل‌ها
- کلیدها از `SECRET` مشتق می‌شوند و با دیپلوی مجدد ثابت می‌مانند

> [!WARNING]
> WireGuard روی **UDP** کار می‌کند. Railway فقط TCP/HTTPS ارائه می‌دهد، پس WG روی Railway **وصل نمی‌شود** — برای سرور شخصی/VPS پورت UDP را در فایروال باز کنید. بقیه پروتکل‌ها روی Railway عادی‌اند.

### ⚡ سرعت و پایداری (کانفیگ‌های بهینه‌شده)
| لایه | بهینه‌سازی |
|---|---|
| سرور Xray | `bufferSize` دوبرابر (512)، `connIdle` طولانی‌تر (300s) → تونل گرم، re-handshake کمتر |
| سرور Xray | `TCP Fast Open` + `NoDelay` + keepalive روی خروجی مستقیم |
| nginx | `worker_processes auto`، `worker_connections 16384`، `multi_accept`، `tcp_nopush`، `keepalive_requests 100000` |
| کانفیگ کلاینت | `sockopt` (FastOpen/NoDelay/keepalive) روی همه خروجی‌ها از جمله Reality |
| کانفیگ کلاینت | **بلاک QUIC (UDP:443)** → مرورگر روی HTTP/2/TCP می‌رود؛ داخل تونل TCP سریع‌تر و پایدارتر است |
| پینگ | ترکیب early-data، keepalive طولانی و بلاک QUIC → نوسان real-delay کمتر و سرعت دانلود/آپلود بالاتر |

### 🐛 باگ‌های رفع‌شده
- چیپ پروتکل‌های **خاموش‌شده در تنظیمات سرور** برای کاربران غیرقابل انتخاب شد (قبلاً روشن نمایش داده می‌شد)
- کانفیگ JSON کاربر بدون پروتکل فعال، حالا **خطای 400** می‌دهد (قبلاً 200 با بدنه خطا)
- `wg_ip` کاربران در **بازیابی پشتیبان** حفظ می‌شود (با اعتبارسنجی)
- برچسب چیپ WireGuard و رندر single-word پروتکل‌ها اصلاح شد

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
کلاینت ──UDP──▶ (فقط VPS) ──▶ Xray (WireGuard) روی پورت 51820
```

## محتوای این پکیج

```
nullgate-panel-v2.3.0/
├── panel.html            ← پنل وب (نسخه 2.3.0 — جایگزین فایل قبلی در ریپو)
├── panel.py              ← سرور پنل (نسخه 2.3.0 — جایگزین فایل قبلی در ریپو)
├── README.md             ← همین فایل
├── nullgate-logo.png     ← لوگوی شیر و خورشید
└── screenshots/
    ├── 01-login.png            صفحه ورود (تخت‌جمشید + پرچم شیر و خورشید)
    ├── 02-dashboard-full.png   داشبورد (تم مشکی + بنر تهران + بج‌ها)
    ├── 04-conns-full.png       اینباندها + پنل WireGuard
    ├── 06-settings-full.png    تنظیمات + کلید WireGuard
    ├── 09-client-open.png      جزئیات کاربر + کانفیگ WG + QR
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
| `WG_PORT` | `51820` | پورت UDP وایرگارد (فقط برای VPS اهمیت دارد) |
| `DOMAIN` | *(اختیاری)* | اگر دامنه اختصاصی دارید |

4. `Settings → Networking → Generate Domain` با Target Port برابر `8080`.
5. (پیشنهادی) `Settings → Volumes` با مسیر `/data` تا کاربران و تنظیمات پاک نشوند.
6. پنل: `https://<دامنه>/panel`

## لینک اشتراک (Sub)

در تب **کاربران**، دکمه «کپی ساب» را بزنید و لینک را در کلاینت به‌عنوان Subscription اضافه کنید:

- **v2rayNG**: منو → Subscription group setting → + → لینک را بچسبانید → Update subscription
- **Hiddify / Streisand / V2Box**: افزودن پروفایل از کلیپ‌بورد

با یک لینک، همه پروتکل‌ها (WS، XHTTP، HTTPUpgrade، VMess، Trojan و Reality) یکجا وارد می‌شوند. توکن هر کاربر از `UUID` ساخته می‌شود و با دیپلوی دوباره ثابت می‌ماند.

## WireGuard

WG از ساب پشتیبانی نمی‌کند (اپ رسمی WG ساب نمی‌خورد)؛ کانفیگ هر کاربر:

1. تب **کاربران** → ردیف کاربر را باز کنید (یا منوی سه‌نقطه)
2. بخش **WireGuard** → «نمایش کانفیگ و QR» یا «دانلود .conf»
3. در اپ WireGuard: `+` → `Create from QR code` (اسکن) یا import فایل conf

فایل پیکربندی سرور (شامل اینباند WG) از **ابزارها → دانلود پیکربندی سرور** قابل بررسی است.

## فعال‌کردن TCP Proxy

1. Railway → سرویس → `Settings → Networking → TCP Proxy`
2. پورت را **9000** بگذارید.
3. سرویس را یک‌بار Redeploy کنید.

## متغیرهای اختیاری

`WS_PATH` `XHTTP_PATH` `HU_PATH` `VMESS_PATH` `TROJAN_PATH` · `WG_PORT` (پورت UDP وایرگارد) · `SECRET` (منبع توکن‌ها و کلیدهای WG؛ پیش‌فرض `UUID`) · `TCP_HOST` / `TCP_PORT` · `REALITY_PRIVATE_KEY` / `REALITY_SHORT_ID`

## نکات

- با ساخت یا حذف کاربر، Xray چند ثانیه ری‌استارت می‌شود.
- ترافیک کاربران به شبکه خصوصی Railway (`*.railway.internal`) و آدرس‌های داخلی مسدود است.
- اگر XHTTP روی کلاینتی وصل نشد، همان کلاینت را به‌روز کنید یا موقتاً از WS / HTTPUpgrade استفاده کنید.
- پلن رایگان Railway سقف مصرف دارد.
- **سنجش فریم‌ریت**: در کنسول مرورگر (F12) عبارت `document.documentElement.dataset.fps` را بزنید؛ روی نمایشگر 120Hz حدود `120` می‌بینید (داخل پنل هم در «اطلاعات سیستم → نرخ فریم پنل» زنده است).
- WireGuard روی Railway کار نمی‌کند (نداشتن UDP)؛ در تنظیمات می‌توانید خاموشش کنید تا اینباندش ساخته نشود.
