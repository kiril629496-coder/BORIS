from datetime import datetime, timezone, timedelta

from app.services.lead_radar.connectors import GoogleNewsSearchConnector, RSSConnector
from app.services.lead_radar.engine import _classify_raw
from app.services.lead_radar.models import RawLead
from app.services.lead_radar.repository import _bounded_external_id


def raw(title='x', text='x', family='money', metadata=None):
    return RawLead(
        source='news',
        external_id='id',
        url='https://example.com/item',
        title=title,
        text=text,
        published_at=datetime.now(timezone.utc),
        source_family=family,
        owner_user_id=2,
        metadata=metadata or {},
    )


def test_external_id_is_bounded_and_stable():
    value='https://news.google.com/rss/articles/' + ('X' * 900)
    a=_bounded_external_id(value)
    b=_bounded_external_id(value)
    assert a == b
    assert len(a) <= 300
    assert '#sha256:' in a


def test_google_news_clean_preserves_letters():
    value='Singapore startup launches AI platform for corporate taxes'
    assert GoogleNewsSearchConnector._clean(value) == value


def test_news_evidence_requires_event_and_technology():
    c=GoogleNewsSearchConnector('news','news','money',['q'],owner_user_id=2)
    ok,ids=c._evidence_and_services('Acme AI platform raises $10M in seed funding')
    assert ok is True
    assert 26 in ids
    bad,ids2=c._evidence_and_services('CEO passes away at software company')
    assert bad is False
    assert ids2 == []


def test_google_failure_uses_bing_news_fallback(monkeypatch):
    calls=[]
    def fake_fetch(self, since):
        calls.append(self.url)
        if 'news.google.com' in self.url:
            raise RuntimeError('synthetic tls failure')
        assert 'bing.com/news/search' in self.url
        return [RawLead(
            source='news',
            external_id='provider-guid-' + ('Y'*500),
            url='https://example.com/funding',
            title='Acme raises $10M in seed funding for AI platform',
            text='Acme AI platform funding round',
            published_at=datetime.now(timezone.utc),
            source_family='money',
            owner_user_id=2,
        )]
    monkeypatch.setattr(RSSConnector,'fetch',fake_fetch)
    c=GoogleNewsSearchConnector('news','news','money',['AI startup funding'],owner_user_id=2)
    rows=c.fetch(datetime.now(timezone.utc)-timedelta(days=7))
    assert len(rows) == 1
    assert rows[0].metadata['provider'] == 'bing_rss_fallback'
    assert rows[0].metadata['primary_error']
    assert rows[0].metadata['connector'] == 'google_news_search_rss'
    assert 26 in rows[0].metadata['suggested_service_ids']
    assert len(rows[0].external_id) == 64
    assert any('news.google.com' in x for x in calls)
    assert any('bing.com/news/search' in x for x in calls)


def test_untrusted_metadata_cannot_force_service():
    r=raw(title='ordinary company update', text='nothing about development',
          metadata={'connector':'external_http','suggested_service_ids':[26]})
    assert _classify_raw(r) == []


def test_trusted_news_metadata_can_add_vetted_service():
    r=raw(title='Acme raises funding', text='AI platform',
          metadata={'connector':'google_news_search_rss','suggested_service_ids':[26]})
    assert 26 in _classify_raw(r)

def test_news_rejects_funding_offer_and_roundup():
    c=GoogleNewsSearchConnector('news','news','money',['q'],owner_user_id=2)
    ok1,_=c._evidence_and_services('Askya AI platform offers $200k funding to startups')
    ok2,_=c._evidence_and_services('Weekly startup funding roundup: AI startups')
    assert ok1 is False
    assert ok2 is False


def test_company_inference_strips_hr_software_startup_prefix():
    assert GoogleNewsSearchConnector._infer_company(
        'HR software startup HiBob raises $166M after revenue tops $400M - SiliconANGLE'
    ) == 'HiBob'

def test_news_id_ignores_provider_wrapper_url(monkeypatch):
    urls=iter([
        'http://www.bing.com/news/apiclick.aspx?tid=AAA&url=https://example.com/article',
        'http://www.bing.com/news/apiclick.aspx?tid=BBB&url=https://example.com/article',
    ])
    published=datetime(2026,9,3,12,0,tzinfo=timezone.utc)
    def fake_fetch(self,since):
        return [RawLead(
            source='news',external_id='provider',url=next(urls),
            title='Acme raises $10M in funding for AI platform',
            text='Acme raises funding for AI platform',
            published_at=published,source_family='money',owner_user_id=2,
        )]
    monkeypatch.setattr(RSSConnector,'fetch',fake_fetch)
    c=GoogleNewsSearchConnector('news','news','money',['q'],owner_user_id=2)
    first=c.fetch(published-timedelta(days=1))[0].external_id
    second=c.fetch(published-timedelta(days=1))[0].external_id
    assert first == second
