from datetime import datetime, timezone

from app.services.lead_radar.classifier import classify
from app.services.lead_radar.connectors import TEDTenderConnector
from app.services.lead_radar.models import RawLead, SignalFamily
from app.services.lead_radar.scoring import QUALITY_VERSION, score


NOW=datetime(2026,9,8,21,tzinfo=timezone.utc)


def _raw(title, text, family='brief'):
    return RawLead(
        source='freelancer', external_id='x', url='https://example.invalid/x',
        title=title, text=text, published_at=NOW,
        source_family=family, owner_user_id=2,
    )


def test_quality_version_v17():
    assert QUALITY_VERSION=='demand_gate_v17'


def test_legacy_ted_search_marker_is_not_service_evidence():
    text=(
        'Finland – Surveillance and security systems and devices '
        'Заказчик: Example CPV: 35120000 '
        'Поисковый сигнал: software development'
    )
    assert classify(text)==[]


def test_real_software_development_title_still_classifies():
    ids=classify('Software development services for a new web platform')
    assert 5 in ids


def test_marketing_manager_role_is_rejected_even_with_automation_terms():
    raw=_raw(
        'Event Marketing Manager',
        'Run email automation, CRM, SMS campaigns and build a contact database.'
    )
    s,reasons=score(
        raw,[15],SignalFamily.READY_BRIEF,now=NOW,budget=(1000,5000),
        profile={'mode':'development'}
    )
    assert s<45
    assert any('другую профессию' in r for r in reasons)


def test_ted_connector_keeps_search_term_only_in_metadata(monkeypatch):
    c=TEDTenderConnector(owner_user_id=2,terms=['software development'])
    monkeypatch.setattr(c,'_post',lambda body:{
        'notices':[{
            'publication-number':'123-2026',
            'publication-date':'2026-09-08T20:00:00Z',
            'notice-title':'Surveillance and security systems and devices',
            'buyer-name':'Example Buyer',
            'buyer-country':'FIN',
            'classification-cpv':'35120000',
        }]
    })
    rows=c.fetch(datetime(2026,9,8,tzinfo=timezone.utc))
    assert len(rows)==1
    assert 'Поисковый сигнал' not in rows[0].text
    assert 'software development' not in rows[0].text.lower()
    assert rows[0].metadata['search_term']=='software development'



def test_rejects_software_license_procurement_even_with_salesforce_keyword():
    raw=_raw(
        'Software package and information systems',
        'Acuerdo marco para el suministro de licencias de software y servicios de fabricante Salesforce.',
        family='tender',
    )
    s,reasons=score(
        raw,[15],SignalFamily.READY_BRIEF,now=NOW,budget=(1000,5000),
        profile={'mode':'development'}
    )
    assert s<45
    assert any('закупка лицензий' in r for r in reasons)


def test_keeps_real_salesforce_integration_development():
    raw=_raw(
        'Salesforce CRM Integration Development',
        'Need a developer to build and implement Salesforce API integration with our backend.',
        family='tender',
    )
    s,reasons=score(
        raw,[15,17],SignalFamily.READY_BRIEF,now=NOW,budget=(1000,5000),
        profile={'mode':'development'}
    )
    assert s>=45
    assert not any('закупка лицензий' in r for r in reasons)
