import unittest
from datetime import datetime, timezone, timedelta
from app.services.control_plane_adapters import _reactivation_diagnose


def state(*, external_402=0, checked_at=None, unknown=1, unresolved=1):
    return {
        'settings': {'enabled': True, 'mode': 'recommend'},
        'mop_active': True,
        'avito_capability': {'state': 'healthy', 'classification': 'flow_ok'},
        'scheduler_age_sec': 10,
        'scheduler_state': 'ok',
        'capability_degraded_accounts': 0,
        'capability_waiting_external_accounts': 0,
        'capability_partial_external_accounts': 0,
        'delivery_unknown_messages': unknown,
        'delivery_unknown_oldest_age_sec': 999999,
        'scheduler_details': {
            'delivery_unresolved': unresolved,
            'delivery_unknown_external_402': external_402,
            'delivery_unknown_evidence_checked_at': checked_at,
        },
        'ready_messages': 0,
        'stale_candidates_45d': 0,
    }

class ReactivationAmbiguousControlPlaneTests(unittest.TestCase):
    def test_fresh_exact_chat_402_is_waiting_external_without_recovery_loop(self):
        a=state(external_402=1, checked_at=datetime.now(timezone.utc).isoformat())
        d=_reactivation_diagnose(None, 'otdushi', None, a)
        self.assertEqual(d['classification'], 'REACTIVATION_DELIVERY_AMBIGUOUS_EXTERNAL')
        self.assertEqual(d['health'], 'waiting_external')
        self.assertIsNone(d['safe_action'])

    def test_old_age_without_exact_402_proof_remains_degraded(self):
        a=state(external_402=0, checked_at=datetime.now(timezone.utc).isoformat())
        d=_reactivation_diagnose(None, 'otdushi', None, a)
        self.assertEqual(d['classification'], 'REACTIVATION_DELIVERY_UNKNOWN')
        self.assertEqual(d['safe_action'], 'recover_reactivation_transport')

    def test_stale_402_evidence_cannot_mask_internal_ambiguity(self):
        stale=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        a=state(external_402=1, checked_at=stale)
        d=_reactivation_diagnose(None, 'otdushi', None, a)
        self.assertEqual(d['classification'], 'REACTIVATION_DELIVERY_UNKNOWN')
        self.assertEqual(d['health'], 'degraded')

    def test_partial_402_does_not_claim_all_ambiguities_external(self):
        a=state(external_402=1, unknown=2, unresolved=2, checked_at=datetime.now(timezone.utc).isoformat())
        d=_reactivation_diagnose(None, 'otdushi', None, a)
        self.assertEqual(d['classification'], 'REACTIVATION_DELIVERY_UNKNOWN')
        self.assertEqual(d['safe_action'], 'recover_reactivation_transport')

if __name__ == '__main__':
    unittest.main()

class ReactivationExternal402OwnerSemanticsTests(unittest.TestCase):
    def test_subscription_402_is_auto_reprobe_not_owner_operation(self):
        a=state(external_402=0, checked_at=None, unknown=0, unresolved=0)
        a['avito_capability']={'state':'waiting_external','classification':'avito_messenger_subscription_required'}
        d=_reactivation_diagnose(None,'acct',None,a)
        self.assertEqual(d['health'],'waiting_external')
        self.assertFalse(d['owner_action_required'])
        self.assertTrue(d['auto_reprobe'])

    def test_partial_402_keeps_service_and_is_auto_reprobe(self):
        a=state(external_402=0, checked_at=None, unknown=0, unresolved=0)
        a['avito_capability']={'state':'degraded','classification':'avito_messenger_history_partial_402'}
        d=_reactivation_diagnose(None,'acct',None,a)
        self.assertEqual(d['dependency_state'],'partial_external')
        self.assertTrue(d['service_continues'])
        self.assertFalse(d['owner_action_required'])
        self.assertTrue(d['auto_reprobe'])
