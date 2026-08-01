"use client";
import { useState, useEffect, useRef } from "react";
import { Sidebar } from "../ui/Sidebar";
import { Mascot } from '../components/Mascot';
import { useRouter } from "next/navigation";
import { ConfirmModal } from "../components/ConfirmModal";

// Единая обёртка над fetch - подставляет Authorization: Bearer из localStorage (backend теперь
// требует его почти везде) и при 401 (токен истёк/невалиден) чистит localStorage и уводит на /login,
// вместо того чтобы молча падать. Замена всех голых fetch(...) на apiFetch(...) в этом файле -
// побайтовая, сигнатура идентична нативному fetch.
async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const _url = typeof input === "string" ? input : input.toString();
  if (/account_id=(&|$)/.test(_url)) {
    return new Response(JSON.stringify({ status: "skip" }), { status: 200, headers: { "Content-Type": "application/json" } });
  }
  const token = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(input, { ...init, headers });
  if (resp.status === 401 && typeof window !== "undefined") {
    localStorage.removeItem("boris_token");
    localStorage.removeItem("boris_user_email");
    localStorage.removeItem("boris_user_role");
    localStorage.removeItem("boris_currentAccount");
    window.location.href = "/login";
  }
  return resp;
}

const defaultTemplate = {
  id: 1,
  name: "Шаблон по умолчанию",
  titleTemplate: "{название}",
  description: "{описание}",
  priceType: "original",
  priceModifier: 0,
  cities: ["Москва"],
  category: "Предложение услуг",
  schedule: "09:00",
  scheduleDays: ["пн","вт","ср","чт","пт"],
  delayMin: 2,
  delayMax: 3,
};

const spinText = (text: string) => {
  return text.replace(/\{([^}]+)\}/g, (match, options) => {
    if (options.includes("|")) {
      const variants = options.split("|");
      return variants[Math.floor(Math.random() * variants.length)];
    }
    return match;
  });
};


const CITY_SETS: {[key: string]: string[]} = {
  "РФ 100 000+": ["Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань", "Нижний Новгород", "Красноярск", "Челябинск", "Уфа", "Самара", "Ростов-на-Дону", "Краснодар", "Омск", "Воронеж", "Пермь", "Волгоград", "Саратов", "Тюмень", "Тольятти", "Барнаул", "Ижевск", "Махачкала", "Хабаровск", "Ульяновск", "Иркутск", "Владивосток", "Ярославль", "Кемерово", "Севастополь", "Томск", "Набережные Челны", "Ставрополь", "Оренбург", "Новокузнецк", "Рязань", "Балашиха", "Пенза", "Чебоксары", "Липецк", "Калининград", "Астрахань", "Тула", "Киров", "Сочи", "Курск", "Улан-Удэ", "Тверь", "Магнитогорск", "Сургут", "Брянск", "Иваново", "Якутск", "Владимир", "Симферополь", "Белгород", "Нижний Тагил", "Калуга", "Чита", "Грозный", "Волжский", "Смоленск", "Подольск", "Саранск", "Вологда", "Курган", "Череповец", "Орёл", "Архангельск", "Владикавказ", "Тамбов", "Йошкар-Ола", "Мытищи", "Мурманск", "Кострома", "Нальчик", "Новороссийск", "Стерлитамак", "Химки", "Таганрог", "Люберцы", "Петрозаводск", "Королёв", "Нижневартовск", "Комсомольск-на-Амуре", "Шахты", "Дзержинск", "Братск", "Энгельс", "Орск", "Ангарск", "Благовещенск", "Старый Оскол", "Великий Новгород", "Псков", "Бийск", "Южно-Сахалинск", "Прокопьевск", "Абакан", "Армавир", "Балаково", "Рыбинск", "Северодвинск", "Красногорск", "Петропавловск-Камчатский", "Уссурийск", "Норильск", "Сыктывкар", "Волгодонск", "Каменск-Уральский", "Новочеркасск", "Златоуст", "Электросталь", "Альметьевск", "Салават", "Миасс", "Керчь", "Находка", "Копейск", "Пятигорск", "Хасавюрт", "Рубцовск", "Березники", "Коломна", "Майкоп", "Одинцово", "Ковров", "Домодедово", "Нефтекамск", "Кисловодск", "Нефтеюганск", "Батайск", "Новочебоксарск", "Серпухов", "Щёлково", "Новомосковск", "Дербент", "Первоуральск", "Черкесск", "Орехово-Зуево", "Невинномысск", "Раменское", "Кызыл", "Обнинск", "Каспийск", "Октябрьский", "Новый Уренгой", "Ессентуки", "Долгопрудный", "Жуковский", "Реутов", "Камышин", "Муром", "Новошахтинск", "Пушкино", "Северск", "Ноябрьск", "Артём", "Ачинск", "Бердск", "Елец", "Арзамас", "Сергиев Посад", "Элиста", "Железногорск", "Зеленодольск"],

  "Москва + вся МО": ["Москва","Балашиха","Подольск","Химки","Королёв","Мытищи","Люберцы","Красногорск","Электросталь","Коломна","Одинцово","Домодедово","Серпухов","Щёлково","Орехово-Зуево","Раменское","Долгопрудный","Жуковский","Пушкино","Ногинск","Сергиев Посад","Реутов","Клин","Дмитров","Видное","Дубна","Егорьевск","Чехов","Наро-Фоминск","Ивантеевка","Лобня","Дзержинский","Фрязино","Солнечногорск","Истра","Можайск","Волоколамск","Кашира","Луховицы","Зарайск"],
  "Миллионники РФ": ["Москва","Санкт-Петербург","Новосибирск","Екатеринбург","Казань","Нижний Новгород","Челябинск","Красноярск","Самара","Уфа","Ростов-на-Дону","Краснодар","Омск","Воронеж","Пермь","Волгоград"]
};

const AVITO_CATEGORIES: {[key: string]: {ready: boolean, subs: string[]}} = {
  "Услуги": { ready: true, subs: ["Стихи, поздравления, тосты (готово)", "Ремонт и строительство", "Другие услуги"] },
  "Транспорт": { ready: false, subs: ["Автомобили", "Мотоциклы и мототехника", "Грузовики и спецтехника", "Водный транспорт", "Запчасти и аксессуары"] },
  "Недвижимость": { ready: false, subs: ["Купить жильё", "Коммерческая недвижимость", "Другие категории"] },
  "Работа": { ready: false, subs: ["Ищу работу", "Ищу сотрудника"] },
  "Личные вещи": { ready: false, subs: ["Одежда, обувь, аксессуары", "Детская одежда и обувь", "Красота и здоровье", "Часы и украшения"] },
  "Для дома и дачи": { ready: false, subs: ["Ремонт и строительство", "Мебель и интерьер", "Бытовая техника", "Продукты питания", "Растения", "Посуда и товары для кухни"] },
  "Электроника": { ready: false, subs: ["Телефоны", "Аудио и видео", "Товары для компьютера", "Ноутбуки", "Настольные компьютеры", "Фототехника"] },
  "Бизнес и оборудование": { ready: false, subs: ["Оборудование для бизнеса", "Франшизы", "Готовый бизнес", "ПО для бизнеса"] },
  "Хобби и отдых": { ready: false, subs: ["Билеты и путешествия", "Велосипеды", "Книги и журналы", "Коллекционирование", "Музыкальные инструменты", "Охота и рыбалка", "Спорт и отдых"] },
  "Животные": { ready: false, subs: ["Собаки", "Кошки", "Птицы", "Аквариум", "Товары для животных"] },
};

function TermsGate({ product, pack, accountId, children }: any) {
  const [agreed, setAgreed] = useState(false);
  const [showText, setShowText] = useState(false);
  const [terms, setTerms] = useState<any>(null);
  const loadTerms = async () => {
    setShowText(true);
    if (terms) return;
    try {
      const r = await apiFetch(`/api/legal/terms?product=${product}`);
      const d = await r.json();
      if (d.status === "ok") setTerms(d);
    } catch {}
  };
  const onCheck = async (v: boolean) => {
    setAgreed(v);
    if (v && accountId) {
      try {
        await apiFetch(`/api/legal/accept`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account_id: accountId, product, pack }),
        });
      } catch {}
    }
  };
  return (
    <div style={{ width: "100%" }}>
      <label style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "12.5px", color: "#475467", marginBottom: "10px", textAlign: "left", cursor: "pointer" }}>
        <input type="checkbox" checked={agreed} onChange={e => onCheck(e.target.checked)} style={{ marginTop: "2px", flexShrink: 0 }} />
        <span>Я согласен с <span onClick={e => { e.preventDefault(); loadTerms(); }} style={{ color: "#2F6FED", fontWeight: 600, textDecoration: "underline", cursor: "pointer" }}>правилами использования</span> и офертой</span>
      </label>
      <div style={{ position: "relative" }}>
        {children}
        {!agreed && (
          <div onClick={() => alert("Поставьте галочку согласия с правилами, чтобы продолжить")} style={{ position: "absolute", inset: 0, cursor: "not-allowed", background: "rgba(255,255,255,0.55)", borderRadius: "10px" }} />
        )}
      </div>
      {showText && terms && (
        <div onClick={() => setShowText(false)} style={{ position: "fixed", inset: 0, background: "rgba(16,24,40,0.55)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", padding: "20px" }}>
          <div onClick={e => e.stopPropagation()} style={{ background: "#fff", borderRadius: "16px", maxWidth: "680px", width: "100%", maxHeight: "82vh", overflow: "auto", padding: "28px 30px", textAlign: "left" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <div style={{ fontSize: "18px", fontWeight: 800, color: "#1D2939" }}>{terms.title}</div>
              <button onClick={() => setShowText(false)} style={{ background: "#F2F4F7", border: "none", borderRadius: "8px", width: "32px", height: "32px", cursor: "pointer", fontSize: "16px" }}>✕</button>
            </div>
            <div style={{ fontSize: "12px", color: "#98A2B3", marginBottom: "16px" }}>Редакция от {terms.version}</div>
            <div style={{ fontSize: "14px", color: "#344054", lineHeight: 1.65 }} dangerouslySetInnerHTML={{ __html: terms.html }} />
          </div>
        </div>
      )}
    </div>
  );
}

function RobokassaButton({ invoiceId, pack, accountId }: { invoiceId: string; pack?: string; accountId?: string }) {
  const [busy, setBusy] = useState(false);
  const go = async (e: any) => {
    if (!pack || !accountId) return;            // нет кода пакета — работает старая ссылка
    e.preventDefault();
    setBusy(true);
    try {
      const r = await apiFetch(`/api/payments/robokassa/link?account_id=${encodeURIComponent(accountId)}&pack=${pack}`);
      const d = await r.json();
      if (d.status === "ok" && d.url) { window.location.href = d.url; return; }
      window.open(`https://auth.robokassa.ru/merchant/Invoice/${invoiceId}`, "_blank");
    } catch {
      window.open(`https://auth.robokassa.ru/merchant/Invoice/${invoiceId}`, "_blank");
    } finally { setBusy(false); }
  };
  // Виджет Robokassa в итоге ведёт на простую страницу оплаты по прямой ссылке
  // https://auth.robokassa.ru/merchant/Invoice/{invoiceId} - используем её напрямую
  // в своей кнопке вместо чужого iframe-виджета, чтобы дизайн был полностью наш.
  return (
    <a href={`https://auth.robokassa.ru/merchant/Invoice/${invoiceId}`}
      onClick={go}
      target="_blank"
      rel="noopener noreferrer"
      className="boris-robokassa-btn"
      style={{
        display:"inline-flex", alignItems:"center", justifyContent:"center", gap:"6px",
        width:"100%", background:"transparent", color:"#2F6FED", fontWeight:"bold",
        fontSize:"15px", border:"2px solid #2F6FED", borderRadius:"10px", padding:"11px 20px",
        textDecoration:"none", boxSizing:"border-box"
      }}
    >
      {busy ? "Открываю оплату..." : "Оплатить"} <span style={{fontSize:"17px"}}>→</span>
    </a>
  );
}

function CountUp({ to, delay = 0 }: { to: number; delay?: number }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    let raf = 0;
    const start = performance.now() + delay;
    const dur = 1100;
    const tick = (t: number) => {
      const p = Math.min(1, Math.max(0, (t - start) / dur));
      const eased = 1 - Math.pow(1 - p, 3);
      setV(Math.round(to * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, delay]);
  return <>{v.toLocaleString("ru-RU")}</>;
}

export default function Home() {
  const [view, setView] = useState("accounts");
  const [selectedAccount, setSelectedAccount] = useState<any>(null);
  const [items, setItems] = useState<any[]>([]);
  const [parsedItems, setParsedItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [parserLoading, setParserLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [parserUrl, setParserUrl] = useState("");
  const [parserConsent, setParserConsent] = useState(false);
  const [activeTab, setActiveTab] = useState<string>("listings");
  const [kbModalOpen, setKbModalOpen] = useState(false);
  const [kbModalAccount, setKbModalAccount] = useState("global");
  const [kbText, setKbText] = useState("");
  const [kbFiles, setKbFiles] = useState<any[]>([]);
  const [kbSaving, setKbSaving] = useState(false);
  const [kbDragOver, setKbDragOver] = useState(false);
  const [templates, setTemplates] = useState([defaultTemplate]);
  const [templatesLoaded, setTemplatesLoaded] = useState(false);
  const [mainCat, setMainCat] = useState("Услуги");
  const [subCat, setSubCat] = useState(Object.keys(AVITO_CATEGORIES)[0] ? AVITO_CATEGORIES["Услуги"].subs[0] : "");
  const [showTemplateForm, setShowTemplateForm] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<any>(null);
  const [listingFilter, setListingFilter] = useState<string | null>(null);
  const [allFeeds, setAllFeeds] = useState<any[]>([]);
  const [feedsLoading, setFeedsLoading] = useState(false);
  const loadAllFeeds = async () => {
    setFeedsLoading(true);
    try {
      const r = await apiFetch(`/api/avito/all_feeds?account_id=${currentAccount}`);
      const d = await r.json();
      if (d.status === "ok") setAllFeeds(d.feeds || []);
    } catch {}
    setFeedsLoading(false);
  };
  const copyFeedUrl = (url: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(url);
      showBorisNotify("📋 Скопировано", "Ссылка на фид скопирована в буфер обмена.");
    }
  };
  const checkFeed = (url: string) => {
    copyFeedUrl(url);
    window.open("https://autoload.avito.ru/format/xmlcheck/", "_blank");
  };
  const [openedFeedAccount, setOpenedFeedAccount] = useState<string | null>(null);
  const [feedItemsFull, setFeedItemsFull] = useState<any[]>([]);
  const [feedItemsLoading, setFeedItemsLoading] = useState(false);
  const [editingFeedItem, setEditingFeedItem] = useState<any | null>(null);
  const doImportActive = async (accId: string) => {
    const fd = new FormData();
    fd.append("account_id", accId);
    try {
      const r = await apiFetch("/api/avito/feed_import_active", { method: "POST", body: fd });
      const d = await r.json();
      if (d.status === "ok") { setOpenedFeedAccount(accId); setFeedTab("active"); loadActiveItems(accId); loadFeedItemsFull(accId); showBorisNotify("📥 Готово", `Найдено активных на Avito: ${d.found_active}. Добавлено новых: ${d.added}. Всего активных: ${d.total_active}.\n\nОни в разделе «Активные на Avito» — отдельно от вашего фида.`); }
      else showBorisNotify("Ошибка", d.message || "Не удалось загрузить");
    } catch { showBorisNotify("Ошибка", "Не удалось выгрузить с Avito"); }
  };
  const importActiveFromAvito = (accId: string) => {
    showBorisConfirm("Загрузить объявления с Avito", "Загрузить все ваши активные объявления с Avito? Они появятся в разделе «Активные на Avito» — отдельно от вашего фида. Уже загруженные не дублируются.", () => doImportActive(accId), "Загрузить");
  };
  const doSendFeedToAvito = async (accId: string) => {
    const fd = new FormData();
    fd.append("account_id", accId);
    try {
      const r = await apiFetch("/api/avito/feed_send_to_avito", { method: "POST", body: fd });
      const d = await r.json();
      showBorisNotify(d.status === "ok" ? "🚀 Отправлено" : "Ошибка", d.message || "");
    } catch { showBorisNotify("Ошибка", "Не удалось отправить"); }
  };
  const sendFeedToAvito = (accId: string) => {
    showBorisConfirm("Отправить в Avito", "Запустить выгрузку фида на Avito сейчас? Объявления обновятся в ближайшее время.", () => doSendFeedToAvito(accId), "Отправить");
  };
  const exportFeedXlsx = (accId: string) => {
    window.open(`/api/avito/feed_export_xlsx?account_id=${accId}`, "_blank");
  };
  const importFeedXlsx = async (accId: string, file: File) => {
    const fd = new FormData();
    fd.append("account_id", accId);
    fd.append("file", file);
    try {
      const r = await apiFetch("/api/avito/feed_import_xlsx", { method: "POST", body: fd });
      const d = await r.json();
      if (d.status === "ok") { loadFeedItemsFull(accId); loadAllFeeds(); showBorisNotify("✅ Загружено из Excel", `Обновлено объявлений: ${d.updated}. Изменения сохранены в фиде.`); }
      else showBorisNotify("Ошибка", d.message || "Не удалось загрузить");
    } catch { showBorisNotify("Ошибка", "Не удалось загрузить файл"); }
  };
  const [feedTab, setFeedTab] = useState<"feed"|"active"|"schedule">("feed");
  const [borisModal, setBorisModal] = useState<{open: boolean, title: string, message: string, onConfirm: (() => void) | null, confirmText: string}>({open: false, title: "", message: "", onConfirm: null, confirmText: "OK"});
  const showBorisNotify = (title: string, message: string) => setBorisModal({open: true, title, message, onConfirm: null, confirmText: "OK"});
  const showBorisConfirm = (title: string, message: string, onConfirm: () => void, confirmText = "Подтвердить") => setBorisModal({open: true, title, message, onConfirm, confirmText});
  const closeBorisModal = () => setBorisModal({open: false, title: "", message: "", onConfirm: null, confirmText: "OK"});
  const [activeItems, setActiveItems] = useState<any[]>([]);
  const [activeLoading, setActiveLoading] = useState(false);
  const loadActiveItems = async (accId: string) => {
    setActiveLoading(true);
    try {
      const r = await apiFetch(`/api/avito/active_items_full?account_id=${accId}`);
      const d = await r.json();
      if (d.status === "ok") setActiveItems(d.items || []);
    } catch {}
    setActiveLoading(false);
  };
  const loadFeedItemsFull = async (accId: string) => {
    setFeedItemsLoading(true);
    setOpenedFeedAccount(accId);
    try {
      const r = await apiFetch(`/api/avito/feed_items_full?account_id=${accId}`);
      const d = await r.json();
      if (d.status === "ok") setFeedItemsFull(d.items || []);
    } catch {}
    setFeedItemsLoading(false);
  };
  const [feedPhotoGallery, setFeedPhotoGallery] = useState<{banners: string[], images: string[]}>({banners: [], images: []});
  const [feedPhotoPickerOpen, setFeedPhotoPickerOpen] = useState<"banners"|"images"|null>(null);
  const loadFeedPhotoGallery = async (accId: string) => {
    try {
      const [br, ir] = await Promise.all([
        apiFetch(`/api/banners/list?account_id=${accId}`).then(r => r.json()).catch(() => ({})),
        apiFetch(`/api/avito/images_list?account_id=${accId}`).then(r => r.json()).catch(() => ({})),
      ]);
      const banners: string[] = [];
      if (br.status === "ok" && br.banners) Object.values(br.banners as {[k:string]:string[]}).forEach(arr => banners.push(...arr));
      const imgs: string[] = [];
      if (ir.status === "ok" && ir.folders) Object.values(ir.folders as {[k:string]:string[]}).forEach(arr => imgs.push(...arr));
      setFeedPhotoGallery({banners, images: imgs});
    } catch {}
  };
  const addFeedPhoto = (url: string) => {
    setEditingFeedItem((prev: any) => ({...prev, images: [...(prev.images || []), url]}));
  };
  const removeFeedPhoto = (idx: number) => {
    setEditingFeedItem((prev: any) => ({...prev, images: (prev.images || []).filter((_: any, i: number) => i !== idx)}));
  };
  const uploadFeedPhoto = async (accId: string, files: FileList) => {
    const fd = new FormData();
    fd.append("account_id", accId);
    fd.append("folder", "общая");
    for (let i = 0; i < files.length; i++) fd.append("files", files[i]);
    try {
      const r = await apiFetch("/api/avito/upload_images", { method: "POST", body: fd });
      const d = await r.json();
      const urls = d.urls || d.uploaded || [];
      urls.forEach((u: string) => addFeedPhoto(u));
    } catch { showBorisNotify("Ошибка", "Не удалось загрузить фото"); }
  };
  const feedDescRef = useRef<HTMLTextAreaElement>(null);
  const [textTemplates, setTextTemplates] = useState<any[]>([]);
  const saveAsTextTemplate = async (ad: any) => {
    const name = window.prompt("Название шаблона (для себя, чтобы узнавать в списке):", ad.title.slice(0, 40));
    if (!name) return;
    try {
      const r = await apiFetch("/api/avito/text_templates/create", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, name, title_template: ad.title, description_template: ad.description })
      });
      const d = await r.json();
      if (d.status === "ok") showBorisNotify("💾 Сохранено", `Шаблон «${name}» добавлен в библиотеку. Теперь его можно выбрать при редактировании объявлений в фиде, и Борис будет отслеживать его эффективность.`);
      else showBorisNotify("Ошибка", d.message || "Не удалось сохранить");
    } catch { showBorisNotify("Ошибка", "Не удалось сохранить шаблон"); }
  };
  const loadTextTemplates = async (accId: string) => {
    try {
      const r = await apiFetch(`/api/avito/text_templates?account_id=${accId}`);
      const d = await r.json();
      if (d.status === "ok") setTextTemplates(d.templates || []);
    } catch {}
  };
  const [templateEff, setTemplateEff] = useState<any[]>([]);
  const loadTemplateEffectiveness = async (accId: string) => {
    try {
      const r = await apiFetch(`/api/avito/text_templates/effectiveness?account_id=${accId}`);
      const d = await r.json();
      if (d.status === "ok") setTemplateEff(d.templates || []);
    } catch {}
  };
  const deleteTextTemplate = async (tplId: string) => {
    if (!window.confirm("Удалить этот шаблон из библиотеки?")) return;
    try {
      await apiFetch("/api/avito/text_templates/delete", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, template_id: tplId })
      });
      loadTemplateEffectiveness(currentAccount);
    } catch {}
  };
  const applyTextTemplate = (tplId: string) => {
    if (!tplId) { setEditingFeedItem((prev: any) => ({...prev, template_id: null})); return; }
    const tpl = textTemplates.find(t => t.id === tplId);
    if (!tpl) return;
    setEditingFeedItem((prev: any) => ({...prev, template_id: tplId, title: tpl.title_template, description: tpl.description_template}));
  };
  const [feedEmojiOpen, setFeedEmojiOpen] = useState(false);
  const feedWrap = (before: string, after: string, ph: string) => {
    const ta = feedDescRef.current; if (!ta) return;
    const st = ta.selectionStart, en = ta.selectionEnd;
    const val = ta.value;
    const sel = val.slice(st, en) || ph;
    const nv = val.slice(0, st) + before + sel + after + val.slice(en);
    setEditingFeedItem((prev: any) => ({...prev, description: nv}));
    setTimeout(() => { ta.focus(); ta.selectionStart = st + before.length; ta.selectionEnd = st + before.length + sel.length; }, 0);
  };
  const feedInsert = (snippet: string) => {
    const ta = feedDescRef.current; if (!ta) return;
    const st = ta.selectionStart;
    const val = ta.value;
    const nv = val.slice(0, st) + snippet + val.slice(st);
    setEditingFeedItem((prev: any) => ({...prev, description: nv}));
    setTimeout(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = st + snippet.length; }, 0);
  };
  const saveFeedItem = async () => {
    if (!editingFeedItem || !openedFeedAccount) return;
    try {
      const r = await apiFetch("/api/avito/feed_item/update", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: openedFeedAccount,
          item_id: editingFeedItem.id,
          title: editingFeedItem.title,
          price: Number(editingFeedItem.price),
          description: editingFeedItem.description,
          images: editingFeedItem.images || [],
          template_id: editingFeedItem.template_id || null,
        })
      });
      const d = await r.json();
      if (d.status === "ok") {
        const savedId = editingFeedItem.id;
        const savedTitle = editingFeedItem.title;
        const savedPrice = Number(editingFeedItem.price);
        const savedDesc = editingFeedItem.description;
        setEditingFeedItem(null);
        setFeedItemsFull(prev => prev.map(it => it.id === savedId ? {...it, title: savedTitle, price: savedPrice, description: savedDesc} : it));
      } else notify("Ошибка: " + (d.message || ""));
    } catch (e) { showBorisNotify("Ошибка", "Не удалось сохранить"); }
  };
  const doDeleteFeedItem = async (accId: string, itemId: string) => {
    try {
      const r = await apiFetch("/api/avito/feed_item/delete", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({account_id: accId, item_id: itemId})
      });
      const d = await r.json();
      if (d.status === "ok") {
        setFeedItemsFull(prev => prev.filter(it => it.id !== itemId));
        loadAllFeeds();
      }
    } catch {}
  };
  const deleteFeedItem = (accId: string, itemId: string) => {
    showBorisConfirm("Удалить объявление", "Удалить это объявление из фида?", () => doDeleteFeedItem(accId, itemId), "Удалить");
  };
  const [draftItems, setDraftItems] = useState<any[]>([]);
  const [draftsLoading, setDraftsLoading] = useState(false);
  const [draftBatchFilter, setDraftBatchFilter] = useState<string | null>(null);
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [newTemplate, setNewTemplate] = useState({
    name: "", titleTemplate: "{название}", description: "{описание}",
    priceType: "original", priceModifier: 0, cities: ["Москва"],
    category: "Предложение услуг", schedule: "09:00",
    scheduleDays: ["пн","вт","ср","чт","пт"], delayMin: 2, delayMax: 3,
  });
  const [accounts, setAccounts] = useState<any[]>([]);
  const navRouter = useRouter();
  const [userRole, setUserRole] = useState<string>("");
  const [showAccountsList, setShowAccountsList] = useState(false);
  const [crmOpen, setCrmOpen] = useState(false);
  const [livePrices, setLivePrices] = useState<any>(null);
  const [econData, setEconData] = useState<any>(null);
  const [econOpen, setEconOpen] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setUserRole(typeof window !== "undefined" && localStorage.getItem("boris_user_role") || "");
    }
  }, []);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
    if (!token) {
      navRouter.push("/login");
      return;
    }
    apiFetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } })
      .then(r => { if (!r.ok) throw new Error("unauthorized"); return r.json(); })
      .catch(() => {
        localStorage.removeItem("boris_token");
        navRouter.push("/login");
      });

    if (typeof window !== "undefined" && localStorage.getItem("boris_just_registered")) {
      localStorage.removeItem("boris_just_registered");
      showBorisNotify(
        "Добро пожаловать в БОРИС! 👋",
        "Ваш аккаунт создан, пробный период — 4 дня. Чтобы Борис начал публиковать и вести объявления, укажите Client ID и Client Secret вашего Avito API во вкладке «Компания» — без них он не сможет подключиться к вашему кабинету Avito."
      );
    }
  }, []);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
    apiFetch("/api/accounts/list", { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.json())
      .then(data => {
        if (data.status === "ok") {
          setAccounts(data.accounts.map((a: any) => ({
            id: a.account_id,
            name: a.name,
            login: "",
            comment: "",
            company: {},
          })));
          if (!data.accounts || data.accounts.length === 0) {
            setShowAddForm(true);
            setStep(0);
          }
        } else {
          setShowAddForm(true);
          setStep(0);
        }
      })
      .catch(() => { setShowAddForm(true); setStep(0); });
  }, []);
  const [newAccount, setNewAccount] = useState({
    name: "", login: "", password: "", comment: "", client_id: "", client_secret: "",
    companyWebsite: "", companyDescription: "", companyNiche: "", companyTone: "Дружелюбный", companyAdvantages: ""
  });
  const [analysisQuery, setAnalysisQuery] = useState("");
  const [analysisCities, setAnalysisCities] = useState("Москва, Санкт-Петербург, Казань");
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisResults, setAnalysisResults] = useState<any[]>([]);
  const [analysisProgress, setAnalysisProgress] = useState<{done: number, total: number} | null>(null);
  const [autopilotMode, setAutopilotMode] = useState("always_ask");
  const [republishCandidates, setRepublishCandidates] = useState<any[]>([]);
  const [republishLoading, setRepublishLoading] = useState(false);
  const [republishTotal, setRepublishTotal] = useState<number | null>(null);
  const [kpiTargetLeads, setKpiTargetLeads] = useState(0);
  const [kpiMaxCpl, setKpiMaxCpl] = useState(0);
  const [kpiLeadTemp, setKpiLeadTemp] = useState("любые");
  const [kpiSaved, setKpiSaved] = useState(false);
  const [republishMinViews, setRepublishMinViews] = useState(10);
  const [republishZeroDays, setRepublishZeroDays] = useState(7);
  const [republishEnabled, setRepublishEnabled] = useState(true);
  const [republishSettingsSaved, setRepublishSettingsSaved] = useState(false);
  const [kpiCheckResult, setKpiCheckResult] = useState<any>(null);
  const [kpiPlanIdPrefix, setKpiPlanIdPrefix] = useState("");
  const [kpiPlan, setKpiPlan] = useState<any>(null);
  const [kpiPlanExecuting, setKpiPlanExecuting] = useState(false);
  const [wordstatQuery, setWordstatQuery] = useState("");
  const [wordstatResult, setWordstatResult] = useState<any>(null);
  const [wordstatLoading, setWordstatLoading] = useState(false);
  const [wordstatHistory, setWordstatHistory] = useState<any[]>([]);
  const [planItems, setPlanItems] = useState<any[]>([]);
  const [newPlanItemText, setNewPlanItemText] = useState("");
  const [bannerFormat, setBannerFormat] = useState("infographic");
  const [customBannerW, setCustomBannerW] = useState("");
  const [customBannerH, setCustomBannerH] = useState("");
  const [bannerPrompt, setBannerPrompt] = useState("");
  const [bannerAccentColor, setBannerAccentColor] = useState("#FF6B35");
  const [bannerVaryCount, setBannerVaryCount] = useState(1);
  const [bannerVaryColors, setBannerVaryColors] = useState(false);
  const [bannerVaryImage, setBannerVaryImage] = useState(false);
  const [bannerVaryIcons, setBannerVaryIcons] = useState(false);
  const [bannerVaryAll, setBannerVaryAll] = useState(false);
  const [bannerQuality, setBannerQuality] = useState("medium");
  const [bannerLoading, setBannerLoading] = useState(false);
  const [bannerResultUrls, setBannerResultUrls] = useState<string[]>([]);
  const [webdesignFolder, setWebdesignFolder] = useState<string | null>(null);
  const [showBannerPricing, setShowBannerPricing] = useState(false);
  const [lightboxUrl, setLightboxUrl] = useState("");
  const [bannerRefImageBase64, setBannerRefImageBase64] = useState("");
  const [bannerUseOwnPhoto, setBannerUseOwnPhoto] = useState(false);
  const [bannerOwnPhotoUrl, setBannerOwnPhotoUrl] = useState("");
  const [bannerRefImagePreview, setBannerRefImagePreview] = useState("");
  const [bannerExactText, setBannerExactText] = useState("");
  const [bannerGallery, setBannerGallery] = useState<{[key: string]: string[]}>({infographic: [], extended: [], max_carousel: []});
  const [selectedBanners, setSelectedBanners] = useState<string[]>([]);
  const [savedPrompts, setSavedPrompts] = useState<any[]>([]);

  const loadBannerGallery = () => {
    apiFetch(`/api/banners/list?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok" && d.banners) setBannerGallery(d.banners); })
      .catch(() => {});
  };

  const deleteBannerFile = (subfolder: string, url: string) => {
    const filename = url.split("/").pop();
    if (!window.confirm("Удалить этот баннер?")) return;
    apiFetch(`/api/banners/delete?account_id=${currentAccount}&subfolder=${subfolder}&filename=${filename}`, { method: "DELETE" })
      .then(r => r.json())
      .then(() => loadBannerGallery())
      .catch(() => alert("Не удалось удалить"));
  };

  const handleRefImageUpload = (e: any) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      setBannerRefImagePreview(result);
      setBannerRefImageBase64(result.split(",")[1] || "");
    };
    reader.readAsDataURL(file);
  };

  const loadSavedPrompts = async () => {
    try {
      const res = await apiFetch("https://boris-ai.pro/api/prompts/list?account_id=" + (currentAccount) + "&purpose=banner");
      const data = await res.json();
      setSavedPrompts(Array.isArray(data) ? data : []);
    } catch (e) { setSavedPrompts([]); }
  };

  const savePromptAsTemplate = async () => {
    if (!bannerPrompt.trim()) { alert("Сначала напишите промт"); return; }
    const title = window.prompt("Название шаблона:", bannerPrompt.slice(0, 40));
    if (!title) return;
    try {
      const res = await apiFetch("https://boris-ai.pro/api/prompts/save", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({account_id: currentAccount, title: title, text: bannerPrompt, purpose: "banner"})
      });
      const data = await res.json();
      if (data.status === "ok") { alert("Шаблон сохранён"); loadSavedPrompts(); }
      else alert("Не удалось сохранить");
    } catch (e) { alert("Ошибка связи с сервером"); }
  };

  const deleteSavedPrompt = async (id: number) => {
    if (!confirm("Удалить шаблон?")) return;
    try {
      await apiFetch("https://boris-ai.pro/api/prompts/" + id, {method: "DELETE"});
      loadSavedPrompts();
    } catch (e) {}
  };

  const toggleBannerSelect = (url: string) => {
    setSelectedBanners(prev => prev.includes(url) ? prev.filter(u => u !== url) : [...prev, url]);
  };

  const selectAllBanners = () => {
    const all: string[] = [];
    Object.values(bannerGallery).forEach(arr => arr.forEach(u => all.push(u)));
    setSelectedBanners(prev => prev.length === all.length ? [] : all);
  };

  const downloadSelectedBanners = async () => {
    if (selectedBanners.length === 0) { alert("Выберите баннеры"); return; }
    try {
      const res = await apiFetch("https://boris-ai.pro/api/banners/download_zip", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({paths: selectedBanners})
      });
      if (!res.ok) { alert("Ошибка при скачивании"); return; }
      const blob = await res.blob();
      const link = document.createElement("a");
      link.href = window.URL.createObjectURL(blob);
      link.download = "boris_banners.zip";
      link.click();
      window.URL.revokeObjectURL(link.href);
    } catch (e) { alert("Ошибка связи с сервером"); }
  };

  const deleteSelectedBanners = async () => {
    if (selectedBanners.length === 0) { alert("Выберите баннеры"); return; }
    if (!window.confirm(`Удалить ${selectedBanners.length} баннеров? Отменить нельзя.`)) return;
    for (const url of selectedBanners) {
      const m = url.match(/\/([^/]+)\/banners\/([^/]+)\/([^/?]+)/);
      if (m) {
        try { await apiFetch(`/api/banners/delete?account_id=${m[1]}&subfolder=${m[2]}&filename=${m[3]}`, { method: "DELETE" }); } catch {}
      }
    }
    setSelectedBanners([]);
    loadBannerGallery();
  };

  const generateBanner = async () => {
    if (!bannerPrompt.trim()) { alert("Опишите нишу и что нужно на баннере"); return; }
    if (bannerUseOwnPhoto && !bannerOwnPhotoUrl) { alert("Выберите фото из галереи или снимите галочку «своё фото»"); return; }
    setBannerLoading(true);
    setBannerResultUrls([]);
    try {
      const useOwnPhotoFlow = bannerUseOwnPhoto && (bannerFormat === "infographic" || bannerFormat === "custom");
      let res;
      if (useOwnPhotoFlow) {
        res = await apiFetch("/api/banners/infographic", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            account_id: currentAccount,
            title: bannerExactText.trim() || bannerPrompt.trim(),
            subtitle: "",
            price_text: "",
            icon_name: "star.svg",
            accent_color: bannerAccentColor,
            bg_color_top: bannerAccentColor,
            bg_color_bottom: bannerAccentColor,
            own_photo_url: bannerOwnPhotoUrl,
            ai_quality: bannerQuality,
          }),
        });
      } else {
        const body: any = {
          account_id: currentAccount,
          raw_description: bannerPrompt.trim(),
          format: bannerFormat === "custom" ? "infographic" : bannerFormat,
          accent_color: bannerAccentColor,
          quality: bannerQuality,
          include_contacts: false,
          reference_image_base64: bannerRefImageBase64,
          reference_image_urls: selectedExampleUrls,
          exact_text: bannerExactText.trim(),
          count: bannerVaryCount,
          vary_colors: bannerVaryColors,
          vary_image: bannerVaryImage,
          vary_icons: bannerVaryIcons,
          vary_all: bannerVaryAll,
        };

        res = await apiFetch("/api/banners/full_ai", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      const data = await res.json();
      if (data.status === "ok") {
        setBannerResultUrls((data.urls && data.urls.length > 1) ? data.urls : (data.url ? [data.url] : (data.urls || [])));
      } else {
        alert("Ошибка генерации: " + (data.detail || JSON.stringify(data)) + ". Попробуйте ещё раз — иногда бывает разовый сбой сети.");
      }
    } catch (e) {
      alert("Ошибка связи с сервером при генерации баннера");
    } finally {
      setBannerLoading(false);
    }
  };
  const [autopilotFrom, setAutopilotFrom] = useState("");
  const [autopilotTo, setAutopilotTo] = useState("");
  const [auditLog, setAuditLog] = useState<any[]>([]);
  const [step, setStep] = useState(0);
  const [wizardGoal, setWizardGoal] = useState('');
  const [wizardGoalText, setWizardGoalText] = useState('');
  const [publishStatus, setPublishStatus] = useState("");
  const [stretchTopic, setStretchTopic] = useState("");
  const [stretchCount, setStretchCount] = useState(10);
  const [stretchNames, setStretchNames] = useState<string[]>([]);
  const [stretchLoading, setStretchLoading] = useState(false);

  const generateStretchNames = async () => {
    if (!stretchTopic.trim()) { alert("Впишите тему партии"); return; }
    setStretchLoading(true);
    try {
      const res = await apiFetch("/api/avito/generate_names", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ topic: stretchTopic, count: stretchCount })
      });
      const data = await res.json();
      if (data.status === "ok") {
        setStretchNames(data.names);
      } else {
        alert("Не получилось: " + (data.message || "попробуйте ещё раз"));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setStretchLoading(false);
  };
  const [selectedItems, setSelectedItems] = useState<{[key:number]: boolean}>({});
  const [itemSchedules, setItemSchedules] = useState<{[key:number]: string}>({});
  const [bulkStartDate, setBulkStartDate] = useState("");
  const [bulkStartTime, setBulkStartTime] = useState("10:00");
  const [bulkInterval, setBulkInterval] = useState(30);
  const [borisOpen, setBorisOpen] = useState(false);
  const [pwStep, setPwStep] = useState<"off"|"task"|"questions"|"result">("off");
  const [pwTask, setPwTask] = useState<any>(null);
  const [pwAnswers, setPwAnswers] = useState<string[]>([]);
  const [pwResult, setPwResult] = useState("");
  const [pwLoading, setPwLoading] = useState(false);
  const PW_TASKS = [
    { id:"banner", icon:"\u{1F5BC}", name:"Баннеры и картинки", where:"вкладка «Баннеры и картинки» → поле описания при генерации",
      qs:["Что рекламируем — товар или услуга, опишите подробно (тип, цвет, размер, комплектация)?","Какая ниша и город?","Главное преимущество, которое должно бросаться в глаза?","Фирменные цвета или пожелания по стилю?","Что нельзя показывать или писать?"] },
    { id:"social", icon:"\u{1F4E3}", name:"Посты в соцсети", where:"страница «Социальные сети» → поле «Указания боту (промт) — как писать посты»",
      qs:["О чём канал и для кого пишем?","Какой тон — деловой, дружеский, экспертный?","Что должно быть в каждом посте обязательно (хештеги, призыв, контакты)?","Чего избегать — темы, слова, стиль?","Нужны ли вопросы к аудитории и как часто звать в личку?"] },
    { id:"manager", icon:"\u{1F91D}", name:"ИИ Менеджер по продажам", where:"вкладка «Продажи» → ИИ Менеджер → настройки ответов",
      qs:["Что продаёте и по какой цене (вилка)?","Какая цель диалога — взять телефон, записать на замер, довести до оплаты?","Какие частые возражения и как на них отвечать?","Что нельзя обещать клиенту?","Сроки, доставка, гарантии — что важно упомянуть?"] },
    { id:"rop", icon:"\u{1F9E0}", name:"ИИ Руководитель отдела продаж", where:"вкладка «Продажи» → ИИ РОП → чек-лист разбора",
      qs:["Что обязательно должен сделать менеджер в звонке?","Какие ошибки для вас критичны?","Какие слова и фразы запрещены в разговоре?","Какая цель звонка — замер, оплата, следующий шаг?","На что смотреть в первую очередь при разборе?"] },
    { id:"listing", icon:"\u{1F4CB}", name:"Объявления Avito", where:"вкладка «Объявления» → описание товара",
      qs:["Что продаёте — точное название и характеристики?","Ключевые преимущества перед конкурентами?","Цена и условия (торг, доставка, самовывоз)?","География — куда возите или где забирать?","Что обязательно указать в тексте?"] },
  ];
  const pwStart = () => { setPwStep("task"); setPwTask(null); setPwAnswers([]); setPwResult(""); };
  const pwPickTask = (t:any) => { setPwTask(t); setPwAnswers(new Array(t.qs.length).fill("")); setPwStep("questions"); };
  const pwGenerate = async () => {
    if (!pwTask) return;
    setPwLoading(true); setPwStep("result");
    const qa = pwTask.qs.map((q:string,i:number)=> q + " Ответ: " + (pwAnswers[i] || "не указано")).join(" | ");
    const msg = "Ты помогаешь клиенту составить промт-инструкцию для ИИ по задаче: " + pwTask.name + ". Ответы клиента: " + qa + ". Собери ГОТОВЫЙ текст инструкции на русском, который клиент вставит в настройки. Только сам текст инструкции, конкретными правилами, без вступлений и пояснений. Структурируй короткими абзацами или списком.";
    try {
      const res = await apiFetch("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({message: msg, history: []})});
      const d = await res.json();
      setPwResult(d.answer || "Не удалось собрать промт, попробуйте ещё раз");
    } catch { setPwResult("Ошибка связи. Попробуйте ещё раз."); }
    setPwLoading(false);
  };
  const [notifications, setNotifications] = useState<any[]>([]);
  const [borisInput, setBorisInput] = useState("");
  const [borisHistory, setBorisHistory] = useState<{role:string, content:string, plan?: any, planResolved?: boolean}[]>([]);
  const [borisLoading, setBorisLoading] = useState(false);
  const [genTopic, setGenTopic] = useState("");
  const [genCount, setGenCount] = useState(10);
  const [genPriceFrom, setGenPriceFrom] = useState(0);
  const [genPriceTo, setGenPriceTo] = useState(0);
  const [genExtra, setGenExtra] = useState("");
  const [genSample, setGenSample] = useState("");
  const [textSamples, setTextSamples] = useState<any[]>([]);
  const saveTextSample = () => {
    if (!genSample.trim()) { alert("Сначала впиши текст образца."); return; }
    const name = window.prompt("Название образца:", genSample.trim().slice(0, 40));
    if (!name) return;
    const next = [{ id: Date.now(), name, text: genSample, created_at: new Date().toISOString() }, ...textSamples];
    setTextSamples(next);
    apiFetch("/api/storage/save", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: "text_samples", value: next }) })
      .then(() => alert("Образец сохранён: " + name)).catch(() => alert("Ошибка сохранения"));
  };
  const deleteTextSample = (id: number) => {
    const next = textSamples.filter((x: any) => x.id !== id);
    setTextSamples(next);
    apiFetch("/api/storage/save", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: "text_samples", value: next }) }).catch(() => {});
  };
  const [genLoading, setGenLoading] = useState(false);
  const [genAds, setGenAds] = useState<any[]>([]);
  // --- Виджет поддержки ---
  const [supportOpen, setSupportOpen] = useState(false);
  const [supName, setSupName] = useState("");
  const [supContact, setSupContact] = useState("");
  const [supText, setSupText] = useState("");
  const [supSent, setSupSent] = useState(false);
  const [supSending, setSupSending] = useState(false);
  const [supTicket, setSupTicket] = useState<number | null>(null);
  const [supMy, setSupMy] = useState<any[]>([]);
  const [supMyOpen, setSupMyOpen] = useState(false);
  const loadMyTickets = async () => {
    try {
      const r = await apiFetch("/api/support/my");
      const d = await r.json();
      if (d.status === "ok") { setSupMy(d["обращения"] || []); setSupMyOpen(true); }
    } catch (e) {}
  };
  const sendSupport = async () => {
    if (!supText.trim()) { alert("Напишите сообщение"); return; }
    setSupSending(true);
    try {
      const r = await fetch("/api/support/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: supName, contact: supContact, text: supText, account_id: currentAccount }),
      });
      const d = await r.json();
      if (d.status === "ok") { setSupSent(true); setSupText(""); setSupTicket(d["номер"] || null); }
      else alert("Не удалось отправить, попробуйте позже");
    } catch (e) { alert("Ошибка сети"); }
    setSupSending(false);
  };
  const [lastBatchId, setLastBatchId] = useState<number | null>(null);
  const [genAdsHistory, setGenAdsHistory] = useState<any[]>([]);
  const [useBanner, setUseBanner] = useState(true);
  const [prefsLoaded, setPrefsLoaded] = useState(false);

  useEffect(() => {
    if (!prefsLoaded) return;
    const t = setTimeout(() => {
      apiFetch("/api/storage/save", { method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, key: "gen_sample", value: genSample }) }).catch(() => {});
    }, 800);
    return () => clearTimeout(t);
  }, [genSample, prefsLoaded]);
  const setUseBannerPersist = (v: boolean) => {
    setUseBanner(v);
    apiFetch("/api/storage/save", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: "use_banner", value: v }) }).catch(() => {});
  };

  const saveGenBatch = () => {
    if (genAds.length === 0) return;
    const guess = String(genAds[0]?.title || "Партия").replace(/[{}]/g, "").split("|")[0].slice(0, 60);
    const name = window.prompt("Название партии:", guess);
    if (!name) return;
    const batch = {
      id: Date.now(),
      topic: name,
      created_at: new Date().toISOString(),
      ads: genAds
    };
    const next = [batch, ...genAdsHistory];
    setGenAdsHistory(next);
    apiFetch("/api/storage/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: "gen_ads_history", value: next })
    })
      .then(() => {
        // очищаем витрину: партия теперь в архиве (История партий)
        setGenAds([]);
        apiFetch("/api/storage/save", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ account_id: currentAccount, key: "gen_ads", value: [] })
        }).catch(() => {});
        alert("Партия в архиве: " + name + " (" + batch.ads.length + " шт.). Витрина очищена — можно генерировать новые.");
      })
      .catch(() => alert("Ошибка сохранения"));
  };
  const [batchEffectiveness, setBatchEffectiveness] = useState<{[key:number]: any}>({});
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [expandedAds, setExpandedAds] = useState<Set<number>>(new Set());
  const toggleExpand = (i: number) => setExpandedAds(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s; });
  const [cloneTarget, setCloneTarget] = useState(10);
  // === Автосохранение и автозагрузка в БД ===
  useEffect(() => {
    const acc = (typeof window !== "undefined" && localStorage.getItem("boris_currentAccount")) || "";
    apiFetch(`/api/storage/load?account_id=${acc}&key=templates`)
      .then(r => r.json())
      .then(d => { if (d.value && Array.isArray(d.value) && d.value.length > 0) setTemplates(d.value); })
      .catch(() => {})
      .finally(() => setTemplatesLoaded(true));
    apiFetch(`/api/storage/load?account_id=${acc}&key=gen_ads`)
      .then(r => r.json())
      .then(d => { if (d.value && Array.isArray(d.value)) setGenAds(d.value); })
      .catch(() => {});
    apiFetch(`/api/storage/load?account_id=${acc}&key=gen_sample`)
      .then(r => r.json())
      .then(d => { if (typeof d.value === "string") setGenSample(d.value); })
      .catch(() => {});
    apiFetch(`/api/storage/load?account_id=${acc}&key=text_samples`)
      .then(r => r.json())
      .then(d => { if (d.value && Array.isArray(d.value)) setTextSamples(d.value); })
      .catch(() => {});
    apiFetch(`/api/storage/load?account_id=${acc}&key=text_samples`)
      .then(r => r.json())
      .then(d => { if (d.value && Array.isArray(d.value)) setTextSamples(d.value); })
      .catch(() => {});
    apiFetch(`/api/storage/load?account_id=${acc}&key=use_banner`)
      .then(r => r.json())
      .then(d => { if (typeof d.value === "boolean") setUseBanner(d.value); })
      .catch(() => {});
    apiFetch(`/api/storage/load?account_id=${acc}&key=feed_prefs`)
      .then(r => r.json())
      .then(d => {
        const v = d.value || {};
        if (v.feedImages && typeof v.feedImages === "object") setFeedImages(v.feedImages);
        if (typeof v.photosPerAd === "number") setPhotosPerAd(v.photosPerAd);
        if (typeof v.city === "string" && v.city) setGenAddress(v.city);
      })
      .catch(() => {})
      .finally(() => setPrefsLoaded(true));
    apiFetch(`/api/storage/load?account_id=${acc}&key=gen_ads_history`)
      .then(r => r.json())
      .then(d => { if (d.value && Array.isArray(d.value)) setGenAdsHistory(d.value); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!templatesLoaded) return;
    if (templates.length === 0) return;
    apiFetch("/api/storage/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: "templates", value: templates })
    }).catch(() => {});
  }, [templates, templatesLoaded]);

  useEffect(() => {
    if (genAds.length === 0) return;
    apiFetch("/api/storage/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: "gen_ads", value: genAds })
    }).catch(() => {});
  }, [genAds]);
  const [adTextPhotos, setAdTextPhotos] = useState<{[key: number]: string[]}>({});
  const [restOrderMode, setRestOrderMode] = useState<"order" | "random">("order");

  const toggleTextPhoto = (adIndex: number, url: string) => {
    setAdTextPhotos(prev => {
      const cur = prev[adIndex] || [];
      const next = cur.includes(url) ? cur.filter(u => u !== url) : [...cur, url];
      return {...prev, [adIndex]: next};
    });
  };

  const movePhoto = (adIndex: number, url: string, direction: -1 | 1) => {
    setAdPhotos(prev => {
      const list = [...(prev[adIndex] || [])];
      const i = list.indexOf(url);
      const j = i + direction;
      if (i === -1 || j < 0 || j >= list.length) return prev;
      [list[i], list[j]] = [list[j], list[i]];
      return {...prev, [adIndex]: list};
    });
  };

  const getGalleryPhotos = (i: number): string[] => {
    const sel = Object.keys(feedImages).filter(u => feedImages[u]);
    if (sel.length === 0) return [];
    const isB = (u: string) => useBanner && /banner|баннер|infograph|инфограф/i.test(decodeURIComponent(u));
    const bans = sel.filter(isB);
    const rest = sel.filter(u => !isB(u));
    const out: string[] = [];
    if (bans.length > 0) out.push(bans[i % bans.length]);
    const pool = rest.length > 0 ? rest : sel;
    const need = Math.max(0, photosPerAd - out.length);
    const start = (i * need) % pool.length;
    for (let k = 0; k < need && k < pool.length; k++) {
      const u = pool[(start + k) % pool.length];
      if (!out.includes(u)) out.push(u);
    }
    return out;
  };
  const getAdPhotos = (i: number): string[] =>
    (adPhotos[i] && adPhotos[i].length > 0) ? getFinalPhotoOrder(i) : getGalleryPhotos(i);

  const getFinalPhotoOrder = (adIndex: number): string[] => {
    const all = adPhotos[adIndex] || [];
    const textOnes = adTextPhotos[adIndex] || [];
    const textFirst = all.filter(u => textOnes.includes(u));
    let rest = all.filter(u => !textOnes.includes(u));
    if (restOrderMode === "random") {
      rest = [...rest].sort(() => Math.random() - 0.5);
    }
    return [...textFirst, ...rest];
  };
  const [showPublishConfirm, setShowPublishConfirm] = useState(false);
  const [showImageConfirm, setShowImageConfirm] = useState(false);
  const [photosPerAd, setPhotosPerAd] = useState(1);
  const [allFolders, setAllFolders] = useState<{[key: string]: string[]}>({});
  const [openedFolder, setOpenedFolder] = useState<string | null>(null);
  const [movePickerFor, setMovePickerFor] = useState<number | null>(null);

  const moveImageToFolder = async (imgIndex: number, targetFolder: string) => {
    const img = genImages[imgIndex];
    if (!img) return;
    try {
      const res = await apiFetch("/api/avito/move_image", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url: img.url, target_folder: targetFolder, account_id: currentAccount })
      });
      const data = await res.json();
      if (data.status === "ok") {
        setGenImages(prev => prev.filter((_, idx) => idx !== imgIndex));
        loadAllFolders();
      } else {
        alert("Не удалось перенести: " + (data.message || ""));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setMovePickerFor(null);
  };

  const deleteFolder = async (folder: string) => {
    if (!window.confirm(`Удалить пустую папку "${folder}"?`)) return;
    try {
      const res = await apiFetch(`/api/avito/delete_folder?folder=${encodeURIComponent(folder)}&account_id=${currentAccount}`, { method: "DELETE" });
      const data = await res.json();
      if (data.status === "ok") {
        loadAllFolders();
        if (openedFolder === folder) { setOpenedFolder(null); setGenImages([]); }
      } else {
        alert(data.message || "Не удалось удалить");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const renameFolderPrompt = async (folder: string) => {
    const newName = window.prompt("Новое название папки:", folder);
    if (!newName || newName === folder) return;
    try {
      const res = await apiFetch("/api/avito/rename_folder", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ old_name: folder, new_name: newName, account_id: currentAccount })
      });
      const data = await res.json();
      if (data.status === "ok") {
        loadAllFolders();
        if (openedFolder === folder) setOpenedFolder(data.new_name);
      } else {
        alert("Не удалось переименовать: " + (data.message || ""));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const createNewFolder = async (isPersonal: boolean) => {
    const name = window.prompt(isPersonal ? "Название новой личной папки:" : "Название новой папки:");
    if (!name) return;
    const folderName = isPersonal ? `Личное_${name}` : name;
    try {
      const res = await apiFetch("/api/avito/create_folder", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ folder: folderName, account_id: currentAccount })
      });
      const data = await res.json();
      if (data.status === "ok") {
        loadAllFolders();
      } else {
        alert(data.message || "Не удалось создать папку");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const loadAllFolders = () => {
    apiFetch(`/api/avito/images_list?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok" && d.folders) setAllFolders(d.folders); })
      .catch(() => {});
  };

  const openFolder = (folder: string) => {
    setOpenedFolder(folder);
    setImgFolder(folder);
    const urls = allFolders[folder] || [];
    setGenImages(urls.map((u: string) => ({url: u, prompt: "из папки: " + folder})));
  };

  useEffect(() => {
    loadAllFolders();
  }, []);
  const [citiesData, setCitiesData] = useState<{[region: string]: string[]}>({});
  const [metroData, setMetroData] = useState<{[city: string]: string[]}>({});
  const [selectedRegion, setSelectedRegion] = useState("");
  const [selectedCity, setSelectedCity] = useState("");
  const [showMetroPicker, setShowMetroPicker] = useState(false);
  const [selectedMetro, setSelectedMetro] = useState("");

  useEffect(() => {
    apiFetch("/api/storage/load?account_id=global&key=cities_50k")
      .then(r => r.json())
      .then(d => { if (d.value) setCitiesData(d.value); })
      .catch(() => {});
    apiFetch("/api/storage/load?account_id=global&key=metro_stations")
      .then(r => r.json())
      .then(d => { if (d.value) setMetroData(d.value); })
      .catch(() => {});
  }, []);

  const applyCityToAddress = (city: string) => {
    setSelectedCity(city);
    setSelectedMetro("");
    setGenAddress(city);
  };

  const [bulkCities, setBulkCities] = useState<string[]>([]);
  const [topNCount, setTopNCount] = useState("20");
  const [multiRegionMode, setMultiRegionMode] = useState(false);
  const [pickedRegions, setPickedRegions] = useState<string[]>([]);
  // "РФ 100 000+" уже отсортирован по убыванию численности — берём первые N
  const applyTopRussia = () => {
    const all = citiesData["РФ 100 000+"] || citiesData["Миллионники РФ"] || [];
    const n = Math.max(1, Math.min(Number(topNCount) || 20, all.length));
    const picked = all.slice(0, n);
    if (picked.length === 0) { alert("Справочник городов не загружен"); return; }
    setBulkCities(picked); setBulkMetro([]);
    alert(`Выбрано ${picked.length} крупнейших городов России. Каждому объявлению партии достанется случайный город.`);
  };
  const toggleRegion = (r: string) => {
    setPickedRegions(prev => prev.includes(r) ? prev.filter(x=>x!==r) : [...prev, r]);
  };
  const applyMultiRegions = () => {
    const cities: string[] = [];
    pickedRegions.forEach(r => (citiesData[r] || []).forEach(c => { if(!cities.includes(c)) cities.push(c); }));
    if (cities.length === 0) { alert("Отметьте хотя бы один регион"); return; }
    setBulkCities(cities); setBulkMetro([]);
    alert(`Выбрано ${cities.length} городов из ${pickedRegions.length} регионов. Каждому объявлению партии достанется случайный город.`);
  };
  const [bulkMetro, setBulkMetro] = useState<string[]>([]);

  const selectAllCitiesInRegion = () => {
    const cities = citiesData[selectedRegion] || [];
    if (cities.length === 0) { alert("Сначала выберите регион"); return; }
    setBulkCities(cities);
    setBulkMetro([]);
    alert(`Выбрано ${cities.length} городов региона "${selectedRegion}". Каждому объявлению партии достанется случайный город.`);
  };

  const selectAllMetroInCity = () => {
    const stations = metroData[selectedCity] || [];
    if (stations.length === 0) { alert("В этом городе нет метро"); return; }
    setBulkMetro(stations);
    setBulkCities([]);
    setShowMetroPicker(false);
    alert(`Выбрано ${stations.length} станций метро города "${selectedCity}". Каждому объявлению партии достанется случайная станция.`);
  };

  const getRandomAddressForAd = (): string => {
    if (bulkMetro.length > 0) {
      const st = bulkMetro[Math.floor(Math.random() * bulkMetro.length)];
      return `${selectedCity}, м. ${st}`;
    }
    if (bulkCities.length > 0) {
      return bulkCities[Math.floor(Math.random() * bulkCities.length)];
    }
    return genAddress || "Москва";
  };

  const applyMetroToAddress = (station: string) => {
    setSelectedMetro(station);
    setGenAddress(selectedCity ? `${selectedCity}, м. ${station}` : `м. ${station}`);
    setShowMetroPicker(false);
  };

  const cloneAds = () => {
    if (genAds.length === 0) { alert("Сначала сгенерируйте хотя бы одно объявление"); return; }
    if (cloneTarget <= genAds.length) { alert("Целевое число должно быть больше текущего (" + genAds.length + ")"); return; }
    const base = [...genAds];
    const result = [...base];
    let i = 0;
    while (result.length < cloneTarget) {
      result.push({...base[i % base.length]});
      i++;
    }
    setGenAds(result);
    setAdPhotos(prev => {
      const upd = {...prev};
      for (let j = base.length; j < result.length; j++) {
        const srcIdx = j % base.length;
        if (prev[srcIdx]) upd[j] = [...prev[srcIdx]];
      }
      return upd;
    });
  };
  const [photoPickerIndex, setPhotoPickerIndex] = useState<number | null>(null);
  const [adPhotos, setAdPhotos] = useState<{[key: number]: string[]}>({});

  const uploadForAd = async (adIndex: number, files: FileList | null) => {
    if (!files || files.length === 0) return;
    const formData = new FormData();
    formData.append("folder", imgFolder || "общая");
    formData.append("account_id", currentAccount);
    Array.from(files).forEach(f => formData.append("files", f));
    try {
      const res = await apiFetch("/api/avito/upload_images", { method: "POST", body: formData });
      const data = await res.json();
      if (data.status === "ok" && data.images) {
        setAdPhotos(prev => ({...prev, [adIndex]: [...(prev[adIndex]||[]), ...data.images]}));
        setAllImages(prev => [...data.images, ...prev]);
      } else {
        alert("Не удалось загрузить файлы");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const uploadForPool = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const formData = new FormData();
    formData.append("folder", imgFolder || "общая");
    formData.append("account_id", currentAccount);
    Array.from(files).forEach(f => formData.append("files", f));
    try {
      const res = await apiFetch("/api/avito/upload_images", { method: "POST", body: formData });
      const data = await res.json();
      if (data.status === "ok" && data.images) {
        setAllImages(prev => [...data.images, ...prev]);
        const upd: {[key:string]: boolean} = {};
        data.images.forEach((u: string) => { upd[u] = true; });
        setFeedImages(prev => ({...prev, ...upd}));
      } else {
        alert("Не удалось загрузить файлы");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const toggleAdPhoto = (adIndex: number, url: string) => {
    setAdPhotos(prev => {
      const cur = prev[adIndex] || [];
      const next = cur.includes(url) ? cur.filter(u => u !== url) : [...cur, url];
      return {...prev, [adIndex]: next};
    });
  };
  const [publishMode, setPublishMode] = useState<"now" | "schedule">("now");
  const [publishDate, setPublishDate] = useState("");
  const [publishTime, setPublishTime] = useState("09:00");
  const [publishTimezone, setPublishTimezone] = useState("Московское время");
  const TIMEZONES = ["Калининградская область (- 1мск)","Московское время","Самарское время (+1ч мск)","Екатеринбургское время (+2ч мск)","Омское время (+3ч мск)","Красноярское время (+4ч мск)","Иркутское время (+5ч мск)","Якутское время (+6ч мск)","Владивостокское время (+7ч мск)","Магаданское время (+8ч мск)","Камчатское время (+9ч мск)"];
  const [genGoal, setGenGoal] = useState("call");
  const [genLength, setGenLength] = useState("medium");
  const [useMyAds, setUseMyAds] = useState(false);
  const [genAddress, setGenAddress] = useState("Москва");
  const [feedUrl, setFeedUrl] = useState("");
  const [showConfirm, setShowConfirm] = useState(false);
  const [imgPrompt, setImgPrompt] = useState("");
  const [imgStyle, setImgStyle] = useState("фотореализм, яркий, сочный");
  const [imgLoading, setImgLoading] = useState(false);
  const [genImages, setGenImages] = useState<any[]>([]);

  const [imgCount, setImgCount] = useState(1);
  const [imgFolder, setImgFolder] = useState("общая");
  const [dlUrl, setDlUrl] = useState("");

  const [imgQueueStatus, setImgQueueStatus] = useState("");
  const [currentAccount, setCurrentAccount] = useState("");
  // Гидрация из localStorage только на клиенте (после mount) - иначе SSR mismatch #418
  useEffect(() => {
    if (typeof window === "undefined") return;
    const v = localStorage.getItem("boris_view"); if (v) setView(v);
    const t = localStorage.getItem("boris_activeTab"); if (t) setActiveTab(t);
    const a = localStorage.getItem("boris_currentAccount"); if (a) setCurrentAccount(a);
  }, []);

  useEffect(() => {
    if (!currentAccount && accounts.length > 0) setCurrentAccount(accounts[0].id);
  }, []);

  const [bannerShowcase, setBannerShowcase] = useState<any[]>([]);
  const [selectedExampleUrls, setSelectedExampleUrls] = useState<string[]>([]);
  const [showBrowseAllBanners, setShowBrowseAllBanners] = useState(false);
  const [allBannersList, setAllBannersList] = useState<any[]>([]);
  const [browseAllLoading, setBrowseAllLoading] = useState(false);

  const loadBannerShowcase = async () => {
    try {
      const r = await apiFetch(`/api/banners/showcase?account_id=${currentAccount}`);
      const data = await r.json();
      if (data.status === "ok") setBannerShowcase(data.showcase || []);
    } catch (e) { console.error("Не удалось загрузить витрину баннеров", e); }
  };

  const loadAllBanners = async () => {
    setBrowseAllLoading(true);
    try {
      const r = await apiFetch("/api/banners/browse_all?limit=200");
      const data = await r.json();
      if (data.status === "ok") setAllBannersList(data.banners || []);
    } catch (e) { console.error("Не удалось загрузить все баннеры", e); }
    setBrowseAllLoading(false);
  };

  const addBannerToShowcase = async (url: string) => {
    try {
      await apiFetch("/api/banners/showcase/add", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url, account_id: currentAccount }),
      });
      loadBannerShowcase();
    } catch (e) { alert("Не удалось добавить в витрину"); }
  };

  const removeBannerFromShowcase = async (url: string) => {
    try {
      await apiFetch("/api/banners/showcase/remove", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url, account_id: currentAccount }),
      });
      loadBannerShowcase();
      setSelectedExampleUrls((prev) => prev.filter((u) => u !== url));
    } catch (e) { alert("Не удалось убрать из витрины"); }
  };

  const toggleExampleSelected = (url: string) => {
    setSelectedExampleUrls((prev) => {
      if (prev.includes(url)) return prev.filter((u) => u !== url);
      if (prev.length >= 3) { alert("Можно выбрать максимум 3 примера"); return prev; }
      return [...prev, url];
    });
  };

  const [showcaseUploading, setShowcaseUploading] = useState(false);

  const uploadOwnExampleToShowcase = (file: File): Promise<void> => {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const base64 = (reader.result as string);
          await apiFetch("/api/banners/showcase/upload", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ image_base64: base64, filename: file.name, account_id: currentAccount }),
          });
        } catch (e) {
          console.error("Не удалось загрузить", file.name, e);
        }
        resolve();
      };
      reader.readAsDataURL(file);
    });
  };

  const uploadMultipleExamplesToShowcase = async (files: FileList) => {
    setShowcaseUploading(true);
    for (let i = 0; i < files.length; i++) {
      await uploadOwnExampleToShowcase(files[i]);
    }
    await loadBannerShowcase();
    setShowcaseUploading(false);
  };

  useEffect(() => { loadBannerShowcase(); }, [currentAccount]);

  const [companyForm, setCompanyForm] = useState({website:"", niche:"", description:"", advantages:"", tone:""});
  const [companySaving, setCompanySaving] = useState(false);
  const [companySavedMsg, setCompanySavedMsg] = useState("");

  const loadCompanyForm = async (accId: string) => {
    try {
      const r = await apiFetch(`/api/accounts/${accId}`);
      const data = await r.json();
      if (data.status === "ok" && data.account) {
        setCompanyForm({
          website: data.account.company_website || "",
          niche: data.account.company_niche || "",
          description: data.account.company_description || "",
          advantages: data.account.company_advantages || "",
          tone: data.account.company_tone || "",
        });
      }
    } catch (e) {
      console.error("Не удалось загрузить данные о компании", e);
    }
  };

  useEffect(() => {
    if (currentAccount) loadCompanyForm(currentAccount);
  }, [currentAccount]);

  const saveCompanyForm = async () => {
    setCompanySaving(true);
    setCompanySavedMsg("");
    try {
      const r = await apiFetch(`/api/accounts/${currentAccount}/update`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          company_website: companyForm.website,
          company_niche: companyForm.niche,
          company_description: companyForm.description,
          company_advantages: companyForm.advantages,
          company_tone: companyForm.tone,
        }),
      });
      const data = await r.json();
      if (data.status === "ok") {
        setCompanySavedMsg("✅ Сохранено");
        setTimeout(() => setCompanySavedMsg(""), 3000);
      } else {
        setCompanySavedMsg("❌ Ошибка: " + (data.message || "не удалось сохранить"));
      }
    } catch (e) {
      setCompanySavedMsg("❌ Ошибка сети при сохранении");
    } finally {
      setCompanySaving(false);
    }
  };
  const [myBorisItems, setMyBorisItems] = useState<any[]>([]);

  // === Вкладка "Продажи" — Виртуальный менеджер по продажам ===
  const [salesItems, setSalesItems] = useState<any[]>([]);
  const [salesWhitelist, setSalesWhitelist] = useState<string[]>([]);
  const [salesLoading, setSalesLoading] = useState(false);
  const [salesUsage, setSalesUsage] = useState<any>(null);
  const [salesLeadStats, setSalesLeadStats] = useState<any>(null);
  const [managerBalance, setManagerBalance] = useState<any>(null);
  const [salesView, setSalesView] = useState<"home"|"mop"|"rop">("home");
  const [ropCalls, setRopCalls] = useState<any>(null);
  const [ropCallsLoading, setRopCallsLoading] = useState(false);
  const [ropDetail, setRopDetail] = useState<any>(null);
  const [ropDetailFor, setRopDetailFor] = useState<number|null>(null);
  const [reportFrom, setReportFrom] = useState("");
  const [reportTo, setReportTo] = useState("");
  const [reportMaxCalls, setReportMaxCalls] = useState(15);
  const [reportLoading, setReportLoading] = useState(false);
  const [savedReports, setSavedReports] = useState<any[]>([]);
  const [selectedReports, setSelectedReports] = useState<number[]>([]);
  const ropAcc = () => currentAccount || (accounts && accounts.length > 0 ? accounts[0].id : "") || (typeof window !== "undefined" ? localStorage.getItem("boris_currentAccount") : "") || "";
  const [ropLimits, setRopLimits] = useState<any>(null);
  const [ropShowPacks, setRopShowPacks] = useState(false);
  const [allLimits, setAllLimits] = useState<any>(null);
  const loadAllLimits = () => {
    const acc = ropAcc(); if (!acc) return;
    apiFetch(`/api/calltracking/all_limits?account_id=${acc}`)
      .then(r=>r.json()).then(d=>{ if (d.status==="ok" && !d.unlimited) setAllLimits(d); }).catch(()=>{});
  };
  const limitCard = (t:string, v:any, all:any, u:string, g:string) => (
    <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"14px", padding:"14px 16px", position:"relative", overflow:"hidden", flex:1, minWidth:"180px"}}>
      <div style={{position:"absolute", top:"-24px", right:"-24px", width:"70px", height:"70px", borderRadius:"50%", background:g, opacity:0.1}} />
      <div style={{fontSize:"22px", fontWeight:800, color: v>0 ? "#1D2939" : "#B42318", letterSpacing:"-0.02em"}}>{v} <span style={{fontSize:"14px", fontWeight:600, color:"#667085"}}>из {all} {u}</span></div>
      <div style={{fontSize:"14px", color:"#667085", marginTop:"2px"}}>{t}</div>
    </div>
  );
  const [ropSetup, setRopSetup] = useState<any>(null);
  const [syncing, setSyncing] = useState(false);
  const [trainData, setTrainData] = useState<any>(null);
  const [trainLoading, setTrainLoading] = useState(false);
  const trainManager = (apply:boolean) => {
    const acc = ropAcc(); if (!acc) return;
    setTrainLoading(true);
    apiFetch(`/api/calltracking/train_manager?account_id=${acc}`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({apply})})
      .then(r=>r.json()).then(d=>{
        setTrainLoading(false);
        if (d.status === "empty") { alert(d.message); return; }
        if (d.status !== "ok") { alert(d.message || "Не удалось обучить менеджера"); return; }
        if (apply) { setTrainData(null); alert("Готово! Правила добавлены в настройки ИИ-менеджера — он начнёт применять их в диалогах."); }
        else setTrainData(d);
      }).catch(()=>{ setTrainLoading(false); alert("Ошибка связи"); });
  };
  const loadRopSetup = () => {
    const acc = ropAcc(); if (!acc) return;
    apiFetch(`/api/calltracking/setup_check?account_id=${acc}`)
      .then(r=>r.json()).then(d=>{ if (d.status==="ok") setRopSetup(d); }).catch(()=>{});
  };
  const syncChats = () => {
    const acc = ropAcc(); if (!acc) return;
    setSyncing(true);
    apiFetch(`/api/messenger/sync_chats?account_id=${acc}&limit=50`, {method:"POST"})
      .then(r=>r.json()).then(d=>{
        setSyncing(false);
        if (d.status === "ok") alert(`Подтянул ${d.synced} диалогов (${d.messages} сообщений). Теперь можно собрать отчёт по перепискам.`);
        else alert(d.message || "Не удалось подтянуть переписки");
        loadRopSetup();
      }).catch(()=>{ setSyncing(false); alert("Ошибка связи"); });
  };
  const loadRopLimits = () => {
    const acc = ropAcc(); if (!acc) return;
    apiFetch(`/api/calltracking/limits?account_id=${acc}`)
      .then(r=>r.json()).then(d=>{ if (d.status==="ok") setRopLimits(d); }).catch(()=>{});
  };
  const loadSavedReports = () => {
    const acc = ropAcc(); if (!acc) return;
    apiFetch(`/api/calltracking/reports/list?account_id=${acc}`)
      .then(r=>r.json()).then(d=>{ if (d.status==="ok") setSavedReports(d.items||[]); }).catch(()=>{});
  };
  const downloadSavedReport = (id:number) => {
    const acc = ropAcc(); if (!acc) return;
    apiFetch(`/api/calltracking/reports/download?account_id=${acc}&id=${id}`)
      .then(r=>r.blob()).then(b=>{
        const url = URL.createObjectURL(b);
        const a = document.createElement("a");
        a.href = url; a.download = `otchet_zvonki_${id}.pdf`; a.click();
        URL.revokeObjectURL(url);
      }).catch(()=>{});
  };
  const deleteSavedReports = (all:boolean=false) => {
    const acc = ropAcc(); if (!acc) return;
    if (!all && selectedReports.length===0) return;
    if (!confirm(all ? "Удалить ВСЕ отчёты? Восстановить будет нельзя." : `Удалить выбранные отчёты (${selectedReports.length})? Восстановить будет нельзя.`)) return;
    apiFetch(`/api/calltracking/reports/delete?account_id=${acc}`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(all ? {all:true} : {ids:selectedReports})})
      .then(r=>r.json()).then(()=>{ setSelectedReports([]); loadSavedReports(); }).catch(()=>{});
  };
  const [chatsRepLoading, setChatsRepLoading] = useState(false);
  const downloadChatsReport = async () => {
    const acc = ropAcc(); if (!acc) return;
    try {
      const er = await apiFetch(`/api/calltracking/chats_report/estimate?account_id=${acc}&max_chats=${reportMaxCalls}`);
      const ed = await er.json();
      if (ed.status === "ok") {
        const eta = ed.eta_sec > 60 ? `~${Math.ceil(ed.eta_sec/60)} мин` : `~${ed.eta_sec || 5} сек`;
        const dayNote = ed.skipped_by_day_limit > 0 ? `\nНе войдут сегодня (дневной лимит ${ed.day_limit}): ${ed.skipped_by_day_limit}` : "";
        const txt = ed.new_chats > 0
          ? `Диалогов к разбору: ${ed.total}\n\nБудет разобрано (платно): ${ed.will_analyze}\nУже разобрано ранее: ${ed.already_done} — бесплатно${dayNote}\n\nСегодня разобрано: ${ed.day_used} из ${ed.day_limit}\n\nСписание: ~${ed.cost_rub} ₽\nВремя: ${eta}\n\nЗапустить разбор переписок?`
          : `Все ${ed.already_done} диалогов уже разобраны — новых списаний нет (~${ed.cost_rub} ₽).\n\nСформировать отчёт?`;
        if (!confirm(txt)) return;
      }
    } catch (e) {}
    const maskChats = confirm("Скрыть номера телефонов в отчёте?\n\nОК — номера будут замазаны, удобно показывать отчёт как пример.\nОтмена — оставить номера как есть.");
    setChatsRepLoading(true);
    let u = `/api/calltracking/chats_report?account_id=${acc}&max_chats=${reportMaxCalls}&mask_phones=${maskChats}`;
    if (userRole === "owner") u += `&owner=true`;
    apiFetch(u).then(async r=>{
      if (r.status === 402) { const d = await r.json(); alert(d.message || "Отчёты закончились"); setChatsRepLoading(false); return; }
      const b = await r.blob();
      const url = URL.createObjectURL(b);
      const a = document.createElement("a");
      a.href = url; a.download = "otchet_perepiski.pdf"; a.click();
      URL.revokeObjectURL(url); setChatsRepLoading(false);
      try { loadSavedReports(); loadRopLimits(); } catch(e) {}
    }).catch(()=>setChatsRepLoading(false));
  };
  const downloadRopReport = async () => {
    const acc = currentAccount || (accounts && accounts.length > 0 ? accounts[0].id : "") || "";
    if (!acc) return;
    // предпросмотр цены — ничего не тратит
    try {
      let eu = `/api/calltracking/report/estimate?account_id=${acc}&max_calls=${reportMaxCalls}`;
      if (reportFrom) eu += `&date_from=${reportFrom}T00:00:00Z`;
      if (reportTo) eu += `&date_to=${reportTo}T23:59:59Z`;
      const er = await apiFetch(eu);
      const ed = await er.json();
      if (ed.status === "ok") {
        const mins = ed.eta_sec > 60 ? `~${Math.ceil(ed.eta_sec/60)} мин` : `~${ed.eta_sec || 5} сек`;
        const txt = ed.new_calls > 0
          ? `Звонков за период: ${ed.total} (отвечено ${ed.answered})\n\nБудет разобрано новых: ${ed.new_calls} (${ed.new_minutes} мин)\nУже разобрано ранее: ${ed.already_done} — бесплатно\n\nСтоимость: ~${ed.cost_rub} ₽\nВремя: ${mins}\n\nЗапустить?`
          : `Звонков за период: ${ed.total} (отвечено ${ed.answered})\n\nВсе ${ed.already_done} звонков уже разобраны — новых списаний почти нет (~${ed.cost_rub} ₽).\n\nСформировать отчёт?`;
        if (!confirm(txt)) return;
      }
    } catch (e) {}
    const maskCalls = confirm("Скрыть номера телефонов в отчёте?\n\nОК — номера будут замазаны (79*****4777), удобно показывать отчёт как пример.\nОтмена — оставить номера как есть.");
    setReportLoading(true);
    let u = `/api/calltracking/report?account_id=${acc}&mask_phones=${maskCalls}`;
    if (userRole === "owner") u += `&owner=true`;
    if (reportFrom) u += `&date_from=${reportFrom}T00:00:00Z`;
    if (reportTo) u += `&date_to=${reportTo}T23:59:59Z`;
    u += `&max_calls=${reportMaxCalls}`;
    apiFetch(u).then(async r=>{
      if (!r.ok) {
        let m = "Не удалось сформировать отчёт";
        try { const d = await r.json(); m = d.message || d.detail || m; } catch(e) {}
        setReportLoading(false);
        if (!(window as any).__ropAlertLock) {
          (window as any).__ropAlertLock = true;
          alert(m);
          setTimeout(()=>{ (window as any).__ropAlertLock = false; }, 3000);
        }
        return null;
      }
      return r.blob();
    }).then(b=>{
      if (!b) return;
      const url = URL.createObjectURL(b);
      const a = document.createElement("a");
      a.href = url; a.download = "otchet_zvonki.pdf"; a.click();
      URL.revokeObjectURL(url); setReportLoading(false);
    }).catch(()=>setReportLoading(false));
  };
  const loadRopCalls = () => {
    const acc = currentAccount || (accounts && accounts.length > 0 ? accounts[0].id : "") || (typeof window !== "undefined" ? localStorage.getItem("boris_currentAccount") : "") || "";
    if (!acc) return;
    setRopCallsLoading(true);
    apiFetch(`/api/calltracking/stats?account_id=${acc}&days=30`)
      .then(r=>r.json()).then(d=>{ setRopCalls(d); setRopCallsLoading(false); }).catch(()=>setRopCallsLoading(false));
  };
  const analyzeRopCall = (callId:number) => {
    setRopDetailFor(callId); setRopDetail({loading:true});
    apiFetch(`/api/calltracking/analyze_call?account_id=${currentAccount || (accounts&&accounts[0]?accounts[0].id:"")}`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({call_id:callId})})
      .then(r=>r.json()).then(d=>setRopDetail(d)).catch(()=>setRopDetail({status:"error"}));
  };
  const [dlgAnalysis, setDlgAnalysis] = useState<any>(null);
  const [dlgAnalysisLoading, setDlgAnalysisLoading] = useState(false);
  const [dlgAnalysisOpen, setDlgAnalysisOpen] = useState(false);
  const [scriptGen, setScriptGen] = useState<any>(null);
  const [scriptGenLoading, setScriptGenLoading] = useState(false);
  const [scriptManualOpen, setScriptManualOpen] = useState(false);
  const [scriptManualText, setScriptManualText] = useState("");
  const [pcField, setPcField] = useState("");
  const [pcLoading, setPcLoading] = useState(false);
  const [pcData, setPcData] = useState<any>(null);
  const pcCheck = (field: string, kind: string, text: string) => {
    if (!text || !text.trim()) { alert("Сначала напишите текст, который нужно проверить"); return; }
    setPcField(field); setPcData(null); setPcLoading(true);
    apiFetch("/api/prompt/check", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({text, kind, account_id: currentAccount || ""})})
      .then(r=>r.json()).then(d=>{ setPcData(d); setPcLoading(false); })
      .catch(()=>{ setPcLoading(false); alert("Не удалось проверить промт"); });
  };
  const [scriptSaved, setScriptSaved] = useState(false);
  const makeScriptAuto = () => {
    setScriptGenLoading(true); setScriptSaved(false);
    apiFetch(`/api/messenger/analysis/make_script?account_id=${currentAccount}`, {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"})
      .then(r=>r.json()).then(d=>{ setScriptGenLoading(false); if(d.status==="ok"){ setScriptGen(d.script); setScriptSaved(true); } }).catch(()=>setScriptGenLoading(false));
  };
  const saveManualScript = () => {
    if(!scriptManualText.trim()) return;
    apiFetch(`/api/messenger/analysis/make_script?account_id=${currentAccount}`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({text:scriptManualText})})
      .then(r=>r.json()).then(d=>{ if(d.status==="ok"){ setScriptSaved(true); setScriptManualOpen(false); } }).catch(()=>{});
  };
  const runDlgAnalysis = () => {
    setDlgAnalysisLoading(true); setDlgAnalysisOpen(true);
    apiFetch(`/api/messenger/analysis/run?account_id=${currentAccount}`, {method:"POST"})
      .then(r=>r.json()).then(d=>{ setDlgAnalysis(d); setDlgAnalysisLoading(false); }).catch(()=>setDlgAnalysisLoading(false));
  };
  const [reminderSettings, setReminderSettings] = useState<any>({enabled:false, stages:[], delay_days:2});
  const [reminderSaved, setReminderSaved] = useState(false);
  const saveReminderSettings = (next:any) => {
    setReminderSettings(next); setReminderSaved(false);
    apiFetch(`/api/messenger/reminders/settings?account_id=${currentAccount}`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(next)
    }).then(r=>r.json()).then(d=>{ if(d.status==="ok") setReminderSaved(true); }).catch(()=>{});
  };
  const toggleReminderStage = (stage:string) => {
    const has = (reminderSettings.stages||[]).includes(stage);
    const stages = has ? reminderSettings.stages.filter((x:string)=>x!==stage) : [...(reminderSettings.stages||[]), stage];
    saveReminderSettings({...reminderSettings, stages});
  };
  const [salesLeadOpen, setSalesLeadOpen] = useState<string|null>(null);
  const [salesLeadList, setSalesLeadList] = useState<any[]>([]);
  const [salesLeadListLoading, setSalesLeadListLoading] = useState(false);
  const openLeadStage = (stageKey: string) => {
    if (salesLeadOpen === stageKey) { setSalesLeadOpen(null); return; }
    setSalesLeadOpen(stageKey); setSalesLeadListLoading(true); setSalesLeadList([]);
    apiFetch(`/api/messenger/leads/by_stage?account_id=${currentAccount}&stage=${encodeURIComponent(stageKey)}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setSalesLeadList(d.leads || []); setSalesLeadListLoading(false); })
      .catch(() => setSalesLeadListLoading(false));
  };

  const CITY_NAMES: Record<string, string> = {
    moskva: "Москва", sankt_peterburg: "Санкт-Петербург", "sankt-peterburg": "Санкт-Петербург",
    novosibirsk: "Новосибирск", ekaterinburg: "Екатеринбург", kazan: "Казань",
    nizhniy_novgorod: "Нижний Новгород", chelyabinsk: "Челябинск", omsk: "Омск",
    samara: "Самара", "rostov-na-donu": "Ростов-на-Дону", ufa: "Уфа",
    krasnoyarsk: "Красноярск", voronezh: "Воронеж", perm: "Пермь",
    volgograd: "Волгоград", krasnodar: "Краснодар", saratov: "Саратов",
    tyumen: "Тюмень", izhevsk: "Ижевск", habarovsk: "Хабаровск",
    simferopol: "Симферополь", sevastopol: "Севастополь", tolyatti: "Тольятти",
    barnaul: "Барнаул", irkutsk: "Иркутск", vladivostok: "Владивосток",
    yaroslavl: "Ярославль", penza: "Пенза", stavropol: "Ставрополь",
    kaluga: "Калуга", tver: "Тверь", sochi: "Сочи", tula: "Тула",
    ryazan: "Рязань", kemerovo: "Кемерово", tomsk: "Томск", orenburg: "Оренбург",
  };
  const cityFromUrl = (u: string) => {
    const m = String(u || "").match(/avito\.ru\/([a-z0-9_-]+)\//);
    if (!m) return "";
    const slug = m[1];
    if (slug === "rossiya" || slug === "all") return "";
    return CITY_NAMES[slug] || slug.replace(/[_-]/g, " ");
  };

  const loadSalesItems = () => {
    const acc = currentAccount || (accounts && accounts.length > 0 ? accounts[0].id : "") || (typeof window !== "undefined" ? localStorage.getItem("boris_currentAccount") : "") || "";
    if (!acc) return;
    setSalesLoading(true);
    apiFetch(`/api/messenger/own_items?account_id=${acc}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setSalesItems(d.items || []); setSalesLoading(false); })
      .catch(() => setSalesLoading(false));
    apiFetch(`/api/messenger/whitelist/get?account_id=${acc}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setSalesWhitelist(d.item_ids || []); })
      .catch(() => {});
    apiFetch(`/api/messenger/usage?account_id=${acc}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setSalesUsage(d.usage); })
      .catch(() => {});
    apiFetch(`/api/messenger/leads/stats?account_id=${acc}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setSalesLeadStats(d); })
      .catch(() => {});
    apiFetch(`/api/messenger/manager/balance?account_id=${acc}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setManagerBalance(d.balance); })
      .catch(() => {});
    apiFetch(`/api/messenger/analysis/get?account_id=${acc}`)
      .then(r => r.json())
      .then(d => { if (d && d.summary) setDlgAnalysis(d); })
      .catch(() => {});
    apiFetch(`/api/messenger/reminders/settings?account_id=${acc}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setReminderSettings({enabled:d.enabled, stages:d.stages||[], delay_days:d.delay_days||2}); })
      .catch(() => {});
  };

  const toggleSalesWhitelist = (itemId: string) => {
    const next = salesWhitelist.includes(itemId)
      ? salesWhitelist.filter(x => x !== itemId)
      : [...salesWhitelist, itemId];
    setSalesWhitelist(next);
    apiFetch(`/api/messenger/whitelist/set`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: currentAccount, item_ids: next }),
    }).catch(() => {});
  };
  const [weeklySummary, setWeeklySummary] = useState<any>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);

  const loadWeeklySummary = () => {
    setSummaryLoading(true);
    apiFetch(`/api/avito/weekly_summary?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => { setWeeklySummary(d); setSummaryLoading(false); })
      .catch(() => setSummaryLoading(false));
  };

  useEffect(() => {
    loadWeeklySummary();
  }, [currentAccount]);

  useEffect(() => {
    if (activeTab === "sales") loadSalesItems();
    if (activeTab === "sales" && salesView === "rop" && !ropCalls) loadRopCalls();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, currentAccount, salesView]);
  useEffect(() => {
    if (activeTab === "parser") loadParsedProducts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, currentAccount]);
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [editItemForm, setEditItemForm] = useState<{title: string, description: string, price: number}>({title: "", description: "", price: 0});
  const [editItemSaving, setEditItemSaving] = useState(false);

  const [billingData, setBillingData] = useState<any>(null);
  const [siteOrderOpen, setSiteOrderOpen] = useState<boolean>(false);
  const [sbUploadName, setSbUploadName] = useState<string>("beton_zavod.png");
  const [sbUploading, setSbUploading] = useState<boolean>(false);
  const [sbUploadMsg, setSbUploadMsg] = useState<string>("");
  const [sbLightbox, setSbLightbox] = useState<string | null>(null);
  const [sbZoom, setSbZoom] = useState<number>(1);
  const [sbVideos, setSbVideos] = useState<any[]>([]);
  const [sbVideoTitle, setSbVideoTitle] = useState<string>("");
  const [sbVideoUploading, setSbVideoUploading] = useState<boolean>(false);
  const [sbVideoMsg, setSbVideoMsg] = useState<string>("");
  const loadSbVideos = async () => {
    try {
      const r = await apiFetch("/api/sitebuild/videos");
      const d = await r.json();
      if (d.status === "ok") setSbVideos(d.videos || []);
    } catch (e) {}
  };
  const sbUploadVideo = async (fileList: FileList | null) => {
    if (!fileList || !fileList[0]) return;
    setSbVideoUploading(true); setSbVideoMsg("");
    const fd = new FormData();
    fd.append("title", sbVideoTitle);
    fd.append("file", fileList[0]);
    try {
      const r = await apiFetch("/api/sitebuild/upload_video", { method: "POST", body: fd });
      const d = await r.json();
      if (d.status === "ok") { setSbVideoMsg("✅ Видео загружено"); setSbVideoTitle(""); loadSbVideos(); }
      else setSbVideoMsg("❌ " + (d.message || "ошибка"));
    } catch (e) { setSbVideoMsg("❌ ошибка загрузки"); }
    setSbVideoUploading(false);
  };
  const sbDeleteVideo = async (filename: string) => {
    const fd = new FormData();
    fd.append("filename", filename);
    try { await apiFetch("/api/sitebuild/delete_video", { method: "POST", body: fd }); loadSbVideos(); } catch (e) {}
  };
  const sbRenameVideo = async (filename: string, currentTitle: string) => {
    const nt = window.prompt("Новое название видео:", currentTitle);
    if (!nt || !nt.trim()) return;
    const fd = new FormData();
    fd.append("filename", filename);
    fd.append("title", nt.trim());
    try { await apiFetch("/api/sitebuild/rename_video", { method: "POST", body: fd }); loadSbVideos(); } catch (e) {}
  };
  const [siteOrderName, setSiteOrderName] = useState<string>("");
  const [siteOrderPhone, setSiteOrderPhone] = useState<string>("");
  const [siteOrderNiche, setSiteOrderNiche] = useState<string>("");
  const [siteOrderComment, setSiteOrderComment] = useState<string>("");
  const [siteOrderSending, setSiteOrderSending] = useState<boolean>(false);
  const [siteOrderDone, setSiteOrderDone] = useState<boolean>(false);
  const submitSiteOrder = async () => {
    if (!siteOrderPhone.trim()) { notify("Укажите телефон для связи"); return; }
    setSiteOrderSending(true);
    try {
      const fd = new FormData();
      fd.append("name", siteOrderName);
      fd.append("phone", siteOrderPhone);
      fd.append("niche", siteOrderNiche);
      fd.append("comment", siteOrderComment);
      fd.append("account_id", currentAccount);
      const r = await apiFetch("/api/sitebuild/order", { method: "POST", body: fd });
      const d = await r.json();
      if (d.status === "ok") { setSiteOrderDone(true); }
      else notify("Не удалось отправить: " + (d.message || "ошибка"));
    } catch (e) { notify("Ошибка отправки заявки"); }
    setSiteOrderSending(false);
  };
  const sbUploadScreen = async (fileList: FileList | null) => {
    if (!fileList || !fileList[0]) return;
    setSbUploading(true); setSbUploadMsg("");
    const fd = new FormData();
    fd.append("filename", sbUploadName);
    fd.append("file", fileList[0]);
    try {
      const r = await apiFetch("/api/sitebuild/upload_screen", { method: "POST", body: fd });
      const d = await r.json();
      if (d.status === "ok") setSbUploadMsg("✅ Загружено: " + d.filename + " — обновите страницу (F5)");
      else setSbUploadMsg("❌ " + (d.message || "ошибка"));
    } catch (e) { setSbUploadMsg("❌ ошибка загрузки"); }
    setSbUploading(false);
  };
  const loadBilling = () => {
    if (!currentAccount) return;
    apiFetch(`/api/billing/status?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setBillingData(d.billing); })
      .catch(() => {});
  };

  const loadMyBorisItems = () => {
    // ИСПОЛЬЗУЕМ РЕАЛЬНЫЙ AVITO API (items2), а не локальное хранилище my_boris_items -
    // локальное хранилище исторически теряло записи о старых объявлениях (баг до перехода
    // на PostgreSQL), из-за чего вкладка "Активные" могла показывать 0 при реально живых
    // объявлениях на самом Avito. items2 всегда отражает актуальное состояние на площадке.
    apiFetch(`/api/avito/items2?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => {
        const resources = d.resources || [];
        const mapped = resources.map((r: any) => ({
          id: String(r.id),
          title: r.title || "",
          description: "",
          price: r.price || 0,
          address: r.address || "",
          url: r.url || "",
          status: r.status || "active",
          views: r.views || 0,
          contacts: r.contacts || 0,
          favorites: r.favorites || 0,
          conversion: r.conversion || 0,
        }));
        setMyBorisItems(mapped);
      })
      .catch(() => {});
  };

  useEffect(() => {
    loadMyBorisItems();
  }, [currentAccount]);

  useEffect(() => {
    // ВАЖНО: раньше items (реальные объявления с Avito) загружались ТОЛЬКО через
    // openAccount() (полный экран списка аккаунтов) или один раз при первом заходе.
    // Переключение аккаунта через компактный <select className="b-select"> в сайдбаре НЕ обновляло items -
    // вкладка "Активные" могла показывать 0 при реально живых объявлениях на Avito.
    if (!currentAccount) return;
    apiFetch(`/api/avito/items2?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(data => { setItems(data.resources || []); })
      .catch(() => {});
  }, [currentAccount]);

  const startEditItem = (item: any) => {
    setEditingItemId(item.id);
    setEditItemForm({title: item.title, description: item.description, price: item.price});
  };

  const saveEditItem = async () => {
    if (!editingItemId) return;
    setEditItemSaving(true);
    try {
      const res = await apiFetch("/api/avito/edit_item", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: currentAccount,
          item_id: editingItemId,
          title: editItemForm.title,
          description: editItemForm.description,
          price: editItemForm.price
        })
      });
      const data = await res.json();
      if (data.status === "ok") {
        setEditingItemId(null);
        loadMyBorisItems();
      } else {
        alert(data.message || "Не удалось сохранить");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setEditItemSaving(false);
  };
  const [allAccounts, setAllAccounts] = useState<{account_id: string, name: string}[]>([]);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
    apiFetch("/api/accounts/list", { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setAllAccounts(d.accounts); })
      .catch(() => {});
  }, []);

  const generateImage = async () => {
    if (!imgPrompt.trim()) { alert("Опишите, что нарисовать"); return; }
    setImgLoading(true);
    setImgQueueStatus("Ставлю задачу в очередь...");
    try {
      const createRes = await apiFetch("/api/tasks/create", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, task_type: "generate_images", payload: { prompt: imgPrompt, style: imgStyle, count: imgCount, folder: imgFolder } })
      });
      const createData = await createRes.json();
      if (createData.status !== "ok") { alert("Не удалось поставить задачу в очередь"); setImgLoading(false); return; }
      const taskId = createData.task_id;
      setImgQueueStatus(`Задача №${taskId} в очереди. Можно закрыть вкладку — результат сохранится, проверьте позже в галерее.`);

      const poll = async () => {
        const statusRes = await apiFetch(`/api/tasks/status/${taskId}`);
        const statusData = await statusRes.json();
        if (statusData.status === "done") {
          const images = statusData.result?.images || [];
          setGenImages(prev => [...images.map((u: string) => ({url: u, prompt: imgPrompt})), ...prev]);
          setImgQueueStatus(`✅ Готово! Сгенерировано ${statusData.result?.succeeded || 0} из ${statusData.result?.requested || 0}`);
          setImgLoading(false);
          loadAllFolders();
        } else if (statusData.status === "error") {
          setImgQueueStatus("");
          alert("Ошибка генерации: " + (statusData.error_message || ""));
          setImgLoading(false);
        } else {
          setImgQueueStatus(`Статус: ${statusData.status === "running" ? "выполняется..." : "в очереди..."}`);
          setTimeout(poll, 4000);
        }
      };
      setTimeout(poll, 4000);
    } catch {
      alert("Ошибка связи с сервером");
      setImgLoading(false);
    }
  };

  const [stockQuery, setStockQuery] = useState("");
  const [stockCount, setStockCount] = useState(50);
  const [stockSource, setStockSource] = useState("pexels");
  const [stockLoading, setStockLoading] = useState(false);

  const searchStockPhotos = async () => {
    if (!stockQuery.trim()) { alert("Впиши тему, например: бетон товарный и растворы"); return; }
    setStockLoading(true);
    try {
      const res = await apiFetch("/api/avito/search_stock_photos", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          query: stockQuery, count: stockCount, account_id: currentAccount,
          folder: imgFolder || stockQuery, source: stockSource
        })
      });
      const data = await res.json();
      if (data.status === "ok") {
        alert(`Готово! Скачано ${data.downloaded} фото (${data.source}) в папку "${data.folder}"`);
        setStockQuery("");
      } else {
        alert("Не удалось: " + (data.message || ""));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setStockLoading(false);
  };

  const downloadByUrl = async () => {
    if (!dlUrl.trim()) { alert("Вставьте прямую ссылку на картинку (JPG/PNG)"); return; }
    setImgLoading(true);
    try {
      const res = await apiFetch("/api/avito/download_image", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url: dlUrl, folder: imgFolder, account_id: currentAccount })
      });
      const data = await res.json();
      if (data.status === "ok") {
        setGenImages(prev => [{url: data.image_url, prompt: "скачано по ссылке"}, ...prev]);
        setDlUrl("");
      } else {
        alert("Не скачалось: " + (data.message || ""));
      }
    } catch {
      alert("Ошибка связи");
    }
    setImgLoading(false);
  };

  const deleteImage = async (url: string) => {
    try {
      await apiFetch("/api/avito/delete_image", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url })
      });
      setGenImages(prev => prev.filter(im => im.url !== url));
    } catch {
      alert("Ошибка удаления");
    }
  };

  const [feedImages, setFeedImages] = useState<{[key:string]: boolean}>({});

  useEffect(() => {
    if (!prefsLoaded) return;
    const t = setTimeout(() => {
      apiFetch("/api/storage/save", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, key: "feed_prefs",
          value: { feedImages, photosPerAd, city: genAddress } })
      }).catch(() => {});
    }, 800);
    return () => clearTimeout(t);
  }, [feedImages, photosPerAd, genAddress, prefsLoaded]);
  const [allImages, setAllImages] = useState<string[]>([]);

  const loadImagesForFeed = async () => {
    try {
      const urls: string[] = [];
      // Сначала баннеры (пойдут первыми в списке - их удобно ставить первым фото объявления)
      try {
        const bres = await apiFetch(`/api/banners/list?account_id=${currentAccount}`);
        const bdata = await bres.json();
        if (bdata.status === "ok" && bdata.banners) {
          Object.values(bdata.banners as {[k:string]: string[]}).forEach(arr => urls.push(...arr));
        }
      } catch {}
      // Затем обычные фото из хранилища
      const res = await apiFetch(`/api/avito/images_list?account_id=${currentAccount}`);
      const data = await res.json();
      if (data.status === "ok") {
        Object.values(data.folders as {[k:string]: string[]}).forEach(arr => urls.push(...arr));
      }
      setAllImages(urls);
    } catch {}
  };

  const toggleFeedImage = (url: string) => {
    setFeedImages(prev => ({...prev, [url]: !prev[url]}));
  };

  const updateAd = (i: number, field: string, value: any) => {
    setGenAds(prev => prev.map((a, idx) => idx === i ? {...a, [field]: value} : a));
  };

  const deleteAd = (i: number) => {
    if (!confirm("Удалить это объявление из списка сгенерированных?")) return;
    setGenAds(prev => prev.filter((_, idx) => idx !== i));
    setAdPhotos(prev => {
      const next: {[key: number]: string[]} = {};
      Object.keys(prev).map(Number).sort((a, b) => a - b).forEach(key => {
        if (key < i) next[key] = prev[key];
        else if (key > i) next[key - 1] = prev[key];
        // key === i пропускаем — это удаляемый элемент
      });
      return next;
    });
    if (editingIndex === i) setEditingIndex(null);
    if (photoPickerIndex === i) setPhotoPickerIndex(null);
  };

  const applyBold = (i: number) => {
    const ta = document.getElementById("desc-edit-" + i) as HTMLTextAreaElement;
    if (!ta) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    if (start === end) { alert("Выделите текст, который нужно сделать жирным"); return; }
    const val = ta.value;
    const newVal = val.slice(0, start) + "<strong>" + val.slice(start, end) + "</strong>" + val.slice(end);
    updateAd(i, "description", newVal);
  };

  const [batchDetecting, setBatchDetecting] = useState(false);

  const addAdsToFeed = async () => {
    if (genAds.length === 0) { alert("Сначала сгенерируйте объявления"); return; }
    if (publishMode === "schedule" && !publishDate) { alert("Укажите дату публикации"); return; }

    let batchCategory = detectedCategory;
    if (genTopic.trim()) {
      setBatchDetecting(true);
      try {
        const res = await apiFetch("/api/avito/detect_category", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ account_id: currentAccount, niche: genTopic })
        });
        const data = await res.json();
        if (data.status === "ok" && data.category) batchCategory = data.category;
      } catch {}
      setBatchDetecting(false);
    }

    const items = genAds.map((ad, i) => ({
      id: "boris-gen-" + Date.now() + "-" + i,
      title: ad.title,
      description: ad.description,
      price: ad.price || 0,
      category: batchCategory || mainCat,
      service_type: (!batchCategory && mainCat === "Услуги") ? subCat : "",
      address: (bulkCities.length > 0 || bulkMetro.length > 0) ? getRandomAddressForAd() : (genAddress || "Москва"),
      images: getAdPhotos(i),
      date_begin: (publishMode === "schedule" && publishDate) ? `${publishDate}T${publishTime}:00` : "",
      source_batch_id: lastBatchId,
      params: {}
    }));
    try {
      const res = await apiFetch("/api/avito/generate_feed", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, items })
      });
      const data = await res.json();
      if (data.status === "ok") {
        setFeedUrl(data.feed_url);
      } else {
        alert("Не получилось собрать фид");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const checkBatchEffectiveness = async (batchId: number) => {
    try {
      const res = await apiFetch(`/api/avito/batch_effectiveness?account_id=${currentAccount}&batch_id=${batchId}&days=30`);
      const data = await res.json();
      setBatchEffectiveness(prev => ({...prev, [batchId]: data}));
    } catch {
      setBatchEffectiveness(prev => ({...prev, [batchId]: {status: "error"}}));
    }
  };

  const generateAds = async () => {
    if (!genTopic.trim()) { alert("Укажите тему объявлений"); return; }
    setGenLoading(true);
    setGenAds([]);
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 180000);
      const res = await apiFetch("/api/avito/generate_ads", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        signal: controller.signal,
        body: JSON.stringify({
          topic: genTopic,
          count: genCount,
          price_from: genPriceFrom,
          price_to: genPriceTo,
          extra: genExtra,
          sample: genSample,
          goal: genGoal,
          length: genLength,
          use_my_ads: useMyAds,
          account_id: currentAccount
        })
      });
      clearTimeout(timeoutId);
      const data = await res.json();
      if (data.status === "ok") {
        setGenAds(data.ads);
        try {
          const histRes = await apiFetch(`/api/storage/load?account_id=${currentAccount}&key=gen_ads_history`);
          const histData = await histRes.json();
          const history = (histData.value && Array.isArray(histData.value)) ? histData.value : [];
          const _batchId = Date.now();
          setLastBatchId(_batchId);
          const newBatch = {
            id: _batchId,
            topic: genTopic,
            created_at: new Date().toISOString(),
            ads: data.ads
          };
          const updatedHistory = [newBatch, ...history].slice(0, 50);
          await apiFetch("/api/storage/save", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ account_id: currentAccount, key: "gen_ads_history", value: updatedHistory })
          });
        } catch {}
      } else {
        alert("Не получилось сгенерировать: " + (data.message || "попробуйте ещё раз"));
      }
    } catch (e: any) {
      if (e.name === "AbortError") {
        alert("Борис думает слишком долго (более 3 минут). Попробуйте уменьшить количество объявлений.");
      } else {
        alert("Ошибка связи с сервером: " + (e?.message || String(e)));
      }
    }
    setGenLoading(false);
  };

  const loadNotifications = async () => {
    try {
      const d = await apiFetch(`/api/chat/notifications?account_id=${currentAccount}&unread_only=true`).then(r => r.json());
      setNotifications(d.notifications || []);
    } catch (e) {}
  };

  const markNotificationRead = async (ts: string) => {
    try {
      await apiFetch("/api/chat/notifications/mark_read", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, ts })
      });
      setNotifications(prev => prev.filter(n => n.ts !== ts));
    } catch (e) {}
  };

  useEffect(() => {
    loadNotifications();
    const interval = setInterval(loadNotifications, 60000);
    return () => clearInterval(interval);
  }, [currentAccount]);

  useEffect(() => { if (typeof window !== "undefined") localStorage.setItem("boris_view", view); }, [view]);
  useEffect(() => { if (typeof window !== "undefined") localStorage.setItem("boris_activeTab", activeTab); }, [activeTab]);
  useEffect(() => { if (typeof window !== "undefined") localStorage.setItem("boris_currentAccount", currentAccount); }, [currentAccount]);

  const kbOpenModal = (accId: string) => { setKbModalAccount(accId); setKbText(""); setKbFiles([]); setKbModalOpen(true); };

  const kbUploadFile = async (file: File) => {
    const id = Date.now() + "_" + file.name;
    setKbFiles((prev: any[]) => [...prev, {id, name: file.name, status: "processing"}]);
    try {
      const fd = new FormData(); fd.append("file", file);
      const r = await apiFetch(`/api/chat/knowledge/upload?account_id=${encodeURIComponent(kbModalAccount)}`, {method:"POST", body: fd});
      const d = await r.json();
      setKbFiles((prev: any[]) => prev.map(f => f.id===id ? {...f, status: d.status==="ok"?"done":"error", chars: d.extracted_chars} : f));
    } catch { setKbFiles((prev: any[]) => prev.map(f => f.id===id ? {...f, status:"error"} : f)); }
  };

  const kbSubmit = async () => {
    const okFiles = kbFiles.filter((f: any) => f.status==="done").length;
    if (!kbText.trim() && okFiles===0) { setKbModalOpen(false); return; }
    setKbSaving(true);
    try {
      if (kbText.trim()) {
        await apiFetch("/api/chat/knowledge/add", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({text: kbText, account_id: kbModalAccount})});
      }
      setKbModalOpen(false); setKbText(""); setKbFiles([]);
    } finally { setKbSaving(false); }
  };

  const askBoris = async () => {
    if (!borisInput.trim()) return;
    const userMsg = borisInput.trim();
    const newHistory = [...borisHistory, {role:"user", content:userMsg}];
    setBorisHistory(newHistory);
    setBorisInput("");
    setBorisLoading(true);
    try {
      // Сначала пробуем разобрать как комплексную задачу
      const parseRes = await apiFetch("/api/chat/parse_task", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({message: userMsg})
      }).then(r => r.json());

      if (parseRes.status === "ok" && parseRes.plan?.understood && parseRes.plan?.steps?.length > 0) {
        setBorisHistory([...newHistory, {role:"assistant", content: parseRes.plan.summary, plan: parseRes.plan}]);
      } else {
        // Не задача — обычный вопрос, отвечаем как раньше
        const res = await apiFetch("/api/chat", {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({message: userMsg, history: borisHistory.slice(-10)})
        });
        const data = await res.json();
        setBorisHistory([...newHistory, {role:"assistant", content: data.answer || "Не смог ответить, попробуй ещё раз"}]);
      }
    } catch {
      setBorisHistory([...newHistory, {role:"assistant", content:"Ошибка связи. Проверь интернет и попробуй снова."}]);
    }
    setBorisLoading(false);
  };

  // Онбординг-гид: подсветка вкладок по шагам плана (память проекта #11)
  const [guideActive, setGuideActive] = useState(false);
  const [guideSteps, setGuideSteps] = useState<any[]>([]);
  const [guideIndex, setGuideIndex] = useState(0);

  const STEP_TAB_MAP: {[key: string]: {tab: string, instruction: string}} = {
    generate_banners: {tab: "webdesign", instruction: "Зайди во вкладку «Баннеры и картинки» → «Купить баннеры», опиши нишу и нажми генерацию."},
    publish_listings: {tab: "templates", instruction: "Зайди во вкладку «Шаблоны», выбери или создай шаблон объявления, укажи города и нажми «Опубликовать»."},
    ab_test: {tab: "listings", instruction: "Зайди во вкладку «Объявления», выбери варианты для А/Б-теста."},
    report_back: {tab: "plan", instruction: "Результат появится во вкладке «Задачи и план» — я сам напишу туда, когда будет готово."},
    analyze_competitors: {tab: "analysis", instruction: "Зайди во вкладку «Аналитика по городам», укажи запрос и города для анализа конкурентов."},
    collect_stats: {tab: "stats", instruction: "Зайди во вкладку «Статистика» — там уже собираются данные по объявлениям."},
  };

  const startGuide = (steps: any[]) => {
    const mapped = steps.map((s: any) => ({
      ...s,
      tab: (STEP_TAB_MAP[s.type] || {}).tab || "stats",
      instruction: (STEP_TAB_MAP[s.type] || {}).instruction || "Далее по плану.",
    }));
    setGuideSteps(mapped);
    setGuideIndex(0);
    setGuideActive(true);
    if (mapped.length > 0) setActiveTab(mapped[0].tab);
  };

  const guideNext = () => {
    setGuideIndex(prev => {
      const next = prev + 1;
      if (next >= guideSteps.length) {
        setGuideActive(false);
        return prev;
      }
      setActiveTab(guideSteps[next].tab);
      return next;
    });
  };

  const guideSkip = () => {
    setGuideActive(false);
  };

  const stepLabel = (step: any) => {
    if (step.type === "generate_banners") return `🎨 Сделаю ${step.count || "несколько"} баннера по теме «${step.topic || "?"}»`;
    if (step.type === "publish_listings") return `📤 Опубликую ${step.count || "?"} объявлений в городах: ${(step.cities||[]).join(", ")} (${step.templates_count || "?"} шаблон(ов))`;
    if (step.type === "ab_test") return `🧪 Проведу А/Б-тест ${step.duration_days || "?"} дн.`;
    if (step.type === "report_back") return `📊 Вернусь с результатом через ${step.after_days || step.duration_days || "несколько"} дн.`;
    if (step.type === "analyze_competitors") return `🔍 Проанализирую конкурентов`;
    if (step.type === "collect_stats") return `📈 Соберу статистику`;
    return `• ${step.type}`;
  };

  const acceptPlan = (plan: any, msgIndex: number) => {
    // TODO: следующий кирпич — превратить plan.steps в реальные фоновые задачи
    setBorisHistory(prev => {
      const updated = prev.map((m, idx) => idx === msgIndex ? {...m, planResolved: true} : m);
      return [...updated, {role:"assistant", content: "Принято! Пока я в режиме гида — подсвечу нужные вкладки и подскажу что делать на каждом шаге, а сам шаг выполняешь ты. Полностью автоматическое исполнение подключу отдельно."}];
    });
    if (plan && plan.steps && plan.steps.length > 0) {
      startGuide(plan.steps);
    }
  };

  const rejectPlan = (msgIndex: number) => {
    setBorisHistory(prev => {
      const updated = prev.map((m, idx) => idx === msgIndex ? {...m, planResolved: true} : m);
      return [...updated, {role:"assistant", content: "Хорошо, напиши как поправить план, и я пересоберу его."}];
    });
  };
  const router = useRouter();

  const applyTemplate = (item: any, template: any) => {
    const city = template.cities[Math.floor(Math.random() * template.cities.length)];
    const titleRaw = template.titleTemplate.replace("{название}", item.title || "").replace("{город}", city);
    const title = spinText(titleRaw);
    const description = spinText(template.description.replace("{описание}", item.description || ""));
    let price = item.price || "";
    if (template.priceType === "markup" && item.price) {
      const num = parseFloat(item.price.replace(/\D/g, ""));
      if (!isNaN(num)) price = Math.round(num * (1 + template.priceModifier / 100)) + " ₽";
    }
    if (template.priceType === "fixed") price = template.priceModifier + " ₽";
    return { ...item, title, description, price, city };
  };


  const publishStretch = async () => {
    if (!selectedTemplate) { alert("Выберите шаблон"); return; }
    if (stretchNames.length === 0) { alert("Сначала сгенерируйте названия"); return; }
    setPublishStatus("🔍 Борис определяет категорию для этой партии...");

    let batchCategory = detectedCategory;
    const nicheGuess = selectedTemplate.name || stretchNames[0] || "";
    if (nicheGuess) {
      try {
        const res = await apiFetch("/api/avito/detect_category", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ account_id: currentAccount, niche: nicheGuess })
        });
        const data = await res.json();
        if (data.status === "ok" && data.category) batchCategory = data.category;
      } catch {}
    }

    setPublishStatus("📤 Собираю фид...");
    const items = stretchNames.map((name, i) => {
      const fakeItem = { title: name, description: name, price: "" };
      const applied = applyTemplate(fakeItem, selectedTemplate);
      return {
        id: "boris-stretch-" + Date.now() + "-" + i,
        title: applied.title,
        description: applied.description,
        price: parseInt(String(applied.price).replace(/\D/g, "")) || 0,
        category: batchCategory || (selectedTemplate.category || mainCat || "").split(" / ")[0] || mainCat,
        service_type: "",
        address: applied.city || (selectedTemplate.cities[0] || "Москва"),
        images: getAdPhotos(i),
        params: {}
      };
    });
    try {
      const res = await apiFetch("/api/avito/generate_feed", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, items })
      });
      const data = await res.json();
      if (data.status === "ok") {
        setFeedUrl(data.feed_url);
        setPublishStatus(`✅ Готово! В фид добавлено ${items.length} объявлений из ${1} шаблона(ов)`);
      } else {
        setPublishStatus("");
        alert("Не получилось собрать фид");
      }
    } catch {
      setPublishStatus("");
      alert("Ошибка связи с сервером");
    }
  };

  useEffect(() => {
    const savedView = typeof window !== "undefined" && localStorage.getItem("boris_view");
    const acc = typeof window !== "undefined" && localStorage.getItem("boris_currentAccount");
    if (savedView === "dashboard" && acc) {
      setLoading(true);
      apiFetch(`/api/avito/items2?account_id=${acc}`)
        .then(r => r.json())
        .then(data => { setItems(data.resources || []); setLoading(false); })
        .catch(() => setLoading(false));
      apiFetch(`/api/avito/feed_items_list?account_id=${acc}`)
        .then(r => r.json())
        .then(d => { if (d.status === "ok") setBorisFeedItems(d.items || []); })
        .catch(() => {});
    }
  }, []);

  const [borisFeedItems, setBorisFeedItems] = useState<any[]>([]);
  const loadBorisFeedItems = (acc?: string) => {
    const accId = acc || currentAccount;
    if (!accId) return;
    apiFetch(`/api/avito/feed_items_list?account_id=${accId}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setBorisFeedItems(d.items || []); })
      .catch(() => {});
  };

  const [detectedCategory, setDetectedCategory] = useState<string | null>(null);

  // Перезагружаем объявления при КАЖДОЙ смене аккаунта (в т.ч. через выпадающий список в шапке).
  // Раньше select менял currentAccount, но объявления не перегружались - показывались чужие.
  useEffect(() => {
    if (!currentAccount) return;
    if (view !== "dashboard") return;
    setLoading(true);
    apiFetch(`/api/avito/items2?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(data => { setItems(data.resources || []); setLoading(false); })
      .catch(() => setLoading(false));
    loadBorisFeedItems(currentAccount);
  }, [currentAccount, view]);

  const openAccount = (account: any) => {
    setSelectedAccount(account);
    setCurrentAccount(account.id);
    localStorage.setItem("boris_currentAccount", account.id);
    setView("dashboard");
    setActiveTab("listings");
    setLoading(true);
    apiFetch(`/api/avito/items2?account_id=${account.id}`)
      .then(r => r.json())
      .then(data => { setItems(data.resources || []); setLoading(false); });
    apiFetch(`/api/avito/detected_category?account_id=${account.id}`)
      .then(r => r.json())
      .then(d => { setDetectedCategory(d.status === "ok" ? d.category : null); })
      .catch(() => setDetectedCategory(null));
    apiFetch(`/api/avito/images_list?account_id=${account.id}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok" && d.folders) setAllFolders(d.folders); })
      .catch(() => {});
    loadBorisFeedItems(account.id);
  };

  const [addingAccount, setAddingAccount] = useState(false);
  const [directorMode, setDirectorMode] = useState(false);
  const [qaAuto, setQaAuto] = useState(false);
  const [directorOverview, setDirectorOverview] = useState<any[]>([]);
  const [directorLoading, setDirectorLoading] = useState(false);
  const dsDelta = (v:any) => {
    if (v === null || v === undefined) return <span style={{color:"#98A2B3", fontSize:"13px"}}>—</span>;
    const up = v >= 0;
    return <span style={{color: up ? "#12B76A" : "#F04438", fontSize:"14px", fontWeight:"bold"}}>{up ? "▲" : "▼"} {Math.abs(v)}%</span>;
  };
  const [dsDays, setDsDays] = useState(30);
  const [dsFrom, setDsFrom] = useState(() => { const d=new Date(); d.setDate(d.getDate()-6); return d.toISOString().slice(0,10); });
  const [dsTo, setDsTo] = useState(() => new Date().toISOString().slice(0,10));
  const [dsData, setDsData] = useState<any>(null);
  const [dsLoading, setDsLoading] = useState(false);
  const loadDirectorStats = (d:number) => {
    setDsLoading(true); setDsDays(d);
    apiFetch(`/api/avito/director_stats?days=${d}`)
      .then(r=>r.json()).then(setDsData).catch(()=>{}).finally(()=>setDsLoading(false));
  };
  const loadDirectorStatsRange = (from:string, to:string) => {
    if (!from || !to) return;
    setDsLoading(true);
    apiFetch(`/api/avito/director_stats?date_from=${from}&date_to=${to}`)
      .then(r=>r.json()).then(d=>{ setDsData(d); if (d?.days_requested) setDsDays(d.days_requested); }).catch(()=>{}).finally(()=>setDsLoading(false));
  };
  // Оплата клиентов (только владелец)
  const [payModalAccount, setPayModalAccount] = useState<string | null>(null);
  const [payAmount, setPayAmount] = useState<string>("");
  const [payPeriod, setPayPeriod] = useState<string>("30");
  // Модалка автопилота
  const [autopilotModalOpen, setAutopilotModalOpen] = useState(false);
  const [notifyMsg, setNotifyMsg] = useState<string | null>(null);
  const notify = (m: string) => setNotifyMsg(m);

  const savePayment = async () => {
    if (!payModalAccount || !payAmount) return;
    await apiFetch("/api/payments/set", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({account_id: payModalAccount, amount_rub: parseFloat(payAmount), period_days: parseInt(payPeriod) || 30})
    });
    setPayModalAccount(null); setPayAmount("");
    loadDirectorOverview();
  };
  // === Советник по ставкам (CPX) ===
  const [advisorMode, setAdvisorMode] = useState(false);
  const [tariffsFinanceMode, setTariffsFinanceMode] = useState(false);
  const [requisitesMode, setRequisitesMode] = useState(false);
  const [walletMode, setWalletMode] = useState(false);
  const [balanceData, setBalanceData] = useState<any>({balance:0, history:[]});
  const [topupSum, setTopupSum] = useState("");
  const loadBalance = () => {
    apiFetch(`/api/wallet/balance`).then(r=>r.json()).then(d=>{ if(d.status==="ok") setBalanceData({balance:d.balance||0, history:d.history||[]}); }).catch(()=>{});
  };
  const [invoicesList, setInvoicesList] = useState<any[]>([]);
  const loadInvoices = () => {
    apiFetch(`/api/wallet/invoices`).then(r=>r.json()).then(d=>{ if(d.status==="ok") setInvoicesList(Array.isArray(d.invoices)?d.invoices:[]); }).catch(()=>{});
  };
  const markPaid = async (number: string, amount: number) => {
    const val = prompt(`Сколько пришло по счёту ${number}? (сумма счёта ${amount} ₽)`, String(amount));
    if (val===null) return;
    const paid = Number(val.replace(/[^0-9.]/g,""));
    if (!paid || paid<=0) { alert("Неверная сумма"); return; }
    try {
      const r = await apiFetch(`/api/wallet/invoice/mark_paid`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ number, amount: paid }) });
      const d = await r.json();
      if (d.status==="ok") { alert(`✓ Зачислено ${d.credited} ₽. Статус счёта: ${d.invoice_status}`); loadInvoices(); loadBalance(); }
      else alert(d.message||"Не удалось зачислить");
    } catch { alert("Ошибка зачисления"); }
  };
  const buyWithBalance = async (pack: string, title: string) => {
    if (!confirm(`Купить «${title}» с баланса кошелька?`)) return;
    try {
      const r = await apiFetch(`/api/wallet/purchase`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ account_id: currentAccount, pack }) });
      const d = await r.json();
      if (d.status === "ok") { alert(`✓ Куплено: ${d.purchased}. Списано ${d.charged} ₽, остаток ${d.balance_left} ₽`); loadBalance(); }
      else if (d.need) alert(`Недостаточно средств. Не хватает ${d.need} ₽ — пополните кошелёк.`);
      else alert(d.message || "Не удалось купить");
    } catch { alert("Ошибка покупки"); }
  };
  const [requisites, setRequisites] = useState<any>({});
  const [reqSaved, setReqSaved] = useState(false);
  const [invoiceOpen, setInvoiceOpen] = useState(false);
  const [invoicePack, setInvoicePack] = useState("");
  const [payerName, setPayerName] = useState("");
  const [payerInn, setPayerInn] = useState("");
  const [invoiceBusy, setInvoiceBusy] = useState(false);
  const openInvoice = (pack: string) => { setInvoicePack(pack); setInvoiceOpen(true); };
  const createInvoice = async () => {
    if (!payerName.trim()) { alert("Укажите название организации"); return; }
    setInvoiceBusy(true);
    try {
      const _topup = (window as any)._topup || 0;
      const _body: any = { account_id: currentAccount, payer_name: payerName, payer_inn: payerInn };
      if (_topup > 0) _body.topup_amount = _topup; else _body.pack = invoicePack;
      const r = await apiFetch(`/api/wallet/invoice/create`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(_body) });
      const d = await r.json();
      if (d.status !== "ok") { alert(d.message || "Не удалось создать счёт"); setInvoiceBusy(false); return; }
      const num = d.invoice.number;
      const pr = await apiFetch(`/api/wallet/invoice/pdf?number=${encodeURIComponent(num)}`);
      const blob = await pr.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = `Счёт ${num}.pdf`; a.click();
      URL.revokeObjectURL(url);
      setInvoiceOpen(false); setPayerName(""); setPayerInn(""); (window as any)._topup = 0; setTopupSum("");
    } catch { alert("Ошибка при создании счёта"); }
    finally { setInvoiceBusy(false); }
  };
  const loadRequisites = () => {
    apiFetch(`/api/wallet/requisites`).then(r=>r.json()).then(d=>{ if(d.status==="ok") setRequisites(d.requisites||{}); }).catch(()=>{});
  };
  const saveRequisites = async () => {
    try {
      const r = await apiFetch(`/api/wallet/requisites`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(requisites) });
      const d = await r.json();
      if (d.status==="ok") { setReqSaved(true); setTimeout(()=>setReqSaved(false), 2500); }
      else alert(d.message||"Не удалось сохранить");
    } catch { alert("Ошибка сохранения"); }
  };
  const [advisorData, setAdvisorData] = useState<any>(null);
  const [advisorLoading, setAdvisorLoading] = useState(false);
  const [advMaxCpl, setAdvMaxCpl] = useState<string>("");
  const [advDailyLimit, setAdvDailyLimit] = useState<string>("");

  const loadAdvisor = () => {
    setAdvisorLoading(true);
    // подтягиваем сохранённые настройки KPI в поля формы
    apiFetch(`/api/avito/kpi_settings?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => {
        const s = d.settings;
        if (s) {
          if (s.max_cost_per_lead_rub) setAdvMaxCpl(String(s.max_cost_per_lead_rub));
          if (s.daily_budget_limit_rub) setAdvDailyLimit(String(s.daily_budget_limit_rub));
          setAdvAutopilot(!!s.bid_autopilot);
        }
      })
      .catch(() => {});
    apiFetch(`/api/cpx_advisor/last?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => { setAdvisorData(d.advice || null); setAdvisorLoading(false); })
      .catch(() => setAdvisorLoading(false));
  };

  const runAdvisor = () => {
    setAdvisorLoading(true);
    apiFetch(`/api/cpx_advisor/run?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => { setAdvisorData(d.advice || null); setAdvisorLoading(false); })
      .catch(() => setAdvisorLoading(false));
  };

  const activateAdvisor = async () => {
    const cpl = parseFloat(advMaxCpl), lim = parseFloat(advDailyLimit);
    if (!cpl || !lim) { notify("Заполните обе цифры: макс цену лида и суточный лимит бюджета"); return; }
    setAdvisorLoading(true);
    await apiFetch("/api/avito/set_kpi_settings", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({account_id: currentAccount, target_leads_per_day: 1,
        max_cost_per_lead_rub: cpl, daily_budget_limit_rub: lim,
        bid_autopilot: advAutopilot, lead_temperature: "тёплый"})
    });
    runAdvisor();
  };

  useEffect(() => { if (advisorMode && currentAccount) loadAdvisor(); }, [advisorMode, currentAccount]);

  const [advAutopilot, setAdvAutopilot] = useState(false);

  const applyAdvisorOne = async (itemId: number, action: string) => {
    if (!confirm(action === "archive" ? "Снять продвижение с этого объявления?" : `Изменить ставку (${action === "raise" ? "поднять" : "снизить"})?`)) return;
    const r = await apiFetch("/api/cpx_advisor/apply_one", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({account_id: currentAccount, item_id: itemId, action})
    });
    const d = await r.json();
    if (d.status === "ok") {
      notify(d.action === "archive" ? "Продвижение снято" : `Ставка: ${d.old_bid_rub}₽ → ${d.new_bid_rub}₽`);
      runAdvisor();
    } else {
      notify("Ошибка: " + (d.message || d.code || "не удалось"));
    }
  };

  const toggleAutopilot = () => {
    // если включаем — сначала показываем красивую модалку-подтверждение
    if (!advAutopilot) { setAutopilotModalOpen(true); return; }
    doToggleAutopilot(); // выключение — без подтверждения
  };

  const doToggleAutopilot = async () => {
    const next = !advAutopilot;
    setAutopilotModalOpen(false);
    await apiFetch("/api/avito/set_kpi_settings", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({account_id: currentAccount,
        target_leads_per_day: advisorData?.target?.target_leads_per_day || 1,
        max_cost_per_lead_rub: advisorData?.target?.max_cpl_rub || parseFloat(advMaxCpl) || 500,
        daily_budget_limit_rub: parseFloat(advDailyLimit) || 1000,
        bid_autopilot: next, lead_temperature: "тёплый"})
    });
    setAdvAutopilot(next);
    notify(next ? "Автопилот ставок ВКЛЮЧЁН — Борис будет сам управлять ставками каждый час." : "Автопилот выключен — снова только рекомендации.");
  };
  const [escalations, setEscalations] = useState<any[]>([]);
  const [unreadEscalationsCount, setUnreadEscalationsCount] = useState(0);
  const [showEscalationPopup, setShowEscalationPopup] = useState(true);

  const [newClients, setNewClients] = useState<any[]>([]);
  const [newClientsLoading, setNewClientsLoading] = useState(false);

  const loadNewClients = () => {
    setNewClientsLoading(true);
    apiFetch("/api/avito/director/new_clients")
      .then(r => r.json())
      .then(d => {
        setNewClients(d.clients || []);
        setNewClientsLoading(false);
      })
      .catch(() => setNewClientsLoading(false));
  };

  useEffect(() => {
    if (!currentAccount) return;
    apiFetch(`/api/payments/prices?account_id=${encodeURIComponent(currentAccount)}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setLivePrices(d["цены"]); })
      .catch(() => {});
  }, [currentAccount]);

  const loadDirectorOverview = () => {
    setDirectorLoading(true);
    apiFetch("/api/avito/director_overview")
      .then(r => r.json())
      .then(d => {
        if (d.status === "ok") {
          setDirectorOverview(d.accounts);
          setEscalations(d.escalations || []);
          setUnreadEscalationsCount(d.unread_escalations_count || 0);
        }
        setDirectorLoading(false);
      })
      .catch(() => setDirectorLoading(false));
  };

  const toggleUnlimited = async (accountId: string, current: boolean) => {
    const next = !current;
    try {
      const res = await apiFetch("/api/billing/set_unlimited", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({account_id: accountId, unlimited: next})
      });
      if (!res.ok) {
        notify(`Не удалось переключить лимиты (ошибка ${res.status}) — попробуйте ещё раз.`);
        return;
      }
      const data = await res.json();
      if (data.status !== "ok") {
        notify("Не удалось переключить лимиты — сервер вернул ошибку.");
        return;
      }
      setDirectorOverview((prev: any[]) => prev.map(a =>
        a.account_id === accountId ? {...a, billing: {...a.billing, unlimited: next}} : a
      ));
      notify(next ? "🔓 Лимиты отключены для этого аккаунта — Борис будет работать без ограничений тарифа." : "🔒 Лимиты тарифа снова включены для этого аккаунта.");
    } catch {
      notify("Не удалось переключить лимиты — проверьте соединение и попробуйте ещё раз.");
    }
  };

  useEffect(() => {
    if (directorMode) { loadDirectorOverview(); loadNewClients(); }
    if (directorMode && !dsData) { loadDirectorStatsRange(dsFrom, dsTo); }
    if (directorMode) { apiFetch("/api/qa/auto").then(r=>r.json()).then(d=>setQaAuto(!!d.enabled)).catch(()=>{}); }
  }, [directorMode]);

  // Проверяем эскалации сразу при загрузке страницы, независимо от режима директора
  useEffect(() => {
    apiFetch("/api/avito/director_overview")
      .then(r => r.json())
      .then(d => {
        if (d.status === "ok") {
          setEscalations(d.escalations || []);
          setUnreadEscalationsCount(d.unread_escalations_count || 0);
        }
      })
      .catch(() => {});
  }, []);

  const addAccount = async () => {
    if (!newAccount.name || !newAccount.login) return alert("Заполните название и логин");
    setAddingAccount(true);
    const slug = newAccount.name.toLowerCase()
      .replace(/[а-яё]/g, (c: string) => ({а:"a",б:"b",в:"v",г:"g",д:"d",е:"e",ё:"e",ж:"zh",з:"z",и:"i",й:"y",к:"k",л:"l",м:"m",н:"n",о:"o",п:"p",р:"r",с:"s",т:"t",у:"u",ф:"f",х:"h",ц:"c",ч:"ch",ш:"sh",щ:"sch",ъ:"",ы:"y",ь:"",э:"e",ю:"yu",я:"ya"} as any)[c] || c)
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") + "_" + Date.now().toString().slice(-5);
    try {
      const res = await apiFetch("/api/accounts/create", {
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
          company_advantages: newAccount.companyAdvantages,
          client_goal: wizardGoal,
          client_goal_text: wizardGoalText
        })
      });
      const data = await res.json();
      if (data.status === "ok") {
        const listRes = await apiFetch("/api/accounts/list");
        const listData = await listRes.json();
        if (listData.status === "ok") setAllAccounts(listData.accounts);
        setCurrentAccount(slug);
        // Запускаем автоопределение категории в фоне — не блокируем интерфейс
        if (newAccount.companyNiche) {
          apiFetch("/api/avito/detect_category", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ account_id: slug, niche: newAccount.companyNiche })
          }).catch(() => {});
        }
        alert("✅ Аккаунт создан! Переключились на него. Борис в фоне определяет категорию по вашей нише — это займёт около минуты.");
        setNewAccount({ name: "", login: "", password: "", comment: "", client_id: "", client_secret: "",
          companyWebsite: "", companyDescription: "", companyNiche: "", companyTone: "Дружелюбный", companyAdvantages: "" });
        setShowAddForm(false);
        setStep(0);
        if (wizardGoal === "site") setActiveTab("parser");
        else if (wizardGoal === "avito" || wizardGoal === "scratch") setActiveTab("listings");
        setWizardGoal("");
        setWizardGoalText("");
      } else {
        alert(data.message || "Не удалось создать аккаунт");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setAddingAccount(false);
  };

  const [generatingAiFor, setGeneratingAiFor] = useState<number | null>(null);
  const [aiPreview, setAiPreview] = useState<{index: number, title: string, description: string, images: string[]} | null>(null);

  const applyAiPreview = () => {
    if (!aiPreview) return;
    setParsedItems(prev => {
      const copy = [...prev];
      copy[aiPreview.index] = { ...copy[aiPreview.index], title: aiPreview.title, description: aiPreview.description, images: aiPreview.images, aiGenerated: true };
      return copy;
    });
    setAiPreview(null);
  };

  const updateAiPreview = (field: "title" | "description", value: string) => {
    setAiPreview(prev => prev ? {...prev, [field]: value} : prev);
  };

  const applyBoldPreview = () => {
    const ta = document.getElementById("ai-preview-desc") as HTMLTextAreaElement;
    if (!ta || !aiPreview) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    if (start === end) { alert("Выделите текст, который нужно сделать жирным"); return; }
    const val = ta.value;
    const newVal = val.slice(0, start) + "<strong>" + val.slice(start, end) + "</strong>" + val.slice(end);
    updateAiPreview("description", newVal);
  };

  const generateAiForItem = async (i: number) => {
    const item = parsedItems[i];
    if (!item.product_url) { alert("Нет ссылки на товар для детального парсинга"); return; }
    setGeneratingAiFor(i);
    try {
      const detailRes = await apiFetch("/api/parser/parse_detail", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url: item.product_url })
      });
      const detail = await detailRes.json();
      const noiseKeys = ["адрес", "телефон", "email", "почта", "сайт"];
      const filteredChars = Object.entries(detail.characteristics || {})
        .filter(([k]) => !noiseKeys.some(n => k.toLowerCase().includes(n)))
        .slice(0, 6);
      const charsText = filteredChars.map(([k,v]) => `${k}: ${v}`).join(", ");
      const cleanDesc = (detail.description || "")
        .replace(/[-–—]\s*/g, ", ")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 250)
        .replace(/,[^,]*$/, "");
      // Модели отдаём только чистое название — это стабильно работает.
      // Реальные характеристики/особенности добавляем сами ниже, без участия ИИ.
      const topic = item.title;
      const specsBlock = charsText
        ? `<br><br><strong>Характеристики:</strong><br>${filteredChars.map(([k,v]) => `${k}: ${v}`).join("<br>")}`
        : (cleanDesc ? `<br><br><strong>Особенности:</strong><br>${cleanDesc}` : "");

      const genRes = await apiFetch("/api/avito/generate_ads", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ topic, count: 1, price_from: 0, price_to: 0, account_id: currentAccount })
      });
      const genData = await genRes.json();
      if (genData.ads && genData.ads[0]) {
        let finalDescription = genData.ads[0].description + specsBlock;
        if (companyInfo.trim()) {
          finalDescription += `<br><br><strong>О компании:</strong><br>${companyInfo.trim()}`;
        }
        setAiPreview({ index: i, title: genData.ads[0].title, description: finalDescription, images: detail.images || item.images });
      } else {
        alert("Не удалось сгенерировать текст");
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setGeneratingAiFor(null);
  };

  const [companyInfo, setCompanyInfo] = useState("");
  const [showCompanyEdit, setShowCompanyEdit] = useState(false);

  const selectAllParsedItems = () => {
    const all: {[key:number]: boolean} = {};
    parsedItems.forEach((_, i) => { all[i] = true; });
    setSelectedItems(all);
  };

  const runParser = () => {
    if (!parserUrl) return alert("Введите ссылку");
    if (!parserConsent) return alert("Подтвердите согласие с условиями использования данных перед запуском");
    apiFetch("/api/storage/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: `parser_consent_${Date.now()}`, value: { url: parserUrl, agreed_at: new Date().toISOString() } })
    }).catch(() => {});
    setParserLoading(true);
    setParsedItems([]);
    apiFetch("/api/parser/parse", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url: parserUrl})
    }).then(r => r.json()).then(data => {
      setParsedItems(data.products || []);
      setParserLoading(false);
      if (!data.products || data.products.length === 0) {
        alert("Товары не найдены на этой странице. Возможные причины:\n— это не страница каталога (попробуйте прямую ссылку на раздел с товарами)\n— сайт защищён от автоматического доступа\n— товары на этом сайте показаны без цен");
      }
    });
  };

  const downloadPhotos = () => {
    apiFetch("/api/parser/download_photos", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({products: parsedItems})
    }).then(r => r.blob()).then(blob => {
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "boris_photos.zip";
      document.body.appendChild(a); a.click(); a.remove();
    });
  };

  const toggleItem = (i: number) => {
    setSelectedItems(prev => ({...prev, [i]: !prev[i]}));
  };

  const selectAllItems = () => {
    const all: {[key:number]: boolean} = {};
    parsedItems.forEach((_, i) => { all[i] = true; });
    setSelectedItems(all);
  };

  const clearSelection = () => {
    setSelectedItems({});
  };

  const countSelected = () => Object.values(selectedItems).filter(Boolean).length;

  const applyBulkSchedule = () => {
    if (!bulkStartDate) { alert("Укажите дату старта"); return; }
    const chosen = parsedItems.map((_, i) => i).filter(i => selectedItems[i]);
    if (chosen.length === 0) { alert("Отметьте галочками объявления"); return; }
    const [h, m] = bulkStartTime.split(":").map(Number);
    const start = new Date(bulkStartDate);
    start.setHours(h || 10, m || 0, 0, 0);
    const newSched = {...itemSchedules};
    chosen.forEach((idx, order) => {
      const t = new Date(start.getTime() + order * bulkInterval * 60000);
      const dd = String(t.getDate()).padStart(2, "0");
      const mo = String(t.getMonth() + 1).padStart(2, "0");
      const yy = String(t.getFullYear()).slice(2);
      const hh = String(t.getHours()).padStart(2, "0");
      const mi = String(t.getMinutes()).padStart(2, "0");
      newSched[idx] = `${dd}.${mo}.${yy} ${hh}:${mi}`;
    });
    setItemSchedules(newSched);
    alert(`Расписание применено к ${chosen.length} объявлениям`);
  };

  const publishSingleItem = async (i: number) => {
    const raw = parsedItems[i];
    const built = raw.aiGenerated || !selectedTemplate ? raw : applyTemplate(raw, selectedTemplate);
    const priceNum = parseInt(String(built.price).replace(/\D/g, "")) || 0;
    const feedItem = {
      id: "boris-parsed-" + Date.now() + "-" + i,
      title: built.title,
      description: built.description,
      price: priceNum,
      category: "Предложение услуг",
      service_type: "Товары",
      address: genAddress || "Москва",
      images: (built.images || []).slice(0, 10),
      params: {}
    };
    try {
      const res = await apiFetch("/api/avito/generate_feed", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, items: [feedItem], merge: true })
      });
      const data = await res.json();
      if (data.status === "ok" || data.feed_url) {
        alert("✅ Объявление добавлено в фид (не заменяет остальные)!\n" + (data.feed_url || ""));
      } else {
        alert("Не удалось опубликовать: " + (data.message || JSON.stringify(data)));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const publishWithDelay = async () => {
    const delay = (ms: number) => new Promise(r => setTimeout(r, ms));
    let published = 0;
    let failed = 0;
    for (let i = 0; i < parsedItems.length; i++) {
      const raw = parsedItems[i];
      if (!raw.aiGenerated && !selectedTemplate) {
        setPublishStatus(`⚠️ Пропущено ${i + 1}: нет шаблона и не сгенерировано ИИ`);
        failed++;
        continue;
      }
      const built = raw.aiGenerated || !selectedTemplate ? raw : applyTemplate(raw, selectedTemplate);
      const priceNum = parseInt(String(built.price).replace(/\D/g, "")) || 0;
      const feedItem = {
        id: "boris-parsed-" + Date.now() + "-" + i,
        title: built.title,
        description: built.description,
        price: priceNum,
        category: "Предложение услуг",
        service_type: "Товары",
        address: genAddress || "Москва",
        images: (built.images || []).slice(0, 10),
        params: {}
      };
      setPublishStatus(`📤 Публикую ${i + 1} из ${parsedItems.length}: ${built.title}`);
      try {
        const res = await apiFetch("/api/avito/generate_feed", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ account_id: currentAccount, items: [feedItem], merge: true })
        });
        const data = await res.json();
        if (data.status === "ok" || data.feed_url) {
          published++;
        } else {
          failed++;
          setPublishStatus(`⚠️ Ошибка на ${i + 1}: ${data.message || "не удалось"}`);
        }
      } catch {
        failed++;
        setPublishStatus(`⚠️ Ошибка связи на объявлении ${i + 1}`);
      }
      if (i < parsedItems.length - 1 && selectedTemplate) {
        const waitMs = (selectedTemplate.delayMin + Math.random() * (selectedTemplate.delayMax - selectedTemplate.delayMin)) * 60 * 1000;
        setPublishStatus(`⏳ Жду ${Math.round(waitMs/1000)} сек перед следующим... (готово ${published})`);
        await delay(waitMs);
      }
    }
    setPublishStatus(`✅ Готово! Добавлено в фид: ${published}${failed ? `, ошибок/пропущено: ${failed}` : ""}`);
  };

  const API = "/api/avito";

  const loadKpiSettings = async () => {
    try {
      const res = await apiFetch(`/api/avito/kpi_settings?account_id=${currentAccount}`);
      const data = await res.json();
      if (data.settings) {
        setKpiTargetLeads(data.settings.target_leads_per_day || 0);
        setKpiMaxCpl(data.settings.max_cost_per_lead_rub || 0);
        setKpiLeadTemp(data.settings.lead_temperature || "любые");
      }
    } catch {}
  };

  const saveKpiSettings = async () => {
    try {
      await apiFetch("/api/avito/set_kpi_settings", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: currentAccount,
          target_leads_per_day: kpiTargetLeads,
          max_cost_per_lead_rub: kpiMaxCpl,
          lead_temperature: kpiLeadTemp
        })
      });
      setKpiSaved(true);
      setTimeout(() => setKpiSaved(false), 2000);
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const analyzeWordstat = async () => {
    if (!wordstatQuery.trim()) return;
    setWordstatLoading(true);
    setWordstatResult(null);
    try {
      const res = await apiFetch(`/api/avito/wordstat_analyze?query=${encodeURIComponent(wordstatQuery)}&account_id=${currentAccount}`);
      const data = await res.json();
      setWordstatResult(data);
      if (data.status === "ok") loadWordstatHistory();
    } catch {
      setWordstatResult({status: "error", message: "Ошибка связи с сервером"});
    }
    setWordstatLoading(false);
  };

  const loadWordstatHistory = async () => {
    try {
      const res = await apiFetch(`/api/avito/wordstat_history?account_id=${currentAccount}`);
      const data = await res.json();
      setWordstatHistory(data.analyses || []);
    } catch {}
  };

  const checkKpi = async () => {
    try {
      const res = await apiFetch(`/api/avito/kpi_check?account_id=${currentAccount}`);
      const data = await res.json();
      setKpiCheckResult(data);
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const getKpiPlan = async () => {
    if (!kpiPlanIdPrefix.trim()) { alert("Укажите префикс id направления (например boris-shkaf-...)"); return; }
    try {
      const res = await apiFetch("/api/avito/kpi_plan_execute", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, id_prefix: kpiPlanIdPrefix, confirm: false })
      });
      const data = await res.json();
      if (data.status === "plan_ready") {
        setKpiPlan(data.plan);
      } else {
        alert("Не получилось: " + (data.message || "попробуйте ещё раз"));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const executeKpiPlan = async () => {
    setKpiPlanExecuting(true);
    try {
      const res = await apiFetch("/api/avito/kpi_plan_execute", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, id_prefix: kpiPlanIdPrefix, confirm: true })
      });
      const data = await res.json();
      if (data.status === "ok") {
        alert(`Готово: обновлено ${data.updated} объявлений направления "${data.direction}" новым усиленным текстом`);
        setKpiPlan(null);
        checkKpi();
      } else {
        alert("Не получилось: " + (data.message || "попробуйте ещё раз"));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
    setKpiPlanExecuting(false);
  };

  const deleteKpiSettings = async () => {
    if (!confirm("Удалить цель по лидам для этого аккаунта?")) return;
    try {
      await apiFetch(`/api/avito/delete_kpi_settings?account_id=${currentAccount}`, { method: "POST" });
      setKpiTargetLeads(0);
      setKpiMaxCpl(0);
      setKpiLeadTemp("любые");
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const loadRepublishSettings = async () => {
    try {
      const res = await apiFetch(`/api/avito/republish_settings?account_id=${currentAccount}`);
      const data = await res.json();
      if (data.settings) {
        setRepublishMinViews(data.settings.min_views_no_contact ?? 10);
        setRepublishZeroDays(data.settings.zero_views_days ?? 7);
        setRepublishEnabled(data.settings.enabled ?? true);
      }
    } catch {}
  };

  const saveRepublishSettings = async () => {
    try {
      await apiFetch("/api/avito/set_republish_settings", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: currentAccount,
          min_views_no_contact: republishMinViews,
          zero_views_days: republishZeroDays,
          enabled: republishEnabled
        })
      });
      setRepublishSettingsSaved(true);
      setTimeout(() => setRepublishSettingsSaved(false), 2000);
      loadRepublishCandidates();
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const deleteRepublishSettings = async () => {
    if (!confirm("Сбросить настройки перепубликации к значениям по умолчанию (10 просмотров / 7 дней)?")) return;
    try {
      await apiFetch(`/api/avito/delete_republish_settings?account_id=${currentAccount}`, { method: "POST" });
      setRepublishMinViews(10);
      setRepublishZeroDays(7);
      setRepublishEnabled(true);
      loadRepublishCandidates();
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const loadRepublishCandidates = async () => {
    setRepublishLoading(true);
    try {
      const res = await apiFetch(`/api/avito/republish_check?account_id=${currentAccount}`);
      const data = await res.json();
      setRepublishCandidates(data.candidates || []);
      setRepublishTotal(data.total_checked ?? null);
    } catch {
      alert("Ошибка связи с сервером");
    }
    setRepublishLoading(false);
  };

  const applyRepublish = async () => {
    if (republishCandidates.length === 0) return;
    try {
      const res = await apiFetch("/api/avito/republish_apply", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, item_ids: republishCandidates.map((c: any) => c.id) })
      });
      const data = await res.json();
      if (data.status === "needs_confirmation") {
        if (confirm(data.message + "\n\nПодтвердить снятие и замену прямо сейчас (разово, без включения автопилота)?")) {
          alert("Чтобы выполнить это действие сейчас, включите режим «Работать самостоятельно» выше и повторите.");
        }
      } else if (data.status === "ok") {
        alert(`Готово: снято ${data.removed}, создано замен ${data.replaced}`);
        loadRepublishCandidates();
      } else {
        alert("Не получилось: " + (data.message || "попробуйте ещё раз"));
      }
    } catch {
      alert("Ошибка связи с сервером");
    }
  };

  const loadAutopilot = async () => {
    try {
      const d = await apiFetch(`${API}/autopilot_settings?account_id=${currentAccount}`).then(r => r.json());
      const s = d.settings || {};
      setAutopilotMode(s.mode || "always_ask");
      setAutopilotFrom(s.date_from || "");
      setAutopilotTo(s.date_to || "");
    } catch (e) {}
  };

  const saveAutopilot = async () => {
    const body: any = { account_id: currentAccount, mode: autopilotMode };
    if (autopilotMode === "dates") { body.date_from = autopilotFrom; body.date_to = autopilotTo; }
    await apiFetch(`${API}/set_autopilot_settings`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    alert("Настройки автопилота сохранены");
    loadAuditLog();
  };

  const loadAuditLog = async () => {
    try {
      const d = await apiFetch(`${API}/audit_log?account_id=${currentAccount}`).then(r => r.json());
      setAuditLog(d.log || []);
    } catch (e) {}
  };

  const PLAN_API = "/api/plan_items";
  const [accountTasks, setAccountTasks] = useState<any[]>([]);

  // === Борис заговаривает сам ===
  const [borisAsked, setBorisAsked] = useState<Record<string, boolean>>({});
  const [borisBubbleOff, setBorisBubbleOff] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (localStorage.getItem("boris_bubble_off") === "1") setBorisBubbleOff(true);
    try {
      const saved = localStorage.getItem("boris_asked");
      if (saved) setBorisAsked(JSON.parse(saved));
    } catch {}
  }, []);
  useEffect(() => {
    if (typeof window !== "undefined" && borisBubbleOff) localStorage.setItem("boris_bubble_off", "1");
  }, [borisBubbleOff]);
  useEffect(() => {
    if (typeof window !== "undefined" && Object.keys(borisAsked).length > 0) localStorage.setItem("boris_asked", JSON.stringify(borisAsked));
  }, [borisAsked]);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (localStorage.getItem("boris_bubble_off") === "1") setBorisBubbleOff(true);
    try {
      const saved = localStorage.getItem("boris_asked");
      if (saved) setBorisAsked(JSON.parse(saved));
    } catch {}
  }, []);
  useEffect(() => {
    if (typeof window !== "undefined" && borisBubbleOff) localStorage.setItem("boris_bubble_off", "1");
  }, [borisBubbleOff]);
  useEffect(() => {
    if (typeof window !== "undefined" && Object.keys(borisAsked).length > 0) localStorage.setItem("boris_asked", JSON.stringify(borisAsked));
  }, [borisAsked]);

  const borisQuestions = [
    {
      id: "advantages",
      when: () => !companyForm?.advantages || !String(companyForm.advantages).trim(),
      text: "Я пока не знаю, чем вы лучше конкурентов — тексты выйдут обезличенными. Заполните «Преимущества» во вкладке «О компании», и объявления станут заметно сильнее.",
      actions: [{label: "Открыть", go: "company"}],
    },
    {
      id: "point",
      when: () => true,
      text: "Есть ли у вас точка, куда клиент может приехать — офис, шоурум, склад? Это влияет на геолокацию объявлений и на то, что я пишу в текстах.",
      actions: [{label: "Расскажу", go: "company"}],
    },
    {
      id: "geo",
      when: () => true,
      text: "Работаете по всей России или по конкретным городам? От этого зависит, сколько объявлений и где имеет смысл размещать.",
      actions: [{label: "Расскажу", go: "company"}],
    },
    {
      id: "who",
      when: () => true,
      text: "Кто отвечает на звонки и в чате — вы сами, менеджер или отдел продаж? Мне важно знать, обещать ли клиенту быстрый перезвон.",
      actions: [{label: "Расскажу", go: "company"}],
    },
  ];

  const [borisBubbleOpen, setBorisBubbleOpen] = useState(false);
  const borisCurrentQ = borisBubbleOff ? null : borisQuestions.find(q => !borisAsked[q.id] && q.when());

  // === Настроение Бориса — считается из состояния аккаунта ===
  const borisMood: any = (() => {
    if (accountTasks.some((t: any) => t.status === "running")) return "thinking";
    if (!companyForm?.advantages || !String(companyForm.advantages).trim()) return "curious";
    return "idle";
  })();
  const [parsedProducts, setParsedProducts] = useState<any[]>([]);
  const [parseUrl, setParseUrl] = useState("");
  const [parseLimit, setParseLimit] = useState<number>(0);
  const [parseSource, setParseSource] = useState<string | null>(null);
  const [parseSourceUrls, setParseSourceUrls] = useState<string[]>([]);
  const [parseLoading, setParseLoading] = useState(false);
  const [parseProgress, setParseProgress] = useState<{done: number, total: number} | null>(null);
  const [selectedParsedIdx, setSelectedParsedIdx] = useState<Set<number>>(new Set());
  const [editingParsedIdx, setEditingParsedIdx] = useState<number | null>(null);
  const [feedCheckLoading, setFeedCheckLoading] = useState(false);
  const [feedCheckResult, setFeedCheckResult] = useState<{status: string, raw_text: string} | null>(null);

  // Конвейер "карточки -> публикация": состояние текущей партии черновиков и прогресс шагов
  const [enrichLoading, setEnrichLoading] = useState(false);
  const [enrichProgress, setEnrichProgress] = useState("");
  const [pipelineBatchLabelInput, setPipelineBatchLabelInput] = useState("");
  const [pipelineTopicInput, setPipelineTopicInput] = useState("");
  const [toDraftsLoading, setToDraftsLoading] = useState(false);
  const [pipelineBatch, setPipelineBatch] = useState<{label: string, category: string, count: number} | null>(null);
  const [bannerCount, setBannerCount] = useState(5);
  const [photosPerAdInput, setPhotosPerAdInput] = useState(8);
  const [bannerFolderInput, setBannerFolderInput] = useState("");
  const [batchBannerLoading, setBatchBannerLoading] = useState(false);
  const [bannerResult, setBannerResult] = useState("");
  const [pipelineValidateLoading, setPipelineValidateLoading] = useState(false);
  const [pipelineValidateResult, setPipelineValidateResult] = useState<{status: string, raw_text: string} | null>(null);
  const [pipelinePublishLoading, setPipelinePublishLoading] = useState(false);

  // "Конвейер направления" - явная форма с конкретными параметрами (НЕ свободный промт-оркестратор):
  // владелец задаёт направление + числа, Борис проходит все 6 этапов сам, с проверкой партии перед
  // публикацией. Названо отдельно от "pipeline*" выше - та группа про старый конвейер парсинга карточек.
  const [showPipelineForm, setShowPipelineForm] = useState(false);
  const [directionForm, setDirectionForm] = useState({
    direction: "", count: 10, bannerCount: 5, uniquifyTitles: true, uniquifyDescriptions: true,
    autoCheck: true, autoPublish: true, priceFrom: 5000, priceTo: 50000,
  });
  const [directionStarting, setDirectionStarting] = useState(false);
  // История запусков конвейера ТЕКУЩЕГО аккаунта - каждый запуск это 1-3 связанные Task-записи
  // (pipeline_texts -> pipeline_banners -> pipeline_finalize, см. depends_on_task_id), группируем
  // их в одну карточку по payload.root_task_id. account_id закладывается В САМУ задачу на бэкенде
  // (start_pipeline), поэтому переключение аккаунта здесь не может перепутать чужой прогресс -
  // просто следующий поллинг перезапрашивает список уже для нового currentAccount.
  const [pipelineHistory, setPipelineHistory] = useState<any[]>([]);

  const loadPipelineHistory = async () => {
    if (!currentAccount) return;
    try {
      const r = await apiFetch(`/api/tasks/list?account_id=${currentAccount}`);
      const d = await r.json();
      if (d.status !== "ok") return;
      const pipelineTasks = (d.tasks || []).filter((t: any) => t.task_type && t.task_type.startsWith("pipeline_"));
      const groups: Record<string, any[]> = {};
      for (const t of pipelineTasks) {
        const rootId = t.payload?.root_task_id ?? t.id;
        (groups[rootId] = groups[rootId] || []).push(t);
      }
      const runs = Object.entries(groups).map(([rootId, tasks]) => {
        tasks.sort((a, b) => a.id - b.id);
        const active = tasks.find((t: any) => t.status === "needs_confirmation")
          || tasks.find((t: any) => t.status === "running" || t.status === "queued")
          || tasks[tasks.length - 1];
        const direction = tasks[0]?.payload?.direction || active?.result?.direction || "Без названия";
        const batchId = tasks[0]?.payload?.batch_id || active?.result?.batch_id || null;
        return { rootId, direction, active, tasks, createdAt: tasks[0]?.created_at, batchId };
      });

      // Пакетный запуск (один промт -> N направлений) - группируем связанные runs по batch_id
      // в ОДНУ карточку пакета с общей сводкой, одиночные запуски остаются как раньше.
      const batchMap: Record<string, any[]> = {};
      const items: any[] = [];
      for (const run of runs) {
        if (run.batchId) {
          (batchMap[run.batchId] = batchMap[run.batchId] || []).push(run);
        } else {
          items.push({ type: "single", createdAt: run.createdAt, run });
        }
      }
      for (const [batchId, batchRuns] of Object.entries(batchMap)) {
        batchRuns.sort((a, b) => a.rootId - b.rootId);
        items.push({ type: "batch", createdAt: batchRuns[0]?.createdAt, batchId, runs: batchRuns });
      }
      items.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
      setPipelineHistory(items);
    } catch (e) {}
  };

  useEffect(() => {
    if (activeTab !== "listings" || !currentAccount) return;
    loadPipelineHistory();
    const iv = setInterval(loadPipelineHistory, 4000);
    return () => clearInterval(iv);
  }, [activeTab, currentAccount]);

  const runDirectionPipeline = async () => {
    if (!currentAccount) return;
    if (!directionForm.direction.trim()) { alert("Укажите товарное направление"); return; }
    setDirectionStarting(true);
    try {
      const r = await apiFetch("/api/tasks/pipeline/start", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: currentAccount, direction: directionForm.direction,
          count: directionForm.count, banner_count: directionForm.bannerCount,
          uniquify_titles: directionForm.uniquifyTitles, uniquify_descriptions: directionForm.uniquifyDescriptions,
          auto_check: directionForm.autoCheck, auto_publish: directionForm.autoPublish,
          price_from: directionForm.priceFrom, price_to: directionForm.priceTo,
        })
      });
      const d = await r.json();
      setDirectionStarting(false);
      if (d.status !== "ok") { alert("Не удалось запустить конвейер"); return; }
      // fire-and-forget: задача ушла в фон, окно сразу закрываем и чистим форму для следующего запуска
      setShowPipelineForm(false);
      setDirectionForm({
        direction: "", count: 10, bannerCount: 5, uniquifyTitles: true, uniquifyDescriptions: true,
        autoCheck: true, autoPublish: true, priceFrom: 5000, priceTo: 50000,
      });
      loadPipelineHistory();
    } catch (e) {
      setDirectionStarting(false);
      alert("Ошибка связи с сервером");
    }
  };

  const [pipelineAnswerDrafts, setPipelineAnswerDrafts] = useState<Record<number, string>>({});

  const confirmPipelineTask = async (taskId: number, action: "proceed" | "cancel", answer?: string) => {
    try {
      await apiFetch(`/api/tasks/${taskId}/confirm`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ action, answer: answer || "" })
      });
      setPipelineAnswerDrafts(prev => { const next = {...prev}; delete next[taskId]; return next; });
      loadPipelineHistory();
    } catch (e) {}
  };

  // Карточка ОДНОГО направления (используется и как самостоятельная запись истории, и вложенно
  // внутри карточки пакета - nested чуть уменьшает отступы/фон, чтобы не спорить с внешней рамкой).
  const renderPipelineRunCard = (run: any, nested = false) => {
    const res = run.active?.result || {};
    const status = run.active?.status;
    const percent = res.percent ?? (status === "done" || status === "ready" ? 100 : 0);
    const barColor = status === "needs_confirmation" ? "#F79009"
      : status === "error" || status === "cancelled" ? "#F04438"
      : status === "done" || status === "ready" ? "#12805C" : "#2F6FED";
    const statusLabel = status === "needs_confirmation" ? "⏸ ждёт подтверждения"
      : status === "error" ? "❌ ошибка" : status === "cancelled" ? "🚫 отменено"
      : status === "queued" ? "🕓 в очереди" : status === "running" ? "🔵 идёт"
      : "✅ готово";
    const batchLabel = `Конвейер: ${run.direction}`;
    const draftsCount = Array.isArray(res.draft_ids) ? res.draft_ids.length : null;
    const isFinished = status === "done" || status === "ready";
    return (
      <div key={run.rootId} className={nested ? "" : "b-panel b-card-eq"} style={{padding: nested ? "12px" : "18px", background: nested ? "#FFFFFF" : undefined, borderRadius: nested ? "10px" : undefined, border: nested ? "1px solid #EEF2FA" : undefined}}>
        <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:"10px"}}>
          <div style={{display:"flex", alignItems:"center", gap:"10px"}}>
            {!nested && <span className="b-icon-sm" style={{background:"linear-gradient(135deg,#12805C,#3DBE93)"}}>🚀</span>}
            <div>
              <div style={{fontWeight:"bold", fontSize:"15px", color:"#1D2939"}}>{run.direction}</div>
              <div style={{fontSize:"13px", color:"#667085"}}>{run.createdAt}</div>
            </div>
          </div>
          <span style={{fontSize:"13px", fontWeight:"bold", color: barColor}}>{statusLabel}</span>
        </div>
        <div style={{fontSize:"14px", color:"#344054", marginBottom:"8px"}}>{res.step_label || run.active?.error_message || "…"}</div>
        <div style={{background:"#EEF2FA", borderRadius:"6px", height:"8px", overflow:"hidden"}}>
          <div style={{width:`${percent}%`, height:"100%", background: barColor, borderRadius:"6px", transition:"width 0.6s ease, background-color 0.3s ease"}} />
        </div>
        {status === "needs_confirmation" && (
          <div style={{marginTop:"12px"}}>
            {res.question && <div style={{fontSize:"13px", color:"#8A5A00", background:"#FFF6E9", border:"1px solid #FDEBC8", borderRadius:"8px", padding:"10px", marginBottom:"10px"}}>{res.question}</div>}
            <textarea placeholder="Ваш ответ Борису (например: собственное производство, цена за штуку, цвет — белый)"
              value={pipelineAnswerDrafts[run.active.id] || ""}
              onChange={e => setPipelineAnswerDrafts(prev => ({...prev, [run.active.id]: e.target.value}))}
              rows={2} style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"8px 10px", fontSize:"14px", fontFamily:"inherit", marginBottom:"10px", resize:"vertical"}} />
            <div style={{display:"flex", gap:"10px"}}>
              <button className="boris-btn-hover" onClick={() => confirmPipelineTask(run.active.id, "proceed", pipelineAnswerDrafts[run.active.id])}
                style={{flex:1, background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"8px", fontWeight:"bold", cursor:"pointer", fontSize:"14px"}}>
                ✏️ Ответить и продолжить
              </button>
              <button className="boris-btn-hover" onClick={() => confirmPipelineTask(run.active.id, "proceed")}
                style={{background:"#F79009", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"8px 12px", cursor:"pointer", fontSize:"14px", whiteSpace:"nowrap"}}>
                Всё равно
              </button>
              <button className="boris-btn-hover" onClick={() => confirmPipelineTask(run.active.id, "cancel")}
                style={{background:"#FFFFFF", color:"#667085", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"8px 16px", cursor:"pointer", fontSize:"14px"}}>
                Отмена
              </button>
            </div>
          </div>
        )}
        {isFinished && (
          <div style={{marginTop:"12px"}}>
            <button className="boris-btn-hover" onClick={() => { setActiveTab("listings"); setListingFilter("drafts"); setDraftBatchFilter(batchLabel); loadDrafts(); }}
              style={{background:"#FFFFFF", color:"#12805C", border:"1px solid #12805C", borderRadius:"8px", padding:"7px 14px", fontSize:"14px", fontWeight:"bold", cursor:"pointer"}}>
              ✅ Готово{draftsCount != null ? ` ${draftsCount}` : ""} → Смотреть результаты
            </button>
          </div>
        )}
      </div>
    );
  };

  // Пакетный запуск направлений: один свободный промт -> N конвейеров. ОБЯЗАТЕЛЬНОЕ превью перед
  // запуском (деньги на баннерах) - parse_batch ничего не создаёт, только разбирает и показывает,
  // start_batch вызывается ТОЛЬКО по явному "Запустить всё".
  const [batchPromptText, setBatchPromptText] = useState("");
  const [batchParsing, setBatchParsing] = useState(false);
  const [batchPreview, setBatchPreview] = useState<any>(null);
  const [batchLaunching, setBatchLaunching] = useState(false);
  const [batchConfirmedBig, setBatchConfirmedBig] = useState(false);

  const parseBatchPrompt = async () => {
    if (!batchPromptText.trim()) { alert("Опишите направления текстом"); return; }
    setBatchParsing(true);
    setBatchPreview(null);
    setBatchConfirmedBig(false);
    try {
      const r = await apiFetch("/api/tasks/pipeline/parse_batch", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ message: batchPromptText })
      });
      const d = await r.json();
      if (d.status !== "ok") { alert(d.message || "Не удалось разобрать промт"); setBatchParsing(false); return; }
      setBatchPreview(d);
    } catch (e) {
      alert("Ошибка связи с сервером");
    }
    setBatchParsing(false);
  };

  const launchBatch = async () => {
    if (!batchPreview || !currentAccount) return;
    if (batchPreview.big_batch && !batchConfirmedBig) { setBatchConfirmedBig(true); return; }
    setBatchLaunching(true);
    try {
      const r = await apiFetch("/api/tasks/pipeline/start_batch", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, directions: batchPreview.directions })
      });
      const d = await r.json();
      setBatchLaunching(false);
      if (d.status !== "ok") { alert(d.message || "Не удалось запустить пакет"); return; }
      setBatchPreview(null);
      setBatchPromptText("");
      setBatchConfirmedBig(false);
      showBorisNotify("Пакет запущен", `Запущено ${d.total} конвейеров направлений. Прогресс — во вкладке «Объявления», в истории конвейера.`);
    } catch (e) {
      setBatchLaunching(false);
      alert("Ошибка связи с сервером");
    }
  };

  const checkFeedValidation = async () => {
    if (!currentAccount) return;
    setFeedCheckLoading(true);
    setFeedCheckResult(null);
    try {
      const feedUrl = `https://boris-ai.pro/api/avito/feed/${currentAccount}.xml`;
      const r = await apiFetch("/api/parser/validate_feed", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ feed_url: feedUrl })
      });
      const d = await r.json();
      setFeedCheckResult({ status: d.status, raw_text: d.raw_text || "" });
    } catch (e) {
      setFeedCheckResult({ status: "error", raw_text: "Не удалось выполнить проверку. Попробуйте ещё раз." });
    }
    setFeedCheckLoading(false);
  };
  const [lightboxImage, setLightboxImage] = useState<string | null>(null);
  const [galleryLoading, setGalleryLoading] = useState<number | null>(null);
  const [pgLightbox, setPgLightbox] = useState<string[]>([]);
  const [pgLightboxIdx, setPgLightboxIdx] = useState<number>(0);
  const [lightboxGallery, setLightboxGallery] = useState<string[]>([]);
  const [lightboxIdx, setLightboxIdx] = useState(0);
  const [allGalleriesLoading, setAllGalleriesLoading] = useState(false);
  const fetchAllGalleries = async () => {
    if (!currentAccount) return;
    showBorisConfirm("Собрать все фото", `Борис пройдёт по всем выгруженным карточкам (${parsedProducts.length} шт.) и соберёт полную галерею фото каждой в оригинальном размере. Это займёт несколько минут в фоне — можно продолжать работать, прогресс смотрите во вкладке «Задачи и план».`, async () => {
      setAllGalleriesLoading(true);
      try {
        const r = await apiFetch("/api/tasks/create", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ account_id: currentAccount, task_type: "fetch_all_galleries", payload: { account_id: currentAccount } })
        });
        const d = await r.json();
        if (d.status === "ok") {
          showBorisNotify("Задача поставлена", "Борис собирает фото всех карточек в фоне. Прогресс — во вкладке «✅ Задачи и план». Когда готово, нажмите «Обновить» в этом разделе.");
        }
      } catch (e) { showBorisNotify("Ошибка", "Не удалось запустить сбор фото"); }
      setAllGalleriesLoading(false);
    }, "Собрать все фото");
  };
  const fetchGalleryForCard = async (idx: number) => {
    setGalleryLoading(idx);
    try {
      const r = await apiFetch("/api/parser/parsed_products/fetch_gallery", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, product_idx: idx })
      });
      const d = await r.json();
      if (d.status === "ok") {
        setParsedProducts(prev => prev.map((p, i) => i === idx ? {...p, images: d.images} : p));
        showBorisNotify("Галерея собрана", `Найдено фото товара: ${d.count}.`);
      } else {
        showBorisNotify("Не удалось", d.message || "Фото не найдены на странице товара");
      }
    } catch (e) { showBorisNotify("Ошибка", "Не удалось собрать галерею"); }
    setGalleryLoading(null);
  };
  const openLightboxGallery = (images: string[], startIdx: number) => {
    setLightboxGallery(images.length > 0 ? images : []);
    setLightboxIdx(startIdx);
    setLightboxImage(images[startIdx] || null);
  };
  const boldMap: Record<string,string> = {
    'A':'𝗔','B':'𝗕','C':'𝗖','D':'𝗗','E':'𝗘','F':'𝗙','G':'𝗚','H':'𝗛','I':'𝗜','J':'𝗝','K':'𝗞','L':'𝗟','M':'𝗠','N':'𝗡','O':'𝗢','P':'𝗣','Q':'𝗤','R':'𝗥','S':'𝗦','T':'𝗧','U':'𝗨','V':'𝗩','W':'𝗪','X':'𝗫','Y':'𝗬','Z':'𝗭',
    'a':'𝗮','b':'𝗯','c':'𝗰','d':'𝗱','e':'𝗲','f':'𝗳','g':'𝗴','h':'𝗵','i':'𝗶','j':'𝗷','k':'𝗸','l':'𝗹','m':'𝗺','n':'𝗻','o':'𝗼','p':'𝗽','q':'𝗾','r':'𝗿','s':'𝘀','t':'𝘁','u':'𝘂','v':'𝘃','w':'𝘄','x':'𝘅','y':'𝘆','z':'𝘇',
    '0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰','5':'𝟱','6':'𝟲','7':'𝟳','8':'𝟴','9':'𝟵'
  };
  const toUnicodeBold = (text: string) => text.split("").map(ch => boldMap[ch] || ch).join("");
  const applyFormatToTextarea = (textareaId: string, mode: string) => {
    const el = document.getElementById(textareaId) as HTMLTextAreaElement;
    if (!el) return;
    const start = el.selectionStart, end = el.selectionEnd;
    const selected = el.value.slice(start, end);
    let replacement = selected;
    if (mode === "bold" && selected) {
      replacement = toUnicodeBold(selected);
    } else if (mode === "bullet" && selected) {
      replacement = selected.split("\n").map(l => l.trim() ? `• ${l}` : l).join("\n");
    } else if (mode === "number" && selected) {
      replacement = selected.split("\n").map((l, i) => l.trim() ? `${i+1}. ${l}` : l).join("\n");
    } else if (mode !== "bold" && mode !== "bullet" && mode !== "number") {
      el.value = el.value.slice(0, start) + mode + el.value.slice(end);
      el.focus(); el.selectionStart = el.selectionEnd = start + mode.length;
      return;
    }
    el.value = el.value.slice(0, start) + replacement + el.value.slice(end);
    el.focus(); el.selectionStart = start; el.selectionEnd = start + replacement.length;
  };
  const FormatToolbar = ({textareaId}: {textareaId: string}) => (
    <div style={{display:"flex", gap:"4px", flexWrap:"wrap", marginBottom:"6px"}}>
      <button type="button" onClick={() => applyFormatToTextarea(textareaId, "bold")} title="Жирный" style={{fontWeight:800, background:"#F5F5F7", border:"1px solid #E3E7F0", borderRadius:"4px", padding:"3px 8px", fontSize:"12px", cursor:"pointer"}}>Ж</button>
      <button type="button" onClick={() => applyFormatToTextarea(textareaId, "bullet")} title="Список" style={{background:"#F5F5F7", border:"1px solid #E3E7F0", borderRadius:"4px", padding:"3px 8px", fontSize:"12px", cursor:"pointer"}}>•</button>
      <button type="button" onClick={() => applyFormatToTextarea(textareaId, "number")} title="Нумерованный список" style={{background:"#F5F5F7", border:"1px solid #E3E7F0", borderRadius:"4px", padding:"3px 8px", fontSize:"12px", cursor:"pointer"}}>1.</button>
      <div style={{width:"1px", background:"#E3E7F0", margin:"2px 2px"}} />
      {["🔥","✅","❌","📞","💬","🎁","⭐","🚀"].map(emoji => (
        <button key={emoji} type="button" onClick={() => applyFormatToTextarea(textareaId, emoji)} style={{background:"#F5F5F7", border:"1px solid #E3E7F0", borderRadius:"4px", padding:"3px 6px", fontSize:"12px", cursor:"pointer"}}>{emoji}</button>
      ))}
    </div>
  );
  const [genDescLoading, setGenDescLoading] = useState(false);
  const genDescriptionForCard = async (idx: number) => {
    const p = parsedProducts[idx];
    setGenDescLoading(true);
    try {
      const r = await apiFetch("/api/tasks/create", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, task_type: "product_description", payload: {
          account_id: currentAccount, product_idx: idx, title: p.title, price: p.price, characteristics: p.characteristics
        }})
      });
      const d = await r.json();
      if (d.status === "ok") {
        showBorisNotify("Описание генерируется", "Борис пишет продающий текст (~10-20 сек). Через несколько секунд нажмите «Обновить» в карточке, чтобы увидеть результат.");
      }
    } catch (e) { showBorisNotify("Ошибка", "Не удалось запустить генерацию описания."); }
    setGenDescLoading(false);
  };
  const saveParsedEdit = (idx: number, updates: any) => {
    setParsedProducts(prev => prev.map((p, i) => i === idx ? {...p, ...updates} : p));
    setEditingParsedIdx(null);
  };
  const toggleParsedSelect = (idx: number) => {
    setSelectedParsedIdx(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  };
  const sendSelectedToDrafts = async () => {
    if (selectedParsedIdx.size === 0 || !currentAccount) return;
    if (!pipelineBatchLabelInput.trim()) { alert("Введите название партии (шаг 2 конвейера) — оно понадобится, чтобы найти эти черновики на следующих шагах"); return; }
    setToDraftsLoading(true);
    try {
      const r = await apiFetch("/api/parser/parsed_products/to_drafts", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, indices: Array.from(selectedParsedIdx), batch_label: pipelineBatchLabelInput.trim(), topic: pipelineTopicInput.trim() })
      });
      const d = await r.json();
      if (d.status === "ok") {
        showBorisNotify("Черновики созданы", `Создано черновиков: ${d.created}. Категория: ${d.category}. Партия «${d.batch_label}» — дальше шаги ниже.`);
        setSelectedParsedIdx(new Set());
        setPipelineBatch({ label: d.batch_label, category: d.category, count: d.created });
        setBannerResult(""); setPipelineValidateResult(null);
      } else {
        showBorisNotify("Ошибка", d.message || "Не удалось создать черновики");
      }
    } catch (e) { showBorisNotify("Ошибка", "Не удалось создать черновики"); }
    setToDraftsLoading(false);
  };
  const deleteSelectedParsed = async () => {
    if (selectedParsedIdx.size === 0 || !currentAccount) return;
    showBorisConfirm("Удалить карточки", `Удалить выбранные карточки (${selectedParsedIdx.size} шт.)? Это действие нельзя отменить.`, async () => {
      try {
        const r = await apiFetch("/api/parser/parsed_products/delete", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ account_id: currentAccount, indices: Array.from(selectedParsedIdx) })
        });
        const d = await r.json();
        if (d.status === "ok") {
          showBorisNotify("Удалено", `Удалено карточек: ${d.deleted}. Осталось: ${d.remaining}.`);
          setSelectedParsedIdx(new Set());
          loadParsedProducts();
        }
      } catch (e) { showBorisNotify("Ошибка", "Не удалось удалить карточки."); }
    }, "Удалить");
  };
  // Живой поллинг прогресса обогащения описаний (по образцу startParse) - показывает "товар N из M"
  // прямо во время выполнения, а не молчаливое ожидание done/error. Если партия упёрлась в таймаут,
  // бэкенд сам ставит задачу-продолжение (continued_as_task_id) - подхватываем её прозрачно для
  // клиента, ему не нужно нажимать кнопку заново.
  const enrichDescriptions = async () => {
    if (!currentAccount) return;
    setEnrichLoading(true);
    setEnrichProgress("Ставлю задачу в очередь...");
    try {
      const r = await apiFetch("/api/tasks/create", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, task_type: "enrich_descriptions", payload: { account_id: currentAccount, sample: genSample, force: true } })
      });
      const d = await r.json();
      if (d.status !== "ok") { setEnrichProgress("Не удалось поставить задачу"); setEnrichLoading(false); return; }

      let currentTaskId = d.task_id;
      await new Promise<void>((resolve) => {
        const iv = setInterval(async () => {
          try {
            const st = await apiFetch(`/api/tasks/status/${currentTaskId}`).then(r => r.json());
            const res = st.result;
            if (res) {
              const failedPart = res.failed ? `, ошибок: ${res.failed}` : "";
              const currentPart = res.current_item ? ` — сейчас: «${res.current_item}»` : "";
              setEnrichProgress(`Товар ${res.done ?? 0} из ${res.total ?? 0}${failedPart}${currentPart}`);
            }
            if (st.status === "done") {
              if (res && res.finished === false && res.continued_as_task_id) {
                currentTaskId = res.continued_as_task_id;
                return; // партия продолжается новой задачей - продолжаем поллить её же интервалом
              }
              clearInterval(iv);
              const failedNote = res?.failed ? `, пропущено ${res.failed} (подробности в уведомлениях Бориса)` : "";
              setEnrichProgress(`Готово: обогащено ${res?.done ?? 0} из ${res?.total ?? 0} карточек${failedNote}`);
              loadParsedProducts();
              resolve();
            } else if (st.status === "error") {
              clearInterval(iv);
              setEnrichProgress("Ошибка: " + (st.error_message || "не удалось"));
              resolve();
            }
          } catch (e) {}
        }, 3000);
      });
    } catch (e) { setEnrichProgress("Ошибка связи с сервером"); }
    setEnrichLoading(false);
  };
  // Привязка фото/баннеров к партии ЧЕРНОВИКОВ (не через конвейер) — по batch_label текущего фильтра
  const [draftPhotoLoading, setDraftPhotoLoading] = useState(false);
  // добавление фото к черновику: открыть галерею аккаунта и выбрать
  const [addPhotoDraftId, setAddPhotoDraftId] = useState<string | null>(null);
  const [accountPhotos, setAccountPhotos] = useState<{[folder: string]: string[]}>({});
  const openAddPhoto = async (draftId: string) => {
    if (!currentAccount) return;
    try {
      const r = await apiFetch("/api/avito/images_list?account_id=" + currentAccount);
      const d = await r.json();
      setAccountPhotos(d.folders || {});
      setAddPhotoDraftId(draftId);
    } catch (e) { alert("Не удалось загрузить фото аккаунта"); }
  };
  const addPhotoToDraft = async (imgUrl: string) => {
    if (!currentAccount || !addPhotoDraftId) return;
    const draft = draftItems.find((x: any) => x.id === addPhotoDraftId);
    if (!draft) return;
    const full = imgUrl.startsWith("http") ? imgUrl : ("https://boris-ai.pro" + imgUrl);
    const newImages = [...(draft.images || []), full];
    try {
      await apiFetch("/api/avito/drafts/update_images", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, draft_id: addPhotoDraftId, images: newImages })
      });
      loadDrafts && loadDrafts();
    } catch (e) { alert("Не удалось добавить фото"); }
  };

  // перетаскивание фото для смены порядка (первое = главное на Avito)
  const [dragPhoto, setDragPhoto] = useState<{draftId: string, idx: number} | null>(null);
  const reorderDraftImages = async (draftId: string, fromIdx: number, toIdx: number) => {
    if (!currentAccount || fromIdx === toIdx) return;
    const draft = draftItems.find((x: any) => x.id === draftId);
    if (!draft) return;
    const imgs = [...(draft.images || [])];
    const [moved] = imgs.splice(fromIdx, 1);
    imgs.splice(toIdx, 0, moved);
    try {
      await apiFetch("/api/avito/drafts/update_images", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, draft_id: draftId, images: imgs })
      });
      loadDrafts && loadDrafts();
    } catch (e) { alert("Не удалось изменить порядок"); }
  };

  // удалить фото из черновика (по индексу) — обновляет images и сохраняет
  const removeDraftImage = async (draftId: string, imgIdx: number) => {
    if (!currentAccount) return;
    const draft = draftItems.find((x: any) => x.id === draftId);
    if (!draft) return;
    const newImages = (draft.images || []).filter((_: any, i: number) => i !== imgIdx);
    try {
      await apiFetch("/api/avito/drafts/update_images", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, draft_id: draftId, images: newImages })
      });
      loadDrafts && loadDrafts();
    } catch (e) { alert("Не удалось удалить фото"); }
  };

  const applyPhotosToDrafts = async () => {
    if (!currentAccount) return;
    const label = draftBatchFilter || (draftItems[0] && draftItems[0].batch_label) || "";
    if (!label) { alert("У черновиков нет партии — нечего привязать"); return; }
    if (!window.confirm(`Добавить баннеры и фото к партии «${label}»? Баннер станет первым фото, фото товара добавятся по кругу.`)) return;
    setDraftPhotoLoading(true);
    try {
      const r = await apiFetch("/api/avito/apply_banner_to_batch", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, batch_label: label, banner_count: bannerCount, photos_per_ad: photosPerAdInput, folder: bannerFolderInput.trim() })
      });
      const d = await r.json();
      if (d.status === "ok") {
        alert(`✅ Готово: баннеров ${d.banners_generated || 0}, обновлено черновиков ${d.updated || 0}`);
        loadDrafts && loadDrafts();
      } else {
        alert(`⚠️ ${d.message || "Не удалось"}`);
      }
    } catch (e) { alert("Ошибка связи с сервером"); }
    setDraftPhotoLoading(false);
  };

  const runBatchBanners = async () => {
    if (!pipelineBatch || !currentAccount) return;
    setBatchBannerLoading(true);
    setBannerResult("Генерирую баннеры (обычно 30-90 секунд)...");
    try {
      const r = await apiFetch("/api/avito/apply_banner_to_batch", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, batch_label: pipelineBatch.label, banner_count: bannerCount, photos_per_ad: photosPerAdInput, folder: bannerFolderInput.trim() })
      });
      const d = await r.json();
      if (d.status === "ok") {
        setBannerResult(`✅ Баннеров создано: ${d.banners_generated}, обновлено черновиков: ${d.updated}` + (d.notice ? ` (${d.notice})` : ""));
      } else {
        setBannerResult(`⚠️ ${d.message || "Не удалось"}`);
      }
    } catch (e) { setBannerResult("Ошибка связи с сервером"); }
    setBatchBannerLoading(false);
  };
  const runBatchValidate = async () => {
    if (!pipelineBatch || !currentAccount) return;
    setPipelineValidateLoading(true);
    try {
      const feedUrl = `https://boris-ai.pro/api/avito/feed_preview/${currentAccount}.xml?batch_label=${encodeURIComponent(pipelineBatch.label)}`;
      const r = await apiFetch("/api/parser/validate_feed", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ feed_url: feedUrl })
      });
      const d = await r.json();
      setPipelineValidateResult({ status: d.status || "error", raw_text: d.raw_text || d.message || "Нет данных" });
    } catch (e) { setPipelineValidateResult({ status: "error", raw_text: "Ошибка связи с сервером" }); }
    setPipelineValidateLoading(false);
  };
  // Общая точка публикации черновиков - используется везде (партия, одиночный черновик, "опубликовать
  // все"), чтобы одинаково обрабатывать ответ сервера: needs_confirmation (Борис заметил, что категория
  // не похожа на содержание - показываем список и просим подтвердить или пойти поправить) и
  // validation_failed (жёсткий отказ официального валидатора Avito) - раньше эти статусы вообще не
  // проверялись в двух из трёх мест публикации, кнопка просто молчала при отказе.
  const publishDraftIds = async (ids: string[], onOk: (d: any) => void) => {
    if (!currentAccount || ids.length === 0) return;
    const doRequest = async (confirmed: boolean) => {
      try {
        // если выбраны города/районы (метро) — раздаём каждому публикуемому черновику свой адрес
        let draftAddresses: {[id: string]: string} | undefined = undefined;
        if (bulkCities.length > 0 || bulkMetro.length > 0) {
          draftAddresses = {};
          for (const id of ids) { draftAddresses[id] = getRandomAddressForAd(); }
        }
        const r = await apiFetch("/api/avito/drafts/publish", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ account_id: currentAccount, draft_ids: ids, confirmed, city: (bulkCities.length===0 && bulkMetro.length===0 && selectedCity) ? selectedCity : "", draft_addresses: draftAddresses, date_begin: publishDate ? `${publishDate}T${publishTime || "09:00"}:00` : "" })
        });
        const d = await r.json();
        if (d.status === "ok") {
          onOk(d);
        } else if (d.status === "needs_confirmation") {
          const list = (d.mismatches || []).map((m: any) => `• «${m.title}» — сейчас «${m.category}», похоже на «${m.suggested_category}» (${m.reason})`).join("\n");
          showBorisConfirm(
            "Борис заметил несоответствие категории",
            `${d.message}\n\n${list}\n\nОпубликовать как есть или сначала поправить категории вручную?`,
            () => doRequest(true),
            "Опубликовать всё равно"
          );
        } else if (d.status === "validation_failed") {
          showBorisNotify("Публикация отменена", `${d.message}\n${(d.errors || []).slice(0, 10).join("\n")}`);
        } else {
          showBorisNotify("Ошибка", d.message || "Не удалось опубликовать");
        }
      } catch (e) {
        showBorisNotify("Ошибка", "Не удалось опубликовать");
      }
    };
    await doRequest(false);
  };

  const runBatchPublish = async () => {
    if (!pipelineBatch || !currentAccount) return;
    showBorisConfirm("Опубликовать партию", `Опубликовать партию «${pipelineBatch.label}» (${pipelineBatch.count} объявлений) в реальный фид Avito ПРЯМО СЕЙЧАС? Честно предупреждаем: отложенной публикации по дате в этой версии нет — публикация происходит немедленно.`, async () => {
      setPipelinePublishLoading(true);
      try {
        const dr = await apiFetch(`/api/avito/drafts?account_id=${currentAccount}`);
        const dd = await dr.json();
        const ids = (dd.drafts || []).filter((x: any) => x.batch_label === pipelineBatch.label).map((x: any) => x.id);
        if (ids.length === 0) { showBorisNotify("Нет черновиков", "Партия уже опубликована, удалена или не найдена."); setPipelinePublishLoading(false); return; }
        await publishDraftIds(ids, (d) => {
          showBorisNotify("Опубликовано", `Опубликовано объявлений: ${d.published}. Партия «${pipelineBatch.label}» ушла в реальный фид Avito.`);
          setPipelineBatch(null);
          setBannerResult(""); setPipelineValidateResult(null);
          setPipelineBatchLabelInput(""); setPipelineTopicInput("");
        });
      } catch (e) { showBorisNotify("Ошибка", "Не удалось опубликовать"); }
      setPipelinePublishLoading(false);
    }, "Опубликовать сейчас");
  };
  const loadParsedProducts = async () => {
    if (!currentAccount) return;
    try {
      const r = await apiFetch(`/api/parser/parsed_products?account_id=${currentAccount}`);
      const d = await r.json();
      if (d.status === "ok") { setParsedProducts(d.products || []); setParseSource(d.source_url || null); setParseSourceUrls(d.source_urls || []); }
    } catch (e) {}
  };
  const startParse = async () => {
    const urls = parseUrl.split("\n").map(u => u.trim()).filter(Boolean);
    if (urls.length === 0 || !currentAccount) return;
    setParseLoading(true);
    setParseProgress({done: 0, total: urls.length});
    try {
      const r = await apiFetch("/api/tasks/create", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, task_type: "parse_site", payload: { urls, account_id: currentAccount, limit: parseLimit } })
      });
      const d = await r.json();
      if (d.status === "ok" && d.task_id) {
        setParseUrl("");
        const poll = setInterval(async () => {
          try {
            const st = await apiFetch(`/api/tasks/status/${d.task_id}`).then(r => r.json());
            if (st.result) {
              const res = typeof st.result === "string" ? JSON.parse(st.result) : st.result;
              if (res.results) setParsedProducts(res.results);
              if (res.total) setParseProgress({done: res.done || 0, total: res.total});
            }
            if (st.status === "done" || st.status === "error") {
              clearInterval(poll);
              setParseLoading(false);
              setParseProgress(null);
              loadParsedProducts();
              if (st.status === "error") showBorisNotify("Ошибка", st.error_message || "Не удалось выгрузить карточки");
            }
          } catch (e) {}
        }, 3000);
      } else {
        setParseLoading(false);
        setParseProgress(null);
      }
    } catch (e) {
      showBorisNotify("Ошибка", "Не удалось поставить задачу. Попробуйте ещё раз.");
      setParseLoading(false);
      setParseProgress(null);
    }
  };
  const genProductBanner = async (product: any, idx: number) => {
    if (!currentAccount) return;
    try {
      const r = await apiFetch("/api/tasks/create", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, task_type: "product_banner", payload: {
          account_id: currentAccount, product_idx: idx, title: product.title, price: product.price,
          characteristics: product.characteristics, image: product.image
        }})
      });
      const d = await r.json();
      if (d.status === "ok") {
        showBorisNotify("Баннер генерируется", "Борис создаёт баннер по товару (~1 минута). Появится в карточке автоматически.");
        // автоматически проверяем результат каждые 5 сек, до 20 попыток (~100 сек)
        let attempts = 0;
        const poll = setInterval(async () => {
          attempts++;
          try {
            const rr = await apiFetch(`/api/parser/parsed_products?account_id=${currentAccount}`);
            const dd = await rr.json();
            const fresh = dd.products?.[idx];
            if (fresh?.banner_url) {
              setParsedProducts(prev => prev.map((pp, i) => i === idx ? {...pp, banner_url: fresh.banner_url} : pp));
              clearInterval(poll);
            }
          } catch (e) {}
          if (attempts >= 20) clearInterval(poll);
        }, 5000);
      }
    } catch (e) { showBorisNotify("Ошибка", "Не удалось запустить генерацию баннера."); }
  };
  const loadAccountTasks = async () => {
    if (!currentAccount) return;
    try {
      const r = await apiFetch(`/api/tasks/list?account_id=${currentAccount}`);
      const d = await r.json();
      if (d.status === "ok") setAccountTasks(d.tasks || []);
    } catch (e) { /* тихо */ }
  };
  useEffect(() => {
    loadAccountTasks();
    const iv_tasks = setInterval(loadAccountTasks, 5000);
    return () => clearInterval(iv_tasks);
  }, [currentAccount]);

  const loadDrafts = async () => {
    setDraftsLoading(true);
    try {
      const res = await apiFetch(`/api/avito/drafts?account_id=${currentAccount}`);
      const data = await res.json();
      setDraftItems(data.drafts || []);
    } catch (e) {}
    setDraftsLoading(false);
  };

  const publishDraft = async (draftId: string) => {
    await publishDraftIds([draftId], () => loadDrafts());
  };

  const publishAllDrafts = async (batchId?: string) => {
    // Всегда публикуем только ТЕКУЩЕ ОТОБРАЖАЕМЫЕ черновики (draftItems уже отфильтрованы по batchId,
    // если он передан), а не буквально всё подряд на аккаунте - иначе при нескольких партиях черновиков
    // одновременно кнопка "Опубликовать все" могла бы случайно захватить чужую партию.
    const scoped = batchId ? draftItems.filter((d: any) => d.batch_id === batchId) : draftItems;
    const ids = scoped.map((d: any) => d.id);
    if (ids.length === 0) { alert("Нет черновиков для публикации"); return; }
    if (!confirm(`Опубликовать ${ids.length} черновиков?`)) return;
    await publishDraftIds(ids, () => loadDrafts());
  };

  const deleteDraft = async (draftId: string) => {
    try {
      await apiFetch(`/api/avito/drafts/${draftId}?account_id=${currentAccount}`, { method: "DELETE" });
      loadDrafts();
    } catch (e) { alert("Ошибка связи с сервером"); }
  };

  const saveDraftEdit = async (draftId: string, fields: {title?: string, description?: string, price?: number}) => {
    try {
      await apiFetch(`/api/avito/drafts/${draftId}`, {
        method: "PUT", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, ...fields })
      });
      setEditingDraftId(null);
      loadDrafts();
    } catch (e) { alert("Ошибка связи с сервером"); }
  };

  const loadPlanItems = async () => {
    try {
      const d = await apiFetch(`${PLAN_API}/list?account_id=${currentAccount}`).then(r => r.json());
      setPlanItems(d.items || []);
    } catch (e) {}
  };

  const addPlanItem = async () => {
    if (!newPlanItemText.trim()) return;
    await apiFetch(`${PLAN_API}/create`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, text: newPlanItemText, source: "user", status: "planned" })
    });
    setNewPlanItemText("");
    loadPlanItems();
  };

  const updatePlanItemStatus = async (itemId: number, status: string) => {
    await apiFetch(`${PLAN_API}/${itemId}/update`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ status })
    });
    loadPlanItems();
  };

  const deletePlanItem = async (itemId: number) => {
    await apiFetch(`${PLAN_API}/${itemId}/delete`, { method: "POST" });
    loadPlanItems();
  };

  const executePlanItem = async (itemId: number) => {
    try {
      const res = await apiFetch(`${PLAN_API}/${itemId}/execute`, { method: "POST" });
      const data = await res.json();
      if (data.status === "ok") {
        if (data.decomposed) {
          alert(`🧩 ${data.message || `Задача разбита на ${data.subtasks_count} подзадач, Борис выполняет их в фоне.`}`);
        } else {
          const actionsList = (data.results || []).map((r: any) => r.error ? `❌ ${r.action}: ${r.error}` : `✅ ${r.action}`).join("\n");
          const skippedList = (data.skipped || []).length > 0 ? "\n\n⚠️ Пропущено:\n" + data.skipped.join("\n") : "";
          const costText = (data.cost && userRole === "owner") ? `\n\n💰 Себестоимость: ${data.cost.total_rub}₽ (GPT-разбор: ${data.cost.gpt_routing_rub}₽ + действия: ${data.cost.steps_estimate_rub}₽)` : "";
          alert(`Борис выполнил ${data.steps_done ?? 0} из шагов задачи:\n${actionsList}${skippedList}${costText}`);
        }
      } else if (data.status === "needs_clarification") {
        alert(`🤔 Борис пока не может это сделать сам: ${data.reason}`);
      } else {
        alert(`❌ Ошибка: ${data.message || "неизвестная ошибка"}`);
      }
    } catch (_execErr) {
      alert("Ошибка связи с сервером: " + (_execErr instanceof Error ? _execErr.message : String(_execErr)));
    }
    loadPlanItems();
  };

  // Пока пункт плана выполняется (item.status === "running" на сервере), кнопка должна
  // это показывать даже если исходный fetch выше уже давно всё отдал другой вкладке/вызову -
  // поэтому статус кнопки живёт не в локальном стейте, а в periodic-опросе списка с сервера.
  useEffect(() => {
    if (activeTab !== "plan") return;
    const iv_plan = setInterval(loadPlanItems, 4000);
    return () => clearInterval(iv_plan);
  }, [activeTab, currentAccount]);

  useEffect(() => {
    if (activeTab === "settings") { loadAutopilot(); loadAuditLog(); loadRepublishCandidates(); loadKpiSettings(); loadRepublishSettings(); }
    if (activeTab === "marketing") { loadWordstatHistory(); }
    if (activeTab === "plan") { loadPlanItems(); }
    if (activeTab === "billing") { loadBilling(); }
    if (activeTab === "sitebuild") { loadSbVideos(); }
  }, [activeTab, currentAccount]);

  const runAnalysis = async () => {
    if (!analysisQuery) return alert("Введите запрос");
    setAnalysisLoading(true);
    setAnalysisResults([]);
    setAnalysisProgress(null);
    const cities = analysisCities.split(",").map(c => c.trim());
    const startResp = await apiFetch("/api/parser/city_analysis_async", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({query: analysisQuery, cities})
    }).then(r => r.json());
    const taskId = startResp.task_id;
    if (!taskId) { setAnalysisLoading(false); return alert("Не удалось запустить анализ"); }
    const poll = setInterval(async () => {
      try {
        const st = await apiFetch(`/api/tasks/status/${taskId}`).then(r => r.json());
        if (st.result) {
          const res = typeof st.result === "string" ? JSON.parse(st.result) : st.result;
          if (res.results) setAnalysisResults(res.results);
          if (res.total) setAnalysisProgress({done: res.done || 0, total: res.total});
        }
        if (st.status === "done" || st.status === "error") {
          clearInterval(poll);
          setAnalysisLoading(false);
          setAnalysisProgress(null);
        }
      } catch (e) {}
    }, 3000);
  };

  const [sortBy, setSortBy] = useState("");
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");
  const [selectedItemIds, setSelectedItemIds] = useState<Set<string>>(new Set());

  const getFilteredItems = () => {
    let result;
    switch(listingFilter) {
      case null: result = []; break;
      case "active": result = items.filter((i: any) => i.status === "active"); break;
      case "inactive": result = items.filter((i: any) => i.status !== "active"); break;
      case "search": result = filtered; break;
      case "all": result = items; break;
      default: result = items;
    }
    if (sortBy) {
      result = [...result].sort((a: any, b: any) => {
        const av = a[sortBy] || 0;
        const bv = b[sortBy] || 0;
        return sortDir === "desc" ? bv - av : av - bv;
      });
    }
    return result;
  };

  const toggleItemSelected = (id: string) => {
    setSelectedItemIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const clearItemSelection = () => setSelectedItemIds(new Set());

  const [duplicateEditingItem, setDuplicateEditingItem] = useState<any>(null);
  const descTextareaRef = useRef<HTMLTextAreaElement>(null);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [forceIncludeVariants, setForceIncludeVariants] = useState(false);
  const [rewriteEngine, setRewriteEngine] = useState("gigachat");
  const AD_EMOJIS = ["📞","💬","✅","⭐","🔥","💰","🎁","📍","🚀","👍","❤️","🏆","⏰","📦","🛠","🔧","💡","🌟","✨","👉"];

  const wrapDescriptionSelection = (before: string, after: string, placeholder: string) => {
    const ta = descTextareaRef.current;
    if (!ta || !duplicateEditingItem) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const text = duplicateEditingItem.description || "";
    const selected = text.slice(start, end) || placeholder;
    const newText = text.slice(0, start) + before + selected + after + text.slice(end);
    setDuplicateEditingItem((prev: any) => ({...prev, description: newText}));
    setTimeout(() => { ta.focus(); ta.selectionStart = start + before.length; ta.selectionEnd = start + before.length + selected.length; }, 0);
  };

  const insertAtCursor = (snippet: string) => {
    const ta = descTextareaRef.current;
    if (!ta || !duplicateEditingItem) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const text = duplicateEditingItem.description || "";
    const newText = text.slice(0, start) + snippet + text.slice(end);
    setDuplicateEditingItem((prev: any) => ({...prev, description: newText}));
    setTimeout(() => { ta.focus(); const pos = start + snippet.length; ta.selectionStart = pos; ta.selectionEnd = pos; }, 0);
  };

  const toggleDraftImage = (url: string) => {
    setDuplicateEditingItem((prev: any) => {
      const images = prev.images || [];
      const has = images.includes(url);
      return {...prev, images: has ? images.filter((u: string) => u !== url) : [...images, url]};
    });
  };
  const [duplicateDrafts, setDuplicateDrafts] = useState<any[]>([]);
  const [showDuplicateDrafts, setShowDuplicateDrafts] = useState(false);
  const [rewriteLoading, setRewriteLoading] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);

  const loadDuplicateDrafts = () => {
    apiFetch(`/api/avito/duplicate_drafts?account_id=${currentAccount}`)
      .then(r => r.json())
      .then(d => { if (d.status === "ok") setDuplicateDrafts(d.drafts || []); });
  };

  const startDuplicate = async (item: any) => {
    try {
      const res = await apiFetch("/api/avito/duplicate_item", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: currentAccount, original_item_id: String(item.id),
          title: item.title, description: item.description || "", price: item.price || 0,
          images: item.images?.images || []
        })
      }).then(r => r.json());
      if (res.status === "ok") {
        setDuplicateEditingItem(res.draft);
        loadDuplicateDrafts();
      } else {
        alert("Ошибка создания дубля: " + (res.message || ""));
      }
    } catch (e) {
      alert("Ошибка связи с сервером");
    }
  };

  const rewriteDraftText = async () => {
    if (!duplicateEditingItem) return;
    setRewriteLoading(true);
    try {
      const res = await apiFetch("/api/avito/rewrite_text", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ title: duplicateEditingItem.title, description: duplicateEditingItem.description, include_variants: forceIncludeVariants, engine: rewriteEngine })
      }).then(r => r.json());
      if (res.status === "ok") {
        setDuplicateEditingItem((prev: any) => ({...prev, title: res.title, description: res.description}));
      } else {
        alert("Ошибка переписывания: " + (res.message || ""));
      }
    } catch (e) {
      alert("Ошибка связи с сервером");
    }
    setRewriteLoading(false);
  };

  const saveDuplicateDraft = async () => {
    if (!duplicateEditingItem) return;
    setSavingDraft(true);
    try {
      const res = await apiFetch("/api/avito/duplicate_draft/update", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          account_id: currentAccount, draft_id: duplicateEditingItem.draft_id,
          title: duplicateEditingItem.title, description: duplicateEditingItem.description,
          price: duplicateEditingItem.price, is_template: duplicateEditingItem.is_template
        })
      }).then(r => r.json());
      if (res.status === "ok") {
        alert("Черновик сохранён");
        setDuplicateEditingItem(null);
        loadDuplicateDrafts();
      } else {
        alert("Ошибка сохранения: " + (res.message || ""));
      }
    } catch (e) {
      alert("Ошибка связи с сервером");
    }
    setSavingDraft(false);
  };

  const deleteDuplicateDraft = async (draftId: string) => {
    if (!confirm("Удалить этот черновик-дубль?")) return;
    await apiFetch("/api/avito/duplicate_draft/delete", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, draft_id: draftId })
    });
    loadDuplicateDrafts();
  };

  const bulkAction = (action: "template" | "archive" | "delete") => {
    const count = selectedItemIds.size;
    if (count === 0) return;
    const labels: any = {template: "сохранить как шаблон", archive: "архивировать", delete: "удалить"};
    if (!confirm(`Точно ${labels[action]} ${count} объявлени${count === 1 ? "е" : "й"}?`)) return;

    if (action === "template") {
      const selected = items.filter((it: any) => selectedItemIds.has(String(it.id)));
      const newTemplates = selected.map((it: any) => ({
        id: Date.now() + Math.random(),
        name: it.title || "Шаблон из объявления",
        titleTemplate: it.title || "{название}",
        description: it.description || "{описание}",
        priceType: "original",
        priceModifier: 0,
        cities: [it.address || "Москва"],
        category: (it.category && it.category.name) || "Предложение услуг",
        sourceItemId: it.id
      }));
      setTemplates((prev: any[]) => [...prev, ...newTemplates]);
      alert(`Сохранено как шаблон: ${newTemplates.length} шт.`);
      clearItemSelection();
      return;
    }

    alert(`Массовое действие "${labels[action]}" пока недоступно — у Бориса ещё нет прав архивировать/удалять реальные объявления на Avito напрямую (это отдельная функция, не сделана).`);
    clearItemSelection();
  };

  const filtered = items.filter((item: any) =>
    item.title.toLowerCase().includes(search.toLowerCase())
  );

  const inputStyle: any = {width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px", boxSizing:"border-box"};
  const days = ["пн","вт","ср","чт","пт","сб","вс"];

  if (view === "accounts") return (
    <div style={{background:"#F6F7FB", minHeight:"100vh", fontFamily:"var(--font-inter), sans-serif", color:"#1D2939"}}>
      <div style={{background:"#F6F7FB", borderBottom:"1px solid #EEF2FA", padding:"16px 32px", display:"flex", alignItems:"center", justifyContent:"space-between"}}>
        <div style={{display:"flex", alignItems:"center", gap:"12px"}}>
          <Mascot size={26} interactive={false} />
          <span style={{fontSize:"23px", fontWeight:"bold"}}>БОРИС{userRole === "owner" ? " · Админ-панель" : ""}</span>
        </div>
        {accounts.length > 1 && userRole !== "owner" && (
          <button onClick={() => router.push("/agency")} className="boris-btn-hover" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", marginRight:"10px", fontWeight:600}}>🏢 Мои аккаунты</button>
        )}
        <button onClick={() => router.push("/dashboard/home")} className="boris-btn-hover" style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", marginRight:"10px", fontWeight:600}}>← В кабинет</button>
        <button onClick={() => { localStorage.removeItem("boris_token"); localStorage.removeItem("boris_user_email"); localStorage.removeItem("boris_user_role"); localStorage.removeItem("boris_currentAccount"); router.push("/login"); }} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 16px", cursor:"pointer"}}>Выйти</button>
      </div>

      <div style={{padding:"32px"}}>
        {crmOpen && userRole === "owner" && (
          <div style={{marginBottom:"24px"}}>
            <div style={{display:"flex", alignItems:"center", gap:"14px", marginBottom:"18px", flexWrap:"wrap"}}>
              <button className="boris-btn-hover" onClick={() => { setCrmOpen(false); setEconOpen(false); }} style={{background:"#FFFFFF", border:"1.5px solid #E3E7F0", color:"#667085", borderRadius:"10px", padding:"10px 16px", fontSize:"15px", cursor:"pointer"}}>← Назад</button>
              <span style={{fontSize:"22px", fontWeight:800, color:"#1D2939"}}>CRM Владельца</span>
            </div>
            <div style={{display:"flex", gap:"12px", flexWrap:"wrap"}}>
              <button className="boris-btn-hover" onClick={async () => { const v = !econOpen; setEconOpen(v); if (v && !econData) { try { const r = await apiFetch("/api/economics/overview?days=30"); const d = await r.json(); if (d.status === "ok") setEconData(d); } catch {} } }} style={{background: econOpen ? "#2F6FED" : "#FFFFFF", border:"1.5px solid #2F6FED", color: econOpen ? "#FFFFFF" : "#2F6FED", borderRadius:"10px", padding:"12px 22px", fontSize:"15px", fontWeight:700, cursor:"pointer"}}>💰 Экономика</button>
              <button className="boris-btn-hover" onClick={() => alert("Раздел в разработке")} style={{background:"#FFFFFF", border:"1.5px solid #E3E7F0", color:"#667085", borderRadius:"10px", padding:"12px 22px", fontSize:"15px", fontWeight:700, cursor:"pointer"}}>📋 Лиды и сделки — скоро</button>
            </div>
          </div>
        )}
        {userRole === "owner" && (
          <div style={{display: crmOpen ? "block" : "grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"16px", marginBottom:"32px"}}>
            <div onClick={() => setCrmOpen(true)} className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", cursor:"pointer", display: crmOpen ? "none" : "block", position:"relative", overflow:"hidden"}}>
              <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", opacity:0.08}} /><div style={{display:"flex", alignItems:"center", gap:"12px", marginBottom:"10px"}}><span className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", display:"inline-flex", alignItems:"center", justifyContent:"center", fontSize:"20px", flexShrink:0}}>📇</span><span style={{fontSize:"18px", fontWeight:700, color:"#1D2939"}}>CRM Владельца</span></div>
              <div style={{color:"#667085", fontSize:"15px", marginBottom:"14px"}}>Лиды и сделки агентства — скоро</div>
            </div>
            <div onClick={() => alert("Раздел в разработке")} className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", cursor:"pointer", display: crmOpen ? "none" : "block", position:"relative", overflow:"hidden"}}>
              <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#FDB022,#F79009)", opacity:0.08}} /><div style={{display:"flex", alignItems:"center", gap:"12px", marginBottom:"10px"}}><span className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#FDB022,#F79009)", display:"inline-flex", alignItems:"center", justifyContent:"center", fontSize:"20px", flexShrink:0}}>🛠</span><span style={{fontSize:"18px", fontWeight:700, color:"#1D2939"}}>Технический онбординг</span></div>
              <div style={{color:"#667085", fontSize:"15px"}}>Чек-лист подключения нового клиента — скоро</div>
            </div>
            <div onClick={() => setShowAccountsList(v => !v)} className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", cursor:"pointer", display: crmOpen ? "none" : "block", position:"relative", overflow:"hidden"}}>
              <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", opacity:0.08}} /><div style={{display:"flex", alignItems:"center", gap:"12px", marginBottom:"10px"}}><span className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", display:"inline-flex", alignItems:"center", justifyContent:"center", fontSize:"20px", flexShrink:0}}>📋</span><span style={{fontSize:"18px", fontWeight:700, color:"#1D2939"}}>Аккаунты Авито</span></div>
              <div style={{color:"#667085", fontSize:"15px"}}>{showAccountsList ? "Скрыть список" : "Показать список клиентских аккаунтов"}</div>
            </div>
          {econOpen && econData && (() => {
            const it = econData["итого"] || {};
            const box = (grad: string, val: string, cap: string) => (
              <div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"20px", position:"relative", overflow:"hidden"}}>
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div style={{fontSize:"26px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em"}}>{val}</div>
                <div style={{fontSize:"14px", color:"#667085", marginTop:"2px"}}>{cap}</div>
              </div>
            );
            const rub = (v: any) => (Number(v)||0).toLocaleString("ru") + " \u20bd";
            return (
              <div style={{marginBottom:"32px"}}>
                <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(220px,1fr))", gap:"14px", marginBottom:"18px"}}>
                  {box("linear-gradient(135deg,#32D583,#12805C)", rub(it["получено"]), "получено от клиентов")}
                  {box("linear-gradient(135deg,#FF6B6B,#F04438)", rub(it["потрачено"]), "потрачено на генерацию")}
                  {box("linear-gradient(135deg,#4C8DFF,#2F6FED)", rub(it["маржа"]), "маржа за 30 дней")}
                  {box("linear-gradient(135deg,#98A2B3,#667085)", rub(it["расход_без_аккаунта"]), "расход вне клиентов")}
                </div>
                <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"20px", marginBottom:"14px"}}>
                  <div style={{fontSize:"16px", fontWeight:700, marginBottom:"12px", color:"#1D2939"}}>На что уходят деньги</div>
                  {(econData["по_операциям"]||[]).map((o: any, i: number) => (
                    <div key={i} style={{display:"flex", justifyContent:"space-between", padding:"7px 0", borderTop: i ? "1px solid #EAECF0" : "none", fontSize:"14px"}}>
                      <span style={{color:"#667085"}}>{o["операция"]}</span>
                      <span><b>{rub(o["руб"])}</b> <span style={{color:"#98A2B3"}}>· {o["раз"]} раз</span></span>
                    </div>
                  ))}
                </div>
                <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"20px"}}>
                  <div style={{fontSize:"16px", fontWeight:700, marginBottom:"12px", color:"#1D2939"}}>По клиентам</div>
                  <table style={{width:"100%", borderCollapse:"collapse", fontSize:"14px"}}>
                    <thead><tr style={{textAlign:"left", color:"#667085", fontSize:"12px"}}><th style={{padding:"6px"}}>Клиент</th><th>Заплатил</th><th>Потратил</th><th>Маржа</th></tr></thead>
                    <tbody>{(econData["клиенты"]||[]).map((c: any) => (
                      <tr key={c.account_id} style={{borderTop:"1px solid #EAECF0"}}>
                        <td style={{padding:"9px 6px"}}>{c["название"] || c.account_id}<div style={{fontSize:"11px", color:"#98A2B3"}}>{c.account_id}</div></td>
                        <td><b style={{color:"#12805C"}}>{rub(c["заплатил"])}</b></td>
                        <td style={{color:"#667085"}}>{rub(c["потратил"])}</td>
                        <td><b style={{color: c["маржа"] >= 0 ? "#12805C" : "#B42318"}}>{rub(c["маржа"])}</b></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
            );
          })()}
          </div>
        )}
        {(userRole !== "owner" || showAccountsList) && (
        <>
        <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"24px"}}>
          <h2 style={{margin:0, fontSize:"23px"}}>Аккаунты Авито</h2>
          <button onClick={() => { setShowAddForm(true); setStep(0); }} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 20px", fontWeight:"bold", cursor:"pointer"}}>+ Добавить аккаунт</button>
        </div>

        {showAddForm && (
          <div style={{background:"#F6F7FB", borderRadius:"12px", padding:"24px", border:"1px solid #E3E7F0", marginBottom:"24px"}}>
            <div style={{display:"flex", gap:"8px", marginBottom:"24px"}}>
              <div style={{flex:1, height:"4px", borderRadius:"2px", background: step >= 0 ? "#2F6FED" : "#E3E7F0"}}></div>
              <div style={{flex:1, height:"4px", borderRadius:"2px", background: step >= 1 ? "#2F6FED" : "#E3E7F0"}}></div>
              <div style={{flex:1, height:"4px", borderRadius:"2px", background: step >= 2 ? "#2F6FED" : "#E3E7F0"}}></div>
            </div>

            {step === 0 && (
              <>
                <h3 style={{margin:"0 0 4px", fontSize:"15px"}}>С чего начнём?</h3>
                <p style={{color:"#667085", fontSize:"15px", marginBottom:"20px"}}>Выберите задачу — Борис покажет только нужные шаги</p>
                <div style={{display:"grid", gap:"12px"}}>
                  <button className="boris-btn-hover" onClick={() => { setWizardGoal("site"); setStep(1); }} style={{textAlign:"left", background: wizardGoal==="site" ? "#E7EFFE" : "#FFFFFF", border: wizardGoal==="site" ? "2px solid #2F6FED" : "1px solid #E3E7F0", borderRadius:"10px", padding:"16px 18px", cursor:"pointer", width:"100%"}}>
                    <div style={{fontWeight:"bold", fontSize:"15px", color:"#14161A", marginBottom:"4px"}}>Перенести товары с моего сайта</div>
                    <div style={{fontSize:"13px", color:"#667085"}}>Борис выгрузит каталог с вашего сайта</div>
                  </button>
                  <button className="boris-btn-hover" onClick={() => { setWizardGoal("avito"); setStep(1); }} style={{textAlign:"left", background: wizardGoal==="avito" ? "#E7EFFE" : "#FFFFFF", border: wizardGoal==="avito" ? "2px solid #2F6FED" : "1px solid #E3E7F0", borderRadius:"10px", padding:"16px 18px", cursor:"pointer", width:"100%"}}>
                    <div style={{fontWeight:"bold", fontSize:"15px", color:"#14161A", marginBottom:"4px"}}>Подтянуть объявления с Avito</div>
                    <div style={{fontSize:"13px", color:"#667085"}}>Импортировать то, что уже размещено</div>
                  </button>
                  <button className="boris-btn-hover" onClick={() => { setWizardGoal("scratch"); setStep(1); }} style={{textAlign:"left", background: wizardGoal==="scratch" ? "#E7EFFE" : "#FFFFFF", border: wizardGoal==="scratch" ? "2px solid #2F6FED" : "1px solid #E3E7F0", borderRadius:"10px", padding:"16px 18px", cursor:"pointer", width:"100%"}}>
                    <div style={{fontWeight:"bold", fontSize:"15px", color:"#14161A", marginBottom:"4px"}}>Создать объявления с нуля</div>
                    <div style={{fontSize:"13px", color:"#667085"}}>Составим и оформим новые объявления</div>
                  </button>
                  <div style={{background:"#FFFFFF", border: wizardGoal==="other" ? "2px solid #2F6FED" : "1px solid #E3E7F0", borderRadius:"10px", padding:"16px 18px"}}>
                    <button className="boris-btn-hover" onClick={() => setWizardGoal("other")} style={{textAlign:"left", background:"transparent", border:"none", cursor:"pointer", width:"100%", padding:0}}>
                      <div style={{fontWeight:"bold", fontSize:"15px", color:"#14161A", marginBottom:"4px"}}>Другое</div>
                      <div style={{fontSize:"13px", color:"#667085"}}>Опишите свою задачу своими словами</div>
                    </button>
                    {wizardGoal==="other" && (
                      <input value={wizardGoalText} onChange={e => setWizardGoalText(e.target.value)} placeholder="Например: веду 3 магазина, хочу объединить" style={{...inputStyle, marginTop:"12px"}} />
                    )}
                  </div>
                </div>
                <div style={{display:"flex", gap:"12px", marginTop:"20px"}}>
                  <button className="boris-btn-hover" onClick={() => { setShowAddForm(false); setWizardGoal(""); }} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"10px 24px", cursor:"pointer"}}>Отмена</button>
                  <button className="boris-btn-hover" disabled={!wizardGoal} onClick={() => setStep(1)} style={{background: wizardGoal ? "#2F6FED" : "#E3E7F0", color: wizardGoal ? "#F6F7FB" : "#9AA3B2", border:"none", borderRadius:"10px", padding:"10px 24px", fontWeight:"bold", cursor: wizardGoal ? "pointer" : "not-allowed"}}>Далее →</button>
                </div>
              </>
            )}

            {step === 1 && (
              <>
                <h3 style={{margin:"0 0 4px", fontSize:"15px"}}>Шаг 1: Данные аккаунта Авито</h3>
                <p style={{color:"#667085", fontSize:"15px", marginBottom:"20px"}}>Логин, пароль и API ключи аккаунта</p>
                <div style={{background:"#EAF3FF", border:"1px solid #C7DDFF", borderRadius:"12px", padding:"16px 18px", marginBottom:"20px"}}>
                  <div style={{fontWeight:700, color:"#14161A", fontSize:"14px", marginBottom:"6px"}}>{"\u041a\u0430\u043a \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c Client ID \u0438 Client Secret"}</div>
                  <div style={{color:"#667085", fontSize:"12.5px", marginBottom:"12px"}}>{"\u041d\u0443\u0436\u0435\u043d \u0442\u0430\u0440\u0438\u0444 Avito \u00ab\u0420\u0430\u0441\u0448\u0438\u0440\u0435\u043d\u043d\u044b\u0439\u00bb \u0438\u043b\u0438 \u00ab\u041c\u0430\u043a\u0441\u0438\u043c\u0430\u043b\u044c\u043d\u044b\u0439\u00bb \u2014 \u0431\u0435\u0437 \u043d\u0435\u0433\u043e API \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d."}</div>
                  <ol style={{margin:"0 0 12px", paddingLeft:"20px", color:"#3A414D", fontSize:"13.5px", lineHeight:1.7}}>
                    <li>{"\u0412 \u043b\u0438\u0447\u043d\u043e\u043c \u043a\u0430\u0431\u0438\u043d\u0435\u0442\u0435 Avito \u043e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u00ab\u041f\u0440\u043e\u0444\u0438\u043b\u044c \u0438 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438\u00bb \u2192 \u00ab\u0418\u043d\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u0438 \u0438 API\u00bb (\u043f\u0443\u043d\u043a\u0442 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u043d\u0430 \u0430\u043d\u0433\u043b\u0438\u0439\u0441\u043a\u043e\u043c \u2014 Integrations / API)"}</li>
                    <li>{"\u0421\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u0438\u043d\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u044e \u0434\u043b\u044f \u0430\u0432\u0442\u043e\u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u0439"}</li>
                    <li>{"\u0412\u043d\u0438\u0437\u0443 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u044b \u0441\u043a\u043e\u043f\u0438\u0440\u0443\u0439\u0442\u0435 Client ID \u0438 Client Secret (\u043d\u0430 \u0430\u043d\u0433\u043b\u0438\u0439\u0441\u043a\u043e\u043c) \u0438 \u0432\u0441\u0442\u0430\u0432\u044c\u0442\u0435 \u0432 \u043f\u043e\u043b\u044f \u043d\u0438\u0436\u0435"}</li>
                  </ol>
                  <div style={{background:"#F6F7FB", border:"1px dashed #B9C6DD", borderRadius:"8px", padding:"20px", textAlign:"center", color:"#9AA3B2", fontSize:"12.5px"}}>{"\u0421\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u044b \u0448\u0430\u0433\u043e\u0432 \u0431\u0443\u0434\u0443\u0442 \u0437\u0434\u0435\u0441\u044c"}</div>
                  <a href="https://www.avito.ru/professionals/api" target="_blank" rel="noopener" style={{display:"inline-block", marginTop:"12px", color:"#2F6FED", fontSize:"13.5px", fontWeight:600, textDecoration:"none"}}>{"\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 API \u0432 Avito \u2192"}</a>
                </div>
                <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"16px"}}>
                  {[
                    {label:"Название", key:"name", placeholder:"Название компании"},
                    {label:"Логин (email)", key:"login", placeholder:"email@yandex.ru"},
                    {label:"Пароль", key:"password", placeholder:"••••••••"},
                    {label:"Комментарий", key:"comment", placeholder:"Заметки об аккаунте"},
                    {label:"Client ID (Авито API)", key:"client_id", placeholder:"API Client ID"},
                    {label:"Client Secret (Авито API)", key:"client_secret", placeholder:"API Client Secret"},
                  ].map(field => (
                    <div key={field.key}>
                      <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>{field.label}</label>
                      <input value={(newAccount as any)[field.key]} onChange={e => setNewAccount({...newAccount, [field.key]: e.target.value})}
                        placeholder={field.placeholder} type={field.key === "password" ? "password" : "text"} style={inputStyle} />
                    </div>
                  ))}
                </div>
                <div style={{display:"flex", gap:"12px", marginTop:"20px"}}>
                  <button className="boris-btn-hover" onClick={() => { if (!newAccount.name || !newAccount.login) return alert("Заполните название и логин"); setStep(2); }}
                    style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"8px", padding:"10px 24px", fontWeight:"bold", cursor:"pointer"}}>Далее →</button>
                  <button className="boris-btn-hover" onClick={() => { setShowAddForm(false); setStep(1); }} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"10px 24px", cursor:"pointer"}}>Отмена</button>
                </div>
              </>
            )}

            {step === 2 && (
              <>
                <h3 style={{margin:"0 0 4px", fontSize:"15px"}}>🏢 Шаг 2: Расскажи о компании</h3>
                <p style={{color:"#667085", fontSize:"15px", marginBottom:"20px"}}>БОРИС изучит бизнес и будет использовать эту информацию для создания объявлений</p>
                <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"16px"}}>
                  <div style={{gridColumn:"1/-1"}}>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Сайт компании</label>
                    <input value={newAccount.companyWebsite} onChange={e => setNewAccount({...newAccount, companyWebsite: e.target.value})} placeholder="https://example.com" style={inputStyle} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Сфера деятельности</label>
                    <input value={newAccount.companyNiche} onChange={e => setNewAccount({...newAccount, companyNiche: e.target.value})} placeholder="Строительные материалы" style={inputStyle} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Тон общения</label>
                    <select className="b-select" value={newAccount.companyTone} onChange={e => setNewAccount({...newAccount, companyTone: e.target.value})} style={inputStyle}>
                      <option>Дружелюбный</option><option>Деловой</option><option>Экспертный</option><option>Простой</option>
                    </select>
                  </div>
                  <div style={{gridColumn:"1/-1"}}>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Описание компании</label>
                    <textarea value={newAccount.companyDescription} onChange={e => setNewAccount({...newAccount, companyDescription: e.target.value})}
                      placeholder="Чем занимается компания" rows={3} style={{...inputStyle, resize:"vertical"}} />
                  </div>
                  <div style={{gridColumn:"1/-1"}}>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Преимущества компании</label>
                    <textarea value={newAccount.companyAdvantages} onChange={e => setNewAccount({...newAccount, companyAdvantages: e.target.value})}
                      placeholder="Доставка за час, гарантия 5 лет" rows={3} style={{...inputStyle, resize:"vertical"}} />
                  </div>
                </div>
                <div style={{display:"flex", gap:"12px", marginTop:"20px"}}>
                  <button className="boris-btn-hover" onClick={() => setStep(1)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"10px 24px", cursor:"pointer"}}>← Назад</button>
                  <button className="boris-btn-hover" onClick={addAccount} disabled={addingAccount} style={{background: addingAccount ? "#E3E7F0" : "#2F6FED", color: addingAccount ? "#667085" : "#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 24px", fontWeight:"bold", cursor: addingAccount ? "wait" : "pointer"}}>{addingAccount ? "⏳ Создаю..." : "✅ Создать аккаунт"}</button>
                </div>
              </>
            )}
          </div>
        )}

        {accounts.length > 0 && (
          <div style={{color:"#667085", fontSize:"15px", marginBottom:"16px"}}>Выберите аккаунт, чтобы продолжить работу</div>
        )}

        {accounts.length === 0 && !showAddForm && (
          <div style={{background:"#FFFFFF", borderRadius:"12px", padding:"40px 24px", border:"1px solid #EEF2FA", textAlign:"center"}}>
            <div style={{fontSize:"40px", marginBottom:"12px"}}>🔌</div>
            <div style={{fontSize:"18px", fontWeight:700, color:"#1D2939", marginBottom:"8px"}}>Пока нет ни одного аккаунта Авито</div>
            <div style={{color:"#667085", fontSize:"15px", marginBottom:"20px"}}>Подключите аккаунт — Борис начнёт работать с вашими объявлениями</div>
            <button className="boris-btn-hover" onClick={() => { setShowAddForm(true); setStep(0); }} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"12px 28px", fontWeight:"bold", fontSize:"15px", cursor:"pointer"}}>Подключить аккаунт</button>
            <div style={{marginTop:"16px"}}>
              <button onClick={() => router.push("/dashboard/home")} style={{background:"none", border:"none", color:"#667085", fontSize:"15px", cursor:"pointer", textDecoration:"underline"}}>Вернуться в кабинет</button>
            </div>
          </div>
        )}

        <div style={{display:"grid", gap:"16px"}}>
          {accounts.map(acc => (
            <div key={acc.id} style={{background:"#F6F7FB", borderRadius:"12px", padding:"24px", border:"1px solid #EEF2FA", display:"flex", alignItems:"center", justifyContent:"space-between"}}>
              <div>
                <div style={{fontSize:"23px", fontWeight:"bold", marginBottom:"4px"}}>{acc.name}</div>
                <div style={{color:"#667085", fontSize:"15px", marginBottom:"4px"}}>{acc.login}</div>
                <div style={{color:"#8A93A6", fontSize:"15px"}}>{acc.comment}</div>
                {(acc as any).company?.niche && <div style={{color:"#2F6FED", fontSize:"15px", marginTop:"6px"}}>🏢 {(acc as any).company.niche}</div>}
              </div>
              <div style={{display:"flex", alignItems:"center", gap:"12px"}}>
                <span style={{background:"#1E4FBF", color:"#2F6FED", padding:"4px 12px", borderRadius:"20px", fontSize:"15px"}}>● Активен</span>
                <button className="boris-btn-hover" onClick={() => openAccount(acc)} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 20px", fontWeight:"bold", cursor:"pointer"}}>Открыть →</button>
              </div>
            </div>
          ))}
        </div>
        </>
        )}
      </div>
    </div>
  );

  return (
    <div style={{background:"#F6F7FB", minHeight:"100vh", fontFamily:"var(--font-inter), sans-serif", color:"#1D2939", display:"flex"}}>
            <style jsx global>{`
        @keyframes guidePulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(74,222,128,0.5); }
          50% { box-shadow: 0 0 0 6px rgba(74,222,128,0); }
        }
        .boris-card-hover { transition: box-shadow 0.2s ease, transform 0.2s ease; }
        .boris-card-hover:hover { box-shadow: 0 4px 10px rgba(16,24,40,0.08), 0 12px 24px rgba(16,24,40,0.10) !important; transform: translateY(-3px); }
        .boris-btn-hover:hover { box-shadow: 0 2px 8px rgba(16,24,40,0.10) !important; transform: translateY(-1px); }
        .boris-robokassa-btn { transition: all 0.2s ease; }
        .boris-robokassa-btn:hover { background: rgba(47,111,237,0.08) !important; box-shadow: 0 6px 16px rgba(47,111,237,0.25) !important; transform: translateY(-2px); }
        .boris-nav-hover:hover { background: #F5F8FF !important; transform: translateX(2px); box-shadow: 0 2px 6px rgba(16,24,40,0.06); }
        .boris-side-btn { transition: all 0.2s ease; }
        .boris-side-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 14px rgba(16,24,40,0.12) !important; border-color: #2F6FED !important; }

        .b-card { background:#FFFFFF; border:1px solid #E3E7F0; border-radius:16px; padding:24px; position:relative; overflow:hidden; transition: all 0.2s ease; }
        .b-card:hover { transform: translateY(-3px); box-shadow: 0 12px 24px rgba(16,24,40,0.10); }
        .b-panel { background:#FFFFFF; border:1px solid #E3E7F0; border-radius:16px; padding:24px; }
        .b-title { font-size:20px; font-weight:800; color:#1D2939; margin:0 0 6px; letter-spacing:-0.01em; }
        .b-sub { font-size:14px; color:#667085; margin:0 0 20px; }
        .b-label { font-size:13px; font-weight:600; color:#344054; display:block; margin-bottom:6px; }
        .b-input { width:100%; box-sizing:border-box; background:#FFFFFF; border:1px solid #E3E7F0; border-radius:10px; padding:11px 14px; font-size:14px; color:#1D2939; outline:none; transition: all 0.15s ease; font-family:inherit; }
        .b-input:focus { border-color:#2F6FED; box-shadow:0 0 0 3px rgba(47,111,237,0.10); }
        .b-btn { display:inline-flex; align-items:center; justify-content:center; gap:6px; border-radius:10px; padding:11px 20px; font-size:14px; font-weight:700; cursor:pointer; text-decoration:none; box-sizing:border-box; transition: all 0.2s ease; }
        .b-btn-primary { background:#2F6FED; color:#FFFFFF; border:1px solid #2F6FED; }
        .b-btn-primary:hover { transform:translateY(-2px); box-shadow:0 6px 16px rgba(47,111,237,0.30); }
        /* --- нормализация вкладки Объявления --- */
        .b-btn { height:40px; padding:0 16px !important; font-size:13px !important; white-space:nowrap; line-height:1; }
        .b-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(16,24,40,0.10); }
        .b-btn:active { transform: translateY(0); box-shadow:none; }
        .b-nav { display:flex; align-items:center; gap:10px; width:100%; text-align:left; border-radius:10px; padding:8px 10px; cursor:pointer; font-size:14px; margin-bottom:3px; transition: all .15s ease; }
        .b-nav:hover { background:#F6F7FB !important; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(16,24,40,0.08); }
        .b-nav:active { transform: translateY(0); box-shadow:none; }
        .b-nav .b-icon-sm { transition: transform .15s ease, box-shadow .15s ease; }
        .b-nav:hover .b-icon-sm { transform: scale(1.06); box-shadow: 0 3px 10px rgba(16,24,40,0.18); }
        @keyframes statIn { from { opacity:0; transform: translateY(14px); } to { opacity:1; transform: translateY(0); } }
        @keyframes iconPulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.08); } }
        @keyframes shine { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
        .b-stat { display:flex; align-items:center; gap:18px; padding:20px 22px; opacity:0; animation: statIn .55s cubic-bezier(.22,1,.36,1) forwards; flex:1; min-height:118px; }
        .b-stat:hover .b-stat-ico { animation: iconPulse 1.2s ease-in-out infinite; }
        .b-stat-ico { width:56px; height:56px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:26px; flex-shrink:0; box-shadow: 0 4px 14px rgba(16,24,40,0.14); }
        .b-stat-num { font-size:34px; font-weight:800; line-height:1; letter-spacing:-0.02em; background:linear-gradient(90deg,#1D2939,#475467,#1D2939); background-size:200% auto; -webkit-background-clip:text; background-clip:text; color:transparent; animation: shine 4s linear infinite; }
        /* --- фильтры объявлений --- */
        @keyframes chipIn { from { opacity:0; transform: translateY(-6px) scale(.96); } to { opacity:1; transform:none; } }
        .b-filters { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:20px; }
        .b-filters { gap:12px !important; }
        .b-filters .b-btn {
          height:auto !important; padding:14px 18px !important; font-size:14px !important; font-weight:600 !important;
          background:#FFFFFF; border:1px solid #E3E7F0; color:#475467; border-radius:14px;
          position:relative; overflow:hidden; box-shadow:0 1px 3px rgba(16,24,40,0.04);
        }
        .b-filters .b-btn::after {
          content:""; position:absolute; inset:0; border-radius:14px; opacity:0;
          background:linear-gradient(120deg, transparent 30%, rgba(255,255,255,.55) 50%, transparent 70%);
          background-size:220% 100%; transition:opacity .2s;
        }
        .b-filters .b-btn:hover { transform:translateY(-3px) !important; box-shadow:0 10px 22px rgba(16,24,40,0.13) !important; border-color:#C9D6F5; }
        .b-filters .b-btn:hover::after { opacity:1; animation: shine 1.1s linear; }
        .b-filters .b-btn:active { transform:translateY(-1px) !important; }
        .b-fcard { align-items:center !important; text-align:center; transition: all .2s cubic-bezier(.22,1,.36,1); }
        .b-fcard:hover { transform: translateY(-4px) !important; box-shadow: 0 14px 28px rgba(16,24,40,0.14) !important; }
        .b-fcard:hover .b-stat-ico { transform: scale(1.1) rotate(-4deg); }
        .b-fcard .b-stat-ico { transition: transform .25s cubic-bezier(.34,1.56,.64,1); }
        .b-fcard:active { transform: translateY(-1px) !important; }
        .b-filters .b-btn { animation: chipIn .35s cubic-bezier(.22,1,.36,1) backwards; }
        .b-filters .b-btn:nth-child(1) { animation-delay:.02s } .b-filters .b-btn:nth-child(2) { animation-delay:.05s }
        .b-filters .b-btn:nth-child(3) { animation-delay:.08s } .b-filters .b-btn:nth-child(4) { animation-delay:.11s }
        .b-filters .b-btn:nth-child(5) { animation-delay:.14s } .b-filters .b-btn:nth-child(6) { animation-delay:.17s }
        .b-filters .b-btn:nth-child(7) { animation-delay:.20s } .b-filters .b-btn:nth-child(8) { animation-delay:.23s }
        .b-filters .b-btn:nth-child(9) { animation-delay:.26s } .b-filters .b-btn:nth-child(n+10) { animation-delay:.29s }
        .b-btn-primary:hover { box-shadow: 0 8px 20px rgba(47,111,237,0.35) !important; }
        .b-empty { display:flex; flex-direction:column; align-items:center; gap:14px; padding:48px 20px; animation: statIn .5s cubic-bezier(.22,1,.36,1) both; }
        .b-empty .b-stat-ico { animation: iconPulse 2.4s ease-in-out infinite; }
        .b-input { height:40px; }
        textarea.b-input { height:auto; }
        .b-meta { font-size:13px; color:#667085; white-space:nowrap; }
        .b-card-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; padding-top:14px; border-top:1px solid #EEF2FA; }
        .b-card-actions .b-btn { flex:0 0 auto; margin:0 !important; }
        .b-price-row { display:flex; align-items:center; gap:8px; margin:10px 0; }
        .b-more { width:100%; background:#F6F7FB; border:1px solid #E3E7F0; border-radius:10px; padding:10px 12px; font-size:13px; color:#667085; cursor:pointer; transition:all .2s ease; }
        .b-more:hover { background:#EEF2FA; }
        .b-sep { height:1px; background:#EEF2FA; border:0; margin:16px 0; }
        .b-inline { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
        .b-photo-bar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; row-gap:14px; }
        .b-photo-bar > .b-meta { margin:0; }
        .b-photo-bar .b-brk { flex-basis:100%; height:0; }
        .b-btn-ghost { background:transparent; color:#2F6FED; border:2px solid #2F6FED; }
        .b-btn-ghost:hover { background:rgba(47,111,237,0.08); transform:translateY(-2px); box-shadow:0 6px 16px rgba(47,111,237,0.25); }
        .b-btn-soft { background:#FFFFFF; color:#667085; border:1px solid #E3E7F0; }
        .b-btn-soft:hover { transform:translateY(-2px); box-shadow:0 6px 14px rgba(16,24,40,0.10); border-color:#2F6FED; color:#2F6FED; }
        .b-icon { width:64px; height:64px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:28px; flex-shrink:0; }
        .b-icon-sm { width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:18px; flex-shrink:0; }
        .b-chip { display:inline-block; font-size:13px; font-weight:700; border-radius:20px; padding:5px 14px; }
        .b-blob { position:absolute; top:-40px; right:-40px; width:120px; height:120px; border-radius:50%; opacity:0.08; pointer-events:none; }
        .b-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:16px; align-items:stretch; }
      `}</style>
        <Sidebar activeKey={activeTab} onSelect={setActiveTab}
          guideKey={guideActive && guideSteps[guideIndex]
            ? guideSteps[guideIndex].tab : ""} />

      <div className="b-content-col" style={{flex:1, minWidth:0, overflowY:"auto"}}>

      {userRole === "owner" && unreadEscalationsCount > 0 && showEscalationPopup && !directorMode && (
        <div style={{position:"fixed", bottom:"24px", left:"24px", background:"#FDEDEC", border:"2px solid #F04438", borderRadius:"12px", padding:"16px 20px", maxWidth:"340px", zIndex:1000, boxShadow:"0 8px 24px rgba(0,0,0,0.5)"}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:"12px"}}>
            <div>
              <div style={{color:"#F04438", fontWeight:"bold", fontSize:"23px", marginBottom:"6px"}}>⚠️ Требует внимания</div>
              <div style={{color:"#344054", fontSize:"15px"}}>{unreadEscalationsCount} {unreadEscalationsCount === 1 ? "ошибка" : "ошибки/ошибок"} в аккаунтах — Борис не смог довести задачу до результата.</div>
              <button className="boris-btn-hover" onClick={() => { setDirectorMode(true); setShowEscalationPopup(false); }} style={{marginTop:"10px", background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"6px 14px", fontWeight:"bold", cursor:"pointer", fontSize:"15px"}}>👑 Открыть у директора</button>
            </div>
            <button className="boris-btn-hover" onClick={() => setShowEscalationPopup(false)} style={{background:"#FFFFFF", border:"none", color:"#2F6FED", cursor:"pointer", fontSize:"15px"}}>✕</button>
          </div>
        </div>
      )}

      {advisorMode ? (
        <div style={{padding:"32px"}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"20px"}}>
            <h2 style={{margin:0, color:"#14161A", fontSize:"26px", fontWeight:800, display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #9B87F5, #7C5CFC)"}}>📈</span>Маркетолог-советник по ставкам</h2>
            <button className="boris-btn-hover" onClick={runAdvisor} disabled={advisorLoading} style={{background:"#FFFFFF", color:"#F79009", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"15px"}}>
              {advisorLoading ? "⏳ Считаю..." : "🔄 Пересчитать"}
            </button>
          </div>

          {/* Форма активации: два обязательных поля */}
          <div className="b-panel b-card-eq" style={{marginBottom:"20px"}}>
            <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #12B76A, #039855)"}}>🎯</span>Активация Советника</h3>
            <p className="b-sub">Оба поля обязательны. Пока не заданы — Советник не работает.</p>
            <div style={{display:"flex", gap:"16px", flexWrap:"wrap", alignItems:"flex-end"}}>
              <div>
                <div className="b-label">Макс. цена лида, ₽</div><div style={{fontSize:"12px", color:"#98A2B3", marginBottom:"6px"}}>Сколько готовы платить за один контакт</div>
                <input type="number" value={advMaxCpl} onChange={e => setAdvMaxCpl(e.target.value)} placeholder="500"
                  style={{width:"140px", padding:"9px 12px", border:"1px solid #E3E7F0", borderRadius:"8px", fontSize:"15px"}} />
              </div>
              <div>
                <div className="b-label">Суточный лимит бюджета, ₽</div><div style={{fontSize:"12px", color:"#98A2B3", marginBottom:"6px"}}>Максимум трат за сутки</div>
                <input type="number" value={advDailyLimit} onChange={e => setAdvDailyLimit(e.target.value)} placeholder="1000"
                  style={{width:"170px", padding:"9px 12px", border:"1px solid #E3E7F0", borderRadius:"8px", fontSize:"15px"}} />
              </div>
              <button className="boris-btn-hover" onClick={activateAdvisor} disabled={advisorLoading || !advMaxCpl || !advDailyLimit}
                style={{background: (!advMaxCpl || !advDailyLimit) ? "#C9D2E3" : "#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"10px 20px", fontSize:"15px", fontWeight:"bold", cursor: (!advMaxCpl || !advDailyLimit) ? "not-allowed" : "pointer"}}>
                {(advisorData || advAutopilot) ? "💾 Сохранить" : "Активировать"}
              </button>
            </div>
          </div>

          {advisorData && (
            <>
              {/* Сводка */}
              <div style={{display:"flex", gap:"14px", flexWrap:"wrap", marginBottom:"20px"}}>
                <div style={{flex:1, minWidth:"180px", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"14px"}}>
                  <div style={{fontSize:"13px", color:"#667085"}}>Цель: цена лида</div>
                  <div style={{fontSize:"22px", fontWeight:"bold", color:"#344054"}}>≤ {advisorData?.target?.max_cpl_rub ?? "—"} ₽</div>
                </div>
                <div style={{flex:1, minWidth:"180px", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"14px"}}>
                  <div style={{fontSize:"13px", color:"#667085"}}>Факт: цена лида сегодня</div>
                  <div style={{fontSize:"22px", fontWeight:"bold", color: (advisorData?.fact?.account_cpl_rub && advisorData?.target?.max_cpl_rub && advisorData.fact.account_cpl_rub > advisorData.target.max_cpl_rub) ? "#F04438" : "#12B76A"}}>
                    {advisorData?.fact?.account_cpl_rub ?? "нет данных"} {advisorData?.fact?.account_cpl_rub ? "₽" : ""}
                  </div>
                </div>
                <div style={{flex:1, minWidth:"180px", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"14px"}}>
                  <div style={{fontSize:"13px", color:"#667085"}}>Контактов сегодня</div>
                  <div style={{fontSize:"22px", fontWeight:"bold", color:"#344054"}}>{advisorData?.fact?.contacts_today ?? 0}</div>
                </div>
              </div>

              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", gap:"12px", background: advAutopilot ? "#ECFDF3" : "#EFF4FF", border: advAutopilot ? "1px solid #A6F4C5" : "1px solid #D1E0FF", borderRadius:"8px", padding:"12px 14px", marginBottom:"18px", fontSize:"14px", color: advAutopilot ? "#027A48" : "#2F6FED"}}>
                <div style={{display:"flex", alignItems:"center", gap:"10px", flex:1}}>
                  {advAutopilot && <span style={{whiteSpace:"nowrap", background:"#12B76A", color:"#FFFFFF", borderRadius:"20px", padding:"4px 12px", fontSize:"13px", fontWeight:"bold"}}>🟢 Автопилот активен</span>}
                  <span>{advAutopilot ? "Борис сам меняет ставки каждый час в рамках предохранителей." : "ℹ️ Режим: только рекомендации (Борис ничего не меняет)."} {advisorData?.fact?.data_note || ""}</span>
                </div>
                <button className="boris-btn-hover" onClick={toggleAutopilot} style={{whiteSpace:"nowrap", background: advAutopilot ? "#FFFFFF" : "#2F6FED", color: advAutopilot ? "#F04438" : "#FFFFFF", border: advAutopilot ? "1px solid #F04438" : "none", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontWeight:"bold", fontSize:"14px"}}>
                  {advAutopilot ? "⏹ Отключить автопилот" : "Включить автопилот ставок"}
                </button>
              </div>
              <div style={{marginBottom:"16px", color:"#344054", fontSize:"15px"}}>{advisorData?.summary}</div>

              {/* Три блока рекомендаций */}
              {[
                {k:"raise", t:"🟢 Работают — можно поднять ставку", col:"#12B76A"},
                {k:"lower_or_archive", t:"🔴 Снизить ставку или в архив", col:"#F04438"},
                {k:"watching", t:"🟡 Наблюдаю — мало данных", col:"#F79009"},
              ].map(sec => {
                const list = advisorData?.recommendations?.[sec.k] || [];
                if (!list.length) return null;
                return (
                  <div key={sec.k} style={{marginBottom:"18px"}}>
                    <div style={{fontWeight:"bold", color:sec.col, marginBottom:"8px", fontSize:"16px"}}>{sec.t} ({list.length})</div>
                    <div style={{maxHeight:"320px", overflowY:"auto", display:"flex", flexDirection:"column", gap:"6px"}}>
                      {list.map((it: any, i: number) => (
                        <div key={i} style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px 12px", display:"flex", justifyContent:"space-between", gap:"12px", fontSize:"14px"}}>
                          <div style={{flex:1}}>
                            <div style={{color:"#344054", fontWeight:"500"}}>{it.title}</div>
                            <div style={{color:"#667085", fontSize:"13px", marginTop:"2px"}}>{it.why}{it.suggest ? ` → ${it.suggest}` : ""}</div>
                          </div>
                          <div style={{textAlign:"right", whiteSpace:"nowrap", color:"#667085", fontSize:"13px", display:"flex", flexDirection:"column", alignItems:"flex-end", gap:"4px"}}>
                            <div>👁 {it.views} · 📞 {it.contacts}</div>
                            <div>{it.bid_rub != null ? `${it.bid_rub} ₽` : "без ставки"}</div>
                            {(sec.k === "raise" || sec.k === "lower_or_archive") && (
                              <button className="boris-btn-hover" onClick={() => applyAdvisorOne(it.id, sec.k === "raise" ? "raise" : "archive")}
                                style={{background: sec.k === "raise" ? "#12B76A" : "#F04438", color:"#FFFFFF", border:"none", borderRadius:"6px", padding:"4px 10px", cursor:"pointer", fontSize:"12px", fontWeight:"bold"}}>
                                {sec.k === "raise" ? "▲ Поднять" : "✕ Снять"}
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </>
          )}
          {!advisorData && !advisorLoading && (
            <div style={{color:"#667085", fontSize:"15px", padding:"20px 0"}}>Задайте цену лида и суточный лимит, нажмите «Активировать» — Борис проанализирует объявления.</div>
          )}
        </div>
      ) : directorMode ? (

        <div style={{padding:"32px"}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"20px"}}>
            <h2 style={{margin:0, color:"#14161A", fontSize:"26px", fontWeight:800, display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #F79009, #E8890B)"}}>👑</span>Панель директора — все клиенты</h2>
            {null}
          </div>
          <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"18px", marginBottom:"20px"}}>
            <div style={{display:"flex", gap:"8px", flexWrap:"wrap", marginBottom:"16px"}}>
              <span style={{color:"#667085", fontSize:"14px", alignSelf:"center"}}>с</span>
              <input type="date" value={dsFrom} max={dsTo} onChange={e=>setDsFrom(e.target.value)}
                style={{padding:"7px 10px", border:"1px solid #E3E7F0", borderRadius:"8px", fontSize:"14px"}} />
              <span style={{color:"#667085", fontSize:"14px", alignSelf:"center"}}>по</span>
              <input type="date" value={dsTo} min={dsFrom} onChange={e=>setDsTo(e.target.value)}
                style={{padding:"7px 10px", border:"1px solid #E3E7F0", borderRadius:"8px", fontSize:"14px"}} />
              <button onClick={()=>loadDirectorStatsRange(dsFrom, dsTo)} disabled={dsLoading}
                style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"7px 16px", fontSize:"14px", cursor:"pointer", fontWeight:"bold"}}>Показать</button>
              {[["7д",7],["30д",30],["90д",90],["180д",180],["365д",365]].map(([label,d]:any)=>(
                <button key={d} disabled={dsLoading} onClick={()=>{
                    const t=new Date(); const f=new Date(); f.setDate(t.getDate()-(d-1));
                    const iso=(x:Date)=>x.toISOString().slice(0,10);
                    setDsFrom(iso(f)); setDsTo(iso(t)); loadDirectorStatsRange(iso(f), iso(t));
                  }}
                  style={{background:"#FFFFFF", color:"#344054", border:"1px solid #E3E7F0", borderRadius:"8px",
                    padding:"7px 14px", fontSize:"14px", cursor:"pointer"}}>{label}</button>
              ))}
            </div>
            {dsLoading ? (
              <div style={{color:"#667085", fontSize:"15px"}}>Загрузка…</div>
            ) : dsData && dsData.totals ? (
              <>
                <div style={{display:"flex", gap:"32px", flexWrap:"wrap"}}>
                  <div><div style={{color:"#667085", fontSize:"13px", marginBottom:"4px"}}>Показы</div>
                    <div style={{fontSize:"28px", fontWeight:800, color:"#14161A"}}>{dsData.totals.views}</div>
                    <div>{dsDelta(dsData.deltas?.views)}</div></div>
                  <div><div style={{color:"#667085", fontSize:"13px", marginBottom:"4px"}}>Контакты</div>
                    <div style={{fontSize:"28px", fontWeight:800, color:"#14161A"}}>{dsData.totals.contacts}</div>
                    <div>{dsDelta(dsData.deltas?.contacts)}</div></div>
                  <div><div style={{color:"#667085", fontSize:"13px", marginBottom:"4px"}}>Конверсия</div>
                    <div style={{fontSize:"28px", fontWeight:800, color:"#2F6FED"}}>{dsData.totals.conversion}%</div>
                    <div>{dsDelta(dsData.deltas?.conversion)}</div></div>
                </div>
                <div style={{color:"#98A2B3", fontSize:"13px", marginTop:"12px"}}>
                  Данные с {dsData.data_since || "—"} · наполнено {dsData.days_with_data} дн. из {dsData.days_requested} · сравнение с предыдущими {dsData.days_requested} дн. ({dsData.prev_days_with_data} дн. данных)
                </div>
              </>
            ) : (
              <div style={{color:"#667085", fontSize:"15px"}}>Нет данных за период</div>
            )}
            <div style={{display:"flex", alignItems:"center", gap:"10px"}}>
              <button className="boris-btn-hover" title="Открывает витрину как гость, ПК+моб, разбор глазами клиента (тратит vision)" onClick={async()=>{try{await apiFetch("/api/qa/landing",{method:"POST"});alert("🌐 Аудит лендинга запущен. Отчёт придёт в Telegram через 1-2 минуты.");}catch(e){alert("Не удалось запустить: "+e);}}} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"11px 20px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>🌐 Аудит лендинга</button>
              <button className="boris-btn-hover" title="Бесплатная проверка всех вкладок кабинета" onClick={async()=>{try{await apiFetch("/api/qa/run",{method:"POST"});alert("⚡ Быстрый тест запущен. Отчёт придёт в Telegram через 2-3 минуты.");}catch(e){alert("Не удалось запустить: "+e);}}} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>⚡ Быстрый тест</button>
              <button className="boris-btn-hover" title="Автоматический ежедневный быстрый тест" onClick={async()=>{const nv=!qaAuto;try{await apiFetch("/api/qa/auto",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:nv})});setQaAuto(nv);}catch(e){alert("Не удалось переключить: "+e);}}} style={{background: qaAuto ? "#12B76A" : "#FFFFFF", color: qaAuto ? "#FFFFFF" : "#667085", border: qaAuto ? "1px solid #12B76A" : "1px solid #E3E7F0", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>{qaAuto ? "🟢 Авто вкл" : "⚪ Авто выкл"}</button>
              <button className="boris-btn-hover" onClick={loadDirectorOverview} disabled={directorLoading} style={{background:"#FFFFFF", color:"#F79009", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"15px"}}>
                {directorLoading ? "⏳ Загружаю..." : "🔄 Обновить"}
              </button>
            </div>
          </div>

          <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"20px", marginBottom:"20px"}}>
            <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"14px"}}>
              <div style={{color:"#14161A", fontWeight:800, fontSize:"19px", display:"flex", alignItems:"center", gap:"8px"}}>🆕 Новые клиенты {newClients.length > 0 && <span style={{background:"#EAF3FF", color:"#2F6FED", borderRadius:"20px", padding:"2px 10px", fontSize:"14px"}}>{newClients.length}</span>}</div>
              <button className="boris-btn-hover" onClick={() => kbOpenModal("global")} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"11px 20px", cursor:"pointer", fontSize:"14px", fontWeight:"bold", marginRight:"8px"}}>Обучить Бориса</button>
                <button className="boris-btn-hover" onClick={loadNewClients} disabled={newClientsLoading} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 14px", cursor:"pointer", fontSize:"14px"}}>{newClientsLoading ? "⏳" : "🔄"}</button>
            </div>
            {newClients.length === 0 ? (
              <div style={{color:"#667085", fontSize:"15px", padding:"8px 0"}}>Пока никто не зарегистрировался.</div>
            ) : (
              <div style={{display:"flex", flexDirection:"column", gap:"10px"}}>
                {newClients.map((c: any) => {
                  const badge = c.status === "активна" ? {bg:"#E7F7EE", fg:"#12805C", label:"🟢 активна"} : c.status === "триал" ? {bg:"#FEF6E7", fg:"#B25E09", label:"🟡 триал"} : c.status === "истёк" ? {bg:"#FDEDEC", fg:"#C4320A", label:"🔴 истёк"} : {bg:"#F0F1F5", fg:"#667085", label:"— нет подписки"};
                  const reg = c.registered_at ? new Date(c.registered_at).toLocaleDateString("ru-RU") : "—";
                  return (
                    <div key={c.id} style={{display:"flex", justifyContent:"space-between", alignItems:"center", flexWrap:"wrap", gap:"10px", background:"#F9FAFC", border:"1px solid #EEF1F6", borderRadius:"10px", padding:"12px 14px"}}>
                      <div style={{display:"flex", flexDirection:"column", gap:"3px"}}>
                        <div style={{color:"#14161A", fontWeight:700, fontSize:"15px"}}>{c.email}</div>
                        <div style={{color:"#667085", fontSize:"13px"}}>Регистрация: {reg} · Аккаунтов: {c.accounts_count}</div>
                      </div>
                      <div style={{display:"flex", alignItems:"center", gap:"10px"}}>
                        {c.days_left !== null && c.days_left >= 0 && <span style={{color:"#667085", fontSize:"13px"}}>ещё {c.days_left} дн.</span>}
                        <span style={{background:badge.bg, color:badge.fg, borderRadius:"20px", padding:"4px 12px", fontSize:"13px", fontWeight:700, whiteSpace:"nowrap"}}>{badge.label}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {escalations.length > 0 && (
            <div style={{background:"#FDEDEC", border:"1px solid #FDEDEC", borderRadius:"10px", padding:"16px", marginBottom:"20px"}}>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"10px"}}>
                <div style={{color:"#F04438", fontWeight:"bold", fontSize:"23px"}}>⚠️ Эскалации ошибок ({escalations.length})</div>
                <button className="boris-btn-hover" onClick={async () => {
                  await apiFetch("/api/avito/director/clear_escalations", {method:"POST"});
                  loadDirectorOverview();
                }} style={{background:"#FDEDEC", color:"#F04438", border:"1px solid #FDEDEC", borderRadius:"6px", padding:"4px 10px", cursor:"pointer", fontSize:"15px"}}>
                  🗑 Очистить всё
                </button>
              </div>
              <div style={{maxHeight:"240px", overflowY:"auto", display:"flex", flexDirection:"column", gap:"6px"}}>
                {escalations.map((e: any, i: number) => (
                  <div key={i} style={{fontSize:"15px", color:"#344054", padding:"8px 10px", background:"#F6F7FB", borderRadius:"6px", display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:"10px"}}>
                    <div style={{flex:1}}>
                      <div><b style={{color:"#F79009"}}>{e.account_id}</b> — {e.task_type}</div>
                      <div style={{color:"#F04438", marginTop:"2px"}}>{e.error_message}</div>
                      <div style={{color:"#8A93A6", fontSize:"15px", marginTop:"2px"}}>{new Date(e.ts).toLocaleString("ru-RU")}</div>
                    </div>
                    <button className="boris-btn-hover" onClick={async () => {
                      await apiFetch("/api/avito/director/resolve_escalation", {
                        method:"POST",
                        headers:{"Content-Type":"application/json"},
                        body: JSON.stringify({ts: e.ts})
                      });
                      loadDirectorOverview();
                    }} style={{background:"#E7EFFE", color:"#2F6FED", border:"1px solid #E7EFFE", borderRadius:"6px", padding:"4px 8px", cursor:"pointer", fontSize:"15px", whiteSpace:"nowrap"}}>
                      ✓ Исправить
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {directorLoading && <p style={{color:"#667085"}}>Собираю сводку по всем аккаунтам...</p>}
          {!directorLoading && (() => {
            const paid = directorOverview.filter((a: any) => a.money?.total_rub > 0);
            const total = paid.reduce((s: number, a: any) => s + (a.money?.total_rub || 0), 0);
            const soon = directorOverview.filter((a: any) => {
              const d = a.payment?.days_left;
              return typeof d === "number" && d <= 7;
            });
            if (!total && !soon.length) return null;
            const box = (grad: string, val: string, cap: string) => (
              <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px 20px", position:"relative", overflow:"hidden"}}>
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div style={{fontSize:"26px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em"}}>{val}</div>
                <div style={{fontSize:"14px", color:"#667085", marginTop:"2px"}}>{cap}</div>
              </div>
            );
            return (
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(220px, 1fr))", gap:"14px", marginBottom:"20px"}}>
                {box("linear-gradient(135deg,#32D583,#12805C)", total.toLocaleString("ru") + " \u20bd", "получено от клиентов")}
                {box("linear-gradient(135deg,#4C8DFF,#2F6FED)", String(paid.length), "клиентов платят")}
                {box("linear-gradient(135deg,#FDB022,#F79009)", String(soon.length), "оплата кончается за 7 дней")}
              </div>
            );
          })()}
          {!directorLoading && (() => {
            const paid = directorOverview.filter((a: any) => a.money?.total_rub > 0);
            const total = paid.reduce((s: number, a: any) => s + (a.money?.total_rub || 0), 0);
            const soon = directorOverview.filter((a: any) => {
              const d = a.payment?.days_left;
              return typeof d === "number" && d <= 7;
            });
            if (!total && !soon.length) return null;
            const box = (grad: string, val: string, cap: string) => (
              <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px 20px", position:"relative", overflow:"hidden"}}>
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div style={{fontSize:"26px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em"}}>{val}</div>
                <div style={{fontSize:"14px", color:"#667085", marginTop:"2px"}}>{cap}</div>
              </div>
            );
            return (
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(220px, 1fr))", gap:"14px", marginBottom:"20px"}}>
                {box("linear-gradient(135deg,#32D583,#12805C)", total.toLocaleString("ru") + " \u20bd", "получено от клиентов")}
                {box("linear-gradient(135deg,#4C8DFF,#2F6FED)", String(paid.length), "клиентов платят")}
                {box("linear-gradient(135deg,#FDB022,#F79009)", String(soon.length), "оплата кончается за 7 дней")}
              </div>
            );
          })()}
          {!directorLoading && (() => {
            const paid = directorOverview.filter((a: any) => a.money?.total_rub > 0);
            const total = paid.reduce((s: number, a: any) => s + (a.money?.total_rub || 0), 0);
            const soon = directorOverview.filter((a: any) => {
              const d = a.payment?.days_left;
              return typeof d === "number" && d <= 7;
            });
            if (!total && !soon.length) return null;
            const box = (grad: string, val: string, cap: string) => (
              <div style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px 20px", position:"relative", overflow:"hidden"}}>
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div style={{fontSize:"26px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em"}}>{val}</div>
                <div style={{fontSize:"14px", color:"#667085", marginTop:"2px"}}>{cap}</div>
              </div>
            );
            return (
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(220px, 1fr))", gap:"14px", marginBottom:"20px"}}>
                {box("linear-gradient(135deg,#32D583,#12805C)", total.toLocaleString("ru") + " \u20bd", "получено от клиентов")}
                {box("linear-gradient(135deg,#4C8DFF,#2F6FED)", String(paid.length), "клиентов платят")}
                {box("linear-gradient(135deg,#FDB022,#F79009)", String(soon.length), "оплата кончается за 7 дней")}
              </div>
            );
          })()}
          {!directorLoading && (
            <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(300px, 1fr))", gap:"16px"}}>
              {directorOverview.map((acc: any) => (
                <div key={acc.account_id} onClick={() => { setCurrentAccount(acc.account_id); setDirectorMode(false); }}
                  className={"b-client-card" + (acc.latest_stats_date && Number(acc.balance?.real) < 500 ? " b-bal-crit" : acc.latest_stats_date && Number(acc.balance?.real) < 2000 ? " b-bal-warn" : "")}>
                  <div style={{fontSize:"20px", fontWeight:800, marginBottom:"6px", color:"#14161A", lineHeight:1.25}}>{acc.name}</div>
                  <div style={{fontSize:"12px", color:"#98A2B3", marginBottom:"14px", fontFamily:"ui-monospace, monospace"}}>{acc.account_id}</div>
                  <div style={{display:"flex", flexDirection:"column", gap:"6px", fontSize:"15px"}}>
                    {acc.money && acc.money.total_rub > 0 && (
                      <div style={{background:"#F0FDF6", border:"1px solid #D1FADF", borderRadius:"10px", padding:"8px 12px", marginBottom:"4px"}}>
                        <div><span style={{color:"#667085"}}>Заплатил всего: </span><b style={{color:"#12805C", fontSize:"17px"}}>{acc.money.total_rub.toLocaleString("ru")} ₽</b></div>
                        <div style={{fontSize:"13px", color:"#667085", marginTop:"2px"}}>
                          {acc.money.payments_count} {acc.money.payments_count === 1 ? "платёж" : "платежей"}
                          {acc.money.last_at ? ` · последний ${new Date(acc.money.last_at).toLocaleDateString("ru")}` : ""}
                          {acc.money.last_title ? ` · ${acc.money.last_title}` : ""}
                        </div>
                      </div>
                    )}
                    {acc.money && acc.money.total_rub > 0 && (
                      <div style={{background:"#F0FDF6", border:"1px solid #D1FADF", borderRadius:"10px", padding:"8px 12px", marginBottom:"4px"}}>
                        <div><span style={{color:"#667085"}}>Заплатил всего: </span><b style={{color:"#12805C", fontSize:"17px"}}>{acc.money.total_rub.toLocaleString("ru")} ₽</b></div>
                        <div style={{fontSize:"13px", color:"#667085", marginTop:"2px"}}>
                          {acc.money.payments_count} {acc.money.payments_count === 1 ? "платёж" : "платежей"}
                          {acc.money.last_at ? ` · последний ${new Date(acc.money.last_at).toLocaleDateString("ru")}` : ""}
                          {acc.money.last_title ? ` · ${acc.money.last_title}` : ""}
                        </div>
                      </div>
                    )}
                    <div>
                      <span style={{color:"#667085"}}>Avito-ключи: </span>
                      {acc.has_avito_keys ? <span style={{color:"#2F6FED"}}>✅ подключены</span> : <span style={{color:"#F04438"}}>⚠️ не настроены</span>}
                    </div>
                    {acc.latest_stats_date ? (
                      <>
                        <div><span style={{color:"#667085"}}>Баланс: </span><b style={{color: Number(acc.balance?.real) < 500 ? "#D92D20" : Number(acc.balance?.real) < 2000 ? "#B54708" : "#2F6FED", fontSize:"17px"}}>{acc.balance?.real} ₽</b>{Number(acc.balance?.real) < 500 && <span style={{marginLeft:"8px", background:"#FEE4E2", color:"#B42318", borderRadius:"20px", padding:"2px 9px", fontSize:"11px", fontWeight:800}}>ПОПОЛНИТЬ</span>}</div>
                        <div><span style={{color:"#667085"}}>Объявлений: </span><b>{acc.items_count}</b></div>
                        <div><span style={{color:"#667085"}}>Запросов контактов сегодня: </span><b style={{color:"#2F6FED"}}>{acc.contacts_today ?? 0}</b></div>
                        {acc.cost_per_contact != null && (
                          <div><span style={{color:"#667085"}}>Стоимость 1 контакта: </span><b style={{color:"#F79009"}}>{acc.cost_per_contact} ₽</b></div>
                        )}
                        <div style={{color:"#8A93A6", fontSize:"15px"}}>Данные за {acc.latest_stats_date}</div>
                        {(() => {
                          const st = (dsData?.by_account || []).find((x:any) => x.account_id === acc.account_id);
                          if (!st) return null;
                          return (
                            <div style={{marginTop:"8px", paddingTop:"8px", borderTop:"1px dashed #EEF1F6"}}>
                              <div style={{color:"#98A2B3", fontSize:"12px", marginBottom:"5px"}}>За период · {dsDays} дн.</div>
                              <div style={{display:"flex", flexDirection:"column", gap:"3px", fontSize:"14px"}}>
                                <div><span style={{color:"#667085"}}>Показы: </span><b>{st.views}</b> {dsDelta(st.d_views)}</div>
                                <div><span style={{color:"#667085"}}>Контакты: </span><b>{st.contacts}</b> {dsDelta(st.d_contacts)}</div>
                                <div><span style={{color:"#667085"}}>Конверсия: </span><b style={{color:"#2F6FED"}}>{st.conversion}%</b> {dsDelta(st.d_conversion)}</div>
                              </div>
                            </div>
                          );
                        })()}
                        <div style={{marginTop:"10px", paddingTop:"10px", borderTop:"1px solid #EEF1F6"}}>
                          {acc.payment?.has_payment ? (
                            <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", gap:"8px"}}>
                              <span style={{background: acc.payment.color === "red" ? "#FEE4E2" : acc.payment.color === "yellow" ? "#FEF0C7" : "#D1FADF", color: acc.payment.color === "red" ? "#B42318" : acc.payment.color === "yellow" ? "#B54708" : "#027A48", borderRadius:"20px", padding:"4px 12px", fontSize:"13px", fontWeight:"bold"}}>
                                {acc.payment.color === "red" ? "🔴" : acc.payment.color === "yellow" ? "🟡" : "🟢"} {acc.payment.overdue ? `Просрочено ${-acc.payment.days_left} дн.` : `осталось ${acc.payment.days_left} дн.`}
                              </span>
                              <button className="boris-btn-hover" onClick={(e) => { e.stopPropagation(); setPayModalAccount(acc.account_id); setPayAmount(String(acc.payment.amount_rub||"")); setPayPeriod(String(acc.payment.period_days||30)); }} style={{background:"#FFFFFF", color:"#2F6FED", border:"1px solid #D0D5DD", borderRadius:"10px", padding:"3px 10px", cursor:"pointer", fontSize:"12px"}}>✏️</button>
                            </div>
                          ) : (
                            <button className="boris-btn-hover" onClick={(e) => { e.stopPropagation(); setPayModalAccount(acc.account_id); setPayAmount(""); setPayPeriod("30"); }} style={{background:"#FFFFFF", color:"#2F6FED", border:"1px dashed #D0D5DD", borderRadius:"10px", padding:"4px 12px", cursor:"pointer", fontSize:"13px"}}>💳 Отметить оплату</button>
                          )}
                          {acc.payment?.has_payment && <div style={{color:"#8A93A6", fontSize:"12px", marginTop:"4px"}}>до {acc.payment.paid_until} · {acc.payment.amount_rub}₽</div>}
                        </div>
                      </>
                    ) : (
                      <div style={{color:"#8A93A6", fontSize:"15px"}}>Нет собранной статистики</div>
                    )}
                    <div style={{marginTop:"auto", paddingTop:"12px", borderTop:"1px solid #EEF1F6"}}>
                      <button className="boris-btn-hover" onClick={(e) => { e.stopPropagation(); toggleUnlimited(acc.account_id, !!acc.billing?.unlimited); }}
                        style={{
                          background: acc.billing?.unlimited ? "#D1FADF" : "#FFFFFF",
                          color: acc.billing?.unlimited ? "#027A48" : "#667085",
                          border: acc.billing?.unlimited ? "1px solid #6CE9A6" : "1px solid #D0D5DD",
                          borderRadius:"6px", padding:"4px 12px", cursor:"pointer", fontSize:"13px", width:"100%", fontWeight: acc.billing?.unlimited ? "bold" : "normal"
                        }}>
                        {acc.billing?.unlimited ? "🔓 Без ограничений (вкл)" : "🔒 По тарифу — включить безлимит"}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
              {directorOverview.length === 0 && <p style={{color:"#667085"}}>Клиентов пока нет</p>}
            </div>
          )}
        </div>
      ) : walletMode ? (
        <div style={{padding:"8px 4px", maxWidth:"820px"}}>
          <h2 style={{margin:"0 0 6px", color:"#12805C", fontSize:"23px"}}>💰 Кошелёк</h2>
          <div style={{fontSize:"14px", color:"#667085", marginBottom:"20px"}}>Пополняйте баланс по счёту (для юрлиц и ИП) и покупайте любые услуги Бориса с баланса.</div>
          <div style={{display:"flex", gap:"16px", flexWrap:"wrap", marginBottom:"24px"}}>
            <div style={{flex:"1", minWidth:"260px", background:"linear-gradient(135deg,#F4F3FF,#FFFFFF)", border:"1px solid #D9D6FE", borderRadius:"16px", padding:"22px 24px"}}>
              <div style={{fontSize:"13px", color:"#667085", marginBottom:"6px"}}>Баланс кошелька</div>
              <div style={{fontSize:"34px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em"}}>{Number(balanceData.balance||0).toLocaleString("ru-RU")} ₽</div>
            </div>
            <div style={{flex:"1", minWidth:"260px", background:"#fff", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"22px 24px"}}>
              <div style={{fontSize:"13px", color:"#667085", marginBottom:"10px"}}>Пополнить по счёту</div>
              <div style={{display:"flex", gap:"8px"}}>
                <input value={topupSum} onChange={e=>setTopupSum(e.target.value.replace(/[^0-9]/g,""))} placeholder="Сумма, ₽" style={{flex:1, padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} />
                <button onClick={()=>{ if(!topupSum||Number(topupSum)<=0){alert("Укажите сумму");return;} setInvoicePack(""); (window as any)._topup=Number(topupSum); setInvoiceOpen(true); }} style={{background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"10px 18px", fontSize:"14px", fontWeight:700, cursor:"pointer", whiteSpace:"nowrap"}}>Выставить счёт</button>
              </div>
            </div>
          </div>
          <div style={{background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"14px", padding:"18px 20px", marginBottom:"28px", display:"flex", alignItems:"center", justifyContent:"space-between", gap:"14px", flexWrap:"wrap"}}>
            <div style={{fontSize:"14px", color:"#475467"}}>Все услуги Бориса — РОП, Менеджер, тарифы, баннеры — можно оплатить с баланса кошелька в разделе «Тарифы».</div>
            <button onClick={()=>{ setWalletMode(false); setTariffsFinanceMode(true); }} style={{background:"#7F56D9", color:"#fff", border:"none", borderRadius:"10px", padding:"11px 22px", fontSize:"14px", fontWeight:700, cursor:"pointer", whiteSpace:"nowrap"}}>Купить услуги →</button>
          </div>
          {userRole === "owner" && (
            <div style={{marginBottom:"28px"}}>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"12px"}}>
                <div style={{fontSize:"16px", fontWeight:800, color:"#1D2939"}}>Счета клиентов (админ)</div>
                <button onClick={loadInvoices} style={{fontSize:"13px", color:"#2F6FED", background:"#EFF4FF", border:"none", borderRadius:"8px", padding:"7px 14px", cursor:"pointer", fontWeight:600}}>Обновить</button>
              </div>
              {invoicesList.length===0 ? (
                <div style={{fontSize:"14px", color:"#98A2B3"}}>Счетов пока нет.</div>
              ) : (
                <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                  {invoicesList.map((inv:any, i:number)=>(
                    <div key={i} style={{display:"flex", justifyContent:"space-between", alignItems:"center", padding:"12px 14px", background:"#fff", border:"1px solid #EEF1F6", borderRadius:"10px", gap:"12px", flexWrap:"wrap"}}>
                      <div style={{flex:1, minWidth:"180px"}}>
                        <div style={{fontSize:"14px", fontWeight:700, color:"#1D2939"}}>{inv.number} · {Number(inv.amount||0).toLocaleString("ru-RU")} ₽</div>
                        <div style={{fontSize:"12px", color:"#98A2B3"}}>{inv.account_id} · {inv.payer_name||"—"} · {(inv.created_at||"").slice(0,10)}</div>
                      </div>
                      <div style={{fontSize:"12px", fontWeight:700, padding:"4px 10px", borderRadius:"20px", background: inv.status==="paid"?"#D1FADF":inv.status==="partial"?"#FEF0C7":"#F2F4F7", color: inv.status==="paid"?"#027A48":inv.status==="partial"?"#B54708":"#667085"}}>
                        {inv.status==="paid"?"Оплачен":inv.status==="partial"?"Частично":"Ждёт оплаты"}
                      </div>
                      {inv.status!=="paid" && (
                        <button onClick={()=>markPaid(inv.number, inv.amount)} style={{fontSize:"13px", fontWeight:700, color:"#fff", background:"#12805C", border:"none", borderRadius:"8px", padding:"8px 14px", cursor:"pointer", whiteSpace:"nowrap"}}>Деньги пришли</button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div style={{fontSize:"16px", fontWeight:800, color:"#1D2939", marginBottom:"12px"}}>История операций</div>
          {(!balanceData.history || balanceData.history.length===0) ? (
            <div style={{fontSize:"14px", color:"#98A2B3"}}>Пока операций нет.</div>
          ) : (
            <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
              {balanceData.history.map((h:any, i:number)=>(
                <div key={i} style={{display:"flex", justifyContent:"space-between", alignItems:"center", padding:"10px 14px", background:"#fff", border:"1px solid #EEF1F6", borderRadius:"10px"}}>
                  <div><div style={{fontSize:"14px", color:"#1D2939"}}>{h.note||h.type}</div><div style={{fontSize:"12px", color:"#98A2B3"}}>{(h.ts||"").slice(0,10)}</div></div>
                  <div style={{fontSize:"15px", fontWeight:700, color: (h.amount||0)>=0 ? "#027A48" : "#B42318"}}>{(h.amount||0)>=0?"+":""}{Number(h.amount||0).toLocaleString("ru-RU")} ₽</div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : requisitesMode ? (
        <div style={{padding:"8px 4px", maxWidth:"640px"}}>
          <h2 style={{margin:"0 0 6px", color:"#12805C", fontSize:"23px"}}>🏦 Реквизиты для счетов</h2>
          <div style={{fontSize:"14px", color:"#667085", marginBottom:"20px"}}>Эти данные Борис подставит в счета для оплаты по безналу от юрлиц и ИП. Заполните один раз.</div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>Наименование (ИП Остапенко Кирилл Олегович)</label><input value={requisites.org_name||""} onChange={e=>setRequisites({...requisites, org_name:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>ИНН</label><input value={requisites.inn||""} onChange={e=>setRequisites({...requisites, inn:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>ОГРНИП</label><input value={requisites.ogrnip||""} onChange={e=>setRequisites({...requisites, ogrnip:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>Адрес</label><input value={requisites.address||""} onChange={e=>setRequisites({...requisites, address:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>Банк</label><input value={requisites.bank_name||""} onChange={e=>setRequisites({...requisites, bank_name:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>Расчётный счёт</label><input value={requisites.bank_account||""} onChange={e=>setRequisites({...requisites, bank_account:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>БИК</label><input value={requisites.bank_bik||""} onChange={e=>setRequisites({...requisites, bank_bik:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>Корр. счёт</label><input value={requisites.corr_account||""} onChange={e=>setRequisites({...requisites, corr_account:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>E-mail</label><input value={requisites.email||""} onChange={e=>setRequisites({...requisites, email:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
<div style={{marginBottom:"14px"}}><label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>Телефон</label><input value={requisites.phone||""} onChange={e=>setRequisites({...requisites, phone:e.target.value})} style={{width:"100%", maxWidth:"520px", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px"}} /></div>
          <div style={{marginTop:"18px", display:"flex", alignItems:"center", gap:"14px"}}>
            <button onClick={saveRequisites} style={{background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"12px 24px", fontSize:"15px", fontWeight:700, cursor:"pointer"}}>Сохранить реквизиты</button>
            {reqSaved && <span style={{color:"#027A48", fontWeight:600}}>✓ Сохранено</span>}
          </div>
        </div>
      ) : tariffsFinanceMode ? (

        <div style={{padding:"32px"}}>
          <h2 style={{margin:"0 0 8px", color:"#12805C", fontSize:"23px"}}>💰 Тарифы и Финансы</h2>
          <p style={{color:"#667085", fontSize:"15px", marginBottom:"28px"}}>Оплата тарифа подписки и докупка дополнительных опций сверх лимита.</p>

          {[
            {
              title: "Тарифы подписки (30 дней)",
              items: [
                {name:"Автопилот 2.0", price:"7 000 ₽/мес", badge:"⚡ Старт", badgeColor:"#F79009", icon:"🚀", gradient:"linear-gradient(135deg, #FDB022, #F79009)", invoiceId:"UU31PuJh-kSFcFNmRfA2UA", pack:"sub_auto"},
                {name:"Автопилот MAX", price:"14 000 ₽/мес", badge:"🚀 Для активных продаж", badgeColor:"#7C5CFC", icon:"👑", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"do2mlmm_z0Sk5Pg1luZ-FQ", pack:"sub_max"},
              ]
            },
            {
              title: "Докупка баннеров для объявлений",
              items: [
                {name:"1 баннер", price:"250 ₽", badge:"Точечно", badgeColor:"#667085", icon:"🖼️", gradient:"linear-gradient(135deg, #98A2B3, #667085)", invoiceId:"hlcZfzwZDUWWMhJitJ_wnQ", pack:"ban1"},
                {name:"10 баннеров", price:"1 800 ₽", badge:"Популярно", badgeColor:"#2F6FED", icon:"🖼️", gradient:"linear-gradient(135deg, #4C8DFF, #2F6FED)", invoiceId:"i_DhFWpOYEyAf120Y4Djzg", pack:"ban10"},
                {name:"30 баннеров", price:"5 100 ₽", badge:"💰 Выгодно", badgeColor:"#12805C", icon:"🖼️", gradient:"linear-gradient(135deg, #32D583, #12805C)", invoiceId:"scSJQwIYv0Ke4vDJwMjC3w", pack:"ban30"},
                {name:"50 баннеров", price:"8 000 ₽", badge:"Для каталога", badgeColor:"#667085", icon:"🖼️", gradient:"linear-gradient(135deg, #98A2B3, #667085)", invoiceId:"Lc7XU4driEuytzx6a9453w", pack:"ban50"},
                {name:"100 баннеров", price:"15 000 ₽", badge:"🔥 Максимум", badgeColor:"#F04438", icon:"🖼️", gradient:"linear-gradient(135deg, #FF6B6B, #F04438)", invoiceId:"gA5P5092U0KhSaT3diCv1Q", pack:"ban100"},
              ]
            },
            {
              title: "Баннеры для профиля магазина Avito",
              items: [
                {name:"Расширенный тариф (1 ПК + 1 моб)", price:"700 ₽", badge:"Базово", badgeColor:"#667085", icon:"🏪", gradient:"linear-gradient(135deg, #98A2B3, #667085)", invoiceId:"rpurvpER-UmZnPckeCCkiQ", pack:"prof_ext"},
                {name:"Максимальный тариф (3 ПК + 3 моб)", price:"2 100 ₽", badge:"Полный набор", badgeColor:"#7C5CFC", icon:"🏪", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"O1Xeq-_fAkeAija1zkFpvw", pack:"prof_max"},
              ]
            },
            {
              title: "ИИ менеджер по продажам",
              items: [
                {name:"Базовый пакет — 1 700 сообщений", price:"9 000 ₽", badge:"Автопилот диалогов", badgeColor:"#2F6FED", icon:"💬", gradient:"linear-gradient(135deg, #4C8DFF, #2F6FED)", invoiceId:"lSJ7L0x6zkGbVb3KKO353w", pack:"msg1700"},
                {name:"Докупка +2 000 сообщений", price:"7 000 ₽", badge:"Без пауз", badgeColor:"#667085", icon:"💬", gradient:"linear-gradient(135deg, #98A2B3, #667085)", invoiceId:"P9e953qIq0yZ0cd2NKG_0A", pack:"msg2000"},
                {name:"Докупка +3 000 сообщений", price:"10 000 ₽", badge:"💰 Выгоднее за штуку", badgeColor:"#12805C", icon:"💬", gradient:"linear-gradient(135deg, #32D583, #12805C)", invoiceId:"E6SI_X7tnUqBNDw_RKkOtQ", pack:"msg3000"},
                {name:"ИИ Руководитель отдела продаж", price:"20 000 ₽/мес", badge:"🧠 1500 мин · 450 переписок · 30+30 отчётов", badgeColor:"#7C5CFC", icon:"🧠", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"", pack:"rop1500"},
                {name:"РОП: докупка +300 минут", price:"5 000 ₽", badge:"300 мин · 90 переписок · 5+5 отчётов", badgeColor:"#7C5CFC", icon:"🧠", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"", pack:"rop_a300"},
                {name:"РОП: докупка +500 минут", price:"7 500 ₽", badge:"500 мин · 150 переписок · 10+10 отчётов", badgeColor:"#7C5CFC", icon:"🧠", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"", pack:"rop_a500"},
                {name:"РОП: докупка +700 минут", price:"10 500 ₽", badge:"700 мин · 200 переписок · 15+15 отчётов", badgeColor:"#7C5CFC", icon:"🧠", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"", pack:"rop_a700"},
                {name:"РОП: докупка +1 000 минут", price:"14 000 ₽", badge:"1000 мин · 300 переписок · 20+20 отчётов", badgeColor:"#7C5CFC", icon:"🧠", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"", pack:"rop_a1000"},
                {name:"РОП: докупка +1 500 минут", price:"21 000 ₽", badge:"1500 мин · 400 переписок · 30+30 отчётов", badgeColor:"#7C5CFC", icon:"🧠", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"", pack:"rop_a1500"},
                {name:"РОП: докупка +2 000 минут", price:"26 000 ₽", badge:"2000 мин · 550 переписок · 40+40 отчётов", badgeColor:"#7C5CFC", icon:"🧠", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"", pack:"rop_a2000"},
              ]
            },
          ].map(section => (
            <div key={section.title} style={{marginBottom:"32px"}}>
              <h3 style={{margin:"0 0 14px", fontSize:"18px", color:"#1D2939"}}>{section.title}</h3>
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(260px, 1fr))", gap:"14px", alignItems:"stretch"}}>
                {section.items.map(it => (
                  <div key={it.invoiceId} className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", height:"100%", boxSizing:"border-box", position:"relative", overflow:"hidden"}}>
                    <div style={{position:"absolute", top:"-40px", right:"-40px", width:"120px", height:"120px", borderRadius:"50%", background:it.gradient, opacity:0.08}} />
                    <div style={{width:"64px", height:"64px", borderRadius:"50%", background:it.gradient, display:"flex", alignItems:"center", justifyContent:"center", fontSize:"28px", marginBottom:"16px", boxShadow:`0 8px 20px ${it.badgeColor}40`}}>{it.icon}</div>
                    <div style={{fontWeight:"bold", fontSize:"16px", color:"#1D2939", marginBottom:"8px", minHeight:"40px", display:"flex", alignItems:"center"}}>{it.name}</div>
                    <div style={{color:"#1D2939", fontWeight:800, fontSize:"26px", marginBottom:"14px", letterSpacing:"-0.02em"}}>{(livePrices && (it as any).pack && livePrices[(it as any).pack]) ? livePrices[(it as any).pack].sum.toLocaleString("ru") + " ₽" + (String(it.price).includes("/мес") ? "/мес" : "") : it.price}</div>
                    {it.badge && (
                      <div style={{display:"inline-block", background:`${it.badgeColor}18`, color:it.badgeColor, fontSize:"13px", fontWeight:"bold", borderRadius:"20px", padding:"5px 14px", marginBottom:"22px"}}>{it.badge}</div>
                    )}
                    <div style={{marginTop:"auto", width:"100%"}}>
                      <RobokassaButton invoiceId={it.invoiceId} pack={(it as any).pack} accountId={currentAccount} />
                      <button onClick={()=>buyWithBalance((it as any).pack, it.name)} style={{width:"100%", marginTop:"8px", background:"#fff", color:"#12805C", border:"1.5px solid #12805C", borderRadius:"10px", padding:"9px", fontSize:"13px", fontWeight:700, cursor:"pointer"}}>💰 Купить с баланса</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

      ) : (
      <>

      {guideActive && guideSteps[guideIndex] && (
        <div style={{position:"fixed", bottom:"24px", right:"24px", background:"#F6F7FB", border:"2px solid #2F6FED", borderRadius:"12px", padding:"18px 20px", maxWidth:"340px", zIndex:1000, boxShadow:"0 8px 24px rgba(0,0,0,0.5)"}}>
          <div style={{color:"#2F6FED", fontSize:"15px", fontWeight:"bold", marginBottom:"6px"}}>
            Борис-гид · шаг {guideIndex + 1} из {guideSteps.length}
          </div>
          <div style={{color:"#fff", fontSize:"23px", marginBottom:"14px", lineHeight:"1.4"}}>
            {guideSteps[guideIndex].instruction}
          </div>
          <div style={{display:"flex", gap:"8px"}}>
            <button className="boris-btn-hover" onClick={guideNext} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"11px 20px", fontWeight:"bold", cursor:"pointer", fontSize:"15px"}}>
              {guideIndex + 1 >= guideSteps.length ? "Готово ✓" : "Далее →"}
            </button>
            <button className="boris-btn-hover" onClick={guideSkip} style={{background:"#E3E7F0", color:"#344054", border:"none", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"15px"}}>
              Пропустить всё
            </button>
          </div>
        </div>
      )}

      <div style={{padding:"32px"}}>

        {activeTab === "stats" && (
          <>
          <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(240px, 1fr))", gap:"16px", marginBottom:"20px"}}>
            <div className="b-card b-stat" style={{animationDelay:"0s", minHeight:"110px"}}>
              <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)"}}>📋</span>
              <div>
                <div className="b-meta" style={{fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px", marginBottom:"4px"}}>Всего объявлений</div>
                <div className="b-stat-num"><CountUp to={items.length} /></div>
              </div>
            </div>
            <div className="b-card b-stat" style={{animationDelay:"0.12s", minHeight:"110px"}}>
              <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#3DBE93,#12805C)"}}>✅</span>
              <div>
                <div className="b-meta" style={{fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px", marginBottom:"4px"}}>Активных</div>
                <div className="b-stat-num"><CountUp to={items.filter((i: any) => i.status === "active").length} delay={120} /></div>
              </div>
            </div>
            <div className="b-card b-stat" style={{animationDelay:"0.24s", minHeight:"110px"}}>
              <Mascot size={26} interactive={false} />
              <div>
                <div className="b-meta" style={{fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px", marginBottom:"4px"}}>Статус Бориса</div>
                <div style={{display:"flex", alignItems:"center", gap:"8px", marginTop:"2px"}}>
                  <span style={{width:"9px", height:"9px", borderRadius:"50%", background:"#12805C", boxShadow:"0 0 0 4px rgba(18,128,92,0.15)", animation:"iconPulse 2s ease-in-out infinite"}}></span>
                  <span style={{fontSize:"20px", fontWeight:800, color:"#12805C"}}>Работает</span>
                </div>
              </div>
            </div>
          </div>

          <div className="b-panel" style={{marginTop:"24px"}}>
            <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"16px"}}>
              <h3 className="b-title" style={{margin:0, display:"flex", alignItems:"center", gap:"12px"}}><Mascot size={26} interactive={false} />Резюме и советы от Бориса</h3>
              <button className="b-btn b-btn-ghost" onClick={loadWeeklySummary} disabled={summaryLoading} style={{padding:"7px 16px", fontSize:"14px"}}>
                {summaryLoading ? "⏳ Собираю..." : "🔄 Обновить"}
              </button>
            </div>
            {summaryLoading && <p style={{color:"#667085"}}>Борис анализирует данные...</p>}
            {!summaryLoading && weeklySummary?.status === "ok" && (
              <>
                <p style={{color:"#344054", lineHeight:1.6, whiteSpace:"pre-wrap", marginBottom:"20px"}}>{weeklySummary.summary}</p>
                {weeklySummary.data?.текущий_баланс && (
                  <div style={{display:"flex", gap:"24px", marginBottom:"20px", fontSize:"23px"}}>
                    <div><span style={{color:"#667085"}}>Баланс:</span> <b style={{color:"#2F6FED"}}>{weeklySummary.data.текущий_баланс.real} ₽</b></div>
                    <div><span style={{color:"#667085"}}>Бонусы:</span> <b style={{color:"#7C5CFC"}}>{weeklySummary.data.текущий_баланс.bonus}</b></div>
                    <div><span style={{color:"#667085"}}>Дней в анализе:</span> <b>{weeklySummary.data.дней_данных}</b></div>
                  </div>
                )}
                <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"20px"}}>
                  <div>
                    <div style={{color:"#2F6FED", fontWeight:"bold", marginBottom:"10px", fontSize:"15px"}}>🏆 Самые эффективные</div>
                    {(weeklySummary.data?.примеры_эффективных || []).map((it: any, i: number) => (
                      <div key={i} style={{fontSize:"15px", color:"#344054", padding:"6px 0", borderBottom:"1px solid #FFFFFF"}}>{it.title} <span style={{color:"#2F6FED"}}>({it.conversion}%)</span></div>
                    ))}
                  </div>
                  <div>
                    <div style={{color:"#F04438", fontWeight:"bold", marginBottom:"10px", fontSize:"15px"}}>📉 Требуют внимания</div>
                    {(weeklySummary.data?.примеры_неэффективных || []).map((it: any, i: number) => (
                      <div key={i} style={{fontSize:"15px", color:"#344054", padding:"6px 0", borderBottom:"1px solid #FFFFFF"}}>{it.title} <span style={{color:"#F04438"}}>({it.views} просм.)</span></div>
                    ))}
                  </div>
                </div>
              </>
            )}
            {!summaryLoading && weeklySummary?.status === "error" && (
              <p style={{color:"#667085"}}>{weeklySummary.message}</p>
            )}
          </div>
          </>
        )}

        {activeTab === "listings" && (
          <>
            <details style={{marginBottom:"16px", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"12px 16px"}}>
              <summary style={{cursor:"pointer", color:"#2F6FED", fontSize:"14px", fontWeight:600}}>❓ Как это работает</summary>
              <div style={{fontSize:"14px", color:"#475467", marginTop:"10px", lineHeight:1.6}}>
                Здесь ваши объявления на Avito. Кнопки сверху — это фильтры («Все / Активные / Неактивные / Черновики») и сортировки («По контактам / конверсии / избранному»).<br/><br/>
                Чтобы <b>создать объявления</b> — нажмите «Создать объявление» или «Выгрузить товары с сайта». Чтобы <b>опубликовать</b> готовое — «Опубликовать на Avito» (Борис соберёт фид сам).<br/><br/>
                Не знаете, с чего начать? Синяя кнопка «С чего начать рекламу» вверху подскажет следующий шаг под ваш аккаунт.
              </div>
            </details>
            {(() => {
              const _acc: any = allAccounts.find((a: any) => a.account_id === currentAccount) || {};
              const _goal = _acc.client_goal || "";
              const _site = _acc.company_website || "";
              let _label = ""; let _hint = ""; let _act: (() => void) | null = null;
              if (draftItems.length > 0) {
                _label = "Опубликовать на Avito";
                _hint = "Готово к публикации: " + draftItems.length;
                _act = () => sendFeedToAvito(currentAccount);
              } else if (items.length === 0) {
                if (_goal === "site" || _site) {
                  _label = "Выгрузить товары с сайта";
                  _hint = _site ? _site : "Борис заберёт каталог и сделает объявления";
                  _act = () => setActiveTab("parser");
                } else {
                  _label = "Загрузить объявления с Avito";
                  _hint = "Борис подтянет то, что уже размещено";
                  _act = () => importActiveFromAvito(currentAccount);
                }
              }
              if (!_act) return null;
              return (
                <div style={{background:"linear-gradient(135deg,#EAF3FF,#F6F7FB)", border:"1px solid #C7DDFF", borderRadius:"14px", padding:"18px 20px", marginBottom:"20px", display:"flex", alignItems:"center", gap:"16px", flexWrap:"wrap"}}>
                  <div style={{flex:1, minWidth:"200px"}}>
                    <div style={{fontWeight:800, fontSize:"17px", color:"#14161A"}}>С чего начать рекламу</div>
                    <div style={{color:"#667085", fontSize:"14px", marginTop:"3px"}}>{_hint}</div>
                  </div>
                  <button className="boris-btn-hover" onClick={_act} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"14px 26px", fontSize:"16px", fontWeight:"bold", cursor:"pointer", whiteSpace:"nowrap"}}>{_label}</button>
                </div>
              );
            })()}
            <div style={{fontSize:"13px", color:"#667085", marginBottom:"10px"}}>Отфильтруйте или отсортируйте объявления. «По контактам / конверсии / избранному» — это сортировки: нажмите, чтобы упорядочить список (повторное нажатие меняет направление ↓↑).</div>
            <div style={{fontSize:"13px", color:"#667085", marginBottom:"10px"}}>Отфильтруйте или отсортируйте объявления. «По контактам / конверсии / избранному» — это сортировки: нажмите, чтобы упорядочить список (повторное нажатие меняет направление ↓↑).</div>
            <div style={{display:"grid", gridTemplateColumns:"repeat(3, 1fr)", gap:"16px", marginBottom:"20px"}}>
              {[
                {key:"all", icon:"📋", label:"Все", grad:"linear-gradient(135deg,#98A2B3,#667085)"},
                {key:"active", icon:"✅", label:"Активные", grad:"linear-gradient(135deg,#3DBE93,#12805C)"},
                {key:"inactive", icon:"❌", label:"Неактивные", grad:"linear-gradient(135deg,#F97066,#F04438)"},
                {key:"drafts", icon:"📝", label:"Черновики" + (draftItems.length > 0 ? ` (${draftItems.length})` : ""), grad:"linear-gradient(135deg,#FDB022,#F79009)"},
                {key:"search", icon:"🔍", label:"Поиск", grad:"linear-gradient(135deg,#4C8DFF,#2F6FED)"},
                {key:"feeds", icon:"📡", label:"Фиды для Avito", grad:"linear-gradient(135deg,#7C5CFC,#5B3FD9)"},
                {key:"templates_lib", icon:"📄", label:"Шаблоны", grad:"linear-gradient(135deg,#F06AA8,#D93D86)"},
                {key:"contacts", icon:"📞", label:"По контактам", grad:"linear-gradient(135deg,#4C8DFF,#2F6FED)", sort:true},
                {key:"conversion", icon:"📈", label:"По конверсии", grad:"linear-gradient(135deg,#3DBE93,#0F9A6E)", sort:true},
                {key:"favorites", icon:"⭐", label:"По избранному", grad:"linear-gradient(135deg,#FDB022,#F79009)", sort:true},
                {key:"dupes", icon:"🆕", label:"Новые дубли" + (duplicateDrafts.length > 0 ? ` (${duplicateDrafts.length})` : ""), grad:"linear-gradient(135deg,#7C5CFC,#5B3FD9)", dupes:true},
                {key:"create", icon:"➕", label:"Создать объявление", grad:"linear-gradient(135deg,#4C8DFF,#2F6FED)", create:true},
                {key:"pipeline", icon:"🚀", label:"Конвейер направления", grad:"linear-gradient(135deg,#12805C,#3DBE93)", pipeline:true},
              ].map((f: any, i: number) => {
                const isOn = f.create || f.pipeline ? false : f.dupes ? showDuplicateDrafts : f.sort ? sortBy === f.key : listingFilter === f.key;
                return (
                <button className="b-card b-stat b-fcard" key={f.key} style={{animationDelay:`${i * 0.05}s`, flexDirection:"column", justifyContent:"center", gap:"12px", minHeight:"124px", cursor:"pointer", background: f.create ? "linear-gradient(135deg,#4C8DFF,#2F6FED)" : f.pipeline ? "linear-gradient(135deg,#12805C,#3DBE93)" : isOn ? "#E7EFFE" : "#FFFFFF", borderColor: f.create ? "#2F6FED" : f.pipeline ? "#12805C" : isOn ? "#2F6FED" : "#E3E7F0"}}
                  onClick={() => {
                    if (f.create) { setActiveTab("templates"); loadTemplateEffectiveness(currentAccount); return; }
                    if (f.pipeline) { setShowPipelineForm(true); return; }
                    if (f.dupes) { setShowDuplicateDrafts(v => !v); if (!showDuplicateDrafts) loadDuplicateDrafts(); return; }
                    if (f.sort) { if (sortBy === f.key) setSortDir(d => d === "desc" ? "asc" : "desc"); else { setSortBy(f.key); setSortDir("desc"); } return; }
                    setListingFilter(f.key);
                    if (f.key === "drafts") loadDrafts();
                    if (f.key === "feeds") loadAllFeeds();
                    if (f.key === "templates_lib") loadTemplateEffectiveness(currentAccount);
                  }}>
                  <span className="b-stat-ico" style={{background: f.grad}}>{f.icon}</span>
                  <span style={{fontSize:"15px", fontWeight: (isOn || f.create) ? 800 : 600, color: f.create ? "#FFFFFF" : isOn ? "#2F6FED" : "#475467"}}>
                    {f.label}{f.sort && sortBy === f.key ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
                  </span>
                </button>
                );
              })}
            </div>
            {selectedItemIds.size > 0 && (
              <div style={{background:"#F6F7FB", border:"1px solid #2F6FED", borderRadius:"8px", padding:"12px 16px", marginBottom:"16px", display:"flex", alignItems:"center", gap:"12px", flexWrap:"wrap"}}>
                <span style={{color:"#2F6FED", fontSize:"15px", fontWeight:"bold"}}>Выбрано: {selectedItemIds.size}</span>
                <button className="boris-btn-hover" onClick={() => bulkAction("template")} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px"}}>📝 Сделать шаблон</button>
                <button className="boris-btn-hover" onClick={() => bulkAction("archive")} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px"}}>📦 Архивировать</button>
                <button className="boris-btn-hover" onClick={() => bulkAction("delete")} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px"}}>🗑 Удалить</button>
                <button className="boris-btn-hover" onClick={clearItemSelection} style={{background:"#FFFFFF", border:"none", color:"#8A93A6", fontSize:"15px", cursor:"pointer", marginLeft:"auto"}}>Снять выбор</button>
              </div>
            )}

            {!duplicateEditingItem && !showDuplicateDrafts && (
            <>
            {listingFilter === "templates_lib" && (
              <div>
                <h3 style={{margin:"0 0 6px", fontSize:"18px", color:"#1D2939"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", width:"30px", height:"30px", fontSize:"15px", display:"inline-flex", alignItems:"center", justifyContent:"center", borderRadius:"50%", flexShrink:0}}>📋</span>Библиотека шаблонов</h3>
                <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Про сохранённые тексты</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Сохранённые тексты объявлений. Борис отслеживает эффективность каждого по реальным лидам. Сохранить новый шаблон можно кнопкой «💾 Сохранить как шаблон» под сгенерированным объявлением.</p></details>
                {templateEff.length === 0 ? (
                  <div style={{background:"#F6F7FB", border:"1px dashed #D0D5DD", borderRadius:"12px", padding:"32px", textAlign:"center", color:"#98A2B3"}}>
                    Пока нет сохранённых шаблонов. Сгенерируйте объявление и нажмите «💾 Сохранить как шаблон».
                  </div>
                ) : (
                  <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(320px, 1fr))", gap:"16px"}}>
                    {templateEff.map((t: any) => {
                      const st = t.status === "effective" ? {emoji:"🟢", label:"Эффективный", color:"#12805C", bg:"#E7F7F0"}
                        : t.status === "ineffective" ? {emoji:"🔴", label:"Неэффективный", color:"#D92D20", bg:"#FEF0EF"}
                        : t.status === "in_progress" ? {emoji:"🟡", label:"В процессе", color:"#B54708", bg:"#FEF7EC"}
                        : {emoji:"⚪", label:"Нет данных", color:"#667085", bg:"#F2F4F7"};
                      return (
                        <div key={t.id} style={{background:"#FFFFFF", border:"1px solid #EEF2FA", borderRadius:"12px", padding:"18px"}}>
                          <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:"8px", marginBottom:"10px"}}>
                            <div style={{fontWeight:"bold", fontSize:"16px", color:"#1D2939"}}>{t.name}</div>
                            <button className="boris-btn-hover" onClick={() => deleteTextTemplate(t.id)} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"4px 8px", cursor:"pointer", fontSize:"13px"}}>🗑</button>
                          </div>
                          <div style={{display:"inline-block", background:st.bg, color:st.color, borderRadius:"6px", padding:"3px 10px", fontSize:"13px", fontWeight:"bold", marginBottom:"12px"}}>{st.emoji} {st.label}</div>
                          <div style={{display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"6px", marginBottom:"12px"}}>
                            <div style={{textAlign:"center", background:"#F9FAFB", borderRadius:"6px", padding:"6px"}}>
                              <div style={{color:"#98A2B3", fontSize:"12px"}}>Просмотры</div>
                              <div style={{fontWeight:"bold", color:"#1D2939"}}>{t.views}</div>
                            </div>
                            <div style={{textAlign:"center", background:"#F9FAFB", borderRadius:"6px", padding:"6px"}}>
                              <div style={{color:"#98A2B3", fontSize:"12px"}}>Контакты</div>
                              <div style={{fontWeight:"bold", color:"#1D2939"}}>{t.contacts}</div>
                            </div>
                            <div style={{textAlign:"center", background:"#F9FAFB", borderRadius:"6px", padding:"6px"}}>
                              <div style={{color:"#98A2B3", fontSize:"12px"}}>Конверсия</div>
                              <div style={{fontWeight:"bold", color:"#2F6FED"}}>{t.conversion}%</div>
                            </div>
                          </div>
                          <div style={{color:"#98A2B3", fontSize:"12px"}}>Объявлений с этим шаблоном: {t.items_count}</div>
                          <div style={{color:"#667085", fontSize:"13px", marginTop:"8px", maxHeight:"60px", overflow:"hidden", lineHeight:1.4}}>{t.title_template}</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
            {listingFilter === "feeds" && (
              <div style={{marginTop:"16px"}}>
                <div style={{color:"#1D2939", fontWeight:"bold", fontSize:"18px", marginBottom:"6px"}}>📡 Фиды для Avito — этот аккаунт</div>
                <div style={{fontSize:"13px", color:"#667085", marginBottom:"12px"}}>Фид — это файл со всеми вашими объявлениями, который Avito автоматически забирает и публикует. Настраивать вручную не нужно: Борис собирает и обновляет его сам.</div>
                {feedsLoading ? <div style={{color:"#667085"}}>Загрузка...</div> : (
                  <div style={{display:"flex", flexDirection:"column", gap:"12px"}}>
                    {allFeeds.map((feed, i) => (
                      <div key={i} style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"16px", boxShadow:"0 1px 3px rgba(16,24,40,0.06)"}}>
                        <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", flexWrap:"wrap", gap:"10px"}}>
                          <div style={{flex:"1 1 300px"}}>
                            <div style={{fontWeight:"bold", fontSize:"16px", color:"#1D2939", marginBottom:"4px"}}>{feed.name}</div>
                            <div style={{fontSize:"14px", color:"#667085", marginBottom:"2px"}}>📦 Объявлений: <b style={{color:"#2F6FED"}}>{feed.count}</b> · Городов: {feed.cities_count}</div>
                            <div style={{fontSize:"14px", color:"#667085", marginBottom:"2px"}}>📁 Категория: {(feed.categories || []).join(", ")}</div>
                            <div style={{fontSize:"12px", color:"#8A93A6", wordBreak:"break-all", marginTop:"6px"}}>{feed.feed_url}</div>
                          </div>
                          <div style={{display:"flex", flexDirection:"column", gap:"6px", minWidth:"160px"}}>
                            <button className="boris-btn-hover" onClick={() => copyFeedUrl(feed.feed_url)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>📋 Копировать ссылку</button>
                            <button className="boris-btn-hover" onClick={checkFeedValidation} disabled={feedCheckLoading} style={{background: feedCheckLoading ? "#C9D2E3" : "#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"8px 14px", cursor: feedCheckLoading ? "default" : "pointer", fontSize:"14px", fontWeight:"bold"}}>{feedCheckLoading ? "⏳ Проверяю..." : "✅ Проверить фид"}</button>
                            <button className="boris-btn-hover" onClick={() => checkFeed(feed.feed_url)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"14px"}} title="Скопировать ссылку и открыть валидатор Avito вручную">🔗 Проверить на Avito</button>
                            <button className="boris-btn-hover" onClick={() => sendFeedToAvito(feed.account_id)} style={{background:"#12805C", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>🚀 Отправить в Avito</button>
                            <button className="boris-btn-hover" onClick={() => { if (openedFeedAccount === feed.account_id) { setOpenedFeedAccount(null); } else { setFeedTab("feed"); loadFeedItemsFull(feed.account_id); loadActiveItems(feed.account_id); } }} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>✏️ Редактировать объявления</button>
                            <button className="boris-btn-hover" onClick={() => exportFeedXlsx(feed.account_id)} style={{background:"#FFFFFF", color:"#12805C", border:"1px solid #C7EBDC", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>📥 Выгрузить в Excel</button>
                            <label className="boris-btn-hover" style={{background:"#FFFFFF", color:"#12805C", border:"1px solid #C7EBDC", borderRadius:"8px", padding:"8px 14px", cursor:"pointer", fontSize:"15px", fontWeight:"bold", textAlign:"center", display:"block"}}>📤 Загрузить из Excel<input type="file" accept=".xlsx" style={{display:"none"}} onChange={e => { if (e.target.files && e.target.files[0]) importFeedXlsx(feed.account_id, e.target.files[0]); }} /></label>
                            <button className="boris-btn-hover" onClick={() => importActiveFromAvito(feed.account_id)} style={{background:"#FFFFFF", color:"#B54708", border:"1px solid #FEDF89", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>📥 Выгрузить активные с Avito</button>
                          </div>
                        </div>
                        {openedFeedAccount === feed.account_id && (
                          <div style={{marginTop:"16px", borderTop:"1px solid #E3E7F0", paddingTop:"16px"}}>
                            <div style={{display:"flex", gap:"8px", marginBottom:"14px"}}>
                              <button className="boris-btn-hover" onClick={() => setFeedTab("feed")} style={{background: feedTab === "feed" ? "#2F6FED" : "#FFFFFF", color: feedTab === "feed" ? "#FFFFFF" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>📤 Мой фид ({feedItemsFull.length})</button>
                              <button className="boris-btn-hover" onClick={() => { setFeedTab("active"); loadActiveItems(feed.account_id); }} style={{background: feedTab === "active" ? "#2F6FED" : "#FFFFFF", color: feedTab === "active" ? "#FFFFFF" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>📥 Активные на Avito ({activeItems.length})</button>
                              <button className="boris-btn-hover" onClick={() => setFeedTab("schedule")} style={{background: feedTab === "schedule" ? "#2F6FED" : "#FFFFFF", color: feedTab === "schedule" ? "#FFFFFF" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>🕐 Расписание</button>
                            </div>
                            {feedTab === "schedule" && (
                              <div className="b-panel b-card-eq" style={{marginBottom:"16px"}}>
                                <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #98A2B3, #667085)"}}>🕐</span>Расписание публикаций</h3>

                                <div style={{background:"#FFFAEB", border:"1px solid #FEC84B", borderRadius:"10px", padding:"14px", marginBottom:"18px", fontSize:"14px", color:"#B54708"}}>
                                  <b>Автозапуск по расписанию пока не подключён.</b><br/>
                                  Ежедневная публикация «сама по себе» в разработке. Сейчас доступно разовое планирование пачки — объявления уйдут на Avito с указанной даты и интервалом.
                                </div>

                                <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(160px, 1fr))", gap:"14px", marginBottom:"16px"}}>
                                  <div>
                                    <div className="b-label">Дата старта</div>
                                    <input type="date" value={bulkStartDate} onChange={e => setBulkStartDate(e.target.value)} style={{width:"100%", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"8px", fontSize:"15px"}} />
                                  </div>
                                  <div>
                                    <div className="b-label">Время старта</div>
                                    <input type="time" value={bulkStartTime} onChange={e => setBulkStartTime(e.target.value)} style={{width:"100%", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"8px", fontSize:"15px"}} />
                                  </div>
                                  <div>
                                    <div className="b-label">Интервал, мин</div>
                                    <input type="number" value={bulkInterval} onChange={e => setBulkInterval(Number(e.target.value))} style={{width:"100%", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"8px", fontSize:"15px"}} />
                                  </div>
                                </div>

                                <p style={{color:"#667085", fontSize:"14px", marginBottom:"14px"}}>Интервал между объявлениями нужен, чтобы Avito не увидел массовую загрузку — публикация растягивается во времени.</p>

                                <button className="boris-btn-hover" onClick={applyBulkSchedule} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"10px 18px", cursor:"pointer", fontWeight:"bold", fontSize:"15px"}}>🕐 Применить к выбранным</button>
                              </div>
                            )}

                            {feedTab !== "schedule" && (feedTab === "feed" ? (
                              feedItemsLoading ? <div style={{color:"#667085"}}>Загрузка объявлений...</div> : (
                              <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                                <div style={{fontSize:"13px", color:"#8A93A6", marginBottom:"4px"}}>Объявления, которые Борис создал и отдаёт на автозагрузку Avito.</div>
                                {feedItemsFull.map((it, k) => (
                                  <div key={k} style={{display:"flex", justifyContent:"space-between", alignItems:"center", gap:"10px", background:"#F6F7FB", borderRadius:"8px", padding:"10px 14px", flexWrap:"wrap"}}>
                                    <div style={{flex:"1 1 300px", minWidth:0}}>
                                      <div style={{fontSize:"14px", color:"#1D2939", fontWeight:"600", overflow:"hidden", textOverflow:"ellipsis"}}>{it.title}</div>
                                      <div style={{fontSize:"13px", color:"#667085"}}>{it.price} ₽ · {it.address}</div>
                                    </div>
                                    <div style={{display:"flex", gap:"6px"}}>
                                      <button className="boris-btn-hover" onClick={() => { setEditingFeedItem({...it}); loadFeedPhotoGallery(feed.account_id); loadTextTemplates(feed.account_id); }} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"13px"}}>✏️ Править</button>
                                      <button className="boris-btn-hover" onClick={() => deleteFeedItem(feed.account_id, it.id)} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"13px"}}>🗑</button>
                                    </div>
                                  </div>
                                ))}
                                {feedItemsFull.length === 0 && <div style={{color:"#667085"}}>Нет объявлений в фиде</div>}
                              </div>
                              )
                            ) : (
                              activeLoading ? <div style={{color:"#667085"}}>Загрузка активных...</div> : (
                              <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                                <details style={{marginBottom:"14px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Подробнее</summary><div style={{fontSize:"13px", color:"#8A93A6", marginBottom:"4px"}}>Объявления, которые уже опубликованы и живут на Avito (включая старые). Нажми «📥 Выгрузить активные с Avito» на карточке, чтобы обновить список.</div></details>
                                {activeItems.map((it, k) => (
                                  <div key={k} style={{display:"flex", justifyContent:"space-between", alignItems:"center", gap:"10px", background:"#FFF7ED", borderRadius:"8px", padding:"10px 14px", flexWrap:"wrap", border:"1px solid #FEDF89"}}>
                                    <div style={{flex:"1 1 300px", minWidth:0}}>
                                      <div style={{fontSize:"14px", color:"#1D2939", fontWeight:"600", overflow:"hidden", textOverflow:"ellipsis"}}>{it.title}</div>
                                      <div style={{fontSize:"13px", color:"#667085"}}>{it.price} ₽ · {it.address}</div>
                                    </div>
                                    <div style={{display:"flex", gap:"6px"}}>
                                      {it.avito_url && <a href={it.avito_url} target="_blank" rel="noreferrer" style={{textDecoration:"none", background:"#FFFFFF", color:"#B54708", border:"1px solid #FEDF89", borderRadius:"6px", padding:"6px 12px", fontSize:"13px"}}>🔗 На Avito</a>}
                                      <button className="boris-btn-hover" onClick={() => { setEditingFeedItem({...it}); loadFeedPhotoGallery(feed.account_id); }} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"13px"}}>✏️ Править</button>
                                    </div>
                                  </div>
                                ))}
                                {activeItems.length === 0 && <div style={{color:"#667085"}}>Активных объявлений пока нет. Нажми «📥 Выгрузить активные с Avito».</div>}
                              </div>
                              )
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                    {allFeeds.length === 0 && <div style={{color:"#667085"}}>Фиды не найдены</div>}
                  </div>
                )}
              </div>
            )}
            {feedCheckResult && (
              <div onClick={() => setFeedCheckResult(null)} style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.5)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:3500, padding:"20px"}}>
                <div onClick={e => e.stopPropagation()} style={{background:"#FFFFFF", borderRadius:"16px", padding:"24px", maxWidth:"700px", width:"100%", maxHeight:"80vh", overflowY:"auto", boxShadow:"0 12px 40px rgba(16,24,40,0.2)"}}>
                  <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"12px"}}>
                    <div style={{fontSize:"18px", fontWeight:"bold", color:"#1D2939"}}>
                      {feedCheckResult.status === "ok" ? "✅ Фид корректен" : feedCheckResult.status === "errors" ? "⚠️ Найдены ошибки" : "❌ Ошибка проверки"}
                    </div>
                    <button onClick={() => setFeedCheckResult(null)} style={{background:"#FFFFFF", border:"none", fontSize:"24px", cursor:"pointer", color:"#2F6FED"}}>×</button>
                  </div>
                  <div style={{fontSize:"13px", color:"#475467", whiteSpace:"pre-line", lineHeight:"1.6", fontFamily:"monospace", background:"#F9FAFB", borderRadius:"8px", padding:"14px"}}>
                    {feedCheckResult.raw_text || "Нет данных"}
                  </div>
                </div>
              </div>
            )}
{addPhotoDraftId && (
              <div onClick={() => setAddPhotoDraftId(null)} style={{position:"fixed", inset:0, background:"rgba(0,0,0,0.6)", zIndex:9999, display:"flex", alignItems:"center", justifyContent:"center"}}>
                <div onClick={e => e.stopPropagation()} style={{background:"#fff", borderRadius:"12px", padding:"20px", maxWidth:"760px", maxHeight:"80vh", overflowY:"auto", width:"90%"}}>
                  <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"14px"}}>
                    <span style={{fontSize:"17px", fontWeight:"bold", color:"#1D2939"}}>Выберите фото для добавления</span>
                    <button onClick={() => setAddPhotoDraftId(null)} style={{background:"none", border:"none", fontSize:"22px", cursor:"pointer", color:"#667085"}}>×</button>
                  </div>
                  {Object.keys(accountPhotos).length === 0 && <p style={{color:"#667085"}}>Нет загруженных фото у аккаунта.</p>}
                  {Object.entries(accountPhotos).map(([folder, imgs]: [string, any]) => (
                    <div key={folder} style={{marginBottom:"16px"}}>
                      <div style={{fontSize:"14px", fontWeight:"bold", color:"#7C5CFC", marginBottom:"8px"}}>{folder} ({imgs.length})</div>
                      <div style={{display:"flex", flexWrap:"wrap", gap:"8px"}}>
                        {imgs.map((img: string, i: number) => (
                          <img key={i} src={img.startsWith("http") ? img : ("https://boris-ai.pro" + img)} alt="" onClick={() => addPhotoToDraft(img)} style={{width:"72px", height:"72px", objectFit:"cover", borderRadius:"8px", border:"1px solid #E3E7F0", cursor:"pointer"}} title="Нажмите чтобы добавить" />
                        ))}
                      </div>
                    </div>
                  ))}
                  <p style={{fontSize:"13px", color:"#8A93A6", marginTop:"8px"}}>Нажмите на фото — оно добавится в объявление. Окно можно закрыть крестиком.</p>
                </div>
              </div>
            )}
            {lightboxImage && (
              <div onClick={() => setLightboxImage(null)} style={{position:"fixed", inset:0, background:"rgba(10,14,20,0.92)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:3000, padding:"30px", cursor:"zoom-out"}}>
                <button onClick={() => setLightboxImage(null)} style={{position:"absolute", top:"20px", right:"30px", background:"#FFFFFF", border:"none", color:"#fff", fontSize:"32px", cursor:"pointer"}}>×</button>
                {lightboxGallery.length > 1 && (
                  <button onClick={e => { e.stopPropagation(); const ni = (lightboxIdx - 1 + lightboxGallery.length) % lightboxGallery.length; setLightboxIdx(ni); setLightboxImage(lightboxGallery[ni]); }}
                    style={{position:"absolute", left:"20px", background:"rgba(255,255,255,0.15)", border:"none", color:"#fff", fontSize:"28px", cursor:"pointer", borderRadius:"50%", width:"44px", height:"44px"}}>‹</button>
                )}
                <img src={lightboxImage} alt="Просмотр" style={{maxWidth:"90vw", maxHeight:"90vh", objectFit:"contain", borderRadius:"8px"}} onClick={e => e.stopPropagation()} />
                {lightboxGallery.length > 1 && (
                  <button onClick={e => { e.stopPropagation(); const ni = (lightboxIdx + 1) % lightboxGallery.length; setLightboxIdx(ni); setLightboxImage(lightboxGallery[ni]); }}
                    style={{position:"absolute", right:"20px", background:"rgba(255,255,255,0.15)", border:"none", color:"#fff", fontSize:"28px", cursor:"pointer", borderRadius:"50%", width:"44px", height:"44px"}}>›</button>
                )}
                {lightboxGallery.length > 1 && <div style={{position:"absolute", bottom:"20px", color:"#fff", fontSize:"13px"}}>{lightboxIdx + 1} / {lightboxGallery.length}</div>}
              </div>
            )}
            {editingFeedItem && (
              <div style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.5)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:1000, padding:"20px"}} onClick={() => setEditingFeedItem(null)}>
                <div style={{background:"#FFFFFF", borderRadius:"16px", padding:"24px", maxWidth:"600px", width:"100%", maxHeight:"85vh", overflowY:"auto"}} onClick={e => e.stopPropagation()}>
                  <div style={{fontSize:"18px", fontWeight:"bold", color:"#2F6FED", marginBottom:"16px"}}>✏️ Редактирование объявления в фиде</div>
                  <label style={{fontSize:"15px", color:"#667085", display:"block", marginBottom:"4px"}}>📋 Использовать шаблон (необязательно)</label>
                  <select className="b-select" value={editingFeedItem.template_id || ""} onChange={e => applyTextTemplate(e.target.value)} style={{width:"100%", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px 14px", fontSize:"14px", marginBottom:"14px", boxSizing:"border-box", background:"#FFFFFF"}}>
                    <option value="">— Написать своё описание —</option>
                    {textTemplates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                  <label style={{fontSize:"15px", color:"#667085", display:"block", marginBottom:"4px"}}>Заголовок</label>
                  <input value={editingFeedItem.title} onChange={e => setEditingFeedItem({...editingFeedItem, title: e.target.value})} style={{width:"100%", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px 14px", fontSize:"15px", marginBottom:"14px", boxSizing:"border-box"}} />
                  <label style={{fontSize:"15px", color:"#667085", display:"block", marginBottom:"4px"}}>Цена</label>
                  <input type="number" value={editingFeedItem.price} onChange={e => setEditingFeedItem({...editingFeedItem, price: e.target.value})} style={{width:"180px", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px 14px", fontSize:"15px", marginBottom:"14px", boxSizing:"border-box"}} />
                  <label style={{fontSize:"15px", color:"#667085", display:"block", marginBottom:"4px"}}>Описание</label>
                  <div style={{display:"flex", gap:"4px", marginBottom:"6px", flexWrap:"wrap", position:"relative"}}>
                    <button className="boris-btn-hover" onClick={() => feedWrap("<strong>", "</strong>", "жирный")} title="Жирный" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"5px 11px", cursor:"pointer", fontWeight:"bold", fontSize:"14px"}}>Ж</button>
                    <button className="boris-btn-hover" onClick={() => feedWrap("<i>", "</i>", "курсив")} title="Курсив" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"5px 11px", cursor:"pointer", fontStyle:"italic", fontSize:"14px"}}>К</button>
                    <button className="boris-btn-hover" onClick={() => feedInsert("\n1. \n2. \n3. ")} title="Нумерованный список" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"5px 11px", cursor:"pointer", fontSize:"14px"}}>1.</button>
                    <button className="boris-btn-hover" onClick={() => feedInsert("\n• \n• \n• ")} title="Список точками" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"5px 11px", cursor:"pointer", fontSize:"14px"}}>•</button>
                    <button className="boris-btn-hover" onClick={() => feedInsert("\n— \n— \n— ")} title="Список чёрточками" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"5px 11px", cursor:"pointer", fontSize:"14px"}}>—</button>
                    <button className="boris-btn-hover" onClick={() => feedInsert("{вариант1|вариант2}")} title="Спинтакс уникализации" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"5px 11px", cursor:"pointer", fontSize:"14px", fontFamily:"monospace"}}>{"{|}"}</button>
                    <button className="boris-btn-hover" onClick={() => setFeedEmojiOpen(v => !v)} title="Эмодзи" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"5px 11px", cursor:"pointer", fontSize:"14px"}}>😀</button>
                    {feedEmojiOpen && (
                      <div style={{position:"absolute", top:"38px", left:0, zIndex:10, background:"#FFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"8px", display:"flex", flexWrap:"wrap", gap:"4px", maxWidth:"320px", boxShadow:"0 4px 12px rgba(16,24,40,0.12)"}}>
                        {["📞","💬","✅","⭐","🔥","💰","🎁","📍","🚀","👍","❤️","🏆","⏰","📦","🛠","🔧","💡","🌟","✨","👉"].map((em, i) => (
                          <button key={i} onClick={() => { feedInsert(em); setFeedEmojiOpen(false); }} style={{background:"#FFFFFF", border:"none", cursor:"pointer", fontSize:"20px", padding:"2px"}}>{em}</button>
                        ))}
                      </div>
                    )}
                  </div>
                  <textarea ref={feedDescRef} value={editingFeedItem.description} onChange={e => setEditingFeedItem({...editingFeedItem, description: e.target.value})} rows={10} style={{width:"100%", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px 14px", fontSize:"14px", fontFamily:"inherit", marginBottom:"16px", boxSizing:"border-box"}} />
                  <label style={{fontSize:"15px", color:"#667085", display:"block", marginBottom:"6px"}}>📷 Фото объявления</label>
                  <div style={{display:"flex", flexWrap:"wrap", gap:"8px", marginBottom:"10px"}}>
                    {(editingFeedItem.images || []).map((url: string, idx: number) => (
                      <div key={idx} style={{position:"relative", width:"70px", height:"70px"}}>
                        <img src={url} style={{width:"70px", height:"70px", objectFit:"cover", borderRadius:"8px", border:"1px solid #E3E7F0"}} />
                        <button onClick={() => removeFeedPhoto(idx)} style={{position:"absolute", top:"-6px", right:"-6px", background:"#FFFFFF", color:"#FFF", border:"1.5px solid #F04438", borderRadius:"50%", width:"20px", height:"20px", cursor:"pointer", fontSize:"12px", lineHeight:"1"}}>×</button>
                      </div>
                    ))}
                    {(!editingFeedItem.images || editingFeedItem.images.length === 0) && <span style={{color:"#8A93A6", fontSize:"13px"}}>Фото нет</span>}
                  </div>
                  <div style={{display:"flex", gap:"6px", marginBottom:"16px", flexWrap:"wrap"}}>
                    <label className="boris-btn-hover" style={{background:"#FFFFFF", color:"#2F6FED", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 12px", cursor:"pointer", fontSize:"15px"}}>📁 С компьютера<input type="file" accept="image/*" multiple style={{display:"none"}} onChange={e => { if (e.target.files && openedFeedAccount) uploadFeedPhoto(openedFeedAccount, e.target.files); }} /></label>
                    <button className="boris-btn-hover" onClick={() => setFeedPhotoPickerOpen(feedPhotoPickerOpen === "banners" ? null : "banners")} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"13px"}}>🎨 Из баннеров ({feedPhotoGallery.banners.length})</button>
                    <button className="boris-btn-hover" onClick={() => setFeedPhotoPickerOpen(feedPhotoPickerOpen === "images" ? null : "images")} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"13px"}}>🖼 Из картинок ({feedPhotoGallery.images.length})</button>
                  </div>
                  {feedPhotoPickerOpen && (
                    <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(70px, 1fr))", gap:"6px", maxHeight:"200px", overflowY:"auto", marginBottom:"16px", padding:"8px", background:"#F6F7FB", borderRadius:"8px"}}>
                      {(feedPhotoPickerOpen === "banners" ? feedPhotoGallery.banners : feedPhotoGallery.images).map((url, k) => (
                        <img key={k} src={url} onClick={() => addFeedPhoto(url)} style={{width:"100%", height:"70px", objectFit:"cover", borderRadius:"6px", cursor:"pointer", border:"1px solid #E3E7F0"}} title="Нажми чтобы добавить" />
                      ))}
                      {(feedPhotoPickerOpen === "banners" ? feedPhotoGallery.banners : feedPhotoGallery.images).length === 0 && <span style={{color:"#8A93A6", fontSize:"13px"}}>Пусто</span>}
                    </div>
                  )}
                  <div style={{display:"flex", gap:"10px"}}>
                    <button className="boris-btn-hover" onClick={saveFeedItem} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"10px 20px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>✅ Сохранить в фид</button>
                    <button className="boris-btn-hover" onClick={() => setEditingFeedItem(null)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"10px 20px", cursor:"pointer", fontSize:"15px"}}>Отмена</button>
                  </div>
                </div>
              </div>
            )}
            {listingFilter === "search" && (
              <input placeholder="🔍 Введите название объявления..." value={search} onChange={e => setSearch(e.target.value)}
                style={{width:"100%", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"12px 16px", color:"#1D2939", fontSize:"23px", marginBottom:"16px", boxSizing:"border-box"}} />
            )}

            {false && borisFeedItems.length > 0 && (
              <div style={{background:"#E7EFFE", border:"1px solid #E7EFFE", borderRadius:"10px", padding:"16px", marginBottom:"20px"}}>
                <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"10px"}}>
                  <div style={{color:"#1E4FBF", fontWeight:"bold", fontSize:"23px"}}>Объявления Бориса в фиде: {borisFeedItems.length}</div>
                  <button className="boris-btn-hover" onClick={() => loadBorisFeedItems()} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"4px 10px", cursor:"pointer", fontSize:"15px"}}>🔄 Обновить</button>
                </div>
                <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"10px"}}>
                  Это объявления, уже готовые и сохранённые в фиде Бориса — они появятся выше как реальные объявления Avito, как только площадка обработает автозагрузку (обычно в течение часа).
                </div>
                <div style={{maxHeight:"200px", overflowY:"auto", display:"flex", flexDirection:"column", gap:"6px"}}>
                  {borisFeedItems.slice(0, 20).map((it: any, i: number) => (
                    <div key={i} style={{fontSize:"15px", color:"#667085", padding:"6px 10px", background:"#F6F7FB", borderRadius:"6px"}}>
                      {it.title} — {it.price}₽ — {it.address} — <span style={{color:"#667085"}}>{it.category}</span>
                    </div>
                  ))}
                  {borisFeedItems.length > 20 && (
                    <div style={{fontSize:"15px", color:"#8A93A6", textAlign:"center", padding:"4px"}}>...и ещё {borisFeedItems.length - 20}</div>
                  )}
                </div>
              </div>
            )}

            {listingFilter === "feeds" || listingFilter === "templates_lib" ? null : listingFilter === "drafts" ? (
              <>
                {draftsLoading ? <p style={{color:"#667085"}}>Загружаю черновики...</p> : draftItems.length === 0 ? (
                  <p style={{color:"#667085", fontSize:"15px", background:"#F5FAFF", borderRadius:"8px", padding:"20px", textAlign:"center"}}>📭 Черновиков нет. Дайте Борису задачу в режиме черновика — они появятся здесь.</p>
                ) : (
                  <>
                    {draftBatchFilter && (
                      <div style={{display:"flex", alignItems:"center", gap:"10px", background:"#F0ECFF", border:"1px solid #7C5CFC", borderRadius:"8px", padding:"10px 14px", marginBottom:"14px"}}>
                        <span style={{fontSize:"14px", color:"#5B3FD9"}}>Показаны только результаты партии «{draftBatchFilter}»</span>
                        <button className="boris-btn-hover" onClick={() => setDraftBatchFilter(null)} style={{marginLeft:"auto", background:"#FFFFFF", color:"#5B3FD9", border:"1px solid #7C5CFC", borderRadius:"10px", padding:"4px 10px", fontSize:"13px", cursor:"pointer"}}>Показать все</button>
                      </div>
                    )}
                    {(() => { const visibleDrafts = draftBatchFilter ? draftItems.filter((d: any) => d.batch_label === draftBatchFilter) : draftItems; return (
                    <>
                    <div style={{display:"flex", flexWrap:"wrap", justifyContent:"space-between", alignItems:"center", marginBottom:"14px", gap:"10px"}}>
                      <div style={{flexBasis:"100%", width:"100%", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"12px 14px", marginBottom:"4px", display:"flex", flexWrap:"wrap", gap:"10px", alignItems:"center"}}>
                        <span style={{fontSize:"15px", fontWeight:"bold", color:"#1D2939"}}>📍 Публикация:</span>
                        <input placeholder="Город (напр. Москва)" value={selectedCity} onChange={e=>{setSelectedCity(e.target.value); setBulkCities([]); setBulkMetro([]);}} style={{border:"1px solid #E3E7F0", borderRadius:"8px", padding:"6px 10px", fontSize:"15px", width:"170px"}} />
                        {metroData[selectedCity] && metroData[selectedCity].length>0 && (
                          <button className="boris-btn-hover" onClick={selectAllMetroInCity} style={{background: bulkMetro.length>0 ? "#E7EFFE" : "#FFFFFF", color: bulkMetro.length>0 ? "#2F6FED" : "#667085", border:"1px solid #2F6FED", borderRadius:"8px", padding:"6px 12px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>🚇 Раскидать по районам {bulkMetro.length>0 ? `(${bulkMetro.length})` : `(${metroData[selectedCity].length} станций)`}</button>
                        )}
                        <span style={{fontSize:"14px", color:"#8A93A6"}}>|</span>
                        <label style={{fontSize:"14px", color:"#667085"}}>📅 Дата:</label>
                        <input type="date" value={publishDate} onChange={e=>setPublishDate(e.target.value)} style={{border:"1px solid #E3E7F0", borderRadius:"8px", padding:"6px 10px", fontSize:"15px"}} />
                        <label style={{fontSize:"14px", color:"#667085"}}>🕐 Время:</label>
                        <input type="time" value={publishTime} onChange={e=>setPublishTime(e.target.value)} style={{border:"1px solid #E3E7F0", borderRadius:"8px", padding:"6px 10px", fontSize:"15px"}} />
                        <label style={{fontSize:"14px", color:"#667085"}}>🌍 Пояс:</label>
                        <select value={publishTimezone} onChange={e=>setPublishTimezone(e.target.value)} style={{border:"1px solid #E3E7F0", borderRadius:"8px", padding:"6px 10px", fontSize:"15px"}}>
                          {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
                        </select>
                        {(bulkMetro.length>0 || bulkCities.length>0) && <span style={{fontSize:"13px", color:"#12B76A", fontWeight:"bold"}}>✓ адреса раскинутся по {bulkMetro.length>0 ? "районам" : "городам"}</span>}
                      </div>
                      <div style={{color:"#667085", fontSize:"15px"}}>Черновиков: {visibleDrafts.length}</div>
                      <button className="boris-btn-hover" onClick={() => publishAllDrafts()} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"11px 20px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>✅ Опубликовать все ({draftItems.length})</button>
                      <button className="boris-btn-hover" onClick={checkFeedValidation} disabled={feedCheckLoading}
                        style={{background:"#7C5CFC", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"8px 14px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>
                        🔍 {feedCheckLoading ? "Проверяю (до минуты)..." : "Проверить фид"}
                      </button>
                      <button className="boris-btn-hover" onClick={applyPhotosToDrafts} disabled={draftPhotoLoading}
                        style={{background:"#12B76A", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"8px 14px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>
                        🖼 {draftPhotoLoading ? "Добавляю фото..." : "Добавить фото/баннеры"}
                      </button>
                    </div>
                    <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(280px, 1fr))", gap:"12px"}}>
                      {visibleDrafts.map((d: any) => (
                        <div key={d.id} className="boris-card-hover" style={{background:"#FFFFFF", borderRadius:"10px", padding:"14px", border:"1px solid #EEF2FA", boxShadow:"0 1px 3px rgba(16,24,40,0.06)", display:"flex", flexDirection:"column"}}>
                          <div style={{fontSize:"15px", color:"#7C5CFC", fontWeight:"bold", marginBottom:"6px"}}>{d.batch_label || "Без партии"}</div>
                          {editingDraftId === d.id ? (
                            <>
                              <input defaultValue={d.title} id={`draft-title-${d.id}`} style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"15px", marginBottom:"6px", fontWeight:"bold"}} />
                              <textarea defaultValue={d.description} id={`draft-desc-${d.id}`} rows={10} style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"15px", marginBottom:"6px", fontFamily:"inherit", resize:"vertical", minHeight:"160px"}} />
                              <input type="number" defaultValue={d.price} id={`draft-price-${d.id}`} style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"15px", marginBottom:"8px"}} />
                              <div style={{display:"flex", gap:"6px"}}>
                                <button className="boris-btn-hover" onClick={() => {
                                    const title = (document.getElementById(`draft-title-${d.id}`) as HTMLInputElement)?.value;
                                    const description = (document.getElementById(`draft-desc-${d.id}`) as HTMLTextAreaElement)?.value;
                                    const price = Number((document.getElementById(`draft-price-${d.id}`) as HTMLInputElement)?.value);
                                    saveDraftEdit(d.id, { title, description, price });
                                  }} style={{flex:1, background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"6px", padding:"6px", fontSize:"15px", cursor:"pointer", fontWeight:"bold"}}>Сохранить</button>
                                <button className="boris-btn-hover" onClick={() => setEditingDraftId(null)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px", fontSize:"15px", cursor:"pointer"}}>Отмена</button>
                              </div>
                            </>
                          ) : (
                            <>
                              <div style={{fontSize:"15px", fontWeight:"bold", marginBottom:"6px", color:"#1D2939"}}>{d.title}</div>
                              {d.images && d.images.length > 0 && (
                                <div style={{marginBottom:"8px"}}>
                                  <div style={{display:"flex", gap:"4px", flexWrap:"wrap", alignItems:"center"}}>
                                    {d.images.map((img: string, imgIdx: number) => (
                                      <div key={imgIdx} draggable onDragStart={() => setDragPhoto({draftId: d.id, idx: imgIdx})} onDragOver={e => e.preventDefault()} onDrop={() => { if (dragPhoto && dragPhoto.draftId === d.id) { reorderDraftImages(d.id, dragPhoto.idx, imgIdx); setDragPhoto(null); } }} style={{position:"relative", width:"54px", height:"54px", cursor:"grab"}}>
                                        <img src={img} alt="" onClick={() => { setLightboxGallery(d.images); setLightboxIdx(imgIdx); setLightboxImage(img); }} style={{width:"54px", height:"54px", objectFit:"cover", borderRadius:"6px", border: imgIdx===0 ? "2px solid #12B76A" : "1px solid #E3E7F0", cursor:"pointer"}} title={imgIdx===0 ? "Главное фото (баннер) — перетащите фото чтобы изменить порядок" : "Клик = увеличить · перетащите чтобы изменить порядок"} />
                                        <button onClick={() => removeDraftImage(d.id, imgIdx)} style={{position:"absolute", top:"-6px", right:"-6px", width:"18px", height:"18px", borderRadius:"50%", background:"#F04438", color:"#fff", border:"none", fontSize:"11px", cursor:"pointer", lineHeight:"1", display:"flex", alignItems:"center", justifyContent:"center"}} title="Удалить фото">×</button>
                                      </div>
                                    ))}
                                  </div>
                                  <div style={{display:"flex", gap:"8px", alignItems:"center", marginTop:"4px"}}>
                                    <span style={{fontSize:"12px", color:"#8A93A6"}}>🖼 {d.images.length} фото · зелёная рамка = главное · клик = увеличить</span>
                                    <button onClick={() => openAddPhoto(d.id)} style={{background:"#EAF5EE", color:"#12B76A", border:"1px solid #12B76A", borderRadius:"6px", padding:"2px 8px", fontSize:"12px", cursor:"pointer", fontWeight:"bold"}}>+ фото</button>
                                  </div>
                                </div>
                              )}
                              <div style={{fontSize:"15px", color:"#667085", marginBottom:"8px", flex:1, display:"-webkit-box", WebkitLineClamp:3, WebkitBoxOrient:"vertical" as any, overflow:"hidden", maxHeight:"66px", lineHeight:"22px"}} dangerouslySetInnerHTML={{__html: (d.description || "")
                                .replace(/</g, "&lt;").replace(/>/g, "&gt;")
                                .replace(/&lt;strong&gt;/g, "<strong style='color:#1D2939'>").replace(/&lt;\/strong&gt;/g, "</strong>")
                                .replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>")
                                .replace(/&lt;ul&gt;/g, "<ul style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ul&gt;/g, "</ul>")
                                .replace(/&lt;ol&gt;/g, "<ol style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ol&gt;/g, "</ol>")
                                .replace(/&lt;li&gt;/g, "<li>").replace(/&lt;\/li&gt;/g, "</li>")
                                .replace(/&lt;br\s*\/?&gt;/g, "<br>").replace(/&lt;p&gt;/g, "<p style='margin:6px 0'>").replace(/&lt;\/p&gt;/g, "</p>")
                              }} />
                              <div style={{fontSize:"15px", fontWeight:"bold", color:"#2F6FED", marginBottom:"8px"}}>{d.price} ₽ <span style={{fontSize:"15px", color:"#8A93A6", fontWeight:"normal"}}>· {d.address}</span></div>
                              <div style={{display:"flex", gap:"6px"}}>
                                <button className="boris-btn-hover" onClick={() => publishDraft(d.id)} style={{flex:1, background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"7px", fontSize:"15px", cursor:"pointer", fontWeight:"bold"}}>✅ Опубликовать</button>
                                <button className="boris-btn-hover" onClick={() => setEditingDraftId(d.id)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"7px 10px", fontSize:"15px", cursor:"pointer"}}>✏️</button>
                                <button className="boris-btn-hover" onClick={() => deleteDraft(d.id)} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"7px 10px", fontSize:"15px", cursor:"pointer"}}>🗑</button>
                              </div>
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                    </>
                    ); })()}
                  </>
                )}
              </>
            ) : listingFilter === null ? (
              <div style={{textAlign:"center", padding:"60px 20px", color:"#8A93A6", fontSize:"16px", background:"#F9FAFC", borderRadius:"12px", border:"1px dashed #E3E7F0"}}>
                👆 Выберите фильтр выше («Все», «Активные», «Черновики» и т.д.), чтобы увидеть объявления
              </div>
            ) : (
              <>
            {listingFilter !== "feeds" && listingFilter !== "templates_lib" && (
            <div style={{color:"#667085", fontSize:"15px", marginBottom:"16px", display:"flex", alignItems:"center", gap:"12px"}}>
              <span>Показано: {getFilteredItems().length} объявлений</span>
              <button className="boris-btn-hover" onClick={() => setSelectedItemIds(new Set(getFilteredItems().map((it: any) => String(it.id))))} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"4px 10px", cursor:"pointer", fontSize:"15px"}}>☑ Выбрать все</button>
              {selectedItemIds.size > 0 && (
                <button className="boris-btn-hover" onClick={() => setSelectedItemIds(new Set())} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"4px 10px", cursor:"pointer", fontSize:"15px"}}>✕ Снять выбор</button>
              )}
            </div>
            )}

            {loading ? <p style={{color:"#667085"}}>Загружаю объявления...</p> : (
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(230px, 1fr))", gap:"12px"}}>
                {getFilteredItems().map((item: any) => (
                  <div key={item.id} className="boris-card-hover" style={{background:"#FFFFFF", borderRadius:"10px", padding:"14px", border: selectedItemIds.has(String(item.id)) ? "1px solid #2F6FED" : "1px solid #EEF2FA", boxShadow:"0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.04)", display:"flex", flexDirection:"column", height:"100%"}}>
                    <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", marginBottom:"8px"}}>
                      <div style={{fontSize:"15px", color:"#667085", display:"-webkit-box", WebkitLineClamp:2, WebkitBoxOrient:"vertical" as any, overflow:"hidden", minHeight:"24px", flex:1, marginRight:"6px"}}>📍 {item.address}</div>
                      <input type="checkbox" checked={selectedItemIds.has(String(item.id))} onChange={() => toggleItemSelected(String(item.id))} style={{width:"18px", height:"18px", cursor:"pointer"}} />
                    </div>
                    <div style={{fontSize:"15px", fontWeight:"bold", marginBottom:"8px", color:"#1D2939"}}>{item.title}</div>
                    <div style={{fontSize:"23px", color:"#2F6FED", fontWeight:"bold", marginBottom:"8px"}}>{item.price} ₽</div>
                    <div style={{display:"flex", gap:"8px", marginBottom:"10px", fontSize:"15px", color:"#667085"}}>
                      <span>👁 {item.views || 0}</span>
                      <span>📞 {item.contacts || 0}</span>
                      <span>⭐ {item.favorites || 0}</span>
                      <span>📈 {item.conversion || 0}%</span>
                    </div>
                    <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"8px"}}>
                      <span style={{background: item.status === "active" ? "#E7EFFE" : "#FDEDEC", color: item.status === "active" ? "#1E4FBF" : "#F04438", padding:"3px 9px", borderRadius:"20px", fontSize:"15px"}}>
                        {item.status === "active" ? "✅ Активно" : "❌ Неактивно"}
                      </span>
                      <a href={item.url} target="_blank" style={{color:"#2F6FED", fontSize:"15px", textDecoration:"none"}}>Открыть →</a>
                    </div>
                    <button className="boris-btn-hover" onClick={() => startDuplicate(item)}
                      style={{width:"100%", background:"#FFFFFF", color:"#667085", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"6px", cursor:"pointer", fontSize:"15px", marginTop:"auto"}}>
                      🔁 Дублировать
                    </button>
                    {myBorisItems.some((mi: any) => mi.id === String(item.id)) && (
                      <button className="boris-btn-hover" onClick={() => startEditItem(myBorisItems.find((mi: any) => mi.id === String(item.id)))} style={{marginTop:"8px", width:"100%", background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>
                        ✏️ Править (опубликовано Борисом)
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
              </>
            )}

            {duplicateEditingItem && (
              <div style={{background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"24px", maxWidth:"600px"}}>
                <button className="boris-btn-hover" onClick={() => setDuplicateEditingItem(null)} style={{background:"#FFFFFF", border:"none", color:"#2F6FED", cursor:"pointer", fontSize:"15px", marginBottom:"16px"}}>← Назад к списку</button>
                <h3 style={{margin:"0 0 16px", fontSize:"15px"}}>🔁 Редактирование дубля</h3>

                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Заголовок</label>
                <input value={duplicateEditingItem.title} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, title: e.target.value}))}
                  style={{width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"23px", marginBottom:"16px", boxSizing:"border-box"}} />

                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Описание</label>
                <div style={{display:"flex", gap:"6px", marginBottom:"8px", flexWrap:"wrap", position:"relative"}}>
                  <button className="boris-btn-hover" onClick={() => wrapDescriptionSelection("<strong>", "</strong>", "жирный текст")} title="Жирный" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>Ж</button>
                  <button className="boris-btn-hover" onClick={() => wrapDescriptionSelection("<i>", "</i>", "курсив")} title="Курсив" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px", fontStyle:"italic"}}>К</button>
                  <button className="boris-btn-hover" onClick={() => insertAtCursor("{вариант1|вариант2|вариант3}")} title="Спинтакс — оператор уникализации" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px", fontFamily:"monospace"}}>{"{ | }"}</button>
                  <button className="boris-btn-hover" onClick={() => setShowEmojiPicker(v => !v)} title="Эмодзи" style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px"}}>😀</button>
                  {showEmojiPicker && (
                    <div style={{position:"absolute", top:"36px", left:0, zIndex:10, background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"8px", display:"flex", gap:"4px", flexWrap:"wrap", maxWidth:"280px"}}>
                      {AD_EMOJIS.map(e => (
                        <button className="boris-btn-hover" key={e} onClick={() => { insertAtCursor(e); setShowEmojiPicker(false); }} style={{background:"#FFFFFF", border:"none", cursor:"pointer", fontSize:"23px", padding:"4px"}}>{e}</button>
                      ))}
                    </div>
                  )}
                </div>
                <textarea ref={descTextareaRef} value={duplicateEditingItem.description} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, description: e.target.value}))}
                  rows={5} style={{width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"23px", marginBottom:"16px", boxSizing:"border-box", resize:"vertical"}} />

                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Цена (₽)</label>
                <input type="number" value={duplicateEditingItem.price} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, price: Number(e.target.value)}))}
                  style={{width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"23px", marginBottom:"20px", boxSizing:"border-box"}} />

                <div style={{display:"flex", gap:"8px", marginBottom:"10px"}}>
                  <button className="boris-btn-hover" onClick={() => setRewriteEngine("gigachat")} style={{flex:1, background: rewriteEngine === "gigachat" ? "#2F6FED" : "#FFFFFF", color: rewriteEngine === "gigachat" ? "#F6F7FB" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"8px", cursor:"pointer", fontSize:"15px", fontWeight: rewriteEngine === "gigachat" ? "bold" : "normal"}}>Копирайтер Стандарт</button>
                  <button className="boris-btn-hover" onClick={() => setRewriteEngine("chatgpt")} style={{flex:1, background: rewriteEngine === "chatgpt" ? "#2F6FED" : "#FFFFFF", color: rewriteEngine === "chatgpt" ? "#F6F7FB" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"8px", cursor:"pointer", fontSize:"15px", fontWeight: rewriteEngine === "chatgpt" ? "bold" : "normal"}}>Копирайтер Премиум</button>
                </div>
                <label style={{display:"flex", alignItems:"center", gap:"10px", marginBottom:"10px", cursor:"pointer", color:"#475467", fontSize:"15px"}}>
                  <input type="checkbox" checked={forceIncludeVariants} onChange={e => setForceIncludeVariants(e.target.checked)} style={{width:"16px", height:"16px", cursor:"pointer"}} />
                  🎨 Принудительно добавить блоки характеристик/цвета (RAL) в конец описания
                </label>
                <button className="boris-btn-hover" onClick={rewriteDraftText} disabled={rewriteLoading}
                  style={{width:"100%", background: rewriteLoading ? "#E3E7F0" : "#2F6FED", color:"#1D2939", border:"none", borderRadius:"8px", padding:"12px", fontWeight:"bold", cursor: rewriteLoading ? "wait" : "pointer", fontSize:"23px", marginBottom:"16px"}}>
                  {rewriteLoading ? "⏳ Борис переписывает текст..." : "🔄 Борис перепишет текст эффективнее"}
                </button>

                <label style={{display:"flex", alignItems:"center", gap:"10px", marginBottom:"20px", cursor:"pointer", color:"#475467", fontSize:"23px"}}>
                  <input type="checkbox" checked={!!duplicateEditingItem.is_template} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, is_template: e.target.checked}))} style={{width:"18px", height:"18px", cursor:"pointer"}} />
                  📝 Также сохранить как шаблон
                </label>

                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Фотографии (выбери из галереи)</label>
                <div style={{display:"flex", flexWrap:"wrap", gap:"8px", marginBottom:"20px", maxHeight:"220px", overflowY:"auto", padding:"8px", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px"}}>
                  {Object.entries(allFolders).flatMap(([folder, urls]: [string, any]) =>
                    (urls as string[]).map((url: string) => (
                      <div key={url} onClick={() => toggleDraftImage(url)} style={{position:"relative", width:"70px", height:"70px", borderRadius:"6px", overflow:"hidden", cursor:"pointer", border: (duplicateEditingItem.images || []).includes(url) ? "3px solid #2F6FED" : "1px solid #E3E7F0"}}>
                        <img src={url} style={{width:"100%", height:"100%", objectFit:"cover"}} />
                        {(duplicateEditingItem.images || []).includes(url) && (
                          <div style={{position:"absolute", top:0, right:0, background:"#2F6FED", color:"#F6F7FB", fontSize:"15px", fontWeight:"bold", padding:"1px 4px", borderRadius:"0 0 0 6px"}}>✓</div>
                        )}
                      </div>
                    ))
                  )}
                  {Object.keys(allFolders).length === 0 && <div style={{color:"#8A93A6", fontSize:"15px"}}>Галерея пуста — загрузите фото во вкладке «Выгрузка с сайта».</div>}
                </div>
                <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"16px"}}>
                  Выбрано фото: {(duplicateEditingItem.images || []).length}. Порядок перетаскиванием — в разработке.
                </div>

                <button className="boris-btn-hover" onClick={saveDuplicateDraft} disabled={savingDraft}
                  style={{width:"100%", background: savingDraft ? "#E3E7F0" : "#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"8px", padding:"12px", fontWeight:"bold", cursor: savingDraft ? "wait" : "pointer", fontSize:"23px"}}>
                  {savingDraft ? "⏳ Сохраняю..." : "✅ Сохранить черновик"}
                </button>
              </div>
            )}

            {showDuplicateDrafts && !duplicateEditingItem && (
              <div>
                <h3 style={{margin:"0 0 16px", fontSize:"15px"}}>🆕 Новые дубли — черновики в работе</h3>
                {duplicateDrafts.length === 0 ? (
                  <p style={{color:"#667085"}}>Пока нет черновиков-дублей. Нажми «🔁 Дублировать» на любом объявлении.</p>
                ) : (
                  <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(320px, 1fr))", gap:"16px"}}>
                    {duplicateDrafts.map((d: any) => (
                      <div key={d.draft_id} style={{background:"#F6F7FB", borderRadius:"12px", padding:"20px", border:"1px solid #EEF2FA"}}>
                        <div style={{fontSize:"23px", fontWeight:"bold", marginBottom:"8px"}}>{d.title}</div>
                        <div style={{color:"#667085", fontSize:"15px", marginBottom:"12px"}}>{d.created_at}{d.is_template ? " · 📝 шаблон" : ""}</div>
                        <div style={{fontSize:"23px", color:"#2F6FED", fontWeight:"bold", marginBottom:"16px"}}>{d.price} ₽</div>
                        <div style={{display:"flex", gap:"8px"}}>
                          <button className="boris-btn-hover" onClick={() => setDuplicateEditingItem(d)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px", cursor:"pointer", fontSize:"15px"}}>✏️ Продолжить</button>
                          <button className="boris-btn-hover" onClick={() => deleteDuplicateDraft(d.draft_id)} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"8px 12px", cursor:"pointer", fontSize:"15px"}}>🗑</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {pipelineHistory.length > 0 && (
              <div className="b-panel" style={{marginTop:"24px"}}>
                <div className="b-title">🚀 История конвейера направления</div>
                <div className="b-sub">Аккаунт: {currentAccount} — только его запуски, независимо от того, что крутится в других аккаунтах.</div>
                <div style={{display:"grid", gap:"14px"}}>
                  {pipelineHistory.map((item: any) => {
                    if (item.type === "single") return renderPipelineRunCard(item.run);
                    const runs = item.runs;
                    const doneCount = runs.filter((r: any) => ["done", "ready"].includes(r.active?.status)).length;
                    const runningCount = runs.filter((r: any) => r.active?.status === "running").length;
                    const queuedCount = runs.filter((r: any) => r.active?.status === "queued").length;
                    const waitingCount = runs.filter((r: any) => r.active?.status === "needs_confirmation").length;
                    const errorCount = runs.filter((r: any) => ["error", "cancelled"].includes(r.active?.status)).length;
                    const allDone = doneCount + errorCount === runs.length;
                    return (
                      <div key={item.batchId} className="b-panel b-card-eq" style={{padding:"18px", background:"#F6FBF9"}}>
                        <div style={{display:"flex", alignItems:"center", gap:"10px", marginBottom:"6px"}}>
                          <span className="b-icon-sm" style={{background:"linear-gradient(135deg,#7C5CFC,#5B3FD9)"}}>📦</span>
                          <div style={{fontWeight:"bold", fontSize:"15px", color:"#1D2939"}}>Пакет из {runs.length} направлений</div>
                        </div>
                        <div style={{fontSize:"13px", color:"#667085", marginBottom:"14px"}}>
                          ✅ {doneCount} готово{waitingCount ? ` · ⏸ ${waitingCount} ждут подтверждения` : ""}{runningCount ? ` · 🔵 ${runningCount} в работе` : ""}{queuedCount ? ` · 🕓 ${queuedCount} в очереди` : ""}{errorCount ? ` · ❌ ${errorCount} с ошибкой` : ""}
                        </div>
                        <div style={{background:"#E3E7F0", borderRadius:"6px", height:"6px", overflow:"hidden", marginBottom:"16px"}}>
                          <div style={{width:`${allDone ? 100 : Math.round((doneCount / runs.length) * 100)}%`, height:"100%", background:"#7C5CFC", borderRadius:"6px", transition:"width 0.6s ease"}} />
                        </div>
                        <div style={{display:"grid", gap:"10px"}}>
                          {runs.map((run: any) => renderPipelineRunCard(run, true))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
          </>
        )}

        {activeTab === "templates" && (
          <div>
            <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"24px"}}>
              <h3 style={{margin:0, fontSize:"15px"}}>📝 Шаблоны объявлений</h3>
              <button className="boris-btn-hover" onClick={() => setShowTemplateForm(true)} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 20px", fontWeight:"bold", cursor:"pointer"}}>+ Новый шаблон</button>
            </div>
            <div style={{background:"#F6F7FB", border:"1px solid #E7EFFE", borderRadius:"12px", padding:"24px", marginBottom:"24px"}}>
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"10px"}}><Mascot size={26} interactive={false} />Генератор объявлений через ИИ</h3>
              <p className="b-sub">Борис сам придумает уникальные продающие объявления со спинтаксом для защиты от дублей</p>
              <div style={{display:"grid", gridTemplateColumns:"2fr 1fr", gap:"16px", marginBottom:"16px"}}>
                <div>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Тема / услуга</label>
                  <input value={genTopic} onChange={e => setGenTopic(e.target.value)} placeholder="Например: Поздравления в стихах на юбилей" style={inputStyle} />
                </div>
                <div>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Сколько штук</label>
                  <input type="number" value={genCount} onChange={e => setGenCount(Number(e.target.value))} min={1} max={30} style={inputStyle} />
                </div>
              </div>
              <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"16px", marginBottom:"16px"}}>
                <div>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Цена от (руб)</label>
                  <input type="number" value={genPriceFrom || ""} onChange={e => setGenPriceFrom(Number(e.target.value) || 0)} min={0} style={inputStyle} />
                </div>
                <div>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Цена до (руб)</label>
                  <input type="number" value={genPriceTo || ""} onChange={e => setGenPriceTo(Number(e.target.value) || 0)} min={0} style={inputStyle} />
                    <div style={{color:"#8A93A6", fontSize:"15px", marginTop:"6px"}}>💡 Для товаров обычно нужна одна конкретная цена, а не диапазон — впишите одинаковое число в оба поля.</div>
                </div>
              </div>
              <div style={{marginBottom:"16px"}}>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>О компании / акции / особенности (Борис учтёт это)</label>
                <textarea value={genExtra} onChange={e => setGenExtra(e.target.value)} placeholder="Например: работаем 10 лет, скидка пенсионерам 10%, бесплатная доставка по МО" rows={3} style={{...inputStyle, resize:"vertical", fontFamily:"inherit"}} />
                <label className="b-label" style={{marginTop:"18px"}}>📋 Образец / пожелания к шаблону <span style={{fontWeight:400, color:"#8A93A6"}}>— вставь пример объявления или бриф: Борис возьмёт оттуда общую информацию и стиль, а конкретику по товару придумает свою</span></label>
                <textarea className="b-input" value={genSample} onChange={e => setGenSample(e.target.value)} placeholder="Например: вставь текст удачного объявления конкурента или описание компании — Борис учтёт структуру, тон и общие факты, но переработает под каждый товар" rows={5} style={{resize:"vertical", fontFamily:"inherit"}} />
              </div>
              <div style={{marginBottom:"16px"}}>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Длина описаний</label>
                <div style={{display:"flex", gap:"8px", flexWrap:"wrap"}}>
                  {[{k:"short",l:"📄 Короткие"},{k:"medium",l:"📃 Средние"},{k:"long",l:"📜 Длинные"}].map(g => (
                    <button className="boris-btn-hover" key={g.k} onClick={() => setGenLength(g.k)} style={{padding:"8px 16px", borderRadius:"10px", border: genLength===g.k ? "1px solid #2F6FED" : "1px solid #E3E7F0", background: genLength===g.k ? "#E7EFFE" : "#FFFFFF", color: genLength===g.k ? "#2F6FED" : "#667085", cursor:"pointer", fontSize:"15px", fontWeight: genLength===g.k ? "bold" : "normal"}}>{g.l}</button>
                  ))}
                </div>
              </div>
              <label style={{display:"flex", alignItems:"center", gap:"10px", marginBottom:"16px", cursor:"pointer", color:"#475467", fontSize:"23px"}}>
                <input type="checkbox" checked={useMyAds} onChange={e => setUseMyAds(e.target.checked)} style={{width:"18px", height:"18px", cursor:"pointer"}} />
                📊 Учитывать стиль моих активных объявлений (Борис возьмёт образцы из Авито)
              </label>
              <div style={{marginBottom:"16px"}}>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Цель объявления — что должен сделать клиент?</label>
                <div style={{display:"flex", gap:"8px", flexWrap:"wrap"}}>
                  {[{k:"call",l:"📞 Позвонить"},{k:"message",l:"💬 Написать в чат"},{k:"order",l:"🛒 Сразу заказать"},{k:"visit",l:"📍 Прийти / замер"}].map(g => (
                    <button className="b-btn" key={g.k} onClick={() => setGenGoal(g.k)} style={{border: genGoal===g.k ? "1px solid #2F6FED" : "1px solid #E3E7F0", background: genGoal===g.k ? "#E7EFFE" : "#FFFFFF", color: genGoal===g.k ? "#2F6FED" : "#667085", fontWeight: genGoal===g.k ? 700 : 500}}>{g.l}</button>
                  ))}
                </div>
              </div>
              <button className="b-btn b-btn-primary" onClick={() => setShowConfirm(true)} disabled={genLoading} style={{background: genLoading ? "#E3E7F0" : "#2F6FED", borderColor: genLoading ? "#E3E7F0" : "#2F6FED", cursor: genLoading ? "wait" : "pointer", padding:"12px 24px"}}>
                {genLoading ? "Борис генерирует…" : "✨ Сгенерировать объявления"}
              </button>
{showConfirm && (
  <div style={{position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(0,0,0,0.7)", zIndex:1000, display:"flex", alignItems:"center", justifyContent:"center"}} onClick={() => setShowConfirm(false)}>
    <div onClick={(e:any) => e.stopPropagation()} style={{background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"28px", maxWidth:"480px", width:"90%"}}>
      <h3 style={{margin:"0 0 16px", color:"#1D2939", fontSize:"18px"}}>Проверьте перед генерацией</h3>
      <div style={{fontSize:"23px", color:"#344054", lineHeight:"2"}}>
        <div><b style={{color:"#667085"}}>Тема:</b> {genTopic || "—"}</div>
        <div><b style={{color:"#667085"}}>Цена:</b> {genPriceFrom || genPriceTo ? `${genPriceFrom||0} – ${genPriceTo||0} ₽` : "не задана"}</div>
        <div><b style={{color:"#667085"}}>О компании:</b> {genExtra ? genExtra.slice(0,60)+(genExtra.length>60?"…":"") : "—"}</div>
        <div><b style={{color:"#667085"}}>Количество:</b> {genCount}</div>
        <div><b style={{color:"#667085"}}>Длина:</b> {genLength === "short" ? "Короткие" : genLength === "long" ? "Длинные" : "Средние"}</div>
        <div><b style={{color:"#667085"}}>Цель:</b> {genGoal === "call" ? "📞 Позвонить" : genGoal === "message" ? "💬 Написать" : genGoal === "order" ? "🛒 Заказать" : "📍 Прийти"}</div>
        <div><b style={{color:"#667085"}}>Стиль моих объявлений:</b> {useMyAds ? "учитывать" : "не учитывать"}</div>
      </div>
      <div style={{display:"flex", gap:"12px", marginTop:"24px"}}>
        <button className="boris-btn-hover" onClick={() => { setShowConfirm(false); generateAds(); }} style={{flex:1, background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px", fontWeight:"bold", cursor:"pointer"}}>✅ Всё верно, генерировать</button>
        <button className="boris-btn-hover" onClick={() => setShowConfirm(false)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"12px", cursor:"pointer"}}>Изменить</button>
      </div>
    </div>
  </div>
)}

              {genAdsHistory.length > 0 && (
                <div style={{marginBottom:"24px", background:"#F6F7FB", border:"1px solid #EEF2FA", borderRadius:"10px", padding:"16px"}}>
                  <div style={{color:"#667085", fontWeight:"bold", fontSize:"15px", marginBottom:"12px"}}>📚 История партий шаблонов ({genAdsHistory.length})</div>
                  <div style={{display:"flex", flexDirection:"column", gap:"8px", maxHeight:"280px", overflowY:"auto"}}>
                    {genAdsHistory.map((batch: any) => {
                      const eff = batchEffectiveness[batch.id];
                      return (
                        <div key={batch.id} style={{display:"flex", justifyContent:"space-between", alignItems:"center", background:"#F6F7FB", borderRadius:"8px", padding:"10px 14px", fontSize:"15px"}}>
                          <div style={{flex:1}}>
                            <div><b>{batch.topic}</b> <span style={{color:"#8A93A6"}}>— {batch.ads?.length || 0} шт.</span></div>
                            <div style={{color:"#8A93A6", fontSize:"15px"}}>{new Date(batch.created_at).toLocaleString("ru-RU")}</div>
                            {eff && eff.status === "ok" && (
                              <div style={{marginTop:"4px", fontSize:"15px"}}>
                                Эффективность: <b style={{color: eff.level === "Высокая" ? "#2F6FED" : eff.level === "Средняя" ? "#F79009" : "#F04438"}}>{eff.level}</b>
                                {" "}(конверсия {eff.conversion}%, {eff.contacts} обращений за {eff.days} дн.)
                              </div>
                            )}
                            {eff && eff.status === "no_data" && (
                              <div style={{marginTop:"4px", fontSize:"15px", color:"#8A93A6"}}>Нет данных — объявления из этой партии ещё не публиковались или статистика не собрана</div>
                            )}
                          </div>
                          <div style={{display:"flex", gap:"6px"}}>
                            <button className="boris-btn-hover" onClick={() => checkBatchEffectiveness(batch.id)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 10px", cursor:"pointer", fontSize:"15px"}}>📊 Эффективность</button>
                            <button className="boris-btn-hover" onClick={() => setGenAds(batch.ads)} style={{background:"#E7EFFE", color:"#2F6FED", border:"1px solid #E7EFFE", borderRadius:"10px", padding:"6px 10px", cursor:"pointer", fontSize:"15px"}}>↩ Загрузить</button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {genAds.length > 0 && (
                <div style={{marginTop:"24px"}}>
                  <div className="b-title" style={{display:"flex", alignItems:"center", gap:"10px", marginBottom:"16px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#12805C,#3DBE93)"}}>✅</span>Готово! Сгенерировано: {genAds.length}</div>
                  <div className="b-panel" style={{display:"flex", gap:"10px", alignItems:"center", marginBottom:"16px", padding:"16px", flexWrap:"wrap"}}>
                    <span className="b-label" style={{margin:0}}>🔁 Размножить до:</span>
                    <input type="number" value={cloneTarget} onChange={e => setCloneTarget(Math.max(1, Number(e.target.value)||1))} style={{...inputStyle, width:"90px"}} />
                    <button className="b-btn" onClick={cloneAds} style={{background:"#7C5CFC", color:"#FFFFFF", border:"1px solid #7C5CFC"}}>🔁 Размножить</button>
                    <button className="b-btn" onClick={saveGenBatch} style={{background:"#12805C", color:"#FFFFFF", border:"1px solid #12805C"}}>💾 Сохранить партию</button>
                    <button className="b-btn b-btn-ghost" onClick={() => { if (!feedUrl) { alert("Сначала отправьте партию в фид."); return; } checkFeed(feedUrl); }}>🔍 Проверить фид</button>
                    <span style={{color:"#8A93A6", fontSize:"15px"}}>Уникальность текста и фото сохранится за счёт спинтакса</span>
                  </div>
                  {(() => {
                    const spinMax = (t: string) => { const m = String(t||"").match(/\{([^{}]*)\}/); const vars = m ? m[1].split("|") : [String(t||"")]; return vars.reduce((a, v) => Math.max(a, v.trim().length), 0); };
                    const probs: string[] = [];
                    genAds.forEach((a, i) => {
                      const L = spinMax(a.title);
                      if (L > 50) probs.push("№" + (i+1) + ": заголовок " + L + " симв. (лимит 50) — обрежется");
                      if (!a.price || Number(a.price) <= 0) probs.push("№" + (i+1) + ": цена не указана");
                      if (!a.description || String(a.description).length < 50) probs.push("№" + (i+1) + ": описание пустое или слишком короткое");
                    });
                    if (probs.length === 0) return (
                      <div style={{marginBottom:"16px", padding:"10px 14px", background:"#E9F7F1", border:"1px solid #C7EBDC", borderRadius:"8px", color:"#12805C", fontSize:"14px", fontWeight:"bold"}}>✅ Проверка пройдена: все {genAds.length} объявлений корректны</div>
                    );
                    return (
                      <div style={{marginBottom:"16px", padding:"10px 14px", background:"#FDECEC", border:"1px solid #F5C6C6", borderRadius:"8px", color:"#B42318", fontSize:"14px"}}>
                        <div style={{fontWeight:"bold", marginBottom:"6px"}}>⚠️ Найдено проблем: {probs.length}</div>
                        {probs.slice(0, 12).map((t, k) => <div key={k} style={{marginBottom:"2px"}}>• {t}</div>)}
                        {probs.length > 12 && <div>…и ещё {probs.length - 12}</div>}
                      </div>
                    );
                  })()}
                  <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(320px, 1fr))", gap:"16px"}}>
                    {genAds.map((ad, i) => (
                      <div key={i} className="b-card" style={{padding:"20px"}}>
                        {editingIndex === i ? (
                          <div>
                            <input value={ad.title} onChange={e => updateAd(i, "title", e.target.value)} style={{...inputStyle, marginBottom:"8px", fontWeight:"bold"}} />
                            <input type="number" value={ad.price} onChange={e => updateAd(i, "price", Number(e.target.value))} style={{...inputStyle, marginBottom:"8px", width:"120px"}} />
                            <div style={{display:"flex", gap:"8px", marginBottom:"6px", alignItems:"center"}}>
                              <button className="boris-btn-hover" onClick={() => applyBold(i)} style={{background:"#FFFFFF", color:"#fff", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"4px 12px", cursor:"pointer", fontWeight:"bold"}}>Ж</button>
                              <span style={{color:"#8A93A6", fontSize:"15px"}}>выделите текст и нажмите Ж</span>
                            </div>
                            <textarea id={"desc-edit-" + i} value={ad.description} onChange={e => updateAd(i, "description", e.target.value)} rows={10} style={{...inputStyle, width:"100%", boxSizing:"border-box" as any, fontFamily:"inherit", fontSize:"15px"}} />
                            <button className="boris-btn-hover" onClick={() => setEditingIndex(null)} style={{marginTop:"8px", background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"11px 20px", fontWeight:"bold", cursor:"pointer"}}>✅ Готово</button>
                          </div>
                        ) : (
                          <>
                        <div style={{fontWeight:"bold", fontSize:"18px", marginBottom:"8px", color:"#1D2939", wordBreak:"break-word", overflowWrap:"break-word"}}>{ad.title}</div>
                        <div style={{fontSize:"23px", color:"#2F6FED", marginBottom:"10px"}}>{ad.price} ₽</div>
                        <div style={{fontSize:"15px", color:"#475467", whiteSpace:"pre-wrap", lineHeight:1.5, wordBreak:"break-word", overflowWrap:"break-word", maxHeight: expandedAds.has(i) ? "none" : "120px", overflow: expandedAds.has(i) ? "visible" : "hidden", position:"relative"}} dangerouslySetInnerHTML={{__html: ad.description
                          .replace(/</g, "&lt;").replace(/>/g, "&gt;")
                          .replace(/&lt;strong&gt;/g, "<strong style='color:#1D2939'>").replace(/&lt;\/strong&gt;/g, "</strong>")
                          .replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>")
                          .replace(/&lt;ul&gt;/g, "<ul style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ul&gt;/g, "</ul>")
                          .replace(/&lt;ol&gt;/g, "<ol style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ol&gt;/g, "</ol>")
                          .replace(/&lt;li&gt;/g, "<li>").replace(/&lt;\/li&gt;/g, "</li>")
                          .replace(/&lt;br\s*\/?&gt;/g, "<br>").replace(/&lt;p&gt;/g, "<p style='margin:6px 0'>").replace(/&lt;\/p&gt;/g, "</p>")
                          .replace(/(\{[^}]*\})/g, "<span style='background:#E7EFFE;color:#2F6FED;padding:1px 4px;border-radius:4px;font-weight:bold'>$1</span>")
                        }} />
                          <button className="b-more" onClick={() => toggleExpand(i)} style={{marginTop:"10px"}}>{expandedAds.has(i) ? "▲ Свернуть" : "▼ Показать полностью"}</button>
                          <div style={{marginTop:"10px", display:"flex", alignItems:"center", gap:"8px"}}>
                            <span className="b-meta">Цена:</span>
                            <input className="b-input" type="number" value={ad.price || ""} onChange={e => updateAd(i, "price", e.target.value)} style={{width:"110px", padding:"8px 10px", fontWeight:700, color:"#2F6FED"}} />
                            <span className="b-meta">₽</span>
                          </div>
                          <div className="b-card-actions">
                            <button className="b-btn" onClick={() => setEditingIndex(i)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", padding:"8px 14px", fontSize:"13px"}}>✏️ Править</button>
                            <button className="b-btn" onClick={() => setPhotoPickerIndex(photoPickerIndex === i ? null : i)} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", padding:"8px 14px", fontSize:"13px"}}>🖼 Фото ({getAdPhotos(i).length})</button>
                            <button className="b-btn" onClick={() => deleteAd(i)} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", padding:"8px 14px", fontSize:"13px"}}>🗑 Удалить</button>
                            <button className="b-btn" onClick={() => saveAsTextTemplate(ad)} style={{background:"#FFFFFF", color:"#12805C", border:"1px solid #C7EBDC", padding:"8px 14px", fontSize:"13px"}}>💾 Сохранить как шаблон</button>
                          </div>
                          {photoPickerIndex === i && (
                            <div style={{marginTop:"10px", padding:"10px", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px"}}>
                              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"8px"}}>
                                <span style={{color:"#667085", fontSize:"15px"}}>Выделите фото для этого объявления</span>
                                <div style={{display:"flex", gap:"6px"}}>
                                  <input id={"ad-upload-" + i} type="file" accept="image/*" multiple style={{display:"none"}} onChange={e => uploadForAd(i, e.target.files)} />
                                  <button className="boris-btn-hover" onClick={() => document.getElementById("ad-upload-" + i)?.click()} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"4px 10px", cursor:"pointer", fontSize:"15px"}}>📁 Со своего устройства</button>
                                  <button className="boris-btn-hover" onClick={loadImagesForFeed} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"4px 10px", cursor:"pointer", fontSize:"15px"}}>🔄 Обновить список</button>
                                </div>
                              </div>
                              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(70px, 1fr))", gap:"6px", maxHeight:"200px", overflowY:"auto"}}>
                                {allImages.map((url, ii) => (
                                  <div key={ii} style={{position:"relative"}}>
                                    <div onClick={() => toggleAdPhoto(i, url)} style={{cursor:"pointer", border: (adPhotos[i]||[]).includes(url) ? "2px solid #7C5CFC" : "2px solid #EEF2FA", borderRadius:"6px", overflow:"hidden"}}>
                                      <img src={url} style={{width:"100%", height:"55px", objectFit:"cover", display:"block", opacity: (adPhotos[i]||[]).includes(url) ? 1 : 0.5}} />
                                      {(adPhotos[i]||[]).includes(url) && <div style={{position:"absolute", top:"2px", right:"2px", background:"#7C5CFC", color:"#F6F7FB", borderRadius:"50%", width:"16px", height:"16px", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"15px", fontWeight:"bold"}}>✓</div>}
                                    </div>
                                    {(adPhotos[i]||[]).includes(url) && (
                                      <div style={{display:"flex", justifyContent:"space-between", marginTop:"2px"}}>
                                        <button className="boris-btn-hover" onClick={() => toggleTextPhoto(i, url)} title="Пометить как фото с текстом (пойдёт первым)" style={{background: (adTextPhotos[i]||[]).includes(url) ? "#2F6FED" : "#FFFFFF", color: (adTextPhotos[i]||[]).includes(url) ? "#F6F7FB" : "#8A93A6", border:"1px solid #E3E7F0", borderRadius:"4px", width:"20px", height:"18px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>Т</button>
                                        <div style={{display:"flex", gap:"2px"}}>
                                          <button className="boris-btn-hover" onClick={() => movePhoto(i, url, -1)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"4px", width:"18px", height:"18px", fontSize:"15px", cursor:"pointer"}}>↑</button>
                                          <button className="boris-btn-hover" onClick={() => movePhoto(i, url, 1)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"4px", width:"18px", height:"18px", fontSize:"15px", cursor:"pointer"}}>↓</button>
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                              <button className="boris-btn-hover" onClick={() => setPhotoPickerIndex(null)} style={{marginTop:"8px", background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"11px 20px", fontWeight:"bold", cursor:"pointer", fontSize:"15px"}}>✅ Готово</button>
                            </div>
                          )}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                  <div style={{marginTop:"20px", padding:"16px", background:"#F6F7FB", borderRadius:"10px", border:"1px solid #EEF2FA"}}>
                    <div style={{display:"flex", gap:"12px", alignItems:"flex-end", flexWrap:"wrap"}}>
                      <div>
                        <label className="b-label">📍 Город / адрес для фида</label>
                        <input value={genAddress} onChange={e => setGenAddress(e.target.value)} placeholder="Москва" style={{...inputStyle, width:"260px"}} />
                      </div>
                      <div>
                        <label className="b-label">Или выберите из справочника</label>
                        <div style={{display:"flex", gap:"8px"}}>
                          <select className="b-select" value={selectedRegion} onChange={e => { setSelectedRegion(e.target.value); setSelectedCity(""); }} style={{...inputStyle, width:"160px"}}>
                            <option value="">Регион</option>
                            {Object.keys(citiesData).map(r => <option key={r} value={r}>{r}</option>)}
                          </select>
                          <select className="b-select" value={selectedCity} onChange={e => applyCityToAddress(e.target.value)} disabled={!selectedRegion} style={{...inputStyle, width:"160px"}}>
                            <option value="">Город</option>
                            {(citiesData[selectedRegion] || []).map(c => <option key={c} value={c}>{c}</option>)}
                          </select>
                          {metroData[selectedCity] && (
                            <button className="boris-btn-hover" onClick={() => setShowMetroPicker(!showMetroPicker)} style={{background: selectedMetro ? "#E7EFFE" : "#FFFFFF", color: selectedMetro ? "#2F6FED" : "#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"0 14px", cursor:"pointer", fontSize:"15px", whiteSpace:"nowrap"}}>🚇 {selectedMetro || "Метро"}</button>
                          )}
                        </div>
                        <div style={{display:"flex", gap:"8px", marginTop:"8px"}}>
                          {selectedRegion && (citiesData[selectedRegion] || []).length > 1 && (
                            <button className="boris-btn-hover" onClick={selectAllCitiesInRegion} style={{background: bulkCities.length>0 ? "#E7EFFE" : "#FFFFFF", color: bulkCities.length>0 ? "#2F6FED" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"5px 10px", cursor:"pointer", fontSize:"15px"}}>✅ Все города региона {bulkCities.length>0 ? `(${bulkCities.length})` : ""}</button>
                          )}
                          {metroData[selectedCity] && (
                            <button className="boris-btn-hover" onClick={selectAllMetroInCity} style={{background: bulkMetro.length>0 ? "#E7EFFE" : "#FFFFFF", color: bulkMetro.length>0 ? "#2F6FED" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"5px 10px", cursor:"pointer", fontSize:"15px"}}>✅ Все станции метро {bulkMetro.length>0 ? `(${bulkMetro.length})` : ""}</button>
                          )}
                          <div style={{display:"flex", gap:"6px", alignItems:"center", background:"#FFF", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"3px 8px"}}>
                            <span style={{fontSize:"14px"}}>🇷🇺 Вся Россия: топ</span>
                            <input value={topNCount} onChange={e=>setTopNCount(e.target.value.replace(/[^0-9]/g,""))} style={{width:"48px", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"3px 6px", fontSize:"14px", textAlign:"center"}} />
                            <button className="boris-btn-hover" onClick={applyTopRussia} style={{background:"#7F56D9", color:"#fff", border:"none", borderRadius:"8px", padding:"4px 12px", cursor:"pointer", fontSize:"14px", fontWeight:600}}>Применить</button>
                          </div>
                          <button className="boris-btn-hover" onClick={()=>setMultiRegionMode(!multiRegionMode)} style={{background: pickedRegions.length>0 ? "#E7EFFE" : "#FFFFFF", color: pickedRegions.length>0 ? "#2F6FED" : "#667085", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"5px 10px", cursor:"pointer", fontSize:"15px"}}>🗺️ Несколько регионов {pickedRegions.length>0 ? `(${pickedRegions.length})` : ""}</button>
                          {(bulkCities.length>0 || bulkMetro.length>0) && (
                            <button className="boris-btn-hover" onClick={() => { setBulkCities([]); setBulkMetro([]); }} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"5px 10px", cursor:"pointer", fontSize:"15px"}}>✕ Сбросить</button>
                          )}
                        </div>
                        {multiRegionMode && (
                          <div style={{marginTop:"10px", padding:"12px", background:"#FFF", border:"1px solid #E3E7F0", borderRadius:"10px", display:"flex", flexWrap:"wrap", gap:"8px", maxHeight:"200px", overflowY:"auto", alignItems:"flex-start"}}>
                            {Object.keys(citiesData).map(r => (
                              <label key={r} style={{display:"flex", alignItems:"center", gap:"5px", fontSize:"14px", cursor:"pointer", background: pickedRegions.includes(r)?"#E7EFFE":"#F6F7FB", borderRadius:"8px", padding:"4px 10px"}}>
                                <input type="checkbox" checked={pickedRegions.includes(r)} onChange={()=>toggleRegion(r)} />{r}
                              </label>
                            ))}
                            <button onClick={applyMultiRegions} style={{background:"#2F6FED", color:"#fff", border:"none", borderRadius:"8px", padding:"6px 16px", cursor:"pointer", fontSize:"14px", fontWeight:600, whiteSpace:"nowrap"}}>Собрать города ({pickedRegions.length} рег.)</button>
                          </div>
                        )}
                        {showMetroPicker && metroData[selectedCity] && (
                          <div style={{marginTop:"8px", maxHeight:"180px", overflowY:"auto", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"8px"}}>
                            {metroData[selectedCity].map(st => (
                              <div key={st} onClick={() => applyMetroToAddress(st)} style={{padding:"6px 10px", cursor:"pointer", borderRadius:"6px", fontSize:"15px", color: selectedMetro===st ? "#2F6FED" : "#344054"}} onMouseEnter={e => (e.currentTarget.style.background = "#FFFFFF")} onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                                {st}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                    <div style={{marginTop:"16px"}}>
                      <label className="b-label" style={{marginBottom:"14px"}}>🖼 Фото для объявлений <span style={{fontWeight:400, color:"#8A93A6"}}>— отмеченные галочками распределятся по объявлениям</span></label>
                      <div className="b-photo-bar">
                        <span style={{color:"#667085", fontSize:"15px", marginLeft:"12px"}}>Фото на объявление:</span>
                        <input className="b-input" type="number" min={1} max={10} value={photosPerAd} onChange={e => setPhotosPerAd(Math.max(1, Math.min(10, Number(e.target.value)||1)))} style={{width:"60px", padding:"9px 10px", marginLeft:"6px"}} />
                        <div style={{display:"flex", gap:"6px", alignItems:"center", marginLeft:"10px"}}>
                          <span style={{color:"#8A93A6", fontSize:"15px"}}>Остальные фото:</span>
                          <button className="b-btn" onClick={() => setRestOrderMode("order")} style={{padding:"9px 12px", fontSize:"13px", border: restOrderMode==="order" ? "1px solid #2F6FED" : "1px solid #E3E7F0", background: restOrderMode==="order" ? "#E7EFFE" : "#FFFFFF", color: restOrderMode==="order" ? "#2F6FED" : "#667085"}}>по порядку</button>
                          <button className="b-btn" onClick={() => setRestOrderMode("random")} style={{padding:"9px 12px", fontSize:"13px", border: restOrderMode==="random" ? "1px solid #2F6FED" : "1px solid #E3E7F0", background: restOrderMode==="random" ? "#E7EFFE" : "#FFFFFF", color: restOrderMode==="random" ? "#2F6FED" : "#667085"}}>случайно</button>
                        </div>
                        <div className="b-brk"></div>
                        <button className="b-btn" onClick={loadImagesForFeed} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF"}}>🔄 Из хранилища</button>
                        <input id="pool-upload-input" type="file" accept="image/*" multiple style={{display:"none"}} onChange={e => uploadForPool(e.target.files)} />
                        <button className="b-btn" onClick={() => document.getElementById("pool-upload-input")?.click()} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", padding:"9px 14px", fontSize:"13px", whiteSpace:"nowrap"}}>📁 С устройства</button>
                        <span className="b-meta" style={{marginLeft:"12px"}}>🏷 Баннер первым:</span>
                        <button className="b-btn" onClick={() => setUseBannerPersist(true)} style={{padding:"9px 14px", fontSize:"13px", border: useBanner ? "1px solid #12805C" : "1px solid #E3E7F0", background: useBanner ? "#E9F7F1" : "#FFFFFF", color: useBanner ? "#12805C" : "#667085"}}>Да</button>
                        <button className="b-btn" onClick={() => setUseBannerPersist(false)} style={{padding:"9px 14px", fontSize:"13px", border: !useBanner ? "1px solid #B42318" : "1px solid #E3E7F0", background: !useBanner ? "#FDECEC" : "#FFFFFF", color: !useBanner ? "#B42318" : "#667085"}}>Нет</button>
                        <span style={{color:"#2F6FED", fontSize:"15px"}}>Выбрано: {Object.values(feedImages).filter(Boolean).length}</span>
                      </div>
                      {allImages.length > 0 && (() => {
                        const isBanner = (u: string) => /banner|баннер|infograph|инфограф/i.test(decodeURIComponent(u));
                        const bannerList = allImages.filter(isBanner);
                        const photoList = allImages.filter((u: string) => !isBanner(u));
                        const cntB = bannerList.filter((u: string) => feedImages[u]).length;
                        const cntP = photoList.filter((u: string) => feedImages[u]).length;
                        const grid = (list: string[], accent: string) => (
                          <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(110px, 1fr))", gap:"10px", maxHeight:"220px", overflowY:"auto", padding:"4px"}}>
                            {list.map((url: string, ii: number) => (
                              <div key={ii} onClick={() => toggleFeedImage(url)} style={{position:"relative", cursor:"pointer", border: feedImages[url] ? "2px solid " + accent : "2px solid #EEF2FA", borderRadius:"8px", overflow:"hidden"}}>
                                <img src={url} style={{width:"100%", height:"90px", objectFit:"cover", display:"block", opacity: feedImages[url] ? 1 : 0.6}} />
                                {feedImages[url] && <div style={{position:"absolute", top:"4px", right:"4px", background: accent, color:"#F6F7FB", borderRadius:"50%", width:"20px", height:"20px", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"15px", fontWeight:"bold"}}>✓</div>}
                              </div>
                            ))}
                          </div>
                        );
                        return (
                          <div>
                            {bannerList.length > 0 && (
                              <div style={{marginBottom:"14px"}}>
                                <div style={{color:"#12805C", fontWeight:"bold", fontSize:"14px", marginBottom:"6px"}}>🏷 Баннеры {useBanner ? "(ставятся первым фото, по кругу)" : "(отключены — тумблер «Нет»)"} — выбрано: {cntB} из {bannerList.length}</div>
                                {grid(bannerList, "#12805C")}
                              </div>
                            )}
                            {photoList.length > 0 && (
                              <div>
                                <div style={{color:"#2F6FED", fontWeight:"bold", fontSize:"14px", marginBottom:"6px"}}>🖼 Фото товара (добираются до {Math.max(0, photosPerAd - (useBanner && cntB > 0 ? 1 : 0))}) — выбрано: {cntP} из {photoList.length}</div>
                                {grid(photoList, "#2F6FED")}
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>

                    <div>
                        <label className="b-label">🕐 Когда публиковать</label>
                        <div style={{display:"flex", gap:"8px"}}>
                          <button className="boris-btn-hover" onClick={() => setPublishMode("now")} style={{padding:"8px 14px", borderRadius:"10px", border: publishMode==="now" ? "1px solid #2F6FED" : "1px solid #E3E7F0", background: publishMode==="now" ? "#E7EFFE" : "#FFFFFF", color: publishMode==="now" ? "#2F6FED" : "#667085", cursor:"pointer", fontSize:"15px", fontWeight: publishMode==="now" ? "bold" : "normal"}}>⚡ Сейчас</button>
                          <button className="boris-btn-hover" onClick={() => setPublishMode("schedule")} style={{padding:"8px 14px", borderRadius:"10px", border: publishMode==="schedule" ? "1px solid #2F6FED" : "1px solid #E3E7F0", background: publishMode==="schedule" ? "#E7EFFE" : "#FFFFFF", color: publishMode==="schedule" ? "#2F6FED" : "#667085", cursor:"pointer", fontSize:"15px", fontWeight: publishMode==="schedule" ? "bold" : "normal"}}>🕐 Запланировать</button>
                        </div>
                      </div>
                      {publishMode === "schedule" && (
                        <div style={{display:"flex", gap:"8px"}}>
                          <div>
                            <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Дата</label>
                            <input type="date" value={publishDate} onChange={e => setPublishDate(e.target.value)} style={{...inputStyle, width:"160px"}} />
                          </div>
                          <div>
                            <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Время</label>
                            <input type="time" value={publishTime} onChange={e => setPublishTime(e.target.value)} style={{...inputStyle, width:"120px"}} />
                          </div>
                        </div>
                      )}
                      <button className="b-btn b-btn-primary" onClick={() => setShowPublishConfirm(true)} style={{padding:"12px 24px"}}>
                        {publishMode === "now" ? `📤 Опубликовать сейчас (${genAds.length})` : `🕐 Запланировать (${genAds.length})`}
                      </button>
                      {showPublishConfirm && (
                        <div style={{position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(0,0,0,0.7)", zIndex:1000, display:"flex", alignItems:"center", justifyContent:"center"}} onClick={() => setShowPublishConfirm(false)}>
                          <div onClick={(e:any) => e.stopPropagation()} style={{background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"28px", maxWidth:"480px", width:"90%"}}>
                            <h3 style={{margin:"0 0 16px", color:"#1D2939", fontSize:"18px"}}>Проверьте перед публикацией</h3>
                            <div style={{fontSize:"23px", color:"#344054", lineHeight:"2"}}>
                              <div><b style={{color:"#667085"}}>Объявлений:</b> {genAds.length}</div>
                              <div><b style={{color:"#667085"}}>С индивидуальными фото:</b> {Object.values(adPhotos).filter(p => p && p.length > 0).length} из {genAds.length}</div>
                              <div><b style={{color:"#667085"}}>Город:</b> {genAddress || "не указан"}</div>
                              <div><b style={{color:"#667085"}}>Когда:</b> {publishMode === "now" ? "⚡ Сейчас" : `🕐 ${publishDate || "дата не указана"} в ${publishTime}`}</div>
                            </div>
                            <div style={{display:"flex", gap:"12px", marginTop:"24px"}}>
                              <button className="boris-btn-hover" onClick={() => { setShowPublishConfirm(false); addAdsToFeed(); }} style={{flex:1, background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px", fontWeight:"bold", cursor:"pointer"}}>✅ Всё верно, публикуем</button>
                              <button className="boris-btn-hover" onClick={() => setShowPublishConfirm(false)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"12px", cursor:"pointer"}}>Отмена</button>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                    {feedUrl && (
                      <div style={{marginTop:"16px", padding:"12px", background:"#E7EFFE", border:"1px solid #2F6FED", borderRadius:"8px"}}>
                        <div style={{color:"#2F6FED", fontWeight:"bold", marginBottom:"6px"}}>✅ Фид готов! Ссылка для ЛК Авито (Автозагрузка):</div>
                        <a href={feedUrl} target="_blank" style={{color:"#2F6FED", fontSize:"15px", wordBreak:"break-all"}}>{feedUrl}</a>
                      </div>
                    )}
                  </div>

                </div>
              )}
            </div>


            {showTemplateForm && (
              <div style={{background:"#F6F7FB", borderRadius:"12px", padding:"24px", border:"1px solid #E3E7F0", marginBottom:"24px"}}>
                <h3 style={{margin:"0 0 8px", fontSize:"15px"}}>Новый шаблон</h3>
                <p style={{color:"#667085", fontSize:"15px", marginBottom:"20px"}}>Переменные: {"{название}"}, {"{город}"}, {"{Вариант 1|Вариант 2|Вариант 3}"}</p>
                <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"16px"}}>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Название шаблона</label>
                    <input value={newTemplate.name} onChange={e => setNewTemplate({...newTemplate, name: e.target.value})} placeholder="Услуги в Москве" style={inputStyle} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Категория Авито</label>
                    <div style={{display:"flex", gap:"8px"}}>
                      <select className="b-select" value={mainCat} onChange={e => { const nc = e.target.value; setMainCat(nc); const firstSub = AVITO_CATEGORIES[nc].subs[0]; setSubCat(firstSub); setNewTemplate({...newTemplate, category: nc + " / " + firstSub}); }} style={{...inputStyle, flex:1}}>
                        {Object.keys(AVITO_CATEGORIES).map(c => (
                          <option key={c} value={c}>{AVITO_CATEGORIES[c].ready ? "✅ " : "🔜 "}{c}</option>
                        ))}
                      </select>
                      <select className="b-select" value={subCat} onChange={e => { const ns = e.target.value; setSubCat(ns); setNewTemplate({...newTemplate, category: mainCat + " / " + ns}); }} style={{...inputStyle, flex:1}}>
                        {AVITO_CATEGORIES[mainCat].subs.map(sc => (
                          <option key={sc} value={sc}>{sc}</option>
                        ))}
                      </select>
                    </div>
                    {!AVITO_CATEGORIES[mainCat].ready && (
                      <details style={{marginBottom:"14px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Подробнее</summary><div style={{color:"#F79009", fontSize:"15px", marginTop:"6px"}}>🔜 Эта категория пока не проверена в валидаторе Авито — выгрузка в фид может не пройти. Готово: Услуги → Стихи, поздравления, тосты.</div></details>
                    )}
                  </div>
                  <div style={{gridColumn:"1/-1"}}>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Шаблон заголовка</label>
                    <input value={newTemplate.titleTemplate} onChange={e => setNewTemplate({...newTemplate, titleTemplate: e.target.value})}
                      placeholder="{Тротуарная плитка|Брусчатка} в {город}" style={inputStyle} />
                  </div>
                  <div style={{gridColumn:"1/-1"}}>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Шаблон описания</label>
                    <textarea value={newTemplate.description} onChange={e => setNewTemplate({...newTemplate, description: e.target.value})}
                      placeholder="{описание}" rows={4} style={{...inputStyle, resize:"vertical"}} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Цена</label>
                    <select className="b-select" value={newTemplate.priceType} onChange={e => setNewTemplate({...newTemplate, priceType: e.target.value})} style={inputStyle}>
                      <option value="original">Оригинальная с сайта</option>
                      <option value="markup">Наценка %</option>
                      <option value="fixed">Фиксированная</option>
                    </select>
                  </div>
                  {newTemplate.priceType !== "original" && (
                    <div>
                      <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>{newTemplate.priceType === "markup" ? "Наценка %" : "Цена ₽"}</label>
                      <input type="number" value={newTemplate.priceModifier} onChange={e => setNewTemplate({...newTemplate, priceModifier: Number(e.target.value)})} style={inputStyle} />
                    </div>
                  )}
                  <div style={{gridColumn:"1/-1"}}>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Города</label>
                    <div style={{display:"flex", gap:"8px", marginBottom:"8px", flexWrap:"wrap"}}>
                      {Object.keys(CITY_SETS).map(setName => (
                        <button className="boris-btn-hover" key={setName} onClick={() => setNewTemplate({...newTemplate, cities: CITY_SETS[setName]})}
                          style={{padding:"6px 12px", borderRadius:"8px", border:"1px solid #2F6FED", background:"#E7EFFE", color:"#2F6FED", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>
                          + {setName}
                        </button>
                      ))}
                      <button className="boris-btn-hover" onClick={() => setNewTemplate({...newTemplate, cities: []})}
                        style={{padding:"6px 12px", borderRadius:"8px", border:"1px solid #E3E7F0", background:"#FFFFFF", color:"#667085", cursor:"pointer", fontSize:"15px"}}>
                        Очистить
                      </button>
                    </div>
                    <input value={newTemplate.cities.join(", ")} onChange={e => setNewTemplate({...newTemplate, cities: e.target.value.split(/[,\s]+/).map(c => c.trim()).filter(c => c)})}
                      placeholder="Или введите вручную: москва, спб питер" style={inputStyle} />
                    <div style={{color:"#8A93A6", fontSize:"15px", marginTop:"6px"}}>Выбрано городов: {newTemplate.cities.length}</div>
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Время публикации</label>
                    <input type="time" value={newTemplate.schedule} onChange={e => setNewTemplate({...newTemplate, schedule: e.target.value})} style={inputStyle} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Задержка мин (минут)</label>
                    <input type="number" value={newTemplate.delayMin} onChange={e => setNewTemplate({...newTemplate, delayMin: Number(e.target.value)})} style={inputStyle} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Задержка макс (минут)</label>
                    <input type="number" value={newTemplate.delayMax} onChange={e => setNewTemplate({...newTemplate, delayMax: Number(e.target.value)})} style={inputStyle} />
                  </div>
                  <div style={{gridColumn:"1/-1"}}>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Дни публикации</label>
                    <div style={{display:"flex", gap:"8px"}}>
                      {days.map(day => (
                        <button className="boris-btn-hover" key={day} onClick={() => {
                          const current = newTemplate.scheduleDays;
                          setNewTemplate({...newTemplate, scheduleDays: current.includes(day) ? current.filter(d => d !== day) : [...current, day]});
                        }} style={{padding:"8px 12px", borderRadius:"8px", border:"none", cursor:"pointer",
                          background: newTemplate.scheduleDays.includes(day) ? "#2F6FED" : "#FFFFFF",
                          color: newTemplate.scheduleDays.includes(day) ? "#F6F7FB" : "#667085", fontWeight:"bold", fontSize:"15px"}}>
                          {day}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <div style={{display:"flex", gap:"12px", marginTop:"20px"}}>
                  <button className="boris-btn-hover" onClick={() => {
                    if (!newTemplate.name) return alert("Введите название шаблона");
                    setTemplates([...templates, {...newTemplate, id: Date.now()}]);
                    setShowTemplateForm(false);
                    setNewTemplate({name:"", titleTemplate:"{название}", description:"{описание}", priceType:"original", priceModifier:0, cities:["Москва"], category:"Предложение услуг", schedule:"09:00", scheduleDays:["пн","вт","ср","чт","пт"], delayMin:2, delayMax:3});
                  }} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"8px", padding:"10px 24px", fontWeight:"bold", cursor:"pointer"}}>Сохранить</button>
                  <button className="boris-btn-hover" onClick={() => setShowTemplateForm(false)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"10px 24px", cursor:"pointer"}}>Отмена</button>
                </div>
              </div>
            )}

            <div style={{display:"grid", gap:"16px"}}>
              {templates.map(t => (
                <div key={t.id} style={{background:"#F6F7FB", borderRadius:"12px", padding:"24px", border:"1px solid #EEF2FA"}}>
                  <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"12px"}}>
                    <div style={{fontSize:"23px", fontWeight:"bold"}}>{t.name}</div>
                    <div style={{display:"flex", gap:"8px"}}>
                      <button className="b-btn b-btn-primary" onClick={() => { setSelectedTemplate(t); setActiveTab("parser"); }} style={{padding:"8px 16px", fontSize:"13px"}}>Использовать</button>
                      <button className="boris-btn-hover" onClick={() => setTemplates(templates.filter(x => x.id !== t.id))} style={{background:"#FDEDEC", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"6px 14px", cursor:"pointer", fontSize:"15px"}}>Удалить</button>
                    </div>
                  </div>
                  <div style={{display:"grid", gridTemplateColumns:"repeat(3, 1fr)", gap:"12px"}}>
                    <div className="b-meta" style={{overflow:"hidden", textOverflow:"ellipsis"}} title={t.titleTemplate}>📝 {t.titleTemplate}</div>
                    <div className="b-meta">💰 {t.priceType === "original" ? "С сайта" : t.priceType === "markup" ? `+${t.priceModifier}%` : `${t.priceModifier} ₽`}</div>
                    <div className="b-meta" style={{overflow:"hidden", textOverflow:"ellipsis"}}>📍 {(t.cities || []).join(", ")}</div>
                    <div className="b-meta" style={{overflow:"hidden", textOverflow:"ellipsis"}}>🕐 {t.schedule} / {(t.scheduleDays || []).join(", ")}</div>
                    <div className="b-meta">⏱ Задержка: {t.delayMin}–{t.delayMax} мин</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}


        {activeTab === "parser" && (
          <div>
            <div className="b-panel" style={{maxWidth:"760px", marginBottom:"24px"}}>
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #4C8DFF, #2F6FED)"}}>📤</span>Выгрузка товаров с сайта</h3>
              <p className="b-sub">БОРИС найдёт все товары и перенесёт в Авито</p>

              <div style={{marginBottom:"16px"}}>
                <label className="b-label">Шаблон для выгрузки</label>
                <select className="b-select" value={selectedTemplate?.id || ""} onChange={e => setSelectedTemplate(templates.find(t => t.id === Number(e.target.value)) || null)} style={inputStyle}>
                  <option value="">Выберите шаблон</option>
                  {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </div>
              <div style={{marginBottom:"20px", padding:"20px", background:"#F8FAFF", border:"1px solid #E7EFFE", borderRadius:"12px"}}>
                <h4 style={{margin:"0 0 6px", color:"#1D2939", fontSize:"18px", fontWeight:800, display:"flex", alignItems:"center", gap:"10px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #9B87F5, #7C5CFC)", width:"30px", height:"30px", fontSize:"15px"}}>🔁</span>Или растяните шаблон без сайта</h4>
                <details style={{marginBottom:"14px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Подробнее</summary><p style={{color:"#667085", fontSize:"14px", marginBottom:"14px", lineHeight:1.5}}>Выберите шаблон выше, впишите тему — Борис сгенерирует названия и разошлёт их через выбранный шаблон с уникализацией</p></details>
                <div style={{display:"flex", gap:"12px", marginBottom:"10px", flexWrap:"wrap"}}>
                  <input value={stretchTopic} onChange={e => setStretchTopic(e.target.value)} placeholder="Тема партии, напр. стихи на заказ" style={{...inputStyle, flex:1, minWidth:"200px"}} />
                  <input type="number" value={stretchCount} onChange={e => setStretchCount(Math.max(1, Math.min(50, Number(e.target.value)||1)))} style={{...inputStyle, width:"90px"}} />
                  <button onClick={generateStretchNames} disabled={stretchLoading} className="b-btn b-btn-primary" style={{background: stretchLoading ? "#E3E7F0" : undefined, borderColor: stretchLoading ? "#E3E7F0" : undefined, cursor: stretchLoading ? "wait" : "pointer"}}>
                    {stretchLoading ? "⏳ Думаю..." : "✨ Сгенерировать названия"}
                  </button>
                </div>
                {stretchNames.length > 0 && (
                  <div>
                    <div style={{color:"#667085", fontSize:"15px", marginBottom:"8px"}}>Названия (можно отредактировать каждое):</div>
                    <div style={{display:"grid", gap:"6px", maxHeight:"240px", overflowY:"auto", marginBottom:"12px"}}>
                      {stretchNames.map((name, i) => (
                        <input key={i} value={name} onChange={e => setStretchNames(prev => prev.map((n, ni) => ni === i ? e.target.value : n))} style={{...inputStyle, fontSize:"15px", padding:"8px"}} />
                      ))}
                    </div>
                    <button onClick={publishStretch} className="b-btn b-btn-primary">
                      📤 Опубликовать {stretchNames.length} объявлений
                    </button>
                  </div>
                )}
              </div>
            </div>


            {publishStatus && (
              <div style={{background:"#F6F7FB", borderRadius:"8px", padding:"16px", border:"1px solid #E3E7F0", marginBottom:"16px", color:"#2F6FED"}}>
                {publishStatus}
              </div>
            )}

              <div>
                <div className="b-panel" style={{marginBottom:"18px"}}>
                  <div className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #4C8DFF, #2F6FED)"}}>🌐</span>Выгрузка карточек с сайта</div>
                  <details style={{marginBottom:"14px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Подробнее</summary><div className="b-sub" style={{marginBottom:"14px"}}>Вставьте ссылку на товар или на каталог/категорию (можно несколько, каждая с новой строки) — Борис выгрузит карточки с фото, ценами и характеристиками. Задача уйдёт в очередь, результат появится через 10-30 секунд на ссылку.</div></details>
                  <div style={{display:"flex", gap:"8px", flexWrap:"wrap", alignItems:"flex-start"}}>
                    <textarea value={parseUrl} onChange={e => setParseUrl(e.target.value)} placeholder={"https://сайт-клиента.ru/товар-или-каталог\nhttps://сайт-клиента.ru/другой-каталог"}
                      rows={3} className="b-input" style={{flex:1, minWidth:"280px", fontFamily:"inherit", resize:"vertical"}} />
                    <div style={{display:"flex", flexDirection:"column", gap:"4px", minWidth:"140px"}}>
                      <label style={{fontSize:"12px", color:"#667085"}}>Сколько товаров</label>
                      <input type="number" min={0} value={parseLimit || ""} onChange={e => setParseLimit(Math.max(0, parseInt(e.target.value) || 0))} placeholder="0 = все" className="b-input" style={{width:"100%"}} />
                    </div>
                    <button onClick={startParse} disabled={parseLoading || !parseUrl.trim()}
                      className="b-btn b-btn-primary" style={{opacity: parseLoading ? 0.6 : 1}}>
                      {parseLoading ? "⏳ Выгружаю..." : "🌐 Выгрузить"}
                    </button>
                    <button onClick={loadParsedProducts} className="b-btn b-btn-ghost">
                      🔄 Обновить
                    </button>
                  </div>
                  {parseProgress && (
                    <div style={{fontSize:"13px", color:"#2F6FED", marginTop:"10px", display:"flex", alignItems:"center", gap:"8px"}}>
                      ⏳ Борис выгружает карточки... (товар {parseProgress.done} из {parseProgress.total})
                    </div>
                  )}
                  {parseSourceUrls.length > 0 && (
                    <div style={{fontSize:"12px", color:"#8A93A6", marginTop:"10px"}}>
                      Источник{parseSourceUrls.length > 1 ? "и" : ""}: {parseSourceUrls.map((u, i) => (
                        <span key={u}>{i > 0 && ", "}<a href={u} target="_blank" rel="noopener noreferrer" style={{color:"#2F6FED"}}>{u}</a></span>
                      ))} · выгружено карточек: {parsedProducts.length}
                    </div>
                  )}
                </div>

                {parsedProducts.length > 0 && (
                  <div style={{marginBottom:"14px"}}>
                    <div className="b-panel" style={{marginBottom:"16px", padding:"18px"}}>
                      <label className="b-label">📋 Образец / пожелания к текстам <span style={{fontWeight:400, color:"#8A93A6"}}>— Борис возьмёт отсюда общую информацию о компании и стиль, а конкретику по каждому товару и ключевые слова придумает свои</span></label>
                      <textarea className="b-input" value={genSample} onChange={e => setGenSample(e.target.value)} placeholder="Вставь пример удачного объявления, бриф клиента или описание компании: условия доставки, гарантии, преимущества, тон подачи…" rows={5} style={{resize:"vertical", fontFamily:"inherit"}} />
                      <div style={{display:"flex", gap:"8px", marginTop:"12px", flexWrap:"wrap", alignItems:"center"}}>
                        <button className="b-btn" onClick={saveTextSample} style={{background:"#FFFFFF", color:"#12805C", border:"1px solid #C7EBDC"}}>💾 Сохранить как…</button>
                        {genSample && <button className="b-btn b-btn-ghost" onClick={() => setGenSample("")}>Очистить</button>}
                      </div>
                      {textSamples.length > 0 && (
                        <div style={{marginTop:"14px", paddingTop:"14px", borderTop:"1px solid #EEF2FA"}}>
                          <div className="b-meta" style={{marginBottom:"8px", fontWeight:700}}>Сохранённые образцы ({textSamples.length})</div>
                          {textSamples.map((t: any) => (
                            <div key={t.id} style={{display:"flex", alignItems:"center", gap:"8px", padding:"8px 0", borderBottom:"1px solid #F6F7FB"}}>
                              <span className="b-meta" style={{flex:1, overflow:"hidden", textOverflow:"ellipsis", color:"#1D2939", fontWeight:600}}>{t.name}</span>
                              <button className="b-btn b-btn-ghost" onClick={() => setGenSample(t.text)}>↩ Загрузить</button>
                              <button className="b-btn" onClick={() => deleteTextSample(t.id)} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438"}}>🗑</button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <details style={{marginBottom:"14px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Подробнее</summary><div style={{fontSize:"12px", color:"#8A93A6", marginBottom:"8px"}}>Конвейер: 1) выгрузка (готово) → 2) обогатить тексты + отправить в черновики → 3) баннеры на партию → 4) проверить фид → 5) опубликовать.</div></details>
                    <div style={{display:"flex", gap:"10px", alignItems:"center", flexWrap:"wrap"}}>
                      <button onClick={fetchAllGalleries} disabled={allGalleriesLoading}
                        className="b-btn b-btn-primary" style={{background:"#7C5CFC", borderColor:"#7C5CFC", padding:"9px 16px", fontSize:"13px"}}>
                        🖼 {allGalleriesLoading ? "Запускаю..." : "Собрать все фото (в фоне)"}
                      </button>
                      <button onClick={enrichDescriptions} disabled={enrichLoading}
                        className="b-btn b-btn-primary" style={{background:"#EE46BC", borderColor:"#EE46BC", padding:"9px 16px", fontSize:"13px"}}>
                        ✨ {enrichLoading ? "Пишу описания..." : "Обогатить тексты ИИ (в фоне)"}
                      </button>
                      <button onClick={() => setSelectedParsedIdx(selectedParsedIdx.size === parsedProducts.length ? new Set() : new Set(parsedProducts.map((_,i)=>i)))}
                        className="b-btn b-btn-soft" style={{padding:"9px 16px", fontSize:"13px"}}>
                        {selectedParsedIdx.size === parsedProducts.length ? "☑ Снять выбор" : "☐ Выбрать все"}
                      </button>
                      {selectedParsedIdx.size > 0 && (
                        <>
                          <span style={{fontSize:"13px", color:"#5B6472"}}>Выбрано: {selectedParsedIdx.size}</span>
                          <button onClick={deleteSelectedParsed}
                            className="b-btn b-btn-primary" style={{background:"#F04438", borderColor:"#F04438", padding:"9px 16px", fontSize:"13px"}}>
                            🗑 Удалить выбранные
                          </button>
                        </>
                      )}
                    </div>
                    {enrichProgress && <div style={{fontSize:"12px", color:"#8A93A6", marginTop:"6px"}}>{enrichProgress}</div>}
                    {selectedParsedIdx.size > 0 && (
                      <div style={{display:"flex", gap:"8px", alignItems:"center", flexWrap:"wrap", marginTop:"10px", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px"}}>
                        <input value={pipelineBatchLabelInput} onChange={e => setPipelineBatchLabelInput(e.target.value)} placeholder="Название партии, напр. Детские"
                          className="b-input" style={{minWidth:"200px", fontSize:"13px", padding:"7px 10px"}} />
                        <input value={pipelineTopicInput} onChange={e => setPipelineTopicInput(e.target.value)} placeholder="Ниша для категории (необязательно)"
                          className="b-input" style={{minWidth:"220px", fontSize:"13px", padding:"7px 10px"}} />
                        <button onClick={sendSelectedToDrafts} disabled={toDraftsLoading}
                          className="b-btn b-btn-primary" style={{background:"#12805C", borderColor:"#12805C", padding:"9px 16px", fontSize:"13px"}}>
                          📤 {toDraftsLoading ? "Создаю..." : "В черновики"}
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {pipelineBatch && (
                  <div className="b-panel" style={{marginBottom:"18px", border:"1px solid #E7EFFE"}}>
                    <div className="b-title" style={{fontSize:"15px", display:"flex", alignItems:"center", gap:"10px"}}>
                      <span className="b-icon-sm" style={{background:"linear-gradient(135deg, #12805C, #1E8E5A)"}}>📦</span>
                      Партия «{pipelineBatch.label}» — {pipelineBatch.count} черновиков, категория «{pipelineBatch.category}»
                    </div>
                    <div style={{display:"flex", gap:"8px", alignItems:"center", flexWrap:"wrap", margin:"12px 0"}}>
                      <input type="number" value={bannerCount} onChange={e => setBannerCount(Math.max(1, Number(e.target.value) || 1))} style={{width:"70px", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"7px", fontSize:"13px"}} title="Сколько разных баннеров" />
                      <input type="number" value={photosPerAdInput} onChange={e => setPhotosPerAdInput(Math.max(1, Number(e.target.value) || 1))} style={{width:"70px", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"7px", fontSize:"13px"}} title="Фото на объявление (баннер + обычные)" />
                      <input value={bannerFolderInput} onChange={e => setBannerFolderInput(e.target.value)} placeholder="Папка с доп. фото (необязательно)"
                        className="b-input" style={{minWidth:"200px", fontSize:"13px", padding:"7px 10px"}} />
                      <button onClick={runBatchBanners} disabled={batchBannerLoading}
                        className="b-btn b-btn-primary" style={{background:"#F79009", borderColor:"#F79009", padding:"9px 16px", fontSize:"13px"}}>
                        🎨 {batchBannerLoading ? "Генерирую..." : "Баннеры на партию"}
                      </button>
                      <button onClick={runBatchValidate} disabled={pipelineValidateLoading}
                        className="b-btn b-btn-primary" style={{background:"#7C5CFC", borderColor:"#7C5CFC", padding:"9px 16px", fontSize:"13px"}}>
                        🔍 {pipelineValidateLoading ? "Проверяю..." : "Проверить фид (до публикации)"}
                      </button>
                      <button onClick={runBatchPublish} disabled={pipelinePublishLoading}
                        className="b-btn b-btn-primary" style={{background:"#2F6FED", borderColor:"#2F6FED", padding:"9px 16px", fontSize:"13px"}}>
                        🚀 {pipelinePublishLoading ? "Публикую..." : "Опубликовать сейчас"}
                      </button>
                    </div>
                    {bannerResult && <div style={{fontSize:"13px", color:"#5B6472", marginBottom:"8px"}}>{bannerResult}</div>}
                    {pipelineValidateResult && (
                      <div style={{background: pipelineValidateResult.status === "ok" ? "#F0FBF6" : "#FEF7EC", border:"1px solid " + (pipelineValidateResult.status === "ok" ? "#C7EBDC" : "#FDE9C8"), borderRadius:"8px", padding:"12px", fontSize:"12px", color:"#344054", whiteSpace:"pre-wrap", maxHeight:"260px", overflowY:"auto"}}>
                        <b>{pipelineValidateResult.status === "ok" ? "✅ Фид корректен" : "⚠️ Есть замечания"}</b>
                        <div style={{marginTop:"6px"}}>{pipelineValidateResult.raw_text}</div>
                      </div>
                    )}
                  </div>
                )}

                {parsedProducts.length === 0 ? (
                  <div style={{textAlign:"center", padding:"40px", color:"#8A93A6", fontSize:"14px"}}>Пока нет выгруженных карточек. Вставьте ссылку выше и нажмите «Выгрузить».</div>
                ) : (
                  <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(260px, 1fr))", gap:"16px"}}>
                    {parsedProducts.map((p, idx) => (
                      <div key={idx} className="b-card" style={{padding:0, border:"1px solid #E3E7F0", display:"flex", flexDirection:"column"}}>
                        <div style={{height:"180px", background:"#F5FAFF", display:"flex", alignItems:"center", justifyContent:"center", overflow:"hidden", position:"relative"}}>
                          <input type="checkbox" checked={selectedParsedIdx.has(idx)} onChange={() => toggleParsedSelect(idx)}
                            style={{position:"absolute", top:"8px", left:"8px", width:"20px", height:"20px", cursor:"pointer", zIndex:5, accentColor:"#2F6FED"}} />
                          {p.image ? <img src={p.image} alt={p.title} onClick={() => { const imgs = p.images && p.images.length > 0 ? p.images : [p.image]; setPgLightbox(imgs); setPgLightboxIdx(0); }} style={{width:"100%", height:"100%", objectFit:"contain", background:"#F6F7FB", cursor:"zoom-in"}} /> : <span style={{color:"#C9D2E3", fontSize:"13px"}}>нет фото</span>}
                          {p.images && p.images.length > 1 && <span style={{position:"absolute", bottom:"6px", right:"6px", background:"rgba(0,0,0,0.6)", color:"#fff", fontSize:"11px", padding:"2px 6px", borderRadius:"4px"}}>📷 {p.images.length}</span>}
                        </div>
                        <div style={{padding:"14px", display:"flex", flexDirection:"column", gap:"8px", flex:1}}>
                          {editingParsedIdx === idx ? (
                            <>
                              <input defaultValue={p.title} id={`parsed-title-${idx}`} style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"13px", fontWeight:600}} />
                              <input defaultValue={p.price} id={`parsed-price-${idx}`} style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"13px"}} />
                              <FormatToolbar textareaId={`parsed-desc-${idx}`} />
                              <textarea defaultValue={p.ai_description || p.description || ""} id={`parsed-desc-${idx}`} rows={5}
                                placeholder="Описание товара (можно сгенерировать через ИИ ниже)"
                                style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"12px", fontFamily:"inherit"}} />
                              <button onClick={() => genDescriptionForCard(idx)} disabled={genDescLoading}
                                className="b-btn b-btn-primary" style={{background:"#7C5CFC", borderColor:"#7C5CFC", padding:"8px", fontSize:"12px"}}>
                                ✨ {genDescLoading ? "Генерирую..." : "Сгенерировать ИИ-описание"}
                              </button>
                              <select className="b-select" id={`parsed-template-${idx}`} defaultValue="" style={{width:"100%", boxSizing:"border-box", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"12px"}}>
                                <option value="">— применить структуру шаблона —</option>
                                {templates.filter((t:any) => t.id).map((t:any) => <option key={t.id} value={t.id}>{t.name}</option>)}
                              </select>
                              <div style={{display:"flex", gap:"6px"}}>
                                <button onClick={() => {
                                    const title = (document.getElementById(`parsed-title-${idx}`) as HTMLInputElement)?.value;
                                    const price = (document.getElementById(`parsed-price-${idx}`) as HTMLInputElement)?.value;
                                    const desc = (document.getElementById(`parsed-desc-${idx}`) as HTMLTextAreaElement)?.value;
                                    saveParsedEdit(idx, { title, price, ai_description: desc });
                                  }} style={{flex:1, background:"#2F6FED", color:"#fff", border:"none", borderRadius:"6px", padding:"7px", fontSize:"13px", fontWeight:600, cursor:"pointer"}}>Сохранить</button>
                                <button onClick={() => setEditingParsedIdx(null)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"7px", fontSize:"13px", cursor:"pointer"}}>Отмена</button>
                              </div>
                            </>
                          ) : (
                            <>
                          <div style={{fontSize:"14px", fontWeight:600, color:"#14161A", lineHeight:1.3}}>{p.title}</div>
                          {p.price && <div style={{fontSize:"16px", fontWeight:800, color:"#2F6FED"}}>{p.price}</div>}
                          {p.ai_description && <div style={{fontSize:"12px", color:"#1E8E5A", background:"#F0FBF6", borderRadius:"6px", padding:"6px 8px"}}>✨ {p.ai_description.slice(0,120)}...</div>}
                          {p.characteristics && Object.keys(p.characteristics).length > 0 && (
                            <div style={{fontSize:"12px", color:"#5B6472", lineHeight:1.5}}>
                              {Object.entries(p.characteristics).slice(0,4).map(([k,v]: any, i) => (
                                <div key={i}><b>{k}:</b> {String(v).slice(0,50)}</div>
                              ))}
                            </div>
                          )}
                          <button className="boris-btn-hover" onClick={() => setEditingParsedIdx(idx)} style={{alignSelf:"flex-start", fontSize:"11px", color:"#7C5CFC", background:"#FFFFFF", border:"none", cursor:"pointer", padding:0, textDecoration:"underline"}}>✏️ Редактировать</button>
                            </>
                          )}
                          {p.banner_url && (
                            <div style={{marginTop:"8px"}}>
                              <img src={p.banner_url} alt="Баннер" onClick={() => { setPgLightbox([p.banner_url]); setPgLightboxIdx(0); }} style={{width:"100%", borderRadius:"8px", border:"1px solid #EAF4FF", cursor:"zoom-in"}} />
                              <div style={{fontSize:"11px", color:"#1E8E5A", marginTop:"4px"}}>✅ Баннер готов</div>
                            </div>
                          )}
                          <div style={{marginTop:"auto", display:"flex", gap:"8px", flexWrap:"wrap", paddingTop:"8px"}}>
                            {p.product_url && <a href={p.product_url} target="_blank" rel="noopener noreferrer" className="boris-btn-hover" style={{fontSize:"12px", color:"#2F6FED", textDecoration:"none", border:"1px solid #2F6FED", borderRadius:"6px", padding:"6px 10px", fontWeight:600}}>↗ На сайте</a>}
                            <button className="boris-btn-hover" onClick={() => fetchGalleryForCard(idx)} disabled={galleryLoading === idx}
                              style={{fontSize:"12px", background:"#F5F5F7", color:"#5B6472", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 10px", fontWeight:600, cursor:"pointer"}}>
                              🖼 {galleryLoading === idx ? "Собираю..." : "Все фото"}
                            </button>
                            <button className="boris-btn-hover" onClick={() => genProductBanner(p, idx)} style={{fontSize:"12px", background:"#E08A2C", color:"#fff", border:"none", borderRadius:"10px", padding:"6px 10px", fontWeight:600, cursor:"pointer"}}>🎨 {p.banner_url ? "Пересоздать" : "Баннер"}</button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            {pgLightbox.length > 0 && (
              <div onClick={() => setPgLightbox([])} style={{position:"fixed", inset:0, background:"rgba(10,14,20,0.93)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:5000, padding:"30px"}}>
                <button onClick={(e) => { e.stopPropagation(); setPgLightbox([]); }} style={{position:"absolute", top:"20px", right:"30px", background:"#fff", border:"none", color:"#101828", fontSize:"15px", fontWeight:700, cursor:"pointer", padding:"10px 22px", borderRadius:"22px", zIndex:10, boxShadow:"0 4px 16px rgba(0,0,0,0.3)"}}>✕ Закрыть</button>
                {pgLightbox.length > 1 && (
                  <button onClick={(e) => { e.stopPropagation(); setPgLightboxIdx(i => (i - 1 + pgLightbox.length) % pgLightbox.length); }} style={{position:"absolute", left:"30px", top:"50%", transform:"translateY(-50%)", background:"rgba(255,255,255,0.15)", border:"none", color:"#fff", fontSize:"34px", cursor:"pointer", width:"56px", height:"56px", borderRadius:"50%", zIndex:10}}>‹</button>
                )}
                <img src={pgLightbox[pgLightboxIdx]} alt="Фото" onClick={e => e.stopPropagation()} style={{maxWidth:"88vw", maxHeight:"88vh", objectFit:"contain", borderRadius:"10px", boxShadow:"0 20px 60px rgba(0,0,0,0.5)"}} />
                {pgLightbox.length > 1 && (
                  <button onClick={(e) => { e.stopPropagation(); setPgLightboxIdx(i => (i + 1) % pgLightbox.length); }} style={{position:"absolute", right:"30px", top:"50%", transform:"translateY(-50%)", background:"rgba(255,255,255,0.15)", border:"none", color:"#fff", fontSize:"34px", cursor:"pointer", width:"56px", height:"56px", borderRadius:"50%", zIndex:10}}>›</button>
                )}
                {pgLightbox.length > 1 && (
                  <div style={{position:"absolute", bottom:"24px", left:"50%", transform:"translateX(-50%)", color:"#fff", fontSize:"14px", background:"rgba(0,0,0,0.4)", padding:"6px 16px", borderRadius:"16px"}}>{pgLightboxIdx + 1} / {pgLightbox.length}</div>
                )}
              </div>
            )}
          </div>
        )}

        {activeTab === "webdesign" && (
          <div>
            {invoiceOpen && (
  <div onClick={()=>setInvoiceOpen(false)} style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.55)", zIndex:9999, display:"flex", alignItems:"center", justifyContent:"center", padding:"20px"}}>
    <div onClick={e=>e.stopPropagation()} style={{background:"#fff", borderRadius:"16px", maxWidth:"460px", width:"100%", padding:"26px 28px"}}>
      <div style={{fontSize:"18px", fontWeight:800, color:"#1D2939", marginBottom:"6px"}}>Счёт для оплаты по безналу</div>
      <div style={{fontSize:"13px", color:"#667085", marginBottom:"18px"}}>Укажите реквизиты вашей организации — счёт сформируется автоматически с суммой и назначением платежа.</div>
      <label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>Название организации *</label>
      <input value={payerName} onChange={e=>setPayerName(e.target.value)} placeholder="ООО «Ромашка» / ИП Иванов И.И." style={{width:"100%", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px", marginBottom:"14px"}} />
      <label style={{display:"block", fontSize:"13px", color:"#667085", marginBottom:"5px"}}>ИНН плательщика</label>
      <input value={payerInn} onChange={e=>setPayerInn(e.target.value)} placeholder="7700000000" style={{width:"100%", padding:"10px 12px", border:"1px solid #E3E7F0", borderRadius:"10px", fontSize:"14px", marginBottom:"20px"}} />
      <div style={{display:"flex", gap:"10px"}}>
        <button onClick={createInvoice} disabled={invoiceBusy} style={{flex:1, background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"12px", fontSize:"15px", fontWeight:700, cursor: invoiceBusy?"default":"pointer", opacity: invoiceBusy?0.6:1}}>{invoiceBusy ? "Формируем…" : "Сформировать счёт (PDF)"}</button>
        <button onClick={()=>setInvoiceOpen(false)} style={{background:"#F2F4F7", color:"#475467", border:"none", borderRadius:"10px", padding:"12px 18px", fontSize:"15px", fontWeight:600, cursor:"pointer"}}>Отмена</button>
      </div>
    </div>
  </div>
)}
{lightboxUrl && (<div onClick={() => setLightboxUrl("")} style={{position:"fixed", top:0, left:0, width:"100vw", height:"100vh", background:"rgba(16,24,40,0.88)", zIndex:99999, maxWidth:"none", margin:0, display:"flex", alignItems:"center", justifyContent:"center", cursor:"zoom-out", padding:"40px"}}><img src={lightboxUrl} onClick={(e) => e.stopPropagation()} style={{maxWidth:"calc(100vw - 48px)", maxHeight:"calc(100vh - 48px)", width:"auto", height:"auto", objectFit:"contain", borderRadius:"12px", display:"block", cursor:"default"}} /><button onClick={() => setLightboxUrl("")} style={{position:"absolute", top:"24px", right:"28px", background:"#FFFFFF", color:"#2F6FED", border:"none", borderRadius:"10px", width:"44px", height:"44px", fontSize:"22px", cursor:"pointer"}}>×</button></div>)}
            <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"20px", marginBottom:"28px", alignItems:"stretch", minHeight:"calc(100vh - 190px)"}}>
              {(() => {
                const now = new Date();
                                const daySeed = now.getFullYear() * 10000 + (now.getMonth() + 1) * 100 + now.getDate();
                const goal = (i: number, min: number, max: number) => {
                  const x = Math.sin(daySeed * (i + 7.13) * 12.9898) * 43758.5453;
                  return min + Math.floor((x - Math.floor(x)) * (max - min + 1));
                };
                const passed = (now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds()) / 86400;
                const grown = (i: number, min: number, max: number) => {
                  const v = Math.round(goal(i, min, max) * passed);
                  return v < 1 && passed > 0.02 ? 1 : v;
                };
                const cards = [
                  { icon: "🎨", grad: "linear-gradient(135deg,#F06AA8,#D93D86)", n: grown(1, 1, 100), label: "баннеров для объявлений", verb: "Сделал" },
                  { icon: "📸", grad: "linear-gradient(135deg,#4C8DFF,#2F6FED)", n: grown(2, 140, 1600), label: "качественных фото", verb: "Собрал" },
                  { icon: "🖼", grad: "linear-gradient(135deg,#7C5CFC,#5B3FD9)", n: grown(3, 1, 90), label: "баннеров для тарифов Максимальный и Расширенный", verb: "Оформил" },
                ];
                return (
                  <div>
                    <div className="b-label" style={{marginBottom:"14px", fontSize:"14px"}}>Сегодня Борис для наших клиентов:</div>
                    <div style={{display:"flex", flexDirection:"column", gap:"20px", height:"100%", justifyContent:"space-between"}}>
                      {cards.map((c, i) => (
                        <div key={i} className="b-card b-stat" style={{animationDelay: `${i * 0.13}s`}}>
                          <span className="b-stat-ico" style={{background: c.grad}}>{c.icon}</span>
                          <div style={{minWidth:0, flex:1}}>
                            <div className="b-meta" style={{marginBottom:"4px", fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px"}}>{c.verb}</div>
                            <div className="b-stat-num"><CountUp to={c.n} delay={i * 130} /></div>
                            <div className="b-meta" style={{whiteSpace:"normal", lineHeight:1.4, marginTop:"5px", fontSize:"13px"}}>{c.label}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            <div>
              <div className="b-label" style={{marginBottom:"14px", fontSize:"14px", visibility:"hidden"}}>.</div>
              <div style={{display:"flex", flexDirection:"column", gap:"20px", height:"100%", justifyContent:"space-between"}}>
                <div className="b-card b-stat" onClick={() => setWebdesignFolder(webdesignFolder === "photos" ? null : "photos")} style={{cursor:"pointer", animationDelay:"0.06s", background: webdesignFolder === "photos" ? "#E7EFFE" : "#FFFFFF", borderColor: webdesignFolder === "photos" ? "#2F6FED" : "#E3E7F0"}}>
                  <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)"}}>📤</span>
                  <div><div style={{color:"#2F6FED", fontWeight:700, fontSize:"16px"}}>Мои фото</div><div className="b-meta" style={{marginTop:"3px"}}>Загруженные вами изображения</div></div>
                </div>
                <div className="b-card b-stat" onClick={() => setWebdesignFolder(webdesignFolder === "studio" ? null : "studio")} style={{cursor:"pointer", animationDelay:"0.19s", background: webdesignFolder === "studio" ? "#F0ECFF" : "#FFFFFF", borderColor: webdesignFolder === "studio" ? "#7C5CFC" : "#E3E7F0"}}>
                  <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#F06AA8,#D93D86)"}}>🎨</span>
                  <div><div style={{color:"#7C5CFC", fontWeight:700, fontSize:"16px"}}>Студия картинок Бориса</div><div className="b-meta" style={{marginTop:"3px"}}>Баннеры и инфографика от ИИ</div></div>
                </div>
                <div className="b-card b-stat" onClick={() => { const next = webdesignFolder === "buy" ? null : "buy"; setWebdesignFolder(next); if (next === "buy") { loadBannerGallery(); loadSavedPrompts(); } }} style={{cursor:"pointer", animationDelay:"0.32s", background: webdesignFolder === "buy" ? "#FEF3E2" : "#FFFFFF", borderColor: webdesignFolder === "buy" ? "#F79009" : "#E3E7F0"}}>
                  <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#F7A440,#E8850B)"}}>🛒</span>
                  <div><div style={{color:"#F79009", fontWeight:700, fontSize:"16px"}}>Купить баннеры</div><div className="b-meta" style={{marginTop:"3px"}}>Готовые баннеры из галереи</div></div>
                </div>
              </div>
            </div>
            </div>
              <details style={{display: webdesignFolder === "buy" ? "block" : "none", marginTop:"0", marginBottom:"28px", marginLeft:"0"}}><summary style={{cursor:"pointer", color:"#1D2939", fontSize:"18px", fontWeight:600, marginBottom:"10px"}}>Купить баннеры</summary><div style={{fontSize:"14px", color:"#667085", marginBottom:"14px"}}>20 инфографик в месяц уже входят в подписку. Ниже — докупка сверх лимита.</div><div style={{display:"grid", gridTemplateColumns:"repeat(4, minmax(0, 1fr))", gap:"12px", alignItems:"stretch"}}><div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}><div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#98A2B3,#667085)", opacity:0.08}} /><div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#98A2B3,#667085)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>📋</div><div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>1 баннер</div><div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>250 ₽</div><div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"14px", minHeight:"34px"}}>инфографика для объявления</div><div style={{marginTop:"auto", width:"100%"}}><RobokassaButton invoiceId="hlcZfzwZDUWWMhJitJ_wnQ" pack="ban1" accountId={currentAccount} /></div></div><div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}><div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", opacity:0.08}} /><div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>📋</div><div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>10 баннеров</div><div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>2 300 ₽</div><div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"14px", minHeight:"34px"}}>популярный пакет</div><div style={{marginTop:"auto", width:"100%"}}><RobokassaButton invoiceId="i_DhFWpOYEyAf120Y4Djzg" pack="ban10" accountId={currentAccount} /></div></div><div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}><div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#32D583,#12805C)", opacity:0.08}} /><div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#32D583,#12805C)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>📋</div><div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>30 баннеров</div><div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>6 300 ₽</div><div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"14px", minHeight:"34px"}}>выгоднее за штуку</div><div style={{marginTop:"auto", width:"100%"}}><RobokassaButton invoiceId="scSJQwIYv0Ke4vDJwMjC3w" pack="ban30" accountId={currentAccount} /></div></div><div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}><div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#98A2B3,#667085)", opacity:0.08}} /><div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#98A2B3,#667085)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>📋</div><div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>50 баннеров</div><div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>10 500 ₽</div><div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"14px", minHeight:"34px"}}>для каталога</div><div style={{marginTop:"auto", width:"100%"}}><RobokassaButton invoiceId="Lc7XU4driEuytzx6a9453w" pack="ban50" accountId={currentAccount} /></div></div><div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}><div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#FF6B6B,#F04438)", opacity:0.08}} /><div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#FF6B6B,#F04438)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>📋</div><div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>100 баннеров</div><div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>20 000 ₽</div><div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"14px", minHeight:"34px"}}>максимум</div><div style={{marginTop:"auto", width:"100%"}}><RobokassaButton invoiceId="gA5P5092U0KhSaT3diCv1Q" pack="ban100" accountId={currentAccount} /></div></div><div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}><div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", opacity:0.08}} /><div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>🏪</div><div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>Расширенный тариф</div><div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>800 ₽</div><div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"14px", minHeight:"34px"}}>профиль: 1 ПК + 1 моб</div><div style={{marginTop:"auto", width:"100%"}}><RobokassaButton invoiceId="rpurvpER-UmZnPckeCCkiQ" pack="prof_ext" accountId={currentAccount} /></div></div><div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}><div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#FDB022,#F79009)", opacity:0.08}} /><div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#FDB022,#F79009)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>🏪</div><div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>Максимальный тариф</div><div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>2 400 ₽</div><div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"14px", minHeight:"34px"}}>профиль: 3 ПК + 3 моб</div><div style={{marginTop:"auto", width:"100%"}}><RobokassaButton invoiceId="O1Xeq-_fAkeAija1zkFpvw" pack="prof_max" accountId={currentAccount} /></div></div></div></details>

            <div style={{display: (webdesignFolder === "buy" && userRole === "owner") ? "block" : "none", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", marginBottom:"24px"}}>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom: showBannerPricing ? "8px" : 0}}>
                <h3 style={{margin:0, color:"#1D2939", fontSize:"18px", display:"flex", alignItems:"center", gap:"10px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#32D583,#12805C)", width:"30px", height:"30px", fontSize:"15px", display:"inline-flex", alignItems:"center", justifyContent:"center", borderRadius:"50%", flexShrink:0}}>💰</span>Себестоимость (видите только вы)</h3>
                <button className="boris-btn-hover" onClick={() => setShowBannerPricing(!showBannerPricing)} style={{background:"#FFFFFF", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", color:"#2F6FED", cursor:"pointer", fontSize:"15px"}}>{showBannerPricing ? "Скрыть цены" : "Показать цены"}</button>
              </div>
              <p style={{color:"#2F6FED", fontSize:"15px", marginBottom: showBannerPricing ? "18px" : 0}}>✅ 20 инфографик/мес входят в подписку бесплатно. Расширенный/Максимальный — отдельно.</p>
              <div style={{display: showBannerPricing ? "grid" : "none", gridTemplateColumns:"repeat(auto-fill, minmax(260px, 1fr))", gap:"14px"}}>
                <div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", position:"relative", overflow:"hidden"}}>
                  <div style={{position:"absolute", top:"-40px", right:"-40px", width:"120px", height:"120px", borderRadius:"50%", background:"linear-gradient(135deg,#2F6FED,#1E4FD8)", opacity:0.08}} /><div style={{width:"56px", height:"56px", borderRadius:"50%", background:"linear-gradient(135deg,#2F6FED,#1E4FD8)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"24px", marginBottom:"14px"}}>📋</div><div style={{color:"#1D2939", fontWeight:700, fontSize:"17px", marginBottom:"12px"}}>Инфографика (объявление)</div>
                  <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"4px"}}>1080×1080 · 1 изображение</div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px", marginTop:"10px"}}><span style={{color:"#667085"}}>Черновик</span><span style={{color:"#2F6FED"}}>~0.45₽</span></div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px"}}><span style={{color:"#667085"}}>Стандарт</span><span style={{color:"#2F6FED"}}>~3.6₽</span></div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px"}}><span style={{color:"#667085"}}>Премиум</span><span style={{color:"#2F6FED"}}>~19₽</span></div>
                </div>
                <div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", position:"relative", overflow:"hidden"}}>
                  <div style={{position:"absolute", top:"-40px", right:"-40px", width:"120px", height:"120px", borderRadius:"50%", background:"linear-gradient(135deg,#7C5CFC,#6344E8)", opacity:0.08}} /><div style={{width:"56px", height:"56px", borderRadius:"50%", background:"linear-gradient(135deg,#7C5CFC,#6344E8)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"24px", marginBottom:"14px"}}>💎</div><div style={{color:"#1D2939", fontWeight:700, fontSize:"17px", marginBottom:"12px"}}>Расширенный тариф</div>
                  <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"4px"}}>ПК 1202×436 + моб 1242×936 · 2 изображения</div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px", marginTop:"10px"}}><span style={{color:"#667085"}}>Черновик</span><span style={{color:"#2F6FED"}}>~0.9₽</span></div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px"}}><span style={{color:"#667085"}}>Стандарт</span><span style={{color:"#2F6FED"}}>~7.2₽</span></div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px"}}><span style={{color:"#667085"}}>Премиум</span><span style={{color:"#2F6FED"}}>~38₽</span></div>
                </div>
                <div className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", position:"relative", overflow:"hidden"}}>
                  <div style={{position:"absolute", top:"-40px", right:"-40px", width:"120px", height:"120px", borderRadius:"50%", background:"linear-gradient(135deg,#F79009,#E8850B)", opacity:0.08}} /><div style={{width:"56px", height:"56px", borderRadius:"50%", background:"linear-gradient(135deg,#F79009,#E8850B)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"24px", marginBottom:"14px"}}>👑</div><div style={{color:"#1D2939", fontWeight:700, fontSize:"17px", marginBottom:"12px"}}>Максимальный тариф</div>
                  <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"4px"}}>Карусель 3 слайда × ПК/моб · 6 изображений</div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px", marginTop:"10px"}}><span style={{color:"#667085"}}>Черновик</span><span style={{color:"#2F6FED"}}>~2.7₽</span></div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px"}}><span style={{color:"#667085"}}>Стандарт</span><span style={{color:"#2F6FED"}}>~21.6₽</span></div>
                  <div style={{display:"flex", justifyContent:"space-between", fontSize:"15px"}}><span style={{color:"#667085"}}>Премиум</span><span style={{color:"#2F6FED"}}>~114₽</span></div>
                </div>
              </div>
              {showBannerPricing && <p style={{color:"#98A2B3", fontSize:"15px", marginTop:"14px", marginBottom:0}}>20 инфографик/месяц на Стандарт-качестве — себестоимость ~72₽.</p>}
            </div>

            <div style={{display: webdesignFolder === "photos" ? "block" : "none", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"24px", marginBottom:"24px"}}>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"16px"}}>
                <h3 style={{margin:0, color:"#1D2939", fontSize:"18px", display:"flex", alignItems:"center", gap:"10px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", width:"30px", height:"30px", fontSize:"15px", display:"inline-flex", alignItems:"center", justifyContent:"center", borderRadius:"50%", flexShrink:0}}>📤</span>Мои фото</h3>
                <button className="boris-btn-hover" onClick={() => createNewFolder(true)} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 14px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>➕ Создать папку</button>
              </div>
              <p style={{color:"#667085", fontSize:"15px", marginBottom:"16px"}}>Ваши личные фото (не сгенерированные ИИ) — своя удобная организация, отдельно от Студии картинок.</p>
              {openedFolder && openedFolder.startsWith("Личное_") && (
                <div style={{marginBottom:"14px"}}>
                  <input id="personal-upload-input" type="file" accept="image/*" multiple style={{display:"none"}} onChange={e => uploadForPool(e.target.files)} />
                  <button className="boris-btn-hover" onClick={() => document.getElementById("personal-upload-input")?.click()} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 18px", fontWeight:"bold", cursor:"pointer", fontSize:"15px"}}>📤 Загрузить фото в «{openedFolder.replace("Личное_", "")}»</button>
                </div>
              )}
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(120px, 1fr))", gap:"10px"}}>
                {Object.keys(allFolders).filter(f => f.startsWith("Личное_")).map(folder => (
                  <div key={folder} onClick={() => openFolder(folder)} style={{cursor:"pointer", border: openedFolder===folder ? "2px solid #2F6FED" : "2px solid #EEF2FA", borderRadius:"8px", overflow:"hidden", background:"#F6F7FB", position:"relative"}}>
                    <div style={{width:"100%", height:"80px", background:"#FFFFFF", display:"flex", alignItems:"center", justifyContent:"center", overflow:"hidden"}}>
                      {allFolders[folder][0] ? <img src={allFolders[folder][0]} style={{width:"100%", height:"100%", objectFit:"cover"}} /> : <span style={{fontSize:"23px"}}>📁</span>}
                    </div>
                    <button className="boris-btn-hover" onClick={(e:any) => { e.stopPropagation(); renameFolderPrompt(folder); }} title="Переименовать" style={{position:"absolute", top:"4px", right:"26px", background:"rgba(0,0,0,0.7)", color:"#2F6FED", border:"none", borderRadius:"4px", width:"20px", height:"20px", cursor:"pointer", fontSize:"15px"}}>✏️</button>
                    <button className="boris-btn-hover" onClick={(e:any) => { e.stopPropagation(); deleteFolder(folder); }} title="Удалить папку (только пустую)" style={{position:"absolute", top:"4px", right:"4px", background:"rgba(0,0,0,0.7)", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"4px", width:"20px", height:"20px", cursor:"pointer", fontSize:"15px"}}>🗑</button>
                    <div style={{padding:"6px 8px"}}>
                      <div style={{color:"#344054", fontSize:"15px", fontWeight:"bold", whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"}}>{folder.replace("Личное_", "")}</div>
                      <div style={{color:"#8A93A6", fontSize:"15px"}}>{allFolders[folder].length} фото</div>
                      {openedFolder === folder && <div style={{color:"#2F6FED", fontSize:"15px", marginTop:"2px"}}>✓ Открыто ниже ↓</div>}
                    </div>
                  </div>
                ))}
                {Object.keys(allFolders).filter(f => f.startsWith("Личное_")).length === 0 && <div style={{color:"#8A93A6", fontSize:"15px"}}>Пока нет личных папок — создайте первую</div>}
              </div>
            </div>

            <div className="b-panel" style={{display: webdesignFolder === "studio" ? "block" : "none", marginBottom:"24px"}}>
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #7C5CFC, #6344E8)"}}>🎨</span>Студия картинок Бориса</h3>
              <div className="b-meta" style={{marginTop:"4px"}}>Борис сам рисует баннеры и инфографику для объявлений — опишите товар и получите готовую картинку</div>
              <p style={{color:"#667085", fontSize:"15px", marginBottom:"20px"}}>Опиши конкретно: главный объект первым, меньше сущностей, добавь контекст сцены</p>

              <div className="b-card" style={{marginBottom:"20px", padding:"16px"}}>
                <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"12px"}}>
                  <span style={{color:"#7C5CFC", fontWeight:700, fontSize:"16px"}}>📁 Все папки проектов</span>
                  <button className="boris-btn-hover" onClick={loadAllFolders} style={{background:"#FFFFFF", color:"#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"4px 10px", cursor:"pointer", fontSize:"15px"}}>🔄 Обновить</button>
                </div>
                <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(120px, 1fr))", gap:"10px"}}>
                  {Object.keys(allFolders).map(folder => (
                    <div key={folder} onClick={() => openFolder(folder)} style={{cursor:"pointer", border: openedFolder===folder ? "2px solid #7C5CFC" : "2px solid #EEF2FA", borderRadius:"8px", overflow:"hidden", background:"#F6F7FB", position:"relative"}}>
                      <div style={{width:"100%", height:"80px", background:"#FFFFFF", display:"flex", alignItems:"center", justifyContent:"center", overflow:"hidden"}}>
                        {allFolders[folder][0] ? <img src={allFolders[folder][0]} style={{width:"100%", height:"100%", objectFit:"cover"}} /> : <span style={{fontSize:"23px"}}>📁</span>}
                      </div>
                      <button className="boris-btn-hover" onClick={(e:any) => { e.stopPropagation(); renameFolderPrompt(folder); }} title="Переименовать" style={{position:"absolute", top:"4px", right:"26px", background:"rgba(0,0,0,0.7)", color:"#7C5CFC", border:"none", borderRadius:"4px", width:"20px", height:"20px", cursor:"pointer", fontSize:"15px"}}>✏️</button>
                      <button className="boris-btn-hover" onClick={(e:any) => { e.stopPropagation(); deleteFolder(folder); }} title="Удалить папку (только пустую)" style={{position:"absolute", top:"4px", right:"4px", background:"rgba(0,0,0,0.7)", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"4px", width:"20px", height:"20px", cursor:"pointer", fontSize:"15px"}}>🗑</button>
                      <div style={{padding:"6px 8px"}}>
                        <div style={{color:"#344054", fontSize:"15px", fontWeight:"bold", whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"}}>{folder}</div>
                        <div style={{color:"#8A93A6", fontSize:"15px"}}>{allFolders[folder].length} фото</div>
                      </div>
                    </div>
                  ))}
                  {Object.keys(allFolders).length === 0 && <div style={{color:"#8A93A6", fontSize:"15px"}}>Папок пока нет</div>}
                </div>
              </div>

              <div style={{marginBottom:"16px"}}>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Что нарисовать (промпт под конкретную задачу)</label>
                <textarea value={imgPrompt} onChange={e => setImgPrompt(e.target.value)} placeholder="Например: большой пышный букет красных роз в подарочной упаковке, крупным планом, праздничный фон с боке" rows={3} style={{...inputStyle, resize:"vertical", fontFamily:"inherit"}} />
              </div>
              <div style={{display:"grid", gridTemplateColumns:"2fr 1fr 1fr", gap:"16px", marginBottom:"16px"}}>
                <div>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Стиль</label>
                  <input value={imgStyle} onChange={e => setImgStyle(e.target.value)} style={inputStyle} />
                </div>
                <div>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Сколько (1-50)</label>
                  <input type="number" value={imgCount} min={1} max={50} onChange={e => setImgCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))} style={inputStyle} />
                </div>
                <div>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>Папка проекта</label>
                  <input value={imgFolder} onChange={e => setImgFolder(e.target.value)} placeholder="поздравления" style={inputStyle} />
                </div>
              <div style={{background:"#FEF3E2", border:"1px solid #F79009", borderRadius:"8px", padding:"10px 14px", marginBottom:"14px", fontSize:"15px", color:"#F79009"}}>
                ✅ Задача встанет в очередь на сервере — можно закрыть вкладку, генерация продолжится в фоне. Результат появится в галерее, когда будет готов.
              </div>
              </div>
              <button className="boris-btn-hover" onClick={() => setShowImageConfirm(true)} disabled={imgLoading} style={{background: imgLoading ? "#E3E7F0" : "#7C5CFC", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px 24px", fontWeight:"bold", cursor: imgLoading ? "wait" : "pointer", fontSize:"23px"}}>
                {imgLoading ? "🎨 В очереди..." : "🎨 Нарисовать"}
              </button>
              {imgQueueStatus && <div style={{color:"#2F6FED", fontSize:"15px", marginTop:"10px"}}>{imgQueueStatus}</div>}
              {showImageConfirm && (
                <div style={{position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(0,0,0,0.7)", zIndex:1000, display:"flex", alignItems:"center", justifyContent:"center"}} onClick={() => setShowImageConfirm(false)}>
                  <div onClick={(e:any) => e.stopPropagation()} style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"28px", maxWidth:"480px", width:"90%"}}>
                    <h3 style={{margin:"0 0 16px", color:"#1D2939", fontSize:"18px"}}>Проверьте перед генерацией</h3>
                    <div style={{fontSize:"23px", color:"#344054", lineHeight:"2"}}>
                      <div><b style={{color:"#667085"}}>Промпт:</b> {imgPrompt ? imgPrompt.slice(0,80)+(imgPrompt.length>80?"…":"") : "не задан"}</div>
                      <div><b style={{color:"#667085"}}>Стиль:</b> {imgStyle}</div>
                      <div><b style={{color:"#667085"}}>Количество:</b> {imgCount}</div>
                      <div><b style={{color:"#667085"}}>Папка:</b> {imgFolder || "общая"}</div>
                      <div><b style={{color:"#667085"}}>Время:</b> ~{Math.ceil(imgCount/2)*1}-{Math.ceil(imgCount/2)*1.5} мин (пачками по 2)</div>
                    </div>
                    <div style={{background:"#FEF3E2", border:"1px solid #F79009", borderRadius:"8px", padding:"10px", marginTop:"14px", fontSize:"15px", color:"#F79009"}}>
                      ⚠️ Не закрывайте вкладку до конца генерации. Для {imgCount >= 10 ? "такой партии лучше использовать компьютер, не телефон." : "надёжности держите вкладку открытой."}
                    </div>
                    <div style={{display:"flex", gap:"12px", marginTop:"20px"}}>
                      <button className="boris-btn-hover" onClick={() => { setShowImageConfirm(false); generateImage(); }} style={{flex:1, background:"#7C5CFC", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px", fontWeight:"bold", cursor:"pointer"}}>✅ Всё верно, рисуем</button>
                      <button className="boris-btn-hover" onClick={() => setShowImageConfirm(false)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"12px", cursor:"pointer"}}>Отмена</button>
                    </div>
                  </div>
                </div>
              )}

              <div style={{marginTop:"20px", padding:"16px", background:"#E7EFFE", borderRadius:"10px", border:"1px solid #E7EFFE"}}>
                <label style={{color:"#2F6FED", fontSize:"15px", display:"block", marginBottom:"8px", fontWeight:"bold"}}>🖼️ Собрать реальные фото по теме (бесплатные фотостоки)</label>
                <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"10px", lineHeight:"1.5"}}>
                  Просто напиши тему на английском (лучше находит) — Борис сам найдёт и скачает нужное количество реальных бесплатных фото. Например: "бетон товарный и растворы" или "concrete construction".
                </div>
                <div style={{display:"flex", gap:"10px", marginBottom:"10px"}}>
                  <input value={stockQuery} onChange={e => setStockQuery(e.target.value)} placeholder="Например: бетон товарный и растворы" style={{...inputStyle, flex:1}} />
                  <input type="number" min={1} max={80} value={stockCount} onChange={e => setStockCount(Math.max(1, Math.min(80, Number(e.target.value)||1)))} style={{...inputStyle, width:"80px"}} />
                  <button className="boris-btn-hover" onClick={searchStockPhotos} disabled={stockLoading} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px 20px", fontWeight:"bold", cursor:"pointer", whiteSpace:"nowrap"}}>
                    {stockLoading ? "⏳ Собираю..." : "🖼️ Собрать фото"}
                  </button>
                </div>
              </div>

              <div style={{marginTop:"20px", padding:"16px", background:"#F6F7FB", borderRadius:"10px", border:"1px solid #EEF2FA"}}>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"8px"}}>📥 Или скачать по прямой ссылке (JPG/PNG) в папку проекта</label>
                <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"8px", lineHeight:"1.5"}}>
                  💡 Нужна ссылка именно на КАРТИНКУ, не на страницу сайта. Открой картинку → правый клик → "Копировать адрес изображения" (обычно заканчивается на .jpg/.png). Ссылка на страницу поиска/каталога не сработает.<br/>
                  Файл сохранится в текущую папку проекта: <b style={{color:"#7C5CFC"}}>{imgFolder || "общая"}</b> (если папки ещё нет — она создастся автоматически).
                </div>
                <div style={{display:"flex", gap:"12px"}}>
                  <input value={dlUrl} onChange={e => setDlUrl(e.target.value)} placeholder="https://site.com/photo.jpg" style={{...inputStyle, flex:1}} />
                  <button className="boris-btn-hover" onClick={downloadByUrl} disabled={imgLoading} style={{background:"#2F6FED", color:"#1D2939", border:"none", borderRadius:"10px", padding:"12px 20px", fontWeight:"bold", cursor:"pointer", whiteSpace:"nowrap"}}>📥 Скачать</button>
                </div>
              </div>

              {genImages.length > 0 && (
                <div style={{marginTop:"24px"}}>
                  <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"16px"}}>
                    <div style={{color:"#7C5CFC", fontWeight:"bold"}}>Галерея ({genImages.length})</div>
                    <a href={`/api/avito/download_folder_zip?folder=${encodeURIComponent(imgFolder || "общая")}&account_id=${currentAccount}`} style={{background:"#FFFFFF", color:"#2F6FED", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"6px 14px", fontSize:"15px", textDecoration:"none", fontWeight:"bold"}}>📦 Скачать всё папкой (zip)</a>
                  </div>
                  <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(260px, 1fr))", gap:"16px"}}>
                    {genImages.map((img, i) => (
                      <div key={i} style={{background:"#F6F7FB", border:"1px solid #EEF2FA", borderRadius:"10px", padding:"12px", position:"relative"}}>
                        <a href={`/api/avito/download_single?url=${encodeURIComponent(img.url)}`} title="Скачать на компьютер" style={{position:"absolute", top:"18px", right:"52px", background:"rgba(29,78,216,0.9)", color:"#1D2939", borderRadius:"6px", width:"28px", height:"28px", display:"flex", alignItems:"center", justifyContent:"center", textDecoration:"none", fontWeight:"bold"}}>⬇️</a>
                        <button className="boris-btn-hover" onClick={() => deleteImage(img.url)} title="Удалить с сервера" style={{position:"absolute", top:"18px", right:"18px", background:"rgba(220,38,38,0.9)", color:"#1D2939", border:"1.5px solid #F04438", borderRadius:"10px", width:"28px", height:"28px", cursor:"pointer", fontWeight:"bold"}}>🗑</button>
                        <button className="boris-btn-hover" onClick={() => setMovePickerFor(movePickerFor === i ? null : i)} title="Перенести в другую папку" style={{position:"absolute", top:"18px", right:"86px", background:"rgba(192,132,252,0.9)", color:"#F6F7FB", border:"none", borderRadius:"10px", width:"28px", height:"28px", cursor:"pointer", fontWeight:"bold"}}>➡️</button>
                        {movePickerFor === i && (
                          <div style={{position:"absolute", top:"50px", right:"18px", zIndex:10, background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"6px", maxHeight:"160px", overflowY:"auto", minWidth:"140px"}}>
                            {Object.keys(allFolders).map(f => (
                              <div key={f} onClick={() => moveImageToFolder(i, f)} style={{padding:"6px 8px", cursor:"pointer", borderRadius:"6px", fontSize:"15px", color:"#344054"}} onMouseEnter={e => (e.currentTarget.style.background = "#EEF2FA")} onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>📁 {f}</div>
                            ))}
                          </div>
                        )}
                        <img src={img.url} style={{width:"100%", borderRadius:"8px", marginBottom:"8px"}} />
                        <div style={{fontSize:"15px", color:"#8A93A6", marginBottom:"8px"}}>{(img.prompt || "").slice(0,60)}…</div>
                        <input readOnly value={img.url} onClick={(e: any) => { e.target.select(); document.execCommand("copy"); }} style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px", color:"#2F6FED", fontSize:"15px", cursor:"pointer"}} title="Клик — скопировать ссылку" />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div style={{display: webdesignFolder === "buy" ? "block" : "none", background:"#F6F7FB", border:"1px solid #FEF3E2", borderRadius:"16px", padding:"24px", marginBottom:"24px"}}>
              <h3 style={{margin:"0 0 4px", color:"#1D2939", fontSize:"18px", display:"flex", alignItems:"center", gap:"10px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", width:"30px", height:"30px", fontSize:"15px", display:"inline-flex", alignItems:"center", justifyContent:"center", borderRadius:"50%", flexShrink:0}}>✨</span>Создать баннер</h3>
              <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Как составить описание</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Опишите нишу и что нужно на баннере — Борис придумает текст и сгенерирует изображение через AI. Максимум 5 попыток правки на один баннер.</p></details>

              <div style={{marginBottom:"20px"}}>
                <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"10px"}}>
                  <div style={{fontSize:"15px", fontWeight:"bold", color:"#1D2939"}}>🖼 Витрина примеров {selectedExampleUrls.length > 0 ? `(выбрано ${selectedExampleUrls.length}/3)` : ""}</div>
                  <div style={{display:"flex", gap:"8px"}}>
                    <label className="boris-btn-hover" style={{background:"#FFFFFF", color:"#F79009", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 12px", cursor:"pointer", fontSize:"15px", display:"inline-block"}}>
                      {showcaseUploading ? "⏳ Загружаю..." : "📤 Загрузить своё"}
                      <input type="file" accept="image/*" multiple style={{display:"none"}} onChange={(e) => { const fs = e.target.files; if (fs && fs.length > 0) uploadMultipleExamplesToShowcase(fs); e.target.value = ""; }} />
                    </label>
                    <button className="boris-btn-hover" onClick={() => { setShowBrowseAllBanners((v) => !v); if (!showBrowseAllBanners) loadAllBanners(); }} style={{background:"#FFFFFF", color:"#F79009", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", cursor:"pointer", fontSize:"15px"}}>
                      {showBrowseAllBanners ? "Скрыть" : "📂 Смотреть все баннеры"}
                    </button>
                  </div>
                </div>

                {bannerShowcase.length === 0 && !showBrowseAllBanners && (
                  <p style={{color:"#667085", fontSize:"15px"}}>Витрина пока пуста — нажмите «Смотреть все баннеры», чтобы добавить примеры.</p>
                )}
                <details style={{marginBottom:"4px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Показать примеры стилей</summary>
                <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(110px, 1fr))", gap:"10px"}}>
                  {bannerShowcase.map((item: any, i: number) => (
                    <div key={i} onClick={() => toggleExampleSelected(item.url)} style={{cursor:"pointer", position:"relative", borderRadius:"8px", overflow:"hidden", border: selectedExampleUrls.includes(item.url) ? "3px solid #F79009" : "1px solid #E3E7F0"}}>
                      <img src={item.url} style={{width:"100%", height:"90px", objectFit:"cover", display:"block"}} />
                      {selectedExampleUrls.includes(item.url) && (
                        <div style={{position:"absolute", top:"4px", right:"4px", background:"#F79009", color:"#FFFFFF", borderRadius:"50%", width:"20px", height:"20px", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"15px", fontWeight:"bold"}}>✓</div>
                      )}
                      <button onClick={(e) => { e.stopPropagation(); removeBannerFromShowcase(item.url); }} style={{position:"absolute", bottom:"4px", right:"4px", background:"rgba(0,0,0,0.6)", color:"#FFFFFF", border:"none", borderRadius:"4px", padding:"2px 6px", cursor:"pointer", fontSize:"15px"}}>✕</button>
                    </div>
                  ))}
                </div>

                {showBrowseAllBanners && (
                  <div style={{marginTop:"16px", paddingTop:"16px", borderTop:"1px solid #E3E7F0"}}>
                    <div style={{fontSize:"15px", fontWeight:"bold", color:"#1D2939", marginBottom:"10px"}}>Все сгенерированные баннеры — нажмите ⭐, чтобы добавить в витрину</div>
                    {browseAllLoading ? <p style={{color:"#667085", fontSize:"15px"}}>Загружаю...</p> : (
                      <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(110px, 1fr))", gap:"10px", maxHeight:"340px", overflowY:"auto"}}>
                        {allBannersList.map((b: any, i: number) => (
                          <div key={i} style={{position:"relative", borderRadius:"8px", overflow:"hidden", border:"1px solid #E3E7F0"}}>
                            <img src={b.url} style={{width:"100%", height:"90px", objectFit:"cover", display:"block"}} />
                            <button onClick={() => addBannerToShowcase(b.url)} style={{position:"absolute", top:"4px", right:"4px", background:"rgba(255,255,255,0.9)", color:"#F79009", border:"none", borderRadius:"4px", padding:"2px 6px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>⭐ В витрину</button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                </details>
              </div>

              <div style={{fontSize:"13px", color:"#98A2B3", marginBottom:"16px"}}>

              </div>

              <div style={{marginBottom:"20px"}}>
                <label style={{display:"block", color:"#667085", fontSize:"15px", marginBottom:"6px"}}>Формат баннера</label>
                <select className="b-select" value={bannerFormat} onChange={e => setBannerFormat(e.target.value)} style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"10px", color:"#1D2939", fontSize:"15px"}}>
                  <option value="infographic">📋 Инфографика для объявления (1080×1080)</option>
                  <option value="extended_pc">💎 Расширенный тариф — ПК/планшет (1202×436)</option>
                  <option value="extended_mobile">💎 Расширенный тариф — смартфон (1242×936)</option>
                  <option value="max_pc">👑 Максимальный тариф — карусель ПК (1304×480 × 3)</option>
                  <option value="max_mobile">👑 Максимальный тариф — карусель моб. (1420×960 × 3)</option>
                  <option value="custom">✏️ Свой размер (ввести вручную)</option>
                </select>
              </div>

              {bannerFormat === "custom" && (
                <div style={{display:"flex", gap:"10px", marginBottom:"20px"}}>
                  <input type="number" placeholder="Ширина, px" value={customBannerW} onChange={e => setCustomBannerW(e.target.value)} style={{flex:1, boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"10px", color:"#1D2939", fontSize:"15px"}} />
                  <input type="number" placeholder="Высота, px" value={customBannerH} onChange={e => setCustomBannerH(e.target.value)} style={{flex:1, boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"10px", color:"#1D2939", fontSize:"15px"}} />
                </div>
              )}

              <div style={{marginBottom:"20px"}}>
                <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:"6px", flexWrap:"wrap", gap:"8px"}}>
                  <label style={{color:"#667085", fontSize:"15px"}}>Опишите нишу</label>
                  <div style={{display:"flex", gap:"8px", alignItems:"center"}}>
                    {savedPrompts.length > 0 && (
                      <select className="b-select" onChange={e => { const p = savedPrompts.find(x => String(x.id) === e.target.value); if (p) setBannerPrompt(p.text); }} defaultValue="" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"7px 10px", color:"#1D2939", fontSize:"14px", cursor:"pointer", maxWidth:"220px"}}>
                        <option value="">📥 Загрузить промт…</option>
                        {savedPrompts.map(sp => (<option key={sp.id} value={sp.id}>{sp.title}</option>))}
                      </select>
                    )}
                    <button className="boris-btn-hover" onClick={savePromptAsTemplate} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"7px 12px", fontSize:"14px", cursor:"pointer", whiteSpace:"nowrap"}}>💾 Сохранить как шаблон</button>
                  </div>
                </div>
                <textarea value={bannerPrompt} onChange={e => setBannerPrompt(e.target.value)} placeholder="Например: делаем шкафы-купе на заказ в Пензе, работаем 5 лет, цены от 18000 рублей, гарантия 3 года, бесплатный замер. Хочу яркий оранжевый стиль." style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"10px", color:"#1D2939", fontSize:"15px", minHeight:"110px", fontFamily:"inherit", resize:"vertical"}} />
              </div>

              <details style={{marginBottom:"20px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Тонкая настройка (необязательно)</summary>
              <div style={{marginBottom:"20px"}}>
                <label style={{display:"block", color:"#667085", fontSize:"15px", marginBottom:"6px"}}>Свой текст на баннере</label>
                <textarea value={bannerExactText} onChange={e => setBannerExactText(e.target.value)} placeholder="Например: «5 июля — День семьи, любви и верности»" style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"10px", color:"#1D2939", fontSize:"15px", minHeight:"60px", fontFamily:"inherit", resize:"vertical"}} />
              </div>

              <div style={{marginBottom:"20px", background:"#F6F7FB", border: bannerUseOwnPhoto ? "1px solid #2F6FED" : "1px solid #EEF2FA", borderRadius:"8px", padding:"14px"}}>
                <label style={{display:"flex", alignItems:"center", gap:"10px", marginBottom: bannerUseOwnPhoto ? "12px" : "0", cursor:"pointer", color:"#475467", fontSize:"15px"}}>
                  <input type="checkbox" checked={bannerUseOwnPhoto} onChange={e => { setBannerUseOwnPhoto(e.target.checked); if (e.target.checked) { loadAllFolders(); } else { setBannerOwnPhotoUrl(""); } }} style={{width:"18px", height:"18px", cursor:"pointer"}} />
                  Использовать моё фото {(bannerFormat !== "infographic" && bannerFormat !== "custom") && <span style={{color:"#F04438", fontSize:"15px"}}>(пока только для формата «Инфографика»)</span>}
                </label>
                {bannerUseOwnPhoto && (
                  <div style={{display:"flex", flexWrap:"wrap", gap:"8px", maxHeight:"180px", overflowY:"auto", padding:"8px", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px"}}>
                    {Object.entries(allFolders).filter(([folder]) => folder.startsWith("Личное_")).flatMap(([folder, urls]: [string, any]) =>
                      (urls as string[]).map((url: string) => (
                        <div key={url} onClick={() => setBannerOwnPhotoUrl(url)} style={{position:"relative", width:"64px", height:"64px", borderRadius:"6px", overflow:"hidden", cursor:"pointer", border: bannerOwnPhotoUrl === url ? "3px solid #2F6FED" : "1px solid #E3E7F0"}}>
                          <img src={url} style={{width:"100%", height:"100%", objectFit:"cover"}} />
                          {bannerOwnPhotoUrl === url && (
                            <div style={{position:"absolute", top:0, right:0, background:"#2F6FED", color:"#F6F7FB", fontSize:"15px", fontWeight:"bold", padding:"1px 4px", borderRadius:"0 0 0 6px"}}>✓</div>
                          )}
                        </div>
                      ))
                    )}
                    {Object.keys(allFolders).filter(f => f.startsWith("Личное_")).length === 0 && <div style={{color:"#8A93A6", fontSize:"15px"}}>Личных папок нет — загрузите свои фото во вкладке «Выгрузка с сайта».</div>}
                  </div>
                )}
              </div>

              <div style={{marginBottom:"20px"}}>
                <label style={{display:"block", color:"#667085", fontSize:"15px", marginBottom:"6px"}}>Пример дизайна для подражания</label>
                <label style={{display:"inline-block", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px 18px", color:"#2F6FED", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>
                  📁 Выбрать фото
                  <input type="file" accept="image/*" onChange={handleRefImageUpload} style={{display:"none"}} />
                </label>
                {bannerRefImagePreview && (
                  <div style={{marginTop:"10px"}}>
                    <img src={bannerRefImagePreview} style={{maxWidth:"200px", borderRadius:"8px", border:"1px solid #E3E7F0"}} />
                    <button className="boris-btn-hover" onClick={() => { setBannerRefImageBase64(""); setBannerRefImagePreview(""); }} style={{display:"block", marginTop:"6px", background:"none", border:"1.5px solid #F04438", borderRadius:"10px", padding:"4px 10px", color:"#F04438", cursor:"pointer", fontSize:"15px"}}>Убрать фото</button>
                  </div>
                )}
              </div>

              <div style={{display:"flex", gap:"10px", marginBottom:"20px"}}>
                <div style={{flex:1}}>
                  <label style={{display:"block", color:"#667085", fontSize:"15px", marginBottom:"6px"}}>Акцентный цвет</label>
                  <input type="color" value={bannerAccentColor} onChange={e => setBannerAccentColor(e.target.value)} style={{width:"100%", height:"42px", borderRadius:"8px", border:"1px solid #E3E7F0", background:"#FFFFFF", cursor:"pointer"}} />
                </div>
                <div style={{flex:1}}>
                  <label style={{display:"block", color:"#667085", fontSize:"15px", marginBottom:"6px"}}>Качество генерации</label>
                  <select className="b-select" value={bannerQuality} onChange={e => setBannerQuality(e.target.value)} style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"10px", color:"#1D2939", fontSize:"15px"}}>
                    <option value="low">Дёшево и быстро (черновик)</option>
                    <option value="medium">Стандарт (для клиента)</option>
                    <option value="high">Премиум (дороже)</option>
                  </select>
                </div>
              </div>

              <div style={{marginBottom:"20px", background:"#F5FAFF", border:"1px solid #EAF4FF", borderRadius:"10px", padding:"14px"}}>
                <label style={{display:"block", color:"#667085", fontSize:"15px", marginBottom:"8px", fontWeight:600}}>Вариантов: {bannerVaryCount}</label>
                <input type="range" min={1} max={5} value={bannerVaryCount} onChange={e => setBannerVaryCount(Number(e.target.value))} style={{width:"100%", marginBottom:"12px"}} />
                {bannerVaryCount > 1 && (
                  <>
                    <div style={{fontSize:"13px", color:"#8A93A6", marginBottom:"8px"}}>Отличие вариантов по:</div>
                    <div style={{display:"flex", gap:"14px", flexWrap:"wrap"}}>
                      <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#1D2939", cursor:"pointer"}}>
                        <input type="checkbox" checked={bannerVaryAll ? true : bannerVaryColors} disabled={bannerVaryAll} onChange={e => setBannerVaryColors(e.target.checked)} /> Цветам
                      </label>
                      <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#1D2939", cursor:"pointer"}}>
                        <input type="checkbox" checked={bannerVaryAll ? true : bannerVaryImage} disabled={bannerVaryAll} onChange={e => setBannerVaryImage(e.target.checked)} /> Картинке
                      </label>
                      <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#1D2939", cursor:"pointer"}}>
                        <input type="checkbox" checked={bannerVaryAll ? true : bannerVaryIcons} disabled={bannerVaryAll} onChange={e => setBannerVaryIcons(e.target.checked)} /> Иконкам
                      </label>
                      <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#7C5CFC", cursor:"pointer", fontWeight:600}}>
                        <input type="checkbox" checked={bannerVaryAll} onChange={e => {
                          setBannerVaryAll(e.target.checked);
                          if (e.target.checked) { setBannerVaryColors(false); setBannerVaryImage(false); setBannerVaryIcons(false); }
                        }} /> Полностью всему
                      </label>
                    </div>
                  </>
                )}
              </div>
              </details>

              <button className="boris-btn-hover" onClick={generateBanner} disabled={bannerLoading} style={{background: bannerLoading ? "#E3E7F0" : "#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 20px", fontWeight:"bold", cursor: bannerLoading ? "default" : "pointer", width:"auto", fontSize:"15px"}}>
                {bannerLoading ? `⏳ Борис рисует ${bannerVaryCount > 1 ? bannerVaryCount + " баннера" : "баннер"}...` : `✨ Сгенерировать ${bannerVaryCount > 1 ? bannerVaryCount + " варианта" : "баннер"}`}
              </button>

              {bannerResultUrls.length > 0 && (
                <div style={{marginTop:"28px"}}>
                  <h4 style={{margin:"0 0 12px", color:"#1D2939", fontSize:"18px"}}>Результат:</h4>
                  <div style={{display:"flex", flexWrap:"wrap", gap:"14px"}}>
                    {bannerResultUrls.map((url, i) => (
                      <div key={i} style={{border:"1px solid #E3E7F0", borderRadius:"10px", overflow:"hidden", background:"#FFFFFF"}}>
                        <img src={url} alt={`banner-${i}`} style={{display:"block", maxWidth:"340px", width:"100%"}} />
                        <div style={{padding:"10px", display:"flex", gap:"8px"}}>
                          <a href={url} target="_blank" rel="noreferrer" style={{flex:1, textAlign:"center", background:"#EEF2FA", color:"#F79009", borderRadius:"6px", padding:"8px", fontSize:"15px", textDecoration:"none"}}>Открыть</a>
                          <a href={url} download style={{flex:1, textAlign:"center", background:"#EEF2FA", color:"#F79009", borderRadius:"6px", padding:"8px", fontSize:"15px", textDecoration:"none"}}>Скачать</a>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(bannerGallery.infographic.length > 0 || bannerGallery.extended.length > 0 || bannerGallery.max_carousel.length > 0) && (
                <div style={{marginTop:"32px", borderTop:"1px solid #E3E7F0", paddingTop:"24px"}}>
                  <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:"16px", flexWrap:"wrap", gap:"10px"}}>
                    <h4 style={{margin:0, color:"#1D2939", fontSize:"18px", display:"flex", alignItems:"center", gap:"10px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#98A2B3,#667085)", width:"30px", height:"30px", fontSize:"15px", display:"inline-flex", alignItems:"center", justifyContent:"center", borderRadius:"50%", flexShrink:0}}>📁</span>Все ваши баннеры</h4>
                    <div style={{display:"flex", gap:"8px", alignItems:"center"}}>
                      <button className="boris-btn-hover" onClick={selectAllBanners} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 14px", fontSize:"14px", cursor:"pointer"}}>Выбрать все</button>
                      <button className="boris-btn-hover" onClick={downloadSelectedBanners} disabled={selectedBanners.length === 0} style={{background: selectedBanners.length === 0 ? "#E3E7F0" : "#2F6FED", color: selectedBanners.length === 0 ? "#98A2B3" : "#FFFFFF", border:"none", borderRadius:"10px", padding:"8px 14px", fontSize:"14px", fontWeight:"bold", cursor: selectedBanners.length === 0 ? "default" : "pointer"}}>⬇ Скачать выбранные{selectedBanners.length > 0 ? ` (${selectedBanners.length})` : ""}</button>
                      <button className="boris-btn-hover" onClick={deleteSelectedBanners} disabled={selectedBanners.length === 0} style={{background: selectedBanners.length === 0 ? "#E3E7F0" : "#E24B4A", color: selectedBanners.length === 0 ? "#98A2B3" : "#FFFFFF", border:"1.5px solid #F04438", borderRadius:"10px", padding:"8px 14px", fontSize:"14px", fontWeight:"bold", cursor: selectedBanners.length === 0 ? "default" : "pointer"}}>🗑 Удалить выбранные{selectedBanners.length > 0 ? ` (${selectedBanners.length})` : ""}</button>
                    </div>
                  </div>
                  {Object.entries(bannerGallery).map(([subfolder, urls]) => (
                    urls.length > 0 && (
                      <div key={subfolder} style={{marginBottom:"20px"}}>
                        <div style={{color:"#1D2939", fontSize:"16px", fontWeight:600, marginBottom:"12px"}}>{subfolder === "infographic" ? "Инфографика" : subfolder === "extended" ? "Расширенный тариф" : "Максимальный тариф"} ({urls.length})</div>
                        <div style={{display:"flex", flexWrap:"wrap", gap:"12px"}}>
                          {urls.map((url, i) => (
                            <div key={i} className="boris-card-hover" style={{border: selectedBanners.includes(url) ? "2px solid #2F6FED" : "1px solid #E3E7F0", borderRadius:"16px", overflow:"hidden", background:"#FFFFFF", position:"relative", width:"200px"}}>
                              <img src={url} onClick={() => setLightboxUrl(url)} alt={`gallery-${i}`} style={{display:"block", width:"100%", height:"130px", objectFit:"contain", background:"#F6F7FB", cursor:"zoom-in", opacity: selectedBanners.includes(url) ? 0.75 : 1}} />
                              <div style={{padding:"10px 12px"}}><div style={{fontSize:"14px", fontWeight:600, color:"#1D2939"}}>{String(subfolder).includes("infographic") ? "Инфографика" : String(subfolder).includes("extended") ? "Расширенный тариф" : "Максимальный тариф"}</div></div>
                              <input type="checkbox" checked={selectedBanners.includes(url)} onChange={() => toggleBannerSelect(url)} style={{position:"absolute", top:"8px", left:"8px", width:"20px", height:"20px", cursor:"pointer", accentColor:"#2F6FED"}} />
                              <button className="boris-btn-hover" onClick={() => deleteBannerFile(subfolder, url)} title="Удалить" style={{position:"absolute", top:"6px", right:"6px", background:"rgba(220,38,38,0.9)", color:"#1D2939", border:"1.5px solid #F04438", borderRadius:"10px", width:"24px", height:"24px", cursor:"pointer", fontSize:"15px"}}>🗑</button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "marketing" && (
          <div style={{display:"flex", flexDirection:"column", gap:"20px"}}>
            <div className="b-grid-2">
              <div className="b-panel b-card-eq">
                <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #F79009, #E8890B)"}}>🎯</span>Цель по лидам (KPI)</h3>
                <p style={{color:"#667085", fontSize:"15px", marginBottom:"16px"}}>Задайте цель — Борис будет ориентироваться на неё при принятии решений об изменениях в объявлениях.</p>
                <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(140px, 1fr))", gap:"14px", marginBottom:"16px"}}>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"5px"}}>Лидов в день</label>
                    <input type="number" value={kpiTargetLeads} onChange={e => setKpiTargetLeads(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"5px"}}>Макс. ₽ за лид</label>
                    <input type="number" value={kpiMaxCpl} onChange={e => setKpiMaxCpl(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"5px"}}>Температура лидов</label>
                    <div style={{color:"#98A2B3", fontSize:"13px", marginBottom:"6px", lineHeight:1.4}}>Горячий — спрашивает цену, тёплый — интересуется, холодный — просто смотрит</div>
                    <select className="b-select" value={kpiLeadTemp} onChange={e => setKpiLeadTemp(e.target.value)} style={{...inputStyle, width:"100%", boxSizing:"border-box"}}>
                      <option value="любые">Любые</option>
                      <option value="горячие">🔥 Горячие</option>
                      <option value="тёплые">🌤 Тёплые</option>
                      <option value="холодные">❄️ Холодные</option>
                    </select>
                  </div>
                </div>
                <div style={{display:"flex", gap:"10px", marginBottom:"16px"}}>
                  <button className="boris-btn-hover" onClick={saveKpiSettings} style={{background: kpiSaved ? "#E7EFFE" : "#2F6FED", color: kpiSaved ? "#2F6FED" : "#F6F7FB", border: kpiSaved ? "1px solid #2F6FED" : "none", borderRadius:"10px", padding:"9px 14px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>
                    {kpiSaved ? "✓ Сохранено" : "Сохранить цель по лидам"}
                  </button>
                  <button className="boris-btn-hover" onClick={deleteKpiSettings} style={{marginLeft:"auto", background:"#FFFFFF", color:"#98A2B3", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"9px 14px", fontSize:"15px", cursor:"pointer"}}>
                    🗑 Удалить цель
                  </button>
                  <button className="boris-btn-hover" onClick={checkKpi} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"9px 14px", fontSize:"15px", cursor:"pointer"}}>
                    📊 Как идут дела по цели
                  </button>
                </div>

                {kpiCheckResult && kpiCheckResult.status === "ok" && (
                  <div style={{background:"#F6F7FB", border:"1px solid #EEF2FA", borderRadius:"8px", padding:"14px", marginBottom:"16px", fontSize:"15px"}}>
                    <div style={{color:"#667085", marginBottom:"6px"}}>
                      Лидов сегодня: <b style={{color:"#2F6FED"}}>{kpiCheckResult.contacts_today}</b> из цели <b>{kpiCheckResult.target_leads_per_day}</b>
                      {kpiCheckResult.cost_per_lead_today != null && <> · Цена лида: <b style={{color:"#F79009"}}>{kpiCheckResult.cost_per_lead_today}₽</b></>}
                    </div>
                    <div style={{color:"#344054"}}>{kpiCheckResult.recommendation}</div>
                  </div>
                )}
                {kpiCheckResult && kpiCheckResult.status === "no_goal" && (
                  <div style={{color:"#667085", fontSize:"15px", marginBottom:"16px"}}>{kpiCheckResult.message}</div>
                )}

                <div style={{borderTop:"1px solid #EEF2FA", paddingTop:"14px"}}>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>План по направлению (необязательно)</label>
                  <div style={{color:"#98A2B3", fontSize:"13px", marginBottom:"8px", lineHeight:1.4}}>Введите начало id ваших объявлений — например boris-shkaf. Борис покажет, что улучшить именно в этом направлении. Оставьте пустым, чтобы посмотреть по всем.</div>
                  <div style={{display:"flex", gap:"10px", marginBottom:"10px"}}>
                    <input value={kpiPlanIdPrefix} onChange={e => setKpiPlanIdPrefix(e.target.value)} style={{...inputStyle, flex:1}} placeholder="boris-shkaf-..." />
                    <button className="boris-btn-hover" onClick={getKpiPlan} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 18px", fontSize:"15px", fontWeight:"bold", cursor:"pointer", whiteSpace:"nowrap"}}>
                      Показать план по лидам
                    </button>
                  </div>
                  {kpiPlan && (
                    <div style={{background:"#F6F7FB", border:"1px solid #EEF2FA", borderRadius:"8px", padding:"14px"}}>
                      <div style={{color:"#F79009", fontWeight:"bold", marginBottom:"8px"}}>План для «{kpiPlan.direction}» ({kpiPlan.affected_items} объявлений)</div>
                      <ol style={{color:"#344054", fontSize:"15px", paddingLeft:"18px", marginBottom:"12px"}}>
                        {kpiPlan.steps.map((s: string, i: number) => <li key={i} style={{marginBottom:"4px"}}>{s}</li>)}
                      </ol>
                      <button className="boris-btn-hover" onClick={executeKpiPlan} disabled={kpiPlanExecuting} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 16px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>
                        {kpiPlanExecuting ? "⏳ Выполняю..." : "✅ Одобрить и внедрить"}
                      </button>
                    </div>
                  )}
                </div>
              </div>

              <div className="b-panel b-card-eq">
                <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"10px"}}>
                  <h3 className="b-title" style={{margin:0, display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #12B76A, #039855)"}}>🔄</span>Перепубликация</h3>
                  <button className="boris-btn-hover" onClick={loadRepublishCandidates} disabled={republishLoading} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"6px 12px", fontSize:"15px", cursor:"pointer"}}>
                    {republishLoading ? "⏳..." : "🔄 Обновить"}
                  </button>
                </div>
                <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(140px, 1fr))", gap:"14px", marginBottom:"14px", alignItems:"end"}}>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"5px"}}>Мин. просмотров без контакта</label>
                    <input type="number" value={republishMinViews} onChange={e => setRepublishMinViews(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <div>
                    <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"5px"}}>Дней без просмотров</label>
                    <input type="number" value={republishZeroDays} onChange={e => setRepublishZeroDays(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <label style={{display:"flex", alignItems:"center", gap:"6px", color:"#667085", fontSize:"15px", cursor:"pointer", paddingBottom:"10px"}}>
                    <input type="checkbox" checked={republishEnabled} onChange={e => setRepublishEnabled(e.target.checked)} />
                    Включено
                  </label>
                </div>
                <div style={{display:"flex", gap:"10px", marginBottom:"14px"}}>
                  <button className="boris-btn-hover" onClick={saveRepublishSettings} style={{background: republishSettingsSaved ? "#E7EFFE" : "#2F6FED", color: republishSettingsSaved ? "#2F6FED" : "#F6F7FB", border: republishSettingsSaved ? "1px solid #2F6FED" : "none", borderRadius:"10px", padding:"9px 14px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>
                    {republishSettingsSaved ? "✓ Сохранено" : "Сохранить правила перепубликации"}
                  </button>
                  <button className="boris-btn-hover" onClick={deleteRepublishSettings} style={{background:"#FFFFFF", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"9px 14px", fontSize:"15px", cursor:"pointer"}}>
                    🗑 Сбросить
                  </button>
                </div>
                <p style={{color:"#667085", fontSize:"15px", marginBottom:"12px"}}>
                  {republishTotal !== null && `Проверено объявлений в фиде: ${republishTotal}.`}
                </p>
                {republishCandidates.length === 0 ? (
                  <p style={{color:"#667085", fontSize:"15px"}}>Кандидатов на снятие нет — все объявления эффективны или ещё слишком новые для оценки.</p>
                ) : (
                  <>
                    <div style={{display:"flex", flexDirection:"column", gap:"6px", maxHeight:"240px", overflowY:"auto", marginBottom:"12px"}}>
                      {republishCandidates.map((c: any) => (
                        <div key={c.id} style={{background:"#FFFFFF", borderRadius:"6px", padding:"8px 12px", fontSize:"15px"}}>
                          <div style={{color:"#F79009"}}>{c.title}</div>
                          <div style={{color:"#F04438"}}>{c.reason}</div>
                        </div>
                      ))}
                    </div>
                    <button className="boris-btn-hover" onClick={applyRepublish} style={{background:"#FDEDEC", color:"#F04438", border:"1.5px solid #F04438", borderRadius:"10px", padding:"10px 16px", fontSize:"15px", cursor:"pointer", fontWeight:"bold"}}>
                      🗑 Снять {republishCandidates.length} и создать замену
                    </button>
                  </>
                )}
              </div>

              <div className="b-cell">
            <div className="b-panel b-card-eq" style={{marginBottom:"24px"}}>
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #9B87F5, #7C5CFC)"}}>📈</span>Аналитика выдачи по городам</h3>
              <p className="b-sub">Узнай сколько объявлений, какие цены и названия у конкурентов</p>
              <div style={{marginBottom:"16px"}}>
                <label className="b-label">Запрос</label>
                <input value={analysisQuery} onChange={e => setAnalysisQuery(e.target.value)} placeholder="продажа тротуарной плитки" className="b-input" />
              </div>
              <div style={{marginBottom:"20px"}}>
                <label className="b-label">Города (через запятую)</label>
                <input value={analysisCities} onChange={e => setAnalysisCities(e.target.value)} placeholder="Москва, Санкт-Петербург" className="b-input" />
              </div>
              <button className="b-btn b-btn-primary" onClick={runAnalysis}>
                📊 Запустить анализ
              </button>
            </div>
            {analysisLoading && <p style={{color:"#667085"}}>БОРИС анализирует выдачу...{analysisProgress ? ` (город ${analysisProgress.done} из ${analysisProgress.total})` : ""}</p>}
            {analysisResults.length > 0 && (
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(280px, 1fr))", gap:"16px"}}>
                {analysisResults.map((r: any, i: number) => (
                  <div key={i} style={{background:"#F6F7FB", borderRadius:"12px", padding:"20px", border:"1px solid #EEF2FA"}}>
                    <div style={{fontSize:"23px", fontWeight:"bold", marginBottom:"16px"}}>📍 {r.city}</div>
                    {r.error ? <div style={{color:"#F04438", fontSize:"15px"}}>{r.error}</div> : (
                      <>
                        <div style={{display:"grid", gridTemplateColumns: r.total_count ? "1fr 1fr" : "1fr 1fr", gap:"8px", marginBottom:"8px"}}>
                          <div style={{background:"#FFFFFF", borderRadius:"8px", padding:"8px", textAlign:"center"}}>
                            <div style={{color:"#667085", fontSize:"15px"}}>В выборке</div>
                            <div style={{fontSize:"23px", fontWeight:"bold", color:"#2F6FED"}}>{r.count_found}</div>
                          </div>
                          <div style={{background:"#FFFFFF", borderRadius:"8px", padding:"8px", textAlign:"center"}}>
                            <div style={{color:"#667085", fontSize:"15px"}}>Ср. цена</div>
                            <div style={{fontSize:"23px", fontWeight:"bold", color:"#2F6FED"}}>{r.avg_price || "—"}</div>
                          </div>
                        </div>
                        {r.total_count && (
                          <div style={{background:"#F0F5FF", borderRadius:"8px", padding:"10px", marginBottom:"12px", textAlign:"center"}}>
                            <div style={{color:"#667085", fontSize:"13px"}}>Всего объявлений в базе Avito</div>
                            <div style={{fontSize:"18px", fontWeight:"bold", color:"#1D2939"}}>{r.total_count.toLocaleString("ru-RU")}</div>
                          </div>
                        )}
                        <div style={{color:"#98A2B3", fontSize:"13px", marginBottom:"12px", textAlign:"center", fontStyle:"italic"}}>
                          Показан срез первой страницы выдачи (топ-конкуренты по цене), не полный охват рынка
                        </div>
                        {(r.min_price || r.max_price) && (
                          <div style={{color:"#667085", fontSize:"15px", marginBottom:"12px", textAlign:"center"}}>
                            Цены: {r.min_price || "?"}–{r.max_price || "?"} ₽
                            {r.from_cache && <span style={{color:"#98A2B3", marginLeft:"8px"}}>(из кэша)</span>}
                          </div>
                        )}
                        {r.top5?.length > 0 && (
                          <div style={{borderTop:"1px solid #EEF2FA", paddingTop:"12px"}}>
                            <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"10px"}}>Топ-5 конкурентов (по цене):</div>
                            {r.top5.map((item: any, idx: number) => (
                              <div key={idx} style={{background:"#FFFFFF", borderRadius:"8px", padding:"12px", marginBottom:"8px"}}>
                                <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:"8px", marginBottom:"6px"}}>
                                  <a href={item.url} target="_blank" rel="noreferrer" style={{color:"#1D2939", fontSize:"15px", fontWeight:"bold", textDecoration:"none", flex:1}}>{item.title}</a>
                                  <span style={{color:"#2F6FED", fontSize:"15px", fontWeight:"bold", whiteSpace:"nowrap"}}>{item.price ? item.price + " ₽" : "—"}</span>
                                </div>
                                <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"6px"}}>📷 {item.photos_count ?? "?"} фото</div>
                                {item.advantages && <div style={{color:"#999", fontSize:"15px", lineHeight:"1.4"}}>💡 {item.advantages}</div>}
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
            <div className="b-panel b-card-eq">
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #4C8DFF, #2F6FED)"}}>📊</span>Анализ спроса (Яндекс.Вордстат)</h3>
              <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Зачем это нужно</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Узнайте реальный объём поисковых запросов по вашей нише, похожие запросы и долю коммерческих (с намерением купить) среди них — это помогает понять, стоит ли расширять направление.</p></details>
              <div style={{display:"flex", gap:"10px", marginBottom:"16px"}}>
                <input value={wordstatQuery} onChange={e => setWordstatQuery(e.target.value)} placeholder="например: шкаф купе на заказ"
                  className="b-input" style={{flex:1}} onKeyDown={e => { if (e.key === "Enter") analyzeWordstat(); }} />
                <button className="b-btn b-btn-primary" onClick={analyzeWordstat} disabled={wordstatLoading} style={{whiteSpace:"nowrap"}}>
                  {wordstatLoading ? "⏳ Анализирую..." : "🔍 Проверить спрос"}
                </button>
              </div>

              {wordstatResult && wordstatResult.status === "error" && (
                <div style={{background:"#FDEDEC", border:"1px solid #F04438", borderRadius:"8px", padding:"14px", color:"#F04438", fontSize:"15px"}}>
                  {wordstatResult.message}
                </div>
              )}

              {wordstatResult && wordstatResult.status === "ok" && (
                <div style={{background:"#F6F7FB", border:"1px solid #EEF2FA", borderRadius:"8px", padding:"18px"}}>
                  <div style={{display:"flex", gap:"24px", marginBottom:"16px"}}>
                    <div>
                      <div style={{color:"#667085", fontSize:"15px"}}>Всего запросов в месяц</div>
                      <div style={{color:"#2F6FED", fontSize:"23px", fontWeight:"bold"}}>{wordstatResult.total_count?.toLocaleString("ru-RU")}</div>
                    </div>
                    <div>
                      <div style={{color:"#667085", fontSize:"15px"}}>Доля коммерческих запросов</div>
                      <div style={{color:"#F79009", fontSize:"23px", fontWeight:"bold"}}>{wordstatResult.commercial_share_percent}%</div>
                    </div>
                  </div>

                  <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"20px"}}>
                    <div>
                      <div className="b-label">Топ запросов</div>
                      <div style={{display:"flex", flexDirection:"column", gap:"4px", maxHeight:"280px", overflowY:"auto"}}>
                        {wordstatResult.top_requests?.map((r: any, i: number) => (
                          <div key={i} style={{display:"flex", justifyContent:"space-between", fontSize:"15px", color:"#667085", borderBottom:"1px solid #FFFFFF", paddingBottom:"4px"}}>
                            <span>{r.phrase}</span><span style={{color:"#8A93A6"}}>{r.count?.toLocaleString("ru-RU")}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="b-label">Похожие запросы</div>
                      <div style={{display:"flex", flexDirection:"column", gap:"4px", maxHeight:"280px", overflowY:"auto"}}>
                        {wordstatResult.associations?.map((r: any, i: number) => (
                          <div key={i} style={{display:"flex", justifyContent:"space-between", fontSize:"15px", color:"#667085", borderBottom:"1px solid #FFFFFF", paddingBottom:"4px"}}>
                            <span>{r.phrase}</span><span style={{color:"#8A93A6"}}>{r.count?.toLocaleString("ru-RU")}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
            </div>

            {wordstatHistory.length > 0 && (
              <div className="b-panel">
                <h4 className="b-title">📚 История проверок</h4>
                <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                  {wordstatHistory.map((h: any, i: number) => (
                    <div key={i} style={{display:"flex", justifyContent:"space-between", background:"#FFFFFF", borderRadius:"6px", padding:"10px 14px", fontSize:"15px", cursor:"pointer"}}
                      onClick={() => { setWordstatQuery(h.query); setWordstatResult(h); }}>
                      <span style={{color:"#344054"}}>{h.query}</span>
                      <span style={{color:"#8A93A6"}}>{h.total_count?.toLocaleString("ru-RU")} запросов/мес · {h.commercial_share_percent}% коммерческих</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === "sales" && (
          <div style={{display:"flex", flexDirection:"column", gap:"20px"}}>
            {salesView === "home" && (
              <>
              <div style={{fontSize:"14px", color:"#475467", marginBottom:"4px", lineHeight:1.6, background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"14px 18px"}}>
                Два разных помощника для продаж. <b>Менеджер</b> общается с вашими покупателями в чатах Avito вместо вас. <b>Руководитель отдела продаж</b> контролирует работу ваших менеджеров: разбирает звонки и переписки, находит ошибки, даёт отчёты. Выберите, что открыть:
              </div>
              <div data-block="sales-home-cards" style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(300px, 1fr))", gap:"20px"}}>
                <div onClick={()=>{ setSalesView("mop"); loadAllLimits(); }} className="boris-btn-hover" style={{cursor:"pointer", borderRadius:"18px", padding:"28px", background:"linear-gradient(135deg,#ECFDF3,#F6FEF9)", border:"1px solid #A6F4C5"}}>
                  <div style={{fontSize:"38px", marginBottom:"10px"}}>🤝</div>
                  <div style={{fontSize:"20px", fontWeight:800, color:"#054F31"}}>ИИ Менеджер по продажам</div>
                  <div style={{fontSize:"15px", color:"#475467", marginTop:"8px", lineHeight:1.5}}>Отвечает покупателям в чатах Avito за секунды — 24/7, ночью и в выходные, не упуская горячего клиента.</div>
                  <div style={{marginTop:"14px", display:"flex", flexDirection:"column", gap:"8px"}}>
                    <div style={{fontSize:"15px", color:"#054F31", display:"flex", gap:"8px"}}><span>⚡</span><span>Отвечает за секунды, 24/7 — ночью и в выходные</span></div>
                    <div style={{fontSize:"15px", color:"#054F31", display:"flex", gap:"8px"}}><span>🎯</span><span>Ведёт диалог к цели: берёт телефон, договаривается о шаге</span></div>
                    <div style={{fontSize:"15px", color:"#054F31", display:"flex", gap:"8px"}}><span>💰</span><span>Горячие лиды сразу вам в Telegram</span></div>
                    <div style={{fontSize:"15px", color:"#054F31", display:"flex", gap:"8px"}}><span>📊</span><span>Воронка: кто в процессе, думает, оставил телефон, отвалился</span></div>
                  </div>
                  <div style={{marginTop:"16px", display:"inline-block", fontSize:"14px", fontWeight:700, color:"#039855"}}>Открыть менеджера →</div>
                </div>
                <div onClick={()=>{ setSalesView("rop"); loadRopCalls(); loadSavedReports(); loadRopLimits(); loadRopSetup(); loadAllLimits(); }} className="boris-btn-hover" style={{cursor:"pointer", borderRadius:"18px", padding:"28px", background:"linear-gradient(135deg,#F4F3FF,#FCFAFF)", border:"1px solid #D9D6FE"}}>
                  <div style={{fontSize:"38px", marginBottom:"10px"}}>🧠</div>
                  <div style={{fontSize:"20px", fontWeight:800, color:"#3E1C96"}}>ИИ Руководитель отдела продаж</div>
                  <div style={{fontSize:"15px", color:"#475467", marginTop:"8px", lineHeight:1.5}}>Разбирает переписки менеджеров: находит где сливаются лиды, какие возражения не отрабатываются, и что улучшить в скрипте. Готовит рекомендации и может сам переписать скрипт.</div>
                  <div style={{marginTop:"14px", display:"flex", flexDirection:"column", gap:"8px"}}>
                    <div style={{fontSize:"15px", color:"#3E1C96", display:"flex", gap:"8px"}}><span>📞</span><span>Слушает и разбирает каждый звонок по чек-листу</span></div>
                    <div style={{fontSize:"15px", color:"#3E1C96", display:"flex", gap:"8px"}}><span>🎯</span><span>Находит, где менеджер теряет клиента</span></div>
                    <div style={{fontSize:"15px", color:"#3E1C96", display:"flex", gap:"8px"}}><span>⭐</span><span>Показывает сильные моменты, паразитов и точки роста</span></div>
                    <div style={{fontSize:"15px", color:"#3E1C96", display:"flex", gap:"8px"}}><span>📄</span><span>Отчёты по звонкам и перепискам с рекомендациями</span></div>
                  </div>
                  <div style={{marginTop:"16px", display:"inline-block", fontSize:"14px", fontWeight:700, color:"#6941C6"}}>Открыть РОПа →</div>
                </div>
              </div>
              </>
            )}
            {salesView !== "home" && (
              <button onClick={()=>setSalesView("home")} style={{alignSelf:"flex-start", fontSize:"14px", fontWeight:600, color:"#475467", background:"#F2F4F7", border:"none", borderRadius:"8px", padding:"8px 16px", cursor:"pointer"}}>← Назад к выбору</button>
            )}
{salesView === "rop" && ropLimits && !ropLimits.minutes?.unlimited && (ropLimits.period?.active !== true || ropShowPacks) && (
  <div data-block="rop-packages" style={{margin:"0 0 16px"}}>
    <div style={{fontSize:"18px", fontWeight:800, color:"#1D2939", marginBottom:"6px"}}>ИИ Руководитель отдела продаж</div>
    <div style={{fontSize:"14px", color:"#667085", marginBottom:"18px"}}>{ropShowPacks ? "Докупка объёма до конца текущего периода" : "Чтобы начать, подключите базовый пакет на 30 дней. Дополнительные пакеты можно докупить после."}</div>
    {ropShowPacks && (
      <button onClick={()=>setRopShowPacks(false)} style={{marginBottom:"14px", fontSize:"14px", fontWeight:600, color:"#475467", background:"#F2F4F7", border:"none", borderRadius:"8px", padding:"8px 16px", cursor:"pointer"}}>← Назад</button>
    )}
    {!ropShowPacks && (
      <div className="boris-card-hover" style={{background:"linear-gradient(135deg,#F4F3FF,#FFFFFF)", border:"2px solid #7C5CFC", borderRadius:"18px", padding:"26px", position:"relative", overflow:"hidden", marginBottom:"20px", maxWidth:"640px"}}>
        <div style={{position:"absolute", top:"-40px", right:"-40px", width:"120px", height:"120px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", opacity:0.1}} />
        <div style={{display:"flex", alignItems:"center", gap:"12px", marginBottom:"12px"}}>
          <div style={{width:"56px", height:"56px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"26px"}}>🧠</div>
          <div>
            <div style={{fontSize:"17px", fontWeight:800, color:"#1D2939"}}>Базовый пакет</div>
            <div style={{fontSize:"13px", color:"#7C5CFC", fontWeight:700}}>на 30 дней</div>
          </div>
        </div>
        <div style={{fontSize:"30px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"14px"}}>20 000 ₽</div>
        <div style={{fontSize:"14px", color:"#475467", lineHeight:1.7, marginBottom:"20px"}}>
          <div>🎧 1 500 минут разбора звонков</div>
          <div>💬 450 разборов переписок</div>
          <div>📄 30 отчётов по звонкам + 30 по перепискам</div>
        </div>
        <TermsGate product="rop" pack="rop1500" accountId={currentAccount}><RobokassaButton invoiceId="" pack="rop1500" accountId={currentAccount} /></TermsGate>
        <button onClick={()=>openInvoice("rop1500")} style={{marginTop:"10px", width:"100%", background:"#fff", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"11px", fontSize:"14px", fontWeight:700, cursor:"pointer"}}>🧾 Оплатить по счёту (для юрлиц)</button>
      </div>
    )}
    {ropShowPacks && (
      <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(240px, 1fr))", gap:"14px", alignItems:"stretch"}}>
        {[
          {pack:"rop_a300",  t:"+300 минут",   p:"5 000 ₽",  d:"300 мин · 90 переписок · 5 + 5 отчётов"},
          {pack:"rop_a500",  t:"+500 минут",   p:"7 500 ₽",  d:"500 мин · 150 переписок · 10 + 10 отчётов"},
          {pack:"rop_a700",  t:"+700 минут",   p:"10 500 ₽", d:"700 мин · 200 переписок · 15 + 15 отчётов"},
          {pack:"rop_a1000", t:"+1 000 минут", p:"14 000 ₽", d:"1000 мин · 300 переписок · 20 + 20 отчётов"},
          {pack:"rop_a1500", t:"+1 500 минут", p:"21 000 ₽", d:"1500 мин · 400 переписок · 30 + 30 отчётов"},
          {pack:"rop_a2000", t:"+2 000 минут", p:"26 000 ₽", d:"2000 мин · 550 переписок · 40 + 40 отчётов"},
        ].map((it:any)=>(
          <div key={it.pack} className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}>
            <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", opacity:0.08}} />
            <div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#9B87F5,#7C5CFC)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>🧠</div>
            <div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>{it.t}</div>
            <div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"4px"}}>{it.p}</div>
            <div style={{fontSize:"12px", color:"#98A2B3", marginBottom:"14px", minHeight:"48px"}}>{it.d}</div>
            <div style={{marginTop:"auto", width:"100%"}}><TermsGate product="rop" pack={it.pack} accountId={currentAccount}><RobokassaButton invoiceId="" pack={it.pack} accountId={currentAccount} /></TermsGate></div>
          </div>
        ))}
      </div>
    )}
  </div>
)}
{salesView === "rop" && (ropLimits?.minutes?.unlimited || ropLimits?.period?.active === true) && !ropShowPacks && (
  <div data-block="rop-calls-block" style={{margin:"0 0 16px", padding:"14px", border:"1px solid #E9D7FE", borderRadius:"12px", background:"#fff"}}>
    <div style={{display:"flex", flexWrap:"wrap", alignItems:"center", justifyContent:"space-between", gap:"10px", marginBottom:"12px"}}>
      <div style={{fontSize:"15px", fontWeight:700, color:"#3E1C96"}}>📞 Разбор звонков</div>
      {ropLimits && !ropLimits.minutes?.unlimited && (
        <div data-el="rop-limits" style={{width:"100%", display:"flex", flexWrap:"wrap", gap:"8px", marginTop:"8px"}}>
          <div style={{flex:1, minWidth:"150px", background: ropLimits.minutes.left>0 ? "#F4F3FF" : "#FEF3F2", border:"1px solid "+(ropLimits.minutes.left>0?"#D9D6FE":"#FDA29B"), borderRadius:"10px", padding:"10px 12px"}}>
            <div style={{fontSize:"13px", color:"#667085"}}>Минуты разбора</div>
            <div style={{fontSize:"18px", fontWeight:800, color: ropLimits.minutes.left>0?"#3E1C96":"#B42318"}}>{ropLimits.minutes.left} <span style={{fontSize:"13px", fontWeight:600, color:"#667085"}}>из {ropLimits.minutes.purchased}</span></div>
          </div>
          <div style={{flex:1, minWidth:"150px", background: ropLimits.reports_calls.left>0 ? "#F4F3FF" : "#FEF3F2", border:"1px solid "+(ropLimits.reports_calls.left>0?"#D9D6FE":"#FDA29B"), borderRadius:"10px", padding:"10px 12px"}}>
            <div style={{fontSize:"13px", color:"#667085"}}>Отчёты по звонкам</div>
            <div style={{fontSize:"18px", fontWeight:800, color: ropLimits.reports_calls.left>0?"#3E1C96":"#B42318"}}>{ropLimits.reports_calls.left} <span style={{fontSize:"13px", fontWeight:600, color:"#667085"}}>из {ropLimits.reports_calls.purchased}</span></div>
          </div>
          <div style={{flex:1, minWidth:"150px", background: ropLimits.reports_chats.left>0 ? "#F4F3FF" : "#FEF3F2", border:"1px solid "+(ropLimits.reports_chats.left>0?"#D9D6FE":"#FDA29B"), borderRadius:"10px", padding:"10px 12px"}}>
            <div style={{fontSize:"13px", color:"#667085"}}>Отчёты по перепискам</div>
            <div style={{fontSize:"18px", fontWeight:800, color: ropLimits.reports_chats.left>0?"#3E1C96":"#B42318"}}>{ropLimits.reports_chats.left} <span style={{fontSize:"13px", fontWeight:600, color:"#667085"}}>из {ropLimits.reports_chats.purchased}</span></div>
          </div>
          {(ropLimits.minutes.left<=0 || ropLimits.reports_calls.left<=0) && (
            <button onClick={()=>setRopShowPacks(true)} style={{alignSelf:"center", fontSize:"13px", fontWeight:700, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"10px", padding:"11px 18px", cursor:"pointer", whiteSpace:"nowrap"}}>Докупить объём →</button>
          )}
        </div>
      )}
      {ropSetup && !(ropSetup.ready && ropSetup.chats > 0) && (
        <div data-el="rop-setup" style={{width:"100%", background:"#FCFAFF", border:"1px solid #E9D7FE", borderRadius:"12px", padding:"14px 16px", marginTop:"8px"}}>
          <div style={{fontSize:"15px", fontWeight:700, color:"#3E1C96", marginBottom:"8px"}}>🧠 Чтобы я заработал в полную силу</div>
          <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
            {ropSetup.steps.map((st:any)=>(
              <div key={st.key} style={{display:"flex", gap:"9px", alignItems:"flex-start"}}>
                <span style={{fontSize:"15px", lineHeight:1.4}}>{st.ok ? "✅" : "⬜️"}</span>
                <div style={{flex:1}}>
                  <div style={{fontSize:"15px", fontWeight:600, color: st.ok ? "#027A48" : "#1D2939"}}>{st.title}</div>
                  {!st.ok && <div style={{fontSize:"14px", color:"#667085", marginTop:"2px", lineHeight:1.5}}>{st.hint}</div>}
                </div>
                {!st.ok && st.key === "chats" && (
                  <button onClick={syncChats} disabled={syncing} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor: syncing?"default":"pointer", opacity: syncing?0.6:1, whiteSpace:"nowrap"}}>{syncing ? "Тяну…" : "Подтянуть переписки"}</button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {allLimits && (
        <div data-el="rop-limits-cards" style={{width:"100%", display:"flex", flexWrap:"wrap", gap:"10px", marginTop:"10px"}}>
          {limitCard("Минуты разбора", allLimits.rop.minutes.left, allLimits.rop.minutes.purchased, "мин", "linear-gradient(135deg,#9B87F5,#7C5CFC)")}
          {limitCard("Отчёты по звонкам", allLimits.rop.reports_calls.left, allLimits.rop.reports_calls.purchased, "шт", "linear-gradient(135deg,#4C8DFF,#2F6FED)")}
          {limitCard("Отчёты по перепискам", allLimits.rop.reports_chats.left, allLimits.rop.reports_chats.purchased, "шт", "linear-gradient(135deg,#32D583,#12805C)")}
        </div>
      )}
      <details data-el="rop-hint" style={{width:"100%", marginTop:"6px"}}>
        <summary style={{cursor:"pointer", fontSize:"13px", fontWeight:600, color:"#6941C6", listStyle:"none"}}>❓ Как работает разбор звонков</summary>
        <div style={{marginTop:"8px", fontSize:"13px", color:"#475467", lineHeight:1.6, background:"#FCFAFF", border:"1px solid #E9D7FE", borderRadius:"10px", padding:"12px 14px"}}>
          <div style={{marginBottom:"6px"}}><b style={{color:"#3E1C96"}}>1.</b> Борис забирает звонки из колтрекинга Avito — кто звонил, когда, сколько говорили, ответили или пропустили.</div>
          <div style={{marginBottom:"6px"}}><b style={{color:"#3E1C96"}}>2.</b> Запись разговора расшифровывается в текст.</div>
          <div style={{marginBottom:"6px"}}><b style={{color:"#3E1C96"}}>3.</b> Разговор проверяется по чек-листу продаж: представился, выявил потребность, назвал цену, отработал возражения, закрыл на следующий шаг.</div>
          <div style={{marginBottom:"6px"}}><b style={{color:"#3E1C96"}}>4.</b> Борис отмечает сильные моменты, слова-паразиты (и чем их заменить), точки роста и советы по речи.</div>
          <div><b style={{color:"#3E1C96"}}>5.</b> По кнопке «Скачать отчёт» — PDF за выбранный период: воронка, разбивка по менеджерам и разбор каждого звонка.</div>
        </div>
      </details>
      <button onClick={loadRopCalls} disabled={ropCallsLoading} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor: ropCallsLoading?"default":"pointer", opacity: ropCallsLoading?0.6:1}}>{ropCallsLoading ? "Загрузка..." : "🔄 Загрузить звонки"}</button>
      <div data-el="report-controls" style={{display:"flex", flexWrap:"wrap", alignItems:"center", gap:"6px"}}>
        <input type="date" value={reportFrom} onChange={e=>setReportFrom(e.target.value)} style={{padding:"6px 8px", borderRadius:"8px", border:"1px solid #EAECF0", fontSize:"12px"}} />
        <span style={{fontSize:"12px", color:"#98A2B3"}}>—</span>
        <input type="date" value={reportTo} onChange={e=>setReportTo(e.target.value)} style={{padding:"6px 8px", borderRadius:"8px", border:"1px solid #EAECF0", fontSize:"12px"}} />
        <select data-el="report-depth" value={reportMaxCalls} onChange={e=>setReportMaxCalls(Number(e.target.value))} title="Сколько звонков разобрать в отчёте" style={{padding:"6px 8px", borderRadius:"8px", border:"1px solid #EAECF0", fontSize:"12px"}}>
          <option value={5}>5 звонков</option>
          <option value={10}>10 звонков</option>
          <option value={15}>15 звонков</option>
          <option value={30}>30 звонков</option>
          <option value={50}>50 звонков</option>
        </select>
        <button onClick={downloadRopReport} disabled={reportLoading} style={{fontSize:"13px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"8px 14px", cursor: reportLoading?"default":"pointer", opacity: reportLoading?0.6:1}}>{reportLoading ? "Готовлю PDF..." : "📄 Скачать отчёт"}</button>
        <button onClick={downloadChatsReport} disabled={chatsRepLoading} style={{fontSize:"13px", fontWeight:600, color:"#027A48", background:"#ECFDF3", border:"1px solid #A6F4C5", borderRadius:"8px", padding:"8px 14px", cursor: chatsRepLoading?"default":"pointer", opacity: chatsRepLoading?0.6:1}}>{chatsRepLoading ? "Разбираю переписки..." : "💬 Отчёт по перепискам"}</button>
        <button onClick={()=>trainManager(false)} disabled={trainLoading} style={{fontSize:"13px", fontWeight:600, color:"#B54708", background:"#FFFAEB", border:"1px solid #FEDF89", borderRadius:"8px", padding:"8px 14px", cursor: trainLoading?"default":"pointer", opacity: trainLoading?0.6:1}}>{trainLoading ? "Готовлю…" : "🎓 Обучить ИИ-менеджера"}</button>
      </div>
    </div>
    {ropCalls && ropCalls.status === "no_access" && (<div style={{fontSize:"13px", color:"#B54708", background:"#FFFAEB", borderRadius:"8px", padding:"10px 12px"}}>Нет доступа к колтрекингу Avito. Нужен тариф Расширенный/Максимальный с включённым Коллтрекингом и доступом ключа к CallTracking API.</div>)}
    {ropCalls && ropCalls.status === "ok" && (
      <div>
        <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(120px, 1fr))", gap:"10px", marginBottom:"14px"}}>
          <div style={{background:"#F4F3FF", borderRadius:"10px", padding:"12px"}}><div style={{fontSize:"22px", fontWeight:800, color:"#5925DC"}}>{ropCalls.total}</div><div style={{fontSize:"12px", color:"#667085"}}>Всего звонков</div></div>
          <div style={{background:"#ECFDF3", borderRadius:"10px", padding:"12px"}}><div style={{fontSize:"22px", fontWeight:800, color:"#039855"}}>{ropCalls.answered} · {ropCalls.answered_pct}%</div><div style={{fontSize:"12px", color:"#667085"}}>Отвечено</div></div>
          <div style={{background:"#FEF3F2", borderRadius:"10px", padding:"12px"}}><div style={{fontSize:"22px", fontWeight:800, color:"#B42318"}}>{ropCalls.missed} · {ropCalls.missed_pct}%</div><div style={{fontSize:"12px", color:"#667085"}}>Пропущено</div></div>
          <div style={{background:"#F2F4F7", borderRadius:"10px", padding:"12px"}}><div style={{fontSize:"22px", fontWeight:800, color:"#344054"}}>{ropCalls.total_minutes} мин</div><div style={{fontSize:"12px", color:"#667085"}}>разговоров</div></div>
        </div>
        <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
          {(ropCalls.calls||[]).map((c:any)=>(
            <div key={c.call_id} style={{border:"1px solid #EAECF0", borderRadius:"10px", padding:"10px 12px"}}>
              <div style={{display:"flex", flexWrap:"wrap", alignItems:"center", justifyContent:"space-between", gap:"8px"}}>
                <div style={{fontSize:"13px", color:"#344054"}}><b>{c.buyer_phone||"номер скрыт"}</b> · {(c.call_time||"").slice(0,16).replace("T"," ")} · {c.talk_duration>0 ? Math.round(c.talk_duration/60*10)/10+" мин" : "не отвечен"}</div>
                {c.talk_duration>0 && <button onClick={()=>analyzeRopCall(c.call_id)} style={{fontSize:"12px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"6px 12px", cursor:"pointer"}}>Разобрать звонок</button>}
              </div>
              {ropDetailFor===c.call_id && ropDetail && (
                <div style={{marginTop:"10px", paddingTop:"10px", borderTop:"1px solid #F2F4F7", fontSize:"13px", color:"#344054"}}>
                  {ropDetail.loading && <div style={{color:"#98A2B3"}}>🎙 Расшифровываю и разбираю звонок...</div>}
                  {ropDetail.status==="not_ready" && <div style={{color:"#B54708"}}>Запись ещё не готова (появляется до 30 мин после звонка).</div>}
                  {ropDetail.status==="error" && <div style={{color:"#B42318"}}>Ошибка: {ropDetail.message||"не удалось разобрать"}</div>}
                  {ropDetail.status==="ok" && ropDetail.analysis && (
                    <div>
                      <div style={{marginBottom:"8px"}}><b>{ropDetail.analysis.client_name||"Собеседник"}</b> · <span style={{color:"#6941C6"}}>{ropDetail.analysis.role}</span> · балл <b>{ropDetail.analysis.score}/100</b></div>
                      <div style={{display:"flex", flexDirection:"column", gap:"4px", marginBottom:"8px"}}>
                        {(ropDetail.analysis.checklist||[]).map((it:any,i:number)=>(<div key={i} style={{fontSize:"13px", color: it.done?"#027A48":"#B42318"}}>{it.done?"✅":"❌"} {it.item}</div>))}
                      </div>
                      {ropDetail.analysis.failed_at && <div style={{marginBottom:"4px"}}><b style={{color:"#B42318"}}>Провал:</b> {ropDetail.analysis.failed_at}</div>}
                      {ropDetail.analysis.recommendation && <div><b style={{color:"#027A48"}}>Совет:</b> {ropDetail.analysis.recommendation}</div>}
                      {(ropDetail.analysis.filler_phrases||[]).length>0 && (<div style={{marginTop:"8px"}}><b style={{color:"#B54708"}}>🗣 Фразы-паразиты:</b><div style={{marginTop:"3px"}}>{ropDetail.analysis.filler_phrases.map((x:string,i:number)=><span key={i} style={{display:"inline-block", fontSize:"12px", background:"#FFFAEB", color:"#B54708", borderRadius:"6px", padding:"2px 8px", margin:"2px 4px 2px 0"}}>{x}</span>)}</div></div>)}
                      {(ropDetail.analysis.strong_moments||[]).length>0 && (<div style={{marginTop:"8px"}}><b style={{color:"#027A48"}}>⭐ Крутые фишки:</b><ul style={{margin:"3px 0 0", paddingLeft:"18px"}}>{ropDetail.analysis.strong_moments.map((x:string,i:number)=><li key={i} style={{fontSize:"13px", color:"#027A48"}}>{x}</li>)}</ul></div>)}
                      {(ropDetail.analysis.client_hooks||[]).length>0 && (<div style={{marginTop:"8px"}}><b style={{color:"#1570EF"}}>🎣 Зацепило клиента:</b><ul style={{margin:"3px 0 0", paddingLeft:"18px"}}>{ropDetail.analysis.client_hooks.map((x:string,i:number)=><li key={i} style={{fontSize:"13px", color:"#344054"}}>{x}</li>)}</ul></div>)}
                      {(ropDetail.analysis.client_turnoffs||[]).length>0 && (<div style={{marginTop:"8px"}}><b style={{color:"#B42318"}}>🧊 Оттолкнуло:</b><ul style={{margin:"3px 0 0", paddingLeft:"18px"}}>{ropDetail.analysis.client_turnoffs.map((x:string,i:number)=><li key={i} style={{fontSize:"13px", color:"#344054"}}>{x}</li>)}</ul></div>)}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    )}
  </div>
)}
            {salesView === "mop" && salesItems.length === 0 && (<div style={{background:"linear-gradient(135deg,#EAF3FF,#F6F7FB)", border:"1px solid #C7DDFF", borderRadius:"14px", padding:"18px 20px", marginBottom:"20px", display:"flex", alignItems:"center", gap:"16px", flexWrap:"wrap"}}><div style={{flex:1, minWidth:"200px"}}><div style={{fontWeight:800, fontSize:"17px", color:"#14161A"}}>С чего начать</div><div style={{color:"#667085", fontSize:"14px", marginTop:"3px"}}>Объявлений для менеджера нет. Сначала опубликуйте объявления на Avito.</div></div><button className="boris-btn-hover" onClick={() => setActiveTab("listings")} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"14px 26px", fontSize:"16px", fontWeight:700, cursor:"pointer", whiteSpace:"nowrap"}}>Перейти к объявлениям</button></div>)}
            {salesView === "mop" && allLimits && (
              <div data-el="mop-limits" style={{width:"100%", display:"flex", gap:"10px", marginBottom:"14px"}}>
                {limitCard("Сообщения менеджера", allLimits.mop.left, allLimits.mop.purchased, "шт", "linear-gradient(135deg,#FDB022,#F79009)")}
              </div>
            )}
            <div className="b-panel b-card-eq" style={{width:"100%", display: (salesView === "mop" && (managerBalance?.unlimited || managerBalance?.active === true)) ? undefined : "none"}}>
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #12B76A, #039855)"}}>🤝</span>Виртуальный менеджер по продажам</h3>
              <div className="b-meta" style={{marginTop:"4px"}}>Борис сам отвечает покупателям в чатах Avito по вашему прайсу и скриптам — отмечайте галочками объявления, где он должен работать</div>
              <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Как работает подтверждение ответов</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Отвечает клиентам в чатах Avito от лица вашей компании. Каждый ответ приходит вам на подтверждение (✅ отправить / ✏️ поправить / ❌ пропустить) — в Telegram и здесь.</p></details>
              {salesUsage && (
                <div style={{display:"inline-block", background:"#FFFFFF", borderRadius:"8px", padding:"8px 14px", fontSize:"15px", color:"#667085", marginTop:"4px"}}>
                  📊 За месяц: {salesUsage.calls || 0} ответов · себестоимость ~{(salesUsage.cost_rub || 0).toFixed(2)}₽
                </div>
              )}
            </div>

            <div className="b-panel b-card-eq" style={{width:"100%", display: (salesView === "mop") ? undefined : "none"}}>
              <div style={{display:"flex", flexDirection:"column", alignItems:"stretch", marginBottom:"16px"}}>
                <h3 className="b-title" style={{margin:"0 0 16px", display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #4C8DFF, #2F6FED)"}}>📋</span>Объявления для менеджера</h3>
                {(!managerBalance || (!managerBalance.unlimited && managerBalance.active !== true)) && (
  <div data-block="mop-packages" style={{margin:"0 0 20px"}}>
    <div style={{fontSize:"18px", fontWeight:800, color:"#1D2939", marginBottom:"6px"}}>ИИ Менеджер по продажам</div>
    <div style={{fontSize:"14px", color:"#667085", marginBottom:"18px"}}>Подключите базовый пакет на 30 дней. Дополнительные сообщения можно докупить после.</div>
    <div className="boris-card-hover" style={{background:"linear-gradient(135deg,#ECFDF3,#FFFFFF)", border:"2px solid #12B76A", borderRadius:"18px", padding:"26px", position:"relative", overflow:"hidden", marginBottom:"20px", maxWidth:"640px"}}>
      <div style={{position:"absolute", top:"-40px", right:"-40px", width:"120px", height:"120px", borderRadius:"50%", background:"linear-gradient(135deg,#32D583,#12805C)", opacity:0.1}} />
      <div style={{display:"flex", alignItems:"center", gap:"12px", marginBottom:"12px"}}>
        <div style={{width:"56px", height:"56px", borderRadius:"50%", background:"linear-gradient(135deg,#32D583,#12805C)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"26px"}}>🤝</div>
        <div>
          <div style={{fontSize:"17px", fontWeight:800, color:"#1D2939"}}>Базовый пакет</div>
          <div style={{fontSize:"13px", color:"#12805C", fontWeight:700}}>на 30 дней</div>
        </div>
      </div>
      <div style={{fontSize:"30px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"14px"}}>10 000 ₽</div>
      <div style={{fontSize:"14px", color:"#475467", lineHeight:1.7, marginBottom:"20px"}}>💬 1 700 сообщений покупателям в чатах Avito</div>
      <TermsGate product="mop" pack="msg1700" accountId={currentAccount}><RobokassaButton invoiceId="" pack="msg1700" accountId={currentAccount} /></TermsGate>
    </div>
    {(managerBalance?.active === true || managerBalance?.unlimited) && (<>
    <div style={{fontSize:"14px", fontWeight:700, color:"#667085", marginBottom:"10px"}}>Дополнительные сообщения</div>
    <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(240px, 1fr))", gap:"14px", alignItems:"stretch", maxWidth:"640px"}}>
      {[
        {pack:"msg2000", t:"+2 000 сообщений", p:"8 000 ₽"},
        {pack:"msg3000", t:"+3 000 сообщений", p:"11 000 ₽"},
      ].map((it:any)=>(
        <div key={it.pack} className="boris-card-hover" style={{background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", display:"flex", flexDirection:"column", alignItems:"center", textAlign:"center", position:"relative", overflow:"hidden"}}>
          <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:"linear-gradient(135deg,#32D583,#12805C)", opacity:0.08}} />
          <div className="b-ref-ic" style={{width:"44px", height:"44px", borderRadius:"50%", background:"linear-gradient(135deg,#32D583,#12805C)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"20px", marginBottom:"10px"}}>💬</div>
          <div style={{fontSize:"15px", fontWeight:700, color:"#1D2939", marginBottom:"4px"}}>{it.t}</div>
          <div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", letterSpacing:"-0.02em", marginBottom:"14px"}}>{it.p}</div>
          <div style={{marginTop:"auto", width:"100%"}}><TermsGate product="mop" pack={it.pack} accountId={currentAccount}><RobokassaButton invoiceId="" pack={it.pack} accountId={currentAccount} /></TermsGate></div>
        </div>
      ))}
    </div>
    </>)}
  </div>
)}
                {managerBalance?.active === true && (
  <div data-block="manager-balance-block" style={{margin:"0 0 16px", padding:"14px 16px", borderRadius:"12px", border:"1px solid #EAECF0", background: managerBalance.active ? "#F6FEF9" : "#FFFBFA"}}>
    <div style={{display:"flex", flexWrap:"wrap", alignItems:"center", justifyContent:"space-between", gap:"10px"}}>
      <div>
        <div style={{fontSize:"14px", fontWeight:600, color:"#101828"}}>
          {managerBalance.active ? "✅ Пакет менеджера активен" : "⛔ Пакет менеджера не оплачен"}
        </div>
        <div style={{fontSize:"13px", color:"#475467", marginTop:"3px"}}>
          Куплено {managerBalance.purchased} · использовано {managerBalance.used} · осталось <b style={{color: managerBalance.left > 100 ? "#027A48" : "#B54708"}}>{managerBalance.left}</b> сообщений
        </div>
      </div>
      <button onClick={() => setTariffsFinanceMode(true)} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"9px 16px", cursor:"pointer", whiteSpace:"nowrap"}}>
        Докупить сообщения →
      </button>
    </div>
    {managerBalance.purchased > 0 && (
      <div style={{marginTop:"10px", height:"7px", background:"#EAECF0", borderRadius:"4px", overflow:"hidden"}}>
        <div style={{height:"100%", width: `${Math.max(0, Math.min(100, Math.round(managerBalance.left / managerBalance.purchased * 100)))}%`, background: managerBalance.left > 100 ? "#12B76A" : "#F79009"}} />
      </div>
    )}
  </div>
)}
{managerBalance?.active === true && (
  <div data-block="reminders-settings-block" style={{margin:"0 0 16px", padding:"14px 16px", borderRadius:"12px", border:"1px solid #EAECF0", background:"#fff"}}>
    <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", gap:"10px", flexWrap:"wrap"}}>
      <div style={{fontSize:"14px", fontWeight:600, color:"#101828"}}>🔔 Напоминания по застрявшим лидам</div>
      <label style={{display:"inline-flex", alignItems:"center", gap:"8px", cursor:"pointer", fontSize:"13px", color:"#475467"}}>
        <input type="checkbox" checked={!!reminderSettings.enabled} onChange={e=>saveReminderSettings({...reminderSettings, enabled:e.target.checked})} />
        {reminderSettings.enabled ? "Вкл" : "Выкл"}
      </label>
    </div>
    {reminderSettings.enabled && (
      <div style={{marginTop:"12px"}}>
        <div style={{fontSize:"13px", color:"#667085", marginBottom:"8px"}}>Напоминать в Telegram по стадиям:</div>
        <div style={{display:"flex", flexWrap:"wrap", gap:"8px"}}>
          {["ушёл подумать","оставил телефон","в процессе"].map(st => {
            const on = (reminderSettings.stages||[]).includes(st);
            return (
              <button key={st} onClick={()=>toggleReminderStage(st)} style={{fontSize:"13px", padding:"7px 12px", borderRadius:"8px", cursor:"pointer", border: on?"1px solid #7F56D9":"1px solid #EAECF0", background: on?"#F4F3FF":"#fff", color: on?"#5925DC":"#475467", fontWeight: on?600:400}}>
                {on ? "✓ " : ""}{st}
              </button>
            );
          })}
        </div>
        <div style={{display:"flex", alignItems:"center", gap:"8px", marginTop:"12px", fontSize:"13px", color:"#475467"}}>
          Напоминать через
          <input type="number" min={1} max={30} value={reminderSettings.delay_days}
            onChange={e=>saveReminderSettings({...reminderSettings, delay_days: Math.max(1, parseInt(e.target.value)||1)})}
            style={{width:"56px", padding:"6px 8px", borderRadius:"8px", border:"1px solid #EAECF0", fontSize:"13px"}} />
          дн. молчания клиента
        </div>
        {reminderSaved && <div style={{marginTop:"8px", fontSize:"12px", color:"#027A48"}}>✓ Сохранено</div>}
      </div>
    )}
  </div>
)}

{salesView === "rop" && (
  <div data-block="rop-reports-block" style={{margin:"0 0 16px", padding:"14px", border:"1px solid #E9D7FE", borderRadius:"12px", background:"#fff"}}>
    <div style={{display:"flex", flexWrap:"wrap", alignItems:"center", justifyContent:"space-between", gap:"10px", marginBottom:"10px"}}>
      <div style={{fontSize:"15px", fontWeight:700, color:"#3E1C96"}}>📁 Отчёты</div>
      <div style={{display:"flex", gap:"8px", flexWrap:"wrap"}}>
        <button onClick={loadSavedReports} style={{fontSize:"13px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"7px 12px", cursor:"pointer"}}>🔄 Обновить</button>
        <button onClick={()=>deleteSavedReports(false)} disabled={selectedReports.length===0} style={{fontSize:"13px", fontWeight:600, color:"#B42318", background:"#fff", border:"1.5px solid #F04438", borderRadius:"8px", padding:"7px 12px", cursor: selectedReports.length? "pointer":"default", opacity: selectedReports.length?1:0.5}}>Удалить выбранные</button>
        <button onClick={()=>deleteSavedReports(true)} disabled={savedReports.length===0} style={{fontSize:"13px", fontWeight:600, color:"#B42318", background:"#fff", border:"1.5px solid #F04438", borderRadius:"8px", padding:"7px 12px", cursor: savedReports.length? "pointer":"default", opacity: savedReports.length?1:0.5}}>Удалить все</button>
      </div>
    </div>
    <div style={{fontSize:"13px", color:"#667085", marginBottom:"10px"}}>Готовые отчёты хранятся <b>30 дней</b>, затем удаляются безвозвратно. Скачивание сохранённого отчёта — бесплатно, повторный разбор звонков не запускается.</div>
    {savedReports.length === 0 && <div style={{fontSize:"14px", color:"#98A2B3"}}>Пока нет сохранённых отчётов. Сформируйте отчёт кнопкой «📄 Скачать отчёт» выше — он появится здесь.</div>}
    {savedReports.length > 0 && (
      <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
        {savedReports.map((rp:any)=>(
          <div key={rp.id} style={{display:"flex", alignItems:"center", gap:"10px", flexWrap:"wrap", border:"1px solid #EAECF0", borderRadius:"10px", padding:"10px 12px", background:"#FCFCFD"}}>
            <input type="checkbox" checked={selectedReports.includes(rp.id)} onChange={()=>setSelectedReports(prev=> prev.includes(rp.id) ? prev.filter(x=>x!==rp.id) : [...prev, rp.id])} style={{width:"17px", height:"17px", accentColor:"#7F56D9"}} />
            <div style={{flex:1, minWidth:"200px"}}>
              <div style={{fontSize:"14px", fontWeight:600, color:"#1D2939", display:"flex", alignItems:"center", gap:"8px", flexWrap:"wrap"}}>
                <span style={{fontSize:"11px", fontWeight:700, borderRadius:"20px", padding:"2px 10px", background: rp.kind==="chats" ? "#ECFDF3" : "#EEF4FF", color: rp.kind==="chats" ? "#027A48" : "#2F6FED"}}>{rp.kind==="chats" ? "Переписки" : "Звонки"}</span>
                <span>{rp.date_from} — {rp.date_to}</span>
              </div>
              <div style={{fontSize:"12px", color:"#667085", marginTop:"2px"}}>{rp.created_at} · {rp.kind==="chats" ? "диалогов" : "звонков"} {rp.calls_total} · разобрано {rp.calls_analyzed} · {rp.size_kb} КБ · хранится ещё {rp.days_left} дн.</div>
            </div>
            <button onClick={()=>downloadSavedReport(rp.id)} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor:"pointer"}}>Скачать</button>
          </div>
        ))}
      </div>
    )}
  </div>
)}
{managerBalance?.active === true && (
  <div data-block="dlg-analysis-block" style={{margin:"0 0 16px", padding:"14px", border:"1px solid #EAECF0", borderRadius:"12px", background:"#fff"}}>
    <div style={{display:"flex", flexWrap:"wrap", alignItems:"center", justifyContent:"space-between", gap:"10px"}}>
      <div style={{fontSize:"14px", fontWeight:600, color:"#101828"}}>📊 Анализ диалогов на эффективность</div>
      <div style={{display:"flex", gap:"8px"}}>
        {dlgAnalysis && dlgAnalysis.summary && (
          <button onClick={()=>setDlgAnalysisOpen(!dlgAnalysisOpen)} style={{fontSize:"13px", fontWeight:600, color:"#475467", background:"#F2F4F7", border:"none", borderRadius:"8px", padding:"8px 12px", cursor:"pointer"}}>{dlgAnalysisOpen ? "Свернуть" : "Показать"}</button>
        )}
        <button onClick={runDlgAnalysis} disabled={dlgAnalysisLoading} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor: dlgAnalysisLoading?"default":"pointer", opacity: dlgAnalysisLoading?0.6:1}}>{dlgAnalysisLoading ? "Анализирую..." : "🔄 Анализировать"}</button>
      </div>
    </div>
    {dlgAnalysisOpen && dlgAnalysis && dlgAnalysis.summary && (
      <div style={{marginTop:"12px", fontSize:"13px", color:"#344054"}}>
        {dlgAnalysis.generated_at && <div style={{fontSize:"12px", color:"#98A2B3", marginBottom:"10px"}}>Обновлено {dlgAnalysis.generated_at} · диалогов: {dlgAnalysis.analyzed}</div>}
        {(dlgAnalysis.summary.bottlenecks||[]).length>0 && (<div style={{marginBottom:"10px"}}><b style={{color:"#B42318"}}>🔻 Где сливаются лиды:</b><ul style={{margin:"6px 0 0", paddingLeft:"18px"}}>{dlgAnalysis.summary.bottlenecks.map((x:string,i:number)=><li key={i} style={{marginBottom:"3px"}}>{x}</li>)}</ul></div>)}
        {(dlgAnalysis.summary.objections||[]).length>0 && (<div style={{marginBottom:"10px"}}><b style={{color:"#B54708"}}>🗣 Частые возражения:</b><ul style={{margin:"6px 0 0", paddingLeft:"18px"}}>{dlgAnalysis.summary.objections.map((x:string,i:number)=><li key={i} style={{marginBottom:"3px"}}>{x}</li>)}</ul></div>)}
        {(dlgAnalysis.summary.script_tips||[]).length>0 && (<div style={{marginBottom:"10px"}}><b style={{color:"#027A48"}}>✅ Что улучшить в скрипте:</b><ul style={{margin:"6px 0 0", paddingLeft:"18px"}}>{dlgAnalysis.summary.script_tips.map((x:string,i:number)=><li key={i} style={{marginBottom:"3px"}}>{x}</li>)}</ul></div>)}
        {(dlgAnalysis.lost||[]).length>0 && (<div style={{marginTop:"8px", paddingTop:"10px", borderTop:"1px solid #F2F4F7"}}><b style={{color:"#475467"}}>Разбор ушедших лидов:</b>{dlgAnalysis.lost.map((l:any,i:number)=><div key={i} style={{marginTop:"6px", color:"#667085"}}>• {l.reason}</div>)}</div>)}
      </div>
    )}
  </div>
)}
{dlgAnalysis && dlgAnalysis.summary && (
  <div data-block="script-buttons-block" style={{margin:"0 0 16px", padding:"14px", border:"1px solid #E9D7FE", borderRadius:"12px", background:"#FCFAFF"}}>
    <div style={{fontSize:"14px", fontWeight:600, color:"#5925DC", marginBottom:"10px"}}>📝 Скрипт для менеджера по этим рекомендациям</div>
    <div style={{display:"flex", flexWrap:"wrap", gap:"10px"}}>
      <button onClick={makeScriptAuto} disabled={scriptGenLoading} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"9px 16px", cursor: scriptGenLoading?"default":"pointer", opacity: scriptGenLoading?0.6:1}}>{scriptGenLoading ? "Борис пишет скрипт..." : "✍️ Сделай скрипт сам"}</button>
      <button onClick={()=>setScriptManualOpen(!scriptManualOpen)} style={{fontSize:"13px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"9px 16px", cursor:"pointer"}}>✏️ Я напишу сам скрипт</button>
      {scriptSaved && <span style={{fontSize:"12px", color:"#027A48", alignSelf:"center"}}>✓ Сохранено в активный скрипт менеджера</span>}
    </div>
    {scriptManualOpen && (
      <div style={{marginTop:"12px"}}>
        <textarea value={scriptManualText} onChange={e=>setScriptManualText(e.target.value)} placeholder="Напишите скрипт для менеджера: правила ведения диалога, отработку возражений, как вести к контакту..." rows={5} style={{width:"100%", boxSizing:"border-box", padding:"10px", borderRadius:"8px", border:"1px solid #EAECF0", fontSize:"13px", resize:"vertical"}} />
                <div style={{marginTop:"6px"}}>
                  <button onClick={()=>pcCheck("script","manager",scriptManualText)} style={{fontSize:"13px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"7px 12px", cursor:"pointer"}}>🧠 Проверить промт Борисом</button>
                  {pcField==="script" && pcLoading && <div style={{fontSize:"13px", color:"#667085", marginTop:"6px"}}>Борис читает…</div>}
                  {pcField==="script" && pcData && !pcLoading && (
                    <div style={{marginTop:"8px", background:"#FCFAFF", border:"1px solid #E9D7FE", borderRadius:"10px", padding:"12px 14px"}}>
                      <div style={{fontSize:"14px", color:"#1D2939", marginBottom:"6px"}}>{pcData.verdict}</div>
                      {(pcData.issues||[]).map((it:any,i:number)=>(
                        <div key={i} style={{fontSize:"13px", color:"#475467", marginBottom:"4px"}}>• <b>{it.problem}</b> — {it.fix}</div>
                      ))}
                      {pcData.improved && (
                        <button onClick={()=>{ setScriptManualText(pcData.improved); setPcData(null); }} style={{marginTop:"8px", fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor:"pointer"}}>✍️ Заменить на улучшенный</button>
                      )}
                    </div>
                  )}
                </div>
        <button onClick={saveManualScript} style={{marginTop:"8px", fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"9px 16px", cursor:"pointer"}}>Сохранить скрипт</button>
      </div>
    )}
    {scriptGen && (
      <div style={{marginTop:"12px", padding:"12px", background:"#fff", border:"1px solid #EAECF0", borderRadius:"8px", fontSize:"13px", color:"#344054", whiteSpace:"pre-wrap", maxHeight:"260px", overflowY:"auto"}}>{scriptGen}</div>
    )}
  </div>
)}
{salesLeadStats && salesLeadStats.total > 0 && (
  <div data-block="lead-funnel-block" style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(150px, 1fr))", gap:"12px", margin:"0 0 16px"}}>
    {[
      {k:"в процессе", label:"В процессе", c:"#1570EF", bg:"#EFF8FF"},
      {k:"ушёл подумать", label:"Ушёл подумать", c:"#B54708", bg:"#FFFAEB"},
      {k:"оставил телефон", label:"Оставил телефон", c:"#027A48", bg:"#ECFDF3"},
      {k:"отвалился", label:"Отвалился", c:"#B42318", bg:"#FEF3F2"},
    ].map(st => {
      const found = (salesLeadStats.stages || []).find((x:any) => x.key === st.k);
      const n = found ? found.count : 0;
      return (
        <div key={st.k} onClick={() => openLeadStage(st.k)} style={{flex:"1 1 130px", minWidth:"120px", background:st.bg, borderRadius:"12px", padding:"12px 14px", cursor:"pointer", outline: salesLeadOpen===st.k ? `2px solid ${st.c}` : "none"}}>
          <div style={{fontSize:"26px", fontWeight:700, color:st.c, lineHeight:1.1}}>{n}</div>
          <div style={{fontSize:"13px", color:"#475467", marginTop:"2px"}}>{st.label}</div>
        </div>
      );
    })}
    <div style={{flex:"1 1 130px", minWidth:"120px", background:"#F9FAFB", borderRadius:"12px", padding:"12px 14px", border:"1px solid #EAECF0"}}>
      <div style={{fontSize:"26px", fontWeight:700, color:"#101828", lineHeight:1.1}}>{salesLeadStats.phone_conversion}%</div>
      <div style={{fontSize:"13px", color:"#475467", marginTop:"2px"}}>Конверсия в телефон</div>
    </div>
  </div>
)}
{salesLeadOpen && (
  <div data-block="lead-list-block" style={{margin:"0 0 16px", border:"1px solid #EAECF0", borderRadius:"12px", padding:"14px", background:"#fff"}}>
    <div style={{fontSize:"14px", fontWeight:600, color:"#101828", marginBottom:"10px"}}>Лиды: {salesLeadOpen}</div>
    {salesLeadListLoading && <div style={{color:"#667085", fontSize:"14px"}}>Загрузка...</div>}
    {!salesLeadListLoading && salesLeadList.length === 0 && <div style={{color:"#667085", fontSize:"14px"}}>Нет лидов на этой стадии</div>}
    {salesLeadList.map((L:any, i:number) => (
      <div key={i} style={{display:"flex", flexWrap:"wrap", alignItems:"center", gap:"10px", padding:"10px 0", borderTop: i>0 ? "1px solid #F2F4F7" : "none"}}>
        <div style={{flex:"1 1 240px", minWidth:"200px"}}>
          <div style={{fontSize:"14px", color:"#101828"}}>{L.item_title || "Объявление не определено"}</div>
          <div style={{fontSize:"13px", color:"#667085", marginTop:"2px"}}>{L.last_text || ""} · {L.msg_count} сообщ.</div>
        </div>
        {L.phone && (
          <a href={`tel:${L.phone}`} style={{fontSize:"14px", fontWeight:600, color:"#027A48", textDecoration:"none", whiteSpace:"nowrap"}}>📞 {L.phone}</a>
        )}
        <a href={L.chat_url} target="_blank" rel="noopener noreferrer" style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#1570EF", borderRadius:"8px", padding:"7px 12px", textDecoration:"none", whiteSpace:"nowrap"}}>💬 Открыть чат</a>
        {L.item_url && (
          <a href={L.item_url} target="_blank" rel="noopener noreferrer" style={{fontSize:"13px", fontWeight:600, color:"#344054", background:"#F2F4F7", borderRadius:"8px", padding:"7px 12px", textDecoration:"none", whiteSpace:"nowrap"}}>📄 Объявление</a>
        )}
      </div>
    ))}
  </div>
)}
<button className="boris-btn-hover" onClick={loadSalesItems} style={{background:"#EEF2FA", color:"#F79009", border:"none", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"15px"}}>
                  {salesLoading ? "Загрузка..." : "🔄 Обновить список"}
                </button>
              </div>
              <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Как работают отметки</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Отметьте галочками объявления, по которым менеджер должен вести переписку. Если ничего не отмечено — менеджер работает по всем вашим объявлениям (кроме вакансий, они отсеиваются автоматически). Дальше нажимать ничего не нужно: как только вы отметили объявления, менеджер сразу начинает отвечать покупателям в их чатах, а результаты появляются в воронке лидов выше. Каждый ответ приходит вам на подтверждение в Telegram. Дальше нажимать ничего не нужно: как только вы отметили объявления, менеджер сразу начинает отвечать покупателям в их чатах, а результаты появляются в воронке лидов выше. Каждый ответ приходит вам на подтверждение в Telegram.</p></details>

              {salesItems.length === 0 && !salesLoading && (
                <p style={{color:"#667085"}}>Нажмите «Обновить», чтобы загрузить список объявлений.</p>
              )}

              <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                {salesItems.map((item) => (
                  <label key={item.item_id} style={{display:"flex", alignItems:"center", gap:"12px", background:"#FFFFFF", borderRadius:"8px", padding:"12px 14px", cursor:"pointer", border: salesWhitelist.includes(item.item_id) ? "1px solid #F79009" : "1px solid #EEF2FA"}}>
                    <input
                      type="checkbox"
                      checked={salesWhitelist.includes(item.item_id)}
                      onChange={() => toggleSalesWhitelist(item.item_id)}
                      style={{width:"18px", height:"18px", accentColor:"#F79009", cursor:"pointer"}}
                    />
                    <span style={{flex:1, fontSize:"15px", color: item.is_job_posting ? "#8A93A6" : "#1D2939"}}>
                      {item.title}
                      {cityFromUrl(item.url) && <span style={{marginLeft:"8px", fontSize:"13px", color:"#667085"}}>· {cityFromUrl(item.url)}</span>}
                      {item.is_job_posting && <span style={{marginLeft:"8px", fontSize:"15px", color:"#e0a", background:"#2a1a2a", borderRadius:"4px", padding:"2px 6px"}}>вакансия — не для менеджера</span>}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}

        {activeTab === "sitebuild" && (() => {
          const hasSub = billingData && (billingData.unlimited || billingData.active || (billingData.subscription_expires_at && new Date(billingData.subscription_expires_at) > new Date()));
          const screens = [
            {img:"beton_zavod.png", title:"Бетонный завод", tag:"Производство"},
            {img:"almaznoe_burenie.png", title:"Алмазное бурение", tag:"Строительные услуги"},
            {img:"gruzoperevozki.png", title:"Грузоперевозки", tag:"Логистика"},
            {img:"arenda_spectehniki.png", title:"Аренда спецтехники", tag:"Аренда"},
            {img:"landshaftnyy_dizayn.png", title:"Ландшафтный дизайн", tag:"Услуги"},
            {img:"lazernaya_epilyaciya.png", title:"Лазерная эпиляция", tag:"Бьюти"},
            {img:"stihi_pesni_na_zakaz.png", title:"Стихи и песни на заказ", tag:"Творчество"},
          ];
          const features = [
            {icon:"⚙️", t:"Админ-панель", d:"Меняете тексты, цены и заявки сами, без программиста", g:"linear-gradient(135deg,#4C8DFF,#2F6FED)"},
            {icon:"🔍", t:"Внутреннее SEO", d:"Настройка мета-тегов и заголовков под поиск", g:"linear-gradient(135deg,#34D399,#12B76A)"},
            {icon:"✏️", t:"Лёгкое редактирование", d:"Правки контента прямо в панели, за минуту", g:"linear-gradient(135deg,#A78BFA,#7C3AED)"},
            {icon:"📊", t:"CRM внутри сайта", d:"Заявки собираются и хранятся в кабинете (по желанию)", g:"linear-gradient(135deg,#FBBF24,#F79009)"},
            {icon:"🎯", t:"Маркетинг-фишки", d:"Калькуляторы, таймеры, всплывающие акции, квизы", g:"linear-gradient(135deg,#F87171,#EF4444)"},
            {icon:"🧠", t:"Копирайтинг + НЛП", d:"Продающие тексты по проверенным формулам", g:"linear-gradient(135deg,#22D3EE,#0891B2)"},
          ];
          return (
          <div style={{width:"100%"}}>
            {/* ГЕРОЙ */}
            <div className="b-panel" style={{background:"linear-gradient(135deg,#1a2740,#2F6FED)", color:"#fff", padding:"40px", borderRadius:"16px", marginBottom:"24px", position:"relative", overflow:"hidden"}}>
              <div style={{position:"relative", zIndex:2, display:"flex", justifyContent:"space-between", alignItems:"center", gap:"32px", flexWrap:"wrap"}}>
                <div style={{flex:"1 1 380px"}}>
                  <div style={{display:"inline-block", background:"rgba(255,255,255,0.15)", padding:"6px 14px", borderRadius:"20px", fontSize:"13px", fontWeight:600, marginBottom:"16px"}}>🌐 Создание сайтов под ключ</div>
                  <h2 style={{margin:"0 0 12px", fontSize:"32px", fontWeight:800, lineHeight:1.15}}>Сайт, который продаёт<br/>за вас</h2>
                  <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Что входит в сайт</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Современный дизайн, продающий копирайтинг, админка и все маркетинг-инструменты. Готовый сайт под ваш бизнес — быстро и без головной боли.</p></details>
                  <button onClick={() => setSiteOrderOpen(true)} className="boris-btn-hover" style={{background:"#fff", color:"#2F6FED", border:"none", borderRadius:"10px", padding:"14px 36px", fontSize:"17px", fontWeight:700, cursor:"pointer", boxShadow:"0 4px 16px rgba(0,0,0,0.15)"}}>🚀 Заказать сайт</button>
                </div>
                <div style={{flex:"0 0 auto", background:"rgba(255,255,255,0.12)", border:"1px solid rgba(255,255,255,0.25)", borderRadius:"16px", padding:"24px 28px", textAlign:"center", minWidth:"240px"}}>
                  <div style={{fontSize:"12px", fontWeight:700, letterSpacing:"0.5px", opacity:0.85, marginBottom:"8px"}}>🎁 ЦЕНА ПО ПОДПИСКЕ</div>
                  <div style={{fontSize:"46px", fontWeight:800, lineHeight:1, marginBottom:"4px"}}>25 000 ₽</div>
                  <div style={{fontSize:"20px", opacity:0.6, textDecoration:"line-through", marginBottom:"12px"}}>50 000 ₽</div>
                  <div style={{fontSize:"12px", opacity:0.9, lineHeight:1.45}}>{hasSub ? "Ваша подписка активна ✅" : "При активной подписке тарифа «Автопилот 1»"}</div>
                </div>
              </div>
            </div>

            {/* ПРИМЕРЫ РАБОТ — карусель как на лендинге */}
            <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px", marginBottom:"6px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)"}}>🖼</span>Примеры наших работ</h3>
            <p className="b-sub" style={{marginBottom:"18px"}}>Реальные сайты, сделанные для разных ниш — от бетонного завода до бьюти-студии</p>
            <div className="sb-marquee" style={{marginBottom:"36px"}}>
              <div className="sb-marquee-track">
                {[0,1].map(dup => screens.map((sc,i) => (
                  <div key={dup + "-" + i} className="sb-slide" onClick={() => { setSbLightbox(`/images/_sitebuild/screens/${sc.img}`); setSbZoom(1); }} style={{cursor:"zoom-in"}}>
                    <img src={`/images/_sitebuild/screens/${sc.img}`} alt={sc.title} loading="lazy" style={{width:"100%", height:"auto", display:"block", borderRadius:"14px", boxShadow:"0 8px 28px rgba(16,24,40,0.12)", border:"1px solid #EEF2FA", pointerEvents:"none"}} />
                    <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginTop:"10px", padding:"0 4px"}}>
                      <span style={{fontSize:"15px", fontWeight:700, color:"#101828"}}>{sc.title}</span>
                      <span style={{fontSize:"11px", color:"#2F6FED", background:"#E7EFFE", padding:"3px 10px", borderRadius:"12px", fontWeight:600}}>{sc.tag}</span>
                    </div>
                  </div>
                )))}
              </div>
            </div>

            {/* ВИДЕО */}
            <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px", marginBottom:"6px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#F472B6,#DB2777)"}}>🎬</span>Как это выглядит вживую</h3>
            <p className="b-sub" style={{marginBottom:"18px"}}>Живые записи готовых сайтов — посмотрите, как всё работает</p>
            {userRole === "owner" && (
              <div className="b-panel" style={{marginBottom:"16px", background:"#FFFBEB", border:"1px dashed #F79009"}}>
                <div style={{fontSize:"14px", fontWeight:700, color:"#B54708", marginBottom:"10px"}}>🔧 Загрузка видео (видно только вам)</div>
                <div style={{display:"flex", gap:"12px", alignItems:"center", flexWrap:"wrap"}}>
                  <input value={sbVideoTitle} onChange={e => setSbVideoTitle(e.target.value)} placeholder="Название видео (напр. Сайт грузоперевозки)" style={{...inputStyle, width:"auto", minWidth:"280px"}} />
                  <label className="boris-btn-hover" style={{background:"#2F6FED", color:"#fff", borderRadius:"8px", padding:"10px 20px", fontWeight:600, cursor:"pointer", fontSize:"15px"}}>
                    {sbVideoUploading ? "⏳ Загрузка..." : "🎬 Выбрать видео"}
                    <input type="file" accept="video/*" style={{display:"none"}} disabled={sbVideoUploading} onChange={e => sbUploadVideo(e.target.files)} />
                  </label>
                  {sbVideoMsg && <span style={{fontSize:"14px", color: sbVideoMsg.startsWith("✅") ? "#12B76A" : "#F04438"}}>{sbVideoMsg}</span>}
                </div>
              </div>
            )}
            {sbVideos.length > 0 ? (
              <div className="sb-marquee" style={{marginBottom:"32px"}}>
                <div className="sb-marquee-track sb-video-track">
                  {[...sbVideos, ...sbVideos].map((v,i) => (
                    <div key={i} className="sb-slide">
                      <video src={v.url} controls preload="metadata" style={{width:"100%", height:"300px", objectFit:"cover", borderRadius:"14px", display:"block", background:"#000", boxShadow:"0 8px 28px rgba(16,24,40,0.12)"}} />
                      <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginTop:"10px", padding:"0 4px"}}>
                        <span style={{fontSize:"15px", fontWeight:700, color:"#101828"}}>{v.title}</span>
                        {userRole === "owner" && (
                          <span style={{display:"flex", gap:"10px"}}>
                            <button onClick={() => sbRenameVideo(v.filename, v.title)} style={{background:"transparent", color:"#2F6FED", border:"none", cursor:"pointer", fontSize:"13px"}}>✏️ Переименовать</button>
                            <button onClick={() => sbDeleteVideo(v.filename)} style={{background:"transparent", color:"#F04438", border:"1.5px solid #F04438", cursor:"pointer", fontSize:"13px"}}>🗑 Удалить</button>
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="b-panel" style={{textAlign:"center", padding:"40px", color:"#98A2B3", marginBottom:"32px"}}>Видео пока не добавлены</div>
            )}

            {/* ВОЗМОЖНОСТИ */}
            <div style={{display:"grid", gridTemplateColumns:"repeat(3,1fr)", gap:"16px", marginBottom:"24px"}}>
              {features.map((f,i) => (
                <div key={i} className="b-panel b-card-eq boris-btn-hover" style={{padding:"22px"}}>
                  <span className="b-icon-sm" style={{background:f.g, marginBottom:"12px"}}>{f.icon}</span>
                  <div style={{fontSize:"16px", fontWeight:700, color:"#101828", marginBottom:"6px"}}>{f.t}</div>
                  <div style={{fontSize:"14px", color:"#667085", lineHeight:1.45}}>{f.d}</div>
                </div>
              ))}
            </div>

            {userRole === "owner" && (
              <div className="b-panel" style={{marginBottom:"20px", background:"#FFFBEB", border:"1px dashed #F79009"}}>
                <div style={{fontSize:"14px", fontWeight:700, color:"#B54708", marginBottom:"10px"}}>🔧 Загрузка скриншотов (видно только вам)</div>
                <div style={{display:"flex", gap:"12px", alignItems:"center", flexWrap:"wrap"}}>
                  <select className="b-select" value={sbUploadName} onChange={e => setSbUploadName(e.target.value)} style={{...inputStyle, width:"auto", minWidth:"220px"}}>
                    <option value="beton_zavod.png">Бетонный завод</option>
                    <option value="almaznoe_burenie.png">Алмазное бурение</option>
                    <option value="gruzoperevozki.png">Грузоперевозки</option>
                    <option value="arenda_spectehniki.png">Аренда спецтехники</option>
                    <option value="landshaftnyy_dizayn.png">Ландшафтный дизайн</option>
                    <option value="lazernaya_epilyaciya.png">Лазерная эпиляция</option>
                    <option value="stihi_pesni_na_zakaz.png">Стихи и песни</option>
                  </select>
                  <label className="boris-btn-hover" style={{background:"#2F6FED", color:"#fff", borderRadius:"8px", padding:"10px 20px", fontWeight:600, cursor:"pointer", fontSize:"15px"}}>
                    {sbUploading ? "⏳ Загрузка..." : "📁 Выбрать файл"}
                    <input type="file" accept="image/*" style={{display:"none"}} disabled={sbUploading} onChange={e => sbUploadScreen(e.target.files)} />
                  </label>
                  {sbUploadMsg && <span style={{fontSize:"14px", color: sbUploadMsg.startsWith("✅") ? "#12B76A" : "#F04438"}}>{sbUploadMsg}</span>}
                </div>
              </div>
            )}
            {/* МОДАЛКА ЗАЯВКИ */}
            {siteOrderOpen && (
              <div onClick={() => { setSiteOrderOpen(false); setSiteOrderDone(false); }} style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.6)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:4000, padding:"20px"}}>
                <div onClick={e => e.stopPropagation()} style={{background:"#fff", borderRadius:"16px", padding:"32px", maxWidth:"460px", width:"100%", boxShadow:"0 20px 60px rgba(0,0,0,0.3)"}}>
                  {siteOrderDone ? (
                    <div style={{textAlign:"center", padding:"20px 0"}}>
                      <div style={{fontSize:"52px", marginBottom:"12px"}}>✅</div>
                      <h3 style={{margin:"0 0 8px", fontSize:"22px", color:"#101828"}}>Заявка отправлена!</h3>
                      <p style={{color:"#667085", margin:"0 0 20px"}}>Свяжемся с вами в ближайшее время по указанному телефону.</p>
                      <button onClick={() => { setSiteOrderOpen(false); setSiteOrderDone(false); setSiteOrderName(""); setSiteOrderPhone(""); setSiteOrderNiche(""); setSiteOrderComment(""); }} className="boris-btn-hover" style={{background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"12px 32px", fontWeight:700, cursor:"pointer"}}>Закрыть</button>
                    </div>
                  ) : (
                    <>
                      <h3 style={{margin:"0 0 6px", fontSize:"22px", fontWeight:800, color:"#101828"}}>🚀 Заказать сайт</h3>
                      <p style={{color:"#667085", margin:"0 0 20px", fontSize:"15px"}}>Оставьте контакты — рассчитаем стоимость под ваш бизнес и свяжемся с вами.</p>
                      <div style={{display:"flex", flexDirection:"column", gap:"14px"}}>
                        <input value={siteOrderName} onChange={e => setSiteOrderName(e.target.value)} placeholder="Ваше имя" style={inputStyle} />
                        <input value={siteOrderPhone} onChange={e => setSiteOrderPhone(e.target.value)} placeholder="Телефон * (обязательно)" style={inputStyle} />
                        <input value={siteOrderNiche} onChange={e => setSiteOrderNiche(e.target.value)} placeholder="Сфера бизнеса (например: доставка цветов)" style={inputStyle} />
                        <textarea value={siteOrderComment} onChange={e => setSiteOrderComment(e.target.value)} placeholder="Комментарий, пожелания (необязательно)" rows={3} style={{...inputStyle, resize:"vertical" as any}} />
                        <button onClick={submitSiteOrder} disabled={siteOrderSending} className="boris-btn-hover" style={{background: siteOrderSending ? "#E3E7F0" : "#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"14px", fontWeight:700, fontSize:"16px", cursor: siteOrderSending ? "default" : "pointer"}}>{siteOrderSending ? "⏳ Отправка..." : "Отправить заявку"}</button>
                        <button onClick={() => setSiteOrderOpen(false)} style={{background:"transparent", color:"#98A2B3", border:"none", cursor:"pointer", fontSize:"14px"}}>Отмена</button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}
            <style dangerouslySetInnerHTML={{__html: `
              .sb-marquee { width: 100%; overflow: hidden; -webkit-mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent); mask-image: linear-gradient(90deg, transparent, #000 4%, #000 96%, transparent); }
              .sb-marquee-track { display: flex; gap: 28px; width: max-content; animation: sb-scroll 70s linear infinite; }
              .sb-marquee:hover .sb-marquee-track { animation-play-state: paused; }
              .sb-slide { flex: 0 0 auto; width: 520px; }
              .sb-video-track { animation-duration: 90s; }
              @keyframes sb-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
            `}} />
            {sbLightbox && (
              <div onClick={() => setSbLightbox(null)} style={{position:"fixed", inset:0, background:"rgba(10,14,20,0.93)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:5000, padding:"30px", overflow:"hidden"}}>
                <button onClick={(e) => { e.stopPropagation(); setSbLightbox(null); }} style={{position:"absolute", top:"20px", right:"30px", background:"#fff", border:"none", color:"#101828", fontSize:"16px", fontWeight:700, cursor:"pointer", padding:"12px 24px", borderRadius:"24px", display:"flex", alignItems:"center", gap:"8px", zIndex:10, boxShadow:"0 4px 16px rgba(0,0,0,0.3)"}}>✕ Закрыть</button>
                <div style={{position:"absolute", bottom:"24px", left:"50%", transform:"translateX(-50%)", display:"flex", gap:"12px", zIndex:10}}>
                  <button onClick={(e) => { e.stopPropagation(); setSbZoom(z => Math.max(1, z - 0.25)); }} style={{background:"rgba(255,255,255,0.15)", border:"none", color:"#fff", fontSize:"24px", cursor:"pointer", width:"48px", height:"48px", borderRadius:"50%"}}>−</button>
                  <button onClick={(e) => { e.stopPropagation(); setSbZoom(1); }} style={{background:"rgba(255,255,255,0.15)", border:"none", color:"#fff", fontSize:"13px", cursor:"pointer", height:"48px", borderRadius:"24px", padding:"0 18px"}}>{Math.round(sbZoom*100)}%</button>
                  <button onClick={(e) => { e.stopPropagation(); setSbZoom(z => Math.min(4, z + 0.25)); }} style={{background:"rgba(255,255,255,0.15)", border:"none", color:"#fff", fontSize:"24px", cursor:"pointer", width:"48px", height:"48px", borderRadius:"50%"}}>+</button>
                </div>
                <img src={sbLightbox} alt="Просмотр" onClick={e => e.stopPropagation()} onWheel={(e) => { e.stopPropagation(); setSbZoom(z => Math.min(4, Math.max(1, z - e.deltaY * 0.002))); }} style={{maxWidth:"92vw", maxHeight:"92vh", objectFit:"contain", borderRadius:"10px", boxShadow:"0 20px 60px rgba(0,0,0,0.5)", transform:`scale(${sbZoom})`, transition:"transform 0.15s", cursor: sbZoom > 1 ? "zoom-out" : "zoom-in"}} />
              </div>
            )}
          </div>
          );
        })()}

        {activeTab === "chatbots" && (
          <div style={{background:"#F6F7FB", borderRadius:"12px", padding:"32px", border:"1px solid #EEF2FA"}}>
            <h3 style={{margin:"0 0 8px", fontSize:"15px"}}>Чат-боты ИИ</h3>
            <p style={{color:"#667085"}}>Скоро: чат-боты для соцсетей и мессенджеров на базе ИИ.</p>
          </div>
        )}

        {activeTab === "company" && (
          <div className="b-panel b-card-eq" style={{width:"100%"}}>
            <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #4C8DFF, #2F6FED)"}}>🏢</span>Информация о компании</h3>
            <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Зачем это заполнять</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Борис берёт отсюда факты для текстов объявлений и ответов клиентам. Чем полнее заполнено — тем точнее результат и меньше выдумок.</p></details>
            <div style={{marginBottom:"18px", padding:"16px 18px", background:"#F0F6FF", borderRadius:"12px", border:"1px solid #D5E4FB", display:"flex", alignItems:"center", justifyContent:"space-between", gap:"12px", flexWrap:"wrap"}}>
              <div>
                <div style={{fontWeight:600, color:"#14161A", fontSize:"15px"}}>🧠 Научите Бориса про ваш бизнес</div>
                <div style={{color:"#667085", fontSize:"13px", marginTop:"2px"}}>Доставка, гарантии, товары — Борис будет точнее отвечать вашим покупателям. Можно текстом или файлом.</div>
              </div>
              <button onClick={() => kbOpenModal(currentAccount)} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"10px 18px", cursor:"pointer", fontSize:"14px", fontWeight:600, whiteSpace:"nowrap"}}>Обучить Бориса</button>
            </div>
            <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"18px", alignItems:"start"}}>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Сайт</label>
                <input value={companyForm.website} onChange={e => setCompanyForm((p: any) => ({...p, website: e.target.value}))} placeholder="https://..." style={inputStyle} />
              </div>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Сфера</label>
                <input value={companyForm.niche} onChange={e => setCompanyForm((p: any) => ({...p, niche: e.target.value}))} placeholder="Например: шкафы-купе на заказ" style={inputStyle} />
              </div>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Описание</label>
                <textarea value={companyForm.description} onChange={e => setCompanyForm((p: any) => ({...p, description: e.target.value}))} rows={4} style={{...inputStyle, resize:"vertical" as any}} />
                <div style={{marginTop:"6px"}}>
                  <button onClick={()=>pcCheck("descr","manager",companyForm.description)} style={{fontSize:"13px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"7px 12px", cursor:"pointer"}}>🧠 Проверить промт Борисом</button>
                  {pcField==="descr" && pcLoading && <div style={{fontSize:"13px", color:"#667085", marginTop:"6px"}}>Борис читает…</div>}
                  {pcField==="descr" && pcData && !pcLoading && (
                    <div style={{marginTop:"8px", background:"#FCFAFF", border:"1px solid #E9D7FE", borderRadius:"10px", padding:"12px 14px"}}>
                      <div style={{fontSize:"14px", color:"#1D2939", marginBottom:"6px"}}>{pcData.verdict}</div>
                      {(pcData.issues||[]).map((it:any,i:number)=>(
                        <div key={i} style={{fontSize:"13px", color:"#475467", marginBottom:"4px"}}>• <b>{it.problem}</b> — {it.fix}</div>
                      ))}
                      {pcData.improved && (
                        <button onClick={()=>{ setCompanyForm((p:any)=>({...p, description: pcData.improved})); setPcData(null); }} style={{marginTop:"8px", fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor:"pointer"}}>✍️ Заменить на улучшенный</button>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Преимущества</label>
                <textarea value={companyForm.advantages} onChange={e => setCompanyForm((p: any) => ({...p, advantages: e.target.value}))} rows={3} style={{...inputStyle, resize:"vertical" as any}} />
                <div style={{marginTop:"6px"}}>
                  <button onClick={()=>pcCheck("adv","manager",companyForm.advantages)} style={{fontSize:"13px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"7px 12px", cursor:"pointer"}}>🧠 Проверить промт Борисом</button>
                  {pcField==="adv" && pcLoading && <div style={{fontSize:"13px", color:"#667085", marginTop:"6px"}}>Борис читает…</div>}
                  {pcField==="adv" && pcData && !pcLoading && (
                    <div style={{marginTop:"8px", background:"#FCFAFF", border:"1px solid #E9D7FE", borderRadius:"10px", padding:"12px 14px"}}>
                      <div style={{fontSize:"14px", color:"#1D2939", marginBottom:"6px"}}>{pcData.verdict}</div>
                      {(pcData.issues||[]).map((it:any,i:number)=>(
                        <div key={i} style={{fontSize:"13px", color:"#475467", marginBottom:"4px"}}>• <b>{it.problem}</b> — {it.fix}</div>
                      ))}
                      {pcData.improved && (
                        <button onClick={()=>{ setCompanyForm((p:any)=>({...p, advantages: pcData.improved})); setPcData(null); }} style={{marginTop:"8px", fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor:"pointer"}}>✍️ Заменить на улучшенный</button>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div style={{gridColumn:"1 / -1", display:"flex", gap:"16px", alignItems:"flex-end", flexWrap:"wrap", borderTop:"1px solid #EEF2FA", paddingTop:"18px", marginTop:"4px"}}>
                <div style={{flex:"1 1 320px"}}>
                  <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Тон общения</label>
                  <input value={companyForm.tone} onChange={e => setCompanyForm((p: any) => ({...p, tone: e.target.value}))} placeholder="Например: дружелюбный, деловой" style={inputStyle} />
                </div>
                <button className="boris-btn-hover" onClick={saveCompanyForm} disabled={companySaving} style={{background: companySaving ? "#E3E7F0" : "#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"0 28px", fontWeight:"bold", cursor: companySaving ? "default" : "pointer", whiteSpace:"nowrap", height:"46px", marginBottom:"1px"}}>
                  {companySaving ? "⏳ Сохраняю..." : "💾 Сохранить"}
                </button>
                {companySavedMsg && <div style={{color:"#12B76A", fontSize:"15px", fontWeight:600, alignSelf:"center"}}>{companySavedMsg}</div>}
              </div>
            </div>
          </div>
        )}

        {activeTab === "settings" && (
          <div className="b-panel">
            <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#7C5CFC,#5B3FD9)"}}>🛡</span>Автопилот и безопасность</h3>
            <div className="b-meta" style={{marginTop:"4px"}}>Борис сам управляет ставками и продвижением по вашим правилам — вы задаёте рамки, он работает круглосуточно. Здесь же лимиты и защита от лишних трат.</div>
            <details style={{margin:"0 0 16px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Подробнее о подтверждениях</summary><p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>Управляйте тем, когда Борису можно менять объявления на Авито без вашего подтверждения. По умолчанию — всегда спрашивать.</p></details>

            <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(250px, 1fr))", gap:"16px", marginBottom:"26px"}}>
              <div className="b-card b-stat" style={{animationDelay:"0s", minHeight:"116px"}}>
                <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#7C5CFC,#5B3FD9)"}}>🛡</span>
                <div>
                  <div className="b-meta" style={{fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px", marginBottom:"5px"}}>Режим автопилота</div>
                  <div style={{fontSize:"20px", fontWeight:800, color:"#2F6FED", lineHeight:1.2}}>{autopilotMode==="always_auto" ? "Работает сам" : autopilotMode==="dates" ? "Сам по датам" : "Спрашивает каждый раз"}</div>
                </div>
              </div>
              <div className="b-card b-stat" style={{animationDelay:"0.11s", minHeight:"116px"}}>
                <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)"}}>📋</span>
                <div>
                  <div className="b-meta" style={{fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px", marginBottom:"4px"}}>Записей в журнале</div>
                  <div className="b-stat-num"><CountUp to={auditLog.length} delay={110} /></div>
                </div>
              </div>
              <div className="b-card b-stat" style={{animationDelay:"0.22s", minHeight:"116px"}}>
                <span className="b-stat-ico" style={{background: republishCandidates.length > 0 ? "linear-gradient(135deg,#FDB022,#F79009)" : "linear-gradient(135deg,#98A2B3,#667085)"}}>🔄</span>
                <div>
                  <div className="b-meta" style={{fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px", marginBottom:"4px"}}>Кандидатов на переустановку</div>
                  <div className="b-stat-num"><CountUp to={republishCandidates.length} delay={220} /></div>
                </div>
              </div>
            </div>

            <div style={{marginBottom:"20px"}}>
              <label className="b-label" style={{marginBottom:"12px", fontSize:"15px"}}>⚙️ Режим работы Бориса</label>
              <div style={{display:"flex", flexDirection:"column", gap:"12px"}}>
                <label className="b-card b-fcard" style={{display:"flex", flexDirection:"row", alignItems:"center", textAlign:"left", gap:"16px", cursor:"pointer", padding:"20px 22px", background: autopilotMode==="always_ask" ? "#E7EFFE" : "#FFFFFF", borderColor: autopilotMode==="always_ask" ? "#2F6FED" : "#E3E7F0", animationDelay:"0.05s"}}>
                  <input type="radio" checked={autopilotMode==="always_ask"} onChange={() => setAutopilotMode("always_ask")} style={{width:"18px", height:"18px", accentColor:"#2F6FED", cursor:"pointer", flexShrink:0}} />
                  <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#3DBE93,#12805C)"}}>🔒</span>
                  <div style={{minWidth:0}}>
                    <div style={{fontWeight:700, fontSize:"16px", color: autopilotMode==="always_ask" ? "#2F6FED" : "#1D2939", marginBottom:"3px"}}>Спрашивать каждый раз (рекомендуется)</div>
                    <div className="b-meta" style={{whiteSpace:"normal", lineHeight:1.45}}>Борис показывает что хочет изменить и ждёт вашего «да»</div>
                  </div>
                </label>
                <label className="b-card b-fcard" style={{display:"flex", flexDirection:"row", alignItems:"center", textAlign:"left", gap:"16px", cursor:"pointer", padding:"20px 22px", background: autopilotMode==="always_auto" ? "#E7EFFE" : "#FFFFFF", borderColor: autopilotMode==="always_auto" ? "#2F6FED" : "#E3E7F0", animationDelay:"0.10s"}}>
                  <input type="radio" checked={autopilotMode==="always_auto"} onChange={() => setAutopilotMode("always_auto")} style={{width:"18px", height:"18px", accentColor:"#2F6FED", cursor:"pointer", flexShrink:0}} />
                  <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#FDB022,#F79009)"}}>⚡</span>
                  <div style={{minWidth:0}}>
                    <div style={{fontWeight:700, fontSize:"16px", color: autopilotMode==="always_auto" ? "#2F6FED" : "#1D2939", marginBottom:"3px"}}>Работать сам всегда</div>
                    <div className="b-meta" style={{whiteSpace:"normal", lineHeight:1.45}}>Борис действует сам, без подтверждений. Включает также почасовую корректировку ставок в разделе «Советник».</div>
                  </div>
                </label>
                <label className="b-card b-fcard" style={{display:"flex", flexDirection:"row", alignItems:"center", textAlign:"left", gap:"16px", cursor:"pointer", padding:"20px 22px", background: autopilotMode==="dates" ? "#E7EFFE" : "#FFFFFF", borderColor: autopilotMode==="dates" ? "#2F6FED" : "#E3E7F0", animationDelay:"0.15s"}}>
                  <input type="radio" checked={autopilotMode==="dates"} onChange={() => setAutopilotMode("dates")} style={{width:"18px", height:"18px", accentColor:"#2F6FED", cursor:"pointer", flexShrink:0}} />
                  <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)"}}>🏖</span>
                  <div style={{minWidth:0}}>
                    <div style={{fontWeight:700, fontSize:"16px", color: autopilotMode==="dates" ? "#2F6FED" : "#1D2939", marginBottom:"3px"}}>Работать сам только в отпуске</div>
                    <div className="b-meta" style={{whiteSpace:"normal", lineHeight:1.45}}>Борис действует сам только в выбранные даты</div>
                  </div>
                </label>
              </div>
            </div>

            {autopilotMode==="dates" && (
              <div style={{display:"flex", gap:"12px", marginBottom:"20px"}}>
                <div style={{flex:1}}><label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>С</label><input type="date" value={autopilotFrom} onChange={e => setAutopilotFrom(e.target.value)} style={inputStyle} /></div>
                <div style={{flex:1}}><label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>По</label><input type="date" value={autopilotTo} onChange={e => setAutopilotTo(e.target.value)} style={inputStyle} /></div>
              </div>
            )}

            <button className="boris-btn-hover" onClick={saveAutopilot} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px 24px", fontWeight:"bold", cursor:"pointer", marginBottom:"32px"}}>Сохранить настройки</button>

            <div style={{borderTop:"1px solid #EEF2FA", paddingTop:"24px"}}>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"16px"}}>
                <h4 style={{margin:0, fontSize:"15px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#4C8DFF,#2F6FED)", width:"30px", height:"30px", fontSize:"15px", display:"inline-flex", alignItems:"center", justifyContent:"center", borderRadius:"50%", flexShrink:0}}>📋</span>Журнал действий Бориса</h4>
                <button className="boris-btn-hover" onClick={loadAuditLog} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"15px"}}>Обновить</button>
              </div>
              {auditLog.length === 0 ? (
                <p style={{color:"#667085", fontSize:"15px"}}>Пока нет записей. Здесь появятся все действия Бориса с объявлениями.</p>
              ) : (
                <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                  {auditLog.map((e: any, i: number) => (
                    <div key={i} className="boris-card-hover" style={{background:"#FFFFFF", borderRadius:"10px", padding:"12px 14px", fontSize:"15px", border:"1px solid #EEF2FA", boxShadow:"0 1px 3px rgba(16,24,40,0.05)"}}>
                      <div style={{display:"flex", justifyContent:"space-between", color:"#667085", fontSize:"15px", marginBottom:"5px"}}>
                        <span>{e.who || (e.actor === "user" ? "👤 Вы" : e.actor === "director" ? "👑 Директор" : "Борис")}</span>
                        <span style={{fontFamily:"monospace"}}>{(e.ts || "").replace("T", " ")}</span>
                      </div>
                      <div style={{color:"#1D2939"}}>{e.human ? e.human.replace(/^(👤 Вы|Борис|👑 Директор)\s*/, "") : (<><b>{e.action}</b>{e.details ? ` — ${e.details}` : ""}</>)}</div>
                    </div>
                  ))}
                </div>
              )}

                          </div>
          </div>
        )}

        {activeTab === "billing" && (
          <div className="b-panel">
            <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #32D583, #12805C)"}}>💳</span>Лимиты подписки</h3>
            {!billingData ? (
              <p style={{color:"#667085", fontSize:"15px"}}>Загрузка…</p>
            ) : (
              <>
                <div className="b-card b-stat" style={{marginBottom:"20px", animationDelay:"0s", minHeight:"130px", background: billingData.tier === "none" ? "#FFFFFF" : "linear-gradient(135deg,#F2FBF7,#FFFFFF)"}}>
                  <span className="b-stat-ico" style={{background: billingData.tier === "none" ? "linear-gradient(135deg,#98A2B3,#667085)" : "linear-gradient(135deg,#3DBE93,#12805C)"}}>{billingData.tier === "none" ? "🔒" : "⭐"}</span>
                  <div>
                    <div className="b-meta" style={{fontWeight:700, color:"#98A2B3", textTransform:"uppercase", letterSpacing:"0.06em", fontSize:"11px", marginBottom:"3px"}}>Ваш тариф</div>
                    <div style={{fontSize:"22px", fontWeight:800, color:"#1D2939", lineHeight:1.15}}>{billingData.tier_name}</div>
                    <div className="b-meta" style={{marginTop:"4px"}}>
                      {billingData.tier === "none" ? "Тариф не подключён" : `До обновления лимитов: ${billingData.days_left} дн.`}
                    </div>
                  </div>
                </div>

                <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"18px", alignItems:"stretch"}}>
                  {Object.entries(billingData.usage || {}).map(([unit, info]: [string, any], idx: number) => {
                    const pct = info.limit > 0 ? Math.min(100, Math.round(info.used / info.limit * 100)) : (info.used > 0 ? 100 : 0);
                    const bar = pct >= 100 ? "linear-gradient(90deg,#F97066,#F04438)" : pct >= 80 ? "linear-gradient(90deg,#FDB022,#F79009)" : "linear-gradient(90deg,#3DBE93,#12805C)";
                    const ico: any = { listings: "📋", banners: "🎨", templates: "📝", images: "🖼" };
                    const grd: any = { listings: "linear-gradient(135deg,#4C8DFF,#2F6FED)", banners: "linear-gradient(135deg,#F06AA8,#D93D86)", templates: "linear-gradient(135deg,#7C5CFC,#5B3FD9)", images: "linear-gradient(135deg,#F7A440,#E8850B)" };
                    return (
                      <div key={unit} className="b-card b-stat" style={{flexDirection:"column", alignItems:"stretch", justifyContent:"center", gap:"16px", animationDelay:`${0.08 + idx * 0.09}s`, minHeight:"150px", padding:"24px 26px"}}>
                        <div style={{display:"flex", alignItems:"center", gap:"16px"}}>
                          <span className="b-stat-ico" style={{background: grd[unit] || "linear-gradient(135deg,#98A2B3,#667085)"}}>{ico[unit] || "📦"}</span>
                          <div style={{flex:1, minWidth:0}}>
                            <div style={{color:"#1D2939", fontWeight:700, fontSize:"17px", textTransform:"capitalize"}}>{info.name}</div>
                            <div className="b-meta" style={{marginTop:"3px", fontSize:"13px"}}>{info.limit > 0 ? `${info.used} из ${info.limit}` : "лимит не задан"}</div>
                          </div>
                          <div style={{fontSize:"26px", fontWeight:800, color: pct >= 100 ? "#F04438" : pct >= 80 ? "#F79009" : "#12805C"}}>{info.limit > 0 ? `${pct}%` : "—"}</div>
                        </div>
                        <div style={{background:"#EEF2FA", borderRadius:"999px", height:"10px", overflow:"hidden"}}>
                          <div style={{width:`${pct}%`, height:"100%", background: bar, borderRadius:"999px", transition:"width 1s cubic-bezier(.22,1,.36,1)", transitionDelay:`${0.2 + idx * 0.09}s`}} />
                        </div>
                      </div>
                    );
                  })}
                </div>

                {billingData.tier === "none" && (
                  <div className="b-card" style={{marginTop:"20px", display:"flex", alignItems:"center", gap:"14px", padding:"18px 20px", background:"#FFFBFA", borderColor:"#FECDCA"}}>
                    <span className="b-stat-ico" style={{background:"linear-gradient(135deg,#F97066,#F04438)", width:"42px", height:"42px", fontSize:"20px"}}>🔔</span>
                    <div>
                      <div style={{color:"#B42318", fontWeight:700, fontSize:"14px", marginBottom:"2px"}}>Тариф не подключён</div>
                      <div className="b-meta" style={{whiteSpace:"normal", color:"#98A2B3"}}>Борис пока не может создавать объявления и баннеры. Обратитесь для подключения тарифа.</div>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {activeTab === "plan" && (
          <div style={{width:"100%", display:"flex", flexDirection:"column", gap:"20px"}}>
            {planItems.length === 0 && (<div style={{background:"linear-gradient(135deg,#EAF3FF,#F6F7FB)", border:"1px solid #C7DDFF", borderRadius:"14px", padding:"18px 20px", marginBottom:"20px", display:"flex", alignItems:"center", gap:"16px", flexWrap:"wrap"}}><div style={{flex:1, minWidth:"200px"}}><div style={{fontWeight:800, fontSize:"17px", color:"#14161A"}}>С чего начать</div><div style={{color:"#667085", fontSize:"14px", marginTop:"3px"}}>Задач пока нет. Борис создаст их сам, когда вы запустите рекламу.</div></div><button className="boris-btn-hover" onClick={() => setActiveTab("listings")} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"14px 26px", fontSize:"16px", fontWeight:700, cursor:"pointer", whiteSpace:"nowrap"}}>Запустить рекламу</button></div>)}
            {/* БЛОК: задачи в очереди по аккаунту со статусами */}
            <div className="b-panel b-card-eq account-tasks-block" style={{width:"100%"}}>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"14px"}}>
                <h3 className="b-title" style={{margin:0, display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #3DBE93, #12805C)"}}>⚙️</span>Что Борис делает прямо сейчас</h3>
                <span style={{fontSize:"13px", color:"#8A93A6"}}>
                  🟡 {accountTasks.filter(t => t.status === "queued").length} в очереди · 🔵 {accountTasks.filter(t => t.status === "running").length} выполняется · ✅ {accountTasks.filter(t => t.status === "done").length} готово
                </span>
              </div>
              {accountTasks.length === 0 ? (
                <p style={{color:"#667085", fontSize:"14px", textAlign:"center", padding:"12px"}}>Пока нет задач в очереди по этому аккаунту</p>
              ) : (
                <div style={{display:"flex", flexDirection:"column", gap:"8px", maxHeight:"320px", overflowY:"auto"}}>
                  {accountTasks.map(t => {
                    const st = t.status === "queued" ? {icon:"🟡", label:"В очереди", color:"#E08A2C"}
                             : t.status === "running" ? {icon:"🔵", label:"Выполняется", color:"#2F6FED"}
                             : t.status === "done" ? {icon:"✅", label:"Готово", color:"#1E8E5A"}
                             : t.status === "error" ? {icon:"❌", label:"Ошибка", color:"#D64545"}
                             : {icon:"⚪", label:t.status, color:"#8A93A6"};
                    const typeLabels: Record<string, string> = {generate_images:"Генерация картинок", city_analysis:"Анализ конкурентов", report_back:"Отчёт Бориса", ab_test:"A/B тест", avito_categories_sync:"Синхронизация категорий", execute_plan_subtask:"Выполнение плана", enrich_descriptions:"Улучшение описаний", parse_site:"Выгрузка товаров с сайта", product_description:"Написание описания товара", product_banner:"Создание баннера", fetch_all_galleries:"Загрузка фото товаров", generate_ads:"Создание объявлений", feed_generate:"Сборка фида для Avito"};
                    const typeLabel = typeLabels[t.task_type] || t.task_type;
                    return (
                      <div key={t.id} style={{display:"flex", justifyContent:"space-between", alignItems:"center", padding:"10px 14px", background:"#F9FBFF", borderRadius:"8px", borderLeft:`3px solid ${st.color}`}}>
                        <div style={{display:"flex", alignItems:"center", gap:"10px"}}>
                          <span style={{fontSize:"16px"}}>{st.icon}</span>
                          <div>
                            <div style={{fontSize:"14px", fontWeight:600, color:"#14161A"}}>{typeLabel}</div>
                            <div style={{fontSize:"12px", color:"#8A93A6"}}>№{t.id} · {new Date(t.created_at).toLocaleString("ru-RU", {day:"2-digit", month:"2-digit", hour:"2-digit", minute:"2-digit"})}</div>
                          </div>
                        </div>
                        <span style={{fontSize:"13px", fontWeight:600, color:st.color}}>{st.label}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="b-panel b-card-eq" style={{width:"100%"}}>
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg, #3DBE93, #12805C)"}}>✅</span>Задачи и план</h3>
              <p className="b-sub">Список задач по этому аккаунту — вносите вручную или Борис фиксирует сам при выполнении оркестратором.</p>

            <div style={{display:"flex", gap:"10px", marginBottom:"20px"}}>
              <textarea value={newPlanItemText} onChange={e => setNewPlanItemText(e.target.value)} placeholder="Новая задача..." rows={2} style={{...inputStyle, flex:1, resize:"vertical", minHeight:"44px", fontFamily:"inherit", fontSize:"15px"}} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addPlanItem(); } }} />
              <button className="boris-btn-hover" onClick={addPlanItem} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"11px 18px", fontWeight:"bold", cursor:"pointer", whiteSpace:"nowrap", fontSize:"15px"}}>+ Добавить</button>
            </div>

            <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
              {planItems.map((item: any) => (
                <div key={item.id} className="b-task-row" style={{display:"flex", alignItems:"flex-start", gap:"14px"}}>
                  <select className="b-select" value={item.status} onChange={e => updatePlanItemStatus(item.id, e.target.value)} style={{background:"#EEF2FA", color:"#1D2939", border:"1px solid #E3E7F0", borderRadius:"6px", padding:"6px 8px", fontSize:"15px"}}>
                    <option value="planned">📋 Запланировано</option>
                    <option value="needs_launch">🚀 Требует запуска</option>
                    <option value="in_progress">⏳ В процессе</option>
                    <option value="done">✅ Сделано</option>
                    <option value="cancelled">❌ Отменено</option>
                  </select>
                  <div style={{flex:1}}>
                    <div style={{color:"#1D2939", fontSize:"15px", lineHeight:"1.6", textDecoration: item.status === "cancelled" ? "line-through" : "none", opacity: item.status === "done" ? 0.6 : 1}}>{item.text}</div>
                    <div style={{color:"#8A93A6", fontSize:"15px", marginTop:"4px"}}>{item.source === "boris" ? "Борис" : "👤 Вручную"}{item.due_date ? ` · до ${item.due_date.slice(0,10)}` : ""}</div>
                  </div>
                  <button className="boris-btn-hover" onClick={() => executePlanItem(item.id)} disabled={item.status === "running"} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"11px 20px", fontSize:"15px", fontWeight:"bold", cursor:"pointer", whiteSpace:"nowrap"}}>
                    {item.status === "running" ? "Выполняется..." : "▶️ Сделай"}
                  </button>
                  <button className="boris-btn-hover" onClick={() => deletePlanItem(item.id)} style={{background:"#FFFFFF", border:"none", color:"#2F6FED", cursor:"pointer", fontSize:"15px"}}>×</button>
                </div>
              ))}
              {planItems.length === 0 && <p style={{color:"#667085", fontSize:"15px", background:"#F5FAFF", borderRadius:"8px", padding:"16px", textAlign:"center"}}>📭 Пока нет задач по этому аккаунту — добавьте первую вручную выше</p>}
            </div>
            </div>

            <div className="b-panel b-card-eq" style={{width:"100%"}}>
              <h3 className="b-title" style={{display:"flex", alignItems:"center", gap:"12px"}}><span className="b-icon-sm" style={{background:"linear-gradient(135deg,#7C5CFC,#5B3FD9)"}}>📦</span>Пакетный запуск направлений</h3>
              <details style={{marginBottom:"14px"}}><summary style={{cursor:"pointer",color:"#2F6FED",fontSize:"14px",fontWeight:600}}>Подробнее</summary><p className="b-sub">Опишите одним текстом несколько товарных направлений — Борис разберёт их и запустит по конвейеру на каждое. Ничего не запускается без вашего подтверждения.</p></details>

              <textarea value={batchPromptText} onChange={e => setBatchPromptText(e.target.value)}
                placeholder={"Например: Кухни под ключ — 5 объявлений, 3 баннера, цены 50-300к;\nШкафы-купе — 4 объявления, 2 баннера;\nГардеробные на заказ — 6 объявлений"}
                disabled={batchParsing || !!batchPreview} rows={5}
                style={{...inputStyle, width:"100%", resize:"vertical", fontFamily:"inherit", marginBottom:"14px"}} />

              {!batchPreview ? (
                <button className="boris-btn-hover" onClick={parseBatchPrompt} disabled={batchParsing}
                  style={{background: batchParsing ? "#E3E7F0" : "#7C5CFC", color: batchParsing ? "#667085" : "#FFFFFF", border:"none", borderRadius:"8px", padding:"11px 20px", fontWeight:"bold", cursor: batchParsing ? "wait" : "pointer"}}>
                  {batchParsing ? "⏳ Разбираю..." : "🔍 Разобрать"}
                </button>
              ) : (
                <div>
                  <div style={{background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"10px", padding:"16px", marginBottom:"16px"}}>
                    <div style={{fontWeight:"bold", fontSize:"15px", color:"#1D2939", marginBottom:"10px"}}>
                      Понял так: {batchPreview.total_directions} направлени{batchPreview.total_directions === 1 ? "е" : "й"}, ~{batchPreview.total_ads} объявлений, ~{batchPreview.total_banners} баннеров, ~{batchPreview.estimated_cost_rub}₽, ~{batchPreview.estimated_minutes} мин.
                    </div>
                    <div style={{display:"grid", gap:"6px"}}>
                      {batchPreview.directions.map((d: any, i: number) => (
                        <div key={i} style={{fontSize:"14px", color:"#344054"}}>
                          {i + 1}) <b>{d.direction}</b> — {d.count} объявл., {d.banner_count} баннер{d.banner_count === 1 ? "" : d.banner_count < 5 ? "а" : "ов"}, цены {d.price_from.toLocaleString("ru-RU")}–{d.price_to.toLocaleString("ru-RU")}₽
                        </div>
                      ))}
                    </div>
                  </div>

                  {batchPreview.warning && (
                    <div style={{background:"#FDEDEC", border:"1px solid #F04438", color:"#D64545", borderRadius:"8px", padding:"12px", marginBottom:"16px", fontSize:"14px", fontWeight:"bold"}}>
                      ⚠️ {batchPreview.warning}{batchConfirmedBig ? " — нажмите «Запустить всё» ещё раз для подтверждения." : ""}
                    </div>
                  )}

                  <div style={{display:"flex", gap:"12px"}}>
                    <button className="boris-btn-hover" onClick={launchBatch} disabled={batchLaunching}
                      style={{flex:1, background: batchLaunching ? "#E3E7F0" : "#12805C", color: batchLaunching ? "#667085" : "#FFFFFF", border:"none", borderRadius:"8px", padding:"11px", fontWeight:"bold", cursor: batchLaunching ? "wait" : "pointer"}}>
                      {batchLaunching ? "⏳ Запускаю..." : batchPreview.big_batch && !batchConfirmedBig ? "⚠️ Всё равно запустить всё" : "✅ Запустить всё"}
                    </button>
                    <button className="boris-btn-hover" onClick={() => setBatchPreview(null)} disabled={batchLaunching}
                      style={{background:"#FFFFFF", color:"#667085", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"11px 20px", cursor:"pointer"}}>
                      ✏️ Поправить
                    </button>
                    <button className="boris-btn-hover" onClick={() => { setBatchPreview(null); setBatchPromptText(""); setBatchConfirmedBig(false); }} disabled={batchLaunching}
                      style={{background:"#FFFFFF", color:"#F04438", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"11px 20px", cursor:"pointer"}}>
                      ❌ Отмена
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {trainData && (
        <div style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.45)", zIndex:1250, display:"flex", alignItems:"center", justifyContent:"center", padding:"20px"}} onClick={()=>setTrainData(null)}>
          <div onClick={e=>e.stopPropagation()} style={{background:"#fff", borderRadius:"16px", width:"660px", maxWidth:"100%", maxHeight:"85vh", overflowY:"auto", padding:"24px", boxShadow:"0 20px 60px rgba(16,24,40,0.3)"}}>
            <div style={{fontSize:"18px", fontWeight:800, color:"#1D2939", marginBottom:"4px"}}>🎓 РОП обучает ИИ-менеджера</div>
            <div style={{fontSize:"14px", color:"#667085", marginBottom:"14px"}}>На основе разбора: {trainData.based_on?.calls || 0} звонков и {trainData.based_on?.chats || 0} переписок{trainData.based_on?.avg_score ? `, средний балл ${trainData.based_on.avg_score}` : ""}.</div>
            <div style={{background:"#F9FAFB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"16px", fontSize:"15px", color:"#1D2939", whiteSpace:"pre-wrap", lineHeight:1.55, marginBottom:"14px"}}>{trainData.rules}</div>
            <div style={{background:"#FFFAEB", border:"1px solid #FEDF89", borderRadius:"10px", padding:"12px 14px", fontSize:"14px", color:"#B54708", marginBottom:"14px"}}>Правила добавятся отдельным блоком в скрипт ИИ-менеджера. Ваш текст не пострадает — при повторном обучении обновится только этот блок.</div>
            <div style={{display:"flex", gap:"10px"}}>
              <button onClick={()=>setTrainData(null)} style={{background:"#fff", color:"#475467", border:"1.5px solid #E3E7F0", borderRadius:"10px", padding:"11px 18px", fontWeight:600, cursor:"pointer", fontSize:"15px"}}>Отмена</button>
              <button onClick={()=>trainManager(true)} disabled={trainLoading} style={{flex:1, background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"11px 18px", fontWeight:700, cursor:"pointer", fontSize:"15px", opacity: trainLoading?0.6:1}}>{trainLoading ? "Записываю…" : "Применить к ИИ-менеджеру"}</button>
            </div>
          </div>
        </div>
      )}
      {pwStep !== "off" && (
        <div style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.45)", zIndex:1200, display:"flex", alignItems:"center", justifyContent:"center", padding:"20px"}} onClick={()=>setPwStep("off")}>
          <div onClick={e=>e.stopPropagation()} style={{background:"#fff", borderRadius:"16px", width:"640px", maxWidth:"100%", maxHeight:"85vh", overflowY:"auto", padding:"24px", boxShadow:"0 20px 60px rgba(16,24,40,0.3)"}}>
            <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"14px"}}>
              <div style={{fontSize:"18px", fontWeight:700, color:"#1D2939"}}>✨ Помощь в написании промта</div>
              <button onClick={()=>setPwStep("off")} style={{background:"none", border:"none", fontSize:"22px", color:"#98A2B3", cursor:"pointer", lineHeight:1}}>×</button>
            </div>
            {pwStep === "task" && (
              <div>
                <div style={{fontSize:"15px", color:"#667085", marginBottom:"14px"}}>Для какой задачи нужен промт?</div>
                <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                  {PW_TASKS.map((t:any)=>(
                    <button key={t.id} onClick={()=>pwPickTask(t)} className="boris-btn-hover" style={{textAlign:"left", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"14px 16px", cursor:"pointer", fontSize:"15px", fontWeight:600, color:"#1D2939"}}>
                      <span style={{marginRight:"10px"}}>{t.icon}</span>{t.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {pwStep === "questions" && pwTask && (
              <div>
                <div style={{fontSize:"15px", color:"#667085", marginBottom:"14px"}}>{pwTask.name} — ответьте на вопросы, Борис соберёт готовый текст</div>
                <div style={{display:"flex", flexDirection:"column", gap:"12px"}}>
                  {pwTask.qs.map((q:string,i:number)=>(
                    <div key={i}>
                      <div style={{fontSize:"15px", color:"#1D2939", marginBottom:"5px"}}>{i+1}. {q}</div>
                      <textarea value={pwAnswers[i]||""} onChange={e=>{const a=[...pwAnswers]; a[i]=e.target.value; setPwAnswers(a);}} rows={2}
                        style={{width:"100%", padding:"10px 12px", borderRadius:"10px", border:"1.5px solid #E3E7F0", fontSize:"15px", fontFamily:"inherit", resize:"vertical"}} />
                    </div>
                  ))}
                </div>
                <div style={{display:"flex", gap:"10px", marginTop:"16px"}}>
                  <button onClick={()=>setPwStep("task")} style={{background:"#fff", color:"#475467", border:"1.5px solid #E3E7F0", borderRadius:"10px", padding:"11px 18px", fontWeight:600, cursor:"pointer", fontSize:"15px"}}>Назад</button>
                  <button onClick={pwGenerate} style={{flex:1, background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"11px 18px", fontWeight:700, cursor:"pointer", fontSize:"15px"}}>Собрать промт →</button>
                </div>
              </div>
            )}
            {pwStep === "result" && (
              <div>
                {pwLoading && <div style={{fontSize:"15px", color:"#667085", padding:"20px 0"}}>Борис пишет промт…</div>}
                {!pwLoading && (
                  <div>
                    <div style={{background:"#F9FAFB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"16px", fontSize:"15px", color:"#1D2939", whiteSpace:"pre-wrap", lineHeight:1.55, marginBottom:"14px"}}>{pwResult}</div>
                    <div style={{background:"#EEF4FF", border:"1px solid #D0DEFF", borderRadius:"10px", padding:"12px 14px", fontSize:"14px", color:"#344054", marginBottom:"14px"}}>
                      Скопируйте этот текст и вставьте в <b>{pwTask ? pwTask.where : ""}</b>, затем нажмите «Сохранить». После сохранения Борис изучит инструкцию и станет применять её в работе.
                    </div>
                    <div style={{display:"flex", gap:"10px"}}>
                      <button onClick={()=>{navigator.clipboard.writeText(pwResult); alert("Промт скопирован — вставьте его в нужное поле и сохраните");}} style={{flex:1, background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"11px 18px", fontWeight:700, cursor:"pointer", fontSize:"15px"}}>Скопировать</button>
                      <button onClick={()=>setPwStep("questions")} style={{background:"#fff", color:"#475467", border:"1.5px solid #E3E7F0", borderRadius:"10px", padding:"11px 18px", fontWeight:600, cursor:"pointer", fontSize:"15px"}}>Изменить ответы</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
      {borisOpen && (
        <div style={{position:"fixed", bottom:"90px", right:"24px", width:"360px", maxWidth:"calc(100vw - 48px)", height:"480px", background:"#F6F7FB", border:"1px solid #2F6FED", borderRadius:"16px", display:"flex", flexDirection:"column", boxShadow:"0 8px 32px rgba(0,0,0,0.6)", zIndex:1000}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", padding:"16px", borderBottom:"1px solid #EEF2FA"}}>
            <span style={{fontWeight:"bold", color:"#2F6FED"}}>Борис — помощник</span>
            <button className="boris-btn-hover" onClick={() => setBorisOpen(false)} style={{background:"#FFFFFF", border:"none", color:"#2F6FED", fontSize:"15px", cursor:"pointer", lineHeight:1}}>×</button>
          </div>
          <div style={{flex:1, overflowY:"auto", padding:"16px", display:"flex", flexDirection:"column", gap:"12px"}}>
            {notifications.map((n: any, ni: number) => (
              <div key={ni} style={{background:"#E7EFFE", border:"1px solid #2F6FED", borderRadius:"10px", padding:"12px"}}>
                <div style={{color:"#2F6FED", fontSize:"15px", fontWeight:"bold", marginBottom:"6px"}}>📊 Борис вернулся с результатом</div>
                <div style={{color:"#1D2939", fontSize:"15px", lineHeight:1.4, marginBottom:"8px"}}>{n.text}</div>
                <button className="boris-btn-hover" onClick={() => markNotificationRead(n.ts)} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"11px 20px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>Прочитано</button>
              </div>
            ))}
            {borisHistory.length === 0 && (
              <div style={{color:"#667085", fontSize:"15px"}}>Привет! Я Борис 👋 Спроси меня о чём угодно: как пользоваться сервисом, куда нажать, что делать. Помогу разобраться.</div>
            )}
            {borisHistory.map((m: any, i: number) => (
              <div key={i} style={{alignSelf: m.role==="user" ? "flex-end" : "flex-start", maxWidth:"90%", background: m.role==="user" ? "#2F6FED" : "#FFFFFF", color:"#1D2939", padding:"10px 14px", borderRadius:"12px", fontSize:"23px", lineHeight:1.4, whiteSpace:"pre-wrap"}}>
                {m.content}
                {m.plan && (
                  <div style={{marginTop:"10px", paddingTop:"10px", borderTop:"1px solid #E3E7F0"}}>
                    {m.plan.steps.map((step: any, si: number) => (
                      <div key={si} style={{color:"#344054", fontSize:"15px", marginBottom:"6px"}}>{stepLabel(step)}</div>
                    ))}
                    {m.plan.clarifying_question && (
                      <div style={{color:"#F79009", fontSize:"15px", marginTop:"6px", marginBottom:"6px"}}>❓ {m.plan.clarifying_question}</div>
                    )}
                    {!m.planResolved && (
                      <div style={{display:"flex", gap:"8px", marginTop:"10px"}}>
                        <button className="boris-btn-hover" onClick={() => acceptPlan(m.plan, i)} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"11px 20px", fontWeight:"bold", cursor:"pointer", fontSize:"15px"}}>🤝 Бери в работу, Борис</button>
                        <button className="boris-btn-hover" onClick={() => rejectPlan(i)} style={{background:"#E3E7F0", color:"#344054", border:"none", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontSize:"15px"}}>✏️ Поправить</button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {borisLoading && <div style={{alignSelf:"flex-start", color:"#667085", fontSize:"15px"}}>Борис печатает…</div>}
          </div>
          {/* Плашка поддержки внутри помощника */}
          <div style={{padding:"10px 12px", borderTop:"1px solid #EEF2FA"}}>
            <button onClick={pwStart} style={{width:"100%", background:"#F4F3FF", color:"#5925DC", border:"1px solid #D9D6FE", borderRadius:"10px", padding:"9px", fontWeight:"bold", cursor:"pointer", fontSize:"14px", marginBottom:"8px"}}>✨ Помоги написать промт</button>
            {!supportOpen ? (
              <button onClick={()=>{setSupportOpen(true); setSupSent(false);}} style={{width:"100%", background:"#F0F4FF", color:"#2F6FED", border:"1px solid #D6E0FF", borderRadius:"10px", padding:"9px", fontWeight:"bold", cursor:"pointer", fontSize:"14px"}}>📮 Обратиться в поддержку</button>
            ) : supSent ? (
              <div style={{color:"#12805C", fontSize:"14px", padding:"6px 0"}}>
                ✅ Обращение{supTicket ? " №" + supTicket : ""} принято. Ответим в рабочие часы — ежедневно с 9:00 до 18:00 по Москве. Ответ придёт сюда, в кабинет, и в Telegram.
                <button onClick={()=>{setSupportOpen(false);}} style={{marginLeft:"8px", background:"#FFFFFF", border:"none", color:"#2F6FED", cursor:"pointer", textDecoration:"underline"}}>закрыть</button>
              </div>
            ) : (
              <div>
                <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"8px"}}>
                  <span style={{fontWeight:"bold", fontSize:"14px", color:"#344054"}}>📮 Поддержка</span>
                  <button onClick={()=>setSupportOpen(false)} style={{background:"#FFFFFF", border:"none", color:"#98A2B3", cursor:"pointer", fontSize:"16px"}}>✕</button>
                </div>
                <div style={{fontSize:"12px", color:"#667085", marginBottom:"8px", lineHeight:1.5}}>
                  Работаем ежедневно с 9:00 до 18:00 по Москве. Написать можно в любое время — ответим в рабочие часы.
                </div>
                <input placeholder="Ваше имя" value={supName} onChange={e=>setSupName(e.target.value)} style={{width:"100%", padding:"8px", marginBottom:"6px", border:"1px solid #E3E7F0", borderRadius:"7px", boxSizing:"border-box", fontSize:"14px"}} />
                <input placeholder="Телефон / Telegram / почта" value={supContact} onChange={e=>setSupContact(e.target.value)} style={{width:"100%", padding:"8px", marginBottom:"6px", border:"1px solid #E3E7F0", borderRadius:"7px", boxSizing:"border-box", fontSize:"14px"}} />
                <textarea placeholder="Ваше сообщение" value={supText} onChange={e=>setSupText(e.target.value)} rows={2} style={{width:"100%", padding:"8px", marginBottom:"8px", border:"1px solid #E3E7F0", borderRadius:"7px", boxSizing:"border-box", resize:"vertical", fontSize:"14px"}} />
                <button onClick={sendSupport} disabled={supSending} style={{width:"100%", padding:"9px", background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", fontWeight:"bold", cursor:"pointer", fontSize:"14px"}}>{supSending?"Отправка...":"Отправить"}</button>
                <button onClick={loadMyTickets} style={{width:"100%", marginTop:"6px", padding:"8px", background:"#FFFFFF", color:"#2F6FED", border:"1px solid #D6E0FF", borderRadius:"10px", cursor:"pointer", fontSize:"13px"}}>Мои обращения</button>
                {supMyOpen && (
                  <div style={{marginTop:"8px", maxHeight:"220px", overflowY:"auto"}}>
                    {supMy.length === 0 ? (
                      <div style={{color:"#98A2B3", fontSize:"13px"}}>Обращений пока не было.</div>
                    ) : supMy.map((t:any) => (
                      <div key={t["номер"]} style={{borderTop:"1px solid #EEF2FA", padding:"8px 0"}}>
                        <div style={{fontSize:"13px", color:"#344054"}}>
                          <b>№{t["номер"]}</b> · <span style={{color: t["статус"]==="решено" ? "#12805C" : t["статус"]==="в работе" ? "#E8850B" : "#667085"}}>{t["статус"]}</span>
                        </div>
                        <div style={{fontSize:"13px", color:"#667085", marginTop:"2px"}}>{t["текст"]}</div>
                        {(t["ответы"]||[]).map((rp:any, i:number) => (
                          <div key={i} style={{fontSize:"13px", marginTop:"4px", padding:"6px 8px", background: rp["кто"]==="поддержка" ? "#F0F4FF" : "#F8FAFF", borderRadius:"8px", color:"#344054"}}>
                            <b>{rp["кто"]}:</b> {rp["текст"]}
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <div style={{display:"flex", gap:"8px", padding:"12px", borderTop:"1px solid #EEF2FA"}}>
            <input value={borisInput} onChange={e => setBorisInput(e.target.value)} onKeyDown={e => { if(e.key==="Enter") askBoris(); }} placeholder="Спроси Бориса…"
              style={{flex:1, background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"23px", outline:"none"}} />
            <button className="boris-btn-hover" onClick={askBoris} style={{background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"10px 16px", fontWeight:"bold", cursor:"pointer"}}>➤</button>
          </div>
        </div>
      )}

      {!borisOpen && borisCurrentQ && borisBubbleOpen && (
        <div className="b-boris-bubble" style={{position:"fixed", bottom:"104px", right:"24px", width:"320px", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"16px", padding:"18px", zIndex:1001, boxShadow:"0 12px 32px rgba(16,24,40,.16)"}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"8px"}}>
            <span style={{fontSize:"12px", fontWeight:800, color:"#2F6FED", letterSpacing:".04em"}}>БОРИС СПРАШИВАЕТ</span>
            <button onClick={() => setBorisBubbleOff(true)} title="Больше не показывать подсказки" style={{background:"#FFFFFF", border:"none", color:"#2F6FED", cursor:"pointer", fontSize:"22px", lineHeight:1, padding:"0 4px"}}>×</button>
          </div>
          <div style={{color:"#344054", fontSize:"14px", lineHeight:1.55, marginBottom:"14px"}}>{borisCurrentQ.text}</div>
          <div style={{display:"flex", gap:"8px"}}>
            {borisCurrentQ.actions.map((a: any) => (
              <button key={a.label} className="boris-btn-hover" onClick={() => { setActiveTab(a.go); setBorisAsked((prev: any) => ({...prev, [borisCurrentQ.id]: true})); }}
                style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"8px 16px", fontSize:"14px", fontWeight:"bold", cursor:"pointer"}}>{a.label}</button>
            ))}
            <button className="boris-btn-hover" onClick={() => setBorisAsked((prev: any) => ({...prev, [borisCurrentQ.id]: true}))}
              style={{background:"#FFFFFF", color:"#667085", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"8px 16px", fontSize:"14px", cursor:"pointer"}}>Позже</button>
          </div>
          <div style={{position:"absolute", bottom:"-8px", right:"32px", width:"16px", height:"16px", background:"#FFFFFF", borderRight:"1px solid #E3E7F0", borderBottom:"1px solid #E3E7F0", transform:"rotate(45deg)"}} />
        </div>
      )}

      <button className={"b-boris-fab" + (borisMood === "alert" ? " b-boris-alert" : "")} onClick={() => { if (borisCurrentQ && !borisBubbleOpen) { setBorisBubbleOpen(true); } else { setBorisBubbleOpen(false); setBorisOpen(!borisOpen); } }} style={{position:"fixed", bottom:"24px", right:"24px", width:"68px", height:"68px", borderRadius:"50%", background:"linear-gradient(135deg, #4C8DFF, #2F6FED)", border:"none", cursor:"pointer", boxShadow:"0 6px 20px rgba(47,111,237,0.45)", zIndex:1000, display:"flex", alignItems:"center", justifyContent:"center"}}>
        {borisOpen ? <span style={{color:"#fff", fontSize:"26px"}}>×</span> : <Mascot size={44} interactive={true} mood={borisMood} />}
        {!borisOpen && notifications.length > 0 && (
          <span style={{position:"absolute", top:"-4px", right:"-4px", background:"#F04438", color:"#1D2939", borderRadius:"50%", width:"22px", height:"22px", fontSize:"15px", display:"flex", alignItems:"center", justifyContent:"center", fontWeight:"bold"}}>{notifications.length}</span>
        )}
      </button>

      {showPipelineForm && (
        <div style={{position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(0,0,0,0.8)", zIndex:2000, display:"flex", alignItems:"center", justifyContent:"center", padding:"20px"}}
          onClick={() => { if (!directionStarting) setShowPipelineForm(false); }}>
          <div onClick={(e:any) => e.stopPropagation()} style={{background:"#F6F7FB", border:"1px solid #12805C", borderRadius:"14px", padding:"28px", maxWidth:"520px", width:"100%", maxHeight:"85vh", overflowY:"auto"}}>
            <h3 style={{margin:"0 0 6px", color:"#12805C", fontSize:"18px"}}>🚀 Конвейер направления</h3>
            <p style={{margin:"0 0 20px", color:"#667085", fontSize:"15px"}}>Полный цикл по одному товарному направлению: тексты → категория → баннеры → проверка → публикация.</p>

            <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Направление</label>
            <input value={directionForm.direction} onChange={e => setDirectionForm({...directionForm, direction: e.target.value})}
              placeholder="Например, Гардеробные на заказ" disabled={directionStarting}
              style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px", marginBottom:"14px"}} />

            <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"14px", marginBottom:"14px"}}>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Объявлений</label>
                <input type="number" min={1} value={directionForm.count} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, count: Number(e.target.value)})}
                  style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px"}} />
              </div>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Баннеров</label>
                <input type="number" min={1} value={directionForm.bannerCount} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, bannerCount: Number(e.target.value)})}
                  style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px"}} />
              </div>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Цена от</label>
                <input type="number" min={0} value={directionForm.priceFrom} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, priceFrom: Number(e.target.value)})}
                  style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px"}} />
              </div>
              <div>
                <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Цена до</label>
                <input type="number" min={0} value={directionForm.priceTo} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, priceTo: Number(e.target.value)})}
                  style={{width:"100%", boxSizing:"border-box", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px"}} />
              </div>
            </div>

            <div style={{marginBottom:"10px", color:"#667085", fontSize:"15px"}}>Уникализировать:</div>
            <div style={{display:"flex", gap:"20px", marginBottom:"16px"}}>
              <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#1D2939"}}>
                <input type="checkbox" checked={directionForm.uniquifyTitles} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, uniquifyTitles: e.target.checked})} /> заголовки
              </label>
              <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#1D2939"}}>
                <input type="checkbox" checked={directionForm.uniquifyDescriptions} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, uniquifyDescriptions: e.target.checked})} /> описания
              </label>
            </div>

            <div style={{marginBottom:"10px", color:"#667085", fontSize:"15px"}}>После готовности:</div>
            <div style={{display:"flex", gap:"20px", marginBottom:"20px"}}>
              <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#1D2939"}}>
                <input type="checkbox" checked={directionForm.autoCheck} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, autoCheck: e.target.checked})} /> проверить партию
              </label>
              <label style={{display:"flex", alignItems:"center", gap:"6px", fontSize:"15px", color:"#1D2939"}}>
                <input type="checkbox" checked={directionForm.autoPublish} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, autoPublish: e.target.checked})} /> опубликовать автоматически
              </label>
            </div>

            <div style={{display:"flex", gap:"12px"}}>
              <button className="boris-btn-hover" onClick={runDirectionPipeline} disabled={directionStarting}
                style={{flex:1, background: directionStarting ? "#E3E7F0" : "#12805C", color: directionStarting ? "#667085" : "#FFFFFF", border:"none", borderRadius:"8px", padding:"12px", fontWeight:"bold", cursor: directionStarting ? "wait" : "pointer"}}>
                {directionStarting ? "⏳ Запускаю..." : "🚀 Запустить конвейер"}
              </button>
              <button className="boris-btn-hover" onClick={() => setShowPipelineForm(false)} disabled={directionStarting}
                style={{background:"#FFFFFF", color:"#667085", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"12px 20px", cursor: directionStarting ? "default" : "pointer"}}>
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}

      {editingItemId && (
        <div style={{position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(0,0,0,0.8)", zIndex:2000, display:"flex", alignItems:"center", justifyContent:"center", padding:"20px"}} onClick={() => setEditingItemId(null)}>
          <div onClick={(e:any) => e.stopPropagation()} style={{background:"#F6F7FB", border:"1px solid #2F6FED", borderRadius:"14px", padding:"28px", maxWidth:"600px", width:"100%", maxHeight:"85vh", overflowY:"auto"}}>
            <h3 style={{margin:"0 0 16px", color:"#1D2939", fontSize:"18px"}}>✏️ Редактирование объявления</h3>
            <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Заголовок</label>
            <input value={editItemForm.title} onChange={e => setEditItemForm({...editItemForm, title: e.target.value})}
              style={{width:"100%", boxSizing:"border-box", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"23px", marginBottom:"12px"}} />
            <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Цена</label>
            <input type="number" value={editItemForm.price} onChange={e => setEditItemForm({...editItemForm, price: Number(e.target.value)})}
              style={{width:"160px", boxSizing:"border-box", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"23px", marginBottom:"12px"}} />
            <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Описание</label>
            <textarea value={editItemForm.description} onChange={e => setEditItemForm({...editItemForm, description: e.target.value})} rows={10}
              style={{width:"100%", boxSizing:"border-box", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#344054", fontSize:"15px", lineHeight:1.5, fontFamily:"inherit", resize:"vertical", marginBottom:"16px"}} />
            <div style={{display:"flex", gap:"12px"}}>
              <button className="boris-btn-hover" onClick={saveEditItem} disabled={editItemSaving} style={{flex:1, background: editItemSaving ? "#E3E7F0" : "#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px", fontWeight:"bold", cursor: editItemSaving ? "wait" : "pointer"}}>
                {editItemSaving ? "Сохраняю..." : "✅ Сохранить"}
              </button>
              <button className="boris-btn-hover" onClick={() => setEditingItemId(null)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"12px", cursor:"pointer"}}>Отмена</button>
            </div>
          </div>
        </div>
      )}

      {aiPreview && (
        <div style={{position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(0,0,0,0.8)", zIndex:2000, display:"flex", alignItems:"center", justifyContent:"center", padding:"20px"}} onClick={() => setAiPreview(null)}>
          <div onClick={(e:any) => e.stopPropagation()} style={{background:"#F6F7FB", border:"1px solid #7C5CFC", borderRadius:"14px", padding:"28px", maxWidth:"600px", width:"100%", maxHeight:"85vh", overflowY:"auto"}}>
            <h3 style={{margin:"0 0 16px", color:"#1D2939", fontSize:"18px"}}>Результат генерации ИИ</h3>
            {aiPreview.images[0] && <img src={aiPreview.images[0]} style={{width:"100%", maxHeight:"220px", objectFit:"cover", borderRadius:"8px", marginBottom:"14px"}} />}
            <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"4px"}}>Заголовок</label>
            <textarea value={aiPreview.title} onChange={e => updateAiPreview("title", e.target.value)} rows={2}
              style={{width:"100%", boxSizing:"border-box", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"23px", fontWeight:"bold", fontFamily:"inherit", resize:"vertical", marginBottom:"12px"}} />
            <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"4px"}}>
              <label style={{color:"#667085", fontSize:"15px"}}>Описание</label>
              <button className="boris-btn-hover" onClick={applyBoldPreview} style={{background:"#FFFFFF", color:"#fff", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"3px 10px", cursor:"pointer", fontWeight:"bold", fontSize:"15px"}}>Ж</button>
            </div>
            <textarea id="ai-preview-desc" value={aiPreview.description} onChange={e => updateAiPreview("description", e.target.value)} rows={10}
              style={{width:"100%", boxSizing:"border-box", background:"#F6F7FB", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#344054", fontSize:"15px", lineHeight:1.5, fontFamily:"inherit", resize:"vertical", marginBottom:"8px"}} />
            <div style={{color:"#8A93A6", fontSize:"15px", marginBottom:"8px"}}>Предпросмотр:</div>
            <div style={{fontSize:"15px", color:"#344054", lineHeight:1.5, background:"#F6F7FB", padding:"14px", borderRadius:"8px", border:"1px dashed #E3E7F0", whiteSpace:"pre-wrap", wordBreak:"break-word", overflowWrap:"break-word"}} dangerouslySetInnerHTML={{__html: aiPreview.description}} />
            <div style={{display:"flex", gap:"12px", marginTop:"20px"}}>
              <button className="boris-btn-hover" onClick={applyAiPreview} style={{flex:1, background:"#2F6FED", color:"#F6F7FB", border:"none", borderRadius:"10px", padding:"12px", fontWeight:"bold", cursor:"pointer"}}>✅ Применить к карточке</button>
              <button className="boris-btn-hover" onClick={() => setAiPreview(null)} style={{flex:1, background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"12px", cursor:"pointer"}}>Отмена</button>
            </div>
          </div>
        </div>
      )}
      </>
      )}
      </div>
      <ConfirmModal open={notifyMsg !== null} kind="info" title={notifyMsg || ""}
        confirmText="OK" cancelText="Закрыть"
        onConfirm={() => setNotifyMsg(null)} onCancel={() => setNotifyMsg(null)} />
      <ConfirmModal open={autopilotModalOpen} kind="danger" title="Включить автопилот ставок?"
        message="Борис будет САМ менять ставки каждый час в рамках предохранителей (потолок ставки, шаг не более 20%, суточный лимит бюджета). Это реальные траты на вашем аккаунте."
        confirmText="Включить" cancelText="Отмена"
        onConfirm={doToggleAutopilot} onCancel={() => setAutopilotModalOpen(false)} />
      <ConfirmModal open={payModalAccount !== null} kind="confirm" title="💳 Отметить оплату клиента"
        confirmText="Сохранить" cancelText="Отмена"
        onConfirm={savePayment} onCancel={() => setPayModalAccount(null)}>
        <div style={{display:"flex", flexDirection:"column", gap:"12px"}}>
          <div>
            <div style={{fontSize:"13px", color:"#667085", marginBottom:"4px"}}>Сумма оплаты, ₽</div>
            <input type="number" value={payAmount} onChange={e => setPayAmount(e.target.value)} placeholder="7000" style={{width:"100%", padding:"9px 12px", border:"1px solid #D0D5DD", borderRadius:"8px", fontSize:"15px"}} />
          </div>
          <div>
            <div style={{fontSize:"13px", color:"#667085", marginBottom:"4px"}}>Период, дней</div>
            <input type="number" value={payPeriod} onChange={e => setPayPeriod(e.target.value)} placeholder="30" style={{width:"100%", padding:"9px 12px", border:"1px solid #D0D5DD", borderRadius:"8px", fontSize:"15px"}} />
          </div>
          <div style={{fontSize:"12px", color:"#8A93A6"}}>Напоминания: 🟡 за 10 дней · 🔴 за 4 дня до конца.</div>
        </div>
      </ConfirmModal>
            {borisModal.open && (
                          <div style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.5)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:2000, padding:"20px"}} onClick={closeBorisModal}>
                <div style={{background:"#FFFFFF", borderRadius:"16px", padding:"28px", maxWidth:"460px", width:"100%", boxShadow:"0 12px 40px rgba(16,24,40,0.2)"}} onClick={e => e.stopPropagation()}>
                  <div style={{fontSize:"18px", fontWeight:"bold", color:"#1D2939", marginBottom:"12px"}}>{borisModal.title}</div>
                  <div style={{fontSize:"15px", color:"#475467", lineHeight:"1.5", marginBottom:"24px", whiteSpace:"pre-line"}}>{borisModal.message}</div>
                  <div style={{display:"flex", gap:"10px", justifyContent:"flex-end"}}>
                    {borisModal.onConfirm && (
                      <button className="boris-btn-hover" onClick={closeBorisModal} style={{background:"#FFFFFF", color:"#2F6FED", border:"1.5px solid #2F6FED", borderRadius:"10px", padding:"10px 20px", cursor:"pointer", fontSize:"15px", fontWeight:"600"}}>Отмена</button>
                    )}
                    <button className="boris-btn-hover" onClick={() => { const cb = borisModal.onConfirm; closeBorisModal(); if (cb) cb(); }} style={{background:"#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"10px", padding:"10px 20px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>{borisModal.confirmText}</button>
                  </div>
                </div>
              </div>
            )}

      {kbModalOpen && (
        <div onClick={() => setKbModalOpen(false)} style={{position:"fixed", inset:0, background:"rgba(15,17,21,0.55)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:9999, padding:"16px"}}>
          <div onClick={e => e.stopPropagation()} style={{width:"540px", maxWidth:"100%", maxHeight:"90vh", overflowY:"auto", background:"#FFFFFF", borderRadius:"20px", border:"0.5px solid #E3E7F0", boxShadow:"0 20px 60px rgba(0,0,0,0.3)"}}>
            <div style={{display:"flex", alignItems:"center", gap:"14px", padding:"24px 26px 20px", borderBottom:"0.5px solid #EEF1F6"}}>
              <div style={{width:"46px", height:"46px", borderRadius:"14px", background:"#EAF3FF", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"24px"}}>🧠</div>
              <div style={{flex:1}}>
                <div style={{fontSize:"18px", fontWeight:600, color:"#14161A"}}>Обучить Бориса</div>
                <div style={{fontSize:"13px", color:"#667085", marginTop:"2px"}}>{kbModalAccount==="global" ? "Общие знания — для всех клиентов" : "Знания об этом клиенте"}</div>
              </div>
              <button onClick={() => setKbModalOpen(false)} style={{width:"34px", height:"34px", borderRadius:"10px", border:"none", background:"#F4F6FA", cursor:"pointer", fontSize:"18px", color:"#667085"}}>✕</button>
            </div>
            <div style={{padding:"22px 26px"}}>
              <div style={{display:"flex", justifyContent:"space-between", marginBottom:"10px"}}>
                <label style={{fontSize:"15px", color:"#667085"}}>Текст знания</label>
                <span style={{fontSize:"12px", color:"#98A2B3"}}>{kbText.length} символов</span>
              </div>
              <textarea value={kbText} onChange={e => setKbText(e.target.value)} placeholder="Например: доставка по вашему городу за 1 день. Или вставь текст из документа — Борис запомнит." style={{width:"100%", minHeight:"110px", resize:"vertical", fontSize:"14px", lineHeight:1.6, padding:"14px 16px", boxSizing:"border-box", borderRadius:"12px", border:"1px solid #E3E7F0", outline:"none", fontFamily:"inherit"}} />
              <label
                onDragOver={e => { e.preventDefault(); setKbDragOver(true); }}
                onDragLeave={() => setKbDragOver(false)}
                onDrop={e => { e.preventDefault(); setKbDragOver(false); Array.from(e.dataTransfer.files).forEach(f => kbUploadFile(f as File)); }}
                style={{marginTop:"18px", display:"block", border:`2px dashed ${kbDragOver ? "#2F6FED" : "#D3D9E4"}`, borderRadius:"14px", padding:"26px 20px", textAlign:"center", background: kbDragOver ? "#EAF3FF" : "#F9FAFC", cursor:"pointer", transition:"all 0.2s"}}>
                <input type="file" multiple accept=".pdf,.docx,.txt,.md,.csv" onChange={e => { Array.from(e.target.files||[]).forEach(f => kbUploadFile(f as File)); e.target.value=""; }} style={{display:"none"}} />
                <div style={{fontSize:"28px"}}>📎</div>
                <div style={{fontSize:"14px", color:"#14161A", marginTop:"8px", fontWeight:600}}>Перетащи файл или нажми</div>
                <div style={{fontSize:"12px", color:"#98A2B3", marginTop:"4px"}}>PDF, Word, txt — Борис прочитает текст</div>
              </label>
              {kbFiles.length > 0 && (
                <div style={{marginTop:"14px", display:"flex", flexDirection:"column", gap:"10px"}}>
                  {kbFiles.map((f: any) => (
                    <div key={f.id} style={{display:"flex", alignItems:"center", gap:"12px", padding:"12px 14px", background:"#F9FAFC", borderRadius:"12px", border:"0.5px solid #EEF1F6"}}>
                      <span style={{fontSize:"20px"}}>{f.status==="done" ? "✅" : f.status==="error" ? "❌" : "⏳"}</span>
                      <div style={{flex:1, minWidth:0}}>
                        <div style={{fontSize:"13px", color:"#14161A", fontWeight:600, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>{f.name}</div>
                        <div style={{fontSize:"12px", color: f.status==="done" ? "#12805C" : f.status==="error" ? "#C4320A" : "#667085", marginTop:"2px"}}>
                          {f.status==="done" ? `текст извлечён · ${f.chars||0} символов` : f.status==="error" ? "не удалось прочитать" : "читаю…"}
                        </div>
                      </div>
                      <button onClick={() => setKbFiles((prev: any[]) => prev.filter(x => x.id!==f.id))} style={{width:"28px", height:"28px", border:"none", background:"transparent", cursor:"pointer", color:"#98A2B3", fontSize:"16px"}}>✕</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div style={{display:"flex", gap:"12px", padding:"18px 26px", borderTop:"0.5px solid #EEF1F6", background:"#F9FAFC"}}>
              <button onClick={() => setKbModalOpen(false)} style={{flex:1, padding:"12px", borderRadius:"10px", border:"1.5px solid #2F6FED", background:"#FFFFFF", cursor:"pointer", fontSize:"14px", color:"#2F6FED"}}>Отмена</button>
              <button onClick={kbSubmit} disabled={kbSaving} style={{flex:2, padding:"12px", borderRadius:"10px", border:"none", background:"#2F6FED", color:"#FFFFFF", cursor:"pointer", fontSize:"14px", fontWeight:600}}>{kbSaving ? "Сохраняю…" : "Обучить Бориса"}</button>
            </div>
          </div>
        </div>
      )}
    </div>


  );
}