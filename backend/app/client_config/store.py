import hashlib, json

DDL = """
CREATE TABLE IF NOT EXISTS client_config (
    id                 SERIAL PRIMARY KEY,
    billing_owner_kind TEXT      NOT NULL DEFAULT 'user',
    billing_owner_id   INTEGER   NOT NULL,
    account_id         VARCHAR(255),
    version            INTEGER   NOT NULL,
    status             TEXT      NOT NULL DEFAULT 'draft',
    schema_version     INTEGER   NOT NULL DEFAULT 1,
    source             TEXT      NOT NULL,
    config             JSONB     NOT NULL,
    content_hash       TEXT      NOT NULL,
    validation_status  TEXT      NOT NULL DEFAULT 'unvalidated',
    validation_report  JSONB,
    validated_at       TIMESTAMP,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    created_by_kind    TEXT      NOT NULL,
    created_by_ref     TEXT      NOT NULL,
    activated_at       TIMESTAMP,
    notes              TEXT,
    CONSTRAINT ck_cc_status     CHECK (status IN ('draft','active','superseded')),
    CONSTRAINT ck_cc_owner_kind CHECK (billing_owner_kind IN ('user','company')),
    CONSTRAINT ck_cc_by_kind    CHECK (created_by_kind IN ('user','cron','boris_auto','migration')),
    CONSTRAINT ck_cc_source     CHECK (source IN ('legacy_collector','ui','api','command')),
    CONSTRAINT ck_cc_valid      CHECK (validation_status IN ('unvalidated','valid','invalid','needs_input')),
    CONSTRAINT ck_cc_version    CHECK (version >= 1),
    CONSTRAINT ck_cc_activated  CHECK (status <> 'active' OR activated_at IS NOT NULL),
    CONSTRAINT ck_cc_collector_draft CHECK (source <> 'legacy_collector' OR status = 'draft')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cc_version ON client_config
    (billing_owner_kind, billing_owner_id, COALESCE(account_id,''), version);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cc_active ON client_config
    (billing_owner_kind, billing_owner_id, COALESCE(account_id,'')) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS uq_cc_collector_hash ON client_config
    (billing_owner_kind, billing_owner_id, COALESCE(account_id,''), content_hash)
    WHERE source = 'legacy_collector';
CREATE INDEX IF NOT EXISTS ix_cc_lookup ON client_config (billing_owner_id, account_id, status);
"""


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
