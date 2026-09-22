<div align="center">

<img src="nullgate-logo.png" alt="NullGate — Lion & Sun emblem" width="640">

<br><br>

[![core](https://img.shields.io/badge/core-Xray--core-00ff66?style=flat-square&labelColor=000000)](https://github.com/XTLS/Xray-core)
[![platform](https://img.shields.io/badge/platform-Railway-7cff3f?style=flat-square&labelColor=000000)](https://railway.app)
[![ui](https://img.shields.io/badge/ui-RTL_Persian-d9b44a?style=flat-square&labelColor=000000)]()
[![version](https://img.shields.io/badge/version-2.2.0-d9b44a?style=flat-square&labelColor=000000)]()
[![fps](https://img.shields.io/badge/render-120_FPS-00d9ff?style=flat-square&labelColor=000000)]()
[![use](https://img.shields.io/badge/use-personal_only-7cff3f?style=flat-square&labelColor=000000)]()

**NullGate** یک سرویس مبتنی بر **Xray-core** با پنل وب فارسی، لینک اشتراک (Subscription)، پشتیبانی از **TCP Proxy** ریلوی و لوگوی شیر و خورشید است.
نسخه **2.0.0** ارتقای بزرگ (QR، پشتیبان‌گیری، مدیریت پروتکل‌ها، لاگ زنده) بود؛ **2.0.1** هویت بصری را کامل کرد؛ **2.1.0** باگ‌های نمایشی را گرفت؛ و **2.2.0** پنل را **کاملاً مطابق طرح مرجع** بازطراحی کرده و موتور رندر را به **۱۲۰ فریم بر ثانیه** ارتقا داده است.

</div>

> [!IMPORTANT]
> این پروژه صرفاً برای استفاده شخصی طراحی شده است.

---

## تازه‌های نسخه 2.2.0

### 🎨 بازطراحی کامل مطابق طرح مرجع
- **چیدمان یک‌به‌یک مطابق عکس مرجع**: جای سایدبار، ترتیب کارت‌های آمار، گرید دوم، لجند نمودار و فوتر (شعار سمت راست با خط طلایی، © سمت چپ)
- **بنر هیرو جدید تهران**: برج میلاد + کوه + پرچم شیر و خورشید — و **پس‌زمینه صفحه ورود** با ستون‌های تخت‌جمشید و نشان شیر و خورشید طلایی (تصاویر تولیدشده با AI، داخل خود `panel.html` جاسازی شده‌اند؛ بدون فایل جانبی)
- **جدول «کاربران اخیر» فشرده** مثل مرجع (بدون UUID و چک‌باکس اضافه در داشبورد)
- بج‌های عددی سمت راست مقدار (LTR داخل بج)، آیکون اینباندها از globe به **server**، رنگ حلقه **RAM سبز** و **Storage آبی**
- آیکون‌های فرم ورود مطابق مرجع: کاربر/قفل چپ، دکمه نمایش رمز (چشم) راست

### ⚡ موتور رندر ۱۲۰ فریم بر ثانیه
- همه حلقه‌ها و انیمیشن‌ها روی **`requestAnimationFrame`** — بدون `setInterval` برای رندر؛ روی نمایشگرهای 120Hz دقیقاً با 120fps اجرا می‌شود
- **Tween حلقه‌های پیشرفت مستقل از نرخ فریم** (در هر نمایشگری مدت انیمیشن یکسان و حرکت نرم است)
- نوارهای مصرف با **`scaleX`** (فقط compositor — بدون layout/paint) + لایه CSS عملکرد: `contain` و `will-change`
- چارت‌ها فقط با تغییر واقعی داده و داخل rAF بازترسیم می‌شوند (بدون رندر بیهوده)
- **پروب FPS داخلی**: در کنسول مرورگر `document.documentElement.dataset.fps` نرخ واقعی را نشان می‌دهد

### 🐛 باگ‌های رفع‌شده
| باگ | توضیح |
|---|---|
| اعداد دوجهته (bidi) | نمایش وارونه GB/TB در کارت‌ها، جدول و دونات — با `unicode-bidi: plaintext` و قاب‌های LTR حل شد |
| چک‌باکس تب کاربران | فقط ردیف اول تیک می‌خورد (`list.map(clientRow)` ایندکس را به تابع پاس می‌داد) |
| وضعیت nginx در سلامت سرویس | در `panel.py` منطق `poll() is None` اصلاح شد |
| سرریز موبایل | حلقه‌ها و فونت‌ها در `@560px` کوچک شدند، `flex-wrap` برای وضعیت‌ها |
| جهت شعار لاگین | راست‌به‌چپِ اشتباه اصلاح شد |
| فشردگی نام/وضعیت در اشتراک‌ها | با wrap حل شد |
| واترمارک صفحه ورود | حذف شد |
| منوی اینباندها + کارت آپ‌تایم ۹۹.۹٪ | اضافه شدند (`upPctText()` واقعی) |

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

> **چرا XHTTP قبلاً کار نمی‌کرد؟** مسیر XHTTP برای هر اتصال زیرمسیر می‌سازد (`/xhttp/<id>/<n>`) ولی fallback خود Xray فقط مسیر دقیق را تطبیق می‌دهد. حالا nginx بر اساس **پیشوند مسیر** جدا می‌کند و حالت `packet-up` (سازگار با پروکسی‌های HTTP) استفاده می‌شود.

## محتوای این پکیج

```
nullgate-panel-v2.2.0/
├── panel.html            ← پنل وب (نسخه 2.2.0 — جایگزین فایل قبلی در ریپو)
├── panel.py              ← سرور پنل (نسخه 2.2.0 — جایگزین فایل قبلی در ریپو)
├── README.md             ← همین فایل
├── nullgate-logo.png     ← لوگوی شیر و خورشید
└── screenshots/
    ├── 01-login.png            صفحه ورود (پس‌زمینه تخت‌جمشید)
    ├── 02-dashboard-full.png   داشبورد کامل (بنر تهران، کارت‌ها، دونات)
    ├── 03-clients.png          تب کاربران
    ├── 07-reports-full.png     گزارش‌ها
    └── 10-mobile-dash-full.png داشبورد در موبایل
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
| `UUID` | یک UUID ثابت | **حتماً بگذارید** تا لینک اشتراک و کلید Reality با هر دیپلوی عوض نشود |
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

## فعال‌کردن TCP Proxy

1. Railway → سرویس → `Settings → Networking → TCP Proxy`
2. پورت را **9000** بگذارید.
3. سرویس را یک‌بار Redeploy کنید.

ریلوی خودش متغیرهای `RAILWAY_TCP_PROXY_DOMAIN` و `RAILWAY_TCP_PROXY_PORT` را می‌سازد و پنل آن‌ها را می‌خواند؛ لینک **VLESS · Reality** به‌صورت خودکار به لینک‌ها و ساب اضافه می‌شود. اگر پورت دیگری بخواهید، همان را در Railway و در متغیر `TCP_APP_PORT` بگذارید.

## متغیرهای اختیاری

`WS_PATH` `XHTTP_PATH` `HU_PATH` `VMESS_PATH` `TROJAN_PATH` · `SECRET` (منبع توکن‌ها؛ پیش‌فرض `UUID`) · `TCP_HOST` / `TCP_PORT` (اگر TCP Proxy را با دامنه خودتان CNAME کرده‌اید) · `REALITY_PRIVATE_KEY` / `REALITY_SHORT_ID`

## نکات

- با ساخت یا حذف کاربر، Xray چند ثانیه ری‌استارت می‌شود.
- ترافیک کاربران به شبکه خصوصی Railway (`*.railway.internal`) و آدرس‌های داخلی مسدود است.
- اگر XHTTP روی کلاینتی وصل نشد، همان کلاینت را به‌روز کنید یا موقتاً از WS / HTTPUpgrade استفاده کنید.
- در لاگ Railway، خروجی `nginx -t` و پیام‌های Xray دیده می‌شود.
- پلن رایگان Railway سقف مصرف دارد.
- **سنجش فریم‌ریت**: در کنسول مرورگر (F12) عبارت `document.documentElement.dataset.fps` را بزنید؛ روی نمایشگر 120Hz باید حدود `120` ببینید.
