# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from .costs import append as cost_append
from .media import analyze_asset


def _scene_indexes(total,count):
    if total<=0:return [0]*count
    if count>=total:return list(range(total))[:count]
    if count==1:return [min(total-1,total//2)]
    return [round(i*(total-1)/(count-1)) for i in range(count)]


def generate_missing_stills(data,projects_root,outputs_root,count=1,scene_indexes=None,quality='low'):
    account_id=data.get('account_id')
    if not account_id: raise RuntimeError('account_id_required_for_paid_generation')
    count=max(1,min(int(count or 1),4)); plan=data.get('plan') or {}; scenes=plan.get('scenes') or []
    from app.db.session import SessionLocal
    from app.services.image_service import generate_banner_financially_guarded
    from app.usage import USD_TO_RUB
    outdir=Path(outputs_root)/data['id']/'generated'; outdir.mkdir(parents=True,exist_ok=True)
    db=SessionLocal(); made=[]
    try:
        indexes=[max(0,min(len(scenes)-1,int(x)-1)) for x in (scene_indexes or [])] if scenes else []
        indexes=indexes[:4] if indexes else _scene_indexes(len(scenes),count)
        for no,scene_index in enumerate(indexes,1):
            sc=scenes[scene_index] if scenes else {}
            facts=(plan.get('prompt') or '').strip(); caption=(sc.get('caption') or '').strip(); brief=(sc.get('visual_brief') or '').strip()
            nonvisual=('гарант','цена','₽','руб','телефон','позвон','напиш','город','санкт-петербург','москва','год','месяц','срок')
            if any(k in caption.lower() for k in nonvisual):
                brief=("Покажи сильный визуальный кадр основного продукта или процесса услуги из описания клиента. "
                       "Текущий титр содержит невизуальный факт и будет добавлен монтажом отдельно — НЕ пытайся изображать гарантию, цену, цифры, город, телефон, буквы или надписи внутри картинки. "
                       f"Факты клиента: {facts}")
            fmt=plan.get('format') or 'shorts'; composition='вертикальная композиция 9:16' if fmt=='shorts' else ('горизонтальная композиция 16:9' if fmt=='youtube' else 'центрированная рекламная композиция')
            prompt=(
                "Фотореалистичный коммерческий кадр для рекламного видео. "
                f"{composition}. {brief or ('Смысл сцены: '+caption+'. Факты: '+facts)} "
                "Кадр должен выглядеть как дорогая реальная рекламная съёмка, а не иллюстрация или 3D-рендер. "
                "Естественный свет, реалистичная фактура материалов, корректная геометрия и руки/предметы. "
                "Оставь чистую безопасную область под субтитры. Никаких надписей, букв, логотипов, ценников и водяных знаков."
            )[:3500]
            path=outdir/f'ai_scene_{scene_index+1:02d}_{no:02d}.png'
            quality='medium' if quality=='medium' else 'low'
            result=generate_banner_financially_guarded(db=db,account_id=account_id,prompt=prompt,save_path=str(path),model='gpt-image-1-mini',quality=quality,size='1024x1024',operation='BORIS Video Factory AI still')
            if not result.get('ok'): raise RuntimeError(result.get('error') or 'ai_image_failed')
            cost_rub=float(result.get('cost_rub') or 0); cost_usd=cost_rub/float(USD_TO_RUB or 90)
            cost_append(projects_root,data['id'],'openai','gpt-image-1-mini','image_generation',1,'image',cost_usd,{'cost_rub':cost_rub,'request_id':result.get('request_id'),'scene_index':scene_index+1})
            asset=analyze_asset({'name':path.name,'original_name':path.name,'kind':'generated_ai','size':path.stat().st_size,'path':str(path)})
            asset['scene_index']=scene_index+1; asset['scene_role']=sc.get('role'); asset['generated_for']=caption; asset['generation_quality']=quality; asset['generation_model']='gpt-image-1-mini'
            made.append(asset)
    finally:
        db.close()
    return made
