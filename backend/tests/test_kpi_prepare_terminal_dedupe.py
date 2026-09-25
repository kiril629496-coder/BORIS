import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from app.api.avito import _kpi_prepare_has_active_duplicate

class KpiPrepareTerminalDedupeTests(unittest.TestCase):
    def _entry(self, when, status='all_proposals_submitted', item=None):
        item = item or {'id':8383910642, 'title':'Перегородка для душа', 'description':'Стеклянная перегородка'}
        return {'data': {'operation_id':'op1','action':'prepare_weak_titles','items':[item],
                         'signature':'prepare_weak_titles:8383910642','status':status,
                         'ai_completed_at':when.isoformat()}}
    def test_recent_terminal_same_signature_is_deduped(self):
        with patch('app.api.avito._kpi_prepare_queue_load', return_value=[self._entry(datetime.now()-timedelta(hours=1))]):
            got=_kpi_prepare_has_active_duplicate(object(),'a','prepare_weak_titles',[{'id':8383910642}])
        self.assertEqual(got['operation_id'],'op1')
    def test_old_terminal_same_input_remains_deduped(self):
        item={'id':8383910642, 'title':'Перегородка для душа', 'description':'Стеклянная перегородка'}
        with patch('app.api.avito._kpi_prepare_queue_load', return_value=[self._entry(datetime.now()-timedelta(hours=25), item=item)]):
            got=_kpi_prepare_has_active_duplicate(object(),'a','prepare_weak_titles',[item])
        self.assertEqual(got['operation_id'],'op1')

    def test_old_terminal_changed_input_can_run_again(self):
        old={'id':8383910642, 'title':'Перегородка для душа', 'description':'Стеклянная перегородка'}
        new={'id':8383910642, 'title':'Перегородка для душа без порога', 'description':'Стеклянная перегородка'}
        with patch('app.api.avito._kpi_prepare_queue_load', return_value=[self._entry(datetime.now()-timedelta(hours=25), item=old)]):
            got=_kpi_prepare_has_active_duplicate(object(),'a','prepare_weak_titles',[new])
        self.assertIsNone(got)
    def test_different_signature_not_blocked(self):
        with patch('app.api.avito._kpi_prepare_queue_load', return_value=[self._entry(datetime.now()-timedelta(hours=1))]):
            got=_kpi_prepare_has_active_duplicate(object(),'a','prepare_weak_titles',[{'id':999}])
        self.assertIsNone(got)

if __name__ == '__main__': unittest.main()
