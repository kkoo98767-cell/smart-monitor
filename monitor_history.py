"""Price archive and conservative deal evaluation. Existing tables are preserved."""
import math
import sqlite3
import statistics
import time
from collections import Counter
from datetime import datetime, timezone

MIN_DEAL_DISCOUNT = 0.25
MIN_VERIFIED_DISCOUNT = 0.25
MIN_HISTORY_HOURS = 12
ALERT_COOLDOWN_HOURS = 72
HISTORY_DAYS = 90


def valid_price(value):
    return (isinstance(value, (float, int)) and not isinstance(value, bool)
            and math.isfinite(value) and 1 <= value <= 300000)


class Archive:
    def __init__(self, path='monitor.db', clock=time.time):
        self.clock = clock
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA busy_timeout=10000')
        # DELETE journal keeps the committed .db self contained for Git.
        self.db.execute('PRAGMA journal_mode=DELETE')
        with self.db:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS prices(id TEXT, day TEXT, price REAL,
                    PRIMARY KEY(id,day));
                CREATE TABLE IF NOT EXISTS alerted(id TEXT, price REAL, PRIMARY KEY(id,price));
                CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT);
                CREATE TABLE IF NOT EXISTS products(
                    id TEXT PRIMARY KEY,store TEXT NOT NULL,title TEXT NOT NULL,
                    category TEXT NOT NULL,url TEXT NOT NULL,first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,readings INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS observations(
                    id TEXT NOT NULL,at REAL NOT NULL,price REAL NOT NULL,old REAL,
                    run_id TEXT NOT NULL,source TEXT NOT NULL,
                    PRIMARY KEY(id,run_id));
                CREATE INDEX IF NOT EXISTS observations_history ON observations(id,at);
                CREATE TABLE IF NOT EXISTS alert_events(
                    id TEXT NOT NULL,price REAL NOT NULL,at REAL NOT NULL,
                    PRIMARY KEY(id,price));
                CREATE TABLE IF NOT EXISTS rejected_readings(
                    id TEXT,at REAL,price REAL,reason TEXT,run_id TEXT,
                    PRIMARY KEY(id,run_id));
                CREATE TABLE IF NOT EXISTS identity_aliases(
                    alias TEXT PRIMARY KEY,id TEXT NOT NULL);
            ''')
        self.migrate()

    def migrate(self):
        """Legacy day has no time: use END of UTC day, never imply a false 12h gap."""
        if self.get_meta('archive_migration_v1'):
            return
        with self.db:
            for pid, day, price in self.db.execute('SELECT id,day,price FROM prices').fetchall():
                try:
                    at = datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()+86399
                except (TypeError, ValueError):
                    continue
                if not valid_price(price):
                    continue
                self.db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)',
                                (pid, at, price, None, 'legacy:'+day, 'legacy-day-unknown-time'))
                store = pid.split(':/', 1)[0] if ':/' in pid else 'أمازون السعودية' if pid.startswith('amz:') else ''
                self.db.execute('''INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?,?,0)''',
                                (pid, store, '', '', '', at, at))
            self.db.execute('''UPDATE products SET readings=(SELECT COUNT(*) FROM observations o
                WHERE o.id=products.id),first_seen=COALESCE((SELECT MIN(at) FROM observations o
                WHERE o.id=products.id),first_seen),last_seen=COALESCE((SELECT MAX(at)
                FROM observations o WHERE o.id=products.id),last_seen)''')
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('archive_migration_v1','1'))

    def get_meta(self, key):
        row = self.db.execute('SELECT v FROM meta WHERE k=?',(key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,str(value)))

    def resolve_identity(self, item):
        """Adopt an existing legacy URL ID when a reader discovers a stable SKU."""
        pid = item['id']
        alias = item.get('legacy_id',pid)
        row = self.db.execute('SELECT id FROM identity_aliases WHERE alias IN (?,?)',
                              (pid,alias)).fetchone()
        if row:
            item['id'] = row[0]
        elif self.db.execute('SELECT 1 FROM products WHERE id=?',(alias,)).fetchone():
            item['id'] = alias
        with self.db:
            for key in (pid,alias):
                self.db.execute('INSERT OR IGNORE INTO identity_aliases VALUES(?,?)',(key,item['id']))
        return item['id']

    def history(self, pid, at=None, min_hours=0, run_id=''):
        at = self.clock() if at is None else at
        return self.db.execute('''SELECT at,price,old FROM observations WHERE id=?
            AND at>=? AND at<=? AND run_id<>? ORDER BY at''',
            (pid,at-HISTORY_DAYS*86400,at-min_hours*3600,run_id)).fetchall()

    @staticmethod
    def baseline(rows):
        if not rows:
            return None
        # Equal weight per UTC day; repeated manual runs cannot dominate history.
        daily = {}
        for at,price,*_ in rows:
            daily.setdefault(int(at//86400),[]).append(price)
        values = [statistics.median(v) for _,v in sorted(daily.items())]
        ref = statistics.median(values)
        # Ignore the most recent week when a genuinely older window exists.
        cutoff = rows[-1][0]-7*86400
        older = [r for r in rows if r[0] <= cutoff]
        if len(daily) >= 14 and older:
            ref = min(ref, Archive.baseline(older))
        return ref

    def observe(self, item, category, run_id, at=None):
        at = self.clock() if at is None else at
        pid = self.resolve_identity(item)
        price, old = item.get('price'),item.get('old')
        previous = self.history(pid,at,run_id=run_id)
        reason = None
        if not valid_price(price):
            reason='invalid price'
        elif valid_price(old) and price < old*0.12:
            reason='below 12% of store reference'
        elif previous and price <= self.baseline(previous)*0.25:
            reason='sudden drop at least 75%'
        with self.db:
            if reason:
                self.db.execute('INSERT OR REPLACE INTO rejected_readings VALUES(?,?,?,?,?)',
                                (pid,at,price,reason,run_id))
                return False
            if not valid_price(old) or old <= price:
                old = None
                item['old'] = None
            added = self.db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)',
                (pid,at,round(price,2),old,run_id,item.get('method','unknown'))).rowcount
            if not added:
                return False
            self.db.execute('''INSERT INTO products VALUES(?,?,?,?,?,?,?,1)
                ON CONFLICT(id) DO UPDATE SET store=excluded.store,title=excluded.title,
                category=CASE WHEN products.category='' THEN excluded.category ELSE products.category END,
                url=excluded.url,last_seen=excluded.last_seen,readings=products.readings+1''',
                (pid,item['store'],item['title'],category,item['url'],at,at))
            day = datetime.fromtimestamp(at,timezone.utc).date().isoformat()
            self.db.execute('INSERT OR REPLACE INTO prices VALUES(?,?,?)',(pid,day,price))
        return True

    def fixed_discount(self, item, run_id, at):
        old = item.get('old')
        if not valid_price(old):
            return False
        discount = 1-item['price']/old
        previous = [r for r in self.history(item['id'],at,min_hours=MIN_HISTORY_HOURS,run_id=run_id)
                    if valid_price(r[2]) and r[2]>r[1]]
        # One independent earlier observation is enough to flag an unchanged sale.
        if previous:
            row=previous[-1]
            return (abs((1-row[1]/row[2])-discount)<=0.01
                    and item['price'] >= row[1]*0.99)
        return False

    def evaluate(self,item,run_id,store_uniform=False,at=None,
                 min_discount=MIN_DEAL_DISCOUNT,verified_discount=MIN_VERIFIED_DISCOUNT,
                 min_hours=MIN_HISTORY_HOURS):
        at=self.clock() if at is None else at
        rows=self.history(item['id'],at,min_hours=min_hours,run_id=run_id)
        ref=self.baseline(rows)
        stats={'count':len(rows),'days':round((at-rows[0][0])/86400,1) if rows else 0,
               'low':min(r[1] for r in rows) if rows else None,
               'average':statistics.mean(r[1] for r in rows) if rows else None}
        if ref and verified_discount-1e-9 <= 1-item['price']/ref < 0.75:
            # A historical drop is meaningful even if the shop always advertises a sale.
            return dict(ref=ref,discount=1-item['price']/ref,verified=True,stats=stats)
        old=item.get('old')
        if (valid_price(old) and old>item['price'] and not store_uniform
                and not self.fixed_discount(item,run_id,at)):
            discount=1-item['price']/old
            if min_discount-1e-9 <= discount <= 0.88:
                return dict(ref=old,discount=discount,verified=False,stats=stats)
        return None

    def can_alert(self,item,at=None):
        at=self.clock() if at is None else at
        pid,price=item['id'],round(item['price'],2)
        if self.db.execute('SELECT 1 FROM alerted WHERE id=? AND ABS(price-?)<0.005',
                           (pid,price)).fetchone():
            return False
        row=self.db.execute('SELECT price,at FROM alert_events WHERE id=? ORDER BY at DESC LIMIT 1',
                            (pid,)).fetchone()
        return not row or at-row[1] >= ALERT_COOLDOWN_HOURS*3600 or price < row[0]-0.005

    def mark_alert(self,item,at=None):
        at=self.clock() if at is None else at
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO alerted VALUES(?,?)',(item['id'],round(item['price'],2)))
            self.db.execute('INSERT OR REPLACE INTO alert_events VALUES(?,?,?)',
                            (item['id'],round(item['price'],2),at))

    def close(self):
        self.db.close()


def uniform_discount_ids(items, minimum=8, majority=0.70):
    """Only unverified deals are blocked; no inference from tiny samples."""
    stores={}
    for item in items:
        stores.setdefault(item['store'],{})[item['id']]=item
    flagged=set()
    for unique in stores.values():
        if len(unique)<minimum:
            continue
        counts=Counter(round((1-i['price']/i['old'])*100) for i in unique.values()
                       if valid_price(i.get('old')) and i['old']>i['price'])
        if counts:
            pct,n=counts.most_common(1)[0]
            if n/len(unique)>=majority:
                flagged.update(i['id'] for i in unique.values() if valid_price(i.get('old'))
                               and round((1-i['price']/i['old'])*100)==pct)
    return flagged
