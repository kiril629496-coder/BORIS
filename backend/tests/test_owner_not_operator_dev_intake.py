from app.ext_api import devflow
from app.ext_api.router import DevelopmentTaskIn
from app.ext_api.schema import SCHEMA_YAML


def test_development_task_accepts_plain_owner_task_without_manual_criteria():
    body = DevelopmentTaskIn(task='Мария: Авитолог не выполняет план лидов; бюджет не превышать')
    assert body.task
    assert body.acceptance_criteria is None


def test_boris_supplies_nonnegotiable_engineering_acceptance():
    criteria = devflow._normalize_acceptance(None)
    joined = '\n'.join(criteria).lower()
    assert len(criteria) >= 6
    assert 'root cause' in joined
    assert 'regression test' in joined
    assert 'previously working behavior' in joined
    assert 'runtime/e2e' in joined
    assert 'source of truth' in joined


def test_owner_criteria_are_preserved_and_system_gates_are_appended():
    criteria = devflow._normalize_acceptance(['Нельзя превышать дневной бюджет'])
    assert criteria[0] == 'Нельзя превышать дневной бюджет'
    assert len(criteria) == len(devflow.SYSTEM_ACCEPTANCE) + 1


def test_public_gateway_schema_requires_task_but_not_acceptance_criteria():
    marker = 'operationId: runDevelopmentTask'
    block = SCHEMA_YAML[SCHEMA_YAML.index(marker):SCHEMA_YAML.index('/dev/board:')]
    assert 'required: [task]' in block
    assert 'required: [task, acceptance_criteria]' not in block
