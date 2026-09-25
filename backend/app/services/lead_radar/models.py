from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Any

class SignalFamily(str, Enum):
    READY_BRIEF='ready_brief'       # биржи / тендеры / прямые ТЗ
    HIRING='hiring'                 # вакансии = сигнал проекта, НЕ отклик соискателя
    MONEY='money'                   # инвестиции / гранты / рост
    LAUNCH='launch'                 # новый продукт / SaaS / приложение
    PROBLEM='problem'               # жалоба / ручной процесс / явная боль
    BUSINESS_GAP='business_gap'     # аудит сайта/приложения/процесса
    PARTNER='partner'               # агентство / интегратор / overflow
    UNKNOWN='unknown'

class ContactMode(str, Enum):
    AUTO='auto'
    DRAFT='draft'
    CRM_ONLY='crm_only'
    NONE='none'

@dataclass(frozen=True)
class SourcePolicy:
    key: str
    family: str
    allows_business_contact: bool = False
    allows_automation: bool = False
    applicant_channel: bool = False
    min_interval_seconds: int = 60
    followup_max: int = 1
    enabled: bool = True

@dataclass
class RawLead:
    source: str
    external_id: str
    url: str
    title: str
    text: str
    published_at: datetime
    source_family: str = ''
    author: str = ''
    company_name: str = ''
    company_domain: str = ''
    location: str = ''
    contact_hint: str = ''
    owner_user_id: int = 0
    source_allows_outreach: bool = False
    source_allows_automation: bool = False
    metadata: dict[str,Any] = field(default_factory=dict)

@dataclass
class QualifiedLead:
    raw: RawLead
    service_ids: list[int]
    signal_family: SignalFamily
    score: int
    reasons: list[str] = field(default_factory=list)
    status: str = 'new'
    quality_version: str = ''
    fingerprint: str = ''
    dedupe_key: str = ''
    reply: Optional[str] = None
    contact_mode: ContactMode = ContactMode.NONE
    contact_reason: str = ''
    budget_min_rub: Optional[int] = None
    budget_max_rub: Optional[int] = None
    urgency: str = 'normal'
    decision_maker_hint: str = ''
    crm_deal_id: Optional[int] = None
    next_followup_at: Optional[datetime] = None

    def public_dict(self):
        d=asdict(self)
        d['signal_family']=self.signal_family.value
        d['contact_mode']=self.contact_mode.value
        return d
