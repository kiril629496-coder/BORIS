import unittest

from app.services import kpi_lead_quality as q


class _Result:
    def __init__(self, rows):
        self._rows=list(rows)
    def first(self):
        return self._rows[0] if self._rows else None
    def fetchall(self):
        return list(self._rows)


class _DB:
    def __init__(self, account_row, facts=None):
        self.account_row=account_row
        self.facts=list(facts or [])
    def execute(self, stmt, params=None):
        sql=str(stmt)
        if "FROM accounts" in sql:
            return _Result([self.account_row])
        if "FROM client_facts" in sql:
            return _Result([(x,) for x in self.facts])
        raise AssertionError(sql)


class KpiLeadQualityAccountModeTests(unittest.TestCase):
    def test_personal_data_rule_is_not_recruitment(self):
        db=_DB(
            ("Аренда бытовок", "", "выяснить задачу клиента"),
            ["Не проси присылать паспорт и другие персональные данные в чате."],
        )
        self.assertFalse(q._account_is_recruitment(db, "rent"))

    def test_workers_in_business_context_are_not_recruitment(self):
        db=_DB(
            ("Аренда бытовок", "", "подобрать бытовку"),
            ["Бытовка для работников на строительном объекте."],
        )
        self.assertFalse(q._account_is_recruitment(db, "rent"))

    def test_explicit_personnel_recruitment_is_recruitment(self):
        db=_DB(("Подбор персонала", "", "поиск кандидатов"), [])
        self.assertTrue(q._account_is_recruitment(db, "jobs"))

    def test_vacancy_account_is_recruitment(self):
        db=_DB(("Вакансии РФ", "", "получать отклики"), [])
        self.assertTrue(q._account_is_recruitment(db, "jobs"))

    def test_expired_kpi_read_model_cannot_claim_recovery_is_running(self):
        from pathlib import Path
        src=Path("app/api/avito.py").read_text(encoding="utf-8")
        start=src.index("def kpi_check(")
        block=src[start:src.index("\n\n\n\n@router.get(\"/kpi_root_cause\")", start)]
        self.assertIn("KPI_CHECK_EXPIRED_PERIOD_TRUTH_V1", block)
        self.assertIn('suggested_action = "service_period_expired"', block)
        self.assertIn("новые KPI-действия", block)


if __name__ == "__main__":
    unittest.main()
