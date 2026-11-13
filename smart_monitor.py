"""المراقب الذكي - النسخة 2 (أمازون السعودية + تيليجرام)

الجديد في هذه النسخة:
- رسالة ترحيب أول مرة تتأكد فيها أن تيليجرام شغال
- كشف حظر أمازون (كابتشا / 503) وتنبيهك مرة وحدة باليوم بدل ما يسكت
- ما يوقف إذا فشل جلب كلمة بحث واحدة
- حماية من الأسعار الغلط وتنبيهات مكررة

المتغيرات المطلوبة: TG_TOKEN و TG_CHAT
التشغيل: python smart_monitor.py
"""
import os
import re
import sqlite3
import statistics
import time
from datetime import date, timedelta
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT = os.environ["TG_CHAT"]

# كلمة البحث -> أقل نسبة خصم مقبولة (0.25 = 25%)
WATCH = {
    "ماوس جيمنج": 0.25,
    "كيبورد ميكانيكي": 0.25,
    "يد تحكم PS5": 0.25,
    "شاشة جيمنج": 0.25,
    "تلفزيون": 0.25,
    "مكيف": 0.25,
    "مكنسة": 0.25,
    "عطر رجالي": 0.30,
    "ثوب رجالي": 0.50,
    "حذاء رجالي": 0.50,
}

HISTORY_DAYS = 90
MIN_HISTORY_DAYS = 7          # أقل عدد أيام أسعار نثق فيها
NEW_ITEM_MIN_DISCOUNT = 0.40  # إذا ما عندنا تاريخ كافي
MIN_RATING = 4.0
MAX_DISCOUNT = 0.90           # أي خصم أعلى من كذا غالباً غلط في الصفحة
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
}

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


def send_telegram(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": text},
            timeout=20,
        )
        if r.status_code != 200:
            print("خطأ تيليجرام:", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        print("فشل الإرسال:", e)
        return False


def num(text):
    if not text:
        return None
    t = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,"))
    m = re.search(r"[\d,]+(?:\.\d+)?", t)
    return float(m.group().replace(",", "")) if m else None


class Blocked(Exception):
    pass


def fetch_amazon(keyword):
    url = "https://www.amazon.sa/s?k=" + quote_plus(keyword)
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code in (403, 429, 503):
        raise Blocked(f"HTTP {r.status_code}")
    r.raise_for_status()
    low = r.text.lower()
    if "captcha" in low or "robot check" in low:
        raise Blocked("captcha")
    soup = BeautifulSoup(r.text, "html.parser")
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
            "id": "amz:" + asin,
            "title": title.get_text(strip=True),
            "price": num(price.get_text()),
            "old": num(old.get_text()) if old else None,
            "rating": num(rating.get_text()) if rating else None,
            "url": f"https://www.amazon.sa/dp/{asin}",
            "store": "أمازون السعودية",
        })
    return items


# TODO: fetch_noon(keyword) - نون تعتمد على JavaScript وتحتاج Playwright
FETCHERS = [fetch_amazon]


def evaluate(item, min_discount):
    """يرجع (خصم, سعر_مرجعي, موثّق) أو None إذا ما يستاهل."""
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    rows = db.execute(
        "SELECT price FROM prices WHERE id=? AND day>=? AND day<?",
        (item["id"], since, today)).fetchall()
    past = [r[0] for r in rows]

    if len(past) >= MIN_HISTORY_DAYS:
        ref = statistics.median(past)
        discount = 1 - item["price"] / ref
        if (min_discount <= discount <= MAX_DISCOUNT
                and item["price"] <= min(past) * 1.03):
            return discount, ref, True
        return None

    if item["old"] and item["old"] > item["price"]:
        discount = 1 - item["price"] / item["old"]
        if max(min_discount, NEW_ITEM_MIN_DISCOUNT) <= discount <= MAX_DISCOUNT:
            return discount, item["old"], False
    return None


def main():
    today = date.today().isoformat()

    if not meta_get("hello"):
        if send_telegram("✅ المراقب الذكي اشتغل وتيليجرام متصل.\n"
                         "أول 7 أيام يجمع الأسعار، وتنبيهاته تكون ⚠️ غير موثّقة، "
                         "وبعدها تصير ✅ موثّقة."):
            meta_set("hello", today)

    fetched_ok = 0
    errors = []
    sent = 0

    for keyword, min_discount in WATCH.items():
        for fetch in FETCHERS:
            try:
                items = fetch(keyword)
                fetched_ok += 1
            except Exception as e:
                errors.append(f"{keyword}: {e}")
                print("فشل الجلب:", keyword, e)
                time.sleep(3)
                continue

            for it in items:
                if not it["price"] or it["price"] <= 0:
                    continue
                if it["rating"] and it["rating"] < MIN_RATING:
                    continue
                result = evaluate(it, min_discount)
                db.execute("INSERT OR REPLACE INTO prices VALUES(?,?,?)",
                           (it["id"], today, it["price"]))
                if not result:
                    continue
                discount, ref, verified = result
                if db.execute("SELECT 1 FROM alerted WHERE id=? AND price=?",
                              (it["id"], it["price"])).fetchone():
                    continue
                badge = ("✅ خصم موثّق من تاريخ الأسعار" if verified
                         else "⚠️ غير موثّق (مقارنة بسعر المتجر فقط)")
                ok = send_telegram(
                    f"🔥 {it['title'][:120]}\n"
                    f"🏬 {it['store']}\n"
                    f"💰 {it['price']:.0f} ريال (قبل: {ref:.0f})\n"
                    f"📉 خصم {discount:.0%}  {badge}\n"
                    f"⭐ التقييم: {it['rating'] or 'غير معروف'}\n"
                    f"⏳ مدة العرض: غير معروفة\n"
                    f"🛒 {it['url']}")
                if ok:
                    sent += 1
                    db.execute("INSERT OR IGNORE INTO alerted VALUES(?,?)",
                               (it["id"], it["price"]))
            db.commit()
            time.sleep(3)  # لا نضغط على الموقع

    # تنبيه عند فشل كل شي (مرة وحدة باليوم)
    if fetched_ok == 0 and errors and meta_get("warned") != today:
        send_telegram("⚠️ المراقب ما قدر يجلب أي نتائج اليوم.\n"
                      f"السبب: {errors[0]}\n"
                      "غالباً أمازون حظرت سيرفرات GitHub أو غيّرت الصفحة. "
                      "ارجع لي في المحادثة وأجهز لك البديل.")
        meta_set("warned", today)

    print(f"انتهى. نجح الجلب: {fetched_ok}، تنبيهات مرسلة: {sent}، أخطاء: {len(errors)}")


if __name__ == "__main__":
    main()
