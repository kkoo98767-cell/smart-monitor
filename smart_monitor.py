"""المراقب الذكي - النسخة النهائية 4
يشتغل مرة باليوم، ويرسل لك فقط أقوى العروض الحقيقية.

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
import re
import sqlite3
import statistics
import time
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
CRAWL_STORES = [   # متاجر نقرأ أقسامها تلقائياً (فئتها عطور)
    {"name": "درعة", "unverified_cap": 0.65, "start": [
        "https://www.deraahstore.com/ar_sa/perfumes/all-perfumes",
        "https://www.deraahstore.com/ar_sa/oud/oriental-perfumes",
        "https://www.deraahstore.com/ar_sa/exclusive-offers",
        "https://www.deraahstore.com/ar_sa/11-11-offers"]},
    {"name": "عساف", "unverified_cap": 0.60, "start": ["https://3saf.com/ar"]},
    {"name": "ثنيان", "unverified_cap": 0.60, "start": ["https://thnyan.com/ar"]},
    {"name": "الماجد للعود", "unverified_cap": 0.60, "start": ["https://www.almajed4oud.com/"]},
    {"name": "جولدن سنت", "unverified_cap": 0.55, "start": ["https://www.goldenscent.com/ar"]},
]
SEARCH_STORES = [  # متاجر نبحث فيها بالكلمات (تفتح بمتصفح)
    {"name": "نون", "unverified_cap": 0.45, "skip": [],
     "url": "https://www.noon.com/saudi-ar/search/?q={q}"},
    {"name": "إكسترا", "unverified_cap": 0.45,
     "skip": ["shoes", "clothes", "hoodies", "perfumes"],
     "url": "https://www.extra.com/ar-sa/search/?text={q}"},
    {"name": "علي إكسبرس", "unverified_cap": 0.75, "skip": ["perfumes"],
     "url": "https://www.aliexpress.com/w/wholesale-{q}.html"},
]
AMAZON_UNVERIFIED_CAP = 0.45

# ---- د) حساسية التحقق ----
MIN_HISTORY_DAYS = 7     # كم يوم أسعار نحتاج قبل ما نسمي العرض ✅
NEAR_LOW = 1.03          # لازم السعر <= أقل سعر سابق × هذا الرقم
MIN_RATING = 4.0         # (أمازون) تجاهل التقييم الأقل
MAX_DISCOUNT = 0.90      # خصم أعلى من كذا غالباً خطأ في الصفحة

# ---- هـ) حدود تقنية ----
HISTORY_DAYS = 90
MAX_CRAWL_PAGES = 8
TIME_BUDGET = 40 * 60    # ثواني؛ بعدها يوقف البحث ويرسل اللي جمعه

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


# ============================ جلب الصفحات ============================
_pw = None
_browser = None


def render(url):
    """يفتح الصفحة بمتصفح حقيقي (للمواقع اللي تعتمد على JavaScript)."""
    global _pw, _browser
    try:
        from playwright.sync_api import sync_playwright
        if _browser is None:
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch()
        ctx = _browser.new_context(user_agent=HEADERS["User-Agent"],
                                   locale="ar-SA")
        page = ctx.new_page()
        page.route("**/*", lambda r: r.abort()
                   if r.request.resource_type in ("image", "font", "media")
                   else r.continue_())
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1500)
        html = page.content()
        ctx.close()
        return html
    except Exception as e:
        print("فشل المتصفح:", url, str(e)[:120])
        return None


def plain_get(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    if r.status_code in (403, 429, 503):
        raise Blocked(f"HTTP {r.status_code}")
    r.raise_for_status()
    low = r.text.lower()
    if "captcha" in low[:20000] and len(r.text) < 60000:
        raise Blocked("captcha")
    return r.text


# ============================ استخراج المنتجات (عام) ============================
BAD = re.compile(r"(دفع|تابي|تمارا|tabby|tamara|قسط|أقساط|installment|شهري|/\s*شهر|×)", re.I)
CUR = re.compile(r"(ر\.?\s?س|ريال|SAR|SR|﷼)", re.I)
OLD_CLS = re.compile(r"(old|before|was|compare|regular|strike|original|reduced|list-price|line-through)", re.I)
PRICE_CLS = re.compile(r"(price|sale|special|amount|cost|سعر|value)", re.I)
SKIP_HREF = re.compile(r"(cart|login|account|wishlist|checkout|javascript:|mailto:|tel:|#|contact|policy|terms|faq)", re.I)


def card_prices(card):
    vals = set()
    for el in card.find_all(True):
        if el.name in ("script", "style", "svg"):
            continue
        txt = el.get_text(" ", strip=True)
        if not txt or len(txt) > 40 or BAD.search(txt):
            continue
        cls = " ".join(el.get("class", [])) + " " + (el.get("id") or "")
        is_old = el.name in ("del", "s", "strike") or bool(OLD_CLS.search(cls))
        if not (CUR.search(txt) or PRICE_CLS.search(cls) or is_old):
            continue
        n = num(txt)
        if n and n >= 5:
            vals.add((n, is_old))
    if not vals:
        return None, None
    cur = [v for v, o in vals if not o]
    olds = [v for v, o in vals if o]
    if not cur:
        return None, None
    price = min(cur)
    if olds:
        old = max(olds)
    else:
        old = max(cur) if max(cur) > price * 1.02 else None
    if old is not None and old <= price:
        old = None
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
        card, hrefs = a, set()
        found = None
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
        pid = f"{store}:{urlparse(url).path.rstrip('/')}"
        if pid not in out or (old and not out[pid]["old"]):
            out[pid] = {"id": pid, "title": title[:150], "price": price,
                        "old": old, "rating": None, "url": url, "store": store}
    return out


def jsonld_items(soup, base, store):
    out = {}
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.string or s.get_text() or "")
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
                    pid = f"{store}:{urlparse(url).path.rstrip('/')}"
                    out[pid] = {"id": pid, "title": str(d["name"])[:150],
                                "price": price, "old": None, "rating": None,
                                "url": url, "store": store}
            for k in ("itemListElement", "@graph", "item", "mainEntity"):
                if k in d:
                    stack.append(d[k])
    return out


def shopify_items(origin, store):
    out = {}
    try:
        r = requests.get(origin + "/products.json?limit=250",
                         headers=HEADERS, timeout=25)
        if r.status_code != 200:
            return out
        for p in r.json().get("products", []):
            v = (p.get("variants") or [{}])[0]
            price = num(v.get("price"))
            if not price:
                continue
            old = num(v.get("compare_at_price"))
            url = f"{origin}/products/{p['handle']}"
            pid = f"{store}:/products/{p['handle']}"
            out[pid] = {"id": pid, "title": p.get("title", "")[:150],
                        "price": price, "old": old if old and old > price else None,
                        "rating": None, "url": url, "store": store}
    except Exception as e:
        print("shopify:", e)
    return out


def parse_page(html, url, store):
    soup = BeautifulSoup(html, "html.parser")
    items = jsonld_items(soup, url, store)
    for pid, it in tile_items(soup, url, store).items():
        if pid not in items or (it["old"] and not items[pid]["old"]):
            items[pid] = it
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


def get_items(url, store, force_render=False):
    """يجرب الطلب العادي، وإذا ما طلع منتجات يجرب المتصفح."""
    html, err = None, None
    if not force_render:
        try:
            html = plain_get(url)
        except Exception as e:
            err = e
    items, soup = ({}, None)
    if html:
        items, soup = parse_page(html, url, store)
    if not items:
        rendered = render(url)
        if rendered:
            items, soup = parse_page(rendered, url, store)
    if not items and err:
        raise err
    return items, soup


# ============================ المتاجر ============================
def fetch_amazon(keyword):
    url = "https://www.amazon.sa/s?k=" + quote_plus(keyword)
    html = plain_get(url)
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('div[data-component-type="s-search-result"]')
    if not cards:
        raise Blocked("no results parsed (page layout changed or blocked)")
    items = []
    for card in cards:
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


def crawl_store(cfg):
    items, visited, queue = {}, set(), list(cfg["start"])
    discovered = False
    errors = []
    while queue and len(visited) < MAX_CRAWL_PAGES:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            page_items, soup = get_items(url, cfg["name"])
        except Exception as e:
            errors.append(str(e)[:80])
            continue
        items.update(page_items)
        if soup is not None and not discovered:
            origin = "{u.scheme}://{u.netloc}".format(u=urlparse(url))
            if "shopify" in str(soup)[:200000].lower() and not any(
                    i["id"].startswith(cfg["name"] + ":/products/") for i in items.values()):
                items.update(shopify_items(origin, cfg["name"]))
            queue.extend(discover_links(soup, url, MAX_CRAWL_PAGES))
            discovered = True
        time.sleep(2)
    if not items and errors:
        raise Blocked(errors[0])
    return list(items.values())


def search_store(cfg, keywords):
    out = []
    err = None
    for kw in keywords:
        q = kw.replace(" ", "-") if "aliexpress" in cfg["url"] else quote_plus(kw)
        try:
            items, _ = get_items(cfg["url"].format(q=q), cfg["name"], force_render=True)
            for it in items.values():
                it["keyword"] = kw
                out.append(it)
        except Exception as e:
            err = e
        time.sleep(2)
    if not out and err:
        raise err
    return out


# ============================ منطق العروض ============================
candidates = []   # كل العروض اللي نجحت، نرتبها ونرسل الأقوى فقط
seen_items = 0


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

    # أمازون
    n, err = 0, None
    for key, cat in CATEGORIES.items():
        for kw in cat["keywords"]:
            if time_left() < 0:
                break
            try:
                items = fetch_amazon(kw)
                handle(items, cat, AMAZON_UNVERIFIED_CAP)
                n += len(items)
            except Exception as e:
                err = str(e)[:80]
            time.sleep(3)
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
            health[cfg["name"]] = (0, str(e)[:80])

    # المتاجر الثقيلة
    for cfg in SEARCH_STORES:
        n, err = 0, None
        for key, cat in CATEGORIES.items():
            if key in cfg["skip"]:
                continue
            for kw in cat["keywords"]:
                if time_left() < 0:
                    break
                try:
                    items = search_store(cfg, [kw])
                    handle(items, cat, cfg["unverified_cap"])
                    n += len(items)
                except Exception as e:
                    err = str(e)[:80]
        health[cfg["name"]] = (n, err)

    sent = send_candidates()

    lines = [(f"✅ {name}: {n} منتج" if n else f"❌ {name}: ما قرأ شي ({err or 'بدون سبب'})")
             for name, (n, err) in health.items()]
    report = "\n".join(lines)
    print(report)
    if not meta_get("diag"):
        if send_telegram("📋 تقرير أول تشغيل للمتاجر:\n" + report +
                         "\n\nأرسل لي هذا التقرير في المحادثة لو في متجر ❌."):
            meta_set("diag", today)
    elif any(n == 0 for n, _ in health.values()) and meta_get("warned") != today:
        send_telegram("⚠️ متاجر ما قرأها المراقب اليوم:\n" +
                      "\n".join(l for l in lines if l.startswith("❌")))
        meta_set("warned", today)

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
