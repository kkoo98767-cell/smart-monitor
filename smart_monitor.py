"""المراقب الذكي v6: أرشيف كامل، مرجع من سجلنا، بدائل قراءة، selftest دون تيليجرام.
التشغيل اليومي 08:07 السعودية. الإعدادات الأساسية أدناه.
الخصم المعلن ليس إثباتاً لانخفاض السعر. التوثيق يحتاج قراءة سابقة بفاصل 12 ساعة.
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
from monitor_history import Archive, valid_price, uniform_discount_ids
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")
START = time.time()

# ======================================================================
#                          1) الإعدادات (غيّر هنا)
# ======================================================================

# ---- أ) كم عرض يوصلك في اليوم كحد أقصى ----
MAX_DEALS = 15          # إجمالي العروض؛ اختيار عشوائي موزّع على المتاجر
REMOVED_STORES = {"نون", "الماجد للعود"}
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
    {"name": "جولدن سنت", "unverified_cap": 0.55, "strategies": ("cffi", "browser", "requests"),
     "start": ["https://www.goldenscent.com/ar", "https://www.goldenscent.com/ar/perfumes",
               "https://www.goldenscent.com/ar/offers"]},
]
SEARCH_STORES = [  # متاجر نبحث فيها بالكلمات
    {"name": "إكسترا", "unverified_cap": 0.45, "strategies": ("requests", "cffi", "browser"),
     "skip": ["shoes", "clothes", "hoodies", "perfumes"],
     "url": "https://www.extra.com/ar-sa/search/?text={q}"},
    {"name": "علي إكسبرس", "unverified_cap": 0.75, "skip": ["perfumes"],
     "strategies": ("requests", "cffi", "browser"),
     "url": "https://www.aliexpress.com/w/wholesale-{q}.html"},
]
AMAZON_STRATEGIES = ("requests", "cffi", "browser")
AMAZON_UNVERIFIED_CAP = 0.45

# ---- د) حساسية التحقق ----
MIN_HISTORY_DAYS = 0     # كم يوم أسعار نحتاج قبل ما نسمي العرض ✅
NEAR_LOW = 1.03          # لازم السعر <= أقل سعر سابق × هذا الرقم
MIN_RATING = 4.0         # (أمازون) تجاهل التقييم الأقل
MAX_DISCOUNT = 0.90      # خصم أعلى من كذا غالباً خطأ في الصفحة

# ---- هـ) حدود تقنية ----
HISTORY_DAYS = 90
MAX_CRAWL_PAGES = 8
TIME_BUDGET = 14 * 60    # ثواني؛ بعدها يوقف البحث ويرسل اللي جمعه
HEAVY_STORE_BUDGET = 8 * 60   # إعداد قديم؛ الميزانية الفعلية لكل متجر داخل collect
SITEMAP_PAGES = 8       # لو الموقع مقفول: كم منتج نقرأ من خريطة الموقع
MIN_DELAY = 0.7          # فاصل بين الطلبات (ثواني)
MAX_DELAY = 1.3
RETRY_BASE = 2          # أول انتظار عند 503/429 (يتضاعف)
RETRY_MAX_WAIT = 10
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
# ============================ أدوات عامة ============================
def send_telegram(text):
    if SELFTEST:
        return False
    if not TG_TOKEN or not TG_CHAT:
        print('Telegram: missing repository secrets (values never logged)')
        return False
    try:
        response=requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            data={'chat_id':TG_CHAT,'text':text,'disable_web_page_preview':True},timeout=20)
        success=response.status_code==200 and response.json().get('ok') is True
        print('Telegram: delivered' if success else f'Telegram: rejected HTTP {response.status_code}')
        return success
    except Exception as exc:
        # requests exceptions can contain the token in their URL: never print them.
        print(f'Telegram: delivery failed ({type(exc).__name__})')
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
    remaining=time_left()
    if remaining>0:
        time.sleep(min(remaining,random.uniform(MIN_DELAY,MAX_DELAY)))


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
            last = f"network: {type(e).__name__}"
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
        r = _req_session.get(u, timeout=max(1,min(20,time_left())), proxies=_proxies())
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
            sess.get(f"https://{host}/", timeout=max(1,min(15,time_left())), headers=_headers(),
                     proxies=_proxies())
            time.sleep(random.uniform(1, 2))
        except Exception:
            pass

    def do(u):
        r = sess.get(u, timeout=max(1,min(20,time_left())), headers=_headers(u), proxies=_proxies())
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
            resp = page.goto(url, timeout=max(1000,min(25000,int(time_left()*1000))), wait_until="domcontentloaded")
            status = resp.status if resp else 200
            for _ in range(8):          # ننتظر لو فيه صفحة "تحقق" تنحل لحالها
                if "just a moment" not in (page.title() or "").lower():
                    break
                page.wait_for_timeout(2000)
            try:
                page.wait_for_load_state("networkidle", timeout=max(1000,min(5000,int(time_left()*1000))))
            except Exception:
                pass
            for _ in range(3):          # bounded lazy-load scrolling
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(700)
            html = page.content()
            extra = "".join('<script type="application/json" data-xhr="1">'
                            + b.replace("</", "<\\/") + "</script>" for b in xhr[:15])
            if status in (401, 403, 429, 503):
                net[str(status)] += 1
                raise Blocked(f"HTTP {status}")
            if looks_blocked(html) and not xhr:
                net["challenge"] += 1
                raise Blocked("challenge/captcha")
            return html + extra
        except Blocked:
            raise
        except Exception as e:
            last = f"browser: {type(e).__name__}"
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


def make_id(store,url):
    parsed=urlparse(url)
    if store=='أمازون السعودية':
        match=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})',parsed.path,re.I)
        if match:
            return 'amz:'+match.group(1).upper()
    segs=[x for x in parsed.path.split('/') if x]
    while segs and LOCALE_SEG.match(segs[0]):
        segs.pop(0)
    return store+':/'+('/'.join(segs)).lower()


def card_prices(card):
    """Accept explicit current/old prices; never infer a sale from two arbitrary amounts."""
    current, old_values, savings = set(), set(), set()
    digit = re.compile(r'\d[\d,]*(?:\.\d+)?')
    for el in card.find_all(True):
        if el.name in ('script','style','svg'):
            continue
        txt = el.get('content') if el.get('itemprop') == 'price' else el.get_text(' ',strip=True)
        txt = str(txt or '').translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫٬','0123456789.,'))
        if not txt or len(txt)>80 or len(digit.findall(txt))!=1:
            continue
        cls = ' '.join(el.get('class',[]))+' '+str(el.get('id',''))
        contexts=[txt,cls]
        is_old=el.name in ('del','s','strike') or bool(OLD_CLS.search(cls))
        parent=el.parent
        for _ in range(3):
            if parent is None or parent is card:
                break
            pcls=' '.join(parent.get('class',[]))
            is_old |= parent.name in ('del','s','strike') or bool(OLD_CLS.search(pcls))
            ptxt=parent.get_text(' ',strip=True).translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫٬','0123456789.,'))
            if len(ptxt)<100 and len(digit.findall(ptxt))==1:
                contexts.extend([ptxt,pcls])
            parent=parent.parent
        context=' '.join(contexts)
        value=num(txt)
        if not valid_price(value):
            continue
        if BAD.search(context) or re.search(r'(قسّم|قسم|دفعات|payments|credit|جود|gold|loyalty)',context,re.I):
            continue
        if SAVE.search(context):
            savings.add(value)
            continue
        if not (CUR.search(txt) or PRICE_CLS.search(cls) or is_old or el.get('itemprop')=='price'):
            continue
        (old_values if is_old else current).add(value)
    old=max(old_values) if old_values else None
    if old and savings:
        matches={v for v in current for saving in savings
                 if abs(v-(old-saving))<=max(0.05,old*0.0001)}
        if len(matches)==1:
            return matches.pop(),old
    # Multiple unlabelled amounts are ambiguous (credit, bundles, instalments).
    current-=old_values
    current-=savings
    if len(current)!=1:
        return None,None
    price=current.pop()
    if old and price<old*0.12:
        return None,None
    return price,old if old and old>price else None


def tile_items(soup, base, store):
    out = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if SKIP_HREF.search(href):
            continue
        url = urljoin(base, href).split("#")[0]
        if urlparse(url).netloc != urlparse(base).netloc:
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
        heading = card.find(["h2", "h3", "h4"])
        title = (a.get("title") or (heading.get_text(" ", strip=True) if heading else "")
                 or a.get_text(" ", strip=True) or "").strip()
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
                currency = offers.get("priceCurrency")
                availability = str(offers.get("availability", ""))
                if currency and str(currency).upper() != "SAR":
                    continue
                if "OutOfStock" in availability or "SoldOut" in availability:
                    continue
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


def walk_products(data, base, store, out, limit=100000):
    stack,seen=[data],0
    while stack and seen<limit:
        d=stack.pop(); seen+=1
        if isinstance(d,list):
            stack.extend(d); continue
        if not isinstance(d,dict):
            continue
        stack.extend(d.values())
        if 'aliexpress.com' in base and d.get('productId') and isinstance(d.get('prices'),dict):
            prices=d['prices']; sale=prices.get('salePrice') or {}; original=prices.get('originalPrice') or {}
            title=d.get('title') or {}
            # This adapter is based on the actual captured SAR search payload.
            if sale.get('currencyCode')=='SAR' and isinstance(title,dict):
                link='https://www.aliexpress.com/item/'+str(d['productId'])+'.html'
                pid=make_id(store,link); price=_to_price(sale.get('minPrice'))
                old=_to_price(original.get('minPrice')) if original.get('currencyCode')=='SAR' else None
                if price and title.get('displayTitle'):
                    out[pid]=dict(id=pid,title=title['displayTitle'][:150],price=price,
                        old=old if old and old>price else None,rating=None,url=link,store=store)
        name=next((d[k] for k in NAME_KEYS if isinstance(d.get(k),str) and len(d[k])>=4),None)
        if not name:
            continue
        sku=d.get('sku') or d.get('sku_code') or d.get('code') or d.get('productId')
        link=next((d[k] for k in URL_KEYS if isinstance(d.get(k),str) and len(d[k])>=3),None)
        if 'noon.com' in base and sku:
            if not link:
                link=f'/saudi-ar/{sku}/p/'
            elif '/p/' not in link and str(sku) not in link:
                link=f'/saudi-ar/{link.strip("/")}/{sku}/p/'
        if not link:
            continue
        link=urljoin(base,link if link.startswith(('/','http')) else '/'+link)
        if urlparse(link).hostname!=urlparse(base).hostname:
            continue
        if 'aliexpress.com' in base and '/item/' not in link:
            continue
        currency=d.get('currency') or d.get('currencyIso')
        price_obj=d.get('price')
        if isinstance(price_obj,dict):
            currency=price_obj.get('currencyIso') or price_obj.get('currency') or currency
        if currency and str(currency).upper() not in ('SAR','SR','ر.س'):
            continue
        price=next((p for p in (_to_price(d.get(k)) for k in PRICE_KEYS) if p),None)
        if not price:
            offers=d.get('offers')
            if isinstance(offers,dict):
                price=_to_price(offers.get('price'))
        if not valid_price(price):
            continue
        old=next((p for p in (_to_price(d.get(k)) for k in OLD_KEYS) if p and p>price),None)
        if old is None and any(d.get(k) is not None for k in PRICE_KEYS if k!='price'):
            regular=_to_price(d.get('price'))
            if regular and regular>price:
                old=regular
        if old and price<old*0.12:
            continue
        legacy=make_id(store,link)
        pid=f'{store}:sku:{sku}' if sku and store=='نون' else legacy
        rating=d.get('rating') or d.get('product_rating')
        if isinstance(rating,dict):
            rating=rating.get('value') or rating.get('average')
        rating=_to_price(rating)
        item=dict(id=pid,legacy_id=legacy,title=name[:150],price=price,old=old,
                  rating=rating if rating and rating<=5 else None,url=link,store=store)
        if pid not in out or (old and not out[pid].get('old')):
            out[pid]=item


def json_state_items(soup,base,store):
    out={}; decoder=json.JSONDecoder()
    for sc in soup.find_all('script'):
        raw=sc.string or sc.get_text() or ''
        if len(raw)<30 or 'ld+json' in str(sc.get('type','')):
            continue
        if 'json' in str(sc.get('type','')) or sc.get('id') in ('__NEXT_DATA__','__NUXT_DATA__'):
            try:
                walk_products(json.loads(raw),base,store,out)
            except (ValueError,TypeError):
                pass
        # Decode JSON literals only; never eval JavaScript from a store page.
        patterns=[r'=\s*([\[{])',r'\bdata\s*:\s*({)',r'dispatchEvents\(\s*({)']
        for pattern in patterns:
            for match in list(re.finditer(pattern,raw))[:12]:
                try:
                    obj,_=decoder.raw_decode(raw,match.start(1))
                    walk_products(obj,base,store,out)
                except (ValueError,TypeError):
                    pass
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
        print("shopify:", safe_error(e))
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


def get_items(url, store, strategies=('requests','cffi','browser')):
    errors=[]
    for method in strategies:
        if time_left()<=1:
            break
        try:
            html=STRATS[method](url)
            if html.lstrip().startswith(('{','[')):
                items={}; walk_products(json.loads(html),url,store,items)
                soup=None
            else:
                items,soup=parse_page(html,url,store)
            items={pid:it for pid,it in items.items() if usable_item(it)}
            # Next.js route is derived from an actual build ID, never a guessed API.
            if not items and soup and method!='browser':
                sc=soup.find('script',id='__NEXT_DATA__')
                if sc:
                    state=json.loads(sc.string or sc.get_text())
                    build=state.get('buildId')
                    if build and re.fullmatch(r'[A-Za-z0-9_-]+',str(build)):
                        u=urlparse(url)
                        endpoint=f'{u.scheme}://{u.netloc}/_next/data/{build}{u.path.rstrip("/") or "/index"}.json'
                        if u.query:
                            endpoint+='?'+u.query
                        payload=STRATS[method](endpoint)
                        walk_products(json.loads(payload),url,store,items)
                        method+=':next-data'
            if items:
                won.setdefault(store,Counter())[method]+=1
                for it in items.values():
                    it['method']=method
                return items,soup
            errors.append(f'{method}: zero products; schema/page requires review')
        except Exception as exc:
            errors.append(f'{method}: {safe_error(exc)}')
    raise Blocked(' | '.join(errors) or 'time budget exhausted')


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



def time_left():
    return max(0,min(START+TIME_BUDGET,STORE_DEADLINE)-time.time())

def safe_error(exc):
    message=str(exc)
    for secret in (TG_TOKEN,PROXY_URL):
        if secret:
            message=message.replace(secret,'[redacted]')
    message=re.sub(r'https?://\S+', '[URL]', message)
    return message[:200]

def usable_item(item):
    if not valid_price(item.get('price')):
        return False
    if item.get('old') and item['price']<item['old']*0.12:
        return False
    title=re.sub(r'\s+',' ',item.get('title','')).strip().lower()
    if title in ('عرض المنتج','مشاهدة المنتج','view product','shop now','add to cart','أضف للسلة'):
        return False
    path=urlparse(item['url']).path.lower()
    if item['store']=='نون' and not path.startswith(('/saudi-ar/','/saudi-en/')):
        return False
    return True

# Unified thresholds. Legacy per-category thresholds above are retained for reference
# but no longer override these settings.
MIN_DEAL_DISCOUNT = 0.25
MIN_VERIFIED_DISCOUNT = 0.25
MIN_HISTORY_HOURS = 12
SELFTEST = False
STORE_DEADLINE = float('inf')
EXTRA_KEYWORDS = {
 'phones':['iPhone 16','Samsung Galaxy S25','Google Pixel','هاتف هونر'],
 'ps_controller':['DualSense Edge','يد بلايستيشن 5 سوني'],
 'tv':['تلفزيون LG OLED','Samsung QLED','TCL 65'],
 'gaming_monitor':['LG UltraGear','Samsung Odyssey','شاشة 240Hz'],
 'headphones':['Sony WH-1000XM5','AirPods Pro','JBL سماعات'],
 'pc':['Logitech G502','Samsung 990 PRO SSD','Corsair DDR5'],
 'home':['Dyson مكنسة','Philips Airfryer','غسالة ال جي'],
 'shoes':['Nike Air Max','Adidas Ultraboost','New Balance رجالي'],
 'clothes':['قميص بولو رجالي','بنطلون جينز رجالي'],
 'hoodies':['Nike hoodie men','Adidas هودي رجالي'],
 'perfumes':['عود طبيعي','دهن عود','Dior Sauvage','عطر عساف','عطر ثنيان']}
for _key,_words in EXTRA_KEYWORDS.items():
    CATEGORIES[_key]['keywords'] += [w for w in _words if w not in CATEGORIES[_key]['keywords']]

def clean_ok(item, cat):
    return (valid_price(item.get('price')) and item['price']>=cat['min_price']
            and not re.search(cat['exclude'],item['title'],re.I)
            and (not item.get('rating') or item['rating']>=MIN_RATING))


def message_for(candidate):
    it=candidate['item']; st=candidate['stats']
    badge=('✅ موثّق: انخفض عن السعر المرجعي الذي سجلناه.' if candidate['verified']
           else '⚠️ غير موثّق: خصم معلن من المتجر؛ لم نثبت انخفاضه من سجلنا.')
    history=(f"السجل السابق: {st['count']} قراءة خلال {st['days']:.1f} يوم؛ "
             f"الأقل {st['low']:.2f}، المتوسط {st['average']:.2f} ريال.\n" if st['count']
             else 'السجل السابق: لا توجد قراءة مؤهلة بفاصل 12 ساعة.\n')
    return (f"{badge}\n{it['title']}\n🏬 {it['store']} | {candidate['category']}\n"
            f"💰 {it['price']:.2f} ريال | المرجع {candidate['ref']:.2f}\n"
            f"📉 توفير {candidate['ref']-it['price']:.2f} ريال ({candidate['discount']:.1%})\n"
            +history+it['url'])


def send_candidates(candidates, archive):
    sent={'v':0,'u':0}; categories=Counter(); unique={}; buckets={}
    for candidate in candidates:
        it=candidate['item']
        if it['store'] in REMOVED_STORES or not archive.can_alert(it):
            continue
        previous=unique.get(it['id'])
        if previous is None or (candidate['verified'] and not previous['verified']):
            unique[it['id']]=candidate
    for candidate in unique.values():
        buckets.setdefault(candidate['item']['store'],[]).append(candidate)
    # One random offer per store per round. Catalog size and discount score do
    # not give Amazon or any other store priority over the remaining stores.
    while buckets and sum(sent.values())<MAX_DEALS:
        order=list(buckets)
        random.shuffle(order)
        for store in order:
            if sum(sent.values())>=MAX_DEALS:
                break
            pool=buckets[store]
            least=min(categories[c['category']] for c in pool)
            chosen=random.choice([c for c in pool if categories[c['category']]==least])
            pool.remove(chosen)
            if not pool:
                del buckets[store]
            if not archive.can_alert(chosen['item']):
                continue
            if send_telegram(message_for(chosen)):
                archive.mark_alert(chosen['item'])
                sent['v' if chosen['verified'] else 'u']+=1
                categories[chosen['category']]+=1
    return sent


def collect(archive, run_id, selftest=False):
    global STORE_DEADLINE
    health={}; collected={}
    amazon=dict(name='أمازون السعودية',url='https://www.amazon.sa/s?k={q}',skip=[],strategies=AMAZON_STRATEGIES)
    primary=[amazon]+[c for c in SEARCH_STORES if c['name']=='إكسترا']
    secondary=[c for c in SEARCH_STORES if c['name']=='علي إكسبرس']+CRAWL_STORES
    day=date.today().toordinal()
    def accept(items,key):
        for item in items:
            if archive.observe(item,key,run_id):
                item['category_key']=key
                collected[item['id']]=item
    for cfg in primary+secondary:
        store=cfg['name']
        # Primary stores cannot starve one another; secondary attempts stay bounded.
        budget=(90 if selftest else 210) if cfg in primary else (35 if selftest else 30)
        if store=='علي إكسبرس':
            budget=45 if selftest else 60
        STORE_DEADLINE=min(START+TIME_BUDGET,time.time()+budget)
        before=set(collected); errors=[]
        def read(url):
            if store=='أمازون السعودية':
                # Search HTML uses Amazon's stable ASIN selector; PDP uses JSON-LD.
                if '/s?' in url:
                    items=fetch_amazon_url(url)
                    return items
            items,_=get_items(url,store,cfg.get('strategies',('requests','cffi','browser')))
            return list(items.values())
        if 'start' in cfg:
            try:
                accept(crawl_store(cfg),'perfumes')
            except Exception as exc:
                errors.append(safe_error(exc))
        else:
            order=[k for k in CATEGORIES if k not in cfg.get('skip',[])]
            shift=day%len(order)
            order=order[shift:]+order[:shift]
            tasks=[]
            # Revisit least recently seen archived products; avoids a discovery-only archive.
            if not selftest:
                tasks.extend((key,url) for key,url in archive.db.execute('''
                    SELECT category,url FROM products WHERE store=? AND url<>''
                    AND category<>'' ORDER BY last_seen LIMIT 3''',(store,)) if key in CATEGORIES)
            rounds=1 if selftest else max(len(CATEGORIES[k]['keywords']) for k in order)
            for round_no in range(rounds):
                for key in order:
                    words=CATEGORIES[key]['keywords']
                    if round_no>=len(words):
                        continue
                    keyword=words[(day+round_no)%len(words)]
                    query=keyword.replace(' ','-') if store=='علي إكسبرس' else quote_plus(keyword)
                    tasks.append((key,cfg['url'].format(q=query)))
            failures=0
            for key,url in tasks:
                if time_left()<=2:
                    errors.append('store time budget reached'); break
                try:
                    accept(read(url),key)
                    failures=0
                except Exception as exc:
                    failures+=1
                    errors.append(safe_error(exc))
                    if failures>=2:
                        errors.append('circuit stopped after two failed URLs'); break
                polite_sleep()
            if not any(i['store']==store for i in collected.values()) and time_left()>5:
                # Retains sitemap fallback for search stores as well.
                try:
                    origin='{u.scheme}://{u.netloc}'.format(u=urlparse(cfg['url']))
                    recovered=sitemap_items(origin,store,cfg['strategies'])
                    # Unknown category is archived, but never promoted to a deal.
                    accept(list(recovered.values()),'')
                except Exception as exc:
                    errors.append('sitemap: '+safe_error(exc))
        new=[collected[k] for k in set(collected)-before]
        health[store]={'count':len(new),'methods':dict(won.get(store,{})),
                       'examples':[dict(title=i['title'],price=i['price'],old=i.get('old'),
                                        url=i['url'],method=i.get('method','unknown')) for i in new[:3]],
                       'errors':errors[-5:]}
        if not new:
            health[store]['note']='No usable products. 403/challenge may require PROXY_URL; not proof that every method is blocked on GitHub.'
        print(store,json.dumps(health[store],ensure_ascii=False))
    STORE_DEADLINE=START+TIME_BUDGET
    return list(collected.values()),health


def fetch_amazon_url(url):
    errors=[]
    for method in AMAZON_STRATEGIES:
        if time_left()<=1:
            break
        try:
            html=STRATS[method](url)
            items=_parse_amazon(BeautifulSoup(html,'html.parser'))
            if items:
                won.setdefault('أمازون السعودية',Counter())[method]+=1
                for it in items:
                    it['method']=method
                return items
            errors.append(method+': zero ASIN cards')
        except Exception as exc:
            errors.append(method+': '+safe_error(exc))
    raise Blocked(' | '.join(errors) or 'time budget reached')


def main():
    global SELFTEST,START,STORE_DEADLINE
    import argparse
    import shutil
    import tempfile
    import uuid
    parser=argparse.ArgumentParser()
    parser.add_argument('--selftest',action='store_true')
    parser.add_argument('--telegram-check',action='store_true')
    parser.add_argument('--db',default='monitor.db')
    args=parser.parse_args()
    SELFTEST=args.selftest
    START=time.time(); STORE_DEADLINE=START+TIME_BUDGET
    run_id=str(uuid.uuid4())
    temp=tempfile.TemporaryDirectory() if SELFTEST else None
    path=args.db
    if temp:
        path=os.path.join(temp.name,'selftest.db')
        if os.path.exists(args.db):
            # SQLite backup also works if source uses a WAL journal.
            source=sqlite3.connect(args.db); target=sqlite3.connect(path)
            source.backup(target); target.close(); source.close()
    archive=Archive(path)
    try:
        if args.telegram_check and not SELFTEST:
            # Explicit connection check uses only the existing permitted heartbeat text.
            if send_telegram('🟢 المراقب الذكي شغال'):
                archive.set_meta('last_status',date.today().isoformat())
            else:
                print('Telegram check failed; see status above. No diagnostics sent to Telegram.')
        items,health=collect(archive,run_id,SELFTEST)
        campaign_ids=uniform_discount_ids(items)
        candidates=[]
        for item in items:
            cat=CATEGORIES.get(item.get('category_key'))
            if not cat or not clean_ok(item,cat):
                continue
            result=archive.evaluate(item,run_id,item['id'] in campaign_ids,
                min_discount=MIN_DEAL_DISCOUNT,verified_discount=MIN_VERIFIED_DISCOUNT,
                min_hours=MIN_HISTORY_HOURS)
            if result and (result['verified'] or SHOW_UNVERIFIED):
                candidates.append(dict(item=item,category=cat['name'],**result))
        sent=send_candidates(candidates,archive) if not SELFTEST else {'v':0,'u':0}
        last=archive.get_meta('last_status')
        try:
            due=not last or (date.today()-date.fromisoformat(last)).days>=7
        except ValueError:
            due=True
        if not SELFTEST and WEEKLY_STATUS and items and due:
            if send_telegram('🟢 المراقب الذكي شغال'):
                archive.set_meta('last_status',date.today().isoformat())
        elapsed=round(time.time()-START,1)
        report=dict(mode='selftest' if SELFTEST else 'monitor',stores=health,
                    unique_accepted=len(items),eligible=len(candidates),sent=sent,
                    rejected=archive.db.execute('SELECT COUNT(*) FROM rejected_readings WHERE run_id=?',(run_id,)).fetchone()[0],
                    elapsed_seconds=elapsed,network=dict(net))
        with open('monitor_report.json','w',encoding='utf8') as f:
            json.dump(report,f,ensure_ascii=False,indent=2)
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary,'a',encoding='utf8') as f:
                f.write('### Smart Monitor '+report['mode']+'\n\n')
                for store,status in health.items():
                    f.write(f"- {store}: {status['count']} products; methods {status['methods']}\n")
                    for ex in status['examples']:
                        f.write(f"  - {ex['title']}: SAR {ex['price']}; old {ex['old']}; {ex['method']}\n")
                    for error in status['errors']:
                        f.write('  - '+error.replace('\n',' ')+'\n')
                f.write(f"\nElapsed {elapsed}s. Sent verified {sent['v']}, unverified {sent['u']}.\n")
        print(json.dumps(report,ensure_ascii=False))
        # Zero reads is a failed run, not a successful empty catalog.
        return 0 if items else 2
    finally:
        archive.close()
        if temp:
            temp.cleanup()
        if _browser is not None:
            _browser.close()
        if _pw is not None:
            _pw.stop()


if __name__=='__main__':
    raise SystemExit(main())
