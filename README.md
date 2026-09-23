<div align="center">

<img src="nullgate-logo.png" alt="NullGate — Lion & Sun emblem" width="640">

<br><br>

[![core](https://img.shields.io/badge/core-Xray_%2B_sing--box-00ff66?style=flat-square&labelColor=000000)](https://github.com/XTLS/Xray-core)
[![platform](https://img.shields.io/badge/platform-Railway-7cff3f?style=flat-square&labelColor=000000)](https://railway.app)
[![ui](https://img.shields.io/badge/ui-RTL_Persian-d9b44a?style=flat-square&labelColor=000000)]()
[![version](https://img.shields.io/badge/version-2.4.0-d9b44a?style=flat-square&labelColor=000000)]()
[![fps](https://img.shields.io/badge/render-120_FPS-00d9ff?style=flat-square&labelColor=000000)]()
[![protocols](https://img.shields.io/badge/protocols-11-889199?style=flat-square&labelColor=000000)]()
[![use](https://img.shields.io/badge/use-personal_only-7cff3f?style=flat-square&labelColor=000000)]()

**NullGate** یک سرویس **دو هسته‌ای** (Xray + sing-box) با پنل وب فارسی، لینک اشتراک، پشتیبانی از **TCP Proxy** ریلوی و لوگوی شیر و خورشید است.
در نسخه **2.4.0** هسته‌ی دوم **sing-box** اضافه شد و پنل با **11 پروتکل/ترنسپورت** عرضه می‌شود: AnyTLS، ShadowTLS v3 (+ Shadowsocks-2022)، Hysteria2، TUIC v5، VLESS·Reality·gRPC به همراه پروتکل‌های همیشگی.

</div>

> [!IMPORTANT]
> این پروژه صرفاً برای استفاده شخصی طراحی شده است.

---

## تازه‌های نسخه 2.4.0

### ⚙️ هسته‌ی دوم: sing-box
- پنل در استارت‌آپ **sing-box را پیدا یا خودکار دانلود می‌کند** (نسخه پین‌شده 1.12.9، ~10MB؛ با `SINGBOX_AUTO=0` خاموش می‌شود)
- هر دو هسته با هم مدیریت می‌شوند: watchdog، ری‌استارت خودکار، اعمال کاربران/سهمیه/انقضا روی هر دو
- کانفیگ سرور sing-box قبل از اجرا با `sing-box check` اعتبارسنجی می‌شود
- **کلید/رمز اختصاصی هر کاربر** برای همه پروتکل‌های جدید — مشتق از `SECRET` و پایدار بین دیپلوی‌ها

### 🆕 پروتکل‌های جدید
| پروتکل | هسته | ترنسپورت | روی Railway | روی VPS |
|---|---|---|---|---|
| **AnyTLS** | sing-box | TCP (TCP Proxy شماره ۳) | ✅ | ✅ |
| **ShadowTLS v3 + SS2022** | sing-box | TCP (TCP Proxy شماره ۴) | ✅ | ✅ |
| **VLESS · Reality · gRPC** | Xray | TCP (TCP Proxy شماره ۲) | ✅ | ✅ |
| **Hysteria2** | sing-box | UDP/QUIC | ❌ خودکار خاموش | ✅ |
| **TUIC v5** | sing-box | UDP/QUIC | ❌ خودکار خاموش | ✅ |

- **Railway خودکار شناسایی می‌شود** (`RAILWAY_*`) — پروتکل‌های UDP آنجا ساخته نمی‌شوند تا کانفیگ خراب به دست کاربر نرسد؛ روی VPS خودشان فعال می‌شوند (یا `FORCE_UDP=1`)
- **AnyTLS**: پروتکل ضدتشخیص روی TLS واقعی؛ **ShadowTLS v3**: خودش را شبیه TLS یک سایت واقعی (`STLS_SNI`) نشان می‌دهد و Shadowsocks-2022 پشتش رمز می‌کند
- **Reality·gRPC**: همان هویت Reality با ترنسپورت gRPC — شکل ترافیک متفاوت برای شرایط اختلال (بدون `flow` چون Vision فقط با TCP خام کار می‌کند)

### 📲 خروجی‌های جدید برای هر کاربر
- **لینک‌های تکی** برای همه پروتکل‌های جدید (`anytls://`، `ss://+plugin shadow-tls`، `hy2://`، `tuic://`) + QR
- **کانفیگ کامل sing-box (JSON)** با یک کلیک — همه پروتکل‌های فعال کاربر + مسیربندی (بلاک تبلیغات، ایران مستقیم، بلاک QUIC) + WireGuard به‌صورت endpoint جدید
- همه لینک‌ها داخل **لینک اشتراک** هر کاربر هم قرار می‌گیرند و خودکار به‌روز می‌شوند

### ✅ اعتبارسنجی واقعی (این نسخه تست خودکار دارد)
- کانفیگ‌های سرور و کلاینت sing-box با **باینری واقعی sing-box 1.12.9** (`sing-box check`) تأیید می‌شوند
- تست E2E: اتصال واقعی کلاینت از طریق **هر ۴ پروتکل جدید** به اینترنت — همه پاس شدند
- SS2022 چندکاربره نیازمند PSK سرور است؛ این پنل الگوی استاندارد جامعه را پیاده کرده: **احراز هویت هر کاربر در لایه ShadowTLS + رمزنگاشت مشترک SS2022**

---

## تازه‌های نسخه 2.3.0 (خلاصه)
- 🖤 **تم مشکی خالص** — حذف کامل سرمه‌ای؛ 🦁 **پرچم پهلوی (شیر و خورشید)** در بنر تهران و تخت‌جمشید
- 🛡️ **WireGuard** با کلید اختصاصی هر کاربر + `.conf` و QR
- ⚡ **سرعت/پینگ**: بافر دوبرابر، FastOpen/NoDelay، nginx تیون‌شده، بلاک QUIC → دانلود/آپلود بالاتر و real-delay ثابت‌تر

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

کلاینت ──TCP──▶ TCP Proxy #1 ──▶ Xray      VLESS · Reality · TCP        (9000)
کلاینت ──TCP──▶ TCP Proxy #2 ──▶ Xray      VLESS · Reality · gRPC       (9001)
کلاینت ──TCP──▶ TCP Proxy #3 ──▶ sing-box  AnyTLS                       (8443)
کلاینت ──TCP──▶ TCP Proxy #4 ──▶ sing-box  ShadowTLS v3 + SS2022        (8444)
کلاینت ──UDP──▶ (فقط VPS)      ──▶ sing-box  Hysteria2 (8445) · TUIC (8446)
کلاینت ──UDP──▶ (فقط VPS)      ──▶ Xray      WireGuard                    (51820)
```

## محتوای این پکیج

```
nullgate-panel-v2.4.0/
├── panel.html            ← پنل وب (نسخه 2.4.0 — جایگزین فایل قبلی در ریپو)
├── panel.py              ← سرور پنل (نسخه 2.4.0 — جایگزین فایل قبلی در ریپو)
├── README.md             ← همین فایل
├── nullgate-logo.png     ← لوگوی شیر و خورشید
└── screenshots/          ← تصاویر پنل
```

> [!NOTE]
> این پکیج فقط **فایل‌های تغییرکرده** را شامل می‌شود. ریپوی کامل همچنان به `Dockerfile`، `index.html`، `LICENSE` و فایل‌های قبلی خودش نیاز دارد — دو فایل `panel.html` و `panel.py` را فقط جایگزین کنید.

## راه‌اندازی

**اگر قبلاً دیپلوی کرده‌اید:** فقط `panel.html` و `panel.py` را در ریپو جایگزین و Redeploy کنید — همین. sing-box در اولین اجرا خودش دانلود می‌شود (چند ثانیه در لاگ دیده می‌شود).

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
5. (پیشنهادی) `Settings → Volumes` با مسیر `/data` تا کاربران و تنظیمات و گواهی‌ها پاک نشوند.
6. پنل: `https://<دامنه>/panel`

## فعال‌کردن پروتکل‌های TCP (Railway)

هر سرویسِ روی پورت خام، یک **TCP Proxy** جدا در Railway می‌خواهد (`Settings → Networking → TCP Proxy` → پورت داخلی را بدهید → دامنه و پورت عمومی را در Variables بگذارید → Redeploy):

| # | پورت داخلی | متغیرها | پروتکل |
|---|---|---|---|
| 1 | `9000` | `TCP_HOST` / `TCP_PORT` | VLESS · Reality · TCP |
| 2 | `9001` | `TCP2_HOST` / `TCP2_PORT` | VLESS · Reality · gRPC |
| 3 | `8443` | `ANYTLS_HOST` / `ANYTLS_PORT` | AnyTLS |
| 4 | `8444` | `STLS_HOST` / `STLS_PORT` | ShadowTLS v3 + SS2022 |

تا وقتی TCP Proxy‌های ۲ تا ۴ ساخته نشده‌اند، پروتکل‌هایشان «غیرفعال» نشان داده می‌شوند و لینکی ساخته نمی‌شود — بقیه پنل عادی کار می‌کند.

**روی VPS** هیچ TCP Proxy‌ای لازم نیست: پورت‌ها را در فایروال باز کنید و دامنه/IP را در `address` پنل بگذارید؛ لینک‌ها خودکار ساخته می‌شوند.

## لینک اشتراک (Sub)

در تب **کاربران**، دکمه «کپی ساب» را بزنید و لینک را در کلاینت به‌عنوان Subscription اضافه کنید:

- **v2rayNG / NekoBox / Hiddify / Streisand / V2Box**: افزودن از کلیپ‌بورد → Update subscription

با یک لینک همه پروتکل‌های فعال کاربر (WS، XHTTP، HTTPUpgrade، VMess، Trojan، Reality، Reality-gRPC، AnyTLS، ShadowTLS و روی VPS: Hysteria2/TUIC) یکجا وارد می‌شوند. برای اپ رسمی **sing-box**، دکمه «کانفیگ sing-box» در جزئیات هر کاربر خروجی JSON کامل می‌دهد.

## متغیرهای اختیاری

- مسیرها: `WS_PATH` `XHTTP_PATH` `HU_PATH` `VMESS_PATH` `TROJAN_PATH`
- TCP Proxy‌ها: `TCP_HOST`/`TCP_PORT` · `TCP2_HOST`/`TCP2_PORT` (`TCP2_APP_PORT=9001`) · `ANYTLS_HOST`/`ANYTLS_PORT` (`ANYTLS_APP_PORT=8443`) · `STLS_HOST`/`STLS_PORT` (`STLS_APP_PORT=8444`)
- sing-box: `SINGBOX_BIN` · `SINGBOX_VERSION` (پیش‌فرض 1.12.9) · `SINGBOX_AUTO=0` (غیرفعال‌کردن دانلود) · `GRPC_SERVICE` (نام سرویس gRPC) · `STLS_SNI` (سایت دست‌دادن ShadowTLS، پیش‌فرض www.cloudflare.com)
- UDP (فقط VPS): `HY2_PORT=8445` · `TUIC_PORT=8446` · `WG_PORT=51820` · `FORCE_UDP=1`
- Reality: `REALITY_PRIVATE_KEY` / `REALITY_SHORT_ID` · `SECRET` (منبع توکن‌ها و همه کلیدها؛ پیش‌فرض `UUID`)

## نکات

- با ساخت یا حذف کاربر، هر دو هسته چند ثانیه‌ای ری‌استارت می‌شوند.
- ترافیک کاربران به شبکه خصوصی Railway (`*.railway.internal`) و آدرس‌های داخلی مسدود است (در هر دو هسته).
- گواهی TLS اینباند‌های sing-box (AnyTLS/Hysteria2/TUIC) **سلف‌ساین** است؛ لینک‌ها با `insecure=1` ساخته می‌شوند. روی VPS می‌توانید گواهی واقعی بگذارید (مسیر `DATA_DIR/certs`).
- آمار مصرف هر کاربر روی پروتکل‌های Xray و WireGuard دقیق است؛ برای AnyTLS/ShadowTLS (سنگ‌بنای sing-box) سهمیه به‌صورت فعال/غیرفعال شدن خودکار اعمال می‌شود نه شمارش بایت.
- اگر XHTTP روی کلاینتی وصل نشد، همان کلاینت را به‌روز کنید یا موقتاً از WS / HTTPUpgrade استفاده کنید.
- پلن رایگان Railway سقف مصرف دارد؛ هر هسته ~۳۰-۶۰MB RAM اضافه می‌آورد.
- **سنجش فریم‌ریت**: کنسول مرورگر → `document.documentElement.dataset.fps` (داخل پنل هم در «اطلاعات سیستم» زنده است).
- WireGuard/Hysteria2/TUIC روی Railway کار نمی‌کنند (نداشتن UDP) و در تنظیمات قابل خاموش‌کردن‌اند.
