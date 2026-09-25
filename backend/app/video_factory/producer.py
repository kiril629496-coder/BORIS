# -*- coding: utf-8 -*-
from __future__ import annotations
import re

ROLE_ORDER=('hook','problem','solution','proof','benefit','offer','cta')


def _sentences(text):
    return [x.strip(' -–—') for x in re.split(r'[.!?\n]+',str(text or '')) if x.strip()]


def enrich_plan(plan):
    """Deterministic commercial storyboard. Never invents client facts."""
    if plan.get('mode')=='boris_tutorial':
        return plan
    scenes=list(plan.get('scenes') or [])
    if not scenes:return plan
    facts=_sentences(plan.get('prompt'))
    n=len(scenes)
    if n<=4: roles=['hook','solution','proof','cta']
    elif n==5: roles=['hook','solution','benefit','proof','cta']
    else:
        middle=['solution','benefit','proof','detail','offer']
        roles=['hook']+[middle[(i-1)%len(middle)] for i in range(1,n-1)]+['cta']
    fallback={
        'solution':'Покажите продукт или услугу крупно и понятно',
        'proof':'Покажите качество, процесс или результат',
        'benefit':'Покажите главное подтверждённое преимущество для клиента',
        'detail':'Покажите важную деталь продукта, услуги или процесса',
        'offer':'Соберите предложение в одном понятном кадре',
    }
    for i,sc in enumerate(scenes):
        role=roles[i]; sc['role']=role
        if role=='hook': caption=(plan.get('hook') or (facts[0] if facts else ''))[:90]
        elif role=='cta': caption=(plan.get('cta') or 'Напишите — расскажем подробнее')[:100]
        else:
            fact=facts[i] if i<len(facts) else (facts[-1] if facts else '')
            caption=(fact or fallback[role])[:100]
        sc['caption']=caption
        sc['narration']=caption
        sc['visual_brief']=(
            f"Роль сцены: {role}. Покажи визуально только то, что подтверждается описанием клиента: "
            f"{plan.get('prompt','')}. Смысл текущей сцены: {caption}. "
            "Один главный объект/действие, реалистичная коммерческая съёмка, естественная перспектива, "
            "правильные материалы и детали, без текста, логотипов, водяных знаков и выдуманных характеристик."
        )[:1800]
    plan['scenes']=scenes
    plan['producer']={'version':'commercial_v2','funnel':['hook','problem','solution','proof','offer','cta'],'fact_safe':True}
    return plan
