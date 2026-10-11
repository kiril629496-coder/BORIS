import asyncio, inspect, json, sys
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api import banners as B

ACC = "evgeniy_peregorodki_12447"
N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2
db = SessionLocal()
lib = json.loads(db.execute(text("select value from storage where account_id=:a and key='banner_library'"), {"a": ACC}).fetchone()[0])
st = json.loads(db.execute(text("select value from storage where account_id=:a and key='brand_style'"), {"a": ACC}).fetchone()[0])
db.close()

fn = B.create_full_ai_banner
START = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0
for tpl in lib[START:N]:
    desc = "%s. %s" % (tpl.get("photo", ""), st.get("scene", ""))
    req = B.FullAiRequest(account_id=ACC, raw_description=desc, format="infographic",
                          quality="medium", count=1,
                          exact_text=tpl["title"],
                          exact_subtitle=tpl.get("subtitle", ""),
                          exact_price=tpl.get("price", "") if tpl.get("has_price") else "")
    try:
        res = fn(req)
        if inspect.iscoroutine(res):
            res = asyncio.run(res)
        print(tpl["id"], "->", json.dumps(res, ensure_ascii=False)[:180])
    except Exception as e:
        print(tpl["id"], "ОШИБКА:", str(e)[:160])
