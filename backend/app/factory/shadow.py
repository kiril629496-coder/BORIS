"""Factory E shadow — FACTORY_E_SHADOW_V1.

Read-only comparison between materialized values and legacy sources.

No writes.
No consumer cutover.
"""

from sqlalchemy import text


FIELDS = {
    "profile.timezone",
    "kpi.business_hours",
    "schedule.autopilot",
    "schedule.mop_poll",
}


def _storage_json(db, account_id, key):
    row=db.execute(text("""
        SELECT value
        FROM storage
        WHERE account_id=:a
          AND key=:k
        ORDER BY id DESC
        LIMIT 1
    """),{
        "a":account_id,
        "k":key,
    }).fetchone()

    if not row:
        return None

    return row[0]


def legacy_timezone(db, account_id):
    # No canonical typed legacy timezone source exists.
    # A resolved timezone is derived from city; legacy consumer
    # currently has no equivalent persisted field.
    return {
        "state":"unknown",
        "value":None,
        "source":None,
    }


def legacy_business_hours(db, account_id):
    # Factory A derived this from messenger activity.
    # There is no persisted legacy business_hours setting.
    return {
        "state":"unknown",
        "value":None,
        "source":None,
    }


def legacy_schedule_autopilot(db, account_id):
    import factory_readiness as A

    cron=A.client_cron([account_id])

    rows=cron.get(account_id,[])

    for x in rows:
        if x.get("job")=="autopilot":
            return {
                "state":"known",
                "value":{
                    "enabled":True,
                    "cadence":x.get("cadence"),
                },
                "source":"crontab",
            }

    return {
        "state":"unknown",
        "value":None,
        "source":"crontab",
    }


def legacy_schedule_mop(db, account_id):
    import factory_readiness as A

    cron=A.client_cron([account_id])

    rows=cron.get(account_id,[])

    for x in rows:
        if x.get("job")=="mop_poll":
            return {
                "state":"known",
                "value":{
                    "enabled":True,
                    "cadence":x.get("cadence"),
                },
                "source":"crontab",
            }

    return {
        "state":"unknown",
        "value":None,
        "source":"crontab",
    }


def legacy(db, account_id, field):
    if field=="profile.timezone":
        return legacy_timezone(db,account_id)

    if field=="kpi.business_hours":
        return legacy_business_hours(db,account_id)

    if field=="schedule.autopilot":
        return legacy_schedule_autopilot(db,account_id)

    if field=="schedule.mop_poll":
        return legacy_schedule_mop(db,account_id)

    return {
        "state":"unknown",
        "value":None,
        "source":None,
    }


def compare(resolved, legacy_obj):
    if resolved is None:
        return "resolved_unknown"

    if legacy_obj.get("state")!="known":
        return "legacy_unknown"

    if resolved == legacy_obj.get("value"):
        return "same"

    return "different"
