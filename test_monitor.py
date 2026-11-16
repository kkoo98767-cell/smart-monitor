"""Offline regression suite. Does not contact any store or Telegram."""
import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import smart_monitor as m
from monitor_history import Archive, uniform_discount_ids

if os.environ.get('MONITOR_TEST_TMP'):
    tempfile.tempdir=os.environ['MONITOR_TEST_TMP']


def item(price=1000,old=None,pid='p',store='إكسترا'):
    return dict(id=pid,title='منتج اختباري',price=price,old=old,url='https://www.extra.com/p/'+pid,
                store=store,method='fixture')


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,'monitor.db')
        self.now=1800000000
        self.a=Archive(self.path,lambda:self.now)

    def tearDown(self):
        self.a.close(); self.tmp.cleanup()

    def observe(self,price,run,old=None,at=None):
        it=item(price,old)
        self.assertTrue(self.a.observe(it,'phones',run,self.now if at is None else at))
        return it

    def test_archive_all_products_and_run_dedup(self):
        it=self.observe(1000,'a')
        self.assertFalse(self.a.observe(it,'phones','a'))
        self.assertIsNone(self.a.evaluate(it,'a'))
        self.assertEqual(self.a.db.execute('SELECT readings,title,category FROM products').fetchone(),
                         (1,'منتج اختباري','phones'))

    def test_second_reading_12_hours_boundary_and_current_exclusion(self):
        self.observe(1000,'first',at=self.now-43200)
        it=self.observe(750,'second')
        result=self.a.evaluate(it,'second')
        self.assertTrue(result['verified']); self.assertEqual(result['ref'],1000)
        self.assertEqual(result['stats']['count'],1)
        self.assertEqual(result['stats']['average'],1000)
        self.assertIsNone(self.a.evaluate(it,'second',at=self.now-1))

    def test_announced_discount_exact_25_percent(self):
        self.assertIsNotNone(self.a.evaluate(item(750,1000),'a'))
        self.assertIsNone(self.a.evaluate(item(751,1000),'a'))

    def test_fixed_discount_requires_independent_reading(self):
        self.observe(500,'first',1000,at=self.now-43200)
        self.assertIsNone(self.a.evaluate(item(500,1000),'second'))
        # A genuine additional drop remains eligible as verified.
        result=self.a.evaluate(item(375,1000),'second')
        self.assertTrue(result['verified'])

    def test_uniform_store_discount(self):
        items=[item(50,100,str(n),'درعة') for n in range(20)]
        ids=uniform_discount_ids(items)
        self.assertEqual(len(ids),20)
        self.assertIsNone(self.a.evaluate(items[0],'a',True))
        self.assertFalse(uniform_discount_ids(items[:5]))

    def test_impossible_price_is_quarantined(self):
        self.observe(1000,'first',at=self.now-86400)
        self.assertFalse(self.a.observe(item(250),'phones','bad'))
        self.assertFalse(self.a.observe(item(100,1000),'phones','bad2'))
        self.assertFalse(self.a.observe(item(float('nan')),'phones','bad3'))
        self.assertEqual(self.a.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)

    def test_robust_baseline_rejects_prepromotion_inflation(self):
        rows=[(self.now-(20-n)*86400,1000 if n<14 else 2000,None) for n in range(20)]
        self.assertEqual(self.a.baseline(rows),1000)
        self.assertEqual(self.a.baseline([(1,1000,None),(86401,1000,None),(172801,500,None)]),1000)

    def test_repeat_runs_reopen_same_database(self):
        self.observe(1000,'first',at=self.now-86400)
        self.a.close(); self.a=Archive(self.path,lambda:self.now)
        it=self.observe(750,'second')
        self.assertTrue(self.a.evaluate(it,'second')['verified'])
        self.assertEqual(self.a.db.execute('SELECT readings FROM products').fetchone()[0],2)

    def test_legacy_migration_is_idempotent_and_preserves_alerts(self):
        self.a.close()
        db=sqlite3.connect(self.path)
        db.execute("DELETE FROM meta WHERE k='archive_migration_v1'")
        db.execute("INSERT INTO prices VALUES('legacy','2026-01-01',1000)")
        db.execute("INSERT INTO alerted VALUES('legacy',750)"); db.commit(); db.close()
        self.a=Archive(self.path,lambda:self.now)
        self.a.migrate()
        self.assertEqual(self.a.db.execute("SELECT count(*) FROM observations WHERE id='legacy'").fetchone()[0],1)
        self.assertFalse(self.a.can_alert(item(750,pid='legacy')))
        self.assertEqual(self.a.db.execute("SELECT readings FROM products WHERE id='legacy'").fetchone()[0],1)

    def test_legacy_identity_adopts_history(self):
        self.a.observe(item(1000,pid='legacy'),'phones','a',self.now-86400)
        new=item(750,pid='stable'); new['legacy_id']='legacy'
        self.a.observe(new,'phones','b')
        self.assertEqual(new['id'],'legacy')
        self.assertTrue(self.a.evaluate(new,'b')['verified'])

    def test_alert_suppression_and_lower_price_exception(self):
        self.a.mark_alert(item(750))
        self.assertFalse(self.a.can_alert(item(750)))
        self.assertFalse(self.a.can_alert(item(800)))
        self.assertTrue(self.a.can_alert(item(740)))
        self.now+=4*86400
        self.assertTrue(self.a.can_alert(item(800)))
        self.assertFalse(self.a.can_alert(item(750)))


class ReaderTests(unittest.TestCase):
    def setUp(self):
        m.START=time.time();m.STORE_DEADLINE=float('inf');m.won.clear()

    def prices(self,html):
        return m.card_prices(m.BeautifulSoup('<div>'+html+'</div>','html.parser').div)

    def test_extra_saving_credit_instalments_percent(self):
        html='''<span class="price">1319 ر.س</span><del><span>1649 ر.س</span></del>
          <div>وفر <span class="price">330 ر.س</span></div>
          <div>20.01% خصم</div><div>66 ر.س رصيد جود جولد</div>
          <div>قسّم مشترياتك حتى <span class="price">36</span> دفعات</div>'''
        self.assertEqual(self.prices(html),(1319,1649))

    def test_extra_1500_to_1400_not_100(self):
        self.assertEqual(self.prices('''<b class="price">1400 ر.س</b><del>1500 ر.س</del>
            <div class="saving">وفر <span class="price">100 ر.س</span></div>'''),(1400,1500))

    def test_ambiguous_amounts_and_savings_only_rejected(self):
        self.assertEqual(self.prices('<del>1500 ر.س</del><span>وفر 100 ر.س</span>'),(None,None))
        self.assertEqual(self.prices('<span class="price">100 ر.س</span><span class="price">1400 ر.س</span>'),(None,None))

    def test_arabic_numbers(self):
        self.assertEqual(m.num('١٬٣١٩٫٥٠ ر.س'),1319.5)

    def test_noon_next_data_sku_without_url(self):
        data={'props':{'pageProps':{'catalog':{'hits':[dict(sku='N12345A',name='Sony DualSense Controller',
                                                       sale_price=750,price=1000)]}}}}
        html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'
        items,_=m.parse_page(html,'https://www.noon.com/saudi-ar/search/?q=x','نون')
        product=next(iter(items.values()))
        self.assertEqual((product['price'],product['old']),(750,1000))
        self.assertIn('/N12345A/p/',product['url'])

    def test_extra_json_price_object(self):
        data=dict(name='Test Sony Controller',url='/ar-sa/p/123',price=dict(value=1319,currencyIso='SAR'),wasPrice=1649)
        out={};m.walk_products(data,'https://www.extra.com','إكسترا',out)
        self.assertEqual(next(iter(out.values()))['price'],1319)

    def test_fallback_after_zero_and_blocked_method(self):
        good='<script type="application/json">'+json.dumps(dict(name='Test Product',url='/p/1',price=100))+'</script>'
        calls=[]
        def empty(url):calls.append('requests');return '<html>No products</html>'
        def blocked(url):calls.append('cffi');raise m.Blocked('HTTP 403')
        def success(url):calls.append('browser');return good
        with patch.dict(m.STRATS,dict(requests=empty,cffi=blocked,browser=success)):
            items,_=m.get_items('https://www.extra.com/search','إكسترا')
        self.assertEqual(calls,['requests','cffi','browser'])
        self.assertEqual(next(iter(items.values()))['method'],'browser')

    def test_retry_503_429_then_success(self):
        get=Mock(side_effect=[(503,'',{}),(429,'',{'Retry-After':'1'}),(200,'valid page',{})])
        with patch.object(m.time,'sleep') as sleep:
            self.assertEqual(m._fetch_with(get,'https://example.com'),'valid page')
        self.assertEqual(get.call_count,3);self.assertEqual(sleep.call_count,2)

    def test_403_skips_retry(self):
        get=Mock(return_value=(403,'',{}))
        with self.assertRaises(m.Blocked):m._fetch_with(get,'https://example.com')
        self.assertEqual(get.call_count,1)

    def test_fake_telegram_marks_only_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Archive(os.path.join(tmp,'db'))
            it=item(750,1000)
            result=a.evaluate(it,'run')
            c=dict(item=it,category='جوالات',**result)
            with patch.object(m,'send_telegram',return_value=False):
                m.send_candidates([c],a)
            self.assertTrue(a.can_alert(it))
            with patch.object(m,'send_telegram',return_value=True) as send:
                m.send_candidates([c],a)
            self.assertFalse(a.can_alert(it));self.assertIn('⚠️',send.call_args.args[0])
            a.close()

    def test_selftest_suppresses_all_telegram(self):
        with patch.object(m,'SELFTEST',True),patch.object(m.requests,'post') as post:
            self.assertFalse(m.send_telegram('never send'))
        post.assert_not_called()

    def test_telegram_requires_api_ok(self):
        with patch.object(m,'SELFTEST',False),patch.object(m,'TG_TOKEN','dummy'),patch.object(m,'TG_CHAT','dummy'):
            with patch.object(m.requests,'post',return_value=Mock(status_code=200,json=lambda:{'ok':False})):
                self.assertFalse(m.send_telegram('fixture'))

    def test_jsonld_foreign_currency_and_unavailable_rejected(self):
        for extra in ({'priceCurrency':'USD'},{'availability':'https://schema.org/OutOfStock'}):
            data={'@type':'Product','name':'Test Product','url':'/p/1','offers':dict(price=100,**extra)}
            soup=m.BeautifulSoup('<script type="application/ld+json">'+json.dumps(data)+'</script>','html.parser')
            self.assertFalse(m.jsonld_items(soup,'https://www.extra.com','إكسترا'))

    def test_derived_next_data_fallback(self):
        state='<script id="__NEXT_DATA__" type="application/json">{"buildId":"abc123","props":{}}</script>'
        get=Mock(side_effect=[state,json.dumps(dict(name='Test Product',price=100,url='/p/1'))])
        with patch.dict(m.STRATS,{'requests':get}):
            items,_=m.get_items('https://www.noon.com/saudi-ar/search/?q=x','نون',('requests',))
        self.assertTrue(items)
        self.assertIn('/_next/data/abc123/saudi-ar/search.json?q=x',get.call_args.args[0])

    def test_noon_uae_region_and_generic_goldenscent_tile_rejected(self):
        it=item(200,1000,store='نون');it['url']='https://www.noon.com/uae-en/product/N123/p/'
        self.assertFalse(m.usable_item(it))
        it['url']='https://www.noon.com/saudi-ar/product/N123/p/'
        self.assertTrue(m.usable_item(it))
        it['title']='عرض المنتج'
        self.assertFalse(m.usable_item(it))

    def test_selftest_main_keeps_database_and_sends_nothing(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'monitor.db');a=Archive(path);a.close()
            before=hashlib.sha256(Path(path).read_bytes()).digest()
            cwd=os.getcwd()
            try:
                os.chdir(tmp)
                with patch('sys.argv',['smart_monitor.py','--selftest','--telegram-check','--db',path]), \
                     patch.object(m,'collect',return_value=([dict(item(),category_key='phones')],{})), \
                     patch.object(m.requests,'post') as post:
                    self.assertEqual(m.main(),0)
                post.assert_not_called()
            finally:
                os.chdir(cwd);m.SELFTEST=False
            self.assertEqual(before,hashlib.sha256(Path(path).read_bytes()).digest())


class GitSaveTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git'),'git needed')
    def test_binary_database_rebase_keeps_local_monitor_and_other_bot(self):
        # Actual isolated Git repositories, not a mocked assertion about shell text.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(cwd,*args):
                result=subprocess.run(['git','-c','protocol.file.allow=always',*args],cwd=cwd,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                if result.returncode:
                    raise subprocess.CalledProcessError(result.returncode,result.args,
                                                        output=result.stdout,stderr=result.stderr)
                return result.stdout
            git(root,'init','--bare','origin.git')
            git(root,'clone',str(root/'origin.git'),'a')
            a=root/'a'
            git(a,'config','user.email','test@example.com');git(a,'config','user.name','test')
            git(a,'checkout','-b','main')
            (a/'monitor.db').write_bytes(b'old\x00db');(a/'quality.db').write_bytes(b'quality\x00old')
            git(a,'add','.');git(a,'commit','-m','initial');git(a,'push','-u','origin','main')
            git(root,'clone','--branch','main',str(root/'origin.git'),'b')
            b=root/'b'
            git(b,'config','user.email','test@example.com');git(b,'config','user.name','test')
            (a/'monitor.db').write_bytes(b'local\x00monitor');git(a,'add','monitor.db');git(a,'commit','-m','local prices')
            (b/'monitor.db').write_bytes(b'remote\x00monitor');(b/'quality.db').write_bytes(b'quality\x00new')
            git(b,'add','.');git(b,'commit','-m','concurrent updates');git(b,'push')
            # A push at this point must fail (non-fast-forward), then original recovery works.
            with self.assertRaises(subprocess.CalledProcessError):git(a,'push')
            git(a,'pull','--rebase','-X','theirs','origin','main');git(a,'push','origin','HEAD:main')
            self.assertEqual((a/'monitor.db').read_bytes(),b'local\x00monitor')
            self.assertEqual((a/'quality.db').read_bytes(),b'quality\x00new')


if __name__=='__main__':
    unittest.main()
