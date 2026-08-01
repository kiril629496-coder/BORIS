path = "/root/BORIS/frontend/app/page.tsx"
with open(path, encoding="utf-8") as f:
    src = f.read()

old = '''  const addAccount = () => {
    if (!newAccount.name || !newAccount.login) return alert("Заполните название и логин");
    setAccounts([...accounts, {
      id: Date.now(), name: newAccount.name, login: newAccount.login,
      comment: newAccount.comment, status: "active",
      company: { website: newAccount.companyWebsite, description: newAccount.companyDescription,
        niche: newAccount.companyNiche, tone: newAccount.companyTone, advantages: newAccount.companyAdvantages }
    }]);
    setNewAccount({ name: "", login: "", password: "", comment: "", client_id: "", client_secret: "",
      companyWebsite: "", companyDescription: "", companyNiche: "", companyTone: "Дружелюбный", companyAdvantages: "" });
    setShowAddForm(false);
    setStep(1);
  };'''

new = '''  const [addingAccount, setAddingAccount] = useState(false);

  const addAccount = async () => {
    if (!newAccount.name || !newAccount.login) return alert("Заполните название и логин");
    setAddingAccount(true);
    const slug = newAccount.name.toLowerCase()
      .replace(/[а-яё]/g, (c: string) => ({а:"a",б:"b",в:"v",г:"g",д:"d",е:"e",ё:"e",ж:"zh",з:"z",и:"i",й:"y",к:"k",л:"l",м:"m",н:"n",о:"o",п:"p",р:"r",с:"s",т:"t",у:"u",ф:"f",х:"h",ц:"c",ч:"ch",ш:"sh",щ:"sch",ъ:"",ы:"y",ь:"",э:"e",ю:"yu",я:"ya"} as any)[c] || c)
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") + "_" + Date.now().toString().slice(-5);
    try {
      const res = await fetch("http://193.160.209.44:8000/api/accounts/create", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: slug,
          name: newAccount.name,
          avito_login: newAccount.login,
          avito_password: newAccount.password,
          comment: newAccount.comment,
          avito_client_id: newAccount.client_id,
          avito_client_secret: newAccount.client_secret,
          company_website: newAccount.companyWebsite,
          company_niche: newAccount.companyNiche,
          company_tone: newAccount.companyTone,
          company_description: newAccount.companyDescription,
          company_advantages: newAccount.companyAdvantages
        })
      });
      const data = await res.json();
      if (data.status === "ok") {
        const listRes = await fetch("http://193.160.209.44:8000/api/accounts/list");
        const listData = await listRes.json();
        if (listData.status === "ok") setAllAccounts(listData.accounts);
        setCurrentAccount(slug);
        alert("✅ Аккаунт создан! Переключились на него.");
        setNewAccount({ name: "", login: "", password: "", comment: "", client_id: "", client_secret: "",
          companyWebsite: "", companyDescription: "", companyNiche: "", companyTone: "Дружелюбный", companyAdvantages: "" });
        setShowAddForm(false);
        setStep(1);
      } else {
        alert(data.message || "Не удалось создать аккаунт");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setAddingAccount(false);
  };'''

if old in src and "addingAccount" not in src:
    src = src.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("OK: addAccount переписана на реальное сохранение")
else:
    print("ПРОВЕРЬ:", "уже есть" if "addingAccount" in src else "якорь не найден")
