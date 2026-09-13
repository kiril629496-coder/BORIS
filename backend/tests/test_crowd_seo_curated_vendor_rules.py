from __future__ import annotations

from app.services import crowd_seo_reserve_policy as reserve_policy
from app.services import platform_rules
from app.services import service_marketplace as marketplace


def _inspection(text: str, links: list[str] | None = None) -> dict:
    return {
        "decision": "review",
        "text_excerpt": text,
        "outbound_links": links or [],
    }


CASES = {
    "disc_forum_arcadecontrols_com": {
        "text": (
            "Retail Vendors. Relating to the sales of products from retail and semi-retail hobby vendors. "
            "This board is for commercial/retail vendors. If you've sold more than 3 of whatever it is you sell, "
            "this means you. Vendor www.GameMolding.com."
        ),
        "links": [],
        "required": "use_retail_vendors_board_only",
    },
    "disc_teaforum_org": {
        "text": (
            "Tea/Teaware Vendors. Vendor news and self-promotion. Create your own Vendor or Artisan topic. "
            "Request to join the Vendors or Artisans groups. Membership will remain pending until approved by an Admin. "
            "Provide links to your web site. Only one thread per vendor. Do not promote your company elsewhere on the "
            "forum via links or testimonials. Bulk Rooibos Tea For Sale fairestcapeteacompany.com."
        ),
        "links": [],
        "required": "use_tea_teaware_vendors_board_only",
    },
    "disc_teachat_com": {
        "text": (
            "Tea Vendor Forum Guidelines. Vendors may advertise their teas in the Tea Vendor section of the forum only. "
            "New members with less than 10 posts and less than 30 days membership shall not post links. "
            "Vendors may not place links or e-mail addresses on this forum leading to their businesses outside the Tea "
            "Vendors forum. If you're a vendor reading this PLEASE REACH OUT!"
        ),
        "links": [],
        "required": "minimum_account_age_30_days",
    },
    "disc_forums_deeperblue_com": {
        "text": (
            "Self-promotion or commercial posts require prior permission. The Marketplace. Goods For Sale - this forum "
            "is for commercial advertisements. Do you have an item ready to be sold? Post in here. Register for a free "
            "account. You can gain access to all this absolutely free. MAKO Spearguns."
        ),
        "links": ["https://makospearguns.com/products/kona-knives"],
        "required": "commercial_permission_required_before_first_post",
    },
    "disc_doityourselfchristmas_com": {
        "text": (
            "Vendors Arena. A Place for Companies to show us their products and promote special sales. "
            "The purpose of this forum is for registered companies to promote products that may be useful and utilized "
            "in animated holiday displays. Supporting Membership will remove all Ads."
        ),
        "links": ["https://www.diyledexpress.com/product", "https://3kings.llc/collections/pixels"],
        "required": "registered_company_identity_required",
    },
    "disc_mcarterbrown_com": {
        "text": (
            "Dealers Forum. Paintball Dealer? Having a Special? Let us know here. You will need to register before you "
            "can post. Simply click the Register link above to get started."
        ),
        "links": ["http://www.docsmachine.com"],
        "required": "use_dealers_forum_only",
    },
    "disc_penturners_org": {
        "text": (
            "All use of the Marketplace will be FREE. Personally owned or business sales are allowed. Going forward, "
            "there will be no cost for a Vendor Forum. You must be an IAP member for at least one year. Vendor Forums. "
            "Specials, Tips, and General Information from our Vendors."
        ),
        "links": [],
        "required": "minimum_account_age_365_days",
    },
    "diyaudio_goods": {
        "text": (
            "We have two free forums for advertising your wares. Vendors Bazaar. For commercial advertisements the "
            "Vendors Bazaar is the right forum to tell the world about your products. It's currently free to post in "
            "the Vendors Bazaar."
        ),
        "links": [],
        "required": "use_vendors_bazaar_only",
    },
}


def test_curated_vendor_rules_require_fresh_complete_evidence_and_emit_strict_marker():
    for key, case in CASES.items():
        decision, requirements = platform_rules._curated_goods_vendor_rule_override(
            key,
            [_inspection(case["text"], case["links"])],
            "review",
            [],
        )
        assert decision == "allowed", key
        assert "curated_vendor_surface_verified" in requirements, key
        assert case["required"] in requirements, key
        assert reserve_policy.rule_counts_for_reserve({
            "decision": decision,
            "requirements": requirements,
            "inspections": [{"decision": "review"}],
        }), key


def test_curated_vendor_rule_fails_closed_when_evidence_is_incomplete():
    for key, case in CASES.items():
        decision, requirements = platform_rules._curated_goods_vendor_rule_override(
            key,
            [_inspection("generic marketplace page")],
            "allowed",
            ["use_exact_discovered_goods_surface_only"],
        )
        assert decision == "review", key
        assert "curated_vendor_surface_verified" not in requirements, key


def test_generic_dynamic_allowed_still_does_not_count_without_inspection_evidence():
    assert not reserve_policy.rule_counts_for_reserve({
        "decision": "allowed",
        "requirements": ["use_exact_discovered_goods_surface_only"],
        "inspections": [{"decision": "review"}],
    })


def test_curated_vendor_surfaces_are_narrow_and_have_maturity_rules():
    keys = set(CASES)
    live_keys = {p.key for p in marketplace.all_platform_objects()}
    assert "diyaudio_goods" in live_keys
    for key in keys:
        assert key in marketplace.VERIFIED_PUBLICATION_SURFACES
        surfaces = marketplace.VERIFIED_PUBLICATION_SURFACES[key]
        assert surfaces
        assert any("goods" in (surface.get("niches") or []) for surface in surfaces)
        assert key in marketplace.PLATFORM_MATURITY_REQUIREMENTS
        for surface in surfaces:
            assert surface.get("required_terms"), key
