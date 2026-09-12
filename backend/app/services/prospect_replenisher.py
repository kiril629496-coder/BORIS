from __future__ import annotations
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import prospect_campaigns as pc

def ensure_schema(db):
    # Canonical migration 049 owns the last_search_attempt_at column on prospect_replenish_runs.
    # Minute-worker hot path: never execute DDL once the canonical replenisher
    # schema is complete. CREATE/ALTER IF NOT EXISTS still takes relation locks
    # and can stall unrelated prospecting API requests under concurrency.
    ready=db.execute(text("""SELECT
      to_regclass('public.prospect_replenish_runs') IS NOT NULL
      AND to_regclass('public.prospect_repair_state') IS NOT NULL
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_replenish_runs' AND column_name='last_discovery_at')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_replenish_runs' AND column_name='last_repair_at')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_replenish_runs' AND column_name='discovery_cursor')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_replenish_runs' AND column_name='last_search_attempt_at')
    """)).scalar()
    if ready:
        return
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text("""UPDATE prospect_replenish_runs
      SET last_discovery_at=COALESCE(last_discovery_at,last_run_at),
          last_repair_at=COALESCE(last_repair_at,last_run_at)
      WHERE last_discovery_at IS NULL OR last_repair_at IS NULL"""))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.commit()

def _adaptive_discovery_hours(ready_now:int, min_ready:int, every_hours:float)->float:
    """Increase only reserve discovery cadence while the ready pool is low."""
    ratio=float(max(0,int(ready_now)))/float(max(1,int(min_ready)))
    if ratio < 0.75:
        return min(float(every_hours),0.5)
    if ratio < 1.0:
        return min(float(every_hours),1.0)
    return float(every_hours)

def tick(min_ready:int=100, every_hours:int=6, repair_every_minutes:int=60)->dict:
    db=SessionLocal(); pc.ensure_schema(db); ensure_schema(db)
    try:
        c=db.execute(text("""SELECT c.id,c.owner_id,c.niche,c.regions,c.min_quality_score,
          count(m.id) FILTER (WHERE m.status='ready') ready,
          r.last_discovery_at,r.last_search_attempt_at,r.last_repair_at,COALESCE(r.discovery_cursor,0) discovery_cursor
          FROM prospect_campaigns c LEFT JOIN prospect_campaign_members m ON m.campaign_id=c.id
          LEFT JOIN prospect_replenish_runs r ON r.campaign_id=c.id
          WHERE c.status='active'
            -- Development outreach has its own buyer-oriented radar/refill.
            -- The generic replenisher searches by campaign.niche and would turn
            -- the offer text "custom software development" into a vendor query,
            -- discovering competing software agencies instead of buyers.
            AND c.name <> 'Кирилл · разработка SaaS/App'
            AND (r.last_repair_at IS NULL OR r.last_repair_at < NOW() - (:rm || ' minutes')::interval)
          GROUP BY c.id,r.last_discovery_at,r.last_search_attempt_at,r.last_repair_at,r.discovery_cursor
          HAVING count(m.id) FILTER (WHERE m.status='ready') < :mr
          ORDER BY count(m.id) FILTER (WHERE m.status='ready') ASC,c.id LIMIT 1"""),{'rm':str(repair_every_minutes),'mr':min_ready}).mappings().first()
        if not c:return {'status':'idle'}
        cid=int(c['id']); owner=int(c['owner_id']); niche=c['niche']; regions=list(c['regions'] or []); min_quality=int(c['min_quality_score'] or 55); discovery_cursor=int(c['discovery_cursor'] or 0)
        ready_now=int(c.get('ready') or 0)
        # EMAIL_RESERVE_ADAPTIVE_DISCOVERY_V1: when the ready reserve is well
        # below target, waiting three hours between discovery passes lets the
        # campaign starve even though the minute worker is healthy. Increase
        # only discovery cadence (never send volume): <75% target -> 30 min,
        # 75-99% -> 60 min, target reached -> no discovery because this campaign
        # is excluded by the HAVING clause above.
        effective_every_hours=_adaptive_discovery_hours(ready_now,min_ready,every_hours)
        last_search_attempt=c.get('last_search_attempt_at') or c.get('last_discovery_at')
        discovery_due=(last_search_attempt is None) or db.execute(text("SELECT :d < NOW() - (:h || ' hours')::interval"),{'d':last_search_attempt,'h':str(effective_every_hours)}).scalar()
        db.execute(text("""INSERT INTO prospect_replenish_runs(campaign_id,last_run_at,last_repair_at)
          VALUES(:c,NOW(),NOW()) ON CONFLICT(campaign_id) DO UPDATE
          SET last_run_at=NOW(),last_repair_at=NOW(),updated_at=NOW()"""),{'c':cid});db.commit()
    finally:db.close()
    try:
        # Keep the minute lifecycle bounded: heavy public-web discovery is spread
        # across scheduled passes instead of blocking one email worker for minutes.
        d=pc.discover_niche(owner,niche,regions,target=100,max_queries=12,query_offset=discovery_cursor) if discovery_due else {'inserted':[],'existing':[],'next_offset':discovery_cursor}
        inserted=[int(x) for x in (d.get('inserted') or [])]
        existing=[int(x) for x in (d.get('existing') or [])]
        search_health=dict(d.get('search_health') or {})
        search_degraded=bool(d.get('search_failure_count')) and search_health.get('state')=='degraded'
        if discovery_due:
            db=SessionLocal(); ensure_schema(db)
            if search_degraded:
                db.execute(text("UPDATE prospect_replenish_runs SET last_search_attempt_at=NOW(),last_error='search_degraded',updated_at=NOW() WHERE campaign_id=:c"),{'c':cid})
            else:
                db.execute(text("UPDATE prospect_replenish_runs SET last_search_attempt_at=NOW(),last_discovery_at=NOW(),discovery_cursor=:cursor,last_error=NULL,updated_at=NOW() WHERE campaign_id=:c"),{'c':cid,'cursor':int(d.get('next_offset') or 0)})
            db.commit(); db.close()
        # Reuse already discovered domains that still have no selected email and
        # consume any newly seeded canonical backlog. This keeps discovery source-
        # agnostic without creating a second parser/scheduler.
        candidates=list(dict.fromkeys(inserted+existing))
        db=SessionLocal()
        backlog=[int(x[0]) for x in db.execute(text("""
          SELECT c.id FROM prospect_companies c
          WHERE c.owner_id=:o AND c.status='new'
            AND (c.city = ANY(:regions) OR cardinality(:regions)=0)
            AND c.search_query ILIKE '%' || :niche || '%'
          ORDER BY c.discovery_score DESC NULLS LAST,c.id
          LIMIT 30
        """),{'o':owner,'regions':regions,'niche':niche}).all()]
        candidates=list(dict.fromkeys(backlog+candidates))
        # Repair is driven by the active campaign's quality threshold, not by
        # the mere existence of any selected email. A company with only a weak
        # score-40/50 mailbox still needs another parse pass when the campaign
        # requires >=55.
        max_repair_attempts=3
        retry=[int(x[0]) for x in db.execute(text("""
          SELECT c.id FROM prospect_companies c
          LEFT JOIN prospect_repair_state rs ON rs.campaign_id=:campaign AND rs.company_id=c.id
          WHERE c.owner_id=:o
            AND COALESCE(rs.attempts,0) < :max_attempts
            AND (cardinality(:regions)=0 OR c.city = ANY(:regions))
            AND c.search_query ILIKE '%' || :niche || '%'
            AND NOT EXISTS(
              SELECT 1 FROM prospect_contacts p
              WHERE p.company_id=c.id AND p.kind='email'
                AND p.selected_for_outreach=true
                AND COALESCE(p.quality_score,0)>=:q
            )
            AND (rs.last_attempt_at IS NULL OR rs.last_attempt_at < NOW() - interval '12 hours')
          ORDER BY COALESCE(rs.attempts,0) ASC,
                   CASE WHEN c.id = ANY(:ids) THEN 0 ELSE 1 END,
                   CASE WHEN c.status='new' THEN 0 ELSE 1 END,
                   rs.last_attempt_at ASC NULLS FIRST,
                   c.discovery_score DESC NULLS LAST,c.id
          LIMIT 4
        """),{'campaign':cid,'o':owner,'regions':regions,'niche':niche,'q':min_quality,'max_attempts':max_repair_attempts,'ids':candidates or [-1]}).all()]
        if retry:
            db.execute(text("""INSERT INTO prospect_repair_state(campaign_id,company_id,attempts,last_attempt_at)
              SELECT :c,x,1,NOW() FROM unnest(CAST(:ids AS BIGINT[])) x
              ON CONFLICT(campaign_id,company_id) DO UPDATE
              SET attempts=prospect_repair_state.attempts+1,last_attempt_at=NOW()"""),{'c':cid,'ids':retry})
            db.commit()
        db.close()
        parsed=pc.parse_batch(owner,retry,max_pages=4,max_companies=4) if retry else {'done':[],'failed':[]}
        aud=pc.build_audience(owner,cid)
        if retry:
            db=SessionLocal(); ensure_schema(db)
            good_ids=[int(x[0]) for x in db.execute(text("""SELECT DISTINCT company_id FROM prospect_contacts
              WHERE company_id = ANY(:ids) AND kind='email' AND selected_for_outreach=true
                AND COALESCE(quality_score,0)>=:q"""),{'ids':retry,'q':min_quality}).all()]
            if good_ids:
                db.execute(text("UPDATE prospect_repair_state SET last_success_at=NOW() WHERE campaign_id=:c AND company_id = ANY(:ids)"),{'c':cid,'ids':good_ids})
                db.commit()
            db.close()
        db=SessionLocal();ensure_schema(db)
        last_error='search_degraded' if search_degraded else None
        db.execute(text("UPDATE prospect_replenish_runs SET last_inserted=:i,last_parsed=:p,last_error=:e,updated_at=NOW() WHERE campaign_id=:c"),{'i':len(inserted),'p':len(parsed.get('done') or []),'e':last_error,'c':cid});db.commit();db.close()
        return {
            'status':'degraded' if search_degraded else 'ok',
            'campaign_id':cid,
            'inserted':len(inserted),
            'reused':len(existing),
            'retry_candidates':len(retry),
            'parsed':len(parsed.get('done') or []),
            'ready':aud.get('ready',0),
            'search_health':search_health,
            'search_failure_count':int(d.get('search_failure_count') or 0),
        }
    except Exception as e:
        db=SessionLocal();ensure_schema(db);db.execute(text("UPDATE prospect_replenish_runs SET last_error=:e,updated_at=NOW() WHERE campaign_id=:c"),{'e':type(e).__name__,'c':cid});db.commit();db.close();return {'status':'error','campaign_id':cid,'error':type(e).__name__}
