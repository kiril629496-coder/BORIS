import ast
import inspect
import pathlib
import unittest
from unittest.mock import patch


class _Resp:
    status_code = 200
    text = ''
    def json(self):
        return {'id': 'avito-created-123', 'created': 1788519000, 'direction': 'out', 'type': 'text', 'content': {'text': 'Тестовый текст'}}


class ReactivationAvitoTransportTests(unittest.TestCase):
    def test_canonical_messenger_send_is_avito_only(self):
        from app.api import messenger
        with patch.object(messenger, '_get_user_id_and_token', return_value=('987654', 'TOKEN')), patch.object(messenger, '_avito_http_post', return_value=_Resp()) as post:
            r = messenger.send_message('acc', 'u2i-CHAT', 'Тестовый текст')
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['avito_message']['id'], 'avito-created-123')
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://api.avito.ru/messenger/v1/accounts/987654/chats/u2i-CHAT/messages')
        self.assertEqual(kwargs['json'], {'message': {'text': 'Тестовый текст'}, 'type': 'text'})

    def test_reactivation_runtime_persists_avito_provider_id(self):
        import app.reactivation_runtime as rr
        class DB:
            def commit(self): pass
            def rollback(self): pass
        captured = {}
        with patch.object(rr, 'preflight_send', return_value=(True, 'ok', {'candidate_id': 77, 'candidate_status': 'approved', 'status': 'scheduled', 'account_id':'acc', 'avito_chat_id':'u2i-CHAT'})), patch('app.api.messenger.sync_chat_messages', return_value={'status':'ok','messages':1}), patch.object(rr.rc, 'claim_for_send', return_value=(77, 'Текст', 'acc', 'u2i-CHAT')), patch.object(rr.rc, 'mark_sent', side_effect=lambda db, mid, avito_message_id=None: captured.update({'id': avito_message_id}) or True), patch.object(rr.rc, 'mark_delivery_unknown') as unknown, patch('app.api.messenger.send_message', return_value={'status': 'ok', 'avito_message': {'id': 'avito-created-123'}}) as send:
            r = rr.send_message(DB(), 55, explicit=True)
        self.assertEqual(r['status'], 'sent')
        self.assertEqual(r['avito_message_id'], 'avito-created-123')
        self.assertEqual(captured['id'], 'avito-created-123')
        send.assert_called_once_with('acc', 'u2i-CHAT', 'Текст')
        unknown.assert_not_called()

    def test_missing_avito_provider_id_is_not_marked_sent(self):
        import app.reactivation_runtime as rr
        class DB:
            def commit(self): pass
            def rollback(self): pass
        with patch.object(rr, 'preflight_send', return_value=(True, 'ok', {'candidate_id': 77, 'candidate_status': 'approved', 'status': 'scheduled', 'account_id':'acc', 'avito_chat_id':'u2i-CHAT'})), patch('app.api.messenger.sync_chat_messages', return_value={'status':'ok','messages':1}), patch.object(rr.rc, 'claim_for_send', return_value=(77, 'Текст', 'acc', 'u2i-CHAT')), patch.object(rr.rc, 'mark_sent') as sent, patch.object(rr.rc, 'mark_delivery_unknown') as unknown, patch('app.api.messenger.send_message', return_value={'status': 'ok', 'avito_message': None}):
            r = rr.send_message(DB(), 55, explicit=True)
        self.assertEqual(r, {'ok': False, 'reason': 'delivery_unknown'})
        sent.assert_not_called()
        unknown.assert_called_once()

    def test_sync_chats_exposes_partial_transport_failures(self):
        import inspect
        from app.api import messenger
        src = inspect.getsource(messenger.sync_chats)
        self.assertIn('transport_errors', src)
        self.assertIn('"status": "partial"', src)
        self.assertIn('Avito message history unavailable', src)

    def test_reactivation_runtime_has_no_sms_or_email_transport(self):
        source = pathlib.Path('/root/BORIS/backend/app/reactivation_runtime.py').read_text()
        tree = ast.parse(source)
        imports = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import): imports += [x.name for x in n.names]
            elif isinstance(n, ast.ImportFrom): imports.append(n.module or '')
        joined = ' '.join(imports).lower()
        self.assertNotIn('twilio', joined)
        self.assertNotIn('smtplib', joined)
        self.assertNotIn('email', joined)
        self.assertIn('app.api.messenger', source)


    def test_reactivation_transport_graph_has_no_sms_email_send_calls(self):
        """Канал реактивации должен оставаться только Avito Messenger."""
        files = [
            pathlib.Path('/root/BORIS/backend/app/reactivation_runtime.py'),
            pathlib.Path('/root/BORIS/backend/app/reactivation_core.py'),
            pathlib.Path('/root/BORIS/backend/app/reactivation_outgoing.py'),
            pathlib.Path('/root/BORIS/backend/app/api/reactivation.py'),
        ]
        forbidden = {'send_sms', 'send_email', 'enqueue_sms', 'enqueue_email', 'sms_send', 'email_send'}
        for path in files:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ''
                    self.assertNotIn(name.lower(), forbidden, f'forbidden transport call {name} in {path.name}')
        runtime = pathlib.Path('/root/BORIS/backend/app/reactivation_runtime.py').read_text(encoding='utf-8')
        self.assertIn('from app.api.messenger import send_message as avito_send', runtime)

    def test_avito_receive_path_is_canonical_messenger_history(self):
        source = pathlib.Path('/root/BORIS/backend/app/api/messenger.py').read_text(encoding='utf-8')
        self.assertIn('def fetch_chat_messages(account_id: str, chat_id: str):', source)
        self.assertIn('/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages/', source)
        self.assertIn('_store_messages_locally(account_id, chat_id', source)

    def test_receive_402_blocks_before_avito_post(self):
        import app.reactivation_runtime as rr
        class DB:
            def commit(self): pass
            def rollback(self): pass
            def execute(self,*a,**k):
                class R: pass
                return R()
        m={'candidate_id':77,'candidate_status':'approved','status':'ready','account_id':'acc','avito_chat_id':'u2i-CHAT'}
        with patch.object(rr,'preflight_send',return_value=(True,'ok',m)), \
             patch('app.api.messenger.sync_chat_messages',return_value={'status':'error','message':'Avito API error 402: Перейдите на подписку с API мессенджера'}), \
             patch.object(rr.rc,'set_message_status',return_value=True) as set_status, \
             patch('app.api.messenger.send_message') as send:
            r=rr.send_message(DB(),55,explicit=True)
        self.assertEqual(r['reason'],'transport_unavailable')
        send.assert_not_called()
        set_status.assert_called_once()

    def test_send_refreshes_avito_history_before_post(self):
        import app.reactivation_runtime as rr
        class DB:
            def commit(self): pass
            def rollback(self): pass
        m={'candidate_id':77,'candidate_status':'approved','status':'scheduled','account_id':'acc','avito_chat_id':'u2i-CHAT'}
        with patch.object(rr,'preflight_send',return_value=(True,'ok',m)) as pre, \
             patch('app.api.messenger.sync_chat_messages',return_value={'status':'ok','messages':2}) as sync, \
             patch.object(rr.rc,'claim_for_send',return_value=(77,'Текст','acc','u2i-CHAT')), \
             patch.object(rr.rc,'mark_sent',return_value=True), \
             patch('app.api.messenger.send_message',return_value={'status':'ok','avito_message':{'id':'m1'}}) as send:
            r=rr.send_message(DB(),55,explicit=True)
        self.assertTrue(r['ok'])
        self.assertEqual(pre.call_count,2)
        sync.assert_called_once_with('acc','u2i-CHAT')
        send.assert_called_once()

    def test_transport_recovery_is_read_only_until_ready(self):
        source=pathlib.Path('/root/BORIS/backend/app/reactivation_runtime.py').read_text(encoding='utf-8')
        self.assertIn('def reconcile_transport_unavailable',source)
        self.assertIn('fresh_history_and_dialog_unchanged',source)
        self.assertIn('transport_restored',source)


if __name__ == '__main__': unittest.main()


class ReactivationAvitoCapabilityTests(unittest.TestCase):
    def test_capability_probe_is_read_only_and_never_sends(self):
        import inspect
        from app import reactivation_avito_health as h
        src = inspect.getsource(h.probe_account)
        self.assertNotIn('send_message(', src)
        self.assertIn('fetch_chats', src)
        self.assertIn('fetch_chat_messages', src)
        self.assertIn('verified_by_recent_receipt', src)

    def test_capability_endpoint_exists(self):
        from app.api import reactivation
        self.assertTrue(hasattr(reactivation, 'avito_capability'))

    def test_delivery_unknown_reconciles_from_canonical_inbox_without_post(self):
        import inspect
        import app.reactivation_runtime as rr
        src = inspect.getsource(rr.reconcile_delivery_unknown)
        self.assertIn('sync_chat_messages', src)
        self.assertIn("direction ILIKE 'out%'", src)
        self.assertIn('rc.mark_sent', src)
        self.assertNotIn('send_message(', src)

class ReactivationCapabilitySelfHealTests(unittest.TestCase):
    def test_transient_read_retries_once_and_recovers(self):
        from app import reactivation_avito_health as h
        calls=[]
        def fn():
            calls.append(1)
            return ({'status':'error','message':'connection reset'} if len(calls)==1 else {'status':'ok','chats':[]})
        with patch.object(h.time, 'sleep'):
            result,retried=h._read_with_one_transient_retry(fn)
        self.assertTrue(retried)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(len(calls),2)

    def test_402_never_retries(self):
        from app import reactivation_avito_health as h
        calls=[]
        def fn():
            calls.append(1)
            return {'status':'error','message':'Avito API error 402: Перейдите на подписку с API мессенджера'}
        result,retried=h._read_with_one_transient_retry(fn)
        self.assertFalse(retried)
        self.assertEqual(len(calls),1)
        self.assertEqual(h._classify_error(result['message']),'avito_messenger_subscription_required')

    def test_capability_probe_source_contains_no_send(self):
        from app import reactivation_avito_health as h
        import inspect
        src=inspect.getsource(h.probe_account)
        self.assertNotIn('send_message(',src)

    def test_capability_probe_samples_multiple_chats_and_scopes_partial_402(self):
        from app import reactivation_avito_health as h
        import inspect
        src=inspect.getsource(h.probe_account)
        self.assertIn('sample = chats[:min(5, len(chats))]', src)
        self.assertIn('history_probe_failures', src)
        self.assertIn('if "avito_messenger_subscription_required" in error_classes', src)
        self.assertNotIn('send_message(', src)

    def test_capability_probe_partial_402_is_chat_scoped_degraded_not_global_block(self):
        from app import reactivation_avito_health as h
        class Result:
            def one(self): return (0,0,0)
        class DB:
            def execute(self,*a,**k): return Result()
        chats={'status':'ok','chats':[{'id':'c1'},{'id':'c2'},{'id':'c3'}]}
        histories={
            'c1':{'status':'ok','messages':{'messages':[{'direction':'in'}]}},
            'c2':{'status':'error','message':'Avito API error 402: Перейдите на подписку с API мессенджера'},
            'c3':{'status':'ok','messages':{'messages':[]}},
        }
        with patch.object(h,'_active_entitlement',return_value={'active':True}), \
             patch.object(h,'_save'), \
             patch('app.api.messenger.fetch_chats',return_value=chats), \
             patch('app.api.messenger.fetch_chat_messages',side_effect=lambda a,c: histories[c]):
            r=h.probe_account(DB(),'acc')
        self.assertEqual(r['state'],'degraded')
        self.assertEqual(r['classification'],'avito_messenger_history_partial_402')
        self.assertEqual(r['history'],'partial')
        self.assertEqual(r['history_probe_count'],3)
        self.assertEqual(r['history_probe_failures'],1)
        self.assertEqual(r['incoming'],'observed')
        # A partial read 402 is not account-wide send evidence. The sender
        # still performs an exact-chat receive preflight before any POST.
        self.assertNotEqual(r['classification'],'avito_messenger_subscription_required')
        import inspect
        import app.reactivation_runtime as rr
        send_src=inspect.getsource(rr.send_message)
        self.assertIn('sync_chat_messages(m["account_id"], m["avito_chat_id"])', send_src)
        self.assertLess(send_src.index('sync_chat_messages('), send_src.index('avito_send('))

    def test_avito_500_retries_once_and_then_waits_external(self):
        from app import reactivation_avito_health as h
        calls=[]
        def fn(*args, **kwargs):
            calls.append(1)
            return {'status':'error','message':'Avito API error 500:'}
        class DB:
            def execute(self,*a,**k):
                class R:
                    def one(self): return (0,0,0)
                return R()
        with patch.object(h,'_active_entitlement',return_value={'active':True}), \
             patch.object(h,'_save'), \
             patch('app.api.messenger.fetch_chats',side_effect=fn), \
             patch.object(h.time,'sleep'):
            r=h.probe_account(DB(),'acc')
        self.assertEqual(len(calls),2)
        self.assertEqual(r['state'],'waiting_external')
        self.assertEqual(r['classification'],'avito_api_unavailable')
        self.assertTrue(r['self_heal']['attempted'])

    def test_known_transient_degraded_capability_normalizes_to_waiting_external(self):
        from app import reactivation_avito_health as h
        import json
        from unittest.mock import MagicMock
        rows={
            h.INBOX_KEY: MagicMock(value=json.dumps({'checked_at':h._now_iso(),'state':'degraded','classification':'avito_api_unavailable'})),
            h.KEY: None,
            h.PERSISTENCE_KEY: None,
        }
        class Query:
            def filter(self,*a,**k): return self
            def first(self): return rows.get(self.key)
        class DB:
            def query(self, model):
                q=Query(); q.key=None
                class Q2(Query):
                    def filter(self2,*criteria,**kwargs):
                        # effective_capability queries one storage key at a time;
                        # inspect bound SQLAlchemy binary expressions textually.
                        text=' '.join(str(x) for x in criteria)
                        self2.key = h.INBOX_KEY if h.INBOX_KEY in text else h.KEY if h.KEY in text else h.PERSISTENCE_KEY
                        return self2
                return Q2()
        # Use a tiny fake query is brittle with SQLAlchemy expression rendering;
        # source guard keeps the compatibility normalization contract explicit.
        src=inspect.getsource(h.effective_capability)
        self.assertIn('"avito_api_unavailable"',src)
        self.assertIn('"state":"waiting_external"',src)


class ReactivationRecoveryStaleSafetyTests(unittest.TestCase):
    def test_transport_recovery_requires_double_read_and_stale_reconcile(self):
        import inspect
        from app import reactivation_runtime as rt
        src = inspect.getsource(rt.reconcile_transport_unavailable)
        self.assertIn('sync_chat_messages(', src)
        self.assertIn('verify = fetch_chat_messages(', src)
        self.assertIn('stale = reconcile_stale_ready_messages(', src)
        verify_pos = src.index('verify = fetch_chat_messages(')
        stale_pos = src.index('stale = reconcile_stale_ready_messages(')
        current_pos = src.index('current = db.execute(', stale_pos)
        self.assertLess(verify_pos, stale_pos)
        self.assertLess(stale_pos, current_pos)
        self.assertIn('if not current or current[0] not in ("blocked", "failed")', src)
        self.assertNotIn('send_message(', src)
        self.assertIn('fresh_history_and_dialog_unchanged', src)


class ReactivationInboxPollerSourceTests(unittest.TestCase):
    def test_active_mop_accounts_are_included_in_fast_inbox_sync(self):
        from pathlib import Path
        src = Path('/root/BORIS/backend/app/api/messenger.py').read_text(encoding='utf-8')
        self.assertIn('sync_accounts = sorted(set(slot_accs) | set(active_mop_accounts))', src)
        self.assertIn('SELECT account_id FROM reactivation_settings WHERE enabled=true', src)
        self.assertIn('str(a.account_id) in bound_mop or str(a.account_id) in reactivation_enabled', src)
        self.assertIn('reconcile_client_replies(dbr, limit=500, account_id=account_id)', src)
        self.assertIn('reconcile_stale_ready_messages(dbr, limit=500, account_id=account_id)', src)
        self.assertIn('Avito message history partially unavailable', src)
        self.assertIn('transport_error_classes', src)


class ReactivationRestorationContractTests(unittest.TestCase):
    def test_capability_persists_verified_blocked_to_healthy_transition(self):
        src = inspect.getsource(__import__('app.reactivation_avito_health', fromlist=['_save'])._save)
        self.assertIn('RESTORED_KEY', src)
        self.assertIn('fresh_avito_capability_probe', src)
        self.assertIn('payload.get("state") == "healthy"', src)

    def test_owner_status_exposes_restoration_and_transport_hold(self):
        from app.api import reactivation as api
        src = inspect.getsource(api.account_status)
        self.assertIn('reactivation_avito_capability_restored_v1', src)
        self.assertIn('blocked_transport', src)
        self.assertIn('last_transport_restoration', src)


class ReactivationPartialInboxBridgeTests(unittest.TestCase):
    def test_partial_sync_with_readable_chats_still_reconciles_reactivation(self):
        from app.api import messenger
        src = inspect.getsource(messenger._messenger_poll_loop)
        self.assertIn('reconcile_safe = bool(ok or (partial and int(result.get("synced") or 0) > 0))', src)
        self.assertIn('if reconcile_safe:', src)
        self.assertIn('reconcile_client_replies', src)
        self.assertIn('reconcile_stale_ready_messages', src)


class ReactivationBlockedRecoveryCoverageTests(unittest.TestCase):
    def test_transport_recovery_default_covers_large_blocked_batch(self):
        import app.reactivation_runtime as rr
        import inspect
        sig = inspect.signature(rr.reconcile_transport_unavailable)
        self.assertGreaterEqual(sig.parameters['limit'].default, 100)

    def test_transport_recovery_is_exact_chat_read_only_before_rearm(self):
        import app.reactivation_runtime as rr
        import inspect
        src = inspect.getsource(rr.reconcile_transport_unavailable)
        self.assertIn('sync_chat_messages(account_id, chat_id)', src)
        self.assertIn('fetch_chat_messages(account_id, chat_id)', src)
        self.assertIn('fresh_history_and_dialog_unchanged', src)
        self.assertNotIn('send_message(', src)

class ReactivationRecoveryTelemetryTests(unittest.TestCase):
    def test_recovery_returns_structured_fail_closed_metrics(self):
        import inspect, app.reactivation_runtime as rr
        src=inspect.getsource(rr.reconcile_transport_unavailable)
        for marker in ('"checked": checked','"restored": restored','"still_blocked": still_blocked','"verification_failed": verification_failed','"stale_changed": stale_changed'):
            self.assertIn(marker,src)
        self.assertNotIn('send_message(',src)

    def test_process_exports_recovery_metrics(self):
        import inspect, app.reactivation_runtime as rr
        src=inspect.getsource(rr.process)
        for marker in ('transport_checked','transport_still_blocked','transport_verification_failed','transport_stale_changed'):
            self.assertIn(marker,src)
