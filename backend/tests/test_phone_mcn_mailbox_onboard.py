import importlib.util
import json
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phone-mcn-mailbox-onboard.py"
SPEC = importlib.util.spec_from_file_location("phone_mcn_mailbox_onboard", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(M)


class FakeImap:
    def __init__(self, *args, **kwargs):
        self.logged = False
        self.readonly = None
        self.fetch_queries = []
        self.messages = {}

    def login(self, username, password):
        self.logged = True
        return "OK", [b"ok"]

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "search":
            ids = b" ".join(str(x).encode() for x in sorted(self.messages))
            return "OK", [ids]
        if command == "fetch":
            uid = int(args[0])
            self.fetch_queries.append(args[1])
            raw = self.messages[uid]
            return "OK", [(b"BODY[]", raw)]
        raise AssertionError(command)

    def logout(self):
        return "OK", [b"bye"]


def registration_message(
    uid="42",
    subject="Настройки MCN SIP",
    registrar="sip.mcn.ru",
    username="1234567",
    password="SuperSecretValue",
    did="+7 999 123-45-67",
    sender="MCN Manager <manager@mcn.ru>",
):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "owner@example.test"
    msg["Subject"] = subject
    msg.set_content(
        f"Сервер SIP: {registrar}\n"
        f"Логин SIP: {username}\n"
        f"Пароль SIP: {password}\n"
        f"Номер MCN: {did}\n"
    )
    return msg.as_bytes()


def company_card_required_message(sender="MCN Manager <ava@mcn.ru>"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "owner@example.test"
    msg["Date"] = "Mon, 7 Sep 2026 16:07:30 +0300"
    msg["Message-ID"] = "<company-card-request@mcn.ru>"
    msg["Subject"] = "RE: RE: Партнерская программа"
    msg.set_content(
        "Пришлите уже, пожалуйста, карточку компании. Мы все вам подготовим, "
        "как по клиентскому, так и по Агентскому договору. "
        "Я не могу Вам выгрузить проект договора, пока не заполню данные о Вашей компании. "
        "Вы можете пользоваться сервисом как по оферте, так и по договору подписанного через ЭДО.\n"
    )
    return msg.as_bytes()


def offer_selected_with_quoted_history_message(sender="MCN Manager <ava@mcn.ru>"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "owner@example.test"
    msg["Date"] = "Tue, 8 Sep 2026 11:59:05 +0300"
    msg["Message-ID"] = "<offer-selected@mcn.ru>"
    msg["Subject"] = "RE: Партнерская программа"
    msg.set_content(
        "Тогда работаем по оферте\n\n"
        "С уважением, Владимир Ан\n"
        "From: Кирилл Остапенко <owner@example.test>\n"
        "Sent: Tuesday, September 8, 2026 11:22 AM\n"
        "To: ava@mcn.ru\n"
        "Subject: Re: Партнерская программа\n\n"
        "Добрый день. Во вложении счет на пополнение баланса + оферта на оказание услуг связи. "
        "Счет важно оплатить с Вашего расчетного счета компании, т.к. это элемент верификации Абонента. "
        "В личном кабинете MCN Вам нужно будет внести данные представителя компании. "
        "Пришлите карточку компании для подготовки договора.\n"
    )
    return msg.as_bytes()


def contract_required_message(sender="MCN Manager <ava@mcn.ru>"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "owner@example.test"
    msg["Date"] = "Mon, 7 Sep 2026 09:31:46 +0300"
    msg["Message-ID"] = "<contract-request@mcn.ru>"
    msg["Subject"] = "RE: RE: Партнерская программа"
    msg.set_content(
        "Настройки телефонии до заключения договора выдать Вам не сможем.\n"
        "Для оформления договора потребуются: карточка компании или ИП, "
        "скан св-ва о регистрации компании или лист записи о регистрации юр лица, "
        "решение о назначении директора.\n"
    )
    return msg.as_bytes()


class PhoneMcnMailboxOnboardTests(unittest.TestCase):
    def test_extract_registration_candidate(self):
        out = M.extract_candidate(registration_message(), "42")
        self.assertIsNotNone(out)
        self.assertEqual(out["auth_mode"], "registration")
        self.assertEqual(out["registrar"], "sip.mcn.ru")
        self.assertEqual(out["username"], "1234567")
        self.assertEqual(out["password"], "SuperSecretValue")
        self.assertTrue(out["did"].startswith("+7"))

    def test_public_candidate_never_returns_secret_values(self):
        raw = registration_message(
            subject="SIP sip.mcn.ru 1234567 +79991234567 SuperSecretValue",
            did="+79991234567",
        )
        out = M.extract_candidate(raw, "42")
        public = M.public_candidate(out)
        serialized = json.dumps(public, ensure_ascii=False)
        for secret in ("sip.mcn.ru", "1234567", "+79991234567", "SuperSecretValue"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("password", public)
        self.assertNotIn("username", public)
        self.assertNotIn("registrar", public)

    def test_company_card_reply_from_mcn_is_latest_safe_action(self):
        out = M.classify_operational_mcn_reply(company_card_required_message(), "302843")
        self.assertIsNotNone(out)
        self.assertEqual(out["code"], "mcn_company_card_required")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["requirements"], ["company_or_ip_card"])
        self.assertEqual(out["message_id"], "<company-card-request@mcn.ru>")
        self.assertEqual(out["sender_email"], "ava@mcn.ru")
        self.assertEqual(out["provider_path"][0], "confirm_current_company_card")
        serialized = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("Пришлите уже", serialized)
        self.assertNotIn("проект договора", serialized)

    def test_offer_selected_message_wins_over_quoted_older_stage(self):
        out = M.classify_operational_mcn_reply(
            offer_selected_with_quoted_history_message(), "302913"
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["code"], "mcn_offer_payment_and_representative_required")
        self.assertTrue(out["owner_action_required"])
        self.assertIn("initial_invoice_from_company_account", out["requirements"])
        self.assertIn("company_representative_in_mcn_cabinet", out["requirements"])

    def test_contract_required_reply_from_mcn_is_safe_classified(self):
        out = M.classify_operational_mcn_reply(contract_required_message(), "302788")
        self.assertIsNotNone(out)
        self.assertEqual(out["code"], "mcn_contract_required_before_credentials")
        self.assertTrue(out["owner_action_required"])
        self.assertEqual(out["sender_domain"], "mcn.ru")
        self.assertEqual(out["sender_email"], "ava@mcn.ru")
        self.assertEqual(
            out["requirements"],
            ["company_or_ip_card", "registration_document", "director_appointment_decision"],
        )
        serialized = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("Настройки телефонии", serialized)
        self.assertNotIn("скан св-ва", serialized)

    def test_contract_required_text_from_non_mcn_sender_is_ignored(self):
        out = M.classify_operational_mcn_reply(
            contract_required_message("Someone <attacker@example.test>"), "7"
        )
        self.assertIsNone(out)

    def test_extract_ip_auth_candidate(self):
        msg = EmailMessage()
        msg["From"] = "MCN Manager <manager@mcn.ru>"
        msg["Subject"] = "Настройки телефонии"
        msg.set_content(
            "IP-адрес: 85.94.32.2/32\n"
            "Телефонный номер: +7 999 000-11-22\n"
        )
        out = M.extract_candidate(msg.as_bytes(), "77")
        self.assertIsNotNone(out)
        self.assertEqual(out["auth_mode"], "ip")
        self.assertEqual(out["source_ip"], "85.94.32.2/32")

    def test_build_payloads_carries_secret_only_into_trunk_input(self):
        c = M.extract_candidate(registration_message(), "42")
        trunk, did = M.build_payloads(c)
        self.assertEqual(trunk["password"], "SuperSecretValue")
        self.assertEqual(trunk["username"], "1234567")
        self.assertNotIn("password", did)
        self.assertEqual(did["number"], c["did"])
        self.assertEqual(trunk["metadata"]["source"], "mcn_mailbox_auto_onboard")

    def test_scan_is_readonly_uses_body_peek_and_deduplicates_same_config(self):
        fake = FakeImap()
        fake.messages = {
            41: registration_message(uid="41"),
            42: registration_message(uid="42"),
        }
        row = {
            "secret_encrypted": "cipher",
            "imap_ssl": True,
            "imap_host": "imap.test",
            "imap_port": 993,
            "username": "owner@test",
            "email_address": "owner@test",
        }
        with patch.object(M.D, "_row", return_value=row), \
             patch.object(M.D, "_decrypt_mailbox_secret", return_value="pw"), \
             patch.object(M.D.imaplib, "IMAP4_SSL", return_value=fake):
            out = M.scan_mailbox(2, limit=100)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(fake.readonly)
        self.assertTrue(fake.fetch_queries)
        self.assertTrue(all("BODY.PEEK" in q for q in fake.fetch_queries))
        self.assertEqual(out["unique_complete_candidates"], 1)

    def test_scan_ignores_complete_sip_candidate_from_untrusted_sender(self):
        fake = FakeImap()
        fake.messages = {
            42: registration_message(
                uid="42",
                sender="Fake MCN <attacker@example.test>",
            ),
        }
        row = {
            "secret_encrypted": "cipher",
            "imap_ssl": True,
            "imap_host": "imap.test",
            "imap_port": 993,
            "username": "owner@test",
            "email_address": "owner@test",
        }
        with patch.object(M.D, "_row", return_value=row), \
             patch.object(M.D, "_decrypt_mailbox_secret", return_value="pw"), \
             patch.object(M.D.imaplib, "IMAP4_SSL", return_value=fake):
            out = M.scan_mailbox(2, limit=100)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["unique_complete_candidates"], 0)

    def test_scan_surfaces_latest_mcn_contract_blocker_without_body(self):
        fake = FakeImap()
        fake.messages = {
            302788: contract_required_message(),
            302843: company_card_required_message(),
        }
        row = {
            "secret_encrypted": "cipher",
            "imap_ssl": True,
            "imap_host": "imap.test",
            "imap_port": 993,
            "username": "owner@test",
            "email_address": "owner@test",
        }
        with patch.object(M.D, "_row", return_value=row), \
             patch.object(M.D, "_decrypt_mailbox_secret", return_value="pw"), \
             patch.object(M.D.imaplib, "IMAP4_SSL", return_value=fake):
            out = M.scan_mailbox(2, limit=40)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["unique_complete_candidates"], 0)
        op = out["operational_reply"]
        self.assertEqual(op["code"], "mcn_company_card_required")
        self.assertEqual(op["uid"], "302843")
        self.assertTrue(op["owner_action_required"])
        self.assertNotIn("Пришлите уже", json.dumps(op, ensure_ascii=False))

    def test_newer_sip_candidate_supersedes_older_company_card_blocker_for_progress(self):
        fake = FakeImap()
        fake.messages = {
            302843: company_card_required_message(),
            302900: registration_message(uid="302900"),
        }
        row = {
            "secret_encrypted": "cipher",
            "imap_ssl": True,
            "imap_host": "imap.test",
            "imap_port": 993,
            "username": "owner@test",
            "email_address": "owner@test",
        }
        with patch.object(M.D, "_row", return_value=row), \
             patch.object(M.D, "_decrypt_mailbox_secret", return_value="pw"), \
             patch.object(M.D.imaplib, "IMAP4_SSL", return_value=fake):
            out = M.scan_mailbox(2, limit=40)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["unique_complete_candidates"], 1)
        self.assertEqual(out["_candidates"][0]["uid"], "302900")
        self.assertEqual(out["operational_reply"]["code"], "mcn_company_card_required")
        # The caller progresses on the unique complete SIP candidate; the older
        # operational reply remains informational and must not erase it.

    def test_scan_returns_application_password_blocker_without_secret_echo(self):
        class AuthFailImap(FakeImap):
            def login(self, username, password):
                raise M.D.imaplib.IMAP4.error(
                    b"[AUTHENTICATIONFAILED] Application password is REQUIRED"
                )
        fake = AuthFailImap()
        row = {
            "secret_encrypted": "cipher",
            "imap_ssl": True,
            "imap_host": "imap.mail.ru",
            "imap_port": 993,
            "username": "owner@mail.ru",
            "email_address": "owner@mail.ru",
        }
        with patch.object(M.D, "_row", return_value=row), \
             patch.object(M.D, "_decrypt_mailbox_secret", return_value="NEVER_PRINT"), \
             patch.object(M.D.imaplib, "IMAP4_SSL", return_value=fake):
            out = M.scan_mailbox(2)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"], "application_password_required")
        self.assertNotIn("NEVER_PRINT", repr(out))

    def test_apply_rejects_stale_candidate_uid_before_account_or_mutation(self):
        candidate = M.extract_candidate(registration_message(uid="42"), "42")
        scan = {
            "status": "ok",
            "mailbox_id": 2,
            "unique_complete_candidates": 1,
            "_candidates": [candidate],
        }
        with patch.object(M, "scan_mailbox", return_value=scan), \
             patch.object(M, "resolve_target_account") as resolve_target, \
             patch.object(M, "apply_candidate") as apply_candidate, \
             patch("sys.argv", [
                 "prog", "--mailbox-id", "2", "--account-id", "acc",
                 "--expected-uid", "stale-uid",
                 "--expected-date", str(candidate.get("date") or ""),
                 "--apply",
             ]):
            rc = M.main()
        self.assertEqual(rc, 7)
        resolve_target.assert_not_called()
        apply_candidate.assert_not_called()

    def test_apply_candidate_returns_only_safe_projection(self):
        c = M.extract_candidate(registration_message(), "42")
        with patch.object(M, "provider_preflight", return_value={"status": "ok", "owner_user_id": 1}), \
             patch.object(M, "onboarding_upsert", return_value={
                 "status": "ok",
                 "trunk": {"id": 1, "status": "configured_unverified"},
                 "did": {"id": 2, "number_e164": "+79991234567"},
             }), \
             patch("app.services.telephony_core.save_provider_config", return_value={"status": "ok"}), \
             patch("app.services.telephony_core.verify_provider_connection", return_value={
                 "status": "ok", "connected": False
             }), \
             patch("app.services.asterisk_gateway.mcn_pjsip_guardian", return_value={
                 "status": "ok", "owner_action_required": False
             }), \
             patch.object(M, "mcn_readiness", return_value={
                 "status": "ok", "ready": False, "blockers": ["mcn_trunk_not_verified"]
             }):
            out = M.apply_candidate("acc", 2, c)
        serialized = json.dumps(out, ensure_ascii=False)
        self.assertEqual(out["status"], "ok")
        for secret in ("SuperSecretValue", "1234567", "sip.mcn.ru", c["did"]):
            self.assertNotIn(secret, serialized)

    def test_provider_conflict_blocks_before_onboarding(self):
        c = M.extract_candidate(registration_message(), "42")
        with patch.object(M, "provider_preflight", return_value={
            "status": "blocked", "reason": "provider_conflict"
        }), patch.object(M, "onboarding_upsert") as onboard:
            out = M.apply_candidate("acc", 2, c)
        self.assertEqual(out["reason"], "provider_conflict")
        onboard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
