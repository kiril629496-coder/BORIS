"""Real Python/PostgreSQL parity tests for isolated Clients510 trial candidates.

This harness creates its own PostgreSQL 16 cluster under /tmp, listens only on
its private Unix socket, verifies data_directory before DDL, and removes it
after testing. Production connections and production DDL are never used.
"""
import argparse
import datetime as DT
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest

PACKAGE=Path(__file__).resolve().parent / "fixtures/clients510_garik_trial"
SOURCE=Path(__file__).resolve().parents[1]
DEPENDENCIES=SOURCE if (SOURCE/"app/models/storage.py").exists() else Path("/root/BORIS/releases/backend/current/backend").resolve()
sys.path.insert(0,str(DEPENDENCIES))
from sqlalchemy import create_engine,text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from app.models.storage import Storage

ENGINE=None
APP=None

class TrialParityTests(unittest.TestCase):
    def setUp(self):
        self.conn=ENGINE.connect()
        self.tx=self.conn.begin()
        self.db=Session(bind=self.conn)
        self.now=self.conn.execute(text("SELECT now()")).scalar_one()
        self.put("A","billing",{"paid_until":(self.now-DT.timedelta(hours=1)).isoformat()})
        self.put("A","kpi_settings",{"daily_budget_limit_rub":1000,"hard_max_bid_rub":150})
        self.put("A","autopilot_settings",self.trial())

    def tearDown(self):
        self.db.close()
        self.tx.rollback()
        self.conn.close()

    def trial(self,**changes):
        value={"full_ai_marketer":True,"trial_scope":"ai_marketer_only",
               "trial_source":"owner_direct_instruction_20261008",
               "full_ai_marketer_activated_at":(self.now-DT.timedelta(days=1)).isoformat(),
               "full_ai_marketer_trial_until":(self.now+DT.timedelta(days=2)).isoformat()}
        value.update(changes)
        return value

    def put(self,account,key,value):
        self.conn.execute(text("INSERT INTO storage(account_id,key,value) VALUES (:a,:k,:v)"),
                          {"a":account,"k":key,"v":json.dumps(value)})

    def app(self,account="A",now=None):
        return APP.marketing_service_entitlement(self.db,account,now=now or self.now)

    def mandate(self,**changes):
        values={"status":"active","revoked_at":None,"allowed_operations":["cpx.raise_bid"],
                "account_scope":["A"],"source":"paid_tariff_kpi","daily_budget_rub":500,
                "max_bid_rub":100,"max_bid_delta_pct":10,"max_actions_run":5,"max_actions_day":120,
                "valid_until":self.now+DT.timedelta(days=365)}
        values.update(changes)
        columns=list(values)
        statement="INSERT INTO trial_test_mandates("+",".join(columns)+") VALUES ("+",".join(":"+k for k in columns)+") RETURNING valid_until"
        savepoint=self.conn.begin_nested()
        try:
            until=self.conn.execute(text(statement),values).scalar_one()
            return {"allowed":True,"valid_until":until}
        except Exception as exc:
            return {"allowed":False,"error":str(exc).splitlines()[0]}
        finally:
            savepoint.rollback()

    def assert_rejected_trial(self,value,label=""):
        self.put("A","autopilot_settings",value)
        app=self.app();db=self.mandate()
        self.assertNotEqual(app["state"],"active",{"label":label,"app":app,"db":db})
        self.assertFalse(db["allowed"],{"label":label,"app":app,"db":db})

    def test_valid_trial_after_base_expiry_is_active_but_capped(self):
        app=self.app();db=self.mandate()
        self.assertEqual(app["state"],"active")
        self.assertEqual(app["entitlement_kind"],"trial")
        self.assertEqual(app["scope"],"ai_marketer_only")
        self.assertTrue(db["allowed"],db)
        self.assertEqual(db["valid_until"],self.now+DT.timedelta(days=2))

    def test_full_flag_requires_actual_json_boolean_true(self):
        for flag in [False,"true","false",1,0,None]:
            with self.subTest(flag=flag):
                self.assert_rejected_trial(self.trial(full_ai_marketer=flag),repr(flag))

    def test_other_sources_and_scopes_do_not_grant_rights(self):
        for changes in [
            {"trial_source":"billing_unlimited"},
            {"trial_source":"owner_direct_instruction"},
            {"trial_source":"owner_direct_instruction_20261008_extra"},
            {"trial_source":"owner_direct_instruction_20261008\n"},
            {"trial_source":None},
            {"trial_scope":"mop"},
            {"trial_scope":"full_service"},
            {"trial_scope":None},
        ]:
            with self.subTest(changes=changes):
                self.assert_rejected_trial(self.trial(**changes),str(changes))

    def test_future_activation_is_rejected(self):
        self.assert_rejected_trial(self.trial(full_ai_marketer_activated_at=(self.now+DT.timedelta(seconds=1)).isoformat()))

    def test_expired_and_equal_end_are_rejected(self):
        for until in [self.now-DT.timedelta(microseconds=1),self.now]:
            with self.subTest(until=until):
                self.assert_rejected_trial(self.trial(full_ai_marketer_trial_until=until.isoformat()))

    def test_activation_equal_now_is_allowed(self):
        self.put("A","autopilot_settings",self.trial(full_ai_marketer_activated_at=self.now.isoformat()))
        self.assertEqual(self.app()["state"],"active")
        self.assertTrue(self.mandate()["allowed"])

    def test_invalid_missing_and_infinite_dates_are_rejected(self):
        for key in ["full_ai_marketer_activated_at","full_ai_marketer_trial_until"]:
            for value in [None,"","not-a-date","infinity","-infinity","now","epoch","2026-02-30T12:00:00Z"]:
                with self.subTest(key=key,value=value):
                    self.assert_rejected_trial(self.trial(**{key:value}),key+"="+str(value))

    def test_trial_timestamps_require_explicit_iso_zone_and_seconds(self):
        for key,when in [("full_ai_marketer_activated_at",self.now-DT.timedelta(days=1)),
                         ("full_ai_marketer_trial_until",self.now+DT.timedelta(days=2))]:
            for value in [when.strftime("%Y-%m-%d"),
                          when.strftime("%Y-%m-%dT%H:%M:%S"),
                          when.strftime("%Y-%m-%d %H:%M:%S+00:00"),
                          when.strftime("%Y-%m-%dT%H:%M+00:00"),
                          when.strftime("%Y-%m-%dT%H:%M:%S")+","+"123456Z",
                          when.strftime("%Y-%m-%dT%H:%M:%S")+".1234567Z"]:
                with self.subTest(key=key,value=value):
                    self.assert_rejected_trial(self.trial(**{key:value}),key+"="+str(value))

    def test_invalid_calendar_time_and_offset_fail_closed(self):
        start=(self.now-DT.timedelta(days=1)).strftime("%Y-%m-%d")
        end=(self.now+DT.timedelta(days=2)).strftime("%Y-%m-%d")
        for value in [start+"T24:00:00Z",start+"T12:60:00Z",start+"T12:00:60Z",
                      start+"T12:00:00+24:00",start+"T12:00:00+16:00",
                      start+"T12:00:00Z\n"]:
            with self.subTest(value=value):
                self.assert_rejected_trial(self.trial(full_ai_marketer_activated_at=value),value)

    def test_utc_z_and_offset_forms_have_identical_instants(self):
        offset=DT.timezone(DT.timedelta(hours=2,minutes=30))
        for use_z in [True,False]:
            start=self.now-DT.timedelta(days=1)
            end=self.now+DT.timedelta(days=2)
            start_string=start.isoformat().replace("+00:00","Z") if use_z else start.astimezone(offset).isoformat()
            end_string=end.isoformat().replace("+00:00","Z") if use_z else end.astimezone(offset).isoformat()
            self.put("A","autopilot_settings",self.trial(full_ai_marketer_activated_at=start_string,full_ai_marketer_trial_until=end_string))
            app=self.app();db=self.mandate()
            self.assertEqual(app["state"],"active")
            self.assertTrue(db["allowed"],db)
            self.assertEqual(DT.datetime.fromisoformat(app["until"]),end)
            self.assertEqual(db["valid_until"],end)

    def test_python_naive_now_is_interpreted_as_utc(self):
        self.assertEqual(self.app(now=self.now.replace(tzinfo=None))["state"],"active")

    def test_latest_invalid_row_does_not_borrow_older_trial(self):
        self.assert_rejected_trial(self.trial(full_ai_marketer=False))

    def test_legacy_unlimited_without_current_trial_is_not_entitlement(self):
        self.assert_rejected_trial({"full_ai_marketer":True,"unlimited":True,"trial_scope":"ai_marketer_only"})

    def test_new_feed_launch_does_not_receive_ai_trial(self):
        result=self.mandate(source="new_feed_launch",max_actions_run=1,max_actions_day=24)
        self.assertFalse(result["allowed"],result)
        self.assertIn("entitlement inactive",result["error"])

    def test_trial_does_not_supply_missing_budget(self):
        self.put("A","kpi_settings",{"hard_max_bid_rub":150})
        result=self.mandate()
        self.assertFalse(result["allowed"],result)
        self.assertIn("no positive explicit daily budget",result["error"])

    def test_trial_cannot_exceed_explicit_budget(self):
        for amount in [None,0,-1,1001]:
            with self.subTest(amount=amount):
                result=self.mandate(daily_budget_rub=amount)
                self.assertFalse(result["allowed"],result)

    def test_trial_cannot_exceed_account_or_absolute_bid_cap(self):
        for cap,requested in [(150,151),(90,100),(300,151),(150,0),(150,None)]:
            with self.subTest(cap=cap,requested=requested):
                self.put("A","kpi_settings",{"daily_budget_limit_rub":1000,"hard_max_bid_rub":cap})
                result=self.mandate(max_bid_rub=requested)
                self.assertFalse(result["allowed"],result)

    def test_existing_action_limits_remain_enforced(self):
        for changes in [{"max_bid_delta_pct":11},{"max_actions_run":6},{"max_actions_day":121}]:
            with self.subTest(changes=changes):
                self.assertFalse(self.mandate(**changes)["allowed"])

    def test_trial_does_not_leak_to_another_account(self):
        self.put("B","billing",{"paid_until":(self.now-DT.timedelta(days=1)).isoformat()})
        self.put("B","kpi_settings",{"daily_budget_limit_rub":1000,"hard_max_bid_rub":150})
        self.assertNotEqual(self.app("B")["state"],"active")
        self.assertFalse(self.mandate(account_scope=["B"])["allowed"])
        for scope in [["A","B"],["B","A"]]:
            with self.subTest(scope=scope):
                self.assertFalse(self.mandate(account_scope=scope)["allowed"])

    def test_two_valid_accounts_cap_to_earliest_trial_in_either_order(self):
        self.put("B","billing",{"paid_until":(self.now-DT.timedelta(days=1)).isoformat()})
        self.put("B","kpi_settings",{"daily_budget_limit_rub":1000,"hard_max_bid_rub":150})
        self.put("B","autopilot_settings",self.trial(full_ai_marketer_trial_until=(self.now+DT.timedelta(days=1)).isoformat()))
        for scope in [["A","B"],["B","A"]]:
            with self.subTest(scope=scope):
                result=self.mandate(account_scope=scope)
                self.assertTrue(result["allowed"],result)
                self.assertEqual(result["valid_until"],self.now+DT.timedelta(days=1))

    def test_existing_shorter_valid_until_is_not_extended(self):
        earlier=self.now+DT.timedelta(hours=1)
        result=self.mandate(valid_until=earlier)
        self.assertTrue(result["allowed"],result)
        self.assertEqual(result["valid_until"],earlier)

    def test_missing_valid_until_is_capped_to_trial_end(self):
        result=self.mandate(valid_until=None)
        self.assertTrue(result["allowed"],result)
        self.assertEqual(result["valid_until"],self.now+DT.timedelta(days=2))

    def test_active_paid_billing_retains_its_existing_rights(self):
        until=self.now+DT.timedelta(days=7)
        self.put("A","billing",{"paid_until":until.isoformat()})
        self.put("A","autopilot_settings",self.trial(full_ai_marketer=False))
        app=self.app();db=self.mandate()
        self.assertEqual(app["state"],"active")
        self.assertEqual(app["source"],"billing.paid_until")
        self.assertTrue(db["allowed"],db)
        self.assertEqual(db["valid_until"],until)

    def test_paid_period_wins_over_trial_without_extension(self):
        until=self.now+DT.timedelta(hours=3)
        self.put("A","billing",{"paid_until":until.isoformat()})
        self.assertEqual(self.app()["source"],"billing.paid_until")
        result=self.mandate()
        self.assertTrue(result["allowed"],result)
        self.assertEqual(result["valid_until"],until)

    def test_paid_payment_status_rights_are_preserved(self):
        self.put("A","billing",{})
        self.put("A","payment_status",{"paid_at":(self.now-DT.timedelta(days=1)).isoformat(),"period_days":8})
        self.put("A","autopilot_settings",self.trial(full_ai_marketer=False))
        app=self.app();result=self.mandate()
        self.assertEqual(app["source"],"payment_status")
        self.assertEqual(app["state"],"active")
        self.assertTrue(result["allowed"],result)
        self.assertEqual(result["valid_until"],self.now+DT.timedelta(days=7))

    def test_new_feed_launch_still_accepts_a_real_paid_period(self):
        self.put("A","billing",{"paid_until":(self.now+DT.timedelta(days=1)).isoformat()})
        result=self.mandate(source="new_feed_launch",max_actions_run=1,max_actions_day=24)
        self.assertTrue(result["allowed"],result)



    def test_exact_numbered_rollback_and_forward_reapply(self):
        with self.conn.connection.driver_connection.cursor() as cursor:
            cursor.execute((PACKAGE/"072_clients510_rollback_explicit_ai_trial_money_guard.sql").read_text())
        actual=self.conn.execute(text("SELECT pg_get_functiondef('public.boris_guard_money_mandate_v1()'::regprocedure)")).scalar_one()
        self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(),"497f3029a7184a42385a7aefbcf6f6e10da63f8efc14d6e370d257e5781b5a36")
        self.assertFalse(self.mandate()["allowed"])
        with self.conn.connection.driver_connection.cursor() as cursor:
            cursor.execute((PACKAGE/"071_clients510_explicit_ai_trial_money_guard.sql").read_text())
        self.assertTrue(self.mandate()["allowed"])

    def test_applied_forward_migration_cannot_be_blindly_replayed(self):
        point=self.conn.begin_nested()
        try:
            with self.assertRaisesRegex(Exception,"CLIENTS510_FUNCTION_BASELINE_CHANGED"):
                with self.conn.connection.driver_connection.cursor() as cursor:
                    cursor.execute((PACKAGE/"071_clients510_explicit_ai_trial_money_guard.sql").read_text())
        finally:
            point.rollback()
        self.assertTrue(self.mandate()["allowed"])


def run_command(argv,timeout=30):
    result=subprocess.run(argv,cwd="/tmp",text=True,capture_output=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError("test-cluster command failed: "+str(argv[:3])+" "+(result.stdout+result.stderr)[-2000:])
    return result.stdout+result.stderr


CLUSTER_ROOT=None
CLUSTER_STARTED=False

def setUpModule():
    global APP,ENGINE,CLUSTER_ROOT,CLUSTER_STARTED
    assert os.geteuid()==0,"isolated PostgreSQL cluster needs local runuser; production DB is never used"
    app_path=SOURCE/"app/services/control_plane_adapters_ext.py"
    spec=importlib.util.spec_from_file_location("clients510_staged_trial_app",app_path)
    APP=importlib.util.module_from_spec(spec);spec.loader.exec_module(APP)
    CLUSTER_ROOT=Path(tempfile.mkdtemp(prefix="boris-clients510-trial-qa-",dir="/tmp"))
    pg=pwd.getpwnam("postgres")
    os.chown(CLUSTER_ROOT,pg.pw_uid,pg.pw_gid);os.chmod(CLUSTER_ROOT,0o700)
    data=CLUSTER_ROOT/"data";sock=CLUSTER_ROOT/"socket";sock.mkdir();os.chown(sock,pg.pw_uid,pg.pw_gid)
    bins=Path("/usr/lib/postgresql/16/bin");prefix=["runuser","-u","postgres","--"]
    try:
        run_command(prefix+[str(bins/"initdb"),"-D",str(data),"--auth-local=trust","--auth-host=reject","--no-locale","--encoding=UTF8"])
        options="-c listen_addresses='' -c unix_socket_directories='"+str(sock)+"' -c unix_socket_permissions=0700 -p 55439"
        run_command(prefix+[str(bins/"pg_ctl"),"-D",str(data),"-l",str(CLUSTER_ROOT/"postgres.log"),"-o",options,"-w","start"])
        CLUSTER_STARTED=True
        url=URL.create("postgresql+psycopg2",username="postgres",database="postgres",query={"host":str(sock),"port":"55439"})
        ENGINE=create_engine(url)
        with ENGINE.begin() as conn:
            actual=conn.execute(text("SELECT current_setting('data_directory'),current_setting('listen_addresses')")).one()
            assert Path(actual[0]).resolve()==data.resolve() and actual[1]=="","REFUSE_NONISOLATED_DATABASE"
            conn.execute(text("SET TIME ZONE 'UTC'"))
            Storage.__table__.create(conn)
            conn.execute(text("CREATE TABLE control_requirements(key text,status text,desired_state_json jsonb)"))
            conn.execute(text("INSERT INTO control_requirements VALUES ('system.marketing.absolute_autonomous_bid_cap','active',CAST(:policy AS jsonb))"),{"policy":json.dumps({"hard_max_bid_rub":150})})
            conn.execute(text("CREATE TABLE trial_test_mandates(id bigserial PRIMARY KEY,status text,revoked_at timestamptz,allowed_operations text[],account_scope text[],source text,daily_budget_rub numeric,max_bid_rub numeric,max_bid_delta_pct numeric,max_actions_run integer,max_actions_day integer,valid_until timestamptz)"))
            with conn.connection.driver_connection.cursor() as cursor:
                cursor.execute((PACKAGE/"money_mandate_guard.before.sql").read_text())
                cursor.execute((PACKAGE/"071_clients510_explicit_ai_trial_money_guard.sql").read_text())
            definition=conn.execute(text("SELECT pg_get_functiondef('public.boris_guard_money_mandate_v1()'::regprocedure)")).scalar_one()
            assert hashlib.sha256(definition.encode()).hexdigest()=="bd3ebe0b1b060bb6ee01d7eb24fac8a5f91b6d8194d45949f0da68411f0e3200"
            conn.execute(text("CREATE TRIGGER trial_guard BEFORE INSERT OR UPDATE ON trial_test_mandates FOR EACH ROW EXECUTE FUNCTION public.boris_guard_money_mandate_v1()"))
    except BaseException:
        tearDownModule()
        raise

def tearDownModule():
    global ENGINE,CLUSTER_ROOT,CLUSTER_STARTED
    if ENGINE is not None:ENGINE.dispose();ENGINE=None
    if CLUSTER_STARTED:
        run_command(["runuser","-u","postgres","--","/usr/lib/postgresql/16/bin/pg_ctl","-D",str(CLUSTER_ROOT/"data"),"-m","fast","-w","stop"])
        CLUSTER_STARTED=False
    if CLUSTER_ROOT is not None:
        assert CLUSTER_ROOT.parent==Path("/tmp") and CLUSTER_ROOT.name.startswith("boris-clients510-trial-qa-")
        shutil.rmtree(CLUSTER_ROOT);CLUSTER_ROOT=None
