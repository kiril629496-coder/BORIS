path = "/root/BORIS/backend/app/api/accounts.py"
with open(path, encoding="utf-8") as f:
    src = f.read()

changes = 0

old1 = '''class CreateAccountRequest(BaseModel):
    account_id: str
    name: str

@router.post("/create")
def create_account(req: CreateAccountRequest):
    db = SessionLocal()
    try:
        existing = db.query(Account).filter(Account.account_id == req.account_id).first()
        if existing:
            return {"status": "error", "message": "Аккаунт с таким ID уже существует"}
        acc = Account(account_id=req.account_id, name=req.name)
        db.add(acc)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()'''

new1 = '''class CreateAccountRequest(BaseModel):
    account_id: str
    name: str
    avito_login: str = ""
    avito_password: str = ""
    comment: str = ""
    avito_client_id: str = ""
    avito_client_secret: str = ""
    company_website: str = ""
    company_niche: str = ""
    company_tone: str = ""
    company_description: str = ""
    company_advantages: str = ""

@router.post("/create")
def create_account(req: CreateAccountRequest):
    db = SessionLocal()
    try:
        existing = db.query(Account).filter(Account.account_id == req.account_id).first()
        if existing:
            return {"status": "error", "message": "Аккаунт с таким ID уже существует"}
        acc = Account(
            account_id=req.account_id, name=req.name,
            avito_login=req.avito_login or None, avito_password=req.avito_password or None,
            comment=req.comment or None,
            avito_client_id=req.avito_client_id or None, avito_client_secret=req.avito_client_secret or None,
            company_website=req.company_website or None, company_niche=req.company_niche or None,
            company_tone=req.company_tone or None, company_description=req.company_description or None,
            company_advantages=req.company_advantages or None
        )
        db.add(acc)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()'''

if old1 in src:
    src = src.replace(old1, new1); changes += 1

with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print(f"Внесено правок: {changes}/1")
