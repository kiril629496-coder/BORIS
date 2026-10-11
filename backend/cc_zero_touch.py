"""ZERO-TOUCH architectural test: can client N+1 be described with data only?

No new .py file, no .env change, no crontab line, no manual SQL is allowed.
The fixture is a plain dict of rows exactly as they would come from the DB for
a freshly connected client: an accounts row, Avito keys, an owner and nothing
else. Everything after that must be produced by the existing pipeline.
"""

from app.client_config import collector as C
from app.client_config import resolver as R
from app.client_config import schema as S
from app.client_config import validator as V
from app.client_config import diff as D

bad = 0


def check(name, cond, extra=""):
    global bad
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + str(extra)) if extra != "" else ""))
    bad += 0 if cond else 1


def fixture(account_id="zt_demo_client", with_keys=True, with_kpi=False):
    """Only rows a new client really produces: account, keys, owner, name."""
    st = {}
    if with_kpi:
        st["kpi_settings"] = {"target_leads_per_day": 4, "max_cost_per_lead_rub": 600,
                              "daily_budget_limit_rub": 2400}
    return {
        "account_id": account_id,
        "storage": st,
        "account": {"avito_client_id": "x" if with_keys else None,
                    "avito_client_secret": "y" if with_keys else None,
                    "name": "Demo Client"},
        "bindings": [], "routes": [], "mandates": [], "react": [],
        "prompts": [], "access": [{"user_id": 999, "role": "owner"}], "facts": 0,
    }


print("=== 1. НОВЫЙ КЛИЕНТ ОПИСЫВАЕТСЯ ОДНИМИ ДАННЫМИ ===")
cfg = C.build_config(fixture())
findings = V.validate(cfg, owner_kind="user")
status = V.status_of(findings)
check("конфиг построен без единой строки кода под клиента", isinstance(cfg, dict) and cfg.get("schema_version") == 1)
check("статус честный, а не подогнанный", status in ("invalid", "needs_input"), status)
check("никакого account_id в коде схемы", "zt_demo" not in open(S.__file__, encoding="utf-8").read())

print("=== 2. ЧТО BORIS ПОПРОСИТ У КЛИЕНТА ===")
need = [f["field"] for f in findings if f["code"] in ("NEEDS_INPUT", "BLOCKS_FEATURE")]
for f in sorted(set(need)):
    node = S.get_field(cfg, f) or {}
    print("   %-30s %s" % (f, node.get("source_gap") or node.get("reason")))
check("список недостающего непустой и осмысленный", len(need) >= 5, len(need))
check("KPI требуется явно", any(f.startswith("kpi.") for f in need))

print("=== 3. РЕЗОЛВЕР И DIFF НА ФИКТИВНОМ КЛИЕНТЕ ===")
eff = R.merge(None, cfg)
check("резолвер отработал без базы", eff["channels.avito"]["state"] == S.KNOWN)
flat = {}
for section in S.SECTIONS:
    for name, node in (cfg.get(section) or {}).items():
        flat["%s.%s" % (section, name)] = node
rows = D.compare(flat, eff)
counts = {}
for r in rows.values():
    counts[r["status"]] = counts.get(r["status"], 0) + 1
print("   статусы diff:", counts)
check("нет ни одного different", counts.get("different", 0) == 0, counts)
check("нет потерь конфигом", counts.get("missing_in_config", 0) == 0)

print("=== 4. ЗАПОЛНЕНИЕ ДАННЫМИ МЕНЯЕТ РЕЗУЛЬТАТ БЕЗ ПРАВКИ КОДА ===")
cfg2 = C.build_config(fixture(with_kpi=True))
f2 = V.validate(cfg2)
kpi_blocked = [x for x in f2 if x["field"].startswith("kpi.") and x["code"] == "BLOCKS_FEATURE"]
check("KPI перестал блокировать после появления данных", len(kpi_blocked) < 3, len(kpi_blocked))
check("значение доехало до резолвера", R.value_of(R.merge(None, cfg2), "kpi.target_leads_per_day") == 4)

print("=== 5. БЕЗ КЛЮЧЕЙ AVITO КОНТУР ЧЕСТНО БЛОКИРУЕТСЯ ===")
cfg3 = C.build_config(fixture(with_keys=False))
f3 = V.validate(cfg3)
check("нет подключения -> блокирующая ошибка",
      any(x["field"] == "channels.avito" and x["blocking"] for x in f3))
check("но конфиг всё равно строится", cfg3.get("schema_version") == 1)

print("ZERO-TOUCH:", "PASS" if bad == 0 else "FAIL %d" % bad)
