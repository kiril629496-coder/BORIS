import json


def test_github_retries_one_transient_504(monkeypatch):
    from app.services.lead_radar.connectors import GitHubIssuesConnector
    calls=[]
    class Transient504(Exception):
        code=504
        def __str__(self): return 'HTTP Error 504: Gateway Timeout'
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,*a): return json.dumps({'items':[]}).encode()
    def open_(req,timeout=0):
        calls.append(timeout)
        if len(calls)==1:
            raise Transient504()
        return Resp()
    monkeypatch.setattr('urllib.request.urlopen',open_)
    monkeypatch.setattr('time.sleep',lambda *_:None)
    c=GitHubIssuesConnector(['developer wanted'],owner_user_id=21,timeout=15)
    assert c._get({'q':'developer wanted is:issue','per_page':1,'page':1})=={'items':[]}
    assert calls==[15,15]


def test_github_does_not_retry_non_transient_error(monkeypatch):
    import pytest
    from app.services.lead_radar.connectors import GitHubIssuesConnector
    calls=[]
    class Forbidden(Exception):
        code=403
        def __str__(self): return 'HTTP Error 403: Forbidden'
    def open_(req,timeout=0):
        calls.append(timeout)
        raise Forbidden()
    monkeypatch.setattr('urllib.request.urlopen',open_)
    monkeypatch.setattr('time.sleep',lambda *_:None)
    c=GitHubIssuesConnector(['developer wanted'],owner_user_id=21,timeout=15)
    with pytest.raises(Forbidden):
        c._get({'q':'developer wanted is:issue','per_page':1,'page':1})
    assert calls==[15]
