import lead_radar_runner as runner


class _Repo:
    def __init__(self, engine):
        self.engine=engine
    def install(self):
        pass
    def require_catalog(self, owner):
        return ({'owner_user_id':owner,'name':f'client-{owner}','enabled':True},
                [{'offering_code':1,'name':'offer','keywords':['offer']}])


class _Connector:
    def __init__(self, owner):
        self.owner=owner
        self.key=f'source-{owner}'


def test_runner_executes_all_enabled_tenants(monkeypatch):
    monkeypatch.setattr(runner,'SQLLeadRepository',_Repo)
    monkeypatch.setattr(runner,'_enabled_owners',lambda repo:[10,20])
    monkeypatch.setattr(runner,'build_connectors',
                        lambda owner,profile=None,catalog=None:[_Connector(owner)])

    seen=[]
    def cycle(engine,connectors):
        owner=connectors[0].owner
        seen.append(owner)
        return {'processed':owner//10,'duplicates':0,'health':{'status':'PASS'}}
    monkeypatch.setattr(runner,'run_cycle',cycle)

    out=runner.run_all(object())
    assert seen==[10,20]
    assert out['status']=='PASS'
    assert out['successful_tenants']==2
    assert out['failed_tenants']==0
    assert out['processed']==3


def test_runner_continues_after_one_tenant_failure(monkeypatch):
    monkeypatch.setattr(runner,'SQLLeadRepository',_Repo)
    monkeypatch.setattr(runner,'_enabled_owners',lambda repo:[10,20])
    monkeypatch.setattr(runner,'build_connectors',
                        lambda owner,profile=None,catalog=None:[_Connector(owner)])

    seen=[]
    def cycle(engine,connectors):
        owner=connectors[0].owner
        seen.append(owner)
        if owner==10:
            raise RuntimeError('client-10-source-failed')
        return {'processed':4,'duplicates':1,'health':{'status':'PASS'}}
    monkeypatch.setattr(runner,'run_cycle',cycle)

    out=runner.run_all(object())
    assert seen==[10,20]
    assert out['status']=='DEGRADED'
    assert out['successful_tenants']==1
    assert out['failed_tenants']==1
    assert out['processed']==4
    assert out['tenants'][0]['status']=='ERROR'
    assert out['tenants'][1]['status']=='PASS'


def test_runner_external_wait_is_completed_cycle_not_systemd_failure(monkeypatch):
    monkeypatch.setattr(runner,'SQLLeadRepository',_Repo)
    monkeypatch.setattr(runner,'_enabled_owners',lambda repo:[10])
    monkeypatch.setattr(runner,'build_connectors',
                        lambda owner,profile=None,catalog=None:[_Connector(owner)])
    monkeypatch.setattr(runner,'run_cycle',lambda engine,connectors:{
        'processed':0,
        'duplicates':0,
        'errors':[],
        'health':{
            'status':'BLOCKED',
            'sources_blocked_external':1,
            'scheduler_attention_required':False,
            'self_heal_active':False,
            'owner_action_required':True,
        },
    })

    out=runner.run_all(object())
    assert out['status']=='PASS'
    assert out['successful_tenants']==1
    assert out['failed_tenants']==0
    assert out['tenants'][0]['status']=='BLOCKED'
    assert out['tenants'][0]['dependency_state']=='waiting_external_or_owner'


def test_runner_internal_block_stays_fail_closed(monkeypatch):
    monkeypatch.setattr(runner,'SQLLeadRepository',_Repo)
    monkeypatch.setattr(runner,'_enabled_owners',lambda repo:[10])
    monkeypatch.setattr(runner,'build_connectors',
                        lambda owner,profile=None,catalog=None:[_Connector(owner)])
    monkeypatch.setattr(runner,'run_cycle',lambda engine,connectors:{
        'processed':0,
        'duplicates':0,
        'errors':[],
        'health':{
            'status':'BLOCKED',
            'sources_blocked_external':0,
            'scheduler_attention_required':True,
            'self_heal_active':False,
            'owner_action_required':False,
        },
    })

    out=runner.run_all(object())
    assert out['status']=='BLOCKED'
    assert out['successful_tenants']==0
    assert out['failed_tenants']==1
