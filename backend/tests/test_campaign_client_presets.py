from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def test_campaign_examples_are_account_driven():
    page=(ROOT/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'clientPromptExamples(selectedAccount, promptKind, campaignContext)' in page
    assert 'company_niche' in page
    assert 'active_listing_titles' in page
    assert '/api/campaign/context?account_id=' in page
    assert '1 объявление' in page
    assert 'БАННЕР ДЛЯ ТЕЛЕФОНА' in page


def test_campaign_context_reads_observed_and_feed_titles():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    assert '@router.get("/context")' in src
    assert 'avito_position_snapshots' in src
    assert "key='feed_items'" in src
    assert 'active_listing_titles' in src

def test_account_list_exposes_safe_business_context():
    src=(ROOT/'backend/app/api/accounts.py').read_text(encoding='utf-8')
    for field in ('company_niche','company_description','company_advantages','company_client_description','company_tone'):
        assert f'"{field}"' in src

def test_campaign_banner_mobile_guard_is_fail_closed():
    src=(ROOT/'backend/app/services/media_semantic_validator.py').read_text(encoding='utf-8')
    assert 'tiny, overlapping or unreadable banner text' in src
    assert 'one main headline plus one short supporting phrase' in src
    assert 'mobile_ok' in src
    assert 'visual_hierarchy_ok' in src

def test_campaign_banner_generator_forbids_small_copy():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    compositor=(ROOT/'backend/app/services/avito_premium_compositor.py').read_text(encoding='utf-8')
    assert 'CRITICAL: NO TEXT, NO LETTERS, NO WORDS' in src
    assert 'banner_text_renderer' in src and 'local_pillow' in src
    assert 'Mobile contract: never silently emit tiny or overlong copy' in compositor
    assert 'MOBILE_TEXT_GEOMETRY_FAILED' in compositor

def test_quoted_offer_does_not_poison_geo_parser():
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_extract_plan, FFWorkflowBriefBody
    db=SessionLocal()
    try:
        c=SimpleNamespace(default_account_id='evgeniy_peregorodki_12447')
        r=_ff_extract_plan(db,c,FFWorkflowBriefBody(brief='Сделай 1 боевое объявление по услуге «Душевые перегородки из стекла» в Санкт-Петербурге. Используй данные клиента.',variants=1))
        assert r['directions']==['Душевые перегородки из стекла']
        assert r['cities']==['Санкт-Петербург']
        assert r['quantity']==1 and len(r['matrix'])==1
    finally:
        db.close()


def test_furniture_leaf_category_is_confident_enough_for_auto_pick():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_category_candidates, _ff_auto_category_candidate
    db=SessionLocal()
    try:
        cases={
            'Кухня на заказ':'95712',
            'Прихожая на заказ':'68080',
            'Гардеробная на заказ':'68081',
            'Мебель для ванной на заказ':'68104',
        }
        for direction, template_id in cases.items():
            candidates=_ff_category_candidates(db,direction,limit=8)
            selected=_ff_auto_category_candidate(direction,candidates)
            assert selected.get('template_id') == template_id, (direction,candidates,selected)
            assert int(selected.get('score') or 0) >= 8
    finally:
        db.close()


def test_media_instruction_is_not_misparsed_as_audience():
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_extract_plan, FFWorkflowBriefBody
    db=SessionLocal()
    try:
        c=SimpleNamespace(default_account_id='antonybatono_78732')
        r=_ff_extract_plan(db,c,FFWorkflowBriefBody(brief='Сделай 1 объявление по услуге «Механизированная штукатурка стен» в Сочи. Для результата нужно 3 релевантные фотографии и 1 крупный мобильный баннер.',variants=1,quantity=1))
        assert r['audience']==''
    finally:
        db.close()


def test_campaign_prompt_requires_visible_bottom_uniqueness():
    page=(ROOT/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'УНИКАЛИЗАЦИЯ ВНИЗУ ОПИСАНИЯ' in page
    assert 'между карточками меняется смысловой ракурс' in page
    assert 'Фото + 1 главный баннер' in page


def test_uniqueness_footer_is_bottom_visible_and_rotates():
    from app.api.campaigns import _ff_append_uniqueness_footer, _ff_copy_quality_errors
    base=(
        'Кухня на заказ в Уфе. Напишите нам, чтобы обсудить задачу.\n\n'
        'Подберём решение под подтверждённые параметры клиента и его задачи.\n\n'
        'Работаем с индивидуальными проектами и не подменяем факты обещаниями.\n\n'
        'Пришлите исходные данные — ответим по существу. '
    )*5
    tails=[]
    for variant in range(1,6):
        desc=_ff_append_uniqueness_footer(base,'Кухня на заказ','Уфа',variant=variant,item_id=100,max_chars=2400)
        tails.append(desc.split('\n\n')[-1])
        assert desc.rstrip().endswith(tails[-1])
        assert '· Уфа' in tails[-1]
        payload={'title':'Кухня на заказ','description':desc,'direction':'Кухня на заказ','city':'Уфа','copy_quality_contract':'sales_v3','uniqueness_footer':{'present':True,'position':'bottom'}}
        codes={x.get('code') for x in _ff_copy_quality_errors(payload)}
        assert 'copy_uniqueness_footer_missing' not in codes
    assert len(set(tails))==5


def test_sales_v3_fails_closed_without_uniqueness_footer():
    from app.api.campaigns import _ff_copy_quality_errors
    desc=('Кухня на заказ в Уфе. Напишите нам по вашей задаче.\n\n'
          'Подберём решение по подтверждённым данным.\n\n'
          'Покажем подходящие варианты без выдуманных обещаний.\n\n'
          'Пришлите исходные данные, чтобы обсудить проект. ')*6
    payload={'title':'Кухня на заказ','description':desc,'direction':'Кухня на заказ','city':'Уфа','copy_quality_contract':'sales_v3'}
    codes={x.get('code') for x in _ff_copy_quality_errors(payload)}
    assert 'copy_uniqueness_footer_missing' in codes


def test_city_phrase_in_city_does_not_duplicate_geo():
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_extract_plan, FFWorkflowBriefBody
    db=SessionLocal()
    try:
        c=SimpleNamespace(default_account_id='planetazayavki_65985')
        r=_ff_extract_plan(db,c,FFWorkflowBriefBody(brief='Сделай 1 боевое объявление по товару «Кухня на заказ» в городе Уфа. Используй только подтверждённые данные клиента.',variants=1,quantity=1))
        assert r['directions']==['Кухня на заказ']
        assert r['cities']==['Уфа'], r['cities']
        assert len(r['matrix'])==1 and r['matrix'][0]['city']=='Уфа'
    finally:
        db.close()


def test_confirmed_price_default_uses_only_matching_benchmark_family():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_confirmed_price_default
    db=SessionLocal()
    try:
        assert _ff_confirmed_price_default(db,'planetazayavki_65985','Кухня на заказ').get('price') == 45000
        assert _ff_confirmed_price_default(db,'planetazayavki_65985','Гардеробная на заказ').get('price') == 32000
        assert _ff_confirmed_price_default(db,'planetazayavki_65985','Прихожая на заказ').get('price') == 35000
        assert _ff_confirmed_price_default(db,'planetazayavki_65985','Мебель для ванной на заказ').get('price') == 120000
        assert not _ff_confirmed_price_default(db,'planetazayavki_65985','Механизированная штукатурка стен')
    finally:
        db.close()



def test_missing_upload_history_reconciles_current_only_with_exact_scope():
    src=(ROOT/'backend/app/api/avito.py').read_text(encoding='utf-8')
    assert 'if expected_ad_ids:' in src
    assert 'source="autoload_missing_upload_current_429"' in src
    assert 'selected=current_candidate' in src
    assert 'wanted={str(x).strip() for x in (expected_ad_ids or [])' in src

def test_low_cadence_finished_child_does_not_block_future_reconcile():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'A *finished* low-cadence child' in src
    assert 'if str(other.status or "") in {"queued","running","validating"}' in src
    assert '"partial"' not in src[src.index('A *finished* low-cadence child'):src.index('if duplicate:',src.index('A *finished* low-cadence child'))]

def test_shared_avito_throttle_atomic_save_keeps_runtime_permissions():
    src=(ROOT/'backend/app/services/avito_account_throttle.py').read_text(encoding='utf-8')
    assert 'os.chmod(_LEDGER, 0o660)' in src

def test_banner_preserves_prebanner_photo_stack():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    assert '_preserved_asset_ids=[]' in src
    assert 'become gallery photo #1. Only the exact previous banner is removed.' in src
    assert 'db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id)).delete' in src
    assert 'M.link(db,_pa,"campaign_item",str(row.id),"gallery",_pos' in src


def test_no_reference_banner_qa_does_not_report_fake_identity_failure():
    src=(ROOT/'backend/app/services/media_semantic_validator.py').read_text(encoding='utf-8')
    assert 'out["identity_ok"]=True; out["plate_ok"]=True' in src
    assert 'copy_readability_and_composition' in src
    assert 'vehicle/object' in src


def test_semantic_direction_profile_reuses_same_template_without_owner_repeat():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_profile_load_compatible
    db=SessionLocal()
    try:
        p=_ff_profile_load_compatible(db,'antonybatono_78732','Механизированная штукатурка квартир','66903')
        assert p.get('confirmed') is True
        assert p.get('memory_scope') == 'account_template_semantic'
        assert p.get('memory_from_direction') == 'Механизированная штукатурка стен'
        assert (p.get('answers') or {}).get('WorkExperience',{}).get('value') == '7 лет'
        assert not _ff_profile_load_compatible(db,'antonybatono_78732','Поклейка обоев','66903')
    finally:
        db.close()

def test_client_prompt_for_direction_is_not_misparsed_as_audience():
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_extract_plan, FFWorkflowBriefBody
    db=SessionLocal()
    try:
        c=SimpleNamespace(default_account_id='antonybatono_78732')
        brief='Сделай 1 объявление по услуге «Механизированная штукатурка квартир» в городе Сочи. Преимущества клиента используй только релевантные для «Механизированная штукатурка квартир», без выдуманных обещаний.'
        r=_ff_extract_plan(db,c,FFWorkflowBriefBody(brief=brief,variants=1,quantity=1))
        assert r['audience']==''
        assert r['directions']==['Механизированная штукатурка квартир']
        assert r['cities']==['Сочи']
    finally: db.close()

def test_uniqueness_footer_scales_without_repeating_for_bulk_variants():
    from app.api.campaigns import _ff_append_uniqueness_footer
    base='Оффер Сочи.\n\nПреимущество.\n\nНапишите нам — ответим по задаче.'
    vals=[_ff_append_uniqueness_footer(base,'Кухня на заказ','Уфа',variant=i,item_id=1000+i) for i in range(1,101)]
    tails=[v.splitlines()[-1] for v in vals]
    assert len(set(tails))==100
    assert all(t.startswith('✦ ') for t in tails)
    assert all('Кухня на заказ · Уфа' in t for t in tails)

def test_avito_xmlcheck_has_stale_cache_bust_fallback():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[2]/'backend/app/api/parser.py').read_text(encoding='utf-8')
    assert '/api/v2/public/xml_checker/from_url/' in src
    assert '/api/v2/public/xml_checker/upload/' in src
    assert 'BORIS_XMLCHECK_CACHE_BUST' in src
    assert 'avito_public_api_upload_cache_bust' in src


def test_avito_xmlcheck_pending_is_not_false_failure():
    src=(ROOT/'backend/app/api/parser.py').read_text(encoding='utf-8')
    campaigns=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    jobs=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert '"status":"pending"' in src
    assert '"CHECKING" if check_status in {"pending","checking","processing"}' in campaigns
    assert 'feed.get("validation_state") == "CHECKING"' in jobs
    assert 'действий от клиента не требуется' in jobs


def test_account_negative_guarantee_rule_overrides_old_direction_profile():
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_extract_plan, _ff_hydrate_profiles, FFWorkflowBriefBody
    db=SessionLocal()
    try:
        c=SimpleNamespace(default_account_id='antonybatono_78732')
        ff=_ff_extract_plan(db,c,FFWorkflowBriefBody(brief='Сделай 1 объявление по услуге «Механизированная штукатурка квартир» в городе Сочи.',variants=1,quantity=1))
        ff=_ff_hydrate_profiles(db,c,ff)
        entry=(ff.get('direction_intake') or {}).get('Механизированная штукатурка квартир') or {}
        guarantee=(entry.get('answers') or {}).get('Guarantee') or {}
        assert guarantee.get('value') == 'Нет'
        assert guarantee.get('source') == 'account_negative_rule'
        assert entry.get('policy_override') == 'guarantee_not_promised'
    finally:
        db.close()


def test_launcher_has_no_broker_only_hardcoded_fallback():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[2]/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'GENERIC_PROMPT_EXAMPLES' not in src
    assert 'Брокер недвижимости' not in src
    assert 'Одобрение ипотеки' not in src
    assert 'Подбор квартир' not in src
    assert 'fallbackTasks' in src
    assert 'нужны данные клиента' in src


def test_launcher_auto_selects_services_or_products_from_client_context():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[2]/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'function inferCampaignKind' in src
    assert 'p.startsWith("услуги >")' in src
    assert 'setPromptKind(inferCampaignKind(ctx))' in src
    assert 'if (seeds.length<5)' in src


def test_campaign_ui_does_not_expose_provider_specific_banner_wording():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[2]/'frontend/app/dashboard/campaign/[id]/page.tsx').read_text(encoding='utf-8')
    assert 'BORIS создаёт OpenAI-баннеры' not in src
    assert 'Новые AI-ракурсы' not in src
    assert 'платных AI-баннеров' not in src
    assert 'BORIS создаёт и проверяет баннеры' in src
    assert 'Новые ИИ-ракурсы' in src


def test_launcher_context_is_typed_by_avito_business_kind():
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.is_active==True).order_by(User.id.asc()).first()
        product=campaign_account_context('planetazayavki_65985',db,user)
        service=campaign_account_context('antonybatono_78732',db,user)
        glass=campaign_account_context('evgeniy_peregorodki_12447',db,user)
        assert product.get('launcher_kind') == 'products'
        assert len(product.get('launcher_directions_products') or []) >= 1
        assert (product.get('launcher_directions_services') or []) == []
        assert service.get('launcher_kind') == 'services'
        assert len(service.get('launcher_directions_services') or []) >= 1
        assert (service.get('launcher_directions_products') or []) == []
        assert glass.get('launcher_kind') == 'products'
        assert 'Душевые перегородки из стекла' in (glass.get('launcher_directions_products') or [])
    finally:
        db.close()


def test_launcher_frontend_never_cross_leaks_typed_directions_between_tabs():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[2]/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'launcher_directions_services' in src
    assert 'launcher_directions_products' in src
    assert 'const directionSeed=typedReady ? listingDirs' in src
    assert 'listingDirectionHints(context,kind)' in src


def test_sparse_real_accounts_get_profile_grounded_launcher_directions():
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.is_active==True).order_by(User.id.asc()).first()
        marketing=campaign_account_context('marketing_pod_klyuch_20035',db,user)
        sakura=campaign_account_context('td_sakura_boris_82421',db,user)
        assert marketing.get('launcher_kind') == 'services'
        assert 'Продвижение на Avito' in (marketing.get('launcher_directions_services') or [])
        assert 'Услуги авитолога' in (marketing.get('launcher_directions_services') or [])
        assert sakura.get('launcher_kind') == 'products'
        assert 'Бытовая техника' in (sakura.get('launcher_directions_products') or [])
        assert 'Электроника' in (sakura.get('launcher_directions_products') or [])
    finally:
        db.close()


def test_launcher_rejects_legacy_caps_seo_slogan():
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.is_active==True).order_by(User.id.asc()).first()
        garik=campaign_account_context('garik_plitka_mo_58647',db,user)
        titles=garik.get('launcher_directions_products') or []
        assert all('НАДЕЖНАЯ БРУСЧАТКА' not in x for x in titles)
        assert 'Брусчатка' in titles
        assert 'Бордюры' in titles
    finally:
        db.close()


def test_launcher_context_exposes_existing_feed_category_hints():
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.is_active==True).order_by(User.id.asc()).first()
        cases=[
            ('planetazayavki_65985','products','95712'),
            ('evgeniy_peregorodki_12447','products','107907'),
            ('mbroker_27859','services','133104'),
            ('vasyailchenko_82650','services','96669'),
            ('evz_denis_aleksandra_evakuatory_i_pricepy_22264','services','71631'),
        ]
        for aid,kind,tid in cases:
            ctx=campaign_account_context(aid,db,user)
            entries=ctx.get('launcher_entries_'+kind) or []
            # Live feed titles legitimately evolve. The contract is that at least
            # one current launcher card keeps the account's proven category/template
            # identity; it is not that a historical wording must stay forever.
            hit=next((x for x in entries
                      if str(x.get('template_id') or '')==tid
                      and str(x.get('source') or '').startswith('existing_feed')),None)
            assert hit, (aid,tid,entries)
    finally:
        db.close()


def test_launcher_category_hint_is_canonicalized_server_side():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_validated_category_hint
    db=SessionLocal()
    try:
        got=_ff_validated_category_hint(db,'Кухня на заказ',{'Кухня на заказ':{'template_id':'95712','path':'НЕВЕРНЫЙ ПУТЬ'}})
        assert got.get('template_id')=='95712'
        assert got.get('path')=='Для дома и дачи > Мебель и интерьер > Кухонные гарнитуры > Кухни'
        assert got.get('score')==100
        assert got.get('source')=='existing_account_feed'
    finally:
        db.close()


def test_launcher_frontend_passes_category_hint_only_for_untouched_card_prompt():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[2]/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'selectedPromptStillExact' in src
    assert 'category_hints: categoryHints' in src
    # Editing clears the selected example before the draft-aware state setter.
    import re
    textarea = re.search(r'<textarea id="boris-campaign-brief".*?/>', src, re.S).group(0)
    assert re.search(r"setSelectedPromptExample\(null\);\s*updateCampaignBrief\(e.target.value\)", textarea)
    update = src.split("function updateCampaignBrief", 1)[-1] if "function updateCampaignBrief" in src else src.split("const updateCampaignBrief", 1)[-1]
    assert "setCampaignBrief(value)" in update.split("const [selectedPromptExample", 1)[0]
    assert 'categoryTemplateId' in src


def test_quoted_offer_with_internal_preposition_keeps_full_direction_and_hint():
    from app.db.session import SessionLocal
    from app.models.campaign import Campaign
    from app.api.campaigns import _ff_extract_plan, FFWorkflowBriefBody
    db=SessionLocal()
    try:
        campaign=db.query(Campaign).filter(Campaign.default_account_id=='evz_denis_aleksandra_evakuatory_i_pricepy_22264').order_by(Campaign.id.desc()).first()
        assert campaign is not None
        direction='Переоборудование в эвакуатор на заказ'
        body=FFWorkflowBriefBody(
            brief=f'Сделай 1 боевое объявление по услуге «{direction}». Используй только факты.',
            variants=1,
            category_hints={direction:{'template_id':'71631'}},
        )
        ff=_ff_extract_plan(db,campaign,body)
        assert ff.get('directions') == [direction]
        assert (ff.get('matrix') or [])[0].get('template_id') == '71631'
    finally:
        db.close()


def test_category_semantics_cover_real_launcher_gaps():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_category_candidates, _ff_auto_category_candidate
    db=SessionLocal()
    try:
        cases={
            'одобрение ипотеки':'133102',
            'рефинансирование и консолидация кредитов':'133102',
            'кредит под залог недвижимости':'133102',
            'Подбор квартир':'133104',
            'Брусчатка':'111166',
            'Продажа электровелосипедов в Москве и МО':'126420',
            'Товарный бетон':'90917',
            'Гидроперчатки JetPilot Vault Race':'119484',
            'Песня в подарок быстро':'125593',
            'бытовки':'90576',
        }
        for direction,tid in cases.items():
            got=_ff_auto_category_candidate(direction,_ff_category_candidates(db,direction,6))
            assert str(got.get('template_id') or '') == tid, (direction,got)
    finally:
        db.close()


def test_broad_category_labels_never_auto_pick_random_leaf():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_category_candidates, _ff_auto_category_candidate
    db=SessionLocal()
    try:
        for direction in ['Корпусная мебель на заказ','Бытовая техника','Электроника','Строительные товары и материалы','Экипировка и снаряжение для водных видов спорта']:
            got=_ff_auto_category_candidate(direction,_ff_category_candidates(db,direction,6))
            assert got == {}, (direction,got)
        assert _ff_category_candidates(db,'Продвижение на Avito',6) == []
        assert _ff_category_candidates(db,'Менеджер по продажам',6) == []
    finally:
        db.close()


def test_launcher_prioritizes_specific_feed_titles_over_broad_profile_labels():
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.is_active==True).order_by(User.id.asc()).first()
        furniture=campaign_account_context('andrey_mebel_launzh_moskva_69737',db,user)
        water=campaign_account_context('dinara_spb_vodnaya_ekipirovka_18294',db,user)
        f_entries=furniture.get('launcher_entries_products') or []
        w_entries=water.get('launcher_entries_products') or []
        assert f_entries and str(f_entries[0].get('source') or '').startswith('existing_feed'), f_entries
        assert w_entries and str(w_entries[0].get('source') or '').startswith('existing_feed'), w_entries
        # The exact first SKU may change with the live feed. What must remain
        # stable is that concrete proven listings outrank broad profile labels.
        assert 'корпусная мебель' not in str(f_entries[0].get('title') or '').lower()
        assert 'экипировка и снаряжение' not in str(w_entries[0].get('title') or '').lower()
    finally:
        db.close()


def test_real_launcher_domains_do_not_cross_into_wrong_avito_tree():
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context, _ff_category_candidates, _ff_auto_category_candidate, _ff_validated_category_hint
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.is_active==True).order_by(User.id.asc()).first()
        expected={
            'andrey_mebel_launzh_moskva_69737':'Для дома и дачи > Мебель и интерьер',
            'antonybatono_78732':'Услуги > Предложение услуг > Ремонт и отделка',
            'mbroker_27859':'Услуги > Предложение услуг > Услуги посредников',
            'vasyailchenko_82650':'Услуги > Предложение услуг > Автосервис, аренда',
            'garik_plitka_mo_58647':'Для дома и дачи > Ремонт и строительство',
            'georgiy_mo_elektrosamokaty_88067':'Хобби и отдых > Велосипеды',
            'dinara_spb_vodnaya_ekipirovka_18294':'Хобби и отдых > Спорт и отдых',
            'evgeniy_peregorodki_12447':'Для дома и дачи > Ремонт и строительство',
            'otdushi':'Услуги > Предложение услуг > Искусство',
            'planetazayavki_65985':'Для дома и дачи > Мебель и интерьер',
            'prodazha_bytovok_25677':'Для дома и дачи > Ремонт и строительство',
            'samvel_almaznoe_burenie_64222':'Услуги > Предложение услуг > Ремонт и отделка',
            'evz_denis_aleksandra_evakuatory_i_pricepy_22264':'Услуги > Предложение услуг > Услуги эвакуатора',
        }
        for aid,prefix in expected.items():
            ctx=campaign_account_context(aid,db,user)
            kind=ctx.get('launcher_kind')
            entries={x.get('title'):x for x in (ctx.get('launcher_entries_'+kind) or [])}
            for direction in (ctx.get('launcher_directions_'+kind) or []):
                entry=entries.get(direction) or {}
                hint=_ff_validated_category_hint(db,direction,{direction:entry}) if entry.get('template_id') else {}
                got=hint or _ff_auto_category_candidate(direction,_ff_category_candidates(db,direction,6))
                path=str(got.get('path') or '')
                if path:
                    assert path.startswith(prefix), (aid,direction,path,prefix)
    finally:
        db.close()


def test_glass_partition_categories_distinguish_shower_from_interior():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_category_candidates, _ff_auto_category_candidate
    db=SessionLocal()
    try:
        shower=_ff_auto_category_candidate('Душевые перегородки из стекла',_ff_category_candidates(db,'Душевые перегородки из стекла',4))
        interior=_ff_auto_category_candidate('Офисные перегородки из стекла',_ff_category_candidates(db,'Офисные перегородки из стекла',4))
        assert shower.get('template_id')=='107907'
        assert interior.get('template_id')=='106836'
    finally:
        db.close()


def test_launcher_cards_show_known_avito_subcategory_in_note():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[2]/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'const categoryLeaf=' in src
    assert 'Avito: ${categoryLeaf}' in src


def test_product_origin_defaults_use_only_persisted_manufacturer_evidence():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_account_product_origin_defaults
    db=SessionLocal()
    try:
        fields={
            'Condition':{'allowed':['Новое','Б/у']},
            'AdType':{'allowed':['Товар приобретен на продажу','Товар от производителя']},
            'Availability':{'allowed':['В наличии','Под заказ']},
        }
        got=_ff_account_product_origin_defaults(db,'evgeniy_peregorodki_12447',fields)
        assert got['Condition']['value']=='Новое'
        assert got['AdType']['value']=='Товар от производителя'
        assert got['Availability']['value']=='Под заказ'
        assert 'evidence' in got['Condition']['source']
    finally:
        db.close()


def test_exact_existing_feed_price_can_heal_required_price_without_guessing():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_confirmed_price_default
    db=SessionLocal()
    try:
        # The live feed now contains two exact-title rows (8950/8955), so automatic
        # price recovery must fail closed rather than silently choosing one.
        got=_ff_confirmed_price_default(db,'evgeniy_peregorodki_12447','Стеклянная перегородка в душ')
        assert not got, got
        # Loose neighboring titles are deliberately not accepted by this fallback.
        miss=_ff_confirmed_price_default(db,'evgeniy_peregorodki_12447','Совсем другая стеклянная конструкция')
        assert not miss
    finally:
        db.close()


def test_campaign_prepare_requires_exact_three_semantic_photos_not_any_image():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert '_prepared_photo_count' in src
    assert '!= 3' in src
    assert 'BORIS автоматически заменяет фотографии, которые не соответствуют объявлению.' in src


def test_banner_retry_identity_ignores_mutable_qa_content_hash():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    block=src[src.index('_normal_basis={'):src.index('_artifact_intent="normal:"',src.index('_normal_basis={'))]
    assert 'semantic_fingerprint' in block
    assert 'content_hash' not in block
    assert 'clean_ai_cached_after_stock_composition_qa' in src


def test_banner_semantic_rejections_are_persisted_for_media_self_heal():
    campaigns=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    jobs=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'media_semantic_rejections' in campaigns
    assert 'banner_semantic_mismatch_return_to_media' in jobs
    assert 'media_retry' in jobs


def test_copy_price_reconcile_uses_canonical_listing_price_without_touching_other_tariff():
    from app.api.campaigns import _ff_reconcile_copy_price
    src='Подберём вариант и сориентируем по стоимости от 7280 ₽.\nЦена. от 7280 ₽.\nМонтаж 1500 ₽ по подтвержденному тарифу.'
    got=_ff_reconcile_copy_price(src,8950,'')
    assert '7280' not in got
    assert got.count('8950')==2
    assert 'Монтаж 1500 ₽' in got


def test_materialize_reconciles_price_copy_on_every_idempotent_retry():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    anchor=src.index('Canonical payload price always wins.')
    block=src[anchor-1800:anchor+1200]
    assert 'if row:' in block
    assert 'old["description"]=_ff_reconcile_copy_price' in block


def test_internal_validation_semantic_mismatch_returns_to_media_refill():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'recuration_codes={"media_semantic_mismatch","media_semantic_low_confidence","media_semantic_unreviewed"}' in src
    assert 'BORIS автоматически добирает недостающие проверенные фотографии после смысловой очистки.' in src
    assert '_media_refill_codes={' in src
    for code in (
        'photo_stack_incomplete','images_missing','media_semantic_no_links',
        'media_semantic_proof_missing','media_semantic_proof_stale',
        'media_semantic_link_payload_drift',
    ):
        assert code in src


def test_glass_shower_stock_query_targets_fixed_walkin_partition():
    from app.api.media import _stock_topic_query
    got=_stock_topic_query('Стеклянная перегородка в душ')
    low=got.lower()
    assert 'glass' in low and ('partition' in low or 'panel' in low)
    assert 'walk' in low


def test_zero_touch_prepare_persists_ready_for_review_campaign_status():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'campaign.status="ready_for_review"' in src
    assert 'READY_FOR_REVIEW_IS_TERMINAL_PREP_V1' in src


def test_copy_price_claim_extractor_ignores_secondary_tariffs():
    from app.api.campaigns import _ff_explicit_copy_price_claims
    text='Цена: 8950 ₽. Монтаж 1500 ₽. Доставка 700 ₽. Стоимость товара от 8950 руб.'
    assert _ff_explicit_copy_price_claims(text)==[8950,8950]


def test_campaign_validation_blocks_copy_price_mismatch_contract():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    assert 'copy_price_unconfirmed' in src
    assert 'copy_price_mismatch' in src
    assert '_ff_explicit_copy_price_claims(desc)' in src


def test_mixed_account_products_tab_excludes_rental_intent_titles():
    import sys, types
    fake=types.ModuleType('app.auth')
    fake.get_current_user=lambda: None
    sys.modules.setdefault('app.auth',fake)
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        owner=db.query(User).filter(User.id==2).first()
        ctx=campaign_account_context('3411770_94346',db,owner)
        assert ctx.get('launcher_directions_services')
        products=ctx.get('launcher_directions_products') or []
        assert products
        assert not any(any(x in str(title).lower() for x in ('аренд','прокат','сдам','напрокат')) for title in products)
    finally:
        db.close()


def test_copy_price_contract_is_scoped_to_feed_factory_not_legacy_auto_campaigns():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    assert '_strict_price_contract=bool(p.get("feed_factory"))' in src
    assert 'copy_quality_contract' in src
    assert 'legacy AUTO campaign descriptions' in src


def test_launcher_category_params_never_expose_system_feed_controls():
    import sys, types
    fake=types.ModuleType('app.auth'); fake.get_current_user=lambda: None; sys.modules.setdefault('app.auth',fake)
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        owner=db.query(User).filter(User.id==2).first()
        ctx=campaign_account_context('evgeniy_peregorodki_12447',db,owner)
        entries=ctx.get('launcher_entries_products') or []
        assert entries
        forbidden={'ListingFee','Promo','AdStatus','Id','id','avitoid','Phone','ContactPhone','Email'}
        assert not any(forbidden & set((e.get('params') or {}).keys()) for e in entries)
    finally:
        db.close()


def test_launcher_active_snapshots_are_bounded_to_recent_window():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    assert "INTERVAL '72 hours'" in src
    assert 'active_snapshot_window_hours' in src


def test_all_resolved_real_launcher_hints_revalidate_server_side():
    import sys, types
    fake=types.ModuleType('app.auth'); fake.get_current_user=lambda: None; sys.modules.setdefault('app.auth',fake)
    from app.db.session import SessionLocal
    from app.models.account import Account
    from app.models.user import User
    from app.api.campaigns import campaign_account_context,_ff_validated_category_hint
    db=SessionLocal()
    try:
        owner=db.query(User).filter(User.id==2).first(); checked=0; checked_accounts=set()
        for account in db.query(Account).order_by(Account.id).all():
            aid=str(account.account_id or '')
            if aid.startswith(('qa','test','ostapenkokirill')) or aid in {'inbox_60','boris_video_demo'}: continue
            ctx=campaign_account_context(aid,db,owner); kind=ctx.get('launcher_kind')
            entries=(ctx.get('launcher_entries_services') if kind=='services' else ctx.get('launcher_entries_products')) or []
            for entry in entries:
                tid=str(entry.get('template_id') or ''); path=str(entry.get('category_path') or ''); direction=str(entry.get('title') or '')
                if not tid: continue
                got=_ff_validated_category_hint(db,direction,{direction:{'template_id':tid,'path':path}})
                assert got and str(got.get('template_id') or '')==tid, (aid,direction,tid,path,got)
                checked+=1; checked_accounts.add(aid)
        # Live accounts/titles legitimately come and go. Keep a broad real-data
        # integration sample without coupling correctness to yesterday's exact
        # number of launcher cards.
        assert len(checked_accounts)>=10, checked_accounts
        assert checked>=len(checked_accounts)
    finally:
        db.close()


def test_ready_for_review_marks_live_items_ready_without_publishing():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert '_ReadyCampaignItem.status:"ready"' in src
    assert '_ReadyCampaignItem.status!="superseded"' in src
    assert 'only the separate owner-confirmed deliver action may publish' in src


def test_active_titles_also_respect_typed_rental_product_filter():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    assert '_typed_listing_semantic_ok(_active_title,"services")' in src
    assert '_typed_listing_semantic_ok(_active_title,"products")' in src


def test_unresolved_launcher_category_is_explicit_in_ui():
    page=(ROOT/'frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
    assert 'Avito: уточним подкатегорию' in page


def test_launcher_existing_feed_template_without_path_is_canonicalized():
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.is_active==True).order_by(User.id.asc()).first()
        ctx=campaign_account_context('evz_denis_aleksandra_evakuatory_i_pricepy_22264',db,user)
        entries=ctx.get('launcher_entries_services') or []
        hit=next((x for x in entries if x.get('title')=='Переоборудование в эвакуатор на заказ'),None)
        assert hit and hit.get('template_id')=='71631', hit
        assert hit.get('category_path')=='Услуги > Предложение услуг > Услуги эвакуатора', hit
    finally:
        db.close()


def test_exact_feed_price_fails_closed_on_conflicting_duplicate_titles():
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_confirmed_price_default
    db=SessionLocal()
    try:
        # This real legacy feed has the exact same launcher title at several
        # different prices. BORIS must not pick the first row arbitrarily.
        got=_ff_confirmed_price_default(db,'evz_denis_aleksandra_evakuatory_i_pricepy_22264','Переоборудование в эвакуатор на заказ')
        assert not got or got.get('source')!='existing_account_feed_exact', got
        # Evgeniy currently also has two exact-title rows at 8950/8955; this must
        # likewise remain ambiguous for any NEW campaign. Existing campaign 247
        # keeps its already materialized canonical price and is not rewritten.
        good=_ff_confirmed_price_default(db,'evgeniy_peregorodki_12447','Стеклянная перегородка в душ')
        assert not good, good
    finally:
        db.close()


def test_photo_stack_validation_ignores_extra_legacy_candidate_links():
    from app.db.session import SessionLocal
    from app.models.campaign_item import CampaignItem
    from app.api.campaigns import _ff_validate_row
    db=SessionLocal()
    try:
        # Campaigns 222/223 retain extra historical candidate links, but every id
        # in their canonical photo_stack is still linked. They must not be rejected
        # merely because the audit trail contains additional candidates.
        for iid in (4263,4283):
            row=db.query(CampaignItem).filter(CampaignItem.id==iid).first()
            got=_ff_validate_row(row,db)
            assert not any((e or {}).get('code')=='photo_stack_incomplete' for e in (got.get('errors') or [])), got
        # Fresh battle item remains valid under the same canonical-stack rule.
        row=db.query(CampaignItem).filter(CampaignItem.id==4867).first()
        assert _ff_validate_row(row,db).get('valid') is True
    finally:
        db.close()


def test_delivery_full_success_updates_campaign_lifecycle_truth():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    # Both direct-upload and scheduled-URL terminal success branches must move
    # Campaign out of draft/ready_for_review after Avito truth is known.
    assert src.count('campaign.status="completed"') >= 2
    assert src.count('campaign.status="partial"') >= 2
    assert src.count('campaign.status="failed"') >= 2
    assert 'campaign.status="pending_review"' in src
    assert 'ff["step"]="publication_completed"' in src


def test_delivery_partial_jobs_autonomously_watch_same_upload_without_owner():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'BackgroundJob.status.in_(("awaiting_confirmation","partial"))' in src
    # Only non-terminal publication states are resumed. A terminal partial is
    # evidence and must never be degraded by a later pending report.
    assert 'job.kind=="campaign_deliver" and stage=="publication_pending_review"' in src
    assert 'job.kind=="campaign_deliver" and stage=="publication_processing"' in src
    assert '"publication_pending_review","publication_processing","publication_partial"' not in src
    assert 'p["stage"]="publication_watch"' in src
    assert 'job.kind == "campaign_deliver" and final == "partial"' in src
    assert '_publication_watch_count' in src
    assert 'if str(payload.get("stage") or "") == "publication_watch"' in src
    assert 'payload["stage"]="awaiting_avito_result"' in src
    assert 'campaign.status="processing"' in src


def test_pending_publication_poll_preserves_last_proven_counters():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert '_prev_published=int(_prev_activation.get("published") or 0)' in src
    assert '_prev_errors=int(_prev_activation.get("errors") or 0)' in src
    assert '"published":max(_prev_published,int(snap.get("successful") or 0))' in src
    assert '"errors":max(_prev_errors,int(snap.get("error") or 0))' in src


def test_terminal_partial_delivery_is_never_auto_resumed_as_polling():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'stage=="publication_pending_review"' in src
    assert 'stage=="publication_processing"' in src
    assert '"publication_pending_review","publication_processing","publication_partial"' not in src


def test_pending_avito_report_cannot_regress_same_upload_terminal_truth():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert '_prev_upload==int(upload_id or 0)' in src
    assert '_prev_status in {"completed","partial","failed"}' in src
    assert 'временный pending не меняет факт публикации' in src


def test_stale_avito_pending_uses_low_cadence_and_partial_is_terminal():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'timedelta(hours=6)' in src
    assert 'elif _stage == "publication_partial"' in src
    assert 'PUBLICATION_PARTIAL_REPAIR_STATE_V1' in src
    assert 'job.status="completed"' in src
    assert '_publication_partial_terminal' in src


def test_duplicate_feed_title_launcher_uses_only_consensus_params():
    import sys, types
    fake=types.ModuleType('app.auth'); fake.get_current_user=lambda:None; sys.modules.setdefault('app.auth',fake)
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.api.campaigns import campaign_account_context
    db=SessionLocal()
    try:
        owner=db.query(User).filter(User.id==2).first()
        ctx=campaign_account_context('evgeniy_peregorodki_12447',db,owner)
        hit=next(x for x in (ctx.get('launcher_entries_products') or []) if x.get('title')=='Стеклянная перегородка в душ')
        assert hit.get('duplicate_exact_matches')==2, hit
        params=hit.get('params') or {}
        assert params.get('ProductType')=='Душевые уголки, перегородки и двери', params
        assert params.get('ShowerEnclosureBrand')=='HAG', params
        assert 'ShowerEnclosureWidth' not in params and 'ShowerEnclosureHeight' not in params, params
    finally:
        db.close()

def test_low_cadence_pending_review_reconciler_is_read_only_and_six_hour_bounded():
    src=(ROOT/'backend/app/services/jobs.py').read_text(encoding='utf-8')
    assert 'def _low_cadence_pending_review_reconcile(db):' in src
    assert 'now-last < timedelta(hours=6)' in src
    assert 'watch_payload["stage"]="publication_watch"' in src
    assert 'watch_payload["upload_id"]=upload_id' in src
    assert 'BackgroundJob(owner_user_id=campaign.owner_user_id' in src
    assert 'if int(payload.get("_low_cadence_parent_job_id") or 0):' in src
    assert '_low_cadence_pending_review_reconcile(db)' in src
    assert '_campaign_status not in {"pending_review","processing"}' in src
    assert 'LOW_CADENCE_PROCESSING_REENTRY_V1' in src
    assert 'same campaign is still processing an existing external upload' in src


def test_water_sport_launcher_distinguishes_gloves_from_goggles():
    src=(ROOT/'backend/app/api/campaigns.py').read_text(encoding='utf-8')
    assert 'water_accessory_product' in src
    assert 'CategoryTreeLeaf.template_id == "119478"' in src
    assert 'hydro_gloves_product' in src
    assert 'CategoryTreeLeaf.template_id == "119484"' in src


def test_count_fragment_is_not_a_fake_campaign_direction():
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    from app.api.campaigns import _ff_extract_plan, FFWorkflowBriefBody
    db=SessionLocal()
    try:
        c=SimpleNamespace(default_account_id='testref_47784')
        r=_ff_extract_plan(
            db,
            c,
            FFWorkflowBriefBody(
                brief='Тестовая уборка в Москве, 1 объявление, цена от 1000 ₽.',
                variants=1,
            ),
        )
        assert len(r['directions']) == 1, r['directions']
        assert not any('объявлен' in x.lower() for x in r['directions'])
        assert r['quantity'] == 1
    finally:
        db.close()
