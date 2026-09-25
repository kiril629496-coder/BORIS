import inspect
import unittest

from app.services import prospect_campaigns
from app.services import prospecting


class ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar(self):
        return self.value


class FakeDb:
    def __init__(self, existing_id=42):
        self.calls = []
        self.existing_id = existing_id

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "SELECT id" in sql and "FROM prospect_companies" in sql:
            return ScalarResult(self.existing_id)
        return ScalarResult(None)


class ProspectCompanyDedupeTests(unittest.TestCase):
    def test_create_company_reuses_existing_owner_domain(self):
        db = FakeDb(existing_id=42)
        company_id = prospecting.create_company(
            db,
            2,
            "Повтор компании",
            website="https://www.example.ru/catalog",
            city="Москва",
        )
        self.assertEqual(company_id, 42)
        sql = "\n".join(x[0] for x in db.calls)
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn("lower(domain)=:domain", sql)
        self.assertNotIn("INSERT INTO prospect_companies", sql)

    def test_discovery_serializes_same_domain_before_create(self):
        src = inspect.getsource(prospect_campaigns.discover_niche)
        self.assertIn("lock_company_domain", src)
        self.assertIn("lower(domain)=lower(:d)", src)
        # Each found/inserted domain releases the transaction-scoped lock before
        # the loop can acquire another domain lock.
        self.assertGreaterEqual(src.count("db.commit()"), 3)

    def test_campaign_audience_dedupes_company_domains(self):
        src = inspect.getsource(prospect_campaigns.build_audience)
        self.assertIn("active_company_domains", src)
        self.assertIn("best_by_domain", src)
        self.assertIn("company_domain_skipped", src)

    def test_send_selector_blocks_second_contact_to_same_company_domain(self):
        src = inspect.getsource(prospect_campaigns.tick_campaign)
        self.assertIn("pc.domain company_domain", src)
        self.assertIn("JOIN prospect_companies zc", src)
        self.assertIn("z.sent_at IS NOT NULL", src)

    def test_campaign_email_uniqueness_index_is_part_of_schema(self):
        self.assertIn("ux_pcm_campaign_email_active", prospect_campaigns.SCHEMA)
        self.assertIn("lower(email)", prospect_campaigns.SCHEMA)


if __name__ == "__main__":
    unittest.main()
