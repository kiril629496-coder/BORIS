"""Isolated Guardian alert regressions: no database, network or live self-heal."""
import ast
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'boris_guardian.py'


def load_alert_tail(path=SOURCE):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    helper = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_owner_alert_signature'), None)
    quick = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'quick')
    start = next(i for i, n in enumerate(quick.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'sig' for t in n.targets))
    tail = ast.FunctionDef(
        name='alert_tail',
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=x) for x in ('s','bad','healed','send_ok','warnings')], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
        body=copy.deepcopy(quick.body[start:]), decorator_list=[])
    body = ([copy.deepcopy(helper)] if helper else []) + [tail]
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    messages=[]
    scope={'dt':dt, 'json':json, 'tg':messages.append, 'save':lambda state: None}
    exec(compile(module, str(path), 'exec'), scope)
    def tick(state, bad, warnings=None, healed=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return scope['alert_tail'](state, list(bad), list(healed or []), False, list(warnings or []))
    return tick, messages, scope.get('_owner_alert_signature')


def queue(n):
    return f'Очередь: готово 0, старейшая 0 сек, системных ошибок 24ч {n}'


class GuardianEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tick, self.messages, self.signature = load_alert_tail()
        self.state={}

    def test_growing_counter_confirms_same_incident(self):
        self.tick(self.state, [queue(20)])
        self.assertEqual(self.messages, [])
        result=self.tick(self.state, [queue(21)])
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(result[0])

    def test_growing_counter_does_not_spam(self):
        for n in range(20, 90):
            self.tick(self.state, [queue(n)])
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(self.state['last_quick_result']['ok'])
        self.assertEqual(self.state['last_quick_result']['critical'], [queue(89)])

    def test_legacy_sent_signature_migrates_without_repeat(self):
        self.state['last_alert_sig']=queue(1208)
        self.tick(self.state, [queue(1558)])
        self.tick(self.state, [queue(1559)])
        self.assertEqual(self.messages, [])

    def test_legacy_pending_signature_does_not_lose_first_sample(self):
        self.state.update(pending_alert_sig=queue(20),pending_alert_count=1)
        self.tick(self.state, [queue(21)])
        self.assertEqual(len(self.messages), 1)

    def test_transient_does_not_notify_or_announce_recovery(self):
        self.tick(self.state, [queue(20)])
        self.tick(self.state, [])
        self.tick(self.state, [])
        self.assertEqual(self.messages, [])

    def test_confirmed_recovery_needs_two_samples(self):
        self.tick(self.state, [queue(20)])
        self.tick(self.state, [queue(21)])
        self.tick(self.state, [])
        self.assertEqual(len(self.messages), 1)
        self.tick(self.state, [])
        self.assertEqual(len(self.messages), 2)
        self.assertTrue(self.state['last_quick_result']['ok'])

    def test_new_component_is_not_hidden_by_existing_queue(self):
        self.state['last_alert_sig']=queue(20)
        bad=[queue(30), 'API полностью недоступен']
        self.tick(self.state,bad)
        self.tick(self.state,[queue(31),bad[1]])
        self.assertEqual(len(self.messages),1)

    def test_account_identifiers_and_unknown_messages_are_preserved(self):
        a='Контроль ставок: account_123'
        b='Контроль ставок: account_456'
        self.assertNotEqual(self.signature([a]),self.signature([b]))
        self.assertEqual(self.signature(['Очередь: unknown 123']), 'Очередь: unknown 123')

    def test_evidence_is_not_an_old_notification(self):
        self.state['last_alert_sig']=queue(20)
        self.tick(self.state,[queue(80)],warnings=['provider_wait'],healed=['local_check'])
        evidence=self.state['last_quick_result']
        self.assertEqual(evidence['critical'],[queue(80)])
        self.assertEqual(evidence['warnings'],['provider_wait'])
        self.assertEqual(evidence['healed'],['local_check'])
        self.assertIsNotNone(dt.datetime.fromisoformat(evidence['checked_at']).tzinfo)

    def test_order_and_duplicates_are_stable(self):
        a='Контроль ставок: account_123'
        self.assertEqual(self.signature([queue(20),a,a]),self.signature([a,queue(99)]))


if __name__=='__main__':
    unittest.main()
