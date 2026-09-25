"""
BORIS authoritative account evidence.

READ ONLY.

Sources are the real current production tables:
    accounts
    account_slots
    catalog_products
    catalog_facts
    client_facts
    messenger_prompts
    campaigns
    campaign_items
    media_assets
    media_links
    banners
    photo_sessions
    photo_shots
    storage

This module does not:
    - write DB
    - call Avito
    - publish
    - call OAuth
    - send messages
    - spend money
"""

from sqlalchemy import text


def _scalar(db, sql, params):
    return int(
        db.execute(
            text(sql),
            params,
        ).scalar()
        or 0
    )


def _owner_id(db, account_id):
    return db.execute(
        text("""
            SELECT owner_user_id
            FROM accounts
            WHERE account_id=:a
            LIMIT 1
        """),
        {"a": account_id},
    ).scalar()


def build_snapshot(db, account_id):
    """
    Build deterministic read-only snapshot for one account.
    """

    owner_id = _owner_id(db, account_id)

    if owner_id is None:
        return {
            "source": "authoritative_snapshot",
            "account_id": account_id,
            "found": False,
        }

    # ------------------------------------------------------------------
    # ACCOUNT
    # ------------------------------------------------------------------

    account = db.execute(
        text("""
            SELECT
                account_id,
                owner_user_id,
                name,
                company_niche,
                company_website,
                avito_user_id,
                billing_mode,
                client_goal
            FROM accounts
            WHERE account_id=:a
            LIMIT 1
        """),
        {"a": account_id},
    ).mappings().first()

    # ------------------------------------------------------------------
    # ENTITLEMENT / CONNECTION SLOTS
    # ------------------------------------------------------------------

    slots = db.execute(
        text("""
            SELECT
                id,
                owner_user_id,
                product,
                slot_no,
                status,
                account_id,
                avito_user_id,
                account_name,
                tariff_ok,
                paid_until,
                sync_status,
                last_sync_at,
                last_sync_error
            FROM account_slots
            WHERE account_id=:a
            ORDER BY slot_no, id
        """),
        {"a": account_id},
    ).mappings().all()

    # ------------------------------------------------------------------
    # CATALOG PRODUCTS
    # ------------------------------------------------------------------

    products = db.execute(
        text("""
            SELECT
                id,
                owner_user_id,
                account_id,
                sku,
                name,
                kind,
                category,
                avito_category_id,
                status,
                description,
                price,
                price_from,
                price_to,
                currency,
                attrs,
                tags,
                geo,
                source,
                source_ref
            FROM catalog_products
            WHERE account_id=:a
              AND deleted_at IS NULL
            ORDER BY id
        """),
        {"a": account_id},
    ).mappings().all()

    # ------------------------------------------------------------------
    # CATALOG FACTS
    # ------------------------------------------------------------------

    catalog_facts = _scalar(
        db,
        """
        SELECT count(*)
        FROM catalog_facts
        WHERE owner_user_id=:o
          AND deleted_at IS NULL
        """,
        {"o": owner_id},
    )

    # ------------------------------------------------------------------
    # CLIENT FACTS
    # ------------------------------------------------------------------

    client_facts = _scalar(
        db,
        """
        SELECT count(*)
        FROM client_facts
        WHERE account_id=:a
        """,
        {"a": account_id},
    )

    # ------------------------------------------------------------------
    # MESSENGER PROMPTS
    # ------------------------------------------------------------------

    messenger_prompts = _scalar(
        db,
        """
        SELECT count(*)
        FROM messenger_prompts
        WHERE account_id=:a
          AND is_active
        """,
        {"a": account_id},
    )

    # ------------------------------------------------------------------
    # CAMPAIGNS
    # ------------------------------------------------------------------

    campaigns = _scalar(
        db,
        """
        SELECT count(*)
        FROM campaigns
        WHERE default_account_id=:a
        """,
        {"a": account_id},
    )

    # ------------------------------------------------------------------
    # CAMPAIGN ITEMS
    # ------------------------------------------------------------------

    campaign_items = db.execute(
        text("""
            SELECT
                id,
                campaign_id,
                account_id,
                external_item_id,
                segment,
                intent,
                geo,
                offer,
                status,
                readiness_avito,
                readiness_sales,
                product_id,
                variant_id,
                hero_media_id
            FROM campaign_items
            WHERE account_id=:a
            ORDER BY id
        """),
        {"a": account_id},
    ).mappings().all()

    # ------------------------------------------------------------------
    # MEDIA ASSETS
    # ------------------------------------------------------------------

    media_assets = _scalar(
        db,
        """
        SELECT count(*)
        FROM media_assets
        WHERE owner_user_id=:o
          AND deleted_at IS NULL
        """,
        {"o": owner_id},
    )

    # ------------------------------------------------------------------
    # MEDIA LINKS
    # ------------------------------------------------------------------

    media_links = _scalar(
        db,
        """
        SELECT count(*)
        FROM media_links ml
        WHERE EXISTS (
            SELECT 1
            FROM media_assets ma
            WHERE ma.id=ml.media_id
              AND ma.owner_user_id=:o
              AND ma.deleted_at IS NULL
        )
        """,
        {"o": owner_id},
    )

    # ------------------------------------------------------------------
    # BANNERS
    # ------------------------------------------------------------------

    banners = _scalar(
        db,
        """
        SELECT count(*)
        FROM banners
        WHERE account_id=:a
        """,
        {"a": account_id},
    )

    # ------------------------------------------------------------------
    # PHOTO SESSIONS
    # ------------------------------------------------------------------

    photo_sessions = _scalar(
        db,
        """
        SELECT count(*)
        FROM photo_sessions
        WHERE account_id=:a
        """,
        {"a": account_id},
    )

    # ------------------------------------------------------------------
    # PHOTO SHOTS
    # ------------------------------------------------------------------

    photo_shots = _scalar(
        db,
        """
        SELECT count(*)
        FROM photo_shots ps
        WHERE EXISTS (
            SELECT 1
            FROM photo_sessions s
            WHERE s.id=ps.session_id
              AND s.account_id=:a
        )
        """,
        {"a": account_id},
    )

    # ------------------------------------------------------------------
    # LEGACY STORAGE FEED
    # ------------------------------------------------------------------

    feed_items_storage = _scalar(
        db,
        """
        SELECT count(*)
        FROM storage
        WHERE account_id=:a
          AND key='feed_items'
        """,
        {"a": account_id},
    )

    feed_prefs_storage = _scalar(
        db,
        """
        SELECT count(*)
        FROM storage
        WHERE account_id=:a
          AND key='feed_prefs'
        """,
        {"a": account_id},
    )

    # ------------------------------------------------------------------
    # DERIVED EVIDENCE
    # ------------------------------------------------------------------

    categories = [
        {
            "product_id": p["id"],
            "category": p["category"],
            "avito_category_id": p["avito_category_id"],
        }
        for p in products
        if p["category"] or p["avito_category_id"]
    ]

    items_ready_avito = [
        x for x in campaign_items
        if bool(x["readiness_avito"])
    ]

    items_ready_sales = [
        x for x in campaign_items
        if bool(x["readiness_sales"])
    ]

    return {
        "source": "authoritative_snapshot",
        "version": 1,
        "found": True,

        "account": dict(account),

        "ownership": {
            "owner_user_id": owner_id,
            "account_owner_verified": True,
        },

        "slots": {
            "count": len(slots),
            "rows": [dict(x) for x in slots],
        },

        "catalog": {
            "product_count": len(products),
            "products": [dict(x) for x in products],
            "category_count": len(categories),
            "categories": categories,
            "catalog_facts": catalog_facts,
        },

        "content": {
            "client_facts": client_facts,
            "messenger_prompts": messenger_prompts,
        },

        "campaigns": {
            "campaign_count": campaigns,
            "item_count": len(campaign_items),
            "items": [dict(x) for x in campaign_items],
            "ready_avito": len(items_ready_avito),
            "ready_sales": len(items_ready_sales),
        },

        "media": {
            "media_assets": media_assets,
            "media_links": media_links,
            "banners": banners,
            "photo_sessions": photo_sessions,
            "photo_shots": photo_shots,
        },

        "feed": {
            "storage_feed_items": feed_items_storage,
            "storage_feed_prefs": feed_prefs_storage,
            "present": bool(
                feed_items_storage or feed_prefs_storage
            ),
        },

        "safety": {
            "read_only": True,
            "db_write": False,
            "avito_write": False,
            "oauth": False,
            "paid_action": False,
        },
    }
