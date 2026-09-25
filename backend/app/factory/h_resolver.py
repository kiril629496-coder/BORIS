"""
BORIS Factory H — FACTORY_H_V1.

Purpose:
    Resolve internal fields previously marked SKIPPED_UNRESOLVED.

V1:
    profile.timezone

Evidence policy:
    structured sources only.

Allowed:
    client_facts explicit city/region fact
    client_config explicit structured city/region column
    storage explicit city/region keys or structured profile JSON
    catalog_products.geo structured JSON

Forbidden:
    account_id/name inference
    free-text NLP
    external APIs
    guessing
    Client Config writes
    money/messages/publishing/CPX/KPI/autopilot

Writes:
    factory_resolved_values only.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import inspect, text

from app.factory import fact_intake as J

FACTORY_J_SOURCE_V1 = True


FACTORY_H_V1 = True

FIELD = "profile.timezone"

MIN_CONFIDENCE = 0.90

RESOLUTION_KIND = "H_STRUCTURED_GEO"


# Exact deterministic mapping only.
GEO_TZ = {
    # UTC+3
    "москва": "Europe/Moscow",
    "moscow": "Europe/Moscow",
    "московская область": "Europe/Moscow",
    "московская обл": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow",
    "санкт петербург": "Europe/Moscow",
    "спб": "Europe/Moscow",
    "ленинградская область": "Europe/Moscow",
    "пенза": "Europe/Moscow",
    "пензенская область": "Europe/Moscow",
    "калуга": "Europe/Moscow",
    "калужская область": "Europe/Moscow",
    "краснодар": "Europe/Moscow",
    "краснодарский край": "Europe/Moscow",
    "сочи": "Europe/Moscow",
    "адлер": "Europe/Moscow",
    "ростов-на-дону": "Europe/Moscow",
    "ростов на дону": "Europe/Moscow",
    "ростовская область": "Europe/Moscow",
    "воронеж": "Europe/Moscow",
    "воронежская область": "Europe/Moscow",
    "белгород": "Europe/Moscow",
    "тверь": "Europe/Moscow",
    "тула": "Europe/Moscow",
    "рязань": "Europe/Moscow",
    "ярославль": "Europe/Moscow",
    "владимир": "Europe/Moscow",
    "ставрополь": "Europe/Moscow",
    "махачкала": "Europe/Moscow",

    # UTC+4
    "самара": "Europe/Samara",
    "самарская область": "Europe/Samara",
    "саратов": "Europe/Saratov",
    "саратовская область": "Europe/Saratov",
    "ульяновск": "Europe/Ulyanovsk",
    "астрахань": "Europe/Astrakhan",
    "астраханская область": "Europe/Astrakhan",

    # UTC+5
    "екатеринбург": "Asia/Yekaterinburg",
    "свердловская область": "Asia/Yekaterinburg",
    "челябинск": "Asia/Yekaterinburg",
    "челябинская область": "Asia/Yekaterinburg",
    "уфа": "Asia/Yekaterinburg",
    "республика башкортостан": "Asia/Yekaterinburg",
    "пермь": "Asia/Yekaterinburg",
    "пермский край": "Asia/Yekaterinburg",
    "тюмень": "Asia/Yekaterinburg",
    "тюменская область": "Asia/Yekaterinburg",

    # UTC+6
    "омск": "Asia/Omsk",
    "омская область": "Asia/Omsk",

    # UTC+7
    "новосибирск": "Asia/Novosibirsk",
    "новосибирская область": "Asia/Novosibirsk",
    "томск": "Asia/Tomsk",
    "томская область": "Asia/Tomsk",
    "красноярск": "Asia/Krasnoyarsk",
    "красноярский край": "Asia/Krasnoyarsk",
    "кемерово": "Asia/Novokuznetsk",
    "кемеровская область": "Asia/Novokuznetsk",
    "барнаул": "Asia/Barnaul",
    "алтайский край": "Asia/Barnaul",

    # Armenia
    "ереван": "Asia/Yerevan",
    "yerevan": "Asia/Yerevan",
    "армения": "Asia/Yerevan",
    "armenia": "Asia/Yerevan",
}


def _json(value):
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


def stable_hash(value):
    raw=json.dumps(
        _json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def norm(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value,(dict,list,bool,int,float)):
        return None

    s=str(value).strip().lower()

    if not s:
        return None

    s=s.replace("ё","е")
    s=" ".join(s.split())

    for prefix in (
        "г. ",
        "г ",
        "город ",
    ):
        if s.startswith(prefix):
            s=s[len(prefix):].strip()

    return s


def timezone_for(value):
    n=norm(value)

    if not n:
        return None

    return GEO_TZ.get(n)


def table_exists(db, name):
    return bool(
        db.execute(
            text("SELECT to_regclass(:n)"),
            {"n":"public."+name},
        ).scalar()
    )


def cols(db, table):
    if not table_exists(db,table):
        return set()

    return {
        c["name"]
        for c in inspect(db.bind).get_columns(table)
    }


def scalar(value):
    if value is None:
        return None

    if isinstance(value,str):
        s=value.strip()

        if not s:
            return None

        try:
            obj=json.loads(s)
        except Exception:
            return s

        return scalar(obj)

    if isinstance(value,dict):
        for key in (
            "value",
            "candidate_value",
            "city",
            "region",
            "locality",
        ):
            if key in value:
                result=scalar(value[key])

                if result:
                    return result

    return None


def geo_from_obj(obj):
    if isinstance(obj,str):
        try:
            obj=json.loads(obj)
        except Exception:
            return None

    if not isinstance(obj,dict):
        return None

    paths=[]

    for key in (
        "city",
        "region",
        "locality",
        "settlement",
    ):
        if key in obj:
            paths.append(obj[key])

    for parent in (
        "profile",
        "location",
        "geo",
        "address",
    ):
        child=obj.get(parent)

        if isinstance(child,dict):
            for key in (
                "city",
                "region",
                "locality",
                "settlement",
            ):
                if key in child:
                    paths.append(child[key])

    for value in paths:
        result=scalar(value)

        if result:
            return result

    return None


def add(out, value, source, quality):
    if value is None:
        return

    tz=timezone_for(value)

    out.append({
        "geo":value,
        "normalized":norm(value),
        "timezone":tz,
        "source":source,
        "quality":quality,
    })


def from_client_facts(db, account_id):
    out=[]

    c=cols(db,"client_facts")

    if not {
        "account_id",
        "name",
        "value",
    }.issubset(c):
        return out

    sql="""
        SELECT name,value
        FROM client_facts
        WHERE account_id=:a
          AND lower(name) IN (
              'city',
              'profile.city',
              'profile_city',
              'город',
              'region',
              'profile.region',
              'регион'
          )
    """

    if "status" in c:
        sql += """
          AND status IN (
              'confirmed',
              'active'
          )
        """

    if "id" in c:
        sql += " ORDER BY id"

    for name,value in db.execute(
        text(sql),
        {"a":account_id},
    ):
        add(
            out,
            scalar(value),
            "client_facts:%s" % name,
            "confirmed_structured_fact",
        )

    return out


def from_storage(db, account_id):
    out=[]

    c=cols(db,"storage")

    if not {
        "account_id",
        "key",
        "value",
    }.issubset(c):
        return out

    order=" ORDER BY id" if "id" in c else ""

    rows=db.execute(
        text("""
            SELECT key,value
            FROM storage
            WHERE account_id=:a
        """ + order),
        {"a":account_id},
    ).fetchall()

    direct={
        "city",
        "region",
        "profile_city",
        "profile_region",
        "seller_city",
        "seller_region",
        "avito_city",
        "avito_region",
        "factory_profile_city",
    }

    structured={
        "profile",
        "parsed_profile",
        "onboarding_profile",
        "seller_defaults",
        "feed_prefs",
    }

    for key,value in rows:
        if key in direct:
            add(
                out,
                scalar(value),
                "storage:%s" % key,
                "explicit_storage_field",
            )

        elif key in structured:
            add(
                out,
                geo_from_obj(value),
                "storage:%s" % key,
                "structured_storage_json",
            )

    return out


def from_catalog(db, account_id):
    out=[]

    c=cols(db,"catalog_products")

    if not {
        "account_id",
        "geo",
    }.issubset(c):
        return out

    sql="""
        SELECT geo
        FROM catalog_products
        WHERE account_id=:a
    """

    if "deleted_at" in c:
        sql += " AND deleted_at IS NULL"

    if "id" in c:
        sql += " ORDER BY id"

    for (geo,) in db.execute(
        text(sql),
        {"a":account_id},
    ):
        add(
            out,
            geo_from_obj(geo),
            "catalog_products.geo",
            "structured_product_geo",
        )

    return out


def from_client_config(db, account_id):
    """
    Read-only and conservative:
    only explicit direct city/region columns.
    No assumptions about opaque config blobs.
    """
    out=[]

    c=cols(db,"client_config")

    if "account_id" not in c:
        return out

    candidates=[
        x for x in (
            "city",
            "region",
            "profile_city",
            "profile_region",
        )
        if x in c
    ]

    if not candidates:
        return out

    select=", ".join(candidates)

    sql=f"""
        SELECT {select}
        FROM client_config
        WHERE account_id=:a
    """

    if "id" in c:
        sql += " ORDER BY id DESC"

    sql += " LIMIT 1"

    row=db.execute(
        text(sql),
        {"a":account_id},
    ).mappings().first()

    if not row:
        return out

    for key in candidates:
        add(
            out,
            scalar(row.get(key)),
            "client_config:%s" % key,
            "explicit_config_field",
        )

    return out


def from_factory_j(db, account_id):
    out=[]

    fact=J.get_fact(
        db,
        account_id,
        "geo.city_or_region",
    )

    if not fact:
        return out

    add(
        out,
        fact["value"],
        "factory_j:%s:%s"
        % (
            fact["source_type"],
            fact["source_ref"],
        ),
        "controlled_fact_intake",
    )

    return out


def collect(db, account_id):
    values=[]

    values.extend(
        from_factory_j(db,account_id)
    )

    values.extend(
        from_client_facts(db,account_id)
    )

    values.extend(
        from_client_config(db,account_id)
    )

    values.extend(
        from_storage(db,account_id)
    )

    values.extend(
        from_catalog(db,account_id)
    )

    # deterministic dedupe
    seen=set()
    out=[]

    for x in values:
        key=(
            x["normalized"],
            x["source"],
        )

        if key in seen:
            continue

        seen.add(key)
        out.append(x)

    return out


def unresolved_rows(db):
    if not table_exists(
        db,
        "factory_execution_state",
    ):
        return []

    return [
        dict(x)
        for x in db.execute(text("""
            SELECT
                account_id,
                field
            FROM factory_execution_state
            WHERE status='SKIPPED_UNRESOLVED'
            ORDER BY account_id,field
        """)).mappings()
    ]


def resolve(db, account_id, field):
    if field != FIELD:
        return {
            "account_id":account_id,
            "field":field,
            "status":"UNSUPPORTED",
            "value":None,
            "confidence":0.0,
            "evidence":{
                "reason":"field_not_supported_by_H_v1",
            },
        }

    evidence=collect(
        db,
        account_id,
    )

    usable=[
        x for x in evidence
        if x["timezone"]
    ]

    if not usable:
        return {
            "account_id":account_id,
            "field":field,
            "status":"UNRESOLVED",
            "value":None,
            "confidence":0.0,
            "evidence":{
                "reason":"no_structured_mappable_geo",
                "observed":evidence,
            },
        }

    groups=defaultdict(list)

    for x in usable:
        groups[x["timezone"]].append(x)

    if len(groups) != 1:
        return {
            "account_id":account_id,
            "field":field,
            "status":"CONFLICT",
            "value":None,
            "confidence":0.0,
            "evidence":{
                "reason":"structured_sources_conflict",
                "observed":usable,
            },
        }

    value=next(iter(groups))
    support=groups[value]

    source_families={
        x["source"].split(":")[0]
        for x in support
    }

    confidence=(
        0.98
        if len(source_families) >= 2
        else 0.90
    )

    return {
        "account_id":account_id,
        "field":field,
        "status":"RESOLVED",
        "value":value,
        "confidence":confidence,
        "evidence":{
            "reason":"structured_geo_consensus",
            "support":support,
        },
    }


def build(db):
    results=[]

    for row in unresolved_rows(db):
        item=resolve(
            db,
            row["account_id"],
            row["field"],
        )

        item["source_fingerprint"]=stable_hash({
            "account_id":item["account_id"],
            "field":item["field"],
            "status":item["status"],
            "value":item["value"],
            "confidence":item["confidence"],
            "evidence":item["evidence"],
        })

        results.append(item)

    return results


def existing_rows(db, account_id, field):
    if not table_exists(
        db,
        "factory_resolved_values",
    ):
        raise RuntimeError(
            "factory_resolved_values table missing"
        )

    return [
        dict(x)
        for x in db.execute(text("""
            SELECT *
            FROM factory_resolved_values
            WHERE account_id=:a
              AND field=:f
            ORDER BY id
        """),{
            "a":account_id,
            "f":field,
        }).mappings()
    ]


def apply(db):
    items=build(db)

    inserted=0
    updated=0
    unchanged=0
    unresolved=0
    conflicts=0

    for item in items:

        if item["status"]=="UNRESOLVED":
            unresolved += 1
            continue

        if item["status"]=="CONFLICT":
            conflicts += 1
            continue

        if item["status"]!="RESOLVED":
            continue

        rows=existing_rows(
            db,
            item["account_id"],
            item["field"],
        )

        if len(rows) > 1:
            raise RuntimeError(
                "duplicate resolved values for %s/%s"
                % (
                    item["account_id"],
                    item["field"],
                )
            )

        payload={
            "a":item["account_id"],
            "f":item["field"],
            "v":json.dumps(
                item["value"],
                ensure_ascii=False,
            ),
            "s":"Factory H structured geo",
            "c":item["confidence"],
            "k":RESOLUTION_KIND,
            "e":json.dumps(
                item["evidence"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "fp":item["source_fingerprint"],
        }

        if not rows:
            db.execute(text("""
                INSERT INTO factory_resolved_values (
                    account_id,
                    field,
                    value,
                    source,
                    confidence,
                    resolution_kind,
                    evidence,
                    source_fingerprint,
                    created_at,
                    updated_at
                )
                VALUES (
                    :a,
                    :f,
                    CAST(:v AS JSONB),
                    :s,
                    :c,
                    :k,
                    CAST(:e AS JSONB),
                    :fp,
                    NOW(),
                    NOW()
                )
            """),payload)

            inserted += 1
            continue

        old=rows[0]

        # Do not overwrite another resolver's authority.
        if old["resolution_kind"] != RESOLUTION_KIND:
            unchanged += 1
            continue

        if (
            old["source_fingerprint"]
            == item["source_fingerprint"]
        ):
            unchanged += 1
            continue

        db.execute(text("""
            UPDATE factory_resolved_values
            SET
                value=CAST(:v AS JSONB),
                source=:s,
                confidence=:c,
                resolution_kind=:k,
                evidence=CAST(:e AS JSONB),
                source_fingerprint=:fp,
                updated_at=NOW()
            WHERE account_id=:a
              AND field=:f
              AND resolution_kind=:k
        """),payload)

        updated += 1

    return {
        "items":items,
        "inserted":inserted,
        "updated":updated,
        "unchanged":unchanged,
        "unresolved":unresolved,
        "conflicts":conflicts,
    }


def get_value(db, account_id, field):
    if not table_exists(
        db,
        "factory_resolved_values",
    ):
        return None

    rows=[
        dict(x)
        for x in db.execute(text("""
            SELECT
                value,
                source,
                confidence,
                resolution_kind,
                evidence,
                source_fingerprint
            FROM factory_resolved_values
            WHERE account_id=:a
              AND field=:f
            ORDER BY
                confidence DESC NULLS LAST,
                updated_at DESC,
                id DESC
        """),{
            "a":account_id,
            "f":field,
        }).mappings()
    ]

    if not rows:
        return None

    best=rows[0]

    if float(best.get("confidence") or 0) < MIN_CONFIDENCE:
        return None

    if best.get("value") in (None,""):
        return None

    return best
