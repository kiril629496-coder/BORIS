from app.ext_api import deploy

def test_dispatcher_restarts_for_recover_runtime_change():
    units=deploy._reviewed_units(['backend/app/ext_api/recover.py'])
    assert 'boris-dispatcher' in units

def test_dispatcher_restarts_for_a2a_runtime_change():
    units=deploy._reviewed_units(['backend/app/ext_api/a2a.py'])
    assert 'boris-dispatcher' in units

def test_dispatcher_not_restarted_for_unrelated_api_change():
    units=deploy._reviewed_units(['backend/app/api/report.py'])
    assert 'boris-dispatcher' not in units
