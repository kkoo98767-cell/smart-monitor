"""المراقب الذكي - النسخة 5 (قراءة متعددة الطبقات)
يشتغل مرة باليوم، ويرسل لك فقط أقوى العروض الحقيقية.

الجديد في النسخة 5:
- إصلاح قراءة السعر: ما عاد يقرأ "وفر 330" أو "رصيد جولد" أو "36 دفعة" كأنها سعر
  (يتحقق: السعر = السعر القديم - مبلغ التوفير)
- قراءة كل متجر بعدة طرق بالترتيب لين تنجح: طلب بنفس بصمة متصفح كروم (curl_cffi)،
  طلب عادي، متصفح حقيقي بوضع التخفي (مع التقاط بيانات JSON الداخلية)، وأخيراً
  خريطة الموقع (sitemap)
- ما يرسل أي رسالة خطأ لتيليجرام. صحة المتاجر تنكتب في سجل التشغيل وملخص GitHub
- حماية من الأسعار الغلط: قفزة سعر مستحيلة عن تاريخ المنتج تُتجاهل

المتاجر: أمازون السعودية، نون، إكسترا، علي إكسبرس، درعة، عساف، ثنيان،
         الماجد للعود، جولدن سنت

✅ موثّق    = السعر أقل بوضوح من تاريخ أسعار المنتج نفسه عندنا (بعد 7 أيام جمع)
              وقريب من أقل سعر شافه خلال 90 يوم.
⚠️ غير موثّق = مقارنة بالسعر القديم المكتوب في المتجر فقط (حدوده أعلى بكثير
              لأن كثير من المتاجر تكتب خصومات وهمية).

المتغيرات المطلوبة (تنحط كأسرار في GitHub): TG_TOKEN و TG_CHAT

╔══════════════════════════════════════════════════════════════╗
║  وين تغيّر؟ كل شي تغيّره موجود في القسم الأول "الإعدادات"       ║
║  تحت، ولا تحتاج تلمس أي شي بعده.                              ║
╚══════════════════════════════════════════════════════════════╝
"""
import json
import os
import random
import re
import sqlite3
import statistics
import time
from collections import Counter
from datetime import date, timedelta
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT = os.environ["TG_CHAT"]
START = time.time()

# ======================================================================
#                          1) الإعدادات (غيّر هنا)
# ======================================================================

# ---- أ) كم عرض يوصلك في اليوم كحد أقصى ----
MAX_VERIFIED = 10        # عروض ✅ الموثّقة
MAX_UNVERIFIED = 5       # عروض ⚠️ غير الموثّقة
SHOW_UNVERIFIED = True   # اجعلها False لو تبغى ✅ فقط (الأنقى)

# ---- ب) فئات المنتجات ----
# keywords      : كلمات البحث (أضف أو احذف براحتك)
# verified_min  : أقل خصم لعرض ✅   (0.30 = 30%)
# unverified_min: أقل خصم لعرض ⚠️
# min_price     : تجاهل أي منتج أرخص من كذا ريال (يشيل الإكسسوارات الرخيصة)
# exclude       : كلمات لو ظهرت في اسم المنتج نتجاهله
CASES = r"(غطاء|جراب|كفر|حماية|واقي|ستيكر|حامل|مسند|case|cover|protector|holder|stand|bracket)"
KIDS = r"(أطفال|اطفال|kids|نسائي|حريمي|women)"
CATEGORIES = {
    "phones": {
        "name": "جوالات", "keywords": ["ايفون", "سامسونج جالكسي", "شاومي هاتف"],
        "verified_min": 0.15, "unverified_min": 0.30, "min_price": 400, "exclude": CASES},
    "tv": {
        "name": "تلفزيونات", "keywords": ["تلفزيون ذكي 55 بوصة", "تلفزيون 65 بوصة"],
        "verified_min": 0.25, "unverified_min": 0.40, "min_price": 600, "exclude": CASES},
    "gaming_monitor": {
        "name": "شاشات جيمنج", "keywords": ["شاشة جيمنج 144", "شاشة جيمنج 27 بوصة"],
        "verified_min": 0.25, "unverified_min": 0.40, "min_price": 400, "exclude": CASES},
    "headphones": {
        "name": "سماعات", "keywords": ["سماعات لاسلكية", "سماعات جيمنج", "سماعات سوني"],
        "verified_min": 0.30, "unverified_min": 0.45, "min_price": 80,
        "exclude": CASES + r"|(وسادة|اسفنج|pads|حافظة)"},
    "ps_controller": {
        "name": "يدات بلايستيشن", "keywords": ["يد تحكم PS5", "DualSense"],
        "verified_min": 0.20, "unverified_min": 0.35, "min_price": 120,
        "exclude": CASES + r"|(grip|شاحن|skin)"},
    "pc": {
        "name": "إكسسوارات PC", "keywords": ["ماوس جيمنج", "كيبورد ميكانيكي", "SSD", "ذاكرة RAM"],
        "verified_min": 0.30, "unverified_min": 0.45, "min_price": 60,
        "exclude": CASES + r"|(sticker|pad فقط)"},
    "home": {
        "name": "أجهزة منزلية", "keywords": ["مكيف", "مكنسة", "قلاية هوائية"],
        "verified_min": 0.30, "unverified_min": 0.45, "min_price": 100, "exclude": CASES},
    "shoes": {
        "name": "أحذية", "keywords": ["حذاء رياضي رجالي", "نايك", "اديداس"],
        "verified_min": 0.45, "unverified_min": 0.60, "min_price": 80, "exclude": KIDS},
    "clothes": {
        "name": "ملابس", "keywords": ["ثوب رجالي", "قميص رجالي", "بنطلون رجالي"],
        "verified_min": 0.45, "unverified_min": 0.60, "min_price": 50, "exclude": KIDS},
    "hoodies": {
        "name": "هوديز", "keywords": ["هودي رجالي", "هودي"],
        "verified_min": 0.45, "unverified_min": 0.60, "min_price": 50, "exclude": KIDS},
    "perfumes": {
        "name": "عطور وعود", "keywords": ["عطر رجالي"],
        "verified_min": 0.30, "unverified_min": 0.55, "min_price": 40, "exclude": r"(عينة|sample|tester)"},
}

# ---- ج) المتاجر ----
# unverified_cap: أقل خصم ⚠️ لهذا المتجر (أعلى من الفئة لو المتجر خصوماته وهمية)
# skip         : فئات ما نبحث عنها في هذا المتجر
# strategies = طرق القراءة بالترتيب (أول طريقة تنجح تُعتمد):
#   "requests" طلب عادي | "cffi" طلب ببصمة متصفح كروم | "browser" متصفح حقيقي بوضع التخفي
CRAWL_STORES = [   # متاجر نقرأ أقسامها تلقائياً (فئتها عطور)
    {"name": "درعة", "unverified_cap": 0.65, "strategies": ("requests", "cffi", "browser"),
     "start": [
        "https://www.deraahstore.com/ar_sa/perfumes/all-perfumes",
        "https://www.deraahstore.com/ar_sa/oud/oriental-perfumes",
        "https://www.deraahstore.com/ar_sa/exclusive-offers",
        "https://www.deraahstore.com/ar_sa/11-11-offers"]},
    {"name": "عساف", "unverified_cap": 0.60, "strategies": ("requests", "cffi", "browser"),
     "start": ["https://3saf.com/ar"]},
    {"name": "ثنيان", "unverified_cap": 0.60, "strategies": ("requests", "cffi", "browser"),
     "start": ["https://thnyan.com/ar"]},
    {"name": "الماجد للعود", "unverified_cap": 0.60, "strategies": ("cffi", "browser", "requests"),
     "start": ["https://www.almajed4oud.com/", "https://www.almajed4oud.com/ar"]},
    {"name": "جولدن سنت", "unverified_cap": 0.55, "strategies": ("cffi", "browser", "requests"),
     "start": ["https://www.goldenscent.com/ar", "https://www.goldenscent.com/ar/perfumes",
               "https://www.goldenscent.com/ar/offers"]},
]
SEARCH_STORES = [  # متاجر نبحث فيها بالكلمات
    {"name": "نون", "unverified_cap": 0.45, "skip": [], "strategies": ("browser", "cffi"),
     "url": "https://www.noon.com/saudi-ar/search/?q={q}"},
    {"name": "إكسترا", "unverified_cap": 0.45, "strategies": ("browser", "cffi", "requests"),
     "skip": ["shoes", "clothes", "hoodies", "perfumes"],
     "url": "https://www.extra.com/ar-sa/search/?text={q}"},
    {"name": "علي إكسبرس", "unverified_cap": 0.75, "skip": ["perfumes"],
     "strategies": ("browser", "cffi"),
     "url": "https://www.aliexpress.com/w/wholesale-{q}.html"},
]
AMAZON_STRATEGIES = ("requests", "cffi", "browser")
AMAZON_UNVERIFIED_CAP = 0.45

# ---- د) حساسية التحقق ----
MIN_HISTORY_DAYS = 7     # كم يوم أسعار نحتاج قبل ما نسمي العرض ✅
NEAR_LOW = 1.03          # لازم السعر <= أقل سعر سابق × هذا الرقم
MIN_RATING = 4.0         # (أمازون) تجاهل التقييم الأقل
MAX_DISCOUNT = 0.90      # خصم أعلى من كذا غالباً خطأ في الصفحة

# ---- هـ) حدود تقنية ----
HISTORY_DAYS = 90
MAX_CRAWL_PAGES = 8
TIME_BUDGET = 55 * 60    # ثواني؛ بعدها يوقف البحث ويرسل اللي جمعه
HEAVY_STORE_BUDGET = 8 * 60   # أقصى وقت لكل متجر ثقيل (نون/إكسترا/علي) في اليوم
SITEMAP_PAGES = 40       # لو الموقع مقفول: كم منتج نقرأ من خريطة الموقع
MIN_DELAY = 3.0          # فاصل بين الطلبات (ثواني)
MAX_DELAY = 6.0
RETRY_BASE = 10          # أول انتظار عند 503/429 (يتضاعف)
RETRY_MAX_WAIT = 90
WEEKLY_STATUS = True     # رسالة "المراقب شغال" مرة بالأسبوع (بدون أي أخطاء)
PROXY_URL = os.environ.get("PROXY_URL", "").strip()   # اختياري: لو أي متجر ظل مقفول

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
}

# ======================================================================
#                      2) الكود الداخلي (لا تلمسه)
# ======================================================================
# ============================ قاعدة البيانات ============================
db = sqlite3.connect("monitor.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS prices(id TEXT, day TEXT, price REAL,
                                  PRIMARY KEY(id, day));
CREATE TABLE IF NOT EXISTS alerted(id TEXT, price REAL, PRIMARY KEY(id, price));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
""")


def meta_get(k):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def meta_set(k, v):
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))
    db.commit()


# ============================ أدوات عامة ============================
def send_telegram(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": text}, timeout=20)
        if r.status_code != 200:
            print("خطأ تيليجرام:", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        print("فشل الإرسال:", e)
        return False


def num(text):
    if text is None:
        return None
    t = str(text).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,"))
    m = re.search(r"\d[\d,]*(?:\.\d+)?", t)
    return float(m.group().replace(",", "")) if m else None


class Blocked(Exception):
    pass


# ============================ جلب الصفحات (طبقات متعددة) ============================
net = Counter()   # عدّاد الأخطاء للسجل فقط: 503 429 403 challenge network
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]
pace = {"now": (MIN_DELAY + MAX_DELAY) / 2, "ok": 0}


def polite_sleep():
    time.sleep(random.uniform(pace["now"] * 0.8, pace["now"] * 1.4))


def _slow():
    pace["now"] = min(pace["now"] * 1.5, 20.0)
    pace["ok"] = 0


def _fast():
    pace["ok"] += 1
    if pace["ok"] >= 10:
        pace["now"] = max(pace["now"] * 0.85, (MIN_DELAY + MAX_DELAY) / 2)
        pace["ok"] = 0


def _proxies():
    return {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None


def _headers(url=None):
    h = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
         "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document",
         "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1"}
    if url:
        u = urlparse(url)
        h["Referer"] = f"{u.scheme}://{u.netloc}/"
    return h


BLOCK_PAT = re.compile(r"(captcha|just a moment|attention required|access denied|"
                       r"enable javascript and cookies|px-captcha|incapsula|"
                       r"request unsuccessful|pardon our interruption|robot check)", re.I)


def looks_blocked(text):
    return len(text) < 60000 and bool(BLOCK_PAT.search(text[:20000]))


def _fetch_with(do_get, url, tries=3):
    """طلب مع إعادة محاولة: 503/429 تأخير متصاعد، 403/تحدي = ننتقل للطريقة التالية."""
    delay = RETRY_BASE
    last = "unknown"
    for attempt in range(tries):
        if time_left() <= 0:
            raise Blocked("انتهى وقت التشغيل")
        try:
            status, text, headers = do_get(url)
        except Exception as e:
            net["network"] += 1
            last = f"network: {str(e)[:40]}"
            if attempt == tries - 1 or time_left() < delay + 5:
                raise Blocked(last)
            time.sleep(delay + random.uniform(0, 3))
            delay = min(delay * 2, RETRY_MAX_WAIT)
            continue
        if status in (429, 503):
            net[str(status)] += 1
            last = f"HTTP {status}"
            _slow()
            ra = (headers or {}).get("Retry-After", "") if headers else ""
            wait = float(ra) if str(ra).strip().isdigit() else delay
            wait = min(wait, RETRY_MAX_WAIT) + random.uniform(0, 3)
            if attempt == tries - 1 or time_left() < wait + 5:
                raise Blocked(last)
            time.sleep(wait)
            delay = min(delay * 2, RETRY_MAX_WAIT)
            continue
        if status in (401, 403, 451):
            net[str(status)] += 1
            _slow()
            raise Blocked(f"HTTP {status}")
        if status >= 400:
            raise Blocked(f"HTTP {status}")
        if looks_blocked(text):
            net["challenge"] += 1
            _slow()
            raise Blocked("challenge/captcha")
        _fast()
        return text
    raise Blocked(last)


_req_session = requests.Session()


def get_requests(url):
    def do(u):
        _req_session.headers.update(_headers(u))
        _req_session.headers["User-Agent"] = random.choice(UAS)
        r = _req_session.get(u, timeout=30, proxies=_proxies())
        return r.status_code, r.text, r.headers
    return _fetch_with(do, url)


_cffi_sessions = {}


def get_cffi(url):
    """طلب ببصمة TLS لمتصفح كروم الحقيقي (يتجاوز كثير من حمايات 403)."""
    try:
        from curl_cffi import requests as cr
    except ImportError:
        raise Blocked("curl_cffi غير مثبت")
    host = urlparse(url).netloc
    sess = _cffi_sessions.get(host)
    if sess is None:
        sess = cr.Session(impersonate="chrome124")
        _cffi_sessions[host] = sess
        try:   # زيارة الصفحة الرئيسية أول عشان ناخذ الكوكيز
            sess.get(f"https://{host}/", timeout=25, headers=_headers(),
                     proxies=_proxies())
            time.sleep(random.uniform(1, 2))
        except Exception:
            pass

    def do(u):
        r = sess.get(u, timeout=30, headers=_headers(u), proxies=_proxies())
        return r.status_code, r.text, r.headers
    return _fetch_with(do, url)


_pw = None
_browser = None
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ar-SA','ar','en-US','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
const _q = window.navigator.permissions && window.navigator.permissions.query;
if (_q) { window.navigator.permissions.query = (p) =>
  p && p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : _q(p); }
"""


def _launch_browser():
    global _pw, _browser
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    kw = {"headless": not os.environ.get("USE_XVFB"),
          "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox",
                   "--disable-dev-shm-usage", "--lang=ar-SA"]}
    if PROXY_URL:
        kw["proxy"] = {"server": PROXY_URL}
    try:
        _browser = _pw.chromium.launch(channel="chrome", **kw)   # كروم الحقيقي لو موجود
    except Exception:
        _browser = _pw.chromium.launch(**kw)


def get_browser(url):
    """متصفح حقيقي بوضع التخفي + نلتقط ردود JSON الداخلية (فيها المنتجات بدقة)."""
    last = "browser"
    for attempt in range(2):
        if time_left() <= 0:
            raise Blocked("انتهى وقت التشغيل")
        ctx = None
        try:
            if _browser is None:
                _launch_browser()
            ctx = _browser.new_context(
                user_agent=UAS[0], locale="ar-SA", timezone_id="Asia/Riyadh",
                viewport={"width": 1366, "height": 850},
                extra_http_headers={"Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8"})
            ctx.add_init_script(STEALTH_JS)
            page = ctx.new_page()
            xhr = []

            def on_resp(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct and re.search(
                            r"(search|catalog|product|listing|items|plp|query|graphql|results)",
                            resp.url, re.I):
                        body = resp.text()
                        if 200 < len(body) < 3_000_000:
                            xhr.append(body)
                except Exception:
                    pass
            page.on("response", on_resp)
            page.route("**/*", lambda r: r.abort() if r.request.resource_type in
                       ("image", "font", "media") else r.continue_())
            resp = page.goto(url, timeout=50000, wait_until="domcontentloaded")
            status = resp.status if resp else 200
            for _ in range(8):          # ننتظر لو فيه صفحة "تحقق" تنحل لحالها
                if "just a moment" not in (page.title() or "").lower():
                    break
                page.wait_for_timeout(2000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            for _ in range(5):          # تمرير لتحميل المنتجات الكسولة
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(700)
            html = page.content()
            extra = "".join('<script type="application/json" data-xhr="1">'
                            + b.replace("</", "<\\/") + "</script>" for b in xhr[:15])
            if status in (403, 429, 503) and looks_blocked(html):
                net[str(status)] += 1
                raise Blocked(f"HTTP {status}")
            if looks_blocked(html) and not xhr:
                net["challenge"] += 1
                raise Blocked("challenge/captcha")
            return html + extra
        except Blocked:
            raise
        except Exception as e:
            last = f"browser: {str(e)[:50]}"
            net["network"] += 1
            time.sleep(3)
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass
    raise Blocked(last)


STRATS = {"requests": get_requests, "cffi": get_cffi, "browser": get_browser}
won = {}   # المتجر -> Counter(الطريقة اللي نجحت) للسجل


# ============================ استخراج المنتجات ============================
# عناصر نتجاهلها لما نقرأ السعر: تقسيط، رصيد/كاش باك، نقاط، شحن، نسب مئوية
BAD = re.compile(r"(دفع|تابي|تمارا|tabby|tamara|قسط|أقساط|installment|شهري|/\s*شهر|×|"
                 r"رصيد|cashback|كاش ?باك|نقاط|points|coins|عملات|مكافأ|شحن|توصيل|"
                 r"shipping|delivery|%|٪)", re.I)
SAVE = re.compile(r"(وفر|وفّر|توفير|توفر|\bsave\b|saving|you save)", re.I)   # مبلغ التوفير مو سعر
CUR = re.compile(r"(ر\.?\s?س|ريال|SAR|SR|﷼)", re.I)
OLD_CLS = re.compile(r"(old|before|was|compare|regular|strike|original|reduced|list-price|line-through)", re.I)
PRICE_CLS = re.compile(r"(price|sale|special|amount|cost|سعر|value)", re.I)
SKIP_HREF = re.compile(r"(cart|login|account|wishlist|checkout|javascript:|mailto:|tel:|#|contact|policy|terms|faq)", re.I)
LOCALE_SEG = re.compile(r"^(ar|en|ar-sa|en-sa|saudi-ar|saudi-en|ar_sa|en_sa|sa-ar|sa-en|sa)$", re.I)


def make_id(store, url):
    segs = [x for x in urlparse(url).path.split("/") if x]
    while segs and LOCALE_SEG.match(segs[0]):
        segs.pop(0)
    return f"{store}:/" + "/".join(segs).lower()


def card_prices(card):
    """يرجع (السعر الحالي, السعر القديم). يتجاهل مبلغ التوفير والرصيد والأقساط،
    ويتحقق: السعر الحالي = السعر القديم - مبلغ التوفير."""
    vals, saves = set(), []
    for el in card.find_all(True):
        if el.name in ("script", "style", "svg"):
            continue
        txt = el.get_text(" ", strip=True)
        if not txt or len(txt) > 45:
            continue
        if SAVE.search(txt):                 # مثل: "وفر 330 ر.س" -> هذا توفير مو سعر
            n = num(txt)
            if n and n > 0:
                saves.append(n)
            continue
        if BAD.search(txt):
            continue
        cls = " ".join(el.get("class", [])) + " " + (el.get("id") or "")
        is_old = el.name in ("del", "s", "strike") or bool(OLD_CLS.search(cls))
        if not (CUR.search(txt) or PRICE_CLS.search(cls) or is_old):
            continue
        n = num(txt)
        if n and n >= 5:
            vals.add((n, is_old))
    cur = sorted(v for v, o in vals if not o)
    olds = [v for v, o in vals if o]
    old = max(olds) if olds else None
    price = None
    if old and saves:                        # التحقق: سعر = قديم - توفير
        for sv in saves:
            target = old - sv
            for c in cur:
                if abs(c - target) <= max(1.5, old * 0.003):
                    price = c
                    break
            if price is None and target > 0 and not cur:
                price = target
            if price is not None:
                break
    if price is None and cur:
        price = cur[0]
        if saves and any(abs(price - sv) < 0.01 for sv in saves) and len(cur) > 1:
            price = cur[1]
    if price is None:
        return None, None
    if old is None:
        if saves:
            old = price + saves[0]           # القديم = الحالي + التوفير
        elif cur and cur[-1] > price * 1.02:
            old = cur[-1]
    if old is not None and old <= price:
        old = None
    if old is not None and price < old * 0.12:   # مستحيل غالباً: خطأ قراءة
        return None, None
    return price, old


def tile_items(soup, base, store):
    out = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if SKIP_HREF.search(href):
            continue
        url = urljoin(base, href).split("#")[0]
        if urlparse(url).netloc != urlparse(base).netloc and "noon" not in store:
            continue
        card, found = a, None
        for _ in range(5):
            card = card.parent
            if card is None or card.name in ("body", "html"):
                break
            hrefs = {x["href"] for x in card.find_all("a", href=True)
                     if not SKIP_HREF.search(x["href"])}
            if len(hrefs) > 3:
                break
            p, o = card_prices(card)
            if p:
                found = (p, o, card)
                break
        if not found:
            continue
        price, old, card = found
        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if len(title) < 4:
            img = card.find("img", alt=True)
            title = (img["alt"] if img else "").strip()
        if len(title) < 4:
            h = card.find(["h2", "h3", "h4"])
            title = h.get_text(" ", strip=True) if h else ""
        if len(title) < 4:
            continue
        pid = make_id(store, url)
        if pid not in out or (old and not out[pid]["old"]):
            out[pid] = {"id": pid, "title": title[:150], "price": price,
                        "old": old, "rating": None, "url": url, "store": store}
    return out


def jsonld_items(soup, base, store):
    out = {}
    for sc in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(sc.string or sc.get_text() or "")
        except Exception:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
                continue
            if not isinstance(d, dict):
                continue
            t = d.get("@type")
            if isinstance(t, list):
                t = t[0] if t else ""
            if t == "Product":
                offers = d.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = num(offers.get("price") or offers.get("lowPrice"))
                url = d.get("url") or offers.get("url")
                if price and url and d.get("name"):
                    url = urljoin(base, url)
                    pid = make_id(store, url)
                    out[pid] = {"id": pid, "title": str(d["name"])[:150],
                                "price": price, "old": None, "rating": None,
                                "url": url, "store": store}
            for k in ("itemListElement", "@graph", "item", "mainEntity"):
                if k in d:
                    stack.append(d[k])
    return out


# --- بيانات JSON الداخلية (Next.js / ردود API) : أدق من قراءة الصفحة ---
PRICE_KEYS = ("sale_price", "salePrice", "special_price", "specialPrice", "final_price",
              "finalPrice", "offer_price", "offerPrice", "selling_price", "sellingPrice",
              "current_price", "currentPrice", "discounted_price", "discountedPrice",
              "min_price", "price")
OLD_KEYS = ("was_price", "wasPrice", "original_price", "originalPrice", "old_price",
            "oldPrice", "list_price", "listPrice", "regular_price", "regularPrice",
            "price_before_discount", "compare_at_price", "compareAtPrice", "mrp",
            "strike_price", "strikePrice", "base_price", "basePrice", "price_was")
NAME_KEYS = ("name", "title", "product_name", "productName", "name_ar", "name_en", "label")
URL_KEYS = ("url", "link", "href", "product_url", "productUrl", "canonical_url",
            "canonicalUrl", "path", "slug", "seo_url", "pdp_url")


def _to_price(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if 1 <= v <= 300000 else None
    if isinstance(v, str):
        n = num(v)
        return n if n and 1 <= n <= 300000 else None
    if isinstance(v, dict):
        for k in ("value", "amount", "current", "raw", "price"):
            if k in v:
                p = _to_price(v[k])
                if p:
                    return p
    return None


def walk_products(data, base, store, out, limit=300000):
    stack, seen = [data], 0
    while stack and seen < limit:
        d = stack.pop()
        seen += 1
        if isinstance(d, list):
            stack.extend(d)
            continue
        if not isinstance(d, dict):
            continue
        price = next((p for p in (_to_price(d.get(k)) for k in PRICE_KEYS if k in d) if p), None)
        name = next((str(d[k]) for k in NAME_KEYS
                     if isinstance(d.get(k), str) and len(d[k]) >= 4), None)
        link = next((str(d[k]) for k in URL_KEYS
                     if isinstance(d.get(k), str) and len(d[k]) >= 3), None)
        if price and name and link:
            if not link.startswith("http"):
                link = urljoin(base, link if link.startswith("/") else "/" + link)
            old = next((p for p in (_to_price(d.get(k)) for k in OLD_KEYS if k in d)
                        if p and p > price), None)
            if old is None and "price" in d:       # sale_price موجود، و"price" هو القديم
                op = _to_price(d.get("price"))
                if op and op > price * 1.001:
                    old = op
            pid = make_id(store, link)
            rating = None
            for rk in ("rating", "product_rating", "average_rating", "avgRating"):
                rv = d.get(rk)
                rating = _to_price(rv.get("value")) if isinstance(rv, dict) else None
                if rating:
                    break
            if pid not in out or (old and not out[pid]["old"]):
                out[pid] = {"id": pid, "title": name[:150], "price": price, "old": old,
                            "rating": None, "url": link.split("#")[0], "store": store}
        stack.extend(d.values())


def json_state_items(soup, base, store):
    out = {}
    decoder = json.JSONDecoder()
    for sc in soup.find_all("script"):
        raw = sc.string or sc.get_text() or ""
        if len(raw) < 40:
            continue
        typ = (sc.get("type") or "").lower()
        if "ld+json" in typ:
            continue
        data = None
        if "json" in typ or sc.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__"):
            try:
                data = json.loads(raw)
            except Exception:
                data = None
        if data is not None:
            walk_products(data, base, store, out)
            continue
        if not re.search(r'"(price|sale_price|salePrice|special_price)"', raw):
            continue
        for m in list(re.finditer(r"=\s*([\[{])", raw))[:6]:   # window.__STATE__ = {...}
            try:
                obj, _ = decoder.raw_decode(raw, m.start(1))
            except Exception:
                continue
            walk_products(obj, base, store, out)
    return out


def shopify_items(origin, store):
    out = {}
    try:
        text = fetch_text(origin + "/products.json?limit=250")
        data = json.loads(text)
        for p in data.get("products", []):
            v = (p.get("variants") or [{}])[0]
            price = num(v.get("price"))
            if not price:
                continue
            old = num(v.get("compare_at_price"))
            url = f"{origin}/products/{p['handle']}"
            pid = make_id(store, url)
            out[pid] = {"id": pid, "title": p.get("title", "")[:150], "price": price,
                        "old": old if old and old > price else None, "rating": None,
                        "url": url, "store": store}
    except Exception as e:
        print("shopify:", str(e)[:60])
    return out


def parse_page(html, url, store):
    soup = BeautifulSoup(html, "html.parser")
    items = json_state_items(soup, url, store)          # الأدق: بيانات المتجر الداخلية
    for pid, it in jsonld_items(soup, url, store).items():
        if pid not in items:
            items[pid] = it
    for pid, it in tile_items(soup, url, store).items():   # قراءة الشاشة (احتياط)
        if pid not in items:
            items[pid] = it
        elif it["old"] and not items[pid]["old"] and abs(it["price"] - items[pid]["price"]) < 1:
            items[pid]["old"] = it["old"]
    return items, soup


def discover_links(soup, base, limit):
    host = urlparse(base).netloc
    hints = re.compile(r"(category|categories|collection|/c/|offer|sale|deal|عروض|"
                       r"perfume|عطور|oud|عود|product|shop|brand|men|women)", re.I)
    found = []
    for a in soup.find_all("a", href=True):
        u = urljoin(base, a["href"]).split("#")[0].split("?")[0]
        if urlparse(u).netloc != host or SKIP_HREF.search(u):
            continue
        if hints.search(u) and u not in found and u.rstrip("/") != base.rstrip("/"):
            found.append(u)
        if len(found) >= limit:
            break
    return found


def get_items(url, store, strategies=("requests", "cffi", "browser")):
    """نجرب الطرق بالترتيب، وأول طريقة تطلع منتجات فعلاً نعتمدها."""
    errs = []
    for st in strategies:
        if time_left() <= 0:
            break
        try:
            html = STRATS[st](url)
        except Exception as e:
            errs.append(f"{st}: {str(e)[:35]}")
            continue
        items, soup = parse_page(html, url, store)
        if items:
            won.setdefault(store, Counter())[st] += 1
            return items, soup
        errs.append(f"{st}: 0 منتج")
    raise Blocked(" | ".join(errs) or "no strategy")


def fetch_text(url):
    for st in ("cffi", "requests"):
        try:
            return STRATS[st](url)
        except Exception:
            continue
    return ""


def sitemap_items(origin, store, strategies):
    """آخر حل لو الصفحات مقفولة: نقرأ منتجات من خريطة الموقع (تدوير يومي)."""
    robots = fetch_text(origin + "/robots.txt")
    maps = [ln.split(":", 1)[1].strip() for ln in robots.splitlines()
            if ln.lower().startswith("sitemap:")] or [origin + "/sitemap.xml"]
    locs = []
    for sm in maps[:3]:
        xml = fetch_text(sm)
        found = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)
        kids = [x for x in found if x.endswith(".xml") or "sitemap" in x.lower()]
        if kids:
            kids.sort(key=lambda x: "product" not in x.lower())
            for kid in kids[:3]:
                locs += re.findall(r"<loc>\s*(.*?)\s*</loc>", fetch_text(kid))
        else:
            locs += found
    host = urlparse(origin).netloc
    urls = [u for u in dict.fromkeys(locs)
            if urlparse(u).netloc == host and not u.endswith(".xml")]
    if not urls:
        return {}
    k = date.today().toordinal() * SITEMAP_PAGES % len(urls)
    window = (urls + urls)[k:k + SITEMAP_PAGES]
    items = {}
    for u in window:
        if time_left() <= 0:
            break
        try:
            page_items, _ = get_items(u, store, strategies)
            items.update(page_items)
        except Exception:
            pass
        polite_sleep()
    return items


# ============================ المتاجر ============================
def _parse_amazon(soup):
    items = []
    for card in soup.select('div[data-component-type="s-search-result"]'):
        asin = card.get("data-asin")
        title = card.select_one("h2")
        price = card.select_one(".a-price:not(.a-text-price) .a-offscreen")
        old = card.select_one(".a-price.a-text-price .a-offscreen")
        rating = card.select_one("span.a-icon-alt")
        if not (asin and title and price):
            continue
        items.append({
            "id": "amz:" + asin, "title": title.get_text(strip=True)[:150],
            "price": num(price.get_text()),
            "old": num(old.get_text()) if old else None,
            "rating": num(rating.get_text()) if rating else None,
            "url": f"https://www.amazon.sa/dp/{asin}", "store": "أمازون السعودية"})
    return items


def fetch_amazon(keyword):
    url = "https://www.amazon.sa/s?k=" + quote_plus(keyword)
    errs = []
    for st in AMAZON_STRATEGIES:
        if time_left() <= 0:
            break
        try:
            html = STRATS[st](url)
        except Exception as e:
            errs.append(f"{st}: {str(e)[:35]}")
            continue
        items = _parse_amazon(BeautifulSoup(html, "html.parser"))
        if items:
            won.setdefault("أمازون السعودية", Counter())[st] += 1
            return items
        errs.append(f"{st}: 0 بطاقة")
    raise Blocked(" | ".join(errs) or "no strategy")


def crawl_store(cfg):
    strategies = cfg.get("strategies", ("requests", "cffi", "browser"))
    items, visited, queue = {}, set(), list(cfg["start"])
    discovered, errors = False, []
    while queue and len(visited) < MAX_CRAWL_PAGES:
        url = queue.pop(0)
        if url in visited or time_left() <= 0:
            continue
        visited.add(url)
        try:
            page_items, soup = get_items(url, cfg["name"], strategies)
        except Exception as e:
            errors.append(str(e)[:80])
            continue
        items.update(page_items)
        if soup is not None and not discovered:
            origin = "{u.scheme}://{u.netloc}".format(u=urlparse(url))
            if "shopify" in str(soup)[:200000].lower() and not any(
                    "/products/" in i["id"] for i in items.values()):
                items.update(shopify_items(origin, cfg["name"]))
            queue.extend(discover_links(soup, url, MAX_CRAWL_PAGES))
            discovered = True
        polite_sleep()
    if not items:   # كل الصفحات انقفلت: نجرب خريطة الموقع
        origin = "{u.scheme}://{u.netloc}".format(u=urlparse(cfg["start"][0]))
        try:
            items = sitemap_items(origin, cfg["name"], strategies)
            if items:
                won.setdefault(cfg["name"], Counter())["sitemap"] += 1
        except Exception as e:
            errors.append(f"sitemap: {str(e)[:40]}")
    if not items:
        raise Blocked(errors[0] if errors else "no items")
    return list(items.values())


def search_store(cfg, keywords):
    strategies = cfg.get("strategies", ("browser", "cffi"))
    out, err = [], None
    for kw in keywords:
        q = kw.replace(" ", "-") if "aliexpress" in cfg["url"] else quote_plus(kw)
        try:
            items, _ = get_items(cfg["url"].format(q=q), cfg["name"], strategies)
            for it in items.values():
                it["keyword"] = kw
                out.append(it)
        except Exception as e:
            err = e
        polite_sleep()
    if not out and err:
        raise err
    return out


# ============================ منطق العروض ============================
candidates = []   # كل العروض اللي نجحت، نرتبها ونرسل الأقوى فقط
seen_items = 0
suspect = {"n": 0}


def clean_ok(item, cat):
    if not item["price"] or item["price"] < cat["min_price"]:
        return False
    if re.search(cat["exclude"], item["title"], re.I):
        return False
    if item.get("rating") and item["rating"] < MIN_RATING:
        return False
    return True


def evaluate(item, vmin, umin):
    """يرجع (خصم, سعر_مرجعي, موثّق) أو None."""
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    past = [r[0] for r in db.execute(
        "SELECT price FROM prices WHERE id=? AND day>=? AND day<? ORDER BY day",
        (item["id"], since, today))]
    if len(past) >= MIN_HISTORY_DAYS:
        # نتجاهل آخر 7 أيام إذا عندنا تاريخ كافي، عشان رفع السعر قبل العرض ما ينحسب
        base = past[:-7] if len(past) >= 14 else past
        ref = min(statistics.median(base), statistics.median(past))
        discount = 1 - item["price"] / ref
        if vmin <= discount <= MAX_DISCOUNT and item["price"] <= min(past) * NEAR_LOW:
            return discount, ref, True
        return None
    if SHOW_UNVERIFIED and item["old"] and item["old"] > item["price"]:
        discount = 1 - item["price"] / item["old"]
        if umin <= discount <= MAX_DISCOUNT:
            return discount, item["old"], False
    return None


def handle(items, cat, unverified_cap):
    global seen_items
    today = date.today().isoformat()
    for it in items:
        if not it["price"]:
            continue
        # أسعار مستحيلة = خطأ قراءة: توفير مقروء كسعر، أو سعر أقل بكثير من القديم
        if it.get("old") and it["price"] < it["old"] * 0.12:
            suspect["n"] += 1
            continue
        past_all = [r[0] for r in db.execute(
            "SELECT price FROM prices WHERE id=? AND day>=?",
            (it["id"], (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()))]
        if len(past_all) >= 2 and it["price"] < statistics.median(past_all) * 0.25:
            suspect["n"] += 1          # نزول 75%+ فجأة عن تاريخ المنتج = غالباً خطأ قراءة
            continue
        seen_items += 1
        # نسجل السعر دايماً (حتى لو المنتج مستبعد) لبناء التاريخ
        result = evaluate(it, cat["verified_min"],
                          max(cat["unverified_min"], unverified_cap)) \
            if clean_ok(it, cat) else None
        db.execute("INSERT OR REPLACE INTO prices VALUES(?,?,?)",
                   (it["id"], today, it["price"]))
        if not result:
            continue
        discount, ref, verified = result
        if db.execute("SELECT 1 FROM alerted WHERE id=? AND price=?",
                      (it["id"], it["price"])).fetchone():
            continue
        saving = ref - it["price"]
        candidates.append({
            "item": it, "cat": cat["name"], "discount": discount, "ref": ref,
            "verified": verified, "score": discount + min(saving / 2000, 0.2)})
    db.commit()


def time_left():
    return TIME_BUDGET - (time.time() - START)


def send_candidates():
    sent = {"v": 0, "u": 0}
    best = {}
    for c in candidates:   # نفس المنتج قد يظهر في أكثر من بحث: نخلي أفضل نسخة
        k = c["item"]["id"]
        if k not in best or (c["verified"], c["score"]) > (best[k]["verified"], best[k]["score"]):
            best[k] = c
    for c in sorted(best.values(), key=lambda c: -c["score"]):
        key = "v" if c["verified"] else "u"
        if sent[key] >= (MAX_VERIFIED if c["verified"] else MAX_UNVERIFIED):
            continue
        it = c["item"]
        badge = ("✅ موثّق (من تاريخ الأسعار)" if c["verified"]
                 else "⚠️ غير موثّق (مقارنة بسعر المتجر فقط)")
        ok = send_telegram(
            f"🔥 {it['title']}\n"
            f"🏷️ {c['cat']}  |  🏬 {it['store']}\n"
            f"💰 {it['price']:.0f} ريال (قبل: {c['ref']:.0f})\n"
            f"📉 خصم {c['discount']:.0%}  {badge}\n"
            + (f"⭐ التقييم: {it['rating']}\n" if it.get("rating") else "")
            + f"🛒 {it['url']}")
        if ok:
            sent[key] += 1
            db.execute("INSERT OR IGNORE INTO alerted VALUES(?,?)",
                       (it["id"], it["price"]))
    db.commit()
    return sent


# ============================ التشغيل ============================
def main():
    today = date.today().isoformat()
    db.execute("DELETE FROM prices WHERE day < ?",
               ((date.today() - timedelta(days=HISTORY_DAYS + 30)).isoformat(),))
    if not meta_get("hello"):
        if send_telegram("✅ المراقب الذكي اشتغل وتيليجرام متصل.\n"
                         "يشتغل مرة باليوم ويرسل لك فقط أقوى العروض.\n"
                         "✅ = موثّق من تاريخ الأسعار\n"
                         "⚠️ = غير موثّق (مقارنة بسعر المتجر فقط)\n"
                         "أول 7 أيام يجمع الأسعار، فالعروض تكون ⚠️ وبحدود عالية."):
            meta_set("hello", today)

    health = {}
    cat_order = list(CATEGORIES.items())
    k = date.today().toordinal() % len(cat_order)
    cat_order = cat_order[k:] + cat_order[:k]      # تدوير يومي: ما تتضرر نفس الفئات

    # أمازون
    n, err = 0, None
    for key, cat in cat_order:
        for kw in cat["keywords"]:
            if time_left() < 0:
                break
            try:
                items = fetch_amazon(kw)
                handle(items, cat, AMAZON_UNVERIFIED_CAP)
                n += len(items)
            except Exception as e:
                err = str(e)[:90]
            polite_sleep()
    health["أمازون السعودية"] = (n, err)

    # متاجر العطور والعود
    for cfg in CRAWL_STORES:
        if time_left() < 0:
            health[cfg["name"]] = (0, "انتهى وقت التشغيل")
            continue
        try:
            items = crawl_store(cfg)
            handle(items, CATEGORIES["perfumes"], cfg["unverified_cap"])
            health[cfg["name"]] = (len(items), None)
        except Exception as e:
            health[cfg["name"]] = (0, str(e)[:90])

    # المتاجر الثقيلة (لكل متجر ميزانية وقت)
    for cfg in SEARCH_STORES:
        n, err, t0 = 0, None, time.time()
        for key, cat in cat_order:
            if key in cfg["skip"]:
                continue
            for kw in cat["keywords"]:
                if time_left() < 0 or time.time() - t0 > HEAVY_STORE_BUDGET:
                    break
                try:
                    items = search_store(cfg, [kw])
                    handle(items, cat, cfg["unverified_cap"])
                    n += len(items)
                except Exception as e:
                    err = str(e)[:90]
        health[cfg["name"]] = (n, err)

    sent = send_candidates()

    # صحة المتاجر: في السجل وملخص GitHub فقط (ما نرسل أخطاء لتيليجرام)
    lines = []
    for name, (n, err) in health.items():
        how = ", ".join(f"{k}×{v}" for k, v in won.get(name, {}).items())
        lines.append(f"OK  {name}: {n} منتج" + (f" [{how}]" if how else "") if n
                     else f"--  {name}: 0 ({err or 'بدون سبب'})")
    report = "\n".join(lines)
    print(report)
    print("أخطاء الشبكة:", dict(net), "| أسعار مشبوهة تم تجاهلها:", suspect["n"])
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        try:
            with open(summ, "a", encoding="utf8") as f:
                f.write("### صحة المتاجر\n```\n" + report + "\n```\n")
        except Exception:
            pass

    stores_ok = sum(1 for n, _ in health.values() if n)
    total_read = sum(n for n, _ in health.values())
    last = meta_get("last_status")
    due = True
    if last:
        try:
            due = (date.today() - date.fromisoformat(last)).days >= 7
        except ValueError:
            due = True
    if WEEKLY_STATUS and stores_ok and due:
        send_telegram(f"🟢 المراقب الذكي شغال\nقرأ {total_read} منتج من {stores_ok} متجر اليوم.\n"
                      "ما يوصلك إلا العروض القوية الحقيقية.")
        meta_set("last_status", today)

    if _browser is not None:
        try:
            _browser.close()
            _pw.stop()
        except Exception:
            pass
    print(f"انتهى. فحص {seen_items} منتج، أُرسل ✅{sent['v']} ⚠️{sent['u']}، "
          f"المدة {int(time.time() - START)} ثانية")


if __name__ == "__main__":
    main()
