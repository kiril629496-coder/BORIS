import hashlib, json

DDL = 'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'


def content_hash(config):
    canon = json.dumps(config, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def ensure_schema(conn):
    cu = conn.cursor()
    cu.execute(DDL)
    conn.commit()


def save_draft(conn, owner_kind, owner_id, account_id, config,
               validation_status, validation_report):
    """Idempotent: same content for same scope is never stored twice."""
    h = content_hash(config)
    cu = conn.cursor()
    cu.execute(
        "SELECT id, version FROM client_config"
        " WHERE billing_owner_kind=%s AND billing_owner_id=%s"
        "   AND COALESCE(account_id,'')=COALESCE(%s,'')"
        "   AND source='legacy_collector' AND content_hash=%s",
        (owner_kind, owner_id, account_id, h))
    row = cu.fetchone()
    if row:
        return ("unchanged", row[1])
    cu.execute(
        "SELECT COALESCE(MAX(version),0) FROM client_config"
        " WHERE billing_owner_kind=%s AND billing_owner_id=%s"
        "   AND COALESCE(account_id,'')=COALESCE(%s,'')",
        (owner_kind, owner_id, account_id))
    ver = cu.fetchone()[0] + 1
    try:
        cu.execute(
            "INSERT INTO client_config (billing_owner_kind, billing_owner_id, account_id,"
            " version, status, schema_version, source, config, content_hash,"
            " validation_status, validation_report, validated_at,"
            " created_by_kind, created_by_ref, notes)"
            " VALUES (%s,%s,%s,%s,'draft',%s,'legacy_collector',%s,%s,%s,%s,now(),"
            " 'migration','legacy_collector',%s)",
            (owner_kind, owner_id, account_id, ver, config.get("schema_version", 1),
             json.dumps(config, ensure_ascii=False, default=str), h,
             validation_status, json.dumps(validation_report, ensure_ascii=False, default=str),
             "collected from legacy sources"))
        conn.commit()
        return ("inserted", ver)
    except Exception as e:
        conn.rollback()
        return ("conflict:" + type(e).__name__, ver)
