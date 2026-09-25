from datetime import datetime, timezone

from app.services.lead_radar.models import RawLead, SignalFamily
from app.services.lead_radar.scoring import QUALITY_VERSION, score


NOW=datetime(2026,9,8,12,tzinfo=timezone.utc)


def _lead(title, text):
    return RawLead(
        source='freelancer', external_id='x', url='https://example.invalid/x',
        title=title, text=text, published_at=NOW,
        source_family='brief', owner_user_id=2,
    )


def _score(title, text, service_ids=None):
    return score(
        _lead(title,text),
        service_ids or [5,17,26],
        SignalFamily.READY_BRIEF,
        now=NOW,
        budget=(1000,5000),
        profile={'mode':'development'},
    )


def test_quality_version_v16_or_newer():
    assert QUALITY_VERSION.startswith('demand_gate_v')
    assert int(QUALITY_VERSION.rsplit('v',1)[1])>=16


def test_rejects_digital_marketing_sales_specialist_even_with_crm_automation_words():
    s,reasons=_score(
        'GoHighLevel Demo & Digital Marketing Sales Specialist',
        'Need a sales specialist to demo CRM, automated follow-up, WhatsApp, AI and automation. '
        'This is not an implementation role.'
    )
    assert s<45
    assert any('другую профессию' in x for x in reasons)


def test_rejects_content_publishing_marketing_role_even_with_software_saas_words():
    s,reasons=_score(
        'Tech Content Publishing & Marketing Specialists',
        'Write and publish articles about AI, software, mobile, web, UI/UX and SaaS.'
    )
    assert s<45
    assert any('другую профессию' in x for x in reasons)


def test_rejects_vapt_testing_without_development_scope():
    s,reasons=_score(
        'VAPT Testing – Web Application & API',
        'Conduct vulnerability assessment and penetration testing for a web application and REST API. '
        'Deliver a VAPT report and remediation recommendations.'
    )
    assert s<45
    assert any('тестирование' in x for x in reasons)


def test_keeps_real_crm_automation_development_request():
    s,reasons=_score(
        'Build GoHighLevel CRM Automation',
        'Need a developer to build and implement CRM automation, webhooks and API integrations.'
    )
    assert s>=45
    assert not any('другую профессию' in x for x in reasons)


def test_keeps_real_web_api_build_request():
    s,reasons=_score(
        'Build Web Application & API',
        'Looking for a developer to build a web app, backend API and database.'
    )
    assert s>=45
    assert not any('тестирование' in x for x in reasons)
