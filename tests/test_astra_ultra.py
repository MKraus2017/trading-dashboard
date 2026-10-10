"""Offline experiment tests: no app bootstrap, live data, account or messaging."""
import ast
import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from functools import wraps
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
B = 14400
spec = importlib.util.spec_from_file_location('astra_test', ROOT/'analyzer/astra_ultra.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class AstraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'test.db'
        self.now = 1800000000 // B * B + 600
        self.p = dict(cash=1000, positions=[], trades=[], start_capital=1000, monitor_success_at=self.now)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript("CREATE TABLE crypto_portfolios(user_id INTEGER PRIMARY KEY,data TEXT); CREATE TABLE settings(user_id INTEGER PRIMARY KEY,crypto_strategy_version TEXT); INSERT INTO settings VALUES(1,'volume_confirmed');")
        self.persist()
        a.init_schema(self.path)
        self.bars = Mock(side_effect=lambda sym: self.candles())
        self.quote = Mock(side_effect=lambda sym: dict(price=102, market_at=self.now))

    def persist(self):
        self.p['monitor_success_at'] = self.now
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('INSERT OR REPLACE INTO crypto_portfolios VALUES(1,?)',(json.dumps(self.p),))

    def cycle(self):
        return a.cycle(self.path,bars=self.bars,quote=self.quote,clock=lambda:self.now)

    def candles(self, breakout=True):
        end = int(self.now//B)*B
        rows = [[int((end-(500-i)*B)*1000),'100','101','99','100','1','1','100','1'] for i in range(500)]
        if breakout:
            rows[-1][2] = '103'
            rows[-1][4] = '102'
        return list(reversed(rows))

    def next_hour(self):
        self.now = int(self.now//B)*B+B+10
        self.persist()

    def test_seed_matches_source_equity_not_free_cash(self):
        self.p['positions']=[dict(id='old',margin_eur=100,leverage=2,entry_price=100,last_price=110,unrealized_eur=20)]
        self.p['cash']=900
        self.persist()
        s=self.cycle()
        self.assertEqual(s['initial_capital'],1020)
        self.assertEqual(s['cash'],1020)
        self.assertEqual(s['baseline_equity'],1020)
        self.bars.assert_not_called()

    def test_seed_requires_fresh_monitor_and_positive_equity(self):
        with self.assertRaises(ValueError): a.seed(dict(self.p,monitor_success_at=self.now-100),'classic',self.now)
        with self.assertRaises(ValueError): a.seed(dict(self.p,cash=0),'classic',self.now)

    def test_never_modifies_original_ledger_or_strategy(self):
        before=self.p.copy()
        self.cycle(); self.next_hour(); self.cycle()
        p,strategy=a.source(self.path,1)
        self.assertEqual(p,self.p)
        self.assertEqual(strategy,'volume_confirmed')
        self.assertEqual(p['cash'],before['cash'])

    def test_closed_candles_channels_exclude_signal_bar(self):
        self.next_hour()
        rows=self.candles()
        sig=a.signal(rows,self.now)
        self.assertTrue(sig['enter'])
        self.assertEqual(sig['upper24'],101)
        rows.insert(0,[int(self.now//B*B*1000),'1','99999','1','9999','1','1','1','0'])
        self.assertEqual(a.signal(rows,self.now),sig)

    def test_gap_stale_invalid_candles_fail(self):
        self.next_hour()
        rows=self.candles(); rows.pop(30)
        with self.assertRaises(ValueError): a.signal(rows,self.now)
        with self.assertRaises(ValueError): a.signal(self.candles()[:200],self.now)
        with self.assertRaises(ValueError): a.signal(self.candles(),self.now+600)
        rows=self.candles(); rows[0][2]='1'
        with self.assertRaises(ValueError): a.signal(rows,self.now)

    def test_entry_after_signal_costs_risk_caps_and_idempotency(self):
        self.cycle(); self.next_hour(); s=self.cycle()
        self.assertEqual(len(s['positions']),2)
        for pos in s['positions']:
            self.assertGreater(pos['entry_price'],102)
            self.assertGreaterEqual(pos['opened_at'],pos['signal']['bar_end'])
            self.assertLessEqual(pos['planned_risk'],5)
            self.assertLessEqual(pos['margin_eur'],400)
            self.assertEqual(pos['leverage'],1)
        self.assertLess(s['equity'],1000)
        self.assertGreater(s['cash'],0)
        self.assertEqual(len(self.cycle()['positions']),2)

    def test_restart_preserves_capital_and_version(self):
        s=self.cycle(); self.next_hour(); s=self.cycle()
        self.p['cash']=2000; self.persist()
        after=self.cycle()
        self.assertEqual(after['initial_capital'],1000)
        self.assertEqual(after['positions'],s['positions'])

    def test_stale_quote_blocks_all_new_entries(self):
        self.cycle(); self.next_hour()
        self.quote.side_effect=lambda sym:dict(price=102,market_at=self.now-61)
        s=self.cycle()
        self.assertEqual(s['positions'],[])
        self.assertTrue(s['last_error'])
        self.assertEqual(s['data_errors'],1)

    def test_partial_universe_does_not_selectively_open(self):
        self.cycle(); self.next_hour()
        self.bars.side_effect=lambda sym:[] if sym=='SOL' else self.candles()
        s=self.cycle()
        self.assertEqual(s['positions'],[])

    def test_quotes_revalidated_after_other_symbols_finish(self):
        self.cycle();self.next_hour()
        def tick(sym):
            self.now+=35
            return dict(price=102,market_at=self.now)
        self.quote.side_effect=tick
        s=self.cycle()
        self.assertEqual(s['positions'],[])
        self.assertIn('vor Entscheidung veraltet',s['last_error'])

    def test_second_observed_quote_honors_stop_immediately(self):
        self.cycle();self.next_hour();self.cycle();self.next_hour()
        calls={}
        def tick(sym):
            calls[sym]=calls.get(sym,0)+1
            return dict(price=102 if calls[sym]==1 else 80,market_at=self.now)
        self.quote.side_effect=tick
        s=self.cycle()
        self.assertEqual(len(s['trades']),2)
        self.assertFalse(any(p['symbol'] in ('BTC','ETH') for p in s['positions']))
        self.assertTrue(all(t['close_reason']=='Stop-Loss' for t in s['trades']))

    def test_exits_release_capacity_before_new_entries(self):
        self.cycle();self.next_hour();s=self.cycle()
        for p in s['positions']:
            p['symbol']='ETH' if p['symbol']=='BTC' else 'SOL'
        with closing(sqlite3.connect(self.path)) as c,c:
            c.execute('UPDATE astra_ultra_runs SET data=?',(json.dumps(s),))
        self.next_hour()
        original=a.signal
        def sig(rows,now):
            value=original(rows,now)
            value['exit']=True
            return value
        with patch.object(a,'signal',side_effect=sig):s=self.cycle()
        self.assertTrue(any(p['symbol']=='BTC' for p in s['positions']))
        self.assertEqual(len(s['trades']),2)

    def test_no_backfill_after_downtime(self):
        self.cycle(); self.now+=B*5; self.persist()
        s=self.cycle()
        self.assertEqual(s['positions'],[])
        self.assertEqual(s['missed_hours'],5)
        self.assertEqual(s['observation_gaps'],1)

    def test_stop_at_worse_quote_not_ideal_stop_and_fees_reconcile(self):
        self.cycle(); self.next_hour(); s=self.cycle()
        initial_position=s['positions'][0]
        self.now+=30; self.persist()
        self.quote.side_effect=lambda sym:dict(price=80,market_at=self.now)
        s=self.cycle()
        self.assertEqual(len(s['positions']),0)
        self.assertEqual(len(s['trades']),2)
        t=s['trades'][0]
        self.assertLess(t['exit_price'],initial_position['stop_loss'])
        self.assertLess(t['pnl_eur'],-initial_position['planned_risk'])
        self.assertAlmostEqual(s['cash'],1000+sum(x['pnl_eur'] for x in s['trades']))
        self.assertAlmostEqual(s['fees_paid'],sum(x['entry_fee']+x['exit_fee'] for x in s['trades']))

    def test_stale_data_does_not_fabricate_exit(self):
        self.cycle(); self.next_hour(); self.cycle()
        self.now+=30; self.persist()
        self.quote.side_effect=TimeoutError()
        s=self.cycle()
        self.assertEqual(len(s['positions']),2)
        self.assertEqual(s['trades'],[])
        self.assertFalse(a.report(self.path,1,self.now)['healthy'])

    def test_no_reentry_in_stop_cycle(self):
        self.cycle(); self.next_hour(); self.cycle(); self.next_hour()
        calls={}
        def tick(sym):
            calls[sym]=calls.get(sym,0)+1
            return dict(price=80 if calls[sym]==1 and sym in ('BTC','ETH') else 102,market_at=self.now)
        self.quote.side_effect=tick
        s=self.cycle()
        self.assertFalse(any(p['symbol'] in ('BTC','ETH') for p in s['positions']))

    def test_baseline_costs_only_since_start_and_reset_invalid(self):
        self.p['trades']=[dict(id='historical')];self.persist();s=self.cycle()
        self.p['trades'].append(dict(id='new',margin_eur=100,leverage=2,entry_price=100,last_price=110,exit_price=110))
        self.p['cash']=1020;self.persist();s=self.cycle()
        self.assertAlmostEqual(s['baseline_costs'],1.05)
        self.assertAlmostEqual(s['baseline_equity'],1018.95)
        self.p['trades']=[];self.persist();s=self.cycle()
        self.assertTrue(s['baseline_invalid'])

    def test_rule_change_requires_version_instead_of_silent_reset(self):
        s=a.seed(self.p,'classic',self.now);s['rule_hash']='old'
        with self.assertRaises(ValueError):a.step(s,self.bars,self.quote,lambda:self.now)

    def test_version_bump_cannot_silently_reset_losing_equity(self):
        self.cycle()
        with closing(sqlite3.connect(self.path)) as c,c:
            c.execute("UPDATE astra_ultra_runs SET version='previous-version'")
        with self.assertRaisesRegex(ValueError,'capital_carry_forward'):self.cycle()

    def test_report_readonly_and_user_isolated(self):
        self.cycle();before=self.path.read_bytes()
        self.assertFalse(a.report(self.path,2,self.now)['started'])
        r=a.report(self.path,1,self.now)
        self.assertNotIn('baseline_old_trades',r['run'])
        self.assertEqual(self.path.read_bytes(),before)

    def test_authenticated_readonly_route(self):
        from flask import Flask,jsonify,redirect,request,session,url_for
        app=Flask('astra-test');app.secret_key='offline-only'
        app.add_url_rule('/login','login_page',lambda:'login')
        ns=dict(app=app,wraps=wraps,jsonify=jsonify,redirect=redirect,request=request,session=session,url_for=url_for,
                is_logged_in=lambda:bool(session.get('user_id')),get_current_user_id=lambda:session.get('user_id'),db_store=types.SimpleNamespace(DB_PATH=self.path))
        tree=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
        for name in ('login_required','api_astra_ultra'):
            node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            exec(compile(ast.Module(body=[node],type_ignores=[]),'isolated-app','exec'),ns)
        fake=types.ModuleType('analyzer');fake.astra_ultra=a
        self.cycle()
        with patch.dict(sys.modules,{'analyzer':fake}),app.test_client() as client:
            self.assertEqual(client.get('/api/crypto/astra_ultra',headers={'Content-Type':'application/json'}).status_code,401)
            with client.session_transaction() as sess:sess['user_id']=2
            r=client.get('/api/crypto/astra_ultra?user_id=1')
            self.assertFalse(r.json['started'])
            self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertEqual(client.post('/api/crypto/astra_ultra').status_code,405)


if __name__=='__main__':unittest.main()
