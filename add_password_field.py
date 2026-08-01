path = "/root/BORIS/backend/app/models/account.py"
with open(path, encoding="utf-8") as f:
    src = f.read()

old = "    avito_user_id = Column(String, nullable=True)  # numeric id в личном кабинете Авито (для stats/balance)"
new = old + "\n    avito_login = Column(String, nullable=True)  # логин (email) от Avito, для будущего парсинга личного кабинета\n    avito_password = Column(String, nullable=True)  # пароль от Avito, хранится открытым текстом (техдолг)\n    comment = Column(String, nullable=True)\n    company_website = Column(String, nullable=True)\n    company_niche = Column(String, nullable=True)\n    company_tone = Column(String, nullable=True)\n    company_description = Column(String, nullable=True)\n    company_advantages = Column(String, nullable=True)"

if old in src and "avito_login" not in src:
    src = src.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("OK: поля добавлены")
else:
    print("ПРОВЕРЬ")
