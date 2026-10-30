"""Independent transition/read-only regression checks; no app import/live IO.

Uses temporary SQLite, in-memory ledger fixtures, AST-extracted route bodies,
and public-client mocks. Never reads credentials or the production database.
"""
import ast
import copy
import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[1]
_safety_patches = []


def setUpModule():
    # Also stop unexpected future imports/helpers from opening sockets or git.
    for target in ('urllib.request.urlopen', 'socket.create_connection', 'socket.socket.connect', 'subprocess.Popen'):
        guard = patch(target, side_effect=AssertionError('External IO forbidden in transition tests'))
        guard.start()
        _safety_patches.append(guard)


def tearDownModule():
    while _safety_patches:
        _safety_patches.pop().stop()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def route_body(name, namespace):
    source = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig'))
    fn = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    fn = copy.deepcopy(fn)
    fn.decorator_list = []
    tree = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    exec(compile(tree, str(ROOT / 'app.py'), 'exec'), namespace)
    return namespace[name]


class ReferenceTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.paper-transition-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'isolated.db'
        self.a = load('transition_astra', ROOT / 'analyzer/astra_ultra.py')
        self.now = 1_793_376_000 + 600
        self.p = dict(cash=1000., start_capital=1000., positions=[], trades=[],
                      value_history=[], monitor_success_at=self.now)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.executescript('CREATE TABLE crypto_portfolios(user_id INTEGER PRIMARY KEY,data TEXT);'
                            'CREATE TABLE settings(user_id INTEGER PRIMARY KEY,crypto_strategy_version TEXT);'
                            "INSERT INTO settings VALUES(1,'volume_confirmed');"
                            "INSERT INTO settings VALUES(2,'classic');")
            c.execute('INSERT INTO crypto_portfolios VALUES(1,?)', (json.dumps(self.p),))
            c.execute('INSERT INTO crypto_portfolios VALUES(2,?)', (json.dumps(self.p),))
        self.a.init_schema(self.path)
        self.bars = Mock(side_effect=AssertionError('Unexpected market acquisition'))
        self.quotes = Mock(side_effect=AssertionError('Unexpected market acquisition'))

    def persist(self, uid=1):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('UPDATE crypto_portfolios SET data=? WHERE user_id=?', (json.dumps(self.p), uid))

    def marker(self):
        return dict(version='crypto_offensive_quality_v1', started_at=self.now,
                    rule_hash='fixture', rules={'fee_pct': .002, 'slippage_pct': .0005})

    def cycle(self):
        return self.a.cycle(self.path, bars=self.bars, quote=self.quotes, clock=lambda: self.now)

    def test_source_policy_suffix_owner_only_and_readonly(self):
        self.p['paper_execution_policy'] = self.marker()
        self.persist(1)
        self.persist(2)
        before = self.path.read_bytes()
        p1, strategy1 = self.a.source(self.path, 1)
        _, strategy2 = self.a.source(self.path, 2)
        self.assertEqual(strategy1, 'volume_confirmed|crypto_offensive_quality_v1')
        self.assertEqual(strategy2, 'classic')
        self.assertEqual(p1, self.p)
        self.assertEqual(self.path.read_bytes(), before)

    def test_marker_records_one_change_without_resetting_costs_or_archived_hash(self):
        a = self.a
        old = a.seed(dict(self.p, monitor_success_at=self.now-86400), 'volume_confirmed', self.now - 86400)
        old.update(version=a.PREVIOUS_VERSION, rule_hash=a.PREVIOUS_RULE_HASH,
                   rules=copy.deepcopy(a.PREVIOUS_RULES), cash=970., equity=970.,
                   fees_paid=4., trades=[dict(id='astra-old-loss', pnl_eur=-30.)])
        encoded_old = json.dumps(old)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('INSERT INTO astra_ultra_runs VALUES(1,?,?)', (a.PREVIOUS_VERSION, encoded_old))
            c.execute('INSERT INTO astra_ultra_equity VALUES(1,?,?,?)',
                      (a.PREVIOUS_VERSION, self.now - 100, '{"equity":970}'))
        before = self.cycle()
        with closing(sqlite3.connect(self.path)) as c:
            archived_rows = list(c.execute('SELECT version,data FROM astra_ultra_runs WHERE version<>?', (a.VERSION,)))
            old_equity = list(c.execute('SELECT * FROM astra_ultra_equity WHERE version<>?', (a.VERSION,)))
            source_before = c.execute('SELECT data FROM crypto_portfolios WHERE user_id=1').fetchone()[0]
        self.assertEqual(archived_rows, [(a.PREVIOUS_VERSION, encoded_old)])
        self.assertEqual(before['prior_versions'][0]['state_sha256'], a._state_hash(old))
        self.p['paper_execution_policy'] = self.marker()
        self.persist()
        self.now += 30
        after = self.cycle()
        for key in ('started_at', 'initial_capital', 'version_started_at', 'version', 'rule_hash',
                    'rules', 'cash', 'trades', 'fees_paid', 'prior_versions', 'baseline_start_equity',
                    'baseline_start_capital', 'baseline_initial_reserve', 'baseline_costs',
                    'baseline_equity', 'baseline_peak', 'baseline_max_drawdown_pct', 'baseline_old_trades'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after['baseline_strategy'], 'volume_confirmed')
        self.assertEqual(after['baseline_last_strategy'], 'volume_confirmed|crypto_offensive_quality_v1')
        self.assertEqual(len(after['baseline_strategy_changes']), 1)
        self.assertEqual(after['baseline_invalid'], [])
        self.now += 30
        again = self.cycle()
        self.assertEqual(again['baseline_strategy_changes'], after['baseline_strategy_changes'])
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(list(c.execute('SELECT version,data FROM astra_ultra_runs WHERE version<>?', (a.VERSION,))), archived_rows)
            self.assertEqual(list(c.execute('SELECT * FROM astra_ultra_equity WHERE version<>?', (a.VERSION,))), old_equity)
            source_after = json.loads(c.execute('SELECT data FROM crypto_portfolios WHERE user_id=1').fetchone()[0])
        source_after.pop('paper_execution_policy')
        self.assertEqual(source_after, json.loads(source_before))
        self.bars.assert_not_called()
        self.quotes.assert_not_called()

    def test_inherited_position_cost_reserve_is_not_charged_again_at_policy_change(self):
        a = self.a
        inherited = dict(id='inherited', symbol='BTC', direction='LONG', margin_eur=100.,
                         leverage=4, entry_price=100., last_price=100., unrealized_eur=0.)
        p = dict(cash=900., start_capital=1000., trades=[], positions=[inherited], monitor_success_at=self.now-86400)
        state = a.seed(p, 'volume_confirmed', self.now - 86400)
        self.assertEqual(state['baseline_initial_reserve'], 1.)
        p['positions'][0].update(last_price=110., unrealized_eur=40.)
        a.benchmark(state, p, 'volume_confirmed', self.now)
        self.assertAlmostEqual(state['baseline_costs'], .1)
        expected_equity = state['baseline_equity']
        original = copy.deepcopy(state)
        a.benchmark(state, p, 'volume_confirmed|crypto_offensive_quality_v1', self.now+30)
        self.assertEqual(state['baseline_equity'], expected_equity)
        self.assertEqual(state['baseline_costs'], original['baseline_costs'])
        p['positions'] = []
        p['cash'] = 1040.
        p['trades'].append(dict(inherited, exit_price=110., pnl_eur=40.))
        a.benchmark(state, p, 'volume_confirmed|crypto_offensive_quality_v1', self.now+60)
        self.assertAlmostEqual(state['baseline_costs'], .1)
        self.assertAlmostEqual(state['baseline_equity'], 1039.9)
        self.assertEqual(len(state['baseline_strategy_changes']), 1)


class ReadonlySurfaceTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.uid = 1
        self.policy = dict(version='crypto_offensive_quality_v1', started_at=1_793_376_000,
                           rule_hash='fixture', rules={'closed_only': True})
        self.ledger = dict(cash=1000., trades=[{'id': 'old'}], paper_execution_policy=self.policy)
        self.db = types.SimpleNamespace(
            load_crypto_portfolio=Mock(side_effect=lambda uid: copy.deepcopy(self.ledger)),
            get_settings=Mock(return_value={'crypto_strategy_version': 'volume_confirmed'}),
            save_crypto_portfolio=Mock(side_effect=AssertionError('GET write')),
            set_crypto_strategy_version=Mock(side_effect=AssertionError('GET settings write')))
        self.signals = types.SimpleNamespace(generate_crypto_recommendations=Mock(return_value={'count': 0, 'suggestions': []}))
        package = types.ModuleType('analyzer')
        package.crypto_signals = self.signals
        self.import_patch = patch.dict(sys.modules, {'analyzer': package})
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.ns = dict(db_store=self.db, get_current_user_id=lambda: self.uid,
                       request=request, jsonify=jsonify, crypto_signals=self.signals)

    def test_strategy_get_reads_policy_without_enrollment_or_ledger_write(self):
        fn = route_body('api_crypto_strategy', self.ns)
        original = copy.deepcopy(self.ledger)
        with self.app.test_request_context('/api/crypto/strategy', method='GET'):
            response = fn()
        self.assertEqual(response.get_json()['execution_policy'], self.policy)
        self.assertEqual(self.ledger, original)
        self.db.save_crypto_portfolio.assert_not_called()
        self.db.set_crypto_strategy_version.assert_not_called()

    def test_other_user_strategy_get_does_not_load_owner_policy(self):
        self.uid = 2
        fn = route_body('api_crypto_strategy', self.ns)
        with self.app.test_request_context('/api/crypto/strategy', method='GET'):
            response = fn()
        self.assertIsNone(response.get_json()['execution_policy'])
        self.db.load_crypto_portfolio.assert_not_called()

    def test_recommendations_opt_in_is_owner_only_without_writes(self):
        fn = route_body('api_crypto_recommendations', self.ns)
        with self.app.test_request_context('/api/crypto/recommendations', method='GET'):
            self.assertTrue(fn().get_json()['ok'])
        self.signals.generate_crypto_recommendations.assert_called_once_with(strategy_version='volume_confirmed', closed_only=True)
        self.uid = 2
        self.signals.generate_crypto_recommendations.reset_mock()
        with self.app.test_request_context('/api/crypto/recommendations', method='GET'):
            self.assertTrue(fn().get_json()['ok'])
        self.signals.generate_crypto_recommendations.assert_called_once_with(strategy_version='volume_confirmed')
        self.db.save_crypto_portfolio.assert_not_called()

    def test_research_exports_policy_evidence_and_only_allowlisted_fields(self):
        api = load('transition_research', ROOT / 'analyzer/astra_research_api.py')
        metadata = dict(paper_policy_version='crypto_offensive_quality_v1', paper_policy_rule_hash='fixture',
                        planned_risk_eur=15., estimated_entry_cost_eur=1., estimated_exit_cost_eur=1.,
                        signal_bar_end=1_793_376_000, entry_quote_at=1_793_376_010,
                        signal_price=100., signal_strategy_version='volume_confirmed', risk_equity_eur=1000.)
        reference = dict(paper_execution_policy=dict(self.policy, secret='SECRET'),
                         paper_policy_quote_times={'BTC': 123, 'private_key': 'SECRET'},
                         positions=[dict(id='new', **metadata, password='SECRET')],
                         trades=[dict(id='old', pnl_eur=1.)], secret='SECRET')
        projected = api._project(reference, api.REFERENCE)
        self.assertEqual(projected['positions'][0], dict(id='new', **metadata))
        self.assertEqual(projected['paper_policy_quote_times'], {'BTC': 123})
        self.assertEqual(projected['paper_execution_policy'], self.policy)
        self.assertNotIn('SECRET', json.dumps(projected))

    def test_policy_import_or_initialization_failure_does_not_stop_existing_exits(self):
        tree = ast.parse((ROOT / 'analyzer/crypto_watch.py').read_text(encoding='utf-8-sig'))
        check = copy.deepcopy(next(node for node in ast.walk(tree)
                                   if isinstance(node, ast.FunctionDef) and node.name == 'check'))
        ns = dict(storage_runtime=types.SimpleNamespace(maintenance_required=lambda _: False, is_render=lambda: False),
                  db_store=types.SimpleNamespace(DB_PATH='fixture-only-no-open'),
                  scheduler_tasks=types.SimpleNamespace(crypto_monitor_positions=Mock(return_value={'ok': True})))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[check], type_ignores=[])), 'isolated-watch-check', 'exec'), ns)
        # The fake analyzer has no __path__ or policy attribute: import fails.
        self.assertTrue(ns['check']()['ok'])
        self.assertEqual(ns['scheduler_tasks'].crypto_monitor_positions.call_count, 1)
        sys.modules['analyzer'].crypto_paper_policy = types.SimpleNamespace(initialize=Mock(side_effect=ValueError('invalid marker')))
        self.assertTrue(ns['check']()['ok'])
        self.assertEqual(ns['scheduler_tasks'].crypto_monitor_positions.call_count, 2)


class PublicTickerCompatibilityTests(unittest.TestCase):
    def test_missing_exchange_timestamp_preserves_legacy_ticker_fields(self):
        client = load('transition_okx', ROOT / 'analyzer/okx_client.py')
        payload = {'data': [dict(last='101', high24h='105', low24h='98', volCcy24h='10000', open24h='100')]}
        with patch.object(client, '_http_get', return_value=payload), patch.object(client.time, 'time', return_value=1234):
            result = client.fetch_ticker('BTC')
        self.assertEqual(result['last'], 101.)
        self.assertEqual(result['change_pct'], 1.)
        self.assertEqual(result['ts'], 1234)
        self.assertIsNone(result['market_timestamp'])

    def test_exchange_timestamp_is_distinct_from_local_cache_timestamp(self):
        client = load('transition_okx_timestamp', ROOT / 'analyzer/okx_client.py')
        payload = {'data': [dict(last='101', high24h='105', low24h='98', volCcy24h='10000', open24h='100', ts='1200000')]}
        with patch.object(client, '_http_get', return_value=payload) as http, patch.object(client.time, 'time', return_value=1234):
            result = client.fetch_ticker('BTC')
            again = client.fetch_ticker('BTC')
        self.assertEqual(result['market_timestamp'], 1200.)
        self.assertEqual(result['ts'], 1234)
        self.assertIs(result, again)
        self.assertEqual(http.call_count, 1)


class PolicyInvariantReviewTests(unittest.TestCase):
    """Independent invariants using the policy suite's isolated ledger fixture."""
    def setUp(self):
        fixture_module = load('transition_policy_fixture', ROOT / 'tests/test_crypto_paper_policy.py')
        self.fixture = fixture_module.PolicyTests('test_booking_metadata_gross_cash_and_history_preserved')
        self.addCleanup(self.fixture.doCleanups)
        make_temp = tempfile.TemporaryDirectory
        # Windows sandbox's virtual TEMP can deny SQLite creation; keep the
        # shared fixture inside this verified writable worktree.
        with patch.object(fixture_module.tempfile, 'TemporaryDirectory',
                          side_effect=lambda: make_temp(prefix='.policy-review-', dir=ROOT)):
            self.fixture.setUp()

    def test_completed_signal_cannot_be_reused_after_hour_boundary(self):
        f = self.fixture
        f.signal['signal_bar_end'] = int(f.now // 3600) * 3600 - 3600
        result = f.plan()
        self.assertFalse(result['ok'], 'Fresh entry quote must not revive a stale completed-candle signal')

    def test_same_version_with_changed_rules_fails_closed_without_relabelling(self):
        f = self.fixture
        f.policy.initialize(1)
        original = f.read()
        for mutation in ('hash', 'rules'):
            with self.subTest(mutation=mutation):
                p = copy.deepcopy(original)
                if mutation == 'hash':
                    p['paper_execution_policy']['rule_hash'] = 'incompatible-hash'
                else:
                    p['paper_execution_policy']['rules']['max_entry_risk_pct'] = 99
                f.db.save_crypto_portfolio(1, p)
                before = f.path.read_bytes()
                try:
                    initialized = f.policy.initialize(1)
                except ValueError:
                    initialized = {'ok': False}
                self.assertFalse(initialized.get('ok'), 'Version equality alone cannot validate frozen execution rules')
                try:
                    result = f.policy.run(1)
                except ValueError:
                    result = {'actions': []}
                self.assertEqual(result['actions'], [])
                self.assertEqual(f.path.read_bytes(), before)

    def test_unknown_policy_version_cannot_be_overwritten_by_older_executable(self):
        f = self.fixture
        p = f.read()
        p['paper_execution_policy'] = dict(version='crypto_offensive_quality_v2',
            started_at=f.now-100, rule_hash='future-hash', rules={'unknown_future_rule': True})
        f.db.save_crypto_portfolio(1, p)
        before = f.path.read_bytes()
        try:
            initialized = f.policy.initialize(1)
        except ValueError:
            initialized = {'ok': False}
        self.assertFalse(initialized.get('ok'), 'Unknown policy needs an explicit forward-compatible transition')
        try:
            result = f.policy.run(1)
        except ValueError:
            result = {'actions': []}
        self.assertEqual(result['actions'], [])
        self.assertEqual(f.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
