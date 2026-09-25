from __future__ import annotations
from .base import TelephonyProviderAdapter
from .zadarma import ZadarmaAdapter
from .mango import MangoAdapter
from .novofon import NovofonAdapter
from .uis import UISAdapter
from .telphin import TelphinAdapter
from .mcn import MCNAdapter

class PendingAdapter(TelephonyProviderAdapter):
    def __init__(self, key: str, capabilities: set[str]):
        self.key = key
        self.capabilities = capabilities

_ADAPTERS: dict[str, TelephonyProviderAdapter] = {
    'zadarma': ZadarmaAdapter(),
    'novofon': NovofonAdapter(),
    'uis': UISAdapter(),
    'mango': MangoAdapter(),
    'telphin': TelphinAdapter(),
    'mcn': MCNAdapter(),
    'sip': PendingAdapter('sip', {'sip'}),
}

def get_adapter(provider: str | None) -> TelephonyProviderAdapter | None:
    return _ADAPTERS.get(str(provider or '').strip().lower())

def register_adapter(adapter: TelephonyProviderAdapter) -> None:
    _ADAPTERS[adapter.key] = adapter

def adapter_status(provider: str | None) -> dict:
    a=get_adapter(provider)
    if not a:
        return {'available':False,'implemented':False,'capabilities':[]}
    implemented=a.__class__.__name__ != 'PendingAdapter'
    return {'available':True,'implemented':implemented,'capabilities':sorted(a.capabilities)}
