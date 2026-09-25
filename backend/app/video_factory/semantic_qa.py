# -*- coding: utf-8 -*-
from __future__ import annotations


def _real_visuals(assets):
    return [a for a in assets if (a.get('analysis') or {}).get('kind') in ('video','image')]


def score(data,technical=None):
    plan=data.get('plan') or {}; scenes=plan.get('scenes') or []; assets=data.get('assets') or []
    mode=plan.get('mode') or 'materials'; real=_real_visuals(assets)
    hook=(plan.get('hook') or '').strip(); cta=(plan.get('cta') or '').strip(); captions=[(s.get('caption') or '').strip() for s in scenes]
    hook_score=100 if 8<=len(hook)<=90 else 75 if hook else 20
    cta_words=('напиш','закаж','получ','узна','запиш','открой','позвон','остав','подключ')
    cta_score=100 if cta and any(w in cta.lower() for w in cta_words) else 60 if cta else 10
    readability=100 if captions and max([len(c) for c in captions] or [0])<=110 else 65
    diversity=100 if captions and len(set(captions))==len(captions) else max(40,round(len(set(captions))/max(1,len(captions))*100))
    technical_score=100 if (technical or {}).get('pass') else 0
    visual_count=len(real)
    visual=100 if visual_count>=min(4,max(1,len(scenes))) else 82 if visual_count>=2 else 65 if visual_count==1 else 0
    offer=90 if len((plan.get('prompt') or '').strip())>=25 else 55
    blockers=[]
    if mode=='materials' and visual_count==0: blockers.append('Нужны реальные фото или видео клиента.')
    if mode=='from_scratch' and visual_count==0: blockers.append('Нужны сгенерированные или реальные визуальные кадры.')
    if mode=='from_scratch' and data.get('visual_qa') and not (data.get('visual_qa') or {}).get('pass'): blockers.append('Визуальные сцены не прошли проверку релевантности и качества.')
    if mode=='from_scratch' and (data.get('visual_qa') or {}).get('pass'): visual=int((data.get('visual_qa') or {}).get('score') or 0)
    if mode=='boris_tutorial':
        routes=[s.get('route') for s in scenes if s.get('route')]; narr=[s.get('narration') for s in scenes if (s.get('narration') or '').strip()]
        capture=any(a.get('kind')=='tutorial_capture' for a in assets)
        if not capture: blockers.append('Нужна реальная безопасная запись интерфейса BORIS.')
        visual=100 if capture and len(set(routes))>=4 else 55 if capture else 0
        diversity=100 if len(set(routes))>=4 else min(diversity,60)
        offer=100 if len(narr)>=max(3,len(scenes)-1) else 55
    overall=round(hook_score*.15+cta_score*.13+readability*.12+diversity*.10+visual*.28+technical_score*.17+offer*.05)
    recommendations=list(blockers)
    if hook_score<80: recommendations.append('Усилить первый экран и сделать hook короче.')
    if cta_score<80: recommendations.append('Добавить конкретный призыв к действию.')
    if diversity<75: recommendations.append('Убрать повторяющиеся смысловые титры между сценами.')
    if visual<80 and not blockers: recommendations.append('Добавить больше релевантных визуальных сцен.')
    passed=not blockers and overall>=82 and technical_score==100 and visual>=80
    return {'pass':passed,'overall':overall,'client_ready':passed,'blockers':blockers,'scores':{'hook':hook_score,'visual':visual,'readability':readability,'offer':offer,'cta':cta_score,'diversity':diversity,'technical':technical_score},'recommendations':recommendations}
