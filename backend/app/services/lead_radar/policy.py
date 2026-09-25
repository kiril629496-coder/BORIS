from dataclasses import dataclass
from .models import ContactMode, SignalFamily, SourcePolicy

@dataclass(frozen=True)
class OutreachDecision:
    mode: ContactMode
    reason: str


def decide(raw, score: int, signal: SignalFamily, source_policy: SourcePolicy|None=None):
    p=source_policy or SourcePolicy(
        key=raw.source,family=raw.source_family or 'unknown',
        allows_business_contact=raw.source_allows_outreach,
        # Raw input can justify a human draft, but AUTO always requires an explicit SourcePolicy.
        allows_automation=False)
    # Вакансия — только сигнал бизнеса. Никогда не masquerade as applicant / автоотклик на вакансию.
    # Engine уже отбрасывает мусор ниже 45; квалифицированный hiring-сигнал 45+ должен
    # явно оставаться CRM_ONLY, а не терять семантику из-за общего outreach-порога 55.
    if signal==SignalFamily.HIRING or p.applicant_channel:
        if score < 45:
            return OutreachDecision(ContactMode.NONE,'скоринг ниже порога квалификации')
        return OutreachDecision(ContactMode.CRM_ONLY,'вакансия используется как B2B-сигнал; отклик соискателя запрещён')
    if score < 55: return OutreachDecision(ContactMode.NONE,'скоринг ниже порога')
    if not p.allows_business_contact:
        return OutreachDecision(ContactMode.CRM_ONLY,'источник не разрешает прямой бизнес-контакт')
    # холодный аудит/жалоба требует человечески проверяемого черновика по умолчанию
    if signal in (SignalFamily.BUSINESS_GAP,SignalFamily.PROBLEM,SignalFamily.MONEY,SignalFamily.LAUNCH,SignalFamily.PARTNER):
        return OutreachDecision(ContactMode.DRAFT,'персональный B2B-контакт: подготовить черновик, не слать массово автоматически')
    if p.allows_automation:
        return OutreachDecision(ContactMode.AUTO,'прямой запрос + источник явно разрешает автоматизированный ответ; внутреннего дневного лимита BORIS нет')
    return OutreachDecision(ContactMode.DRAFT,'контакт разрешён, автоматическая отправка не разрешена')
