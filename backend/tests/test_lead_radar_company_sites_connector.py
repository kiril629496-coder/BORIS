import inspect
import unittest

from app.services.lead_radar.connectors import BorisCompanySitesConnector
from app.services.lead_radar.source_policy import DEFAULT_SOURCE_POLICIES


class LeadRadarCompanySitesConnectorTests(unittest.TestCase):
    def test_company_sites_connector_is_owner_scoped_and_read_only(self):
        self.assertEqual(BorisCompanySitesConnector.group_key, "company_sites")
        self.assertFalse(BorisCompanySitesConnector.supports_outreach)
        self.assertFalse(BorisCompanySitesConnector.outreach_idempotent)
        src = inspect.getsource(BorisCompanySitesConnector.fetch)
        self.assertIn("owner_id=:owner", src)
        self.assertIn("официальный сайт", src)
        self.assertIn("prospect_companies", src)

    def test_company_sites_policy_never_allows_automation(self):
        p = DEFAULT_SOURCE_POLICIES["boris_company_sites"]
        self.assertFalse(p.allows_business_contact)
        self.assertFalse(p.allows_automation)
        self.assertFalse(p.applicant_channel)


if __name__ == "__main__":
    unittest.main()
