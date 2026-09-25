"use client";
import { Card as BorisCard } from "@/app/ui";

import { Button } from "@/app/ui";

import { useBackTarget } from "../lib/useBackTarget";
import { fetchOnboardingStatus } from "../lib/onboardingClient";
import { useState, useEffect, useRef } from "react";
import { DashboardSidebarAdapter as Sidebar } from "../components/shell/DashboardSidebarAdapter";
import GlobalLogoutButton from "../components/shell/GlobalLogoutButton";
import { Mascot } from '../components/Mascot';
import { useRouter } from "next/navigation";
import { ConfirmModal } from "../components/ConfirmModal";
import { readCampaignDraft, writeCampaignDraft, clearCampaignDraft } from "@/app/lib/campaignDraft";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

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
    <div className="boris-f2-cad980f4b707">
      <label className="boris-f2-e935c0b4b558">
        <input type="checkbox" checked={agreed} onChange={e => onCheck(e.target.checked)}  className="boris-f2-d99a80608a47"/>
        <span>Я согласен с <span onClick={e => { e.preventDefault(); loadTerms(); }} className="boris-f2-a64151a01fa8">правилами использования</span> и офертой</span>
      </label>
      <div className="boris-f2-87db2275805b">
        {children}
        {!agreed && (
          <div onClick={() => alert("Поставьте галочку согласия с правилами, чтобы продолжить")}  className="boris-f2-b87242fa6763"/>
        )}
      </div>
      {showText && terms && (
        <div onClick={() => setShowText(false)} className="boris-f2-e5cf81d91efd">
          <div onClick={e => e.stopPropagation()} className="boris-f2-bb0da04a8206">
            <div className="boris-f2-a42388f688c8">
              <div className="boris-f2-36c5f1945b9b">{terms.title}</div>
              <Button onClick={() => setShowText(false)} className="boris-f2-5b99a9342ea6">✕</Button>
            </div>
            <div className="boris-f2-93fb4cf4cb86">Редакция от {terms.version}</div>
            <div dangerouslySetInnerHTML={{ __html: terms.html }}  className="boris-f2-92ee211bca28"/>
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
      className="boris-robokassa-btn boris-f2-6dd65d38c061"

    >
      {busy ? "Открываю оплату..." : "Оплатить"} <span className="boris-f2-be8ffda67663">→</span>
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
  const [currentAccount, setCurrentAccount] = useState("");
  const [generatorAccount, setGeneratorAccount] = useState("");
  const [generatorStorageError, setGeneratorStorageError] = useState("");
  const activeGeneratorAccount = useRef("");
  const generatorSaveQueue = useRef<Promise<void>>(Promise.resolve());
  useEffect(() => { activeGeneratorAccount.current = currentAccount; }, [currentAccount]);
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
  const [accountsError, setAccountsError] = useState("");
  // Кнопка «В кабинет» теперь возвращает туда, откуда пришли,
  // а не всегда на Главную. Подпись остаётся прежней.
  const back = useBackTarget("/dashboard/home", "← В кабинет");
  const [prefillDone, setPrefillDone] = useState(false);
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
        "Ваш аккаунт создан, пробный период — 4 дня. Начните с подходящего сценария — БОРИС покажет, что делать шаг за шагом. Подключить Avito можно в любой момент кнопкой «Подключить аккаунт»."
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
          // P0.2: автопоказ визарда снят. Задачу выбирает каталог сценариев,
          // подключение Avito открывается кнопкой «Подключить аккаунт».
        } else {
          setAccountsError(data.message || "Не удалось загрузить список аккаунтов");
        }
      })
      .catch(() => { setAccountsError("Не удалось связаться с сервером"); });
  }, []);
  // P0.2: форма подключения Avito открывается только по явному сигналу.
  // Параметр ставит кнопка с Главной; после открытия убираем его из адреса,
  // чтобы обновление страницы не открывало форму снова.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.location.search.indexOf("connect=avito") === -1) return;
    setShowAddForm(true);
    setStep(1);
    window.history.replaceState({}, "", "/dashboard");
  }, []);
  const [newAccount, setNewAccount] = useState({
    name: "", login: "", password: "", comment: "", client_id: "", client_secret: "",
    companyWebsite: "", companyDescription: "", companyNiche: "", companyTone: "Дружелюбный", companyAdvantages: ""
  });
  // P0.2: один раз при явном открытии формы подставляем то, что клиент уже
  // рассказал в мастере. Только в пустые поля — поверх набранного не пишем.
  useEffect(() => {
    if (!showAddForm || prefillDone) return;
    setPrefillDone(true);
    fetchOnboardingStatus()
      .then((st: any) => {
        const f: any = (st && st.form_data) || {};
        setNewAccount((p: any) => ({
          ...p,
          name: p.name || f.company_name || "",
          companyNiche: p.companyNiche || f.company_niche || "",
          companyWebsite: p.companyWebsite || f.website || "",
        }));
      })
      .catch(() => {});
  }, [showAddForm, prefillDone]);
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
  const [kpiGoalAuto, setKpiGoalAuto] = useState<boolean | null>(null);
  const [kpiLeadTemp, setKpiLeadTemp] = useState("любые");
  const [kpiSaved, setKpiSaved] = useState(false);
  const [republishMinViews, setRepublishMinViews] = useState(10);
  const [republishZeroDays, setRepublishZeroDays] = useState(7);
  const [republishEnabled, setRepublishEnabled] = useState(true);
  const [republishSettingsSaved, setRepublishSettingsSaved] = useState(false);
  const [kpiCheckResult, setKpiCheckResult] = useState<any>(null);
  const [kpiActionMemory, setKpiActionMemory] = useState<any[]>([]);
  const [kpiReadiness, setKpiReadiness] = useState<any>(null);
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
  const bannerGenerationKeyRef = useRef("");
  const draftBatchBannerKeyRef = useRef("");
  const pipelineBatchBannerKeyRef = useRef("");
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
    if (!bannerGenerationKeyRef.current) {
      bannerGenerationKeyRef.current = globalThis.crypto?.randomUUID?.() || `banner-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    let res: Response | null = null;
    try {
      const useOwnPhotoFlow = bannerUseOwnPhoto && (bannerFormat === "infographic" || bannerFormat === "custom");
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
        // Avito visual safety contract: never let an image model render visible
        // Russian copy. The safe endpoint generates the photographic layer and
        // composes large mobile-readable text locally. One explicit click = one
        // paid banner, preventing accidental multi-generation and legacy full_ai.
        const body: any = {
          account_id: currentAccount,
          description: bannerPrompt.trim(),
          title: bannerExactText.trim(),
          subtitle: "",
          price_text: "",
          photo_url: "",
          accent_color: bannerAccentColor,
          idempotency_key: bannerGenerationKeyRef.current,
        };

        res = await apiFetch("/api/banners/v2/create-one", {
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
      // Keep the same key only for an ambiguous network failure. If BORIS
      // answered, the explicit next click is a new generation request.
      if (res) bannerGenerationKeyRef.current = "";
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
    if (!prefsLoaded || !currentAccount || generatorAccount !== currentAccount) return;
    const t = setTimeout(() => {
      apiFetch("/api/storage/save", { method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, key: "gen_sample", value: genSample }) }).catch(() => {});
    }, 800);
    return () => clearTimeout(t);
  }, [genSample, prefsLoaded, generatorAccount, currentAccount]);
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
  // Account-scoped hydration: a late response must never enter another tenant's form.
  useEffect(() => {
    const acc = currentAccount;
    let cancelled = false;
    setGeneratorAccount(""); setTemplatesLoaded(false); setPrefsLoaded(false);
    setGenAds([]); setGenSample(""); setTextSamples([]); setGenAdsHistory([]);
    setTemplates([defaultTemplate]); setSelectedTemplate(null);
    setFeedImages({}); setAdPhotos({}); setAdTextPhotos({}); setPhotosPerAd(1);
    setGenAddress(""); setGenTopic(""); setGenExtra(""); setGenPriceFrom(0); setGenPriceTo(0);
    setGeneratorStorageError("");
    if (!acc) return () => { cancelled = true; };
    const read = async (key: string) => {
      const response = await apiFetch(`/api/storage/load?account_id=${encodeURIComponent(acc)}&key=${key}`);
      const data = await response.json();
      if (!response.ok || data.status === "error") throw new Error("Не удалось загрузить сохранённые данные. Обновите страницу, чтобы повторить.");
      return data.value;
    };
    Promise.all(["templates","gen_ads","gen_sample","text_samples","use_banner","feed_prefs","gen_ads_history"].map(read))
      .then(([tpl, ads, sample, samples, banner, prefs, history]) => {
        if (cancelled) return;
        const recovered = readCampaignDraft<any[]>("generated:" + acc);
        setTemplates(Array.isArray(tpl) ? tpl : [defaultTemplate]);
        setGenAds(Array.isArray(recovered) ? recovered : Array.isArray(ads) ? ads : []);
        setGenSample(typeof sample === "string" ? sample : "");
        setTextSamples(Array.isArray(samples) ? samples : []);
        setUseBanner(typeof banner === "boolean" ? banner : true);
        setFeedImages(prefs?.feedImages && typeof prefs.feedImages === "object" ? prefs.feedImages : {});
        setPhotosPerAd(typeof prefs?.photosPerAd === "number" ? prefs.photosPerAd : 1);
        setGenAddress(typeof prefs?.city === "string" ? prefs.city : "");
        setGenAdsHistory(Array.isArray(history) ? history : []);
        setTemplatesLoaded(true); setPrefsLoaded(true); setGeneratorAccount(acc);
      }).catch((e: any) => { if (!cancelled) setGeneratorStorageError(e.message); });
    return () => { cancelled = true; };
  }, [currentAccount]);

  useEffect(() => {
    if (!templatesLoaded || !currentAccount || generatorAccount !== currentAccount) return;
    if (templates.length === 0) return;
    apiFetch("/api/storage/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ account_id: currentAccount, key: "templates", value: templates })
    }).catch(() => {});
  }, [templates, templatesLoaded, generatorAccount, currentAccount]);

  useEffect(() => {
    if (!currentAccount || generatorAccount !== currentAccount) return;
    const acc = currentAccount;
    const localSaved = writeCampaignDraft("generated:" + acc, genAds);
    if (!localSaved) setGeneratorStorageError("Браузер не смог сохранить копию. Не закрывайте страницу до сохранения в BORIS.");
    const timer = setTimeout(() => {
      generatorSaveQueue.current = generatorSaveQueue.current.catch(() => {}).then(async () => {
      try {
        const response = await apiFetch("/api/storage/save", {
          method: "POST", headers: {"Content-Type":"application/json"},
          body: JSON.stringify({ account_id: acc, key: "gen_ads", value: genAds })
        });
        const data = await response.json();
        if (!response.ok || data.status === "error") throw new Error("Сохранение в BORIS не выполнено. Локальная копия сохранена; повторите после восстановления связи.");
        if (JSON.stringify(readCampaignDraft("generated:" + acc)) === JSON.stringify(genAds)) clearCampaignDraft("generated:" + acc);
        if (activeGeneratorAccount.current === acc && localSaved) setGeneratorStorageError("");
      } catch (e: any) {
        if (activeGeneratorAccount.current === acc) setGeneratorStorageError(localSaved ? e.message : "Не удалось сохранить изменения. Не закрывайте страницу.");
      }
      });
    }, 800);
    return () => clearTimeout(timer);
  }, [genAds, generatorAccount, currentAccount]);
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
  useEffect(() => { setKpiActionMemory([]); setKpiReadiness(null); setKpiCheckResult(null); }, [currentAccount]);
  // Гидрация из localStorage только на клиенте (после mount) - иначе SSR mismatch #418
  useEffect(() => {
    if (typeof window === "undefined") return;
    const v = localStorage.getItem("boris_view"); if (v) setView(v);
    // boris_tab_from_url: адрес важнее сохранённой вкладки. Меню других разделов
    // ведёт на /dashboard?tab=<ключ>, и без этого клик по «Статистике» открывал
    // ту вкладку, что осталась в браузере с прошлого раза.
    const _urlTab = new URLSearchParams(window.location.search).get("tab");
    if (_urlTab) setActiveTab(_urlTab);
    else { const t = localStorage.getItem("boris_activeTab"); if (t) setActiveTab(t); }
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
  const [kbFileBusy, setKbFileBusy] = useState(false);
  const [kbFileMsg, setKbFileMsg] = useState("");

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
    <div className="boris-f2-b53027ebb268">
      <div style={{position:"absolute", top:"-24px", right:"-24px", width:"70px", height:"70px", borderRadius:"50%", background:g, opacity:0.1}} />
      <div style={{fontSize:"22px", fontWeight:800, color: v>0 ? "var(--boris-text)" : "#B42318", letterSpacing:"-0.02em"}}>{v} <span className="boris-f2-f401e183907f">из {all} {u}</span></div>
      <div className="boris-f2-b703107f9e84">{t}</div>
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
    if (!prefsLoaded || !currentAccount || generatorAccount !== currentAccount) return;
    const t = setTimeout(() => {
      apiFetch("/api/storage/save", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, key: "feed_prefs",
          value: { feedImages, photosPerAd, city: genAddress } })
      }).catch(() => {});
    }, 800);
    return () => clearTimeout(t);
  }, [feedImages, photosPerAd, genAddress, prefsLoaded, generatorAccount, currentAccount]);
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
    if (generatorAccount !== currentAccount) { alert("Дождитесь загрузки данных аккаунта."); return; }
    const generationAccount = currentAccount;
    setGenLoading(true);
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
      if (activeGeneratorAccount.current !== generationAccount) { setGenLoading(false); return; }
      if (res.ok && data.status === "ok") {
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
  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem("boris_activeTab", activeTab);
    // Держим адрес в согласии с открытой вкладкой: ссылку можно отправить
    // коллеге или сохранить, и она откроет то же самое.
    try {
      const _u = new URL(window.location.href);
      if (_u.searchParams.get("tab") !== activeTab) {
        _u.searchParams.set("tab", activeTab);
        window.history.replaceState({}, "", _u.toString());
      }
    } catch {}
  }, [activeTab]);
  useEffect(() => { if (typeof window !== "undefined") localStorage.setItem("boris_currentAccount", currentAccount); }, [currentAccount]);

  const kbOpenModal = (accId: string) => { setKbModalAccount(accId); setKbText(""); setKbFiles([]); setKbModalOpen(true); };

  const kbUploadFile = async (file: File) => {
    const id = Date.now() + "_" + file.name;
    setKbFiles((prev: any[]) => [...prev, {id, name: file.name, status: "processing"}]);
    try {
      const fd = new FormData(); fd.append("file", file);
      const r = await apiFetch(`/api/memory/upload_file?account_id=${encodeURIComponent(kbModalAccount)}&preview=true`, {method:"POST", body: fd});
      const d = await r.json();
      if (d && d.status === "ok" && d.facts && d.facts.length) {
        setKbFiles((prev: any[]) => prev.map(f => f.id===id ? {...f, status:"done", raw:file, facts:d.facts, keep:d.facts.map((x:any)=>x.i)} : f));
      } else {
        setKbFiles((prev: any[]) => prev.map(f => f.id===id ? {...f, status:"error", err:(d && d.message) || ""} : f));
      }
    } catch { setKbFiles((prev: any[]) => prev.map(f => f.id===id ? {...f, status:"error"} : f)); }
  };

  const kbToggleFact = (fileId: string, i: number) => {
    setKbFiles((prev: any[]) => prev.map(f => f.id!==fileId ? f
      : {...f, keep: (f.keep||[]).includes(i) ? (f.keep||[]).filter((x:number)=>x!==i) : [...(f.keep||[]), i]}));
  };

  const kbSubmit = async () => {
    const kbReady = kbFiles.filter((f: any) => f.status==="done" && f.raw && (f.keep||[]).length>0);
    if (!kbText.trim() && kbReady.length===0) { setKbModalOpen(false); return; }
    setKbSaving(true);
    try {
      if (kbText.trim()) {
        await apiFetch("/api/chat/knowledge/add", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({text: kbText, account_id: kbModalAccount})});
      }
      for (const kf of kbReady) {
        const kfd = new FormData(); kfd.append("file", kf.raw);
        await apiFetch(`/api/memory/upload_file?account_id=${encodeURIComponent(kbModalAccount)}&keep=${(kf.keep||[]).join(",")}`, {method:"POST", body: kfd});
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
    if (v === null || v === undefined) return <span className="boris-f2-6774eea0e0db">—</span>;
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
  const [advHardMaxBid, setAdvHardMaxBid] = useState<string>("");
  // Защита от межаккаунтной гонки: старый HTTP-ответ не имеет права
  // заполнить форму уже выбранного другого клиента его бюджетом/ставками.
  const advisorLoadSeq = useRef(0);

  const loadAdvisor = () => {
    const aid = currentAccount;
    const seq = ++advisorLoadSeq.current;
    setAdvisorLoading(true);
    setAdvisorData(null);
    // Сбрасываем поля немедленно при смене клиента. Нулевой бюджет — это
    // авторитетное значение, а не повод оставить цифры предыдущего аккаунта.
    setAdvMaxCpl("");
    setAdvDailyLimit("");
    setAdvHardMaxBid("");
    setAdvAutopilot(false);
    apiFetch(`/api/avito/kpi_settings?account_id=${aid}`)
      .then(r => r.json())
      .then(d => {
        if (seq !== advisorLoadSeq.current) return;
        const s = d.settings;
        if (s) {
          setAdvMaxCpl(s.max_cost_per_lead_rub != null ? String(s.max_cost_per_lead_rub) : "");
          setAdvDailyLimit(s.daily_budget_limit_rub != null ? String(s.daily_budget_limit_rub) : "");
          setAdvHardMaxBid(s.hard_max_bid_rub != null ? String(s.hard_max_bid_rub) : "");
          setAdvAutopilot(!!s.bid_autopilot);
        }
      })
      .catch(() => {});
    apiFetch(`/api/cpx_advisor/last?account_id=${aid}`)
      .then(r => r.json())
      .then(d => {
        if (seq !== advisorLoadSeq.current) return;
        setAdvisorData(d.advice || null);
        setAdvisorLoading(false);
      })
      .catch(() => { if (seq === advisorLoadSeq.current) setAdvisorLoading(false); });
  };

  const runAdvisor = () => {
    const aid = currentAccount;
    const seq = ++advisorLoadSeq.current;
    setAdvisorLoading(true);
    apiFetch(`/api/cpx_advisor/run?account_id=${aid}`)
      .then(r => r.json())
      .then(d => {
        if (seq !== advisorLoadSeq.current) return;
        setAdvisorData(d.advice || null);
        setAdvisorLoading(false);
      })
      .catch(() => { if (seq === advisorLoadSeq.current) setAdvisorLoading(false); });
  };

  const activateAdvisor = async () => {
    const cpl = parseFloat(advMaxCpl), lim = parseFloat(advDailyLimit);
    if (!cpl || !lim) { notify("Заполните обе цифры: макс цену лида и суточный лимит бюджета"); return; }
    setAdvisorLoading(true);
    await apiFetch("/api/avito/set_kpi_settings", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({account_id: currentAccount, target_leads_per_day: advisorData?.target?.target_leads_per_day ?? undefined,
        max_cost_per_lead_rub: cpl, daily_budget_limit_rub: lim,
        hard_max_bid_rub: parseFloat(advHardMaxBid) || undefined,
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
    // Повышение ставок является денежным действием: никогда не подставляем
    // скрытый бюджет. Для включения нужен явно сохранённый суточный лимит.
    if (!advAutopilot) {
      const lim = parseFloat(advDailyLimit);
      const cpl = parseFloat(advMaxCpl);
      if (!cpl || cpl <= 0) {
        notify("Чтобы включить автопилот, сначала задайте максимальную цену лида и нажмите «Сохранить».");
        return;
      }
      if (!lim || lim <= 0) {
        notify("Чтобы включить повышение ставок, сначала задайте суточный лимит бюджета и нажмите «Сохранить».");
        return;
      }
      setAutopilotModalOpen(true); return;
    }
    doToggleAutopilot(); // выключение — без подтверждения
  };

  const doToggleAutopilot = async () => {
    const next = !advAutopilot;
    setAutopilotModalOpen(false);
    await apiFetch("/api/avito/set_kpi_settings", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({account_id: currentAccount,
        target_leads_per_day: advisorData?.target?.target_leads_per_day ?? undefined,
        max_cost_per_lead_rub: advisorData?.target?.max_cpl_rub ?? (advMaxCpl.trim() ? parseFloat(advMaxCpl) : undefined),
        daily_budget_limit_rub: advDailyLimit.trim() ? parseFloat(advDailyLimit) : undefined,
        hard_max_bid_rub: advHardMaxBid.trim() ? parseFloat(advHardMaxBid) : undefined,
        bid_autopilot: next})
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
      const yyyy = String(t.getFullYear());
      const mo = String(t.getMonth() + 1).padStart(2, "0");
      const dd = String(t.getDate()).padStart(2, "0");
      const hh = String(t.getHours()).padStart(2, "0");
      const mi = String(t.getMinutes()).padStart(2, "0");
      // AVITO_REAL_DATEBEGIN_SCHEDULE_V1
      newSched[idx] = `${yyyy}-${mo}-${dd}T${hh}:${mi}:00`;
    });
    setItemSchedules(newSched);
    alert(`Расписание применено к ${chosen.length} объявлениям. Даты попадут в фид при публикации.`);
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
      params: {},
      date_begin: itemSchedules[i] || "",
      timezone: itemSchedules[i] ? (Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow") : ""
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
        params: {},
        date_begin: itemSchedules[i] || "",
        timezone: itemSchedules[i] ? (Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow") : ""
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

  const loadKpiActionMemory = async () => {
    try {
      const res = await apiFetch(`/api/avito/kpi_action_memory?account_id=${currentAccount}&limit=12`);
      const data = await res.json();
      setKpiActionMemory(Array.isArray(data.items) ? data.items : []);
    } catch { setKpiActionMemory([]); }
  };

  const loadKpiReadiness = async () => {
    try { const res = await apiFetch(`/api/avito/ai_marketing_readiness?account_id=${currentAccount}`); const data = await res.json(); setKpiReadiness(data?.status === "ok" ? data : null); setKpiCheckResult(data?.current_kpi?.status === "ok" ? data.current_kpi : null); } catch { setKpiReadiness(null); }
  };
  const confirmKpiTariff = async (state: "CONFIRMED_ACTIVE" | "CONFIRMED_NOT_ACTIVE") => {
    if (!currentAccount || !kpiReadiness?.required_avito_tariff) return;
    const r = await apiFetch(`/api/avito/ai_marketing_tariff_confirmation`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({account_id:currentAccount,state,actual_tariff:kpiReadiness.required_avito_tariff})});
    if (!r.ok) throw new Error(`Ошибка сервера, код ${r.status}`);
    await loadKpiReadiness();
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
          lead_temperature: kpiLeadTemp,
          ...(kpiGoalAuto !== null ? { goal_auto_enabled: kpiGoalAuto } : {})
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
    if (!kpiPlanIdPrefix.trim() && !confirm("Тексты будут переписаны у ВСЕХ объявлений аккаунта (" + (kpiPlan && kpiPlan.affected_items ? kpiPlan.affected_items : "?") + " шт.). Продолжить?")) return;
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
      setKpiGoalAuto(s.mode === "goal_auto");
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
      : status === "done" || status === "ready" ? "#12805C" : "var(--boris-blue)";
    const statusLabel = status === "needs_confirmation" ? "⏸ ждёт подтверждения"
      : status === "error" ? "❌ ошибка" : status === "cancelled" ? "🚫 отменено"
      : status === "queued" ? "🕓 в очереди" : status === "running" ? "🔵 идёт"
      : "✅ готово";
    const batchLabel = `Конвейер: ${run.direction}`;
    const draftsCount = Array.isArray(res.draft_ids) ? res.draft_ids.length : null;
    const isFinished = status === "done" || status === "ready";
    return (
      <div key={run.rootId} className={nested ? "" : "b-panel b-card-eq"} style={{padding: nested ? "12px" : "18px", background: nested ? "var(--boris-surface)" : undefined, borderRadius: nested ? "10px" : undefined, border: nested ? "1px solid #EEF2FA" : undefined}}>
        <div className="boris-f2-eee27c7b4dc7">
          <div className="boris-f2-7e30d285d288">
            {!nested && <span className="b-icon-sm boris-f2-886256c1d4fc boris-g1-icon">🚀</span>}
            <div>
              <div className="boris-f2-5a316b8cb6a1">{run.direction}</div>
              <div className="boris-f2-6c2427e9980a">{run.createdAt}</div>
            </div>
          </div>
          <span style={{fontSize:"13px", fontWeight:"bold", color: barColor}}>{statusLabel}</span>
        </div>
        <div className="boris-f2-a31d068d635d">{res.step_label || run.active?.error_message || "…"}</div>
        <div className="boris-f2-60c739c22a08">
          <div style={{width:`${percent}%`, height:"100%", background: barColor, borderRadius:"6px", transition:"width 0.6s ease, background-color 0.3s ease"}} />
        </div>
        {status === "needs_confirmation" && (
          <div className="boris-f2-9374e8421025">
            {res.question && <div className="boris-f2-53074ef0bedb">{res.question}</div>}
            <textarea placeholder="Ваш ответ Борису (например: собственное производство, цена за штуку, цвет — белый)"
              value={pipelineAnswerDrafts[run.active.id] || ""}
              onChange={e => setPipelineAnswerDrafts(prev => ({...prev, [run.active.id]: e.target.value}))}
              rows={2}  className="boris-f2-2fbbefd1a50d"/>
            <div className="boris-f2-6b00e75351fc">
              <Button className="boris-btn-hover boris-f2-ce4a2a8735c1" onClick={() => confirmPipelineTask(run.active.id, "proceed", pipelineAnswerDrafts[run.active.id])}
>
                ✏️ Ответить и продолжить
              </Button>
              <Button className="boris-btn-hover boris-f2-677893180d9a" onClick={() => confirmPipelineTask(run.active.id, "proceed")}
>
                Всё равно
              </Button>
              <Button className="boris-btn-hover boris-f2-5c30ecb73ebc" onClick={() => confirmPipelineTask(run.active.id, "cancel")}
>
                Отмена
              </Button>
            </div>
          </div>
        )}
        {isFinished && (
          <div className="boris-f2-9374e8421025">
            <Button className="boris-btn-hover boris-f2-e3cc8c33c3ba" onClick={() => { setActiveTab("listings"); setListingFilter("drafts"); setDraftBatchFilter(batchLabel); loadDrafts(); }}
>
              ✅ Готово{draftsCount != null ? ` ${draftsCount}` : ""} → Смотреть результаты
            </Button>
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
    <div className="boris-f2-95d055f2fb06">
      <Button type="button" onClick={() => applyFormatToTextarea(textareaId, "bold")} title="Жирный" className="boris-f2-18c74fc4d520">Ж</Button>
      <Button type="button" onClick={() => applyFormatToTextarea(textareaId, "bullet")} title="Список" className="boris-f2-ac71e5eb9b0d">•</Button>
      <Button type="button" onClick={() => applyFormatToTextarea(textareaId, "number")} title="Нумерованный список" className="boris-f2-ac71e5eb9b0d">1.</Button>
      <div  className="boris-f2-79d7f850cd3f"/>
      {["🔥","✅","❌","📞","💬","🎁","⭐","🚀"].map(emoji => (
        <Button key={emoji} type="button" onClick={() => applyFormatToTextarea(textareaId, emoji)} className="boris-f2-34ddb35f15e7">{emoji}</Button>
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
    if (!draftBatchBannerKeyRef.current) {
      draftBatchBannerKeyRef.current = globalThis.crypto?.randomUUID?.() || `draft-banners-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    let serverAnswered = false;
    try {
      const r = await apiFetch("/api/avito/apply_banner_to_batch", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, batch_label: label, banner_count: bannerCount, photos_per_ad: photosPerAdInput, folder: bannerFolderInput.trim(), idempotency_key: draftBatchBannerKeyRef.current })
      });
      serverAnswered = true;
      const d = await r.json();
      if (d.status === "ok") {
        alert(`✅ Готово: баннеров ${d.banners_generated || 0}, обновлено черновиков ${d.updated || 0}`);
        loadDrafts && loadDrafts();
      } else {
        alert(`⚠️ ${d.message || "Не удалось"}`);
      }
    } catch (e) { alert("Ошибка связи с сервером"); }
    finally {
      // Preserve the key only when the network result is ambiguous.
      if (serverAnswered) draftBatchBannerKeyRef.current = "";
      setDraftPhotoLoading(false);
    }
  };

  const runBatchBanners = async () => {
    if (!pipelineBatch || !currentAccount) return;
    setBatchBannerLoading(true);
    setBannerResult("Генерирую баннеры (обычно 30-90 секунд)...");
    if (!pipelineBatchBannerKeyRef.current) {
      pipelineBatchBannerKeyRef.current = globalThis.crypto?.randomUUID?.() || `pipeline-banners-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    let serverAnswered = false;
    try {
      const r = await apiFetch("/api/avito/apply_banner_to_batch", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, batch_label: pipelineBatch.label, banner_count: bannerCount, photos_per_ad: photosPerAdInput, folder: bannerFolderInput.trim(), idempotency_key: pipelineBatchBannerKeyRef.current })
      });
      serverAnswered = true;
      const d = await r.json();
      if (d.status === "ok") {
        setBannerResult(`✅ Баннеров создано: ${d.banners_generated}, обновлено черновиков: ${d.updated}` + (d.notice ? ` (${d.notice})` : ""));
      } else {
        setBannerResult(`⚠️ ${d.message || "Не удалось"}`);
      }
    } catch (e) { setBannerResult("Ошибка связи с сервером"); }
    finally {
      // A retry after an ambiguous disconnect reuses the same economic intent.
      if (serverAnswered) pipelineBatchBannerKeyRef.current = "";
      setBatchBannerLoading(false);
    }
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
          const list = (d.mismatches || []).map((m: any) => `• «${m.title}» — сейчас «${m.category}», похоже на «${m.suggested_category}» (${humanizeBusinessText(m.reason,"причина уточняется")})`).join("\n");
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
      const response = await apiFetch(`/api/avito/drafts/${draftId}`, {
        method: "PUT", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ account_id: currentAccount, ...fields })
      });
      const result = await response.json();
      if (!response.ok || result.status !== "ok") throw new Error(result?.detail?.message || result?.detail || result?.message || "Не удалось сохранить черновик");
      setEditingDraftId(null);
      loadDrafts();
    } catch (e: any) { alert(e?.message || "Ошибка связи с сервером"); }
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
          const actionsList = (data.results || []).map((r: any) => r.error ? `❌ ${humanizeBusinessText(r.action,"Действие BORIS")}: ${humanizeBusinessText(r.error,"не удалось завершить действие")}` : `✅ ${humanizeBusinessText(r.action,"Действие BORIS")}`).join("\n");
          const skippedList = (data.skipped || []).length > 0 ? "\n\n⚠️ Пропущено:\n" + data.skipped.join("\n") : "";
          const costText = (data.cost && userRole === "owner") ? `\n\n💰 Себестоимость: ${data.cost.total_rub}₽ (GPT-разбор: ${data.cost.gpt_routing_rub}₽ + действия: ${data.cost.steps_estimate_rub}₽)` : "";
          alert(`Борис выполнил ${data.steps_done ?? 0} из шагов задачи:\n${actionsList}${skippedList}${costText}`);
        }
      } else if (data.status === "needs_clarification") {
        alert(`BORIS пока не может продолжить автоматически: ${humanizeBusinessText(data.reason,"нужны дополнительные данные или внешний шаг")}`);
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
    if (activeTab === "marketing") { loadKpiSettings(); loadKpiActionMemory(); loadKpiReadiness(); checkKpi(); }
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
    if (!currentAccount) { setAnalysisLoading(false); return alert("Сначала выберите аккаунт Avito"); }
    const startResp = await apiFetch("/api/parser/city_analysis_gateway", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({account_id: currentAccount, query: analysisQuery, cities, top_n: 20})
    }).then(r => r.json());
    const jobs = Array.isArray(startResp.jobs) ? startResp.jobs : [];
    if (!jobs.length) { setAnalysisLoading(false); return alert(startResp.message || "Не удалось запустить анализ"); }
    setAnalysisProgress({done: 0, total: jobs.length});
    const completed = new Map<string, any>();
    const poll = setInterval(async () => {
      try {
        await Promise.all(jobs.map(async (job: any) => {
          if (completed.has(job.job_id)) return;
          const st = await apiFetch(`/api/parser/city_analysis_gateway/status?account_id=${encodeURIComponent(currentAccount)}&job_id=${encodeURIComponent(job.job_id)}`).then(r => r.json());
          if (st.status === "ok" || st.status === "error") completed.set(job.job_id, st);
        }));
        setAnalysisResults(Array.from(completed.values()));
        setAnalysisProgress({done: completed.size, total: jobs.length});
        if (completed.size >= jobs.length) {
          clearInterval(poll);
          setAnalysisLoading(false);
          setAnalysisProgress(null);
        }
      } catch (e) {}
    }, 2500);
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

  const reorderDraftImage = (draggedUrl: string, targetUrl: string) => {
    if (!draggedUrl || !targetUrl || draggedUrl === targetUrl) return;
    setDuplicateEditingItem((prev: any) => {
      const images = [...(prev?.images || [])];
      const from = images.indexOf(draggedUrl);
      const to = images.indexOf(targetUrl);
      if (from < 0 || to < 0) return prev;
      images.splice(from, 1);
      images.splice(to, 0, draggedUrl);
      return {...prev, images};
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
          price: duplicateEditingItem.price, images: duplicateEditingItem.images || [],
          is_template: duplicateEditingItem.is_template
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

  const inputStyle: any = {width:"100%", background:"var(--boris-surface)", border:"1px solid var(--boris-border)", borderRadius:"8px", padding:"10px", color:"var(--boris-text)", fontSize:"15px", boxSizing:"border-box"};
  const days = ["пн","вт","ср","чт","пт","сб","вс"];

  if (view === "accounts") return (
    <div className="boris-f2-7ba8c0d7e5d9">
      <div className="boris-f2-de6da4e6c404">
        <div className="boris-f2-16f2d512882a">
          <Mascot size={26} interactive={false} />
          <span className="boris-f2-722924035001">БОРИС{userRole === "owner" ? " · Админ-панель" : ""}</span>
        </div>
        {accounts.length > 1 && userRole !== "owner" && (
          <Button onClick={() => router.push("/agency")} className="boris-btn-hover boris-f2-5e664ad55d8d">🏢 Мои аккаунты</Button>
        )}
        <Button onClick={back.go} className="boris-btn-hover boris-f2-b1618c3b47eb">← В кабинет</Button>
      </div>

      <div className="boris-f2-7a3756a5cd36">
        {crmOpen && userRole === "owner" && (
          <div className="boris-f2-8b9688e6e090">
            <div className="boris-f2-0baf9f8e11aa">
              <Button className="boris-btn-hover boris-f2-094291ed5607" onClick={() => { setCrmOpen(false); setEconOpen(false); }}>← Назад</Button>
              <span className="boris-f2-8bd094d7a630">Клиенты владельца</span>
            </div>
            <div className="boris-f2-283d51b208bf">
              <Button className="boris-btn-hover" onClick={async () => { const v = !econOpen; setEconOpen(v); if (v && !econData) { try { const r = await apiFetch("/api/economics/overview?days=30"); const d = await r.json(); if (d.status === "ok") setEconData(d); } catch {} } }} style={{background: econOpen ? "var(--boris-blue)" : "var(--boris-surface)", border:"1.5px solid var(--boris-blue)", color: econOpen ? "var(--boris-surface)" : "var(--boris-blue)", borderRadius:"10px", padding:"12px 22px", fontSize:"15px", fontWeight:700, cursor:"pointer"}}>💰 Экономика</Button>
              <Button className="boris-btn-hover boris-f2-0ea4e5b82c11" onClick={() => router.push("/dashboard/crm")}>📋 Лиды и сделки</Button>
            </div>
          </div>
        )}
        {userRole === "owner" && (
          <div style={{display: crmOpen ? "block" : "grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"16px", marginBottom:"32px"}}>
            <BorisCard onClick={() => setCrmOpen(true)} className="boris-card-hover boris-g1-card" style={{background:"var(--boris-surface)", border:"1px solid var(--boris-border)", borderRadius:"16px", padding:"24px", cursor:"pointer", display: crmOpen ? "none" : "block", position:"relative", overflow:"hidden"}}>
              <div  className="boris-f2-f2082c9afe40"/><div className="boris-f2-3eb9ecfc6d8f"><span className="b-ref-ic boris-f2-fcc8aad8504e">📇</span><span className="boris-f2-0c9626a4337f">Клиенты владельца</span></div>
              <div className="boris-f2-8d8140e5d2d5">CRM клиентов, воронка, задачи и сделки</div>
            </BorisCard>
            <BorisCard onClick={() => router.push("/dashboard/launch-client")} className="boris-card-hover boris-g1-card" style={{background:"var(--boris-surface)", border:"1px solid var(--boris-border)", borderRadius:"16px", padding:"24px", cursor:"pointer", display: crmOpen ? "none" : "block", position:"relative", overflow:"hidden"}}>
              <div  className="boris-f2-212ed57b15c0"/><div className="boris-f2-3eb9ecfc6d8f"><span className="b-ref-ic boris-f2-c50e8401a6cc">🛠</span><span className="boris-f2-0c9626a4337f">Технический онбординг</span></div>
              <div className="boris-f2-96046ead62a3">Проверить готовность и подключить нового клиента</div>
            </BorisCard>
            <BorisCard onClick={() => setShowAccountsList(v => !v)} className="boris-card-hover boris-g1-card" style={{background:"var(--boris-surface)", border:"1px solid var(--boris-border)", borderRadius:"16px", padding:"24px", cursor:"pointer", display: crmOpen ? "none" : "block", position:"relative", overflow:"hidden"}}>
              <div  className="boris-f2-f4e03e24cfce"/><div className="boris-f2-3eb9ecfc6d8f"><span className="b-ref-ic boris-f2-b6403d10ce58">📋</span><span className="boris-f2-0c9626a4337f">Аккаунты Авито</span></div>
              <div className="boris-f2-96046ead62a3">{showAccountsList ? "Скрыть список" : "Показать список клиентских аккаунтов"}</div>
            </BorisCard>
          {econOpen && econData && (() => {
            const it = econData["итого"] || {};
            const box = (grad: string, val: string, cap: string) => (
              <BorisCard className="boris-card-hover boris-f2-a1b59dff2608 boris-g1-card">
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div className="boris-f2-4a3867b8b765">{val}</div>
                <div className="boris-f2-b703107f9e84">{cap}</div>
              </BorisCard>
            );
            const rub = (v: any) => (Number(v)||0).toLocaleString("ru") + " \u20bd";
            return (
              <div className="boris-f2-2a28d13f8dfd">
                <div className="boris-f2-9952a1398673">
                  {box("linear-gradient(135deg,#32D583,#12805C)", rub(it["получено"]), "получено от клиентов")}
                  {box("linear-gradient(135deg,#FF6B6B,#F04438)", rub(it["потрачено"]), "потрачено на генерацию")}
                  {box("linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", rub(it["маржа"]), "маржа за 30 дней")}
                  {box("linear-gradient(135deg,var(--boris-text-3),var(--boris-text-2))", rub(it["расход_без_аккаунта"]), "расход вне клиентов")}
                </div>
                <div className="boris-f2-ccdd3fa8b8b8">
                  <div className="boris-f2-54ef736e30e1">На что уходят деньги</div>
                  {(econData["по_операциям"]||[]).map((o: any, i: number) => (
                    <div key={i} style={{display:"flex", justifyContent:"space-between", padding:"7px 0", borderTop: i ? "1px solid var(--boris-border)" : "none", fontSize:"14px"}}>
                      <span className="boris-f2-de52905d633b">{o["операция"]}</span>
                      <span><b>{rub(o["руб"])}</b> <span className="boris-f2-6607ac287d11">· {o["раз"]} раз</span></span>
                    </div>
                  ))}
                </div>
                <div className="boris-f2-0d88e7648069">
                  <div className="boris-f2-54ef736e30e1">По клиентам</div>
                  <table className="boris-f2-1f5bf21361d2">
                    <thead><tr className="boris-f2-4cdc3a34c4cd"><th className="boris-f2-885d720aa494">Клиент</th><th>Заплатил</th><th>Потратил</th><th>Маржа</th></tr></thead>
                    <tbody>{(econData["клиенты"]||[]).map((c: any) => (
                      <tr key={c.account_id} className="boris-f2-54bb6239453e">
                        <td className="boris-f2-0dfecaf781a4">{c["название"] || c.account_id}<div className="boris-f2-edeb33f149cd">{c.account_id}</div></td>
                        <td><b className="boris-f2-de450bffd9aa">{rub(c["заплатил"])}</b></td>
                        <td className="boris-f2-de52905d633b">{rub(c["потратил"])}</td>
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
        <div className="boris-f2-d90da07dd31d">
          <h2 className="boris-f2-6b1845f3027b">Аккаунты Авито</h2>
          <Button onClick={() => { setShowAddForm(true); setStep(1); }} className="boris-f2-1cec6a943c82">+ Добавить аккаунт</Button>
        </div>

        {showAddForm && (
          <div className="boris-f2-497dbb52498e">
            <div className="boris-f2-cffa47be58d9">
              <div style={{flex:1, height:"4px", borderRadius:"2px", background: step >= 0 ? "var(--boris-blue)" : "var(--boris-border)"}}></div>
              <div style={{flex:1, height:"4px", borderRadius:"2px", background: step >= 1 ? "var(--boris-blue)" : "var(--boris-border)"}}></div>
              <div style={{flex:1, height:"4px", borderRadius:"2px", background: step >= 2 ? "var(--boris-blue)" : "var(--boris-border)"}}></div>
            </div>

            {step === 0 && (
              <>
                <h3 className="boris-f2-2f71836d68bb">С чего начнём?</h3>
                <p className="boris-f2-5209fe92f5d0">Выберите задачу — Борис покажет только нужные шаги</p>
                <div className="boris-f2-fe3fb0d0fc7d">
                  <Button className="boris-btn-hover" onClick={() => { setWizardGoal("site"); setStep(1); }} style={{textAlign:"left", background: wizardGoal==="site" ? "var(--boris-blue-soft)" : "var(--boris-surface)", border: wizardGoal==="site" ? "2px solid var(--boris-blue)" : "1px solid var(--boris-border)", borderRadius:"10px", padding:"16px 18px", cursor:"pointer", width:"100%"}}>
                    <div className="boris-f2-66f0d659b84c">Перенести товары с моего сайта</div>
                    <div className="boris-f2-6c2427e9980a">Борис выгрузит каталог с вашего сайта</div>
                  </Button>
                  <Button className="boris-btn-hover" onClick={() => { setWizardGoal("avito"); setStep(1); }} style={{textAlign:"left", background: wizardGoal==="avito" ? "var(--boris-blue-soft)" : "var(--boris-surface)", border: wizardGoal==="avito" ? "2px solid var(--boris-blue)" : "1px solid var(--boris-border)", borderRadius:"10px", padding:"16px 18px", cursor:"pointer", width:"100%"}}>
                    <div className="boris-f2-66f0d659b84c">Подтянуть объявления с Avito</div>
                    <div className="boris-f2-6c2427e9980a">Импортировать то, что уже размещено</div>
                  </Button>
                  <Button className="boris-btn-hover" onClick={() => { setWizardGoal("scratch"); setStep(1); }} style={{textAlign:"left", background: wizardGoal==="scratch" ? "var(--boris-blue-soft)" : "var(--boris-surface)", border: wizardGoal==="scratch" ? "2px solid var(--boris-blue)" : "1px solid var(--boris-border)", borderRadius:"10px", padding:"16px 18px", cursor:"pointer", width:"100%"}}>
                    <div className="boris-f2-66f0d659b84c">Создать объявления с нуля</div>
                    <div className="boris-f2-6c2427e9980a">Составим и оформим новые объявления</div>
                  </Button>
                  <div style={{background:"var(--boris-surface)", border: wizardGoal==="other" ? "2px solid var(--boris-blue)" : "1px solid var(--boris-border)", borderRadius:"10px", padding:"16px 18px"}}>
                    <Button className="boris-btn-hover boris-f2-5d455e2295eb" onClick={() => setWizardGoal("other")}>
                      <div className="boris-f2-66f0d659b84c">Другое</div>
                      <div className="boris-f2-6c2427e9980a">Опишите свою задачу своими словами</div>
                    </Button>
                    {wizardGoal==="other" && (
                      <input value={wizardGoalText} onChange={e => setWizardGoalText(e.target.value)} placeholder="Например: веду 3 магазина, хочу объединить" style={{...inputStyle, marginTop:"12px"}} />
                    )}
                  </div>
                </div>
                <div className="boris-f2-d4268e91708c">
                  <Button className="boris-btn-hover boris-f2-cb85b182d2e2" onClick={() => { setShowAddForm(false); setWizardGoal(""); }}>Отмена</Button>
                  <Button className="boris-btn-hover" disabled={!wizardGoal} onClick={() => setStep(1)} style={{background: wizardGoal ? "var(--boris-blue)" : "var(--boris-border)", color: wizardGoal ? "var(--boris-bg)" : "#9AA3B2", border:"none", borderRadius:"10px", padding:"10px 24px", fontWeight:"bold", cursor: wizardGoal ? "pointer" : "not-allowed"}}>Далее →</Button>
                </div>
              </>
            )}

            {step === 1 && (
              <>
                <h3 className="boris-f2-2f71836d68bb">Шаг 1: Данные аккаунта Авито</h3>
                <p className="boris-f2-5209fe92f5d0">Логин, пароль и API ключи аккаунта</p>
                <div className="boris-f2-5876f482cb27">
                  <div className="boris-f2-d41556864646">{"Как получить идентификатор приложения (Client ID) и секрет приложения (Client Secret)"}</div>
                  <div className="boris-f2-6a7e47b0325d">{"\u041d\u0443\u0436\u0435\u043d \u0442\u0430\u0440\u0438\u0444 Avito \u00ab\u0420\u0430\u0441\u0448\u0438\u0440\u0435\u043d\u043d\u044b\u0439\u00bb \u0438\u043b\u0438 \u00ab\u041c\u0430\u043a\u0441\u0438\u043c\u0430\u043b\u044c\u043d\u044b\u0439\u00bb \u2014 \u0431\u0435\u0437 \u043d\u0435\u0433\u043e API \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d."}</div>
                  <ol className="boris-f2-e1d10995655d">
                    <li>{"\u0412 \u043b\u0438\u0447\u043d\u043e\u043c \u043a\u0430\u0431\u0438\u043d\u0435\u0442\u0435 Avito \u043e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u00ab\u041f\u0440\u043e\u0444\u0438\u043b\u044c \u0438 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438\u00bb \u2192 \u00ab\u0418\u043d\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u0438 \u0438 API\u00bb (\u043f\u0443\u043d\u043a\u0442 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u043d\u0430 \u0430\u043d\u0433\u043b\u0438\u0439\u0441\u043a\u043e\u043c \u2014 Integrations / API)"}</li>
                    <li>{"\u0421\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u0438\u043d\u0442\u0435\u0433\u0440\u0430\u0446\u0438\u044e \u0434\u043b\u044f \u0430\u0432\u0442\u043e\u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u0439"}</li>
                    <li>{"\u0412\u043d\u0438\u0437\u0443 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u044b \u0441\u043a\u043e\u043f\u0438\u0440\u0443\u0439\u0442\u0435 Client ID \u0438 Client Secret (\u043d\u0430 \u0430\u043d\u0433\u043b\u0438\u0439\u0441\u043a\u043e\u043c) \u0438 \u0432\u0441\u0442\u0430\u0432\u044c\u0442\u0435 \u0432 \u043f\u043e\u043b\u044f \u043d\u0438\u0436\u0435"}</li>
                  </ol>
                  <div className="boris-f2-1af820fe18b2">{"\u0421\u043a\u0440\u0438\u043d\u0448\u043e\u0442\u044b \u0448\u0430\u0433\u043e\u0432 \u0431\u0443\u0434\u0443\u0442 \u0437\u0434\u0435\u0441\u044c"}</div>
                  <a href="https://www.avito.ru/professionals/api" target="_blank" rel="noopener" className="boris-f2-bdc11cff861c">{"\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 API \u0432 Avito \u2192"}</a>
                </div>
                <div className="boris-f2-001013efee98">
                  {[
                    {label:"Название", key:"name", placeholder:"Название компании"},
                    {label:"Логин (email)", key:"login", placeholder:"email@yandex.ru"},
                    {label:"Пароль", key:"password", placeholder:"••••••••"},
                    {label:"Комментарий", key:"comment", placeholder:"Заметки об аккаунте"},
                    {label:"Идентификатор приложения (Client ID, Авито API)", key:"client_id", placeholder:"Вставьте Client ID"},
                    {label:"Секрет приложения (Client Secret, Авито API)", key:"client_secret", placeholder:"Вставьте Client Secret"},
                  ].map(field => (
                    <div key={field.key}>
                      <label className="boris-f2-5a0de0cf2264">{field.label}</label>
                      <input value={(newAccount as any)[field.key]} onChange={e => setNewAccount({...newAccount, [field.key]: e.target.value})}
                        placeholder={field.placeholder} type={field.key === "password" ? "password" : "text"} style={inputStyle} />
                    </div>
                  ))}
                </div>
                <div className="boris-f2-d4268e91708c">
                  <Button className="boris-btn-hover boris-f2-2fe5fa0e8409" onClick={() => { if (!newAccount.name || !newAccount.login) return alert("Заполните название и логин"); setStep(2); }}
>Далее →</Button>
                  <Button className="boris-btn-hover boris-f2-cb85b182d2e2" onClick={() => { setShowAddForm(false); setStep(1); }}>Отмена</Button>
                </div>
              </>
            )}

            {step === 2 && (
              <>
                <h3 className="boris-f2-2f71836d68bb">🏢 Шаг 2: Расскажи о компании</h3>
                <p className="boris-f2-5209fe92f5d0">БОРИС изучит бизнес и будет использовать эту информацию для создания объявлений</p>
                <div className="boris-f2-001013efee98">
                  <div className="boris-f2-97294b207c62">
                    <label className="boris-f2-5a0de0cf2264">Сайт компании</label>
                    <input value={newAccount.companyWebsite} onChange={e => setNewAccount({...newAccount, companyWebsite: e.target.value})} placeholder="https://example.com" style={inputStyle} />
                  </div>
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Сфера деятельности</label>
                    <input value={newAccount.companyNiche} onChange={e => setNewAccount({...newAccount, companyNiche: e.target.value})} placeholder="Строительные материалы" style={inputStyle} />
                  </div>
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Тон общения</label>
                    <select className="b-select" value={newAccount.companyTone} onChange={e => setNewAccount({...newAccount, companyTone: e.target.value})} style={inputStyle}>
                      <option>Дружелюбный</option><option>Деловой</option><option>Экспертный</option><option>Простой</option>
                    </select>
                  </div>
                  <div className="boris-f2-97294b207c62">
                    <label className="boris-f2-5a0de0cf2264">Описание компании</label>
                    <textarea value={newAccount.companyDescription} onChange={e => setNewAccount({...newAccount, companyDescription: e.target.value})}
                      placeholder="Чем занимается компания" rows={3} style={{...inputStyle, resize:"vertical"}} />
                  </div>
                  <div className="boris-f2-97294b207c62">
                    <label className="boris-f2-5a0de0cf2264">Преимущества компании</label>
                    <textarea value={newAccount.companyAdvantages} onChange={e => setNewAccount({...newAccount, companyAdvantages: e.target.value})}
                      placeholder="Доставка за час, гарантия 5 лет" rows={3} style={{...inputStyle, resize:"vertical"}} />
                  </div>
                </div>
                <div className="boris-f2-d4268e91708c">
                  <Button className="boris-btn-hover boris-f2-cb85b182d2e2" onClick={() => setStep(1)}>← Назад</Button>
                  <Button className="boris-btn-hover" onClick={addAccount} disabled={addingAccount} style={{background: addingAccount ? "var(--boris-border)" : "var(--boris-blue)", color: addingAccount ? "var(--boris-text-2)" : "var(--boris-bg)", border:"none", borderRadius:"10px", padding:"10px 24px", fontWeight:"bold", cursor: addingAccount ? "wait" : "pointer"}}>{addingAccount ? "⏳ Создаю..." : "✅ Создать аккаунт"}</Button>
                </div>
              </>
            )}
          </div>
        )}

        {accounts.length > 0 && (
          <div className="boris-f2-8d66ef11ddb7">Выберите аккаунт, чтобы продолжить работу</div>
        )}

        {accountsError && !showAddForm && (
          <div className="boris-f2-1dd3b1dc6020">
            <div className="boris-f2-863ecc270f82">Не удалось загрузить список аккаунтов</div>
            <div className="boris-f2-5209fe92f5d0">{accountsError}</div>
            <Button className="boris-btn-hover boris-f2-2cc305605595" onClick={() => window.location.reload()}>Повторить</Button>
          </div>
        )}
        {accounts.length === 0 && !showAddForm && !accountsError && (
          <div className="boris-f2-ebe9e7fbab13">
            <div className="boris-f2-9f0adb8bdffc">🔌</div>
            <div className="boris-f2-f62da0285e94">Пока нет ни одного аккаунта Авито</div>
            <div className="boris-f2-5209fe92f5d0">Подключите аккаунт — Борис начнёт работать с вашими объявлениями</div>
            <Button className="boris-btn-hover boris-f2-2cc305605595" onClick={() => { setShowAddForm(true); setStep(1); }}>Подключить аккаунт</Button>
            <div className="boris-f2-8a359a76ebec">
              <Button onClick={() => router.push("/dashboard/home")} className="boris-f2-fb1db79dceca">Вернуться в кабинет</Button>
            </div>
          </div>
        )}

        <div className="boris-f2-6aa137d58b1b">
          {accounts.map(acc => (
            <div key={acc.id} className="boris-f2-abc6aa954ffe">
              <div>
                <div className="boris-f2-2e00323aee86">{acc.name}</div>
                <div className="boris-f2-8e893b7db6d7">{acc.login}</div>
                <div className="boris-f2-316437256d37">{acc.comment}</div>
                {(acc as any).company?.niche && <div className="boris-f2-8e97d7c9817d">🏢 {(acc as any).company.niche}</div>}
              </div>
              <div className="boris-f2-16f2d512882a">
                <span className="boris-f2-9b85b88d6c35">● Активен</span>
                <Button className="boris-btn-hover boris-f2-1cec6a943c82" onClick={() => openAccount(acc)}>Открыть →</Button>
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
    <div className="boris-f2-5846b899f23a">
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
        .boris-side-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 14px rgba(16,24,40,0.12) !important; border-color: var(--boris-blue) !important; }

        .b-card { background:var(--boris-surface); border:1px solid var(--boris-border); border-radius:16px; padding:24px; position:relative; overflow:hidden; transition: all 0.2s ease; }
        .b-card:hover { transform: translateY(-3px); box-shadow: 0 12px 24px rgba(16,24,40,0.10); }
        .b-panel { background:var(--boris-surface); border:1px solid var(--boris-border); border-radius:16px; padding:24px; }
        .b-title { font-size:20px; font-weight:800; color:var(--boris-text); margin:0 0 6px; letter-spacing:-0.01em; }
        .b-sub { font-size:14px; color:var(--boris-text-2); margin:0 0 20px; }
        .b-label { font-size:13px; font-weight:600; color:var(--boris-text-2); display:block; margin-bottom:6px; }
        .b-input { width:100%; box-sizing:border-box; background:var(--boris-surface); border:1px solid var(--boris-border); border-radius:10px; padding:11px 14px; font-size:14px; color:var(--boris-text); outline:none; transition: all 0.15s ease; font-family:inherit; }
        .b-input:focus { border-color:var(--boris-blue); box-shadow:0 0 0 3px rgba(47,111,237,0.10); }
        .b-btn { display:inline-flex; align-items:center; justify-content:center; gap:6px; border-radius:10px; padding:11px 20px; font-size:14px; font-weight:700; cursor:pointer; text-decoration:none; box-sizing:border-box; transition: all 0.2s ease; }
        .b-btn-primary { background:var(--boris-blue); color:var(--boris-surface); border:1px solid var(--boris-blue); }
        .b-btn-primary:hover { transform:translateY(-2px); box-shadow:0 6px 16px rgba(47,111,237,0.30); }
        /* --- нормализация вкладки Объявления --- */
        .b-btn { height:40px; padding:0 16px !important; font-size:13px !important; white-space:nowrap; line-height:1; }
        .b-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(16,24,40,0.10); }
        .b-btn:active { transform: translateY(0); box-shadow:none; }
        .b-nav { display:flex; align-items:center; gap:10px; width:100%; text-align:left; border-radius:10px; padding:8px 10px; cursor:pointer; font-size:14px; margin-bottom:3px; transition: all .15s ease; }
        .b-nav:hover { background:var(--boris-bg) !important; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(16,24,40,0.08); }
        .b-nav:active { transform: translateY(0); box-shadow:none; }
        .b-nav .b-icon-sm { transition: transform .15s ease, box-shadow .15s ease; }
        .b-nav:hover .b-icon-sm { transform: scale(1.06); box-shadow: 0 3px 10px rgba(16,24,40,0.18); }
        @keyframes statIn { from { opacity:0; transform: translateY(14px); } to { opacity:1; transform: translateY(0); } }
        @keyframes iconPulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.08); } }
        @keyframes shine { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
        .b-stat { display:flex; align-items:center; gap:18px; padding:20px 22px; opacity:0; animation: statIn .55s cubic-bezier(.22,1,.36,1) forwards; flex:1; min-height:118px; }
        .b-stat:hover .b-stat-ico { animation: iconPulse 1.2s ease-in-out infinite; }
        .b-stat-ico { width:56px; height:56px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:26px; flex-shrink:0; box-shadow: 0 4px 14px rgba(16,24,40,0.14); }
        .b-stat-num { font-size:34px; font-weight:800; line-height:1; letter-spacing:-0.02em; background:linear-gradient(90deg,var(--boris-text),var(--boris-text-2),var(--boris-text)); background-size:200% auto; -webkit-background-clip:text; background-clip:text; color:transparent; animation: shine 4s linear infinite; }
        /* --- фильтры объявлений --- */
        @keyframes chipIn { from { opacity:0; transform: translateY(-6px) scale(.96); } to { opacity:1; transform:none; } }
        .b-filters { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:20px; }
        .b-filters { gap:12px !important; }
        .b-filters .b-btn {
          height:auto !important; padding:14px 18px !important; font-size:14px !important; font-weight:600 !important;
          background:var(--boris-surface); border:1px solid var(--boris-border); color:var(--boris-text-2); border-radius:14px;
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
        .b-meta { font-size:13px; color:var(--boris-text-2); white-space:nowrap; }
        .b-card-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; padding-top:14px; border-top:1px solid #EEF2FA; }
        .b-card-actions .b-btn { flex:0 0 auto; margin:0 !important; }
        .b-price-row { display:flex; align-items:center; gap:8px; margin:10px 0; }
        .b-more { width:100%; background:var(--boris-bg); border:1px solid var(--boris-border); border-radius:10px; padding:10px 12px; font-size:13px; color:var(--boris-text-2); cursor:pointer; transition:all .2s ease; }
        .b-more:hover { background:#EEF2FA; }
        .b-sep { height:1px; background:#EEF2FA; border:0; margin:16px 0; }
        .b-inline { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
        .b-photo-bar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; row-gap:14px; }
        .b-photo-bar > .b-meta { margin:0; }
        .b-photo-bar .b-brk { flex-basis:100%; height:0; }
        .b-btn-ghost { background:transparent; color:var(--boris-blue); border:2px solid var(--boris-blue); }
        .b-btn-ghost:hover { background:rgba(47,111,237,0.08); transform:translateY(-2px); box-shadow:0 6px 16px rgba(47,111,237,0.25); }
        .b-btn-soft { background:var(--boris-surface); color:var(--boris-text-2); border:1px solid var(--boris-border); }
        .b-btn-soft:hover { transform:translateY(-2px); box-shadow:0 6px 14px rgba(16,24,40,0.10); border-color:var(--boris-blue); color:var(--boris-blue); }
        .b-icon { width:64px; height:64px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:28px; flex-shrink:0; }
        .b-icon-sm { width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:18px; flex-shrink:0; }
        .b-chip { display:inline-block; font-size:13px; font-weight:700; border-radius:20px; padding:5px 14px; }
        .b-blob { position:absolute; top:-40px; right:-40px; width:120px; height:120px; border-radius:50%; opacity:0.08; pointer-events:none; }
        .b-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:16px; align-items:stretch; }
      `}</style>
        <Sidebar activeKey={activeTab} onSelect={setActiveTab}
          guideKey={guideActive && guideSteps[guideIndex]
            ? guideSteps[guideIndex].tab : ""} />

      <div className="b-content-col boris-dashboard-scroll">
        <div style={{ position:"sticky", top:0, zIndex:35, display:"flex", justifyContent:"flex-end", padding:"10px 16px", background:"rgba(255,255,255,.94)", borderBottom:"1px solid var(--boris-border)", backdropFilter:"blur(12px)" }}>
          <GlobalLogoutButton />
        </div>

      {userRole === "owner" && unreadEscalationsCount > 0 && showEscalationPopup && !directorMode && (
        <div className="boris-f2-db6fa137094b">
          <div className="boris-f2-f69162a77f4a">
            <div>
              <div className="boris-f2-7831ee398e7f">⚠️ Требует внимания</div>
              <div className="boris-f2-96046ead62a3">{unreadEscalationsCount} {unreadEscalationsCount === 1 ? "ошибка" : "ошибки/ошибок"} в аккаунтах — Борис не смог довести задачу до результата.</div>
              <Button className="boris-btn-hover boris-f2-e02afc9a225b" onClick={() => { setDirectorMode(true); setShowEscalationPopup(false); }}>👑 Открыть у директора</Button>
            </div>
            <Button className="boris-btn-hover boris-f2-ed9c6fefa922" onClick={() => setShowEscalationPopup(false)}>✕</Button>
          </div>
        </div>
      )}

      {advisorMode ? (
        <div className="boris-f2-7a3756a5cd36">
          <div className="boris-f2-faeec7a3a7ea">
            <h2 className="boris-f2-c482369e57a1"><span className="b-icon-sm boris-f2-fc42933d2a24 boris-g1-icon">📈</span>Маркетолог-советник по ставкам</h2>
            <Button className="boris-btn-hover boris-f2-515ee9c8c7e4" onClick={runAdvisor} disabled={advisorLoading}>
              {advisorLoading ? "⏳ Считаю..." : "🔄 Пересчитать"}
            </Button>
          </div>

          {/* Форма активации: два обязательных поля */}
          <BorisCard className="b-panel b-card-eq boris-f2-7dde5e56b3f3 boris-g1-card">
            <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-e179ca50e9d4 boris-g1-icon">🎯</span>Активация Советника</h3>
            <p className="b-sub">Оба поля обязательны. Пока не заданы — Советник не работает.</p>
            <div className="boris-f2-02bf13b70f3c">
              <div>
                <div className="b-label">Макс. цена лида, ₽</div><div className="boris-f2-be522d824950">Сколько готовы платить за один контакт</div>
                <input type="number" value={Number(advMaxCpl) === 0 ? "" : advMaxCpl} onChange={e => setAdvMaxCpl(e.target.value)} placeholder="500"
  className="boris-f2-e1f3097a630d"/>
              </div>
              <div>
                <div className="b-label">Суточный лимит бюджета, ₽</div><div className="boris-f2-be522d824950">Максимум трат за сутки</div>
                <input type="number" value={Number(advDailyLimit) === 0 ? "" : advDailyLimit} onChange={e => setAdvDailyLimit(e.target.value)} placeholder="1000"
  className="boris-f2-6d9f904b53ce"/>
              </div>
              <div>
                <div className="b-label">Максимальная ставка, ₽</div><div className="boris-f2-be522d824950">Жёсткий потолок: BORIS никогда не поднимет ставку выше него</div>
                <input type="number" min="1" step="1" value={Number(advHardMaxBid) === 0 ? "" : advHardMaxBid} onChange={e => setAdvHardMaxBid(e.target.value)} placeholder="40" className="boris-f2-6d9f904b53ce"/>
              </div>
              <Button className="boris-btn-hover" onClick={activateAdvisor} disabled={advisorLoading || !advMaxCpl || !advDailyLimit}
                style={{background: (!advMaxCpl || !advDailyLimit) ? "#C9D2E3" : "var(--boris-blue)", color:"var(--boris-surface)", border:"none", borderRadius:"8px", padding:"10px 20px", fontSize:"15px", fontWeight:"bold", cursor: (!advMaxCpl || !advDailyLimit) ? "not-allowed" : "pointer"}}>
                {(advisorData || advAutopilot) ? "💾 Сохранить" : "Активировать"}
              </Button>
            </div>
          </BorisCard>

          {advisorData && (
            <>
              {/* Сводка */}
              <div className="boris-f2-3f0e936bda76">
                <div className="boris-f2-c068c0b0ee12">
                  <div className="boris-f2-6c2427e9980a">Цель: цена лида</div>
                  <div className="boris-f2-603b3071dec1">≤ {advisorData?.target?.max_cpl_rub ?? "—"} ₽</div>
                </div>
                <div className="boris-f2-c068c0b0ee12">
                  <div className="boris-f2-6c2427e9980a">Факт: цена лида сегодня</div>
                  <div style={{fontSize:"22px", fontWeight:"bold", color: (advisorData?.fact?.account_cpl_rub && advisorData?.target?.max_cpl_rub && advisorData.fact.account_cpl_rub > advisorData.target.max_cpl_rub) ? "#F04438" : "#12B76A"}}>
                    {advisorData?.fact?.account_cpl_rub ?? "нет данных"} {advisorData?.fact?.account_cpl_rub ? "₽" : ""}
                  </div>
                </div>
                <div className="boris-f2-c068c0b0ee12">
                  <div className="boris-f2-6c2427e9980a">Контактов сегодня</div>
                  <div className="boris-f2-603b3071dec1">{advisorData?.fact?.contacts_today ?? 0}</div>
                </div>
              </div>

              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", gap:"12px", background: advAutopilot ? "#ECFDF3" : "#EFF4FF", border: advAutopilot ? "1px solid #A6F4C5" : "1px solid #D1E0FF", borderRadius:"8px", padding:"12px 14px", marginBottom:"18px", fontSize:"14px", color: advAutopilot ? "#027A48" : "var(--boris-blue)"}}>
                <div className="boris-f2-c47ffdad4342">
                  {advAutopilot && Number(advDailyLimit) > 0 && <span className="boris-f2-5c89e00ca1ea">🟢 Автопилот активен</span>}
                  {advAutopilot && Number(advDailyLimit) <= 0 && <span className="boris-f2-5c89e00ca1ea">🟠 Повышение ставок остановлено: суточный бюджет не задан</span>}
                  <span>{advAutopilot ? `BORIS проверяет ставки каждый час. Денежное повышение разрешено только при явно заданном суточном лимите; ставка не превысит ${advHardMaxBid || "заданный"} ₽. Для новых доступных продвижений BORIS ориентируется на минимальную ставку Avito и применяет защитный шаг.` : "ℹ️ Режим: только рекомендации (Борис ничего не меняет)."} {advisorData?.fact?.data_note || ""}</span>
                </div>
                <Button className="boris-btn-hover" onClick={toggleAutopilot} style={{whiteSpace:"nowrap", background: advAutopilot ? "var(--boris-surface)" : "var(--boris-blue)", color: advAutopilot ? "#F04438" : "var(--boris-surface)", border: advAutopilot ? "1px solid #F04438" : "none", borderRadius:"10px", padding:"8px 14px", cursor:"pointer", fontWeight:"bold", fontSize:"14px"}}>
                  {advAutopilot ? "⏹ Отключить автопилот" : "Включить автопилот ставок"}
                </Button>
              </div>
              <div className="boris-f2-fec5a3117a9c">{advisorData?.summary}</div>
              {advisorData?.owner_action && (
                <div style={{margin:"10px 0 16px", padding:"12px 14px", border:"1px solid var(--boris-border)", borderRadius:"10px", background:"var(--boris-surface-2)", fontSize:"14px"}}>
                  <div><b>Нужно ли ваше действие:</b> {humanizeBusinessText(advisorData.owner_action,"действие не требуется")}</div>
                  <div style={{marginTop:"4px", color:"var(--boris-text-2)"}}>{humanizeBusinessText(advisorData?.next_check_text,"BORIS продолжит проверку автоматически.")}</div>
                </div>
              )}
              <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))", gap:"10px", margin:"12px 0 18px"}}>
                <div style={{padding:"12px 14px", border:"1px solid var(--boris-border)", borderRadius:"10px", background:"var(--boris-surface)"}}>
                  <div style={{fontWeight:800, marginBottom:"4px"}}>Расходы Avito</div>
                  <div style={{fontSize:"13px", color:"var(--boris-text-2)"}}>
                    {advisorData?.fact?.spend_signal?.status === "ok"
                      ? `Подтверждено: ${Number(advisorData?.fact?.spend_signal?.spent_today_rub || 0).toFixed(0)} ₽ сегодня`
                      : `Повышение ставок закрыто: ${humanizeBusinessText(advisorData?.fact?.spend_signal?.reason,"расход за сегодня не подтверждён")}`}
                  </div>
                </div>
                <div style={{padding:"12px 14px", border:"1px solid var(--boris-border)", borderRadius:"10px", background:"var(--boris-surface)"}}>
                  <div style={{fontWeight:800, marginBottom:"4px"}}>Позиции в Avito</div>
                  <div style={{fontSize:"13px", color:"var(--boris-text-2)"}}>
                    {advisorData?.position_monitor?.state === "fresh"
                      ? `Измерено${advisorData?.position_monitor?.coverage_pct != null ? ` · покрытие ${advisorData.position_monitor.coverage_pct}%` : ""}`
                      : `Только чтение · ${humanizeBusinessText(advisorData?.position_monitor?.reason,"ожидаем подтверждённое измерение")}`}
                  </div>
                </div>
              </div>

              {/* Три блока рекомендаций */}
              {[
                {k:"raise", t:"🟢 Работают — можно поднять ставку", col:"#12B76A"},
                {k:"lower_or_archive", t:"🔴 Снизить ставку или в архив", col:"#F04438"},
                {k:"watching", t:"🟡 Наблюдаю — мало данных", col:"#F79009"},
              ].map(sec => {
                const list = advisorData?.recommendations?.[sec.k] || [];
                if (!list.length) return null;
                return (
                  <div key={sec.k} className="boris-f2-5b6aad9a3fd6">
                    <div style={{fontWeight:"bold", color:sec.col, marginBottom:"8px", fontSize:"16px"}}>{sec.t} ({list.length})</div>
                    <div className="boris-f2-55b86e49a4ce">
                      {list.map((it: any, i: number) => (
                        <div key={i} className="boris-f2-3b4a094a8e5e">
                          <div className="boris-f2-7623f0554520">
                            <div className="boris-f2-f362877bb2d3">{it.title}</div>
                            <div style={{display:"flex", flexWrap:"wrap", gap:"8px", marginTop:"5px", fontSize:"12px", color:"var(--boris-text-2)"}}>
                              <span>Текущая ставка: <b>{it.bid_rub ?? "—"} ₽</b></span>
                              <span>Минимум Avito: <b>{it.min_bid_rub ?? "—"} ₽</b></span>
                              {it.recommended_bid_rub != null && <span>Рекомендация Avito: <b>{it.recommended_bid_rub} ₽</b></span>}
                              <span>{it.promotion_active ? "Продвижение включено" : "Продвижение выключено"}</span>
                            </div>
                            <div className="boris-f2-a16cc290ede7"><b>Почему:</b> {it.why}{it.suggest ? ` → ${it.suggest}` : ""}</div>
                            <div style={{display:"grid", gap:"3px", marginTop:"7px", fontSize:"12px", color:"var(--boris-text-2)"}}>
                              <div><b>BORIS сделал:</b> {it.boris_action_text || "Ставку пока не менял"}</div>
                              <div><b>Следующая проверка:</b> {it.boris_next_check_at ? new Date(it.boris_next_check_at).toLocaleString("ru-RU") : "по часовому циклу BORIS"}</div>
                              {it.boris_measure_status === "waiting_measurement" && <div><b>Статус:</b> измеряю эффект изменения</div>}
                              {it.boris_measure_effect && <div><b>Результат замера:</b> {String(it.boris_measure_effect)}</div>}
                            </div>
                          </div>
                          <div className="boris-f2-e3e9bd53cada">
                            <div>👁 {it.views} · 📞 {it.contacts}</div>
                            <div>{it.bid_rub != null ? `${it.bid_rub} ₽` : "без ставки"}</div>
                            {(sec.k === "raise" || sec.k === "lower_or_archive") && (
                              <Button className="boris-btn-hover" onClick={() => applyAdvisorOne(it.id, sec.k === "raise" ? "raise" : "archive")}
                                style={{background: sec.k === "raise" ? "#12B76A" : "#F04438", color:"var(--boris-surface)", border:"none", borderRadius:"6px", padding:"4px 10px", cursor:"pointer", fontSize:"12px", fontWeight:"bold"}}>
                                {sec.k === "raise" ? "▲ Поднять" : "✕ Снять"}
                              </Button>
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
            <div className="boris-f2-150ee12d824b">Задайте цену лида и суточный лимит, нажмите «Активировать» — Борис проанализирует объявления.</div>
          )}
        </div>
      ) : directorMode ? (

        <div className="boris-f2-7a3756a5cd36">
          <div className="boris-f2-faeec7a3a7ea">
            <h2 className="boris-f2-c482369e57a1"><span className="b-icon-sm boris-f2-3402db1f72e2 boris-g1-icon">👑</span>Панель директора — все клиенты</h2>
            {null}
          </div>
          <div className="boris-f2-292c10b4285b">
            <div className="boris-f2-2ab7c34db4b0">
              <span className="boris-f2-3c176516799f">с</span>
              <input type="date" value={dsFrom} max={dsTo} onChange={e=>setDsFrom(e.target.value)}
  className="boris-f2-159a1738d4f8"/>
              <span className="boris-f2-3c176516799f">по</span>
              <input type="date" value={dsTo} min={dsFrom} onChange={e=>setDsTo(e.target.value)}
  className="boris-f2-159a1738d4f8"/>
              <Button onClick={()=>loadDirectorStatsRange(dsFrom, dsTo)} disabled={dsLoading}
 className="boris-f2-1f368c236d46">Показать</Button>
              {[["7д",7],["30д",30],["90д",90],["180д",180],["365д",365]].map(([label,d]:any)=>(
                <Button key={d} disabled={dsLoading} onClick={()=>{
                    const t=new Date(); const f=new Date(); f.setDate(t.getDate()-(d-1));
                    const iso=(x:Date)=>x.toISOString().slice(0,10);
                    setDsFrom(iso(f)); setDsTo(iso(t)); loadDirectorStatsRange(iso(f), iso(t));
                  }}
 className="boris-f2-9e801f70d186">{label}</Button>
              ))}
            </div>
            {dsLoading ? (
              <div className="boris-f2-96046ead62a3">Загрузка…</div>
            ) : dsData && dsData.totals ? (
              <>
                <div className="boris-f2-7b6f30baea06">
                  <div><div className="boris-f2-e8f44a95aa96">Показы</div>
                    <div className="boris-f2-0b60ea9ea94f">{dsData.totals.views}</div>
                    <div>{dsDelta(dsData.deltas?.views)}</div></div>
                  <div><div className="boris-f2-e8f44a95aa96">Контакты</div>
                    <div className="boris-f2-0b60ea9ea94f">{dsData.totals.contacts}</div>
                    <div>{dsDelta(dsData.deltas?.contacts)}</div></div>
                  <div><div className="boris-f2-e8f44a95aa96">Конверсия</div>
                    <div className="boris-f2-599e804904c7">{dsData.totals.conversion}%</div>
                    <div>{dsDelta(dsData.deltas?.conversion)}</div></div>
                </div>
                <div className="boris-f2-b0dbcd946e76">
                  Данные с {dsData.data_since || "—"} · наполнено {dsData.days_with_data} дн. из {dsData.days_requested} · сравнение с предыдущими {dsData.days_requested} дн. ({dsData.prev_days_with_data} дн. данных)
                </div>
              </>
            ) : (
              <div className="boris-f2-96046ead62a3">Нет данных за период</div>
            )}
            <div className="boris-f2-7e30d285d288">
              <Button className="boris-btn-hover boris-f2-dd00a862416c" title="Открывает витрину как гость, ПК+моб, разбор глазами клиента (тратит vision)" onClick={async()=>{try{await apiFetch("/api/qa/landing",{method:"POST"});alert("🌐 Аудит лендинга запущен. Отчёт придёт в Telegram через 1-2 минуты.");}catch(e){alert("Не удалось запустить: "+e);}}}>🌐 Аудит лендинга</Button>
              <Button className="boris-btn-hover boris-f2-0040eb377774" title="Бесплатная проверка всех вкладок кабинета" onClick={async()=>{try{await apiFetch("/api/qa/run",{method:"POST"});alert("⚡ Быстрый тест запущен. Отчёт придёт в Telegram через 2-3 минуты.");}catch(e){alert("Не удалось запустить: "+e);}}}>⚡ Быстрый тест</Button>
              <Button className="boris-btn-hover" title="Автоматический ежедневный быстрый тест" onClick={async()=>{const nv=!qaAuto;try{await apiFetch("/api/qa/auto",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:nv})});setQaAuto(nv);}catch(e){alert("Не удалось переключить: "+e);}}} style={{background: qaAuto ? "#12B76A" : "var(--boris-surface)", color: qaAuto ? "var(--boris-surface)" : "var(--boris-text-2)", border: qaAuto ? "1px solid #12B76A" : "1px solid var(--boris-border)", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>{qaAuto ? "🟢 Авто вкл" : "⚪ Авто выкл"}</Button>
              <Button className="boris-btn-hover boris-f2-515ee9c8c7e4" onClick={loadDirectorOverview} disabled={directorLoading}>
                {directorLoading ? "⏳ Загружаю..." : "🔄 Обновить"}
              </Button>
            </div>
          </div>

          <div className="boris-f2-cc35bba3875d">
            <div className="boris-f2-a794b88522b7">
              <div className="boris-f2-4081af7f7494">🆕 Новые клиенты {newClients.length > 0 && <span className="boris-f2-cab53acd9f90">{newClients.length}</span>}</div>
              <Button className="boris-btn-hover boris-f2-45463ef53533" onClick={() => kbOpenModal("global")}>Обучить Бориса</Button>
                <Button className="boris-btn-hover boris-f2-e755c2c41599" onClick={loadNewClients} disabled={newClientsLoading}>{newClientsLoading ? "⏳" : "🔄"}</Button>
            </div>
            {newClients.length === 0 ? (
              <div className="boris-f2-274e86856bbf">Пока никто не зарегистрировался.</div>
            ) : (
              <div className="boris-f2-b32a606a6817">
                {newClients.map((c: any) => {
                  const badge = c.status === "активна" ? {bg:"#E7F7EE", fg:"#12805C", label:"🟢 активна"} : c.status === "триал" ? {bg:"#FEF6E7", fg:"#B25E09", label:"🟡 триал"} : c.status === "истёк" ? {bg:"#FDEDEC", fg:"#C4320A", label:"🔴 истёк"} : {bg:"#F0F1F5", fg:"var(--boris-text-2)", label:"— нет подписки"};
                  const reg = c.registered_at ? new Date(c.registered_at).toLocaleDateString("ru-RU") : "—";
                  return (
                    <div key={c.id} className="boris-f2-0a84b392ec70">
                      <div className="boris-f2-526713208b4c">
                        <div className="boris-f2-ce826865b101">{c.email}</div>
                        <div className="boris-f2-274b6124e0e0">Регистрация: {reg} · Аккаунтов: {c.accounts_count}</div>
                      </div>
                      <div className="boris-f2-7e30d285d288">
                        {c.days_left !== null && c.days_left >= 0 && <span className="boris-f2-274b6124e0e0">ещё {c.days_left} дн.</span>}
                        <span style={{background:badge.bg, color:badge.fg, borderRadius:"20px", padding:"4px 12px", fontSize:"13px", fontWeight:700, whiteSpace:"nowrap"}}>{badge.label}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {escalations.length > 0 && (
            <div className="boris-f2-5d3727e62bbc">
              <div className="boris-f2-40eee6f1a1fd">
                <div className="boris-f2-b9c03fff328a">⚠️ Эскалации ошибок ({escalations.length})</div>
                <Button className="boris-btn-hover boris-f2-0045bf90441b" onClick={async () => {
                  await apiFetch("/api/avito/director/clear_escalations", {method:"POST"});
                  loadDirectorOverview();
                }}>
                  🗑 Очистить всё
                </Button>
              </div>
              <div className="boris-f2-529361f4274b">
                {escalations.map((e: any, i: number) => (
                  <div key={i} className="boris-f2-ec55c0f7447a">
                    <div className="boris-f2-7623f0554520">
                      <div><b className="boris-f2-b26be2f96183">{e.account_id}</b> — {humanizeBusinessText(e.task_type,"Задача BORIS")}</div>
                      <div className="boris-f2-f8c7e5877c2e">{humanizeBusinessText(e.error_message,"Техническая ошибка зафиксирована")}</div>
                      <div className="boris-f2-e1bdcf8ddaf7">{new Date(e.ts).toLocaleString("ru-RU")}</div>
                    </div>
                    <Button className="boris-btn-hover boris-f2-62dd9cb0b9b3" onClick={async () => {
                      await apiFetch("/api/avito/director/resolve_escalation", {
                        method:"POST",
                        headers:{"Content-Type":"application/json"},
                        body: JSON.stringify({ts: e.ts})
                      });
                      loadDirectorOverview();
                    }}>
                      ✓ Исправить
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {directorLoading && <p className="boris-f2-de52905d633b">Собираю сводку по всем аккаунтам...</p>}
          {!directorLoading && (() => {
            const paid = directorOverview.filter((a: any) => a.money?.total_rub > 0);
            const total = paid.reduce((s: number, a: any) => s + (a.money?.total_rub || 0), 0);
            const soon = directorOverview.filter((a: any) => {
              const d = a.payment?.days_left;
              return typeof d === "number" && d <= 7;
            });
            if (!total && !soon.length) return null;
            const box = (grad: string, val: string, cap: string) => (
              <div className="boris-f2-447425b64129">
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div className="boris-f2-4a3867b8b765">{val}</div>
                <div className="boris-f2-b703107f9e84">{cap}</div>
              </div>
            );
            return (
              <div className="boris-f2-4b22f818aac7">
                {box("linear-gradient(135deg,#32D583,#12805C)", total.toLocaleString("ru") + " \u20bd", "получено от клиентов")}
                {box("linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", String(paid.length), "клиентов платят")}
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
              <div className="boris-f2-447425b64129">
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div className="boris-f2-4a3867b8b765">{val}</div>
                <div className="boris-f2-b703107f9e84">{cap}</div>
              </div>
            );
            return (
              <div className="boris-f2-4b22f818aac7">
                {box("linear-gradient(135deg,#32D583,#12805C)", total.toLocaleString("ru") + " \u20bd", "получено от клиентов")}
                {box("linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", String(paid.length), "клиентов платят")}
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
              <div className="boris-f2-447425b64129">
                <div style={{position:"absolute", top:"-30px", right:"-30px", width:"90px", height:"90px", borderRadius:"50%", background:grad, opacity:0.08}} />
                <div className="boris-f2-4a3867b8b765">{val}</div>
                <div className="boris-f2-b703107f9e84">{cap}</div>
              </div>
            );
            return (
              <div className="boris-f2-4b22f818aac7">
                {box("linear-gradient(135deg,#32D583,#12805C)", total.toLocaleString("ru") + " \u20bd", "получено от клиентов")}
                {box("linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", String(paid.length), "клиентов платят")}
                {box("linear-gradient(135deg,#FDB022,#F79009)", String(soon.length), "оплата кончается за 7 дней")}
              </div>
            );
          })()}
          {!directorLoading && (
            <div className="boris-f2-b6a38b2ae32e">
              {directorOverview.map((acc: any) => (
                <div key={acc.account_id} onClick={() => { setCurrentAccount(acc.account_id); setDirectorMode(false); }}
                  className={"b-client-card" + (acc.latest_stats_date && Number(acc.balance?.real) < 500 ? " b-bal-crit" : acc.latest_stats_date && Number(acc.balance?.real) < 2000 ? " b-bal-warn" : "")}>
                  <div className="boris-f2-cd19185ab3a9">{acc.name}</div>
                  <div className="boris-f2-feedb2cbf60b">{acc.account_id}</div>
                  <div className="boris-f2-2b6ab878f831">
                    {acc.money && acc.money.total_rub > 0 && (
                      <div className="boris-f2-ae2e2dae4ffa">
                        <div><span className="boris-f2-de52905d633b">Заплатил всего: </span><b className="boris-f2-9b2d2d643310">{acc.money.total_rub.toLocaleString("ru")} ₽</b></div>
                        <div className="boris-f2-e41894b51ae0">
                          {acc.money.payments_count} {acc.money.payments_count === 1 ? "платёж" : "платежей"}
                          {acc.money.last_at ? ` · последний ${new Date(acc.money.last_at).toLocaleDateString("ru")}` : ""}
                          {acc.money.last_title ? ` · ${acc.money.last_title}` : ""}
                        </div>
                      </div>
                    )}
                    {acc.money && acc.money.total_rub > 0 && (
                      <div className="boris-f2-ae2e2dae4ffa">
                        <div><span className="boris-f2-de52905d633b">Заплатил всего: </span><b className="boris-f2-9b2d2d643310">{acc.money.total_rub.toLocaleString("ru")} ₽</b></div>
                        <div className="boris-f2-e41894b51ae0">
                          {acc.money.payments_count} {acc.money.payments_count === 1 ? "платёж" : "платежей"}
                          {acc.money.last_at ? ` · последний ${new Date(acc.money.last_at).toLocaleDateString("ru")}` : ""}
                          {acc.money.last_title ? ` · ${acc.money.last_title}` : ""}
                        </div>
                      </div>
                    )}
                    <div>
                      <span className="boris-f2-de52905d633b">Avito-ключи: </span>
                      {acc.has_avito_keys ? <span className="boris-f2-b5d95bf86ec9">✅ подключены</span> : <span className="boris-f2-de4c8a9423c7">⚠️ не настроены</span>}
                    </div>
                    {acc.latest_stats_date ? (
                      <>
                        <div><span className="boris-f2-de52905d633b">Баланс: </span><b style={{color: Number(acc.balance?.real) < 500 ? "#D92D20" : Number(acc.balance?.real) < 2000 ? "#B54708" : "var(--boris-blue)", fontSize:"17px"}}>{acc.balance?.real} ₽</b>{Number(acc.balance?.real) < 500 && <span className="boris-f2-c436fc998734">ПОПОЛНИТЬ</span>}</div>
                        <div><span className="boris-f2-de52905d633b">Объявлений: </span><b>{acc.items_count}</b></div>
                        <div><span className="boris-f2-de52905d633b">Запросов контактов сегодня: </span><b className="boris-f2-b5d95bf86ec9">{acc.contacts_today ?? 0}</b></div>
                        {acc.cost_per_contact != null && (
                          <div><span className="boris-f2-de52905d633b">Стоимость 1 контакта: </span><b className="boris-f2-b26be2f96183">{acc.cost_per_contact} ₽</b></div>
                        )}
                        <div className="boris-f2-316437256d37">Данные за {acc.latest_stats_date}</div>
                        {(() => {
                          const st = (dsData?.by_account || []).find((x:any) => x.account_id === acc.account_id);
                          if (!st) return null;
                          return (
                            <div className="boris-f2-7764b280c044">
                              <div className="boris-f2-4107528df4c6">За период · {dsDays} дн.</div>
                              <div className="boris-f2-e956bc196d6c">
                                <div><span className="boris-f2-de52905d633b">Показы: </span><b>{st.views}</b> {dsDelta(st.d_views)}</div>
                                <div><span className="boris-f2-de52905d633b">Контакты: </span><b>{st.contacts}</b> {dsDelta(st.d_contacts)}</div>
                                <div><span className="boris-f2-de52905d633b">Конверсия: </span><b className="boris-f2-b5d95bf86ec9">{st.conversion}%</b> {dsDelta(st.d_conversion)}</div>
                              </div>
                            </div>
                          );
                        })()}
                        <div className="boris-f2-3eb8cb462bef">
                          {acc.payment?.has_payment ? (
                            <div className="boris-f2-4fbdc40c7bc2">
                              <span style={{background: acc.payment.color === "red" ? "#FEE4E2" : acc.payment.color === "yellow" ? "#FEF0C7" : "#D1FADF", color: acc.payment.color === "red" ? "#B42318" : acc.payment.color === "yellow" ? "#B54708" : "#027A48", borderRadius:"20px", padding:"4px 12px", fontSize:"13px", fontWeight:"bold"}}>
                                {acc.payment.color === "red" ? "🔴" : acc.payment.color === "yellow" ? "🟡" : "🟢"} {acc.payment.overdue ? `Просрочено ${-acc.payment.days_left} дн.` : `осталось ${acc.payment.days_left} дн.`}
                              </span>
                              <Button className="boris-btn-hover boris-f2-641a64e88906" onClick={(e) => { e.stopPropagation(); setPayModalAccount(acc.account_id); setPayAmount(String(acc.payment.amount_rub||"")); setPayPeriod(String(acc.payment.period_days||30)); }}>✏️</Button>
                            </div>
                          ) : (
                            <Button className="boris-btn-hover boris-f2-36600955f4bb" onClick={(e) => { e.stopPropagation(); setPayModalAccount(acc.account_id); setPayAmount(""); setPayPeriod("30"); }}>💳 Отметить оплату</Button>
                          )}
                          {acc.payment?.has_payment && <div className="boris-f2-1ab7002bca97">до {acc.payment.paid_until} · {acc.payment.amount_rub}₽</div>}
                        </div>
                      </>
                    ) : (
                      <div className="boris-f2-316437256d37">Нет собранной статистики</div>
                    )}
                    <div className="boris-f2-9f2a6842b0ca">
                      <Button className="boris-btn-hover" onClick={(e) => { e.stopPropagation(); toggleUnlimited(acc.account_id, !!acc.billing?.unlimited); }}
                        style={{
                          background: acc.billing?.unlimited ? "#D1FADF" : "var(--boris-surface)",
                          color: acc.billing?.unlimited ? "#027A48" : "var(--boris-text-2)",
                          border: acc.billing?.unlimited ? "1px solid #6CE9A6" : "1px solid #D0D5DD",
                          borderRadius:"6px", padding:"4px 12px", cursor:"pointer", fontSize:"13px", width:"100%", fontWeight: acc.billing?.unlimited ? "bold" : "normal"
                        }}>
                        {acc.billing?.unlimited ? "🔓 Без ограничений (вкл)" : "🔒 По тарифу — включить безлимит"}
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
              {directorOverview.length === 0 && <p className="boris-f2-de52905d633b">Клиентов пока нет</p>}
            </div>
          )}
        </div>
      ) : walletMode ? (
        <div className="boris-f2-0389015cb36d">
          <h2 className="boris-f2-ad71f55a12c0">💰 Кошелёк</h2>
          <div className="boris-f2-6caa3780d4c0">Пополняйте баланс по счёту (для юрлиц и ИП) и покупайте любые услуги Бориса с баланса.</div>
          <div className="boris-f2-435027c2417d">
            <div className="boris-f2-e18d82f40a2a">
              <div className="boris-f2-02cdc8abfc90">Баланс кошелька</div>
              <div className="boris-f2-0578f6e090a5">{Number(balanceData.balance||0).toLocaleString("ru-RU")} ₽</div>
            </div>
            <div className="boris-f2-077dd48402da">
              <div className="boris-f2-0cfd15ff8345">Пополнить по счёту</div>
              <div className="boris-f2-b9bbe540d342">
                <input value={topupSum} onChange={e=>setTopupSum(e.target.value.replace(/[^0-9]/g,""))} placeholder="Сумма, ₽"  className="boris-f2-1836f42c12ad"/>
                <Button onClick={()=>{ if(!topupSum||Number(topupSum)<=0){alert("Укажите сумму");return;} setInvoicePack(""); (window as any)._topup=Number(topupSum); setInvoiceOpen(true); }} className="boris-f2-9b9b21e4359c">Выставить счёт</Button>
              </div>
            </div>
          </div>
          <div className="boris-f2-d71015481c00">
            <div className="boris-f2-518c8acb87cd">Все услуги Бориса — РОП, Менеджер, тарифы, баннеры — можно оплатить с баланса кошелька в разделе «Тарифы».</div>
            <Button onClick={()=>{ setWalletMode(false); setTariffsFinanceMode(true); }} className="boris-f2-94ed03f4b697">Купить услуги →</Button>
          </div>
          {userRole === "owner" && (
            <div className="boris-f2-1e1a9b09bf77">
              <div className="boris-f2-bebbec744221">
                <div className="boris-f2-0c5a266e650a">Счета клиентов (админ)</div>
                <Button onClick={loadInvoices} className="boris-f2-e93c8092f5e7">Обновить</Button>
              </div>
              {invoicesList.length===0 ? (
                <div className="boris-f2-e88a7eb51065">Счетов пока нет.</div>
              ) : (
                <div className="boris-f2-f67b86e0fb4c">
                  {invoicesList.map((inv:any, i:number)=>(
                    <div key={i} className="boris-f2-35c941e65b45">
                      <div className="boris-f2-c06ac9822ca4">
                        <div className="boris-f2-654558d879a0">{inv.number} · {Number(inv.amount||0).toLocaleString("ru-RU")} ₽</div>
                        <div className="boris-f2-b68dda3a3439">{inv.account_id} · {inv.payer_name||"—"} · {(inv.created_at||"").slice(0,10)}</div>
                      </div>
                      <div style={{fontSize:"12px", fontWeight:700, padding:"4px 10px", borderRadius:"20px", background: inv.status==="paid"?"#D1FADF":inv.status==="partial"?"#FEF0C7":"var(--boris-border-soft)", color: inv.status==="paid"?"#027A48":inv.status==="partial"?"#B54708":"var(--boris-text-2)"}}>
                        {inv.status==="paid"?"Оплачен":inv.status==="partial"?"Частично":"Ждёт оплаты"}
                      </div>
                      {inv.status!=="paid" && (
                        <Button onClick={()=>markPaid(inv.number, inv.amount)} className="boris-f2-ce4c073d011e">Деньги пришли</Button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="boris-f2-3a4859a19b0c">История операций</div>
          {(!balanceData.history || balanceData.history.length===0) ? (
            <div className="boris-f2-e88a7eb51065">Пока операций нет.</div>
          ) : (
            <div className="boris-f2-f67b86e0fb4c">
              {balanceData.history.map((h:any, i:number)=>(
                <div key={i} className="boris-f2-e3672c6151e4">
                  <div><div className="boris-f2-9a938ed79996">{h.note||h.type}</div><div className="boris-f2-b68dda3a3439">{(h.ts||"").slice(0,10)}</div></div>
                  <div style={{fontSize:"15px", fontWeight:700, color: (h.amount||0)>=0 ? "#027A48" : "#B42318"}}>{(h.amount||0)>=0?"+":""}{Number(h.amount||0).toLocaleString("ru-RU")} ₽</div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : requisitesMode ? (
        <div className="boris-f2-b7b1d39f59db">
          <h2 className="boris-f2-ad71f55a12c0">🏦 Реквизиты для счетов</h2>
          <div className="boris-f2-6caa3780d4c0">Эти данные Борис подставит в счета для оплаты по безналу от юрлиц и ИП. Заполните один раз.</div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">Наименование (ИП Остапенко Кирилл Олегович)</label><input value={requisites.org_name||""} onChange={e=>setRequisites({...requisites, org_name:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">ИНН</label><input value={requisites.inn||""} onChange={e=>setRequisites({...requisites, inn:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">ОГРНИП</label><input value={requisites.ogrnip||""} onChange={e=>setRequisites({...requisites, ogrnip:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">Адрес</label><input value={requisites.address||""} onChange={e=>setRequisites({...requisites, address:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">Банк</label><input value={requisites.bank_name||""} onChange={e=>setRequisites({...requisites, bank_name:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">Расчётный счёт</label><input value={requisites.bank_account||""} onChange={e=>setRequisites({...requisites, bank_account:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">БИК</label><input value={requisites.bank_bik||""} onChange={e=>setRequisites({...requisites, bank_bik:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">Корр. счёт</label><input value={requisites.corr_account||""} onChange={e=>setRequisites({...requisites, corr_account:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">E-mail</label><input value={requisites.email||""} onChange={e=>setRequisites({...requisites, email:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
<div className="boris-f2-a82d7062cc5e"><label className="boris-f2-465ca3ab2ea4">Телефон</label><input value={requisites.phone||""} onChange={e=>setRequisites({...requisites, phone:e.target.value})}  className="boris-f2-a5d6f949dc17"/></div>
          <div className="boris-f2-8540c054bace">
            <Button onClick={saveRequisites} className="boris-f2-8e980078655a">Сохранить реквизиты</Button>
            {reqSaved && <span className="boris-f2-7a2df4816de9">✓ Сохранено</span>}
          </div>
        </div>
      ) : tariffsFinanceMode ? (

        <div className="boris-f2-7a3756a5cd36">
          <h2 className="boris-f2-24cd9c46c930">💰 Тарифы и Финансы</h2>
          <p className="boris-f2-9362daca3207">Оплата тарифа подписки и докупка дополнительных опций сверх лимита.</p>

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
                {name:"1 баннер", price:"250 ₽", badge:"Точечно", badgeColor:"var(--boris-text-2)", icon:"🖼️", gradient:"linear-gradient(135deg, var(--boris-text-3), var(--boris-text-2))", invoiceId:"hlcZfzwZDUWWMhJitJ_wnQ", pack:"ban1"},
                {name:"10 баннеров", price:"1 800 ₽", badge:"Популярно", badgeColor:"var(--boris-blue)", icon:"🖼️", gradient:"linear-gradient(135deg, var(--boris-blue), var(--boris-blue))", invoiceId:"i_DhFWpOYEyAf120Y4Djzg", pack:"ban10"},
                {name:"30 баннеров", price:"5 100 ₽", badge:"💰 Выгодно", badgeColor:"#12805C", icon:"🖼️", gradient:"linear-gradient(135deg, #32D583, #12805C)", invoiceId:"scSJQwIYv0Ke4vDJwMjC3w", pack:"ban30"},
                {name:"50 баннеров", price:"8 000 ₽", badge:"Для каталога", badgeColor:"var(--boris-text-2)", icon:"🖼️", gradient:"linear-gradient(135deg, var(--boris-text-3), var(--boris-text-2))", invoiceId:"Lc7XU4driEuytzx6a9453w", pack:"ban50"},
                {name:"100 баннеров", price:"15 000 ₽", badge:"🔥 Максимум", badgeColor:"#F04438", icon:"🖼️", gradient:"linear-gradient(135deg, #FF6B6B, #F04438)", invoiceId:"gA5P5092U0KhSaT3diCv1Q", pack:"ban100"},
              ]
            },
            {
              title: "Баннеры для профиля магазина Avito",
              items: [
                {name:"Расширенный тариф (1 ПК + 1 моб)", price:"700 ₽", badge:"Базово", badgeColor:"var(--boris-text-2)", icon:"🏪", gradient:"linear-gradient(135deg, var(--boris-text-3), var(--boris-text-2))", invoiceId:"rpurvpER-UmZnPckeCCkiQ", pack:"prof_ext"},
                {name:"Максимальный тариф (3 ПК + 3 моб)", price:"2 100 ₽", badge:"Полный набор", badgeColor:"#7C5CFC", icon:"🏪", gradient:"linear-gradient(135deg, #9B87F5, #7C5CFC)", invoiceId:"O1Xeq-_fAkeAija1zkFpvw", pack:"prof_max"},
              ]
            },
            {
              title: "ИИ менеджер по продажам",
              items: [
                {name:"Базовый пакет — 1 700 сообщений", price:"9 000 ₽", badge:"Автопилот диалогов", badgeColor:"var(--boris-blue)", icon:"💬", gradient:"linear-gradient(135deg, var(--boris-blue), var(--boris-blue))", invoiceId:"lSJ7L0x6zkGbVb3KKO353w", pack:"msg1700"},
                {name:"Докупка +2 000 сообщений", price:"7 000 ₽", badge:"Без пауз", badgeColor:"var(--boris-text-2)", icon:"💬", gradient:"linear-gradient(135deg, var(--boris-text-3), var(--boris-text-2))", invoiceId:"P9e953qIq0yZ0cd2NKG_0A", pack:"msg2000"},
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
            <div key={section.title} className="boris-f2-2a28d13f8dfd">
              <h3 className="boris-f2-ef7dff1e71a6">{section.title}</h3>
              <div className="boris-f2-f3545665e745">
                {section.items.map(it => (
                  <BorisCard key={it.invoiceId} className="boris-card-hover boris-f2-0faba668ede8 boris-g1-card">
                    <div style={{position:"absolute", top:"-40px", right:"-40px", width:"120px", height:"120px", borderRadius:"50%", background:it.gradient, opacity:0.08}} />
                    <div style={{width:"64px", height:"64px", borderRadius:"50%", background:it.gradient, display:"flex", alignItems:"center", justifyContent:"center", fontSize:"28px", marginBottom:"16px", boxShadow:`0 8px 20px ${it.badgeColor}40`}}>{it.icon}</div>
                    <div className="boris-f2-b51f90c20057">{it.name}</div>
                    <div className="boris-f2-047eeaeb44f0">{(livePrices && (it as any).pack && livePrices[(it as any).pack]) ? livePrices[(it as any).pack].sum.toLocaleString("ru") + " ₽" + (String(it.price).includes("/мес") ? "/мес" : "") : it.price}</div>
                    {it.badge && (
                      <div style={{display:"inline-block", background:`${it.badgeColor}18`, color:it.badgeColor, fontSize:"13px", fontWeight:"bold", borderRadius:"20px", padding:"5px 14px", marginBottom:"22px"}}>{it.badge}</div>
                    )}
                    <div className="boris-f2-ff71126d36e1">
                      <RobokassaButton invoiceId={it.invoiceId} pack={(it as any).pack} accountId={currentAccount} />
                      <Button onClick={()=>buyWithBalance((it as any).pack, it.name)} className="boris-f2-ad5f71eb1a34">💰 Купить с баланса</Button>
                    </div>
                  </BorisCard>
                ))}
              </div>
            </div>
          ))}
        </div>

      ) : (
      <>

      {guideActive && guideSteps[guideIndex] && (
        <div className="boris-f2-7c4fb6e0c6db">
          <div className="boris-f2-1e0a8ff8c454">
            Борис-гид · шаг {guideIndex + 1} из {guideSteps.length}
          </div>
          <div className="boris-f2-4c377b747809">
            {guideSteps[guideIndex].instruction}
          </div>
          <div className="boris-f2-b9bbe540d342">
            <Button className="boris-btn-hover boris-f2-3da096912514" onClick={guideNext}>
              {guideIndex + 1 >= guideSteps.length ? "Готово ✓" : "Далее →"}
            </Button>
            <Button className="boris-btn-hover boris-f2-376583938b1d" onClick={guideSkip}>
              Пропустить всё
            </Button>
          </div>
        </div>
      )}

      <div className="boris-f2-7a3756a5cd36">

        {activeTab === "stats" && (
          <>
          <div className="boris-f2-bc6659ec37ea">
            <BorisCard className="b-card b-stat boris-f2-1e0cce60bae6">
              <span className="b-stat-ico boris-f2-79a4d50add5b boris-g1-card">📋</span>
              <div>
                <div className="b-meta boris-f2-870773467861">Всего объявлений</div>
                <BorisCard className="b-stat-num boris-g1-card"><CountUp to={items.length} /></BorisCard>
              </div>
            </BorisCard>
            <BorisCard className="b-card b-stat boris-f2-73edae6743ac">
              <span className="b-stat-ico boris-f2-9b8b21123da7 boris-g1-card">✅</span>
              <div>
                <div className="b-meta boris-f2-870773467861">Активных</div>
                <BorisCard className="b-stat-num boris-g1-card"><CountUp to={items.filter((i: any) => i.status === "active").length} delay={120} /></BorisCard>
              </div>
            </BorisCard>
            <BorisCard className="b-card b-stat boris-f2-1b00b1669e81">
              <Mascot size={26} interactive={false} />
              <div>
                <div className="b-meta boris-f2-870773467861">Статус Бориса</div>
                <div className="boris-f2-c8e24a0e7a57">
                  <span className="boris-f2-925142a329af"></span>
                  <span className="boris-f2-964049ce6339">Работает</span>
                </div>
              </div>
            </BorisCard>
          </div>

          <BorisCard className="b-panel boris-f2-3343fd6464ac">
            <div className="boris-f2-bcbb844d17df">
              <h3 className="b-title boris-f2-ea8ce7c7e635"><Mascot size={26} interactive={false} />Резюме и советы от Бориса</h3>
              <Button className="b-btn b-btn-ghost boris-f2-d39548ec2be4" onClick={loadWeeklySummary} disabled={summaryLoading}>
                {summaryLoading ? "⏳ Собираю..." : "🔄 Обновить"}
              </Button>
            </div>
            {summaryLoading && <p className="boris-f2-de52905d633b">Борис анализирует данные...</p>}
            {!summaryLoading && weeklySummary?.status === "ok" && (
              <>
                <p className="boris-f2-13378323fb95">{weeklySummary.summary}</p>
                {weeklySummary.data?.текущий_баланс && (
                  <div className="boris-f2-63e1329281d3">
                    <div><span className="boris-f2-de52905d633b">Баланс:</span> <b className="boris-f2-b5d95bf86ec9">{weeklySummary.data.текущий_баланс.real} ₽</b></div>
                    <div><span className="boris-f2-de52905d633b">Бонусы:</span> <b className="boris-f2-517924e0b69a">{weeklySummary.data.текущий_баланс.bonus}</b></div>
                    <div><span className="boris-f2-de52905d633b">Дней в анализе:</span> <b>{weeklySummary.data.дней_данных}</b></div>
                  </div>
                )}
                <div className="boris-f2-0a1d28e275a3">
                  <div>
                    <div className="boris-f2-e5b0b65cb8e9">🏆 Самые эффективные</div>
                    {(weeklySummary.data?.примеры_эффективных || []).map((it: any, i: number) => (
                      <div key={i} className="boris-f2-1108fcc45289">{it.title} <span className="boris-f2-b5d95bf86ec9">({it.conversion}%)</span></div>
                    ))}
                  </div>
                  <div>
                    <div className="boris-f2-b72baccd392f">📉 Требуют внимания</div>
                    {(weeklySummary.data?.примеры_неэффективных || []).map((it: any, i: number) => (
                      <div key={i} className="boris-f2-1108fcc45289">{it.title} <span className="boris-f2-de4c8a9423c7">({it.views} просм.)</span></div>
                    ))}
                  </div>
                </div>
              </>
            )}
            {!summaryLoading && weeklySummary?.status === "error" && (
              <p className="boris-f2-de52905d633b">{weeklySummary.message}</p>
            )}
          </BorisCard>
          </>
        )}

        {activeTab === "listings" && (
          <>
            <details className="boris-f2-2a94378c48e5">
              <summary className="boris-f2-c0e3ed53fc0c">❓ Как это работает</summary>
              <div className="boris-f2-107d6b8151e3">
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
                <div className="boris-f2-e0ebb78c5274">
                  <div className="boris-f2-fa67ab694c2a">
                    <div className="boris-f2-e59f0f96c3e5">С чего начать рекламу</div>
                    <div className="boris-f2-cb57d9bd0ff7">{_hint}</div>
                  </div>
                  <Button className="boris-btn-hover boris-f2-b0c191290bbd" onClick={_act}>{_label}</Button>
                </div>
              );
            })()}
            <div className="boris-f2-0cfd15ff8345">Отфильтруйте или отсортируйте объявления. «По контактам / конверсии / избранному» — это сортировки: нажмите, чтобы упорядочить список (повторное нажатие меняет направление ↓↑).</div>
            <div className="boris-f2-0cfd15ff8345">Отфильтруйте или отсортируйте объявления. «По контактам / конверсии / избранному» — это сортировки: нажмите, чтобы упорядочить список (повторное нажатие меняет направление ↓↑).</div>
            <div className="boris-f2-1ddb79e95c91">
              {[
                {key:"all", icon:"📋", label:"Все", grad:"linear-gradient(135deg,var(--boris-text-3),var(--boris-text-2))"},
                {key:"active", icon:"✅", label:"Активные", grad:"linear-gradient(135deg,#3DBE93,#12805C)"},
                {key:"inactive", icon:"❌", label:"Неактивные", grad:"linear-gradient(135deg,#F97066,#F04438)"},
                {key:"drafts", icon:"📝", label:"Черновики" + (draftItems.length > 0 ? ` (${draftItems.length})` : ""), grad:"linear-gradient(135deg,#FDB022,#F79009)"},
                {key:"search", icon:"🔍", label:"Поиск", grad:"linear-gradient(135deg,var(--boris-blue),var(--boris-blue))"},
                {key:"feeds", icon:"📡", label:"Фиды для Avito", grad:"linear-gradient(135deg,#7C5CFC,#5B3FD9)"},
                {key:"templates_lib", icon:"📄", label:"Шаблоны", grad:"linear-gradient(135deg,#F06AA8,#D93D86)"},
                {key:"contacts", icon:"📞", label:"По контактам", grad:"linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", sort:true},
                {key:"conversion", icon:"📈", label:"По конверсии", grad:"linear-gradient(135deg,#3DBE93,#0F9A6E)", sort:true},
                {key:"favorites", icon:"⭐", label:"По избранному", grad:"linear-gradient(135deg,#FDB022,#F79009)", sort:true},
                {key:"dupes", icon:"🆕", label:"Новые дубли" + (duplicateDrafts.length > 0 ? ` (${duplicateDrafts.length})` : ""), grad:"linear-gradient(135deg,#7C5CFC,#5B3FD9)", dupes:true},
                {key:"create", icon:"➕", label:"Создать объявление", grad:"linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", create:true},
                {key:"pipeline", icon:"🚀", label:"Конвейер направления", grad:"linear-gradient(135deg,#12805C,#3DBE93)", pipeline:true},
              ].map((f: any, i: number) => {
                const isOn = f.create || f.pipeline ? false : f.dupes ? showDuplicateDrafts : f.sort ? sortBy === f.key : listingFilter === f.key;
                return (
                <Button className="b-card b-stat b-fcard" key={f.key} style={{animationDelay:`${i * 0.05}s`, flexDirection:"column", justifyContent:"center", gap:"12px", minHeight:"124px", cursor:"pointer", background: f.create ? "linear-gradient(135deg,var(--boris-blue),var(--boris-blue))" : f.pipeline ? "linear-gradient(135deg,#12805C,#3DBE93)" : isOn ? "var(--boris-blue-soft)" : "var(--boris-surface)", borderColor: f.create ? "var(--boris-blue)" : f.pipeline ? "#12805C" : isOn ? "var(--boris-blue)" : "var(--boris-border)"}}
                  onClick={() => {
                    if (f.create) { window.location.href = "/dashboard/campaign"; return; }
                    if (f.pipeline) { setShowPipelineForm(true); return; }
                    if (f.dupes) { setShowDuplicateDrafts(v => !v); if (!showDuplicateDrafts) loadDuplicateDrafts(); return; }
                    if (f.sort) { if (sortBy === f.key) setSortDir(d => d === "desc" ? "asc" : "desc"); else { setSortBy(f.key); setSortDir("desc"); } return; }
                    setListingFilter(f.key);
                    if (f.key === "drafts") loadDrafts();
                    if (f.key === "feeds") loadAllFeeds();
                    if (f.key === "templates_lib") loadTemplateEffectiveness(currentAccount);
                  }}>
                  <span className="b-stat-ico boris-g1-card boris-g1-icon" style={{background: f.grad}}>{f.icon}</span>
                  <span style={{fontSize:"15px", fontWeight: (isOn || f.create) ? 800 : 600, color: f.create ? "var(--boris-surface)" : isOn ? "var(--boris-blue)" : "var(--boris-text-2)"}}>
                    {f.label}{f.sort && sortBy === f.key ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
                  </span>
                </Button>
                );
              })}
            </div>
            {selectedItemIds.size > 0 && (
              <div className="boris-f2-cd6ba7ccfb24">
                <span className="boris-f2-87b915590875">Выбрано: {selectedItemIds.size}</span>
                <Button className="boris-btn-hover boris-f2-bbb6b8ff2463" onClick={() => bulkAction("template")}>📝 Сделать шаблон</Button>
                <Button className="boris-btn-hover boris-f2-bbb6b8ff2463" onClick={() => bulkAction("archive")}>📦 Архивировать</Button>
                <Button className="boris-btn-hover boris-f2-43b1105761fa" onClick={() => bulkAction("delete")}>🗑 Удалить</Button>
                <Button className="boris-btn-hover boris-f2-5e59edcae124" onClick={clearItemSelection}>Снять выбор</Button>
              </div>
            )}

            {!duplicateEditingItem && !showDuplicateDrafts && (
            <>
            {listingFilter === "templates_lib" && (
              <div>
                <h3 className="boris-f2-1faa1f968015"><span className="b-icon-sm boris-f2-ac305422ad3b boris-g1-icon">📋</span>Библиотека шаблонов</h3>
                <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Про сохранённые тексты</summary><p className="boris-f2-183644454912">Сохранённые тексты объявлений. Борис отслеживает эффективность каждого по реальным лидам. Сохранить новый шаблон можно кнопкой «💾 Сохранить как шаблон» под сгенерированным объявлением.</p></details>
                {templateEff.length === 0 ? (
                  <div className="boris-f2-3259b97eab5f">
                    Пока нет сохранённых шаблонов. Сгенерируйте объявление и нажмите «💾 Сохранить как шаблон».
                  </div>
                ) : (
                  <div className="boris-f2-e0abfdb050e2">
                    {templateEff.map((t: any) => {
                      const st = t.status === "effective" ? {emoji:"🟢", label:"Эффективный", color:"var(--b-cat-leads)", bg:"#E7F7F0"}
                        : t.status === "ineffective" ? {emoji:"🔴", label:"Неэффективный", color:"#D92D20", bg:"#FEF0EF"}
                        : t.status === "in_progress" ? {emoji:"🟡", label:"В процессе", color:"var(--b-warn)", bg:"#FEF7EC"}
                        : {emoji:"⚪", label:"Нет данных", color:"var(--boris-text-2)", bg:"var(--boris-border-soft)"};
                      return (
                        <div key={t.id} className="boris-f2-dd820dd9c326">
                          <div className="boris-f2-fbafdde42320">
                            <div className="boris-f2-30b79cafdf6f">{t.name}</div>
                            <Button className="boris-btn-hover boris-f2-ed4eecd783ab" onClick={() => deleteTextTemplate(t.id)}>🗑</Button>
                          </div>
                          <div style={{display:"inline-block", background:st.bg, color:st.color, borderRadius:"6px", padding:"3px 10px", fontSize:"13px", fontWeight:"bold", marginBottom:"12px"}}>{st.emoji} {st.label}</div>
                          <div className="boris-f2-782bce7d3616">
                            <div className="boris-f2-c4ed1893af64">
                              <div className="boris-f2-85a2b3a910c8">Просмотры</div>
                              <div className="boris-f2-180c648529c8">{t.views}</div>
                            </div>
                            <div className="boris-f2-c4ed1893af64">
                              <div className="boris-f2-85a2b3a910c8">Контакты</div>
                              <div className="boris-f2-180c648529c8">{t.contacts}</div>
                            </div>
                            <div className="boris-f2-c4ed1893af64">
                              <div className="boris-f2-85a2b3a910c8">Конверсия</div>
                              <div className="boris-f2-8baa2a1e8844">{t.conversion}%</div>
                            </div>
                          </div>
                          <div className="boris-f2-85a2b3a910c8">Объявлений с этим шаблоном: {t.items_count}</div>
                          <div className="boris-f2-9ef106a8f548">{t.title_template}</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
            {listingFilter === "feeds" && (
              <div className="boris-f2-8a359a76ebec">
                <div className="boris-f2-3a27d53d2160">📡 Фиды для Avito — этот аккаунт</div>
                <div className="boris-f2-e376c4c86d14">Фид — это файл со всеми вашими объявлениями, который Avito автоматически забирает и публикует. Настраивать вручную не нужно: Борис собирает и обновляет его сам.</div>
                {feedsLoading ? <div className="boris-f2-de52905d633b">Загрузка...</div> : (
                  <div className="boris-f2-2e1ca338d758">
                    {allFeeds.map((feed, i) => (
                      <div key={i} className="boris-f2-5b6c5da6a641">
                        <div className="boris-f2-71e2cc7f2338">
                          <div className="boris-f2-8fe4d75b3829">
                            <div className="boris-f2-382adea18445">{feed.name}</div>
                            <div className="boris-f2-bdd5e7e2d132">📦 Объявлений: <b className="boris-f2-b5d95bf86ec9">{feed.count}</b> · Городов: {feed.cities_count}</div>
                            <div className="boris-f2-bdd5e7e2d132">📁 Категория: {(feed.categories || []).join(", ")}</div>
                            <div className="boris-f2-5f8aa513cec6">{feed.feed_url}</div>
                          </div>
                          <div className="boris-f2-312dfe557de1">
                            <Button className="boris-btn-hover boris-f2-d5cc3fa75958" onClick={() => copyFeedUrl(feed.feed_url)}>📋 Копировать ссылку</Button>
                            <Button className="boris-btn-hover" onClick={checkFeedValidation} disabled={feedCheckLoading} style={{background: feedCheckLoading ? "#C9D2E3" : "var(--boris-blue)", color:"var(--boris-surface)", border:"none", borderRadius:"10px", padding:"8px 14px", cursor: feedCheckLoading ? "default" : "pointer", fontSize:"14px", fontWeight:"bold"}}>{feedCheckLoading ? "⏳ Проверяю..." : "✅ Проверить фид"}</Button>
                            <Button className="boris-btn-hover boris-f2-71e7f8a2c363" onClick={() => checkFeed(feed.feed_url)} title="Скопировать ссылку и открыть валидатор Avito вручную">🔗 Проверить на Avito</Button>
                            <Button className="boris-btn-hover boris-f2-1679a09dd622" onClick={() => sendFeedToAvito(feed.account_id)}>🚀 Отправить в Avito</Button>
                            <Button className="boris-btn-hover boris-f2-a3c00bb6a8be" onClick={() => { if (openedFeedAccount === feed.account_id) { setOpenedFeedAccount(null); } else { setFeedTab("feed"); loadFeedItemsFull(feed.account_id); loadActiveItems(feed.account_id); } }}>✏️ Редактировать объявления</Button>
                            <Button className="boris-btn-hover boris-f2-c92397afccee" onClick={() => exportFeedXlsx(feed.account_id)}>📥 Выгрузить в Excel</Button>
                            <label className="boris-btn-hover boris-f2-93e9d9650a1f">📤 Загрузить из Excel<input type="file" accept=".xlsx" onChange={e => { if (e.target.files && e.target.files[0]) importFeedXlsx(feed.account_id, e.target.files[0]); }}  className="boris-f2-c8be1ccba60b"/></label>
                            <Button className="boris-btn-hover boris-f2-8fb43e47aa0e" onClick={() => importActiveFromAvito(feed.account_id)}>📥 Выгрузить активные с Avito</Button>
                          </div>
                        </div>
                        {openedFeedAccount === feed.account_id && (
                          <div className="boris-f2-be866ac5fc0e">
                            <div className="boris-f2-f94566b02aa1">
                              <Button className="boris-btn-hover" onClick={() => setFeedTab("feed")} style={{background: feedTab === "feed" ? "var(--boris-blue)" : "var(--boris-surface)", color: feedTab === "feed" ? "var(--boris-surface)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>📤 Мой фид ({feedItemsFull.length})</Button>
                              <Button className="boris-btn-hover" onClick={() => { setFeedTab("active"); loadActiveItems(feed.account_id); }} style={{background: feedTab === "active" ? "var(--boris-blue)" : "var(--boris-surface)", color: feedTab === "active" ? "var(--boris-surface)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>📥 Активные на Avito ({activeItems.length})</Button>
                              <Button className="boris-btn-hover" onClick={() => setFeedTab("schedule")} style={{background: feedTab === "schedule" ? "var(--boris-blue)" : "var(--boris-surface)", color: feedTab === "schedule" ? "var(--boris-surface)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"8px 16px", cursor:"pointer", fontSize:"14px", fontWeight:"bold"}}>🕐 Расписание</Button>
                            </div>
                            {feedTab === "schedule" && (
                              <BorisCard className="b-panel b-card-eq boris-f2-79a1c5a5db96 boris-g1-card">
                                <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-0f591da89e9c boris-g1-icon">🕐</span>Расписание публикаций</h3>

                                <div className="boris-f2-ccc494ba7417">
                                  <b>Отложенный старт подключён к реальному фиду Avito.</b><br/>
                                  Выберите объявления, дату и интервал. BORIS запишет для каждого объявления DateBegin и часовой пояс; Avito начнёт публикацию не раньше назначенного времени. Повторяющееся ежедневное правило не включается без отдельного решения владельца.
                                </div>

                                <div className="boris-f2-a4205867d71a">
                                  <div>
                                    <div className="b-label">Дата старта</div>
                                    <input type="date" value={bulkStartDate} onChange={e => setBulkStartDate(e.target.value)}  className="boris-f2-7b9b1c880bd1"/>
                                  </div>
                                  <div>
                                    <div className="b-label">Время старта</div>
                                    <input type="time" value={bulkStartTime} onChange={e => setBulkStartTime(e.target.value)}  className="boris-f2-7b9b1c880bd1"/>
                                  </div>
                                  <div>
                                    <div className="b-label">Интервал, мин</div>
                                    <input type="number" value={Number(bulkInterval) === 0 ? "" : bulkInterval} onChange={e => setBulkInterval(Number(e.target.value))}  className="boris-f2-7b9b1c880bd1"/>
                                  </div>
                                </div>

                                <p className="boris-f2-68a14f8ea8e2">Интервал между объявлениями нужен, чтобы Avito не увидел массовую загрузку — публикация растягивается во времени.</p>

                                <Button className="boris-btn-hover boris-f2-bc6d4c47a0a0" onClick={applyBulkSchedule}>🕐 Применить к выбранным</Button>
                              </BorisCard>
                            )}

                            {feedTab !== "schedule" && (feedTab === "feed" ? (
                              feedItemsLoading ? <div className="boris-f2-de52905d633b">Загрузка объявлений...</div> : (
                              <div className="boris-f2-f67b86e0fb4c">
                                <div className="boris-f2-ce3c551df9d5">Объявления, которые Борис создал и отдаёт на автозагрузку Avito.</div>
                                {feedItemsFull.map((it, k) => (
                                  <div key={k} className="boris-f2-2c5ebcda333d">
                                    <div className="boris-f2-e2f828e3f89e">
                                      <div className="boris-f2-56da0241e9ee">{it.title}</div>
                                      <div className="boris-f2-6c2427e9980a">{it.price} ₽ · {it.address}</div>
                                    </div>
                                    <div className="boris-f2-1d8943fa86ea">
                                      <Button className="boris-btn-hover boris-f2-e554ba5154c3" onClick={() => { setEditingFeedItem({...it}); loadFeedPhotoGallery(feed.account_id); loadTextTemplates(feed.account_id); }}>✏️ Править</Button>
                                      <Button className="boris-btn-hover boris-f2-9557399d8149" onClick={() => deleteFeedItem(feed.account_id, it.id)}>🗑</Button>
                                    </div>
                                  </div>
                                ))}
                                {feedItemsFull.length === 0 && <div className="boris-f2-de52905d633b">Нет объявлений в фиде</div>}
                              </div>
                              )
                            ) : (
                              activeLoading ? <div className="boris-f2-de52905d633b">Загрузка активных...</div> : (
                              <div className="boris-f2-f67b86e0fb4c">
                                <details className="boris-f2-a82d7062cc5e"><summary className="boris-f2-c0e3ed53fc0c">Подробнее</summary><div className="boris-f2-ce3c551df9d5">Объявления, которые уже опубликованы и живут на Avito (включая старые). Нажми «📥 Выгрузить активные с Avito» на карточке, чтобы обновить список.</div></details>
                                {activeItems.map((it, k) => (
                                  <div key={k} className="boris-f2-b47ca5a64610">
                                    <div className="boris-f2-e2f828e3f89e">
                                      <div className="boris-f2-56da0241e9ee">{it.title}</div>
                                      <div className="boris-f2-6c2427e9980a">{it.price} ₽ · {it.address}</div>
                                    </div>
                                    <div className="boris-f2-1d8943fa86ea">
                                      {it.avito_url && <a href={it.avito_url} target="_blank" rel="noreferrer" className="boris-f2-c59e275a49dc">🔗 На Avito</a>}
                                      <Button className="boris-btn-hover boris-f2-e554ba5154c3" onClick={() => { setEditingFeedItem({...it}); loadFeedPhotoGallery(feed.account_id); }}>✏️ Править</Button>
                                    </div>
                                  </div>
                                ))}
                                {activeItems.length === 0 && <div className="boris-f2-de52905d633b">Активных объявлений пока нет. Нажми «📥 Выгрузить активные с Avito».</div>}
                              </div>
                              )
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                    {allFeeds.length === 0 && <div className="boris-f2-de52905d633b">Фиды не найдены</div>}
                  </div>
                )}
              </div>
            )}
            {feedCheckResult && (
              <div onClick={() => setFeedCheckResult(null)} className="boris-f2-fb012a6f23e1">
                <div onClick={e => e.stopPropagation()} className="boris-f2-0ad877afc111">
                  <div className="boris-f2-bebbec744221">
                    <div className="boris-f2-6d20d2d2002c">
                      {feedCheckResult.status === "ok" ? "✅ Фид корректен" : feedCheckResult.status === "errors" ? "⚠️ Найдены ошибки" : "❌ Ошибка проверки"}
                    </div>
                    <Button onClick={() => setFeedCheckResult(null)} className="boris-f2-ff3ab6a95f75">×</Button>
                  </div>
                  <div className="boris-f2-d34e32a1d8f7">
                    {feedCheckResult.raw_text || "Нет данных"}
                  </div>
                </div>
              </div>
            )}
{addPhotoDraftId && (
              <div onClick={() => setAddPhotoDraftId(null)} className="boris-f2-3d1c6967b48f">
                <div onClick={e => e.stopPropagation()} className="boris-f2-f51ba28ba993">
                  <div className="boris-f2-a794b88522b7">
                    <span className="boris-f2-df3d5e383a28">Выберите фото для добавления</span>
                    <Button onClick={() => setAddPhotoDraftId(null)} className="boris-f2-a3baf4f66eda">×</Button>
                  </div>
                  {Object.keys(accountPhotos).length === 0 && <p className="boris-f2-de52905d633b">Нет загруженных фото у аккаунта.</p>}
                  {Object.entries(accountPhotos).map(([folder, imgs]: [string, any]) => (
                    <div key={folder} className="boris-f2-79a1c5a5db96">
                      <div className="boris-f2-60d0bb245376">{folder} ({imgs.length})</div>
                      <div className="boris-f2-eee7876950a8">
                        {imgs.map((img: string, i: number) => (
                          <img key={i} src={img.startsWith("http") ? img : ("https://boris-ai.pro" + img)} alt="" onClick={() => addPhotoToDraft(img)} title="Нажмите чтобы добавить"  className="boris-f2-fb0da273abb6"/>
                        ))}
                      </div>
                    </div>
                  ))}
                  <p className="boris-f2-e02d3ab73921">Нажмите на фото — оно добавится в объявление. Окно можно закрыть крестиком.</p>
                </div>
              </div>
            )}
            {lightboxImage && (
              <div onClick={() => setLightboxImage(null)} className="boris-f2-d27f9b35595c">
                <Button onClick={() => setLightboxImage(null)} className="boris-f2-9a58eb4feb71">×</Button>
                {lightboxGallery.length > 1 && (
                  <Button onClick={e => { e.stopPropagation(); const ni = (lightboxIdx - 1 + lightboxGallery.length) % lightboxGallery.length; setLightboxIdx(ni); setLightboxImage(lightboxGallery[ni]); }}
 className="boris-f2-2cab23b778df">‹</Button>
                )}
                <img src={lightboxImage} alt="Просмотр" onClick={e => e.stopPropagation()}  className="boris-f2-dbdf60aedc4f"/>
                {lightboxGallery.length > 1 && (
                  <Button onClick={e => { e.stopPropagation(); const ni = (lightboxIdx + 1) % lightboxGallery.length; setLightboxIdx(ni); setLightboxImage(lightboxGallery[ni]); }}
 className="boris-f2-45f336d638b3">›</Button>
                )}
                {lightboxGallery.length > 1 && <div className="boris-f2-b56e479aa0ed">{lightboxIdx + 1} / {lightboxGallery.length}</div>}
              </div>
            )}
            {editingFeedItem && (
              <div onClick={() => setEditingFeedItem(null)} className="boris-f2-84a756efbb89">
                <div onClick={e => e.stopPropagation()} className="boris-f2-014be6f5e8d0">
                  <div className="boris-f2-f25d861bbc56">✏️ Редактирование объявления в фиде</div>
                  <label className="boris-f2-fa9a6591c1c7">📋 Использовать шаблон (необязательно)</label>
                  <select className="b-select boris-f2-8ccf51308a88" value={editingFeedItem.template_id || ""} onChange={e => applyTextTemplate(e.target.value)}>
                    <option value="">— Написать своё описание —</option>
                    {textTemplates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                  <label className="boris-f2-fa9a6591c1c7">Заголовок</label>
                  <input value={editingFeedItem.title} onChange={e => setEditingFeedItem({...editingFeedItem, title: e.target.value})}  className="boris-f2-44b528d933b6"/>
                  <label className="boris-f2-fa9a6591c1c7">Цена</label>
                  <input type="number" value={Number(editingFeedItem.price) === 0 ? "" : editingFeedItem.price} onChange={e => setEditingFeedItem({...editingFeedItem, price: e.target.value})}  className="boris-f2-6ec949674c50"/>
                  <label className="boris-f2-fa9a6591c1c7">Описание</label>
                  <div className="boris-f2-e26d4e894c87">
                    <Button className="boris-btn-hover boris-f2-0b7236213eae" onClick={() => feedWrap("<strong>", "</strong>", "жирный")} title="Жирный">Ж</Button>
                    <Button className="boris-btn-hover boris-f2-9a79d12b10c1" onClick={() => feedWrap("<i>", "</i>", "курсив")} title="Курсив">К</Button>
                    <Button className="boris-btn-hover boris-f2-ac4beb4fee69" onClick={() => feedInsert("\n1. \n2. \n3. ")} title="Нумерованный список">1.</Button>
                    <Button className="boris-btn-hover boris-f2-ac4beb4fee69" onClick={() => feedInsert("\n• \n• \n• ")} title="Список точками">•</Button>
                    <Button className="boris-btn-hover boris-f2-ac4beb4fee69" onClick={() => feedInsert("\n— \n— \n— ")} title="Список чёрточками">—</Button>
                    <Button className="boris-btn-hover boris-f2-b01ad9e85d28" onClick={() => feedInsert("{вариант1|вариант2}")} title="Спинтакс уникализации">{"{|}"}</Button>
                    <Button className="boris-btn-hover boris-f2-ac4beb4fee69" onClick={() => setFeedEmojiOpen(v => !v)} title="Эмодзи">😀</Button>
                    {feedEmojiOpen && (
                      <div className="boris-f2-81464d48ce23">
                        {["📞","💬","✅","⭐","🔥","💰","🎁","📍","🚀","👍","❤️","🏆","⏰","📦","🛠","🔧","💡","🌟","✨","👉"].map((em, i) => (
                          <Button key={i} onClick={() => { feedInsert(em); setFeedEmojiOpen(false); }} className="boris-f2-f6bc36703206">{em}</Button>
                        ))}
                      </div>
                    )}
                  </div>
                  <textarea ref={feedDescRef} value={editingFeedItem.description} onChange={e => setEditingFeedItem({...editingFeedItem, description: e.target.value})} rows={10}  className="boris-f2-8c3c0d892745"/>
                  <label className="boris-f2-bba85c920562">📷 Фото объявления</label>
                  <div className="boris-f2-91229c80749d">
                    {(editingFeedItem.images || []).map((url: string, idx: number) => (
                      <div key={idx} className="boris-f2-c1638a7d8f0f">
                        <img src={url}  className="boris-f2-8c32cf250d55"/>
                        <Button onClick={() => removeFeedPhoto(idx)} className="boris-f2-2ac17bbe1147">×</Button>
                      </div>
                    ))}
                    {(!editingFeedItem.images || editingFeedItem.images.length === 0) && <span className="boris-f2-6774eea0e0db">Фото нет</span>}
                  </div>
                  <div className="boris-f2-3d775e0b2f0d">
                    <label className="boris-btn-hover boris-f2-5f9a2de107aa">📁 С компьютера<input type="file" accept="image/*" multiple onChange={e => { if (e.target.files && openedFeedAccount) uploadFeedPhoto(openedFeedAccount, e.target.files); }}  className="boris-f2-c8be1ccba60b"/></label>
                    <Button className="boris-btn-hover boris-f2-73e829628e6e" onClick={() => setFeedPhotoPickerOpen(feedPhotoPickerOpen === "banners" ? null : "banners")}>🎨 Из баннеров ({feedPhotoGallery.banners.length})</Button>
                    <Button className="boris-btn-hover boris-f2-73e829628e6e" onClick={() => setFeedPhotoPickerOpen(feedPhotoPickerOpen === "images" ? null : "images")}>🖼 Из картинок ({feedPhotoGallery.images.length})</Button>
                  </div>
                  {feedPhotoPickerOpen && (
                    <div className="boris-f2-321e6c9c05fe">
                      {(feedPhotoPickerOpen === "banners" ? feedPhotoGallery.banners : feedPhotoGallery.images).map((url, k) => (
                        <img key={k} src={url} onClick={() => addFeedPhoto(url)} title="Нажми чтобы добавить"  className="boris-f2-cd9ef6f8d942"/>
                      ))}
                      {(feedPhotoPickerOpen === "banners" ? feedPhotoGallery.banners : feedPhotoGallery.images).length === 0 && <span className="boris-f2-6774eea0e0db">Пусто</span>}
                    </div>
                  )}
                  <div className="boris-f2-6b00e75351fc">
                    <Button className="boris-btn-hover boris-f2-b808cb2238b2" onClick={saveFeedItem}>✅ Сохранить в фид</Button>
                    <Button className="boris-btn-hover boris-f2-9e1b414cf8c3" onClick={() => setEditingFeedItem(null)}>Отмена</Button>
                  </div>
                </div>
              </div>
            )}
            {listingFilter === "search" && (
              <input placeholder="🔍 Введите название объявления..." value={search} onChange={e => setSearch(e.target.value)}
  className="boris-f2-32aa9058655f"/>
            )}

            {false && borisFeedItems.length > 0 && (
              <div className="boris-f2-be45403022f5">
                <div className="boris-f2-40eee6f1a1fd">
                  <div className="boris-f2-53b999dd27bc">Объявления Бориса в фиде: {borisFeedItems.length}</div>
                  <Button className="boris-btn-hover boris-f2-529389c48629" onClick={() => loadBorisFeedItems()}>🔄 Обновить</Button>
                </div>
                <div className="boris-f2-9ade317dcdce">
                  Это объявления, уже готовые и сохранённые в фиде Бориса — они появятся выше как реальные объявления Avito, как только площадка обработает автозагрузку (обычно в течение часа).
                </div>
                <div className="boris-f2-b5121c37f248">
                  {borisFeedItems.slice(0, 20).map((it: any, i: number) => (
                    <div key={i} className="boris-f2-dc2b650a8739">
                      {it.title} — {it.price}₽ — {it.address} — <span className="boris-f2-de52905d633b">{it.category}</span>
                    </div>
                  ))}
                  {borisFeedItems.length > 20 && (
                    <div className="boris-f2-48ccb47b4e0a">...и ещё {borisFeedItems.length - 20}</div>
                  )}
                </div>
              </div>
            )}

            {listingFilter === "feeds" || listingFilter === "templates_lib" ? null : listingFilter === "drafts" ? (
              <>
                {draftsLoading ? <p className="boris-f2-de52905d633b">Загружаю черновики...</p> : draftItems.length === 0 ? (
                  <p className="boris-f2-943b91793f7d">📭 Черновиков нет. Дайте Борису задачу в режиме черновика — они появятся здесь.</p>
                ) : (
                  <>
                    {draftBatchFilter && (
                      <div className="boris-f2-d0dfa8007342">
                        <span className="boris-f2-b84f9c6fc10c">Показаны только результаты партии «{draftBatchFilter}»</span>
                        <Button className="boris-btn-hover boris-f2-560e10b26cb6" onClick={() => setDraftBatchFilter(null)}>Показать все</Button>
                      </div>
                    )}
                    {(() => { const visibleDrafts = draftBatchFilter ? draftItems.filter((d: any) => d.batch_label === draftBatchFilter) : draftItems; return (
                    <>
                    <div className="boris-f2-a6136bab2a36">
                      <div className="boris-f2-39ab2a622026">
                        <span className="boris-f2-870a64560bdb">📍 Публикация:</span>
                        <input placeholder="Город (напр. Москва)" value={selectedCity} onChange={e=>{setSelectedCity(e.target.value); setBulkCities([]); setBulkMetro([]);}}  className="boris-f2-bf71e9f7845e"/>
                        {metroData[selectedCity] && metroData[selectedCity].length>0 && (
                          <Button className="boris-btn-hover" onClick={selectAllMetroInCity} style={{background: bulkMetro.length>0 ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: bulkMetro.length>0 ? "var(--boris-blue)" : "var(--boris-text-2)", border:"1px solid var(--boris-blue)", borderRadius:"8px", padding:"6px 12px", cursor:"pointer", fontSize:"15px", fontWeight:"bold"}}>🚇 Раскидать по районам {bulkMetro.length>0 ? `(${bulkMetro.length})` : `(${metroData[selectedCity].length} станций)`}</Button>
                        )}
                        <span className="boris-f2-e88a7eb51065">|</span>
                        <label className="boris-f2-518c8acb87cd">📅 Дата:</label>
                        <input type="date" value={publishDate} onChange={e=>setPublishDate(e.target.value)}  className="boris-f2-3f2dd663be85"/>
                        <label className="boris-f2-518c8acb87cd">🕐 Время:</label>
                        <input type="time" value={publishTime} onChange={e=>setPublishTime(e.target.value)}  className="boris-f2-3f2dd663be85"/>
                        <label className="boris-f2-518c8acb87cd">🌍 Пояс:</label>
                        <select value={publishTimezone} onChange={e=>setPublishTimezone(e.target.value)} className="boris-f2-3f2dd663be85">
                          {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
                        </select>
                        {(bulkMetro.length>0 || bulkCities.length>0) && <span className="boris-f2-6eb5926fb7af">✓ адреса раскинутся по {bulkMetro.length>0 ? "районам" : "городам"}</span>}
                      </div>
                      <div className="boris-f2-96046ead62a3">Черновиков: {visibleDrafts.length}</div>
                      <Button className="boris-btn-hover boris-f2-673cceaca701" onClick={() => publishAllDrafts()}>✅ Опубликовать все ({draftItems.length})</Button>
                      <Button className="boris-btn-hover boris-f2-9f7abcd254bc" onClick={checkFeedValidation} disabled={feedCheckLoading}
>
                        🔍 {feedCheckLoading ? "Проверяю (до минуты)..." : "Проверить фид"}
                      </Button>
                      <Button className="boris-btn-hover boris-f2-89b3e059df62" onClick={applyPhotosToDrafts} disabled={draftPhotoLoading}
>
                        🖼 {draftPhotoLoading ? "Добавляю фото..." : "Добавить фото/баннеры"}
                      </Button>
                    </div>
                    <div className="boris-f2-4203436f0a4f">
                      {visibleDrafts.map((d: any) => (
                        <BorisCard key={d.id} className="boris-card-hover boris-f2-6e6bcbd330e1 boris-g1-card">
                          <div className="boris-f2-31d4dc6ae00a">{d.batch_label || "Без партии"}</div>
                          {editingDraftId === d.id ? (
                            <>
                              <input defaultValue={d.title} id={`draft-title-${d.id}`}  className="boris-f2-febd82c2c682"/>
                              <textarea defaultValue={d.description} id={`draft-desc-${d.id}`} rows={10}  className="boris-f2-c04b76655525"/>
                              <input type="number" defaultValue={Number(d.price) === 0 ? "" : d.price} id={`draft-price-${d.id}`}  className="boris-f2-76cbb7b6b669"/>
                              <div className="boris-f2-1d8943fa86ea">
                                <Button className="boris-btn-hover boris-f2-1f56e6c46d1d" onClick={() => {
                                    const title = (document.getElementById(`draft-title-${d.id}`) as HTMLInputElement)?.value;
                                    const description = (document.getElementById(`draft-desc-${d.id}`) as HTMLTextAreaElement)?.value;
                                    const price = Number((document.getElementById(`draft-price-${d.id}`) as HTMLInputElement)?.value);
                                    saveDraftEdit(d.id, { title, description, price });
                                  }}>Сохранить</Button>
                                <Button className="boris-btn-hover boris-f2-0661a736aa96" onClick={() => setEditingDraftId(null)}>Отмена</Button>
                              </div>
                            </>
                          ) : (
                            <>
                              <div className="boris-f2-51f1c576d8c7">{d.title}</div>
                              {d.images && d.images.length > 0 && (
                                <div className="boris-f2-e4ad4a163b5e">
                                  <div className="boris-f2-ead5ec20330b">
                                    {d.images.map((img: string, imgIdx: number) => (
                                      <div key={imgIdx} draggable onDragStart={() => setDragPhoto({draftId: d.id, idx: imgIdx})} onDragOver={e => e.preventDefault()} onDrop={() => { if (dragPhoto && dragPhoto.draftId === d.id) { reorderDraftImages(d.id, dragPhoto.idx, imgIdx); setDragPhoto(null); } }} className="boris-f2-21206b6e251d">
                                        <img src={img} alt="" onClick={() => { setLightboxGallery(d.images); setLightboxIdx(imgIdx); setLightboxImage(img); }} style={{width:"54px", height:"54px", objectFit:"cover", borderRadius:"6px", border: imgIdx===0 ? "2px solid #12B76A" : "1px solid var(--boris-border)", cursor:"pointer"}} title={imgIdx===0 ? "Главное фото (баннер) — перетащите фото чтобы изменить порядок" : "Клик = увеличить · перетащите чтобы изменить порядок"} />
                                        <Button onClick={() => removeDraftImage(d.id, imgIdx)} title="Удалить фото" className="boris-f2-de83079c8b08">×</Button>
                                      </div>
                                    ))}
                                  </div>
                                  <div className="boris-f2-1a5bd9156c8e">
                                    <span className="boris-f2-b68dda3a3439">🖼 {d.images.length} фото · зелёная рамка = главное · клик = увеличить</span>
                                    <Button onClick={() => openAddPhoto(d.id)} className="boris-f2-a228e6b86cc8">+ фото</Button>
                                  </div>
                                </div>
                              )}
                              <div style={{fontSize:"15px", color:"var(--boris-text-2)", marginBottom:"8px", flex:1, display:"-webkit-box", WebkitLineClamp:3, WebkitBoxOrient:"vertical" as any, overflow:"hidden", maxHeight:"66px", lineHeight:"22px"}} dangerouslySetInnerHTML={{__html: (d.description || "")
                                .replace(/</g, "&lt;").replace(/>/g, "&gt;")
                                .replace(/&lt;strong&gt;/g, "<strong style='color:var(--boris-text)'>").replace(/&lt;\/strong&gt;/g, "</strong>")
                                .replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>")
                                .replace(/&lt;ul&gt;/g, "<ul style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ul&gt;/g, "</ul>")
                                .replace(/&lt;ol&gt;/g, "<ol style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ol&gt;/g, "</ol>")
                                .replace(/&lt;li&gt;/g, "<li>").replace(/&lt;\/li&gt;/g, "</li>")
                                .replace(/&lt;br\s*\/?&gt;/g, "<br>").replace(/&lt;p&gt;/g, "<p style='margin:6px 0'>").replace(/&lt;\/p&gt;/g, "</p>")
                              }} />
                              <div className="boris-f2-6e7a55f05fca">{d.price} ₽ <span className="boris-f2-13ea4796e6c5">· {d.address}</span></div>
                              <div className="boris-f2-1d8943fa86ea">
                                <Button className="boris-btn-hover boris-f2-35c57ca487ae" onClick={() => publishDraft(d.id)}>✅ Опубликовать</Button>
                                <Button className="boris-btn-hover boris-f2-cd2e364b8a1f" onClick={() => setEditingDraftId(d.id)}>✏️</Button>
                                <Button className="boris-btn-hover boris-f2-24a22e67b472" onClick={() => deleteDraft(d.id)}>🗑</Button>
                              </div>
                            </>
                          )}
                        </BorisCard>
                      ))}
                    </div>
                    </>
                    ); })()}
                  </>
                )}
              </>
            ) : listingFilter === null ? (
              <div className="boris-f2-287ac94afd35">
                👆 Выберите фильтр выше («Все», «Активные», «Черновики» и т.д.), чтобы увидеть объявления
              </div>
            ) : (
              <>
            {listingFilter !== "feeds" && listingFilter !== "templates_lib" && (
            <div className="boris-f2-4a7df0e4aae1">
              <span>Показано: {getFilteredItems().length} объявлений</span>
              <Button className="boris-btn-hover boris-f2-529389c48629" onClick={() => setSelectedItemIds(new Set(getFilteredItems().map((it: any) => String(it.id))))}>☑ Выбрать все</Button>
              {selectedItemIds.size > 0 && (
                <Button className="boris-btn-hover boris-f2-529389c48629" onClick={() => setSelectedItemIds(new Set())}>✕ Снять выбор</Button>
              )}
            </div>
            )}

            {loading ? <p className="boris-f2-de52905d633b">Загружаю объявления...</p> : (
              <div className="boris-f2-60ff095509bd">
                {getFilteredItems().map((item: any) => (
                  <BorisCard key={item.id} className="boris-card-hover boris-g1-card" style={{background:"var(--boris-surface)", borderRadius:"10px", padding:"14px", border: selectedItemIds.has(String(item.id)) ? "1px solid var(--boris-blue)" : "1px solid #EEF2FA", boxShadow:"0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.04)", display:"flex", flexDirection:"column", height:"100%"}}>
                    <div className="boris-f2-8ca25de36f2d">
                      <div style={{fontSize:"15px", color:"var(--boris-text-2)", display:"-webkit-box", WebkitLineClamp:2, WebkitBoxOrient:"vertical" as any, overflow:"hidden", minHeight:"24px", flex:1, marginRight:"6px"}}>📍 {item.address}</div>
                      <input type="checkbox" checked={selectedItemIds.has(String(item.id))} onChange={() => toggleItemSelected(String(item.id))}  className="boris-f2-ccd87a40655a"/>
                    </div>
                    <div className="boris-f2-066e21f56938">{item.title}</div>
                    <div className="boris-f2-6d4a0677cda9">{item.price} ₽</div>
                    <div className="boris-f2-665cfdfa11bc">
                      <span>👁 {item.views || 0}</span>
                      <span>📞 {item.contacts || 0}</span>
                      <span>⭐ {item.favorites || 0}</span>
                      <span>📈 {item.conversion || 0}%</span>
                    </div>
                    <div className="boris-f2-5a9738ff9ea1">
                      <span style={{background: item.status === "active" ? "var(--boris-blue-soft)" : "#FDEDEC", color: item.status === "active" ? "#1E4FBF" : "#F04438", padding:"3px 9px", borderRadius:"20px", fontSize:"15px"}}>
                        {item.status === "active" ? "✅ Активно" : "❌ Неактивно"}
                      </span>
                      <a href={item.url} target="_blank" className="boris-f2-e9dd60ae5c61">Открыть →</a>
                    </div>
                    <Button className="boris-btn-hover boris-f2-595c9828687d" onClick={() => startDuplicate(item)}
>
                      🔁 Дублировать
                    </Button>
                    {myBorisItems.some((mi: any) => mi.id === String(item.id)) && (
                      <Button className="boris-btn-hover boris-f2-6f36e57a8101" onClick={() => startEditItem(myBorisItems.find((mi: any) => mi.id === String(item.id)))}>
                        ✏️ Править (опубликовано Борисом)
                      </Button>
                    )}
                  </BorisCard>
                ))}
              </div>
            )}
              </>
            )}

            {duplicateEditingItem && (
              <div className="boris-f2-bdd984a5751f">
                <Button className="boris-btn-hover boris-f2-28b7615c7757" onClick={() => setDuplicateEditingItem(null)}>← Назад к списку</Button>
                <h3 className="boris-f2-f7fa2916028b">🔁 Редактирование дубля</h3>

                <label className="boris-f2-5a0de0cf2264">Заголовок</label>
                <input value={duplicateEditingItem.title} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, title: e.target.value}))}
  className="boris-f2-939359cccc31"/>

                <label className="boris-f2-5a0de0cf2264">Описание</label>
                <div className="boris-f2-c6e850aae20c">
                  <Button className="boris-btn-hover boris-f2-11562b2a9997" onClick={() => wrapDescriptionSelection("<strong>", "</strong>", "жирный текст")} title="Жирный">Ж</Button>
                  <Button className="boris-btn-hover boris-f2-bfd4c449ecab" onClick={() => wrapDescriptionSelection("<i>", "</i>", "курсив")} title="Курсив">К</Button>
                  <Button className="boris-btn-hover boris-f2-53aec12f80a4" onClick={() => insertAtCursor("{вариант1|вариант2|вариант3}")} title="Спинтакс — оператор уникализации">{"{ | }"}</Button>
                  <Button className="boris-btn-hover boris-f2-bbb6b8ff2463" onClick={() => setShowEmojiPicker(v => !v)} title="Эмодзи">😀</Button>
                  {showEmojiPicker && (
                    <div className="boris-f2-565d6626d267">
                      {AD_EMOJIS.map(e => (
                        <Button className="boris-btn-hover boris-f2-b201388b836a" key={e} onClick={() => { insertAtCursor(e); setShowEmojiPicker(false); }}>{e}</Button>
                      ))}
                    </div>
                  )}
                </div>
                <textarea ref={descTextareaRef} value={duplicateEditingItem.description} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, description: e.target.value}))}
                  rows={5}  className="boris-f2-f9fe8a11aca2"/>

                <label className="boris-f2-5a0de0cf2264">Цена (₽)</label>
                <input type="number" value={Number(duplicateEditingItem.price) === 0 ? "" : duplicateEditingItem.price} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, price: Number(e.target.value)}))}
  className="boris-f2-ab147b296c0e"/>

                <div className="boris-f2-bd7a6315938c">
                  <Button className="boris-btn-hover" onClick={() => setRewriteEngine("gigachat")} style={{flex:1, background: rewriteEngine === "gigachat" ? "var(--boris-blue)" : "var(--boris-surface)", color: rewriteEngine === "gigachat" ? "var(--boris-bg)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"8px", cursor:"pointer", fontSize:"15px", fontWeight: rewriteEngine === "gigachat" ? "bold" : "normal"}}>Копирайтер Стандарт</Button>
                  <Button className="boris-btn-hover" onClick={() => setRewriteEngine("chatgpt")} style={{flex:1, background: rewriteEngine === "chatgpt" ? "var(--boris-blue)" : "var(--boris-surface)", color: rewriteEngine === "chatgpt" ? "var(--boris-bg)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"8px", cursor:"pointer", fontSize:"15px", fontWeight: rewriteEngine === "chatgpt" ? "bold" : "normal"}}>Копирайтер Премиум</Button>
                </div>
                <label className="boris-f2-81ab6a503433">
                  <input type="checkbox" checked={forceIncludeVariants} onChange={e => setForceIncludeVariants(e.target.checked)}  className="boris-f2-16a162db1be5"/>
                  🎨 Принудительно добавить блоки характеристик/цвета (RAL) в конец описания
                </label>
                <Button className="boris-btn-hover" onClick={rewriteDraftText} disabled={rewriteLoading}
                  style={{width:"100%", background: rewriteLoading ? "var(--boris-border)" : "var(--boris-blue)", color:"var(--boris-text)", border:"none", borderRadius:"8px", padding:"12px", fontWeight:"bold", cursor: rewriteLoading ? "wait" : "pointer", fontSize:"23px", marginBottom:"16px"}}>
                  {rewriteLoading ? "⏳ Борис переписывает текст..." : "🔄 Борис перепишет текст эффективнее"}
                </Button>

                <label className="boris-f2-2d4f4606389a">
                  <input type="checkbox" checked={!!duplicateEditingItem.is_template} onChange={e => setDuplicateEditingItem((prev: any) => ({...prev, is_template: e.target.checked}))}  className="boris-f2-ccd87a40655a"/>
                  📝 Также сохранить как шаблон
                </label>

                <label className="boris-f2-5a0de0cf2264">Фотографии (выбери из галереи)</label>
                <div className="boris-f2-4dd2dd07b7fe">
                  {Object.entries(allFolders).flatMap(([folder, urls]: [string, any]) =>
                    (urls as string[]).map((url: string) => (
                      <div key={url} onClick={() => toggleDraftImage(url)} style={{position:"relative", width:"70px", height:"70px", borderRadius:"6px", overflow:"hidden", cursor:"pointer", border: (duplicateEditingItem.images || []).includes(url) ? "3px solid var(--boris-blue)" : "1px solid var(--boris-border)"}}>
                        <img src={url}  className="boris-f2-b1279a8389d1"/>
                        {(duplicateEditingItem.images || []).includes(url) && (
                          <div className="boris-f2-124d97d1aaec">✓</div>
                        )}
                      </div>
                    ))
                  )}
                  {Object.keys(allFolders).length === 0 && <div className="boris-f2-316437256d37">Галерея пуста — загрузите фото во вкладке «Выгрузка с сайта».</div>}
                </div>
                <div className="boris-f2-c78ddedb8b24">
                  Выбрано фото: {(duplicateEditingItem.images || []).length}. Перетащите выбранные фото ниже, чтобы задать порядок на Avito.
                </div>
                {(duplicateEditingItem.images || []).length > 0 && (
                  <div style={{display:"flex", flexWrap:"wrap", gap:"10px", margin:"10px 0 16px"}}>
                    {(duplicateEditingItem.images || []).map((url: string, index: number) => (
                      <div
                        key={url}
                        draggable
                        onDragStart={e => e.dataTransfer.setData("text/plain", url)}
                        onDragOver={e => e.preventDefault()}
                        onDrop={e => { e.preventDefault(); reorderDraftImage(e.dataTransfer.getData("text/plain"), url); }}
                        title="Перетащите, чтобы изменить порядок"
                        style={{position:"relative", width:"82px", height:"82px", borderRadius:"8px", overflow:"hidden", cursor:"grab", border:"2px solid var(--boris-blue)", background:"var(--boris-surface)"}}
                      >
                        <img src={url} alt={`Фото ${index + 1}`} style={{width:"100%", height:"100%", objectFit:"cover"}}/>
                        <div style={{position:"absolute", left:"5px", top:"5px", minWidth:"24px", height:"24px", padding:"0 6px", borderRadius:"12px", background:"rgba(0,0,0,.72)", color:"#fff", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"12px", fontWeight:800}}>{index + 1}</div>
                      </div>
                    ))}
                  </div>
                )}

                <Button className="boris-btn-hover" onClick={saveDuplicateDraft} disabled={savingDraft}
                  style={{width:"100%", background: savingDraft ? "var(--boris-border)" : "var(--boris-blue)", color:"var(--boris-bg)", border:"none", borderRadius:"8px", padding:"12px", fontWeight:"bold", cursor: savingDraft ? "wait" : "pointer", fontSize:"23px"}}>
                  {savingDraft ? "⏳ Сохраняю..." : "✅ Сохранить черновик"}
                </Button>
              </div>
            )}

            {showDuplicateDrafts && !duplicateEditingItem && (
              <div>
                <h3 className="boris-f2-f7fa2916028b">🆕 Новые дубли — черновики в работе</h3>
                {duplicateDrafts.length === 0 ? (
                  <p className="boris-f2-de52905d633b">Пока нет черновиков-дублей. Нажми «🔁 Дублировать» на любом объявлении.</p>
                ) : (
                  <div className="boris-f2-e0abfdb050e2">
                    {duplicateDrafts.map((d: any) => (
                      <div key={d.draft_id} className="boris-f2-8ca8ff789792">
                        <div className="boris-f2-bca662eb31d9">{d.title}</div>
                        <div className="boris-f2-a6dd61d4390b">{d.created_at}{d.is_template ? " · 📝 шаблон" : ""}</div>
                        <div className="boris-f2-ea8b084d2968">{d.price} ₽</div>
                        <div className="boris-f2-b9bbe540d342">
                          <Button className="boris-btn-hover boris-f2-19a58890b3e3" onClick={() => setDuplicateEditingItem(d)}>✏️ Продолжить</Button>
                          <Button className="boris-btn-hover boris-f2-e1068172a030" onClick={() => deleteDuplicateDraft(d.draft_id)}>🗑</Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {pipelineHistory.length > 0 && (
              <BorisCard className="b-panel boris-f2-3343fd6464ac">
                <div className="b-title">🚀 История конвейера направления</div>
                <div className="b-sub">Аккаунт: {currentAccount} — только его запуски, независимо от того, что крутится в других аккаунтах.</div>
                <div className="boris-f2-167ac309cd9b">
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
                      <BorisCard key={item.batchId} className="b-panel b-card-eq boris-f2-0a595cb511d5 boris-g1-card">
                        <div className="boris-f2-0db0719fbfe0">
                          <span className="b-icon-sm boris-f2-042ef8607ebd boris-g1-icon">📦</span>
                          <div className="boris-f2-5a316b8cb6a1">Пакет из {runs.length} направлений</div>
                        </div>
                        <div className="boris-f2-bcf8299c1d0e">
                          ✅ {doneCount} готово{waitingCount ? ` · ⏸ ${waitingCount} ждут подтверждения` : ""}{runningCount ? ` · 🔵 ${runningCount} в работе` : ""}{queuedCount ? ` · 🕓 ${queuedCount} в очереди` : ""}{errorCount ? ` · ❌ ${errorCount} с ошибкой` : ""}
                        </div>
                        <div className="boris-f2-dc5ddb322cef">
                          <div style={{width:`${allDone ? 100 : Math.round((doneCount / runs.length) * 100)}%`, height:"100%", background:"var(--b-cat-diag)", borderRadius:"6px", transition:"width 0.6s ease"}} />
                        </div>
                        <div className="boris-f2-c317fec12a8e">
                          {runs.map((run: any) => renderPipelineRunCard(run, true))}
                        </div>
                      </BorisCard>
                    );
                  })}
                </div>
              </BorisCard>
            )}
          </>
        )}
          </>
        )}

        {activeTab === "templates" && (
          <div>
            {generatorStorageError && <p role="alert" style={{color:"#B42318"}}>{generatorStorageError}</p>}
            <div className="boris-f2-d90da07dd31d">
              <h3 className="boris-f2-5a9a6dee1167">📝 Шаблоны объявлений</h3>
              <Button className="boris-btn-hover boris-f2-1cec6a943c82" onClick={() => setShowTemplateForm(true)}>+ Новый шаблон</Button>
            </div>
            <div className="boris-f2-f4f0cb080183">
              <h3 className="b-title boris-f2-7e30d285d288"><Mascot size={26} interactive={false} />Генератор объявлений через ИИ</h3>
              <p className="b-sub">Подготовьте текст на основе фактов о вашем товаре или услуге. Проверьте результат перед публикацией.</p>
              <div className="boris-f2-f6b9c08f5c7e">
                <div>
                  <label className="boris-f2-5a0de0cf2264">Тема / услуга</label>
                  <input value={genTopic} onChange={e => setGenTopic(e.target.value)} placeholder="Например: Поздравления в стихах на юбилей" style={inputStyle} />
                </div>
                <div>
                  <label className="boris-f2-5a0de0cf2264">Сколько штук</label>
                  <input type="number" value={Number(genCount) === 0 ? "" : genCount} onChange={e => setGenCount(Number(e.target.value))} min={1} max={30} style={inputStyle} />
                </div>
              </div>
              <div className="boris-f2-df9c7b56906a">
                <div>
                  <label className="boris-f2-5a0de0cf2264">Цена от (руб)</label>
                  <input type="number" value={genPriceFrom || ""} onChange={e => setGenPriceFrom(Number(e.target.value) || 0)} min={0} style={inputStyle} />
                </div>
                <div>
                  <label className="boris-f2-5a0de0cf2264">Цена до (руб)</label>
                  <input type="number" value={genPriceTo || ""} onChange={e => setGenPriceTo(Number(e.target.value) || 0)} min={0} style={inputStyle} />
                    <div className="boris-f2-314f95d6c1dc">💡 Для товаров обычно нужна одна конкретная цена, а не диапазон — впишите одинаковое число в оба поля.</div>
                </div>
              </div>
              <div className="boris-f2-79a1c5a5db96">
                <label className="boris-f2-5a0de0cf2264">О компании / акции / особенности (Борис учтёт это)</label>
                <textarea value={genExtra} onChange={e => setGenExtra(e.target.value)} placeholder="Например: работаем 10 лет, скидка пенсионерам 10%, бесплатная доставка по МО" rows={3} style={{...inputStyle, resize:"vertical", fontFamily:"inherit"}} />
                <label className="b-label boris-f2-e7673d9ced19">📋 Образец / пожелания к шаблону <span className="boris-f2-de10b07e3112">— вставь пример объявления или бриф: Борис возьмёт оттуда общую информацию и стиль, а факты о товаре возьмёт из ваших данных</span></label>
                <textarea className="b-input boris-f2-90afa77b7cd3" value={genSample} onChange={e => setGenSample(e.target.value)} placeholder="Например: вставь текст удачного объявления конкурента или описание компании — Борис учтёт структуру, тон и общие факты, но переработает под каждый товар" rows={5} />
              </div>
              <div className="boris-f2-79a1c5a5db96">
                <label className="boris-f2-5a0de0cf2264">Длина описаний</label>
                <div className="boris-f2-0889360d6347">
                  {[{k:"short",l:"📄 Короткие"},{k:"medium",l:"📃 Средние"},{k:"long",l:"📜 Длинные"}].map(g => (
                    <Button className="boris-btn-hover" key={g.k} onClick={() => setGenLength(g.k)} style={{padding:"8px 16px", borderRadius:"10px", border: genLength===g.k ? "1px solid var(--boris-blue)" : "1px solid var(--boris-border)", background: genLength===g.k ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: genLength===g.k ? "var(--boris-blue)" : "var(--boris-text-2)", cursor:"pointer", fontSize:"15px", fontWeight: genLength===g.k ? "bold" : "normal"}}>{g.l}</Button>
                  ))}
                </div>
              </div>
              <label className="boris-f2-66a666fd88d9">
                <input type="checkbox" checked={useMyAds} onChange={e => setUseMyAds(e.target.checked)}  className="boris-f2-ccd87a40655a"/>
                📊 Учитывать стиль моих активных объявлений (Борис возьмёт образцы из Авито)
              </label>
              <div className="boris-f2-79a1c5a5db96">
                <label className="boris-f2-5a0de0cf2264">Цель объявления — что должен сделать клиент?</label>
                <div className="boris-f2-0889360d6347">
                  {[{k:"call",l:"📞 Позвонить"},{k:"message",l:"💬 Написать в чат"},{k:"order",l:"🛒 Сразу заказать"},{k:"visit",l:"📍 Прийти / замер"}].map(g => (
                    <Button className="b-btn" key={g.k} onClick={() => setGenGoal(g.k)} style={{border: genGoal===g.k ? "1px solid var(--boris-blue)" : "1px solid var(--boris-border)", background: genGoal===g.k ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: genGoal===g.k ? "var(--boris-blue)" : "var(--boris-text-2)", fontWeight: genGoal===g.k ? 700 : 500}}>{g.l}</Button>
                  ))}
                </div>
              </div>
              <Button className="b-btn b-btn-primary" onClick={() => setShowConfirm(true)} disabled={genLoading} style={{background: genLoading ? "var(--boris-border)" : "var(--boris-blue)", borderColor: genLoading ? "var(--boris-border)" : "var(--boris-blue)", cursor: genLoading ? "wait" : "pointer", padding:"12px 24px"}}>
                {genLoading ? "Борис генерирует…" : "✨ Сгенерировать объявления"}
              </Button>
{showConfirm && (
  <div onClick={() => setShowConfirm(false)} className="boris-f2-40ee1e11790e">
    <div onClick={(e:any) => e.stopPropagation()} className="boris-f2-33631c931988">
      <h3 className="boris-f2-ab7a0549a1dc">Проверьте перед генерацией</h3>
      <div className="boris-f2-aa5939599233">
        <div><b className="boris-f2-de52905d633b">Тема:</b> {genTopic || "—"}</div>
        <div><b className="boris-f2-de52905d633b">Цена:</b> {genPriceFrom || genPriceTo ? `${genPriceFrom||0} – ${genPriceTo||0} ₽` : "не задана"}</div>
        <div><b className="boris-f2-de52905d633b">О компании:</b> {genExtra ? genExtra.slice(0,60)+(genExtra.length>60?"…":"") : "—"}</div>
        <div><b className="boris-f2-de52905d633b">Количество:</b> {genCount}</div>
        <div><b className="boris-f2-de52905d633b">Длина:</b> {genLength === "short" ? "Короткие" : genLength === "long" ? "Длинные" : "Средние"}</div>
        <div><b className="boris-f2-de52905d633b">Цель:</b> {genGoal === "call" ? "📞 Позвонить" : genGoal === "message" ? "💬 Написать" : genGoal === "order" ? "🛒 Заказать" : "📍 Прийти"}</div>
        <div><b className="boris-f2-de52905d633b">Стиль моих объявлений:</b> {useMyAds ? "учитывать" : "не учитывать"}</div>
      </div>
      <div className="boris-f2-2c5b241b3608">
        <Button className="boris-btn-hover boris-f2-b68f130f1f82" onClick={() => { setShowConfirm(false); generateAds(); }}>✅ Всё верно, генерировать</Button>
        <Button className="boris-btn-hover boris-f2-cc812ecc9f36" onClick={() => setShowConfirm(false)}>Изменить</Button>
      </div>
    </div>
  </div>
)}

              {genAdsHistory.length > 0 && (
                <div className="boris-f2-e0c7755d9873">
                  <div className="boris-f2-35411fbf6eb4">📚 История партий шаблонов ({genAdsHistory.length})</div>
                  <div className="boris-f2-e03a69f985d6">
                    {genAdsHistory.map((batch: any) => {
                      const eff = batchEffectiveness[batch.id];
                      return (
                        <div key={batch.id} className="boris-f2-104b57bf4d1a">
                          <div className="boris-f2-7623f0554520">
                            <div><b>{batch.topic}</b> <span className="boris-f2-6607ac287d11">— {batch.ads?.length || 0} шт.</span></div>
                            <div className="boris-f2-316437256d37">{new Date(batch.created_at).toLocaleString("ru-RU")}</div>
                            {eff && eff.status === "ok" && (
                              <div className="boris-f2-0f13f0ec1909">
                                Эффективность: <b style={{color: eff.level === "Высокая" ? "var(--boris-blue)" : eff.level === "Средняя" ? "#F79009" : "#F04438"}}>{eff.level}</b>
                                {" "}(конверсия {eff.conversion}%, {eff.contacts} обращений за {eff.days} дн.)
                              </div>
                            )}
                            {eff && eff.status === "no_data" && (
                              <div className="boris-f2-4baad7265b0c">Нет данных — объявления из этой партии ещё не публиковались или статистика не собрана</div>
                            )}
                          </div>
                          <div className="boris-f2-1d8943fa86ea">
                            <Button className="boris-btn-hover boris-f2-35f87f7a34b3" onClick={() => checkBatchEffectiveness(batch.id)}>📊 Эффективность</Button>
                            <Button className="boris-btn-hover boris-f2-59834f70617c" onClick={() => setGenAds(batch.ads)}>↩ Загрузить</Button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {genAds.length > 0 && (
                <div className="boris-f2-3343fd6464ac">
                  <div className="b-title boris-f2-2aa22d63b379"><span className="b-icon-sm boris-f2-886256c1d4fc boris-g1-icon">✅</span>Готово! Сгенерировано: {genAds.length}</div>
                  <BorisCard className="b-panel boris-f2-cd1644160ace">
                    <span className="b-label boris-f2-1da9facb4d70">🔁 Размножить до:</span>
                    <input type="number" value={Number(cloneTarget) === 0 ? "" : cloneTarget} onChange={e => setCloneTarget(Math.max(1, Number(e.target.value)||1))} style={{...inputStyle, width:"90px"}} />
                    <Button className="b-btn boris-f2-cba9d2a49a1f" onClick={cloneAds}>🔁 Размножить</Button>
                    <Button className="b-btn boris-f2-af8bb1646461" onClick={saveGenBatch}>💾 Сохранить партию</Button>
                    <Button className="b-btn b-btn-ghost" onClick={() => { if (!feedUrl) { alert("Сначала отправьте партию в фид."); return; } checkFeed(feedUrl); }}>🔍 Проверить фид</Button>
                    <span className="boris-f2-316437256d37">Уникальность текста и фото сохранится за счёт спинтакса</span>
                  </BorisCard>
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
                      <div className="boris-f2-cee76ae2920b">✅ Проверка пройдена: все {genAds.length} объявлений корректны</div>
                    );
                    return (
                      <div className="boris-f2-29f5993fa222">
                        <div className="boris-f2-fa3d46ec5c59">⚠️ Найдено проблем: {probs.length}</div>
                        {probs.slice(0, 12).map((t, k) => <div key={k} className="boris-f2-c188ad004af7">• {t}</div>)}
                        {probs.length > 12 && <div>…и ещё {probs.length - 12}</div>}
                      </div>
                    );
                  })()}
                  <div className="boris-f2-e0abfdb050e2">
                    {genAds.map((ad, i) => (
                      <BorisCard key={i} className="b-card boris-f2-8cddc29a6936">
                        {editingIndex === i ? (
                          <div>
                            <input value={ad.title} onChange={e => updateAd(i, "title", e.target.value)} style={{...inputStyle, marginBottom:"8px", fontWeight:"bold"}} />
                            <input type="number" value={Number(ad.price) === 0 ? "" : ad.price} onChange={e => updateAd(i, "price", Number(e.target.value))} style={{...inputStyle, marginBottom:"8px", width:"120px"}} />
                            <div className="boris-f2-fa288833341a">
                              <Button className="boris-btn-hover boris-f2-a1317344cb1f" onClick={() => applyBold(i)}>Ж</Button>
                              <span className="boris-f2-316437256d37">выделите текст и нажмите Ж</span>
                            </div>
                            <textarea id={"desc-edit-" + i} value={ad.description} onChange={e => updateAd(i, "description", e.target.value)} rows={10} style={{...inputStyle, width:"100%", boxSizing:"border-box" as any, fontFamily:"inherit", fontSize:"15px"}} />
                            <Button className="boris-btn-hover boris-f2-efa1f4de92d4" onClick={() => setEditingIndex(null)}>✅ Готово</Button>
                          </div>
                        ) : (
                          <>
                        <div className="boris-f2-dd7c1e6181f8">{ad.title}</div>
                        <div className="boris-f2-55bc6c20f644">{ad.price} ₽</div>
                        <div style={{fontSize:"15px", color:"var(--boris-text-2)", whiteSpace:"pre-wrap", lineHeight:1.5, wordBreak:"break-word", overflowWrap:"break-word", maxHeight: expandedAds.has(i) ? "none" : "120px", overflow: expandedAds.has(i) ? "visible" : "hidden", position:"relative"}} dangerouslySetInnerHTML={{__html: ad.description
                          .replace(/</g, "&lt;").replace(/>/g, "&gt;")
                          .replace(/&lt;strong&gt;/g, "<strong style='color:var(--boris-text)'>").replace(/&lt;\/strong&gt;/g, "</strong>")
                          .replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>")
                          .replace(/&lt;ul&gt;/g, "<ul style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ul&gt;/g, "</ul>")
                          .replace(/&lt;ol&gt;/g, "<ol style='margin:6px 0;padding-left:18px'>").replace(/&lt;\/ol&gt;/g, "</ol>")
                          .replace(/&lt;li&gt;/g, "<li>").replace(/&lt;\/li&gt;/g, "</li>")
                          .replace(/&lt;br\s*\/?&gt;/g, "<br>").replace(/&lt;p&gt;/g, "<p style='margin:6px 0'>").replace(/&lt;\/p&gt;/g, "</p>")
                          .replace(/(\{[^}]*\})/g, "<span style='background:var(--boris-blue-soft);color:var(--boris-blue);padding:1px 4px;border-radius:4px;font-weight:bold'>$1</span>")
                        }} />
                          <Button className="b-more boris-f2-d8a81eac84c9" onClick={() => toggleExpand(i)}>{expandedAds.has(i) ? "▲ Свернуть" : "▼ Показать полностью"}</Button>
                          <div className="boris-f2-4aecb837fc3e">
                            <span className="b-meta">Цена:</span>
                            <input className="b-input boris-f2-6f2c3d4dc8da" type="number" value={ad.price || ""} onChange={e => updateAd(i, "price", e.target.value)} />
                            <span className="b-meta">₽</span>
                          </div>
                          <BorisCard className="b-card-actions boris-g1-card boris-g1-interactive">
                            <Button className="b-btn boris-f2-0afbd4f80773" onClick={() => setEditingIndex(i)}>✏️ Править</Button>
                            <Button className="b-btn boris-f2-2cbe3eccf188" onClick={() => setPhotoPickerIndex(photoPickerIndex === i ? null : i)}>🖼 Фото ({getAdPhotos(i).length})</Button>
                            <Button className="b-btn boris-f2-d6bd88faab51" onClick={() => deleteAd(i)}>🗑 Удалить</Button>
                            <Button className="b-btn boris-f2-5e05ee655d94" onClick={() => saveAsTextTemplate(ad)}>💾 Сохранить как шаблон</Button>
                          </BorisCard>
                          {photoPickerIndex === i && (
                            <div className="boris-f2-815b2589145e">
                              <div className="boris-f2-5a9738ff9ea1">
                                <span className="boris-f2-96046ead62a3">Выделите фото для этого объявления</span>
                                <div className="boris-f2-1d8943fa86ea">
                                  <input id={"ad-upload-" + i} type="file" accept="image/*" multiple onChange={e => uploadForAd(i, e.target.files)}  className="boris-f2-c8be1ccba60b"/>
                                  <Button className="boris-btn-hover boris-f2-2476d65ce100" onClick={() => document.getElementById("ad-upload-" + i)?.click()}>📁 Со своего устройства</Button>
                                  <Button className="boris-btn-hover boris-f2-2476d65ce100" onClick={loadImagesForFeed}>🔄 Обновить список</Button>
                                </div>
                              </div>
                              <div className="boris-f2-aaea5800ee2a">
                                {allImages.map((url, ii) => (
                                  <div key={ii} className="boris-f2-87db2275805b">
                                    <div onClick={() => toggleAdPhoto(i, url)} style={{cursor:"pointer", border: (adPhotos[i]||[]).includes(url) ? "2px solid #7C5CFC" : "2px solid #EEF2FA", borderRadius:"6px", overflow:"hidden"}}>
                                      <img src={url} style={{width:"100%", height:"55px", objectFit:"cover", display:"block", opacity: (adPhotos[i]||[]).includes(url) ? 1 : 0.5}} />
                                      {(adPhotos[i]||[]).includes(url) && <div className="boris-f2-ba62c0a4386b">✓</div>}
                                    </div>
                                    {(adPhotos[i]||[]).includes(url) && (
                                      <div className="boris-f2-2b5fa1251907">
                                        <Button className="boris-btn-hover" onClick={() => toggleTextPhoto(i, url)} title="Пометить как фото с текстом (пойдёт первым)" style={{background: (adTextPhotos[i]||[]).includes(url) ? "var(--boris-blue)" : "var(--boris-surface)", color: (adTextPhotos[i]||[]).includes(url) ? "var(--boris-bg)" : "var(--boris-text-3)", border:"1px solid var(--boris-border)", borderRadius:"4px", width:"20px", height:"18px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>Т</Button>
                                        <div className="boris-f2-293ab8ff6efc">
                                          <Button className="boris-btn-hover boris-f2-bdc46b98820d" onClick={() => movePhoto(i, url, -1)}>↑</Button>
                                          <Button className="boris-btn-hover boris-f2-bdc46b98820d" onClick={() => movePhoto(i, url, 1)}>↓</Button>
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                              <Button className="boris-btn-hover boris-f2-cfe2d8accbe5" onClick={() => setPhotoPickerIndex(null)}>✅ Готово</Button>
                            </div>
                          )}
                          </>
                        )}
                      </BorisCard>
                    ))}
                  </div>
                  <div className="boris-f2-fe706cba890a">
                    <div className="boris-f2-66689fe172ec">
                      <div>
                        <label className="b-label">📍 Город / адрес для фида</label>
                        <input value={genAddress} onChange={e => setGenAddress(e.target.value)} placeholder="Москва" style={{...inputStyle, width:"260px"}} />
                      </div>
                      <div>
                        <label className="b-label">Или выберите из справочника</label>
                        <div className="boris-f2-b9bbe540d342">
                          <select className="b-select" value={selectedRegion} onChange={e => { setSelectedRegion(e.target.value); setSelectedCity(""); }} style={{...inputStyle, width:"160px"}}>
                            <option value="">Регион</option>
                            {Object.keys(citiesData).map(r => <option key={r} value={r}>{r}</option>)}
                          </select>
                          <select className="b-select" value={selectedCity} onChange={e => applyCityToAddress(e.target.value)} disabled={!selectedRegion} style={{...inputStyle, width:"160px"}}>
                            <option value="">Город</option>
                            {(citiesData[selectedRegion] || []).map(c => <option key={c} value={c}>{c}</option>)}
                          </select>
                          {metroData[selectedCity] && (
                            <Button className="boris-btn-hover" onClick={() => setShowMetroPicker(!showMetroPicker)} style={{background: selectedMetro ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: selectedMetro ? "var(--boris-blue)" : "#7C5CFC", border:"1px solid #F0ECFF", borderRadius:"10px", padding:"0 14px", cursor:"pointer", fontSize:"15px", whiteSpace:"nowrap"}}>🚇 {selectedMetro || "Метро"}</Button>
                          )}
                        </div>
                        <div className="boris-f2-ecd721ef4d39">
                          {selectedRegion && (citiesData[selectedRegion] || []).length > 1 && (
                            <Button className="boris-btn-hover" onClick={selectAllCitiesInRegion} style={{background: bulkCities.length>0 ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: bulkCities.length>0 ? "var(--boris-blue)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"5px 10px", cursor:"pointer", fontSize:"15px"}}>✅ Все города региона {bulkCities.length>0 ? `(${bulkCities.length})` : ""}</Button>
                          )}
                          {metroData[selectedCity] && (
                            <Button className="boris-btn-hover" onClick={selectAllMetroInCity} style={{background: bulkMetro.length>0 ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: bulkMetro.length>0 ? "var(--boris-blue)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"5px 10px", cursor:"pointer", fontSize:"15px"}}>✅ Все станции метро {bulkMetro.length>0 ? `(${bulkMetro.length})` : ""}</Button>
                          )}
                          <div className="boris-f2-4a17cefe8a2b">
                            <span className="boris-f2-ba62946b0ebb">🇷🇺 Вся Россия: топ</span>
                            <input value={topNCount} onChange={e=>setTopNCount(e.target.value.replace(/[^0-9]/g,""))}  className="boris-f2-90fe448eff62"/>
                            <Button className="boris-btn-hover boris-f2-27b984c54fe2" onClick={applyTopRussia}>Применить</Button>
                          </div>
                          <Button className="boris-btn-hover" onClick={()=>setMultiRegionMode(!multiRegionMode)} style={{background: pickedRegions.length>0 ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: pickedRegions.length>0 ? "var(--boris-blue)" : "var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"10px", padding:"5px 10px", cursor:"pointer", fontSize:"15px"}}>🗺️ Несколько регионов {pickedRegions.length>0 ? `(${pickedRegions.length})` : ""}</Button>
                          {(bulkCities.length>0 || bulkMetro.length>0) && (
                            <Button className="boris-btn-hover boris-f2-f0e9c0d60c26" onClick={() => { setBulkCities([]); setBulkMetro([]); }}>✕ Сбросить</Button>
                          )}
                        </div>
                        {multiRegionMode && (
                          <div className="boris-f2-45755f365f5e">
                            {Object.keys(citiesData).map(r => (
                              <label key={r} style={{display:"flex", alignItems:"center", gap:"5px", fontSize:"14px", cursor:"pointer", background: pickedRegions.includes(r)?"var(--boris-blue-soft)":"var(--boris-bg)", borderRadius:"8px", padding:"4px 10px"}}>
                                <input type="checkbox" checked={pickedRegions.includes(r)} onChange={()=>toggleRegion(r)} />{r}
                              </label>
                            ))}
                            <Button onClick={applyMultiRegions} className="boris-f2-06d9e9f324bb">Собрать города ({pickedRegions.length} рег.)</Button>
                          </div>
                        )}
                        {showMetroPicker && metroData[selectedCity] && (
                          <div className="boris-f2-f9169a9eaede">
                            {metroData[selectedCity].map(st => (
                              <div key={st} onClick={() => applyMetroToAddress(st)} style={{padding:"6px 10px", cursor:"pointer", borderRadius:"6px", fontSize:"15px", color: selectedMetro===st ? "var(--boris-blue)" : "var(--boris-text-2)"}} onMouseEnter={e => (e.currentTarget.style.background = "var(--boris-surface)")} onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                                {st}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                    <div className="boris-f2-8a359a76ebec">
                      <label className="b-label boris-f2-a82d7062cc5e">🖼 Фото для объявлений <span className="boris-f2-de10b07e3112">— отмеченные галочками распределятся по объявлениям</span></label>
                      <div className="b-photo-bar">
                        <span className="boris-f2-076b4ea8da03">Фото на объявление:</span>
                        <input className="b-input boris-f2-f03512d7abda" type="number" min={1} max={10} value={Number(photosPerAd) === 0 ? "" : photosPerAd} onChange={e => setPhotosPerAd(Math.max(1, Math.min(10, Number(e.target.value)||1)))} />
                        <div className="boris-f2-f1013f288bab">
                          <span className="boris-f2-316437256d37">Остальные фото:</span>
                          <Button className="b-btn" onClick={() => setRestOrderMode("order")} style={{padding:"9px 12px", fontSize:"13px", border: restOrderMode==="order" ? "1px solid var(--boris-blue)" : "1px solid var(--boris-border)", background: restOrderMode==="order" ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: restOrderMode==="order" ? "var(--boris-blue)" : "var(--boris-text-2)"}}>по порядку</Button>
                          <Button className="b-btn" onClick={() => setRestOrderMode("random")} style={{padding:"9px 12px", fontSize:"13px", border: restOrderMode==="random" ? "1px solid var(--boris-blue)" : "1px solid var(--boris-border)", background: restOrderMode==="random" ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: restOrderMode==="random" ? "var(--boris-blue)" : "var(--boris-text-2)"}}>случайно</Button>
                        </div>
                        <div className="b-brk"></div>
                        <Button className="b-btn boris-f2-06d3f5bf4f3d" onClick={loadImagesForFeed}>🔄 Из хранилища</Button>
                        <input id="pool-upload-input" type="file" accept="image/*" multiple onChange={e => uploadForPool(e.target.files)}  className="boris-f2-c8be1ccba60b"/>
                        <Button className="b-btn boris-f2-7f842d52869a" onClick={() => document.getElementById("pool-upload-input")?.click()}>📁 С устройства</Button>
                        <span className="b-meta boris-f2-4018f4a8b552">🏷 Баннер первым:</span>
                        <Button className="b-btn" onClick={() => setUseBannerPersist(true)} style={{padding:"9px 14px", fontSize:"13px", border: useBanner ? "1px solid #12805C" : "1px solid var(--boris-border)", background: useBanner ? "#E9F7F1" : "var(--boris-surface)", color: useBanner ? "#12805C" : "var(--boris-text-2)"}}>Да</Button>
                        <Button className="b-btn" onClick={() => setUseBannerPersist(false)} style={{padding:"9px 14px", fontSize:"13px", border: !useBanner ? "1px solid #B42318" : "1px solid var(--boris-border)", background: !useBanner ? "#FDECEC" : "var(--boris-surface)", color: !useBanner ? "#B42318" : "var(--boris-text-2)"}}>Нет</Button>
                        <span className="boris-f2-2bd0042afdbf">Выбрано: {Object.values(feedImages).filter(Boolean).length}</span>
                      </div>
                      {allImages.length > 0 && (() => {
                        const isBanner = (u: string) => /banner|баннер|infograph|инфограф/i.test(decodeURIComponent(u));
                        const bannerList = allImages.filter(isBanner);
                        const photoList = allImages.filter((u: string) => !isBanner(u));
                        const cntB = bannerList.filter((u: string) => feedImages[u]).length;
                        const cntP = photoList.filter((u: string) => feedImages[u]).length;
                        const grid = (list: string[], accent: string) => (
                          <div className="boris-f2-7cc92521063d">
                            {list.map((url: string, ii: number) => (
                              <div key={ii} onClick={() => toggleFeedImage(url)} style={{position:"relative", cursor:"pointer", border: feedImages[url] ? "2px solid " + accent : "2px solid #EEF2FA", borderRadius:"8px", overflow:"hidden"}}>
                                <img src={url} style={{width:"100%", height:"90px", objectFit:"cover", display:"block", opacity: feedImages[url] ? 1 : 0.6}} />
                                {feedImages[url] && <div style={{position:"absolute", top:"4px", right:"4px", background: accent, color:"var(--boris-bg)", borderRadius:"50%", width:"20px", height:"20px", display:"flex", alignItems:"center", justifyContent:"center", fontSize:"15px", fontWeight:"bold"}}>✓</div>}
                              </div>
                            ))}
                          </div>
                        );
                        return (
                          <div>
                            {bannerList.length > 0 && (
                              <div className="boris-f2-a82d7062cc5e">
                                <div className="boris-f2-697af69b32f5">🏷 Баннеры {useBanner ? "(ставятся первым фото, по кругу)" : "(отключены — тумблер «Нет»)"} — выбрано: {cntB} из {bannerList.length}</div>
                                {grid(bannerList, "#12805C")}
                              </div>
                            )}
                            {photoList.length > 0 && (
                              <div>
                                <div className="boris-f2-00ad068b4341">🖼 Фото товара (добираются до {Math.max(0, photosPerAd - (useBanner && cntB > 0 ? 1 : 0))}) — выбрано: {cntP} из {photoList.length}</div>
                                {grid(photoList, "var(--boris-blue)")}
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>

                    <div>
                        <label className="b-label">🕐 Когда публиковать</label>
                        <div className="boris-f2-b9bbe540d342">
                          <Button className="boris-btn-hover" onClick={() => setPublishMode("now")} style={{padding:"8px 14px", borderRadius:"10px", border: publishMode==="now" ? "1px solid var(--boris-blue)" : "1px solid var(--boris-border)", background: publishMode==="now" ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: publishMode==="now" ? "var(--boris-blue)" : "var(--boris-text-2)", cursor:"pointer", fontSize:"15px", fontWeight: publishMode==="now" ? "bold" : "normal"}}>⚡ Сейчас</Button>
                          <Button className="boris-btn-hover" onClick={() => setPublishMode("schedule")} style={{padding:"8px 14px", borderRadius:"10px", border: publishMode==="schedule" ? "1px solid var(--boris-blue)" : "1px solid var(--boris-border)", background: publishMode==="schedule" ? "var(--boris-blue-soft)" : "var(--boris-surface)", color: publishMode==="schedule" ? "var(--boris-blue)" : "var(--boris-text-2)", cursor:"pointer", fontSize:"15px", fontWeight: publishMode==="schedule" ? "bold" : "normal"}}>🕐 Запланировать</Button>
                        </div>
                      </div>
                      {publishMode === "schedule" && (
                        <div className="boris-f2-b9bbe540d342">
                          <div>
                            <label className="boris-f2-5a0de0cf2264">Дата</label>
                            <input type="date" value={publishDate} onChange={e => setPublishDate(e.target.value)} style={{...inputStyle, width:"160px"}} />
                          </div>
                          <div>
                            <label className="boris-f2-5a0de0cf2264">Время</label>
                            <input type="time" value={publishTime} onChange={e => setPublishTime(e.target.value)} style={{...inputStyle, width:"120px"}} />
                          </div>
                        </div>
                      )}
                      <Button className="b-btn b-btn-primary boris-f2-0b69cd21e01b" onClick={() => setShowPublishConfirm(true)}>
                        {publishMode === "now" ? `📤 Опубликовать сейчас (${genAds.length})` : `🕐 Запланировать (${genAds.length})`}
                      </Button>
                      {showPublishConfirm && (
                        <div onClick={() => setShowPublishConfirm(false)} className="boris-f2-40ee1e11790e">
                          <div onClick={(e:any) => e.stopPropagation()} className="boris-f2-33631c931988">
                            <h3 className="boris-f2-ab7a0549a1dc">Проверьте перед публикацией</h3>
                            <div className="boris-f2-aa5939599233">
                              <div><b className="boris-f2-de52905d633b">Объявлений:</b> {genAds.length}</div>
                              <div><b className="boris-f2-de52905d633b">С индивидуальными фото:</b> {Object.values(adPhotos).filter(p => p && p.length > 0).length} из {genAds.length}</div>
                              <div><b className="boris-f2-de52905d633b">Город:</b> {genAddress || "не указан"}</div>
                              <div><b className="boris-f2-de52905d633b">Когда:</b> {publishMode === "now" ? "⚡ Сейчас" : `🕐 ${publishDate || "дата не указана"} в ${publishTime}`}</div>
                            </div>
                            <div className="boris-f2-2c5b241b3608">
                              <Button className="boris-btn-hover boris-f2-b68f130f1f82" onClick={() => { setShowPublishConfirm(false); addAdsToFeed(); }}>✅ Всё верно, публикуем</Button>
                              <Button className="boris-btn-hover boris-f2-cc812ecc9f36" onClick={() => setShowPublishConfirm(false)}>Отмена</Button>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                    {feedUrl && (
                      <div className="boris-f2-a8bec053f4c3">
                        <div className="boris-f2-879a36bd023e">✅ Фид готов! Ссылка для ЛК Авито (Автозагрузка):</div>
                        <a href={feedUrl} target="_blank" className="boris-f2-a46eb3d95c47">{feedUrl}</a>
                      </div>
                    )}
                  </div>

                </div>
              )}
            </div>


            {showTemplateForm && (
              <div className="boris-f2-497dbb52498e">
                <h3 className="boris-f2-92a3f3c23f8e">Новый шаблон</h3>
                <p className="boris-f2-5209fe92f5d0">Переменные: {"{название}"}, {"{город}"}, {"{Вариант 1|Вариант 2|Вариант 3}"}</p>
                <div className="boris-f2-001013efee98">
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Название шаблона</label>
                    <input value={newTemplate.name} onChange={e => setNewTemplate({...newTemplate, name: e.target.value})} placeholder="Услуги в Москве" style={inputStyle} />
                  </div>
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Категория Авито</label>
                    <div className="boris-f2-b9bbe540d342">
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
                      <details className="boris-f2-a82d7062cc5e"><summary className="boris-f2-c0e3ed53fc0c">Подробнее</summary><div className="boris-f2-f10a6a56fafd">🔜 Эта категория пока не проверена в валидаторе Авито — выгрузка в фид может не пройти. Готово: Услуги → Стихи, поздравления, тосты.</div></details>
                    )}
                  </div>
                  <div className="boris-f2-97294b207c62">
                    <label className="boris-f2-5a0de0cf2264">Шаблон заголовка</label>
                    <input value={newTemplate.titleTemplate} onChange={e => setNewTemplate({...newTemplate, titleTemplate: e.target.value})}
                      placeholder="{Тротуарная плитка|Брусчатка} в {город}" style={inputStyle} />
                  </div>
                  <div className="boris-f2-97294b207c62">
                    <label className="boris-f2-5a0de0cf2264">Шаблон описания</label>
                    <textarea value={newTemplate.description} onChange={e => setNewTemplate({...newTemplate, description: e.target.value})}
                      placeholder="{описание}" rows={4} style={{...inputStyle, resize:"vertical"}} />
                  </div>
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Цена</label>
                    <select className="b-select" value={newTemplate.priceType} onChange={e => setNewTemplate({...newTemplate, priceType: e.target.value})} style={inputStyle}>
                      <option value="original">Оригинальная с сайта</option>
                      <option value="markup">Наценка %</option>
                      <option value="fixed">Фиксированная</option>
                    </select>
                  </div>
                  {newTemplate.priceType !== "original" && (
                    <div>
                      <label className="boris-f2-5a0de0cf2264">{newTemplate.priceType === "markup" ? "Наценка %" : "Цена ₽"}</label>
                      <input type="number" value={Number(newTemplate.priceModifier) === 0 ? "" : newTemplate.priceModifier} onChange={e => setNewTemplate({...newTemplate, priceModifier: Number(e.target.value)})} style={inputStyle} />
                    </div>
                  )}
                  <div className="boris-f2-97294b207c62">
                    <label className="boris-f2-5a0de0cf2264">Города</label>
                    <div className="boris-f2-568b09bafe45">
                      {Object.keys(CITY_SETS).map(setName => (
                        <Button className="boris-btn-hover boris-f2-660d89275a49" key={setName} onClick={() => setNewTemplate({...newTemplate, cities: CITY_SETS[setName]})}
>
                          + {setName}
                        </Button>
                      ))}
                      <Button className="boris-btn-hover boris-f2-c545564683bb" onClick={() => setNewTemplate({...newTemplate, cities: []})}
>
                        Очистить
                      </Button>
                    </div>
                    <input value={newTemplate.cities.join(", ")} onChange={e => setNewTemplate({...newTemplate, cities: e.target.value.split(/[,\s]+/).map(c => c.trim()).filter(c => c)})}
                      placeholder="Или введите вручную: москва, спб питер" style={inputStyle} />
                    <div className="boris-f2-314f95d6c1dc">Выбрано городов: {newTemplate.cities.length}</div>
                  </div>
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Время публикации</label>
                    <input type="time" value={newTemplate.schedule} onChange={e => setNewTemplate({...newTemplate, schedule: e.target.value})} style={inputStyle} />
                  </div>
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Задержка мин (минут)</label>
                    <input type="number" value={Number(newTemplate.delayMin) === 0 ? "" : newTemplate.delayMin} onChange={e => setNewTemplate({...newTemplate, delayMin: Number(e.target.value)})} style={inputStyle} />
                  </div>
                  <div>
                    <label className="boris-f2-5a0de0cf2264">Задержка макс (минут)</label>
                    <input type="number" value={Number(newTemplate.delayMax) === 0 ? "" : newTemplate.delayMax} onChange={e => setNewTemplate({...newTemplate, delayMax: Number(e.target.value)})} style={inputStyle} />
                  </div>
                  <div className="boris-f2-97294b207c62">
                    <label className="boris-f2-5a0de0cf2264">Дни публикации</label>
                    <div className="boris-f2-b9bbe540d342">
                      {days.map(day => (
                        <Button className="boris-btn-hover" key={day} onClick={() => {
                          const current = newTemplate.scheduleDays;
                          setNewTemplate({...newTemplate, scheduleDays: current.includes(day) ? current.filter(d => d !== day) : [...current, day]});
                        }} style={{padding:"8px 12px", borderRadius:"8px", border:"none", cursor:"pointer",
                          background: newTemplate.scheduleDays.includes(day) ? "var(--boris-blue)" : "var(--boris-surface)",
                          color: newTemplate.scheduleDays.includes(day) ? "var(--boris-bg)" : "var(--boris-text-2)", fontWeight:"bold", fontSize:"15px"}}>
                          {day}
                        </Button>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="boris-f2-d4268e91708c">
                  <Button className="boris-btn-hover boris-f2-2fe5fa0e8409" onClick={() => {
                    if (!newTemplate.name) return alert("Введите название шаблона");
                    setTemplates([...templates, {...newTemplate, id: Date.now()}]);
                    setShowTemplateForm(false);
                    setNewTemplate({name:"", titleTemplate:"{название}", description:"{описание}", priceType:"original", priceModifier:0, cities:["Москва"], category:"Предложение услуг", schedule:"09:00", scheduleDays:["пн","вт","ср","чт","пт"], delayMin:2, delayMax:3});
                  }}>Сохранить</Button>
                  <Button className="boris-btn-hover boris-f2-cb85b182d2e2" onClick={() => setShowTemplateForm(false)}>Отмена</Button>
                </div>
              </div>
            )}

            <div className="boris-f2-6aa137d58b1b">
              {templates.map(t => (
                <div key={t.id} className="boris-f2-f13f2b87dcdf">
                  <div className="boris-f2-bebbec744221">
                    <div className="boris-f2-722924035001">{t.name}</div>
                    <div className="boris-f2-b9bbe540d342">
                      <Button className="b-btn b-btn-primary boris-f2-04069f041f3f" onClick={() => { setSelectedTemplate(t); setActiveTab("parser"); }}>Использовать</Button>
                      <Button className="boris-btn-hover boris-f2-3d6efb02a142" onClick={() => setTemplates(templates.filter(x => x.id !== t.id))}>Удалить</Button>
                    </div>
                  </div>
                  <div className="boris-f2-8d48d3cc01d2">
                    <div className="b-meta boris-f2-fd2b934d6d0e" title={t.titleTemplate}>📝 {t.titleTemplate}</div>
                    <div className="b-meta">💰 {t.priceType === "original" ? "С сайта" : t.priceType === "markup" ? `+${t.priceModifier}%` : `${t.priceModifier} ₽`}</div>
                    <div className="b-meta boris-f2-fd2b934d6d0e">📍 {(t.cities || []).join(", ")}</div>
                    <div className="b-meta boris-f2-fd2b934d6d0e">🕐 {t.schedule} / {(t.scheduleDays || []).join(", ")}</div>
                    <div className="b-meta">⏱ Задержка: {t.delayMin}–{t.delayMax} мин</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}


        {activeTab === "parser" && (
          <div>
            <BorisCard className="b-panel boris-f2-d12871d88e2b">
              <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-3ab1bb33aea8 boris-g1-icon">📤</span>Выгрузка товаров с сайта</h3>
              <p className="b-sub">БОРИС найдёт все товары и перенесёт в Авито</p>

              <div className="boris-f2-79a1c5a5db96">
                <label className="b-label">Шаблон для выгрузки</label>
                <select className="b-select" value={selectedTemplate?.id || ""} onChange={e => setSelectedTemplate(templates.find(t => t.id === Number(e.target.value)) || null)} style={inputStyle}>
                  <option value="">Выберите шаблон</option>
                  {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </div>
              <div className="boris-f2-5c00d98595d4">
                <h4 className="boris-f2-a9b4fa714e7f"><span className="b-icon-sm boris-f2-9146fe9dae58 boris-g1-icon">🔁</span>Или растяните шаблон без сайта</h4>
                <details className="boris-f2-a82d7062cc5e"><summary className="boris-f2-c0e3ed53fc0c">Подробнее</summary><p className="boris-f2-fffa89405079">Выберите шаблон выше, впишите тему — Борис сгенерирует названия и разошлёт их через выбранный шаблон с уникализацией</p></details>
                <div className="boris-f2-4d809b7004bb">
                  <input value={stretchTopic} onChange={e => setStretchTopic(e.target.value)} placeholder="Тема партии, напр. стихи на заказ" style={{...inputStyle, flex:1, minWidth:"200px"}} />
                  <input type="number" value={Number(stretchCount) === 0 ? "" : stretchCount} onChange={e => setStretchCount(Math.max(1, Math.min(50, Number(e.target.value)||1)))} style={{...inputStyle, width:"90px"}} />
                  <Button onClick={generateStretchNames} disabled={stretchLoading} className="b-btn b-btn-primary" style={{background: stretchLoading ? "var(--boris-border)" : undefined, borderColor: stretchLoading ? "var(--boris-border)" : undefined, cursor: stretchLoading ? "wait" : "pointer"}}>
                    {stretchLoading ? "⏳ Думаю..." : "✨ Сгенерировать названия"}
                  </Button>
                </div>
                {stretchNames.length > 0 && (
                  <div>
                    <div className="boris-f2-d6c905d382ac">Названия (можно отредактировать каждое):</div>
                    <div className="boris-f2-34f46bec29b5">
                      {stretchNames.map((name, i) => (
                        <input key={i} value={name} onChange={e => setStretchNames(prev => prev.map((n, ni) => ni === i ? e.target.value : n))} style={{...inputStyle, fontSize:"15px", padding:"8px"}} />
                      ))}
                    </div>
                    <Button onClick={publishStretch} className="b-btn b-btn-primary">
                      📤 Опубликовать {stretchNames.length} объявлений
                    </Button>
                  </div>
                )}
              </div>
            </BorisCard>


            {publishStatus && (
              <div className="boris-f2-70f49cf1039e">
                {publishStatus}
              </div>
            )}

              <div>
                <BorisCard className="b-panel boris-f2-5b6aad9a3fd6">
                  <div className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-3ab1bb33aea8 boris-g1-icon">🌐</span>Выгрузка карточек с сайта</div>
                  <details className="boris-f2-a82d7062cc5e"><summary className="boris-f2-c0e3ed53fc0c">Подробнее</summary><div className="b-sub boris-f2-a82d7062cc5e">Вставьте ссылку на товар или на каталог/категорию (можно несколько, каждая с новой строки) — Борис выгрузит карточки с фото, ценами и характеристиками. Задача уйдёт в очередь, результат появится через 10-30 секунд на ссылку.</div></details>
                  <div className="boris-f2-3487fbc4ce18">
                    <textarea value={parseUrl} onChange={e => setParseUrl(e.target.value)} placeholder={"https://сайт-клиента.ru/товар-или-каталог\nhttps://сайт-клиента.ru/другой-каталог"}
                      rows={3} className="b-input boris-f2-4cb37163ab0e" />
                    <div className="boris-f2-7f58be58a02e">
                      <label className="boris-f2-98cb934029bf">Сколько товаров</label>
                      <input type="number" min={0} value={parseLimit || ""} onChange={e => setParseLimit(Math.max(0, parseInt(e.target.value) || 0))} placeholder="0 = все" className="b-input boris-f2-cad980f4b707" />
                    </div>
                    <Button onClick={startParse} disabled={parseLoading || !parseUrl.trim()}
                      className="b-btn b-btn-primary" style={{opacity: parseLoading ? 0.6 : 1}}>
                      {parseLoading ? "⏳ Выгружаю..." : "🌐 Выгрузить"}
                    </Button>
                    <Button onClick={loadParsedProducts} className="b-btn b-btn-ghost">
                      🔄 Обновить
                    </Button>
                  </div>
                  {parseProgress && (
                    <div className="boris-f2-101c7a75cddd">
                      ⏳ Борис выгружает карточки... (товар {parseProgress.done} из {parseProgress.total})
                    </div>
                  )}
                  {parseSourceUrls.length > 0 && (
                    <div className="boris-f2-42d4a5024937">
                      Источник{parseSourceUrls.length > 1 ? "и" : ""}: {parseSourceUrls.map((u, i) => (
                        <span key={u}>{i > 0 && ", "}<a href={u} target="_blank" rel="noopener noreferrer" className="boris-f2-b5d95bf86ec9">{u}</a></span>
                      ))} · выгружено карточек: {parsedProducts.length}
                    </div>
                  )}
                </BorisCard>

                {parsedProducts.length > 0 && (
                  <div className="boris-f2-a82d7062cc5e">
                    <BorisCard className="b-panel boris-f2-93597abd4e0d">
                      <label className="b-label">📋 Образец / пожелания к текстам <span className="boris-f2-de10b07e3112">— Борис возьмёт отсюда общую информацию о компании и стиль, а конкретику по каждому товару и ключевые слова придумает свои</span></label>
                      <textarea className="b-input boris-f2-90afa77b7cd3" value={genSample} onChange={e => setGenSample(e.target.value)} placeholder="Вставь пример удачного объявления, бриф клиента или описание компании: условия доставки, гарантии, преимущества, тон подачи…" rows={5} />
                      <div className="boris-f2-8841b3e06f48">
                        <Button className="b-btn boris-f2-38e413b98ec6" onClick={saveTextSample}>💾 Сохранить как…</Button>
                        {genSample && <Button className="b-btn b-btn-ghost" onClick={() => setGenSample("")}>Очистить</Button>}
                      </div>
                      {textSamples.length > 0 && (
                        <div className="boris-f2-77b7c168e926">
                          <div className="b-meta boris-f2-ca4dc3e95d0e">Сохранённые образцы ({textSamples.length})</div>
                          {textSamples.map((t: any) => (
                            <div key={t.id} className="boris-f2-437e0dfcc67c">
                              <span className="b-meta boris-f2-c78ddc8b82f9">{t.name}</span>
                              <Button className="b-btn b-btn-ghost" onClick={() => setGenSample(t.text)}>↩ Загрузить</Button>
                              <Button className="b-btn boris-f2-4ed708bc749b" onClick={() => deleteTextSample(t.id)}>🗑</Button>
                            </div>
                          ))}
                        </div>
                      )}
                    </BorisCard>
                    <details className="boris-f2-a82d7062cc5e"><summary className="boris-f2-c0e3ed53fc0c">Подробнее</summary><div className="boris-f2-13ca16e7de8e">Конвейер: 1) выгрузка (готово) → 2) обогатить тексты + отправить в черновики → 3) баннеры на партию → 4) проверить фид → 5) опубликовать.</div></details>
                    <div className="boris-f2-15b47d7710a8">
                      <Button onClick={fetchAllGalleries} disabled={allGalleriesLoading}
                        className="b-btn b-btn-primary boris-f2-96b5a01a96d6">
                        🖼 {allGalleriesLoading ? "Запускаю..." : "Собрать все фото (в фоне)"}
                      </Button>
                      <Button onClick={enrichDescriptions} disabled={enrichLoading}
                        className="b-btn b-btn-primary boris-f2-5880fa0541d0">
                        ✨ {enrichLoading ? "Пишу описания..." : "Обогатить тексты ИИ (в фоне)"}
                      </Button>
                      <Button onClick={() => setSelectedParsedIdx(selectedParsedIdx.size === parsedProducts.length ? new Set() : new Set(parsedProducts.map((_,i)=>i)))}
                        className="b-btn b-btn-soft boris-f2-5d4515f904fc">
                        {selectedParsedIdx.size === parsedProducts.length ? "☑ Снять выбор" : "☐ Выбрать все"}
                      </Button>
                      {selectedParsedIdx.size > 0 && (
                        <>
                          <span className="boris-f2-a5968d92dd2c">Выбрано: {selectedParsedIdx.size}</span>
                          <Button onClick={deleteSelectedParsed}
                            className="b-btn b-btn-primary boris-f2-c2b57489c305">
                            🗑 Удалить выбранные
                          </Button>
                        </>
                      )}
                    </div>
                    {enrichProgress && <div className="boris-f2-1d201bc91c10">{enrichProgress}</div>}
                    {selectedParsedIdx.size > 0 && (
                      <div className="boris-f2-446fac4f5a5f">
                        <input value={pipelineBatchLabelInput} onChange={e => setPipelineBatchLabelInput(e.target.value)} placeholder="Название партии, напр. Детские"
                          className="b-input boris-f2-668d18fb0c60" />
                        <input value={pipelineTopicInput} onChange={e => setPipelineTopicInput(e.target.value)} placeholder="Ниша для категории (необязательно)"
                          className="b-input boris-f2-d9ccacd74cbf" />
                        <Button onClick={sendSelectedToDrafts} disabled={toDraftsLoading}
                          className="b-btn b-btn-primary boris-f2-48e95d1fef4e">
                          📤 {toDraftsLoading ? "Создаю..." : "В черновики"}
                        </Button>
                      </div>
                    )}
                  </div>
                )}

                {pipelineBatch && (
                  <BorisCard className="b-panel boris-f2-3e45ce21f4e9">
                    <div className="b-title boris-f2-12ac1e81f1de">
                      <span className="b-icon-sm boris-f2-9153f4ff5264 boris-g1-icon">📦</span>
                      Партия «{pipelineBatch.label}» — {pipelineBatch.count} черновиков, категория «{pipelineBatch.category}»
                    </div>
                    <div className="boris-f2-41231101c56e">
                      <input type="number" value={Number(bannerCount) === 0 ? "" : bannerCount} onChange={e => setBannerCount(Math.max(1, Number(e.target.value) || 1))} title="Сколько разных баннеров"  className="boris-f2-cd334c10cb14"/>
                      <input type="number" value={Number(photosPerAdInput) === 0 ? "" : photosPerAdInput} onChange={e => setPhotosPerAdInput(Math.max(1, Number(e.target.value) || 1))} title="Фото на объявление (баннер + обычные)"  className="boris-f2-cd334c10cb14"/>
                      <input value={bannerFolderInput} onChange={e => setBannerFolderInput(e.target.value)} placeholder="Папка с доп. фото (необязательно)"
                        className="b-input boris-f2-668d18fb0c60" />
                      <Button onClick={runBatchBanners} disabled={batchBannerLoading}
                        className="b-btn b-btn-primary boris-f2-c24f9a613530">
                        🎨 {batchBannerLoading ? "Генерирую..." : "Баннеры на партию"}
                      </Button>
                      <Button onClick={runBatchValidate} disabled={pipelineValidateLoading}
                        className="b-btn b-btn-primary boris-f2-96b5a01a96d6">
                        🔍 {pipelineValidateLoading ? "Проверяю..." : "Проверить фид (до публикации)"}
                      </Button>
                      <Button onClick={runBatchPublish} disabled={pipelinePublishLoading}
                        className="b-btn b-btn-primary boris-f2-872c8e2020b6">
                        🚀 {pipelinePublishLoading ? "Публикую..." : "Опубликовать сейчас"}
                      </Button>
                    </div>
                    {bannerResult && <div className="boris-f2-f413d1eacb96">{bannerResult}</div>}
                    {pipelineValidateResult && (
                      <div style={{background: pipelineValidateResult.status === "ok" ? "#F0FBF6" : "#FEF7EC", border:"1px solid " + (pipelineValidateResult.status === "ok" ? "#C7EBDC" : "#FDE9C8"), borderRadius:"8px", padding:"12px", fontSize:"12px", color:"var(--boris-text-2)", whiteSpace:"pre-wrap", maxHeight:"260px", overflowY:"auto"}}>
                        <b>{pipelineValidateResult.status === "ok" ? "✅ Фид корректен" : "⚠️ Есть замечания"}</b>
                        <div className="boris-f2-2fd7789b397a">{pipelineValidateResult.raw_text}</div>
                      </div>
                    )}
                  </BorisCard>
                )}

                {parsedProducts.length === 0 ? (
                  <div className="boris-f2-0cbb631ca992">Пока нет выгруженных карточек. Вставьте ссылку выше и нажмите «Выгрузить».</div>
                ) : (
                  <div className="boris-f2-81ebad604be0">
                    {parsedProducts.map((p, idx) => (
                      <BorisCard key={idx} className="b-card boris-f2-102c74593fc1">
                        <div className="boris-f2-4e1513940c7e">
                          <input type="checkbox" checked={selectedParsedIdx.has(idx)} onChange={() => toggleParsedSelect(idx)}
  className="boris-f2-1d75ba679561"/>
                          {p.image ? <img src={p.image} alt={p.title} onClick={() => { const imgs = p.images && p.images.length > 0 ? p.images : [p.image]; setPgLightbox(imgs); setPgLightboxIdx(0); }}  className="boris-f2-58ce61779478"/> : <span className="boris-f2-20730004e9bf">нет фото</span>}
                          {p.images && p.images.length > 1 && <span className="boris-f2-3f118decb119">📷 {p.images.length}</span>}
                        </div>
                        <div className="boris-f2-cd0803287ba5">
                          {editingParsedIdx === idx ? (
                            <>
                              <input defaultValue={p.title} id={`parsed-title-${idx}`}  className="boris-f2-d56343673fe1"/>
                              <input defaultValue={p.price} id={`parsed-price-${idx}`}  className="boris-f2-8c3148cf53ea"/>
                              <FormatToolbar textareaId={`parsed-desc-${idx}`} />
                              <textarea defaultValue={p.ai_description || p.description || ""} id={`parsed-desc-${idx}`} rows={5}
                                placeholder="Описание товара (можно сгенерировать через ИИ ниже)"
  className="boris-f2-b4b58f7330c6"/>
                              <Button onClick={() => genDescriptionForCard(idx)} disabled={genDescLoading}
                                className="b-btn b-btn-primary boris-f2-8c30217501a9">
                                ✨ {genDescLoading ? "Генерирую..." : "Сгенерировать ИИ-описание"}
                              </Button>
                              <select className="b-select boris-f2-6cc0017bc62d" id={`parsed-template-${idx}`} defaultValue="">
                                <option value="">— применить структуру шаблона —</option>
                                {templates.filter((t:any) => t.id).map((t:any) => <option key={t.id} value={t.id}>{t.name}</option>)}
                              </select>
                              <div className="boris-f2-1d8943fa86ea">
                                <Button onClick={() => {
                                    const title = (document.getElementById(`parsed-title-${idx}`) as HTMLInputElement)?.value;
                                    const price = (document.getElementById(`parsed-price-${idx}`) as HTMLInputElement)?.value;
                                    const desc = (document.getElementById(`parsed-desc-${idx}`) as HTMLTextAreaElement)?.value;
                                    saveParsedEdit(idx, { title, price, ai_description: desc });
                                  }} className="boris-f2-947326341b1f">Сохранить</Button>
                                <Button onClick={() => setEditingParsedIdx(null)} className="boris-f2-378162fc8e01">Отмена</Button>
                              </div>
                            </>
                          ) : (
                            <>
                          <div className="boris-f2-7ebe3bb57427">{p.title}</div>
                          {p.price && <div className="boris-f2-7ee8fd6aab78">{p.price}</div>}
                          {p.ai_description && <div className="boris-f2-5b6568e4d996">✨ {p.ai_description.slice(0,120)}...</div>}
                          {p.characteristics && Object.keys(p.characteristics).length > 0 && (
                            <div className="boris-f2-3a21fe8461c9">
                              {Object.entries(p.characteristics).slice(0,4).map(([k,v]: any, i) => (
                                <div key={i}><b>{k}:</b> {String(v).slice(0,50)}</div>
                              ))}
                            </div>
                          )}
                          <Button className="boris-btn-hover boris-f2-49571d99a41b" onClick={() => setEditingParsedIdx(idx)}>✏️ Редактировать</Button>
                            </>
                          )}
                          {p.banner_url && (
                            <div className="boris-f2-b1ecc496e0c7">
                              <img src={p.banner_url} alt="Баннер" onClick={() => { setPgLightbox([p.banner_url]); setPgLightboxIdx(0); }}  className="boris-f2-10e527d6e220"/>
                              <div className="boris-f2-5a87d59b8664">✅ Баннер готов</div>
                            </div>
                          )}
                          <div className="boris-f2-2cdde190c54f">
                            {p.product_url && <a href={p.product_url} target="_blank" rel="noopener noreferrer" className="boris-btn-hover boris-f2-77729ab23993">↗ На сайте</a>}
                            <Button className="boris-btn-hover boris-f2-b0060101407a" onClick={() => fetchGalleryForCard(idx)} disabled={galleryLoading === idx}
>
                              🖼 {galleryLoading === idx ? "Собираю..." : "Все фото"}
                            </Button>
                            <Button className="boris-btn-hover boris-f2-6241354d7d33" onClick={() => genProductBanner(p, idx)}>🎨 {p.banner_url ? "Пересоздать" : "Баннер"}</Button>
                          </div>
                        </div>
                      </BorisCard>
                    ))}
                  </div>
                )}
              </div>
            {pgLightbox.length > 0 && (
              <div onClick={() => setPgLightbox([])} className="boris-f2-826c7dca20b8">
                <Button onClick={(e) => { e.stopPropagation(); setPgLightbox([]); }} className="boris-f2-d3b22bbd5bbc">✕ Закрыть</Button>
                {pgLightbox.length > 1 && (
                  <Button onClick={(e) => { e.stopPropagation(); setPgLightboxIdx(i => (i - 1 + pgLightbox.length) % pgLightbox.length); }} className="boris-f2-d396edb6859f">‹</Button>
                )}
                <img src={pgLightbox[pgLightboxIdx]} alt="Фото" onClick={e => e.stopPropagation()}  className="boris-f2-52023a8056d7"/>
                {pgLightbox.length > 1 && (
                  <Button onClick={(e) => { e.stopPropagation(); setPgLightboxIdx(i => (i + 1) % pgLightbox.length); }} className="boris-f2-24a38cd42a1e">›</Button>
                )}
                {pgLightbox.length > 1 && (
                  <div className="boris-f2-3675fa1e8473">{pgLightboxIdx + 1} / {pgLightbox.length}</div>
                )}
              </div>
            )}
          </div>
        )}

        {activeTab === "webdesign" && (
          <div>
            {invoiceOpen && (
  <div onClick={()=>setInvoiceOpen(false)} className="boris-f2-e5cf81d91efd">
    <div onClick={e=>e.stopPropagation()} className="boris-f2-ef79515cc497">
      <div className="boris-f2-47f0643dc590">Счёт для оплаты по безналу</div>
      <div className="boris-f2-68b5f0c2785d">Укажите реквизиты вашей организации — счёт сформируется автоматически с суммой и назначением платежа.</div>
      <label className="boris-f2-465ca3ab2ea4">Название организации *</label>
      <input value={payerName} onChange={e=>setPayerName(e.target.value)} placeholder="ООО «Ромашка» / ИП Иванов И.И."  className="boris-f2-8279a7300a6c"/>
      <label className="boris-f2-465ca3ab2ea4">ИНН плательщика</label>
      <input value={payerInn} onChange={e=>setPayerInn(e.target.value)} placeholder="7700000000"  className="boris-f2-b7a5f5e58b8f"/>
      <div className="boris-f2-6b00e75351fc">
        <Button onClick={createInvoice} disabled={invoiceBusy} style={{flex:1, background:"var(--boris-blue)", color:"#fff", border:"none", borderRadius:"10px", padding:"12px", fontSize:"15px", fontWeight:700, cursor: invoiceBusy?"default":"pointer", opacity: invoiceBusy?0.6:1}}>{invoiceBusy ? "Формируем…" : "Сформировать счёт (PDF)"}</Button>
        <Button onClick={()=>setInvoiceOpen(false)} className="boris-f2-246fbff19082">Отмена</Button>
      </div>
    </div>
  </div>
)}
{lightboxUrl && (<div onClick={() => setLightboxUrl("")} className="boris-f2-9a5c1a734f51"><img src={lightboxUrl} onClick={(e) => e.stopPropagation()}  className="boris-f2-333c9e636e33"/><Button onClick={() => setLightboxUrl("")} className="boris-f2-1a3789d3e076">×</Button></div>)}
            <div className="boris-f2-02af0c528efa">
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
                  { icon: "📸", grad: "linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", n: grown(2, 140, 1600), label: "качественных фото", verb: "Собрал" },
                  { icon: "🖼", grad: "linear-gradient(135deg,#7C5CFC,#5B3FD9)", n: grown(3, 1, 90), label: "баннеров для тарифов Максимальный и Расширенный", verb: "Оформил" },
                ];
                return (
                  <div>
                    <div className="b-label boris-f2-77b9facee35a">Сегодня Борис для наших клиентов:</div>
                    <div className="boris-f2-267295db1bf4">
                      {cards.map((c, i) => (
                        <BorisCard key={i} className="b-card b-stat boris-g1-card" style={{animationDelay: `${i * 0.13}s`}}>
                          <span className="b-stat-ico boris-g1-card boris-g1-icon" style={{background: c.grad}}>{c.icon}</span>
                          <div className="boris-f2-ec923525254c">
                            <div className="b-meta boris-f2-0820770ed772">{c.verb}</div>
                            <BorisCard className="b-stat-num boris-g1-card"><CountUp to={c.n} delay={i * 130} /></BorisCard>
                            <div className="b-meta boris-f2-765bfbe3b879">{c.label}</div>
                          </div>
                        </BorisCard>
                      ))}
                    </div>
                  </div>
                );
              })()}
            <div>
              <div className="b-label boris-f2-cc7eced42484">.</div>
              <div className="boris-f2-267295db1bf4">
                <BorisCard className="b-card b-stat boris-g1-card" onClick={() => setWebdesignFolder(webdesignFolder === "photos" ? null : "photos")} style={{cursor:"pointer", animationDelay:"0.06s", background: webdesignFolder === "photos" ? "var(--boris-blue-soft)" : "var(--boris-surface)", borderColor: webdesignFolder === "photos" ? "var(--boris-blue)" : "var(--boris-border)"}}>
                  <span className="b-stat-ico boris-f2-79a4d50add5b boris-g1-card">📤</span>
                  <div><div className="boris-f2-0bffb6e4abfb">Мои фото</div><div className="b-meta boris-f2-b7ecdfaf5aa3">Загруженные вами изображения</div></div>
                </BorisCard>
                <BorisCard className="b-card b-stat boris-g1-card" onClick={() => setWebdesignFolder(webdesignFolder === "studio" ? null : "studio")} style={{cursor:"pointer", animationDelay:"0.19s", background: webdesignFolder === "studio" ? "#F0ECFF" : "var(--boris-surface)", borderColor: webdesignFolder === "studio" ? "#7C5CFC" : "var(--boris-border)"}}>
                  <span className="b-stat-ico boris-f2-cfa8c19ebe09 boris-g1-card">🎨</span>
                  <div><div className="boris-f2-afdba7425fa8">Студия картинок Бориса</div><div className="b-meta boris-f2-b7ecdfaf5aa3">Баннеры и инфографика от ИИ</div></div>
                </BorisCard>
                <BorisCard className="b-card b-stat boris-g1-card" onClick={() => { const next = webdesignFolder === "buy" ? null : "buy"; setWebdesignFolder(next); if (next === "buy") { loadBannerGallery(); loadSavedPrompts(); } }} style={{cursor:"pointer", animationDelay:"0.32s", background: webdesignFolder === "buy" ? "#FEF3E2" : "var(--boris-surface)", borderColor: webdesignFolder === "buy" ? "#F79009" : "var(--boris-border)"}}>
                  <span className="b-stat-ico boris-f2-c7c2ac9ce619 boris-g1-card">🛒</span>
                  <div><div className="boris-f2-8debb147e7a0">Купить баннеры</div><div className="b-meta boris-f2-b7ecdfaf5aa3">Готовые баннеры из галереи</div></div>
                </BorisCard>
              </div>
            </div>
            </div>
              <details style={{display: webdesignFolder === "buy" ? "block" : "none", marginTop:"0", marginBottom:"28px", marginLeft:"0"}}><summary className="boris-f2-edba65204236">Купить баннеры</summary><div className="boris-f2-3740881efa64">20 инфографик в месяц уже входят в подписку. Ниже — докупка сверх лимита.</div><div className="boris-f2-f7618e064427"><BorisCard className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card"><div  className="boris-f2-9925a2443d3e"/><div className="b-ref-ic boris-f2-6eb71abc715b">📋</div><div className="boris-f2-242b783ffd40">1 баннер</div><div className="boris-f2-c852af6b2ccb">250 ₽</div><div className="boris-f2-f1a901467a71">инфографика для объявления</div><div className="boris-f2-ff71126d36e1"><RobokassaButton invoiceId="hlcZfzwZDUWWMhJitJ_wnQ" pack="ban1" accountId={currentAccount} /></div></BorisCard><BorisCard className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card"><div  className="boris-f2-f4e03e24cfce"/><div className="b-ref-ic boris-f2-f9a3abd958d1">📋</div><div className="boris-f2-242b783ffd40">10 баннеров</div><div className="boris-f2-c852af6b2ccb">2 300 ₽</div><div className="boris-f2-f1a901467a71">популярный пакет</div><div className="boris-f2-ff71126d36e1"><RobokassaButton invoiceId="i_DhFWpOYEyAf120Y4Djzg" pack="ban10" accountId={currentAccount} /></div></BorisCard><BorisCard className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card"><div  className="boris-f2-63fae23744eb"/><div className="b-ref-ic boris-f2-59af67595616">📋</div><div className="boris-f2-242b783ffd40">30 баннеров</div><div className="boris-f2-c852af6b2ccb">6 300 ₽</div><div className="boris-f2-f1a901467a71">выгоднее за штуку</div><div className="boris-f2-ff71126d36e1"><RobokassaButton invoiceId="scSJQwIYv0Ke4vDJwMjC3w" pack="ban30" accountId={currentAccount} /></div></BorisCard><BorisCard className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card"><div  className="boris-f2-9925a2443d3e"/><div className="b-ref-ic boris-f2-6eb71abc715b">📋</div><div className="boris-f2-242b783ffd40">50 баннеров</div><div className="boris-f2-c852af6b2ccb">10 500 ₽</div><div className="boris-f2-f1a901467a71">для каталога</div><div className="boris-f2-ff71126d36e1"><RobokassaButton invoiceId="Lc7XU4driEuytzx6a9453w" pack="ban50" accountId={currentAccount} /></div></BorisCard><BorisCard className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card"><div  className="boris-f2-2c4fec02cea2"/><div className="b-ref-ic boris-f2-68c33a8457a0">📋</div><div className="boris-f2-242b783ffd40">100 баннеров</div><div className="boris-f2-c852af6b2ccb">20 000 ₽</div><div className="boris-f2-f1a901467a71">максимум</div><div className="boris-f2-ff71126d36e1"><RobokassaButton invoiceId="gA5P5092U0KhSaT3diCv1Q" pack="ban100" accountId={currentAccount} /></div></BorisCard><BorisCard className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card"><div  className="boris-f2-f2082c9afe40"/><div className="b-ref-ic boris-f2-394906a129bd">🏪</div><div className="boris-f2-242b783ffd40">Расширенный тариф</div><div className="boris-f2-c852af6b2ccb">800 ₽</div><div className="boris-f2-f1a901467a71">профиль: 1 ПК + 1 моб</div><div className="boris-f2-ff71126d36e1"><RobokassaButton invoiceId="rpurvpER-UmZnPckeCCkiQ" pack="prof_ext" accountId={currentAccount} /></div></BorisCard><BorisCard className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card"><div  className="boris-f2-212ed57b15c0"/><div className="b-ref-ic boris-f2-f5728095a887">🏪</div><div className="boris-f2-242b783ffd40">Максимальный тариф</div><div className="boris-f2-c852af6b2ccb">2 400 ₽</div><div className="boris-f2-f1a901467a71">профиль: 3 ПК + 3 моб</div><div className="boris-f2-ff71126d36e1"><RobokassaButton invoiceId="O1Xeq-_fAkeAija1zkFpvw" pack="prof_max" accountId={currentAccount} /></div></BorisCard></div></details>

            <div style={{display: (webdesignFolder === "buy" && userRole === "owner") ? "block" : "none", background:"var(--boris-surface)", border:"1px solid var(--boris-border)", borderRadius:"16px", padding:"24px", marginBottom:"24px"}}>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom: showBannerPricing ? "8px" : 0}}>
                <h3 className="boris-f2-24475118349d"><span className="b-icon-sm boris-f2-b6d7d93afe6e boris-g1-icon">💰</span>Себестоимость (видите только вы)</h3>
                <Button className="boris-btn-hover boris-f2-9f14aa887aa8" onClick={() => setShowBannerPricing(!showBannerPricing)}>{showBannerPricing ? "Скрыть цены" : "Показать цены"}</Button>
              </div>
              <p style={{color:"var(--boris-blue)", fontSize:"15px", marginBottom: showBannerPricing ? "18px" : 0}}>✅ 20 инфографик/мес входят в подписку бесплатно. Расширенный/Максимальный — отдельно.</p>
              <div style={{display: showBannerPricing ? "grid" : "none", gridTemplateColumns:"repeat(auto-fill, minmax(260px, 1fr))", gap:"14px"}}>
                <BorisCard className="boris-card-hover boris-f2-7bbcffb4609d boris-g1-card">
                  <div  className="boris-f2-a97425d6acb2"/><div className="boris-f2-ee74a9cfcd73">📋</div><div className="boris-f2-039b1068b4b6">Инфографика (объявление)</div>
                  <div className="boris-f2-6cd12038b46f">1080×1080 · 1 изображение</div>
                  <div className="boris-f2-101232f759bc"><span className="boris-f2-de52905d633b">Черновик</span><span className="boris-f2-b5d95bf86ec9">~0.45₽</span></div>
                  <div className="boris-f2-cbdc03d7af64"><span className="boris-f2-de52905d633b">Стандарт</span><span className="boris-f2-b5d95bf86ec9">~3.6₽</span></div>
                  <div className="boris-f2-cbdc03d7af64"><span className="boris-f2-de52905d633b">Премиум</span><span className="boris-f2-b5d95bf86ec9">~19₽</span></div>
                </BorisCard>
                <BorisCard className="boris-card-hover boris-f2-7bbcffb4609d boris-g1-card">
                  <div  className="boris-f2-5c86b576f4e2"/><div className="boris-f2-87d11c38eb52">💎</div><div className="boris-f2-039b1068b4b6">Расширенный тариф</div>
                  <div className="boris-f2-6cd12038b46f">ПК 1202×436 + моб 1242×936 · 2 изображения</div>
                  <div className="boris-f2-101232f759bc"><span className="boris-f2-de52905d633b">Черновик</span><span className="boris-f2-b5d95bf86ec9">~0.9₽</span></div>
                  <div className="boris-f2-cbdc03d7af64"><span className="boris-f2-de52905d633b">Стандарт</span><span className="boris-f2-b5d95bf86ec9">~7.2₽</span></div>
                  <div className="boris-f2-cbdc03d7af64"><span className="boris-f2-de52905d633b">Премиум</span><span className="boris-f2-b5d95bf86ec9">~38₽</span></div>
                </BorisCard>
                <BorisCard className="boris-card-hover boris-f2-7bbcffb4609d boris-g1-card">
                  <div  className="boris-f2-04411ac582a1"/><div className="boris-f2-290a1c921ae3">👑</div><div className="boris-f2-039b1068b4b6">Максимальный тариф</div>
                  <div className="boris-f2-6cd12038b46f">Карусель 3 слайда × ПК/моб · 6 изображений</div>
                  <div className="boris-f2-101232f759bc"><span className="boris-f2-de52905d633b">Черновик</span><span className="boris-f2-b5d95bf86ec9">~2.7₽</span></div>
                  <div className="boris-f2-cbdc03d7af64"><span className="boris-f2-de52905d633b">Стандарт</span><span className="boris-f2-b5d95bf86ec9">~21.6₽</span></div>
                  <div className="boris-f2-cbdc03d7af64"><span className="boris-f2-de52905d633b">Премиум</span><span className="boris-f2-b5d95bf86ec9">~114₽</span></div>
                </BorisCard>
              </div>
              {showBannerPricing && <p className="boris-f2-24ef0a86e96a">20 инфографик/месяц на Стандарт-качестве — себестоимость ~72₽.</p>}
            </div>

            <div style={{display: webdesignFolder === "photos" ? "block" : "none", background:"var(--boris-surface)", border:"1px solid var(--boris-border)", borderRadius:"16px", padding:"24px", marginBottom:"24px"}}>
              <div className="boris-f2-bcbb844d17df">
                <h3 className="boris-f2-24475118349d"><span className="b-icon-sm boris-f2-ac305422ad3b boris-g1-icon">📤</span>Мои фото</h3>
                <Button className="boris-btn-hover boris-f2-071a8067b547" onClick={() => createNewFolder(true)}>➕ Создать папку</Button>
              </div>
              <p className="boris-f2-8d66ef11ddb7">Ваши личные фото (не сгенерированные ИИ) — своя удобная организация, отдельно от Студии картинок.</p>
              {openedFolder && openedFolder.startsWith("Личное_") && (
                <div className="boris-f2-a82d7062cc5e">
                  <input id="personal-upload-input" type="file" accept="image/*" multiple onChange={e => uploadForPool(e.target.files)}  className="boris-f2-c8be1ccba60b"/>
                  <Button className="boris-btn-hover boris-f2-5b5b4a9ff383" onClick={() => document.getElementById("personal-upload-input")?.click()}>📤 Загрузить фото в «{openedFolder.replace("Личное_", "")}»</Button>
                </div>
              )}
              <div className="boris-f2-9dc685abe29f">
                {Object.keys(allFolders).filter(f => f.startsWith("Личное_")).map(folder => (
                  <div key={folder} onClick={() => openFolder(folder)} style={{cursor:"pointer", border: openedFolder===folder ? "2px solid var(--boris-blue)" : "2px solid #EEF2FA", borderRadius:"8px", overflow:"hidden", background:"var(--boris-bg)", position:"relative"}}>
                    <div className="boris-f2-2e99924fd231">
                      {allFolders[folder][0] ? <img src={allFolders[folder][0]}  className="boris-f2-b1279a8389d1"/> : <span className="boris-f2-f9c0def1c100">📁</span>}
                    </div>
                    <Button className="boris-btn-hover boris-f2-ca601affa1ea" onClick={(e:any) => { e.stopPropagation(); renameFolderPrompt(folder); }} title="Переименовать">✏️</Button>
                    <Button className="boris-btn-hover boris-f2-bc80dd423717" onClick={(e:any) => { e.stopPropagation(); deleteFolder(folder); }} title="Удалить папку (только пустую)">🗑</Button>
                    <div className="boris-f2-6463d40ad060">
                      <div className="boris-f2-01394527d497">{folder.replace("Личное_", "")}</div>
                      <div className="boris-f2-316437256d37">{allFolders[folder].length} фото</div>
                      {openedFolder === folder && <div className="boris-f2-fb88e3ec0a5f">✓ Открыто ниже ↓</div>}
                    </div>
                  </div>
                ))}
                {Object.keys(allFolders).filter(f => f.startsWith("Личное_")).length === 0 && <div className="boris-f2-316437256d37">Пока нет личных папок — создайте первую</div>}
              </div>
            </div>

            <BorisCard className="b-panel boris-g1-card" style={{display: webdesignFolder === "studio" ? "block" : "none", marginBottom:"24px"}}>
              <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-42bebf635359 boris-g1-icon">🎨</span>Студия картинок Бориса</h3>
              <div className="b-meta boris-f2-a3a5568692be">Борис сам рисует баннеры и инфографику для объявлений — опишите товар и получите готовую картинку</div>
              <p className="boris-f2-5209fe92f5d0">Опиши конкретно: главный объект первым, меньше сущностей, добавь контекст сцены</p>

              <BorisCard className="b-card boris-f2-232233b277bc">
                <div className="boris-f2-bebbec744221">
                  <span className="boris-f2-afdba7425fa8">📁 Все папки проектов</span>
                  <Button className="boris-btn-hover boris-f2-2476d65ce100" onClick={loadAllFolders}>🔄 Обновить</Button>
                </div>
                <div className="boris-f2-9dc685abe29f">
                  {Object.keys(allFolders).map(folder => (
                    <div key={folder} onClick={() => openFolder(folder)} style={{cursor:"pointer", border: openedFolder===folder ? "2px solid #7C5CFC" : "2px solid #EEF2FA", borderRadius:"8px", overflow:"hidden", background:"var(--boris-bg)", position:"relative"}}>
                      <div className="boris-f2-2e99924fd231">
                        {allFolders[folder][0] ? <img src={allFolders[folder][0]}  className="boris-f2-b1279a8389d1"/> : <span className="boris-f2-f9c0def1c100">📁</span>}
                      </div>
                      <Button className="boris-btn-hover boris-f2-e9152a6c0f3e" onClick={(e:any) => { e.stopPropagation(); renameFolderPrompt(folder); }} title="Переименовать">✏️</Button>
                      <Button className="boris-btn-hover boris-f2-bc80dd423717" onClick={(e:any) => { e.stopPropagation(); deleteFolder(folder); }} title="Удалить папку (только пустую)">🗑</Button>
                      <div className="boris-f2-6463d40ad060">
                        <div className="boris-f2-01394527d497">{folder}</div>
                        <div className="boris-f2-316437256d37">{allFolders[folder].length} фото</div>
                      </div>
                    </div>
                  ))}
                  {Object.keys(allFolders).length === 0 && <div className="boris-f2-316437256d37">Папок пока нет</div>}
                </div>
              </BorisCard>

              <div className="boris-f2-79a1c5a5db96">
                <label className="boris-f2-5a0de0cf2264">Что нарисовать (промпт под конкретную задачу)</label>
                <textarea value={imgPrompt} onChange={e => setImgPrompt(e.target.value)} placeholder="Например: большой пышный букет красных роз в подарочной упаковке, крупным планом, праздничный фон с боке" rows={3} style={{...inputStyle, resize:"vertical", fontFamily:"inherit"}} />
              </div>
              <div className="boris-f2-1f442ae36742">
                <div>
                  <label className="boris-f2-5a0de0cf2264">Стиль</label>
                  <input value={imgStyle} onChange={e => setImgStyle(e.target.value)} style={inputStyle} />
                </div>
                <div>
                  <label className="boris-f2-5a0de0cf2264">Сколько (1-50)</label>
                  <input type="number" value={Number(imgCount) === 0 ? "" : imgCount} min={1} max={50} onChange={e => setImgCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))} style={inputStyle} />
                </div>
                <div>
                  <label className="boris-f2-5a0de0cf2264">Папка проекта</label>
                  <input value={imgFolder} onChange={e => setImgFolder(e.target.value)} placeholder="поздравления" style={inputStyle} />
                </div>
              <div className="boris-f2-949ddfcacf91">
                ✅ Задача встанет в очередь на сервере — можно закрыть вкладку, генерация продолжится в фоне. Результат появится в галерее, когда будет готов.
              </div>
              </div>
              <Button className="boris-btn-hover" onClick={() => setShowImageConfirm(true)} disabled={imgLoading} style={{background: imgLoading ? "var(--boris-border)" : "#7C5CFC", color:"var(--boris-bg)", border:"none", borderRadius:"10px", padding:"12px 24px", fontWeight:"bold", cursor: imgLoading ? "wait" : "pointer", fontSize:"23px"}}>
                {imgLoading ? "🎨 В очереди..." : "🎨 Нарисовать"}
              </Button>
              {imgQueueStatus && <div className="boris-f2-ad2a81b5745b">{imgQueueStatus}</div>}
              {showImageConfirm && (
                <div onClick={() => setShowImageConfirm(false)} className="boris-f2-40ee1e11790e">
                  <div onClick={(e:any) => e.stopPropagation()} className="boris-f2-5a9e0467fb61">
                    <h3 className="boris-f2-ab7a0549a1dc">Проверьте перед генерацией</h3>
                    <div className="boris-f2-aa5939599233">
                      <div><b className="boris-f2-de52905d633b">Промпт:</b> {imgPrompt ? imgPrompt.slice(0,80)+(imgPrompt.length>80?"…":"") : "не задан"}</div>
                      <div><b className="boris-f2-de52905d633b">Стиль:</b> {imgStyle}</div>
                      <div><b className="boris-f2-de52905d633b">Количество:</b> {imgCount}</div>
                      <div><b className="boris-f2-de52905d633b">Папка:</b> {imgFolder || "общая"}</div>
                      <div><b className="boris-f2-de52905d633b">Время:</b> ~{Math.ceil(imgCount/2)*1}-{Math.ceil(imgCount/2)*1.5} мин (пачками по 2)</div>
                    </div>
                    <div className="boris-f2-53dab9e79de2">
                      ⚠️ Не закрывайте вкладку до конца генерации. Для {imgCount >= 10 ? "такой партии лучше использовать компьютер, не телефон." : "надёжности держите вкладку открытой."}
                    </div>
                    <div className="boris-f2-d4268e91708c">
                      <Button className="boris-btn-hover boris-f2-a720c2c9f5b8" onClick={() => { setShowImageConfirm(false); generateImage(); }}>✅ Всё верно, рисуем</Button>
                      <Button className="boris-btn-hover boris-f2-cc812ecc9f36" onClick={() => setShowImageConfirm(false)}>Отмена</Button>
                    </div>
                  </div>
                </div>
              )}

              <div className="boris-f2-e06e2221170d">
                <label className="boris-f2-72ed274e58a5">🖼️ Собрать реальные фото по теме (бесплатные фотостоки)</label>
                <div className="boris-f2-f55faf5c931d">
                  Просто напиши тему на английском (лучше находит) — Борис сам найдёт и скачает нужное количество реальных бесплатных фото. Например: "бетон товарный и растворы" или "concrete construction".
                </div>
                <div className="boris-f2-a5804c9176e1">
                  <input value={stockQuery} onChange={e => setStockQuery(e.target.value)} placeholder="Например: бетон товарный и растворы" style={{...inputStyle, flex:1}} />
                  <input type="number" min={1} max={80} value={Number(stockCount) === 0 ? "" : stockCount} onChange={e => setStockCount(Math.max(1, Math.min(80, Number(e.target.value)||1)))} style={{...inputStyle, width:"80px"}} />
                  <Button className="boris-btn-hover boris-f2-29dab1003fa4" onClick={searchStockPhotos} disabled={stockLoading}>
                    {stockLoading ? "⏳ Собираю..." : "🖼️ Собрать фото"}
                  </Button>
                </div>
              </div>

              <div className="boris-f2-fe706cba890a">
                <label className="boris-f2-5a0de0cf2264">📥 Или скачать по прямой ссылке (JPG/PNG) в папку проекта</label>
                <div className="boris-f2-6208a3666d62">
                  💡 Нужна ссылка именно на КАРТИНКУ, не на страницу сайта. Открой картинку → правый клик → "Копировать адрес изображения" (обычно заканчивается на .jpg/.png). Ссылка на страницу поиска/каталога не сработает.<br/>
                  Файл сохранится в текущую папку проекта: <b className="boris-f2-517924e0b69a">{imgFolder || "общая"}</b> (если папки ещё нет — она создастся автоматически).
                </div>
                <div className="boris-f2-37bfc2fc415a">
                  <input value={dlUrl} onChange={e => setDlUrl(e.target.value)} placeholder="https://site.com/photo.jpg" style={{...inputStyle, flex:1}} />
                  <Button className="boris-btn-hover boris-f2-8cc388244ecf" onClick={downloadByUrl} disabled={imgLoading}>📥 Скачать</Button>
                </div>
              </div>

              {genImages.length > 0 && (
                <div className="boris-f2-3343fd6464ac">
                  <div className="boris-f2-bcbb844d17df">
                    <div className="boris-f2-4159ac1197bb">Галерея ({genImages.length})</div>
                    <a href={`/api/avito/download_folder_zip?folder=${encodeURIComponent(imgFolder || "общая")}&account_id=${currentAccount}`} className="boris-f2-7d7d6f522f39">📦 Скачать всё папкой (zip)</a>
                  </div>
                  <div className="boris-f2-81ebad604be0">
                    {genImages.map((img, i) => (
                      <div key={i} className="boris-f2-a29e9db64645">
                        <a href={`/api/avito/download_single?url=${encodeURIComponent(img.url)}`} title="Скачать на компьютер" className="boris-f2-14439b70dbde">⬇️</a>
                        <Button className="boris-btn-hover boris-f2-ee927b3fa7a9" onClick={() => deleteImage(img.url)} title="Удалить с сервера">🗑</Button>
                        <Button className="boris-btn-hover boris-f2-b3ad77eb7633" onClick={() => setMovePickerFor(movePickerFor === i ? null : i)} title="Перенести в другую папку">➡️</Button>
                        {movePickerFor === i && (
                          <div className="boris-f2-245f59e349d3">
                            {Object.keys(allFolders).map(f => (
                              <div key={f} onClick={() => moveImageToFolder(i, f)} onMouseEnter={e => (e.currentTarget.style.background = "#EEF2FA")} onMouseLeave={e => (e.currentTarget.style.background = "transparent")} className="boris-f2-27c897000182">📁 {f}</div>
                            ))}
                          </div>
                        )}
                        <img src={img.url}  className="boris-f2-ea0f892ade29"/>
                        <div className="boris-f2-e037bc33e711">{(img.prompt || "").slice(0,60)}…</div>
                        <input readOnly value={img.url} onClick={(e: any) => { e.target.select(); document.execCommand("copy"); }} title="Клик — скопировать ссылку"  className="boris-f2-aebec2f2cf60"/>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </BorisCard>

            <div style={{display: webdesignFolder === "buy" ? "block" : "none", background:"var(--boris-bg)", border:"1px solid #FEF3E2", borderRadius:"16px", padding:"24px", marginBottom:"24px"}}>
              <h3 className="boris-f2-7d8a6640ea49"><span className="b-icon-sm boris-f2-ac305422ad3b boris-g1-icon">✨</span>Создать баннер</h3>
              <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Как составить описание</summary><p className="boris-f2-183644454912">Опишите нишу и что нужно на баннере — Борис придумает текст и сгенерирует изображение через ИИ. Максимум 5 попыток правки на один баннер.</p></details>

              <div className="boris-f2-7dde5e56b3f3">
                <div className="boris-f2-40eee6f1a1fd">
                  <div className="boris-f2-870a64560bdb">🖼 Витрина примеров {selectedExampleUrls.length > 0 ? `(выбрано ${selectedExampleUrls.length}/3)` : ""}</div>
                  <div className="boris-f2-b9bbe540d342">
                    <label className="boris-btn-hover boris-f2-48654127e76c">
                      {showcaseUploading ? "⏳ Загружаю..." : "📤 Загрузить своё"}
                      <input type="file" accept="image/*" multiple onChange={(e) => { const fs = e.target.files; if (fs && fs.length > 0) uploadMultipleExamplesToShowcase(fs); e.target.value = ""; }}  className="boris-f2-c8be1ccba60b"/>
                    </label>
                    <Button className="boris-btn-hover boris-f2-17de572b964b" onClick={() => { setShowBrowseAllBanners((v) => !v); if (!showBrowseAllBanners) loadAllBanners(); }}>
                      {showBrowseAllBanners ? "Скрыть" : "📂 Смотреть все баннеры"}
                    </Button>
                  </div>
                </div>

                {bannerShowcase.length === 0 && !showBrowseAllBanners && (
                  <p className="boris-f2-96046ead62a3">Витрина пока пуста — нажмите «Смотреть все баннеры», чтобы добавить примеры.</p>
                )}
                <details className="boris-f2-d982937dda0f"><summary className="boris-f2-c0e3ed53fc0c">Показать примеры стилей</summary>
                <div className="boris-f2-d7dad7e3c47e">
                  {bannerShowcase.map((item: any, i: number) => (
                    <div key={i} onClick={() => toggleExampleSelected(item.url)} style={{cursor:"pointer", position:"relative", borderRadius:"8px", overflow:"hidden", border: selectedExampleUrls.includes(item.url) ? "3px solid #F79009" : "1px solid var(--boris-border)"}}>
                      <img src={item.url}  className="boris-f2-ce9af9c778e9"/>
                      {selectedExampleUrls.includes(item.url) && (
                        <div className="boris-f2-bfd54d04f7c9">✓</div>
                      )}
                      <Button onClick={(e) => { e.stopPropagation(); removeBannerFromShowcase(item.url); }} className="boris-f2-2ea96f88fb3c">✕</Button>
                    </div>
                  ))}
                </div>

                {showBrowseAllBanners && (
                  <div className="boris-f2-f29c80c45e20">
                    <div className="boris-f2-021c13804a10">Все сгенерированные баннеры — нажмите ⭐, чтобы добавить в витрину</div>
                    {browseAllLoading ? <p className="boris-f2-96046ead62a3">Загружаю...</p> : (
                      <div className="boris-f2-c0e6f79d27ae">
                        {allBannersList.map((b: any, i: number) => (
                          <div key={i} className="boris-f2-e61d9534d306">
                            <img src={b.url}  className="boris-f2-ce9af9c778e9"/>
                            <Button onClick={() => addBannerToShowcase(b.url)} className="boris-f2-8b92c2aca936">⭐ В витрину</Button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                </details>
              </div>

              <div className="boris-f2-b8598b29767e">

              </div>

              <div className="boris-f2-7dde5e56b3f3">
                <label className="boris-f2-22d1b9c90dff">Формат баннера</label>
                <select className="b-select boris-f2-55ba34549ffb" value={bannerFormat} onChange={e => setBannerFormat(e.target.value)}>
                  <option value="infographic">📋 Инфографика для объявления (1080×1080)</option>
                  <option value="extended_pc">💎 Расширенный тариф — ПК/планшет (1202×436)</option>
                  <option value="extended_mobile">💎 Расширенный тариф — смартфон (1242×936)</option>
                  <option value="max_pc">👑 Максимальный тариф — карусель ПК (1304×480 × 3)</option>
                  <option value="max_mobile">👑 Максимальный тариф — карусель моб. (1420×960 × 3)</option>
                  <option value="custom">✏️ Свой размер (ввести вручную)</option>
                </select>
              </div>

              {bannerFormat === "custom" && (
                <div className="boris-f2-f67e6e3ba330">
                  <input type="number" placeholder="Ширина, px" value={Number(customBannerW) === 0 ? "" : customBannerW} onChange={e => setCustomBannerW(e.target.value)}  className="boris-f2-fc890299d79c"/>
                  <input type="number" placeholder="Высота, px" value={Number(customBannerH) === 0 ? "" : customBannerH} onChange={e => setCustomBannerH(e.target.value)}  className="boris-f2-fc890299d79c"/>
                </div>
              )}

              <div className="boris-f2-7dde5e56b3f3">
                <div className="boris-f2-66111486da64">
                  <label className="boris-f2-96046ead62a3">Опишите нишу</label>
                  <div className="boris-f2-78cead6503f9">
                    {savedPrompts.length > 0 && (
                      <select className="b-select boris-f2-9cb98274577d" onChange={e => { const p = savedPrompts.find(x => String(x.id) === e.target.value); if (p) setBannerPrompt(p.text); }} defaultValue="">
                        <option value="">📥 Загрузить промт…</option>
                        {savedPrompts.map(sp => (<option key={sp.id} value={sp.id}>{sp.title}</option>))}
                      </select>
                    )}
                    <Button className="boris-btn-hover boris-f2-1305772b6e12" onClick={savePromptAsTemplate}>💾 Сохранить как шаблон</Button>
                  </div>
                </div>
                <textarea value={bannerPrompt} onChange={e => setBannerPrompt(e.target.value)} placeholder="Например: делаем шкафы-купе на заказ в Пензе, работаем 5 лет, цены от 18000 рублей, гарантия 3 года, бесплатный замер. Хочу яркий оранжевый стиль."  className="boris-f2-411d50eb0d92"/>
              </div>

              <details className="boris-f2-7dde5e56b3f3"><summary className="boris-f2-c0e3ed53fc0c">Тонкая настройка (необязательно)</summary>
              <div className="boris-f2-7dde5e56b3f3">
                <label className="boris-f2-22d1b9c90dff">Свой текст на баннере</label>
                <textarea value={bannerExactText} onChange={e => setBannerExactText(e.target.value)} maxLength={70} placeholder="Короткий оффер — до 6 слов, крупно для телефона"  className="boris-f2-ab04bf3f40a4"/>
                <div style={{fontSize:12,color:"var(--boris-text-3)",marginTop:6}}>Для Avito BORIS оставляет только крупный заголовок: максимум 6 слов. Мелкий текст на баннер не помещается.</div>
              </div>

              <div style={{marginBottom:"20px", background:"var(--boris-bg)", border: bannerUseOwnPhoto ? "1px solid var(--boris-blue)" : "1px solid #EEF2FA", borderRadius:"8px", padding:"14px"}}>
                <label style={{display:"flex", alignItems:"center", gap:"10px", marginBottom: bannerUseOwnPhoto ? "12px" : "0", cursor:"pointer", color:"var(--boris-text-2)", fontSize:"15px"}}>
                  <input type="checkbox" checked={bannerUseOwnPhoto} onChange={e => { setBannerUseOwnPhoto(e.target.checked); if (e.target.checked) { loadAllFolders(); } else { setBannerOwnPhotoUrl(""); } }}  className="boris-f2-ccd87a40655a"/>
                  Использовать моё фото {(bannerFormat !== "infographic" && bannerFormat !== "custom") && <span className="boris-f2-4e518f114f40">(пока только для формата «Инфографика»)</span>}
                </label>
                {bannerUseOwnPhoto && (
                  <div className="boris-f2-3079d2fe21fd">
                    {Object.entries(allFolders).filter(([folder]) => folder.startsWith("Личное_")).flatMap(([folder, urls]: [string, any]) =>
                      (urls as string[]).map((url: string) => (
                        <div key={url} onClick={() => setBannerOwnPhotoUrl(url)} style={{position:"relative", width:"64px", height:"64px", borderRadius:"6px", overflow:"hidden", cursor:"pointer", border: bannerOwnPhotoUrl === url ? "3px solid var(--boris-blue)" : "1px solid var(--boris-border)"}}>
                          <img src={url}  className="boris-f2-b1279a8389d1"/>
                          {bannerOwnPhotoUrl === url && (
                            <div className="boris-f2-124d97d1aaec">✓</div>
                          )}
                        </div>
                      ))
                    )}
                    {Object.keys(allFolders).filter(f => f.startsWith("Личное_")).length === 0 && <div className="boris-f2-316437256d37">Личных папок нет — загрузите свои фото во вкладке «Выгрузка с сайта».</div>}
                  </div>
                )}
              </div>

              <div className="boris-f2-7dde5e56b3f3">
                <label className="boris-f2-22d1b9c90dff">Пример дизайна для подражания</label>
                <label className="boris-f2-da049cbfb798">
                  📁 Выбрать фото
                  <input type="file" accept="image/*" onChange={handleRefImageUpload}  className="boris-f2-c8be1ccba60b"/>
                </label>
                {bannerRefImagePreview && (
                  <div className="boris-f2-d8a81eac84c9">
                    <img src={bannerRefImagePreview}  className="boris-f2-ee2813a5390c"/>
                    <Button className="boris-btn-hover boris-f2-bad0de146220" onClick={() => { setBannerRefImageBase64(""); setBannerRefImagePreview(""); }}>Убрать фото</Button>
                  </div>
                )}
              </div>

              <div className="boris-f2-f67e6e3ba330">
                <div className="boris-f2-7623f0554520">
                  <label className="boris-f2-22d1b9c90dff">Акцентный цвет</label>
                  <input type="color" value={bannerAccentColor} onChange={e => setBannerAccentColor(e.target.value)}  className="boris-f2-fedef0d993ec"/>
                </div>
                <div className="boris-f2-7623f0554520">
                  <label className="boris-f2-22d1b9c90dff">Качество генерации</label>
                  <select className="b-select boris-f2-55ba34549ffb" value={bannerQuality} onChange={e => setBannerQuality(e.target.value)}>
                    <option value="low">Дёшево и быстро (черновик)</option>
                    <option value="medium">Стандарт (для клиента)</option>
                    <option value="high">Премиум (дороже)</option>
                  </select>
                </div>
              </div>

              <div className="boris-f2-99dd07ace0fe">
                <label className="boris-f2-339e217f7224">Вариантов: {bannerVaryCount}</label>
                <input type="range" min={1} max={5} value={bannerVaryCount} onChange={e => setBannerVaryCount(Number(e.target.value))}  className="boris-f2-91b46317bed7"/>
                {bannerVaryCount > 1 && (
                  <>
                    <div className="boris-f2-6d9bb5b46d8b">Отличие вариантов по:</div>
                    <div className="boris-f2-7c33f2688f41">
                      <label className="boris-f2-6d67af7cb811">
                        <input type="checkbox" checked={bannerVaryAll ? true : bannerVaryColors} disabled={bannerVaryAll} onChange={e => setBannerVaryColors(e.target.checked)} /> Цветам
                      </label>
                      <label className="boris-f2-6d67af7cb811">
                        <input type="checkbox" checked={bannerVaryAll ? true : bannerVaryImage} disabled={bannerVaryAll} onChange={e => setBannerVaryImage(e.target.checked)} /> Картинке
                      </label>
                      <label className="boris-f2-6d67af7cb811">
                        <input type="checkbox" checked={bannerVaryAll ? true : bannerVaryIcons} disabled={bannerVaryAll} onChange={e => setBannerVaryIcons(e.target.checked)} /> Иконкам
                      </label>
                      <label className="boris-f2-09c075ad624d">
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

              <Button className="boris-btn-hover" onClick={generateBanner} disabled={bannerLoading} style={{background: bannerLoading ? "var(--boris-border)" : "var(--boris-blue)", color:"var(--boris-bg)", border:"none", borderRadius:"10px", padding:"10px 20px", fontWeight:"bold", cursor: bannerLoading ? "default" : "pointer", width:"auto", fontSize:"15px"}}>
                {bannerLoading ? `⏳ Борис рисует ${bannerVaryCount > 1 ? bannerVaryCount + " баннера" : "баннер"}...` : `✨ Сгенерировать ${bannerVaryCount > 1 ? bannerVaryCount + " варианта" : "баннер"}`}
              </Button>

              {bannerResultUrls.length > 0 && (
                <div className="boris-f2-f0778f9dce9c">
                  <h4 className="boris-f2-78af5e942847">Результат:</h4>
                  <div className="boris-f2-9979355b54ec">
                    {bannerResultUrls.map((url, i) => (
                      <div key={i} className="boris-f2-3da477895706">
                        <img src={url} alt={`banner-${i}`}  className="boris-f2-3ffd560134f1"/>
                        <div className="boris-f2-3bac393eafb8">
                          <a href={url} target="_blank" rel="noreferrer" className="boris-f2-d634642831f5">Открыть</a>
                          <a href={url} download className="boris-f2-d634642831f5">Скачать</a>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(bannerGallery.infographic.length > 0 || bannerGallery.extended.length > 0 || bannerGallery.max_carousel.length > 0) && (
                <div className="boris-f2-2594d24e8b9b">
                  <div className="boris-f2-a0768cfa75f7">
                    <h4 className="boris-f2-24475118349d"><span className="b-icon-sm boris-f2-7404d9a9c9e9 boris-g1-icon">📁</span>Все ваши баннеры</h4>
                    <div className="boris-f2-78cead6503f9">
                      <Button className="boris-btn-hover boris-f2-ffcac3d73933" onClick={selectAllBanners}>Выбрать все</Button>
                      <Button className="boris-btn-hover" onClick={downloadSelectedBanners} disabled={selectedBanners.length === 0} style={{background: selectedBanners.length === 0 ? "var(--boris-border)" : "var(--boris-blue)", color: selectedBanners.length === 0 ? "var(--boris-text-3)" : "var(--boris-surface)", border:"none", borderRadius:"10px", padding:"8px 14px", fontSize:"14px", fontWeight:"bold", cursor: selectedBanners.length === 0 ? "default" : "pointer"}}>⬇ Скачать выбранные{selectedBanners.length > 0 ? ` (${selectedBanners.length})` : ""}</Button>
                      <Button className="boris-btn-hover" onClick={deleteSelectedBanners} disabled={selectedBanners.length === 0} style={{background: selectedBanners.length === 0 ? "var(--boris-border)" : "#E24B4A", color: selectedBanners.length === 0 ? "var(--boris-text-3)" : "var(--boris-surface)", border:"1.5px solid #F04438", borderRadius:"10px", padding:"8px 14px", fontSize:"14px", fontWeight:"bold", cursor: selectedBanners.length === 0 ? "default" : "pointer"}}>🗑 Удалить выбранные{selectedBanners.length > 0 ? ` (${selectedBanners.length})` : ""}</Button>
                    </div>
                  </div>
                  {Object.entries(bannerGallery).map(([subfolder, urls]) => (
                    urls.length > 0 && (
                      <div key={subfolder} className="boris-f2-7dde5e56b3f3">
                        <div className="boris-f2-73e8a9f7ea0e">{subfolder === "infographic" ? "Инфографика" : subfolder === "extended" ? "Расширенный тариф" : "Максимальный тариф"} ({urls.length})</div>
                        <div className="boris-f2-68772bea9957">
                          {urls.map((url, i) => (
                            <BorisCard key={i} className="boris-card-hover boris-g1-card" style={{border: selectedBanners.includes(url) ? "2px solid var(--boris-blue)" : "1px solid var(--boris-border)", borderRadius:"16px", overflow:"hidden", background:"var(--boris-surface)", position:"relative", width:"200px"}}>
                              <img src={url} onClick={() => setLightboxUrl(url)} alt={`gallery-${i}`} style={{display:"block", width:"100%", height:"130px", objectFit:"contain", background:"var(--boris-bg)", cursor:"zoom-in", opacity: selectedBanners.includes(url) ? 0.75 : 1}} />
                              <div className="boris-f2-76bb143b4824"><div className="boris-f2-29be9e0cb513">{String(subfolder).includes("infographic") ? "Инфографика" : String(subfolder).includes("extended") ? "Расширенный тариф" : "Максимальный тариф"}</div></div>
                              <input type="checkbox" checked={selectedBanners.includes(url)} onChange={() => toggleBannerSelect(url)}  className="boris-f2-89ef1cbce2d1"/>
                              <Button className="boris-btn-hover boris-f2-95cbcdb8b618" onClick={() => deleteBannerFile(subfolder, url)} title="Удалить">🗑</Button>
                            </BorisCard>
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
          <div className="boris-f2-2e40e87e54ae">
            <div className="b-grid-2">
              <BorisCard className="b-panel b-card-eq boris-g1-card">
                <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-3402db1f72e2 boris-g1-icon">🎯</span>Цель по лидам</h3>
                <p className="boris-f2-8d66ef11ddb7">Задайте цель — Борис будет ориентироваться на неё при принятии решений об изменениях в объявлениях.</p>
                <div className="boris-f2-ddf54a2efb76">
                  <div>
                    <label className="boris-f2-5c5fa1b234c9">Лидов в день</label>
                    <input type="number" value={Number(kpiTargetLeads) === 0 ? "" : kpiTargetLeads} onChange={e => setKpiTargetLeads(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <div>
                    <label className="boris-f2-5c5fa1b234c9">Макс. ₽ за лид</label>
                    <input type="number" value={Number(kpiMaxCpl) === 0 ? "" : kpiMaxCpl} onChange={e => setKpiMaxCpl(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <div>
                    <label className="boris-f2-5c5fa1b234c9">Температура лидов</label>
                    <div className="boris-f2-4ba58f256316">Горячий — спрашивает цену, тёплый — интересуется, холодный — просто смотрит</div>
                    <select className="b-select" value={kpiLeadTemp} onChange={e => setKpiLeadTemp(e.target.value)} style={{...inputStyle, width:"100%", boxSizing:"border-box"}}>
                      <option value="любые">Любые</option>
                      <option value="горячие">🔥 Горячие</option>
                      <option value="тёплые">🌤 Тёплые</option>
                      <option value="холодные">❄️ Холодные</option>
                    </select>
                  </div>
                </div>
                <div style={{marginTop:14,padding:"14px 16px",border:"1px solid var(--boris-border)",borderRadius:12,background:"var(--boris-card)"}}>
                  <div style={{fontWeight:700,marginBottom:5}}>Цель BORIS: получить не менее {kpiTargetLeads || "—"} обращений в день при цене обращения не выше {kpiMaxCpl || "—"} ₽.</div>
                  <label style={{display:"flex",gap:10,alignItems:"flex-start",cursor:"pointer",fontSize:14,lineHeight:1.45}}>
                    <input type="checkbox" checked={kpiGoalAuto === true} onChange={e => setKpiGoalAuto(e.target.checked)} style={{marginTop:3}} />
                    <span><b>Автоматически достигать цели</b><br/><span style={{color:"var(--boris-muted)"}}>BORIS будет автоматически управлять разрешёнными рекламными действиями для выполнения этого плана. Ограничения безопасности сохраняются.</span></span>
                  </label>
                  <div style={{marginTop:8,fontSize:13,color:"var(--boris-muted)"}}>Автопилот по цели: <b>{kpiGoalAuto === null ? "Требуется настройка" : kpiGoalAuto ? "Включён" : "Выключен"}</b></div>
                </div>

                {kpiReadiness && <div style={{marginTop:14,padding:"14px 16px",border:"1px solid var(--boris-border)",borderRadius:12,background:"var(--boris-card)",fontSize:13,lineHeight:1.55}}>
                  <div style={{fontWeight:800,fontSize:15}}>ИИ-маркетолог · рабочее состояние</div>
                  <div><b>План:</b> {kpiReadiness.current_kpi?.target_leads_per_day ?? kpiTargetLeads} лидов · <b>Получено:</b> {kpiReadiness.current_kpi?.contacts_today ?? "—"}</div>
                  <div><b>Красная цена:</b> {kpiReadiness.current_kpi?.max_cost_per_lead_rub ?? kpiMaxCpl} ₽ · <b>Текущая цена лида:</b> {kpiReadiness.current_kpi?.cost_per_lead_today != null ? `${kpiReadiness.current_kpi.cost_per_lead_today} ₽` : "нет достаточных данных"}</div>
                  <div><b>Автопилот:</b> {kpiReadiness.goal_auto_enabled ? (kpiReadiness.capabilities?.publish_feed_change?.allowed ? "работает, публикация разрешена" : "работает, внешняя публикация ограничена") : "выключен"}</div>
                  <div><b>Что делает BORIS сейчас:</b> {humanizeBusinessText(kpiReadiness.current_work,"контролирует цель")}</div>
                  <div><b>Что сделал сегодня:</b> {humanizeBusinessText(kpiReadiness.last_action?.reason,"новых изменений пока нет")}</div>
                  <div><b>Что мешает:</b> {(kpiReadiness.human_blockers || []).map((x:any)=>humanizeBusinessText(x,"требуется дополнительная проверка")).join("; ") || "нет блокеров"}</div>
                  {kpiReadiness.required_avito_tariff && kpiReadiness.required_tariff_active !== true && <div style={{marginTop:8,padding:10,border:"1px solid var(--boris-border)",borderRadius:10}}><b>Автопилот готов, но публикация изменений остановлена.</b> Подтвердите тариф Avito «{kpiReadiness.required_avito_tariff}» для этого аккаунта.<div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:8}}><Button onClick={()=>confirmKpiTariff("CONFIRMED_ACTIVE")}>Да, нужный тариф подключён</Button><Button onClick={()=>confirmKpiTariff("CONFIRMED_NOT_ACTIVE")}>Нет, ещё не подключён</Button></div></div>}
                </div>}

                <div className="boris-f2-8d05cacc41d6">
                  <Button className="boris-btn-hover" onClick={saveKpiSettings} style={{background: kpiSaved ? "var(--boris-blue-soft)" : "var(--boris-blue)", color: kpiSaved ? "var(--boris-blue)" : "var(--boris-bg)", border: kpiSaved ? "1px solid var(--boris-blue)" : "none", borderRadius:"10px", padding:"9px 14px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>
                    {kpiSaved ? ("✓ Сохранено: " + kpiTargetLeads + " лид/день, до " + kpiMaxCpl + " ₽" + (kpiLeadTemp && kpiLeadTemp !== "любые" ? ", лиды: " + kpiLeadTemp : "")) : "Сохранить цель по лидам"}
                  </Button>
                  <Button className="boris-btn-hover boris-f2-f31df8f7f70d" onClick={deleteKpiSettings}>
                    🗑 Удалить цель
                  </Button>
                  <Button className="boris-btn-hover boris-f2-9e816afdf26b" onClick={checkKpi}>
                    📊 Как идут дела по цели
                  </Button>
                </div>

                {kpiCheckResult && kpiCheckResult.status === "ok" && (
                  <div className="boris-f2-8916a90632ec">
                    <div className="boris-f2-c3a72d20de66">
                      Лидов сегодня: <b className="boris-f2-b5d95bf86ec9">{kpiCheckResult.contacts_today}</b> из цели <b>{kpiCheckResult.target_leads_per_day}</b>
                      {kpiCheckResult.cost_per_lead_today != null && <> · Цена лида: <b className="boris-f2-b26be2f96183">{kpiCheckResult.cost_per_lead_today}₽</b></>}
                    </div>
                    <div className="boris-f2-de52905d633b">{humanizeBusinessText(kpiCheckResult.recommendation,"BORIS продолжает контроль цели")}</div>
                  </div>
                )}
                {kpiCheckResult && kpiCheckResult.status === "no_goal" && (
                  <div className="boris-f2-8d66ef11ddb7">{humanizeBusinessText(kpiCheckResult.message,"Цель пока не задана")}</div>
                )}

                <div style={{marginTop:16, padding:"14px 16px", border:"1px solid var(--boris-border)", borderRadius:12, background:"var(--boris-card)"}}>
                  <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,marginBottom:10}}>
                    <b style={{fontSize:15}}>Действия ИИ-маркетолога</b>
                    <Button onClick={loadKpiActionMemory} style={{padding:"6px 10px",fontSize:12}}>Обновить</Button>
                  </div>
                  {!kpiActionMemory.length && <div style={{fontSize:13,color:"var(--boris-muted)"}}>Изменений по цели пока нет.</div>}
                  {kpiActionMemory.slice(0,5).map((a:any) => {
                    const ch = a.changes || {}; const fields = Object.keys(ch);
                    return <div key={a.operation_id} style={{padding:"10px 0",borderTop:"1px solid var(--boris-border)",fontSize:13,lineHeight:1.5}}>
                      <div><b>Объявление Avito #{a.avito_item_id || "—"}</b>{a.version ? <> · версия {a.version}</> : null}</div>
                      <div style={{color:"var(--boris-muted)"}}>{humanizeBusinessText(a.reason,"Оптимизация результата")}</div>
                      {a.kpi_before && <div>До изменения: {a.kpi_before.views ?? "—"} просмотров · {a.kpi_before.contacts ?? "—"} обращений · {a.kpi_before.conversion ?? "—"}%</div>}
                      {!!fields.length && <div>Изменено: {fields.map((f:string) => `${humanizeBusinessText(f,"Поле")}: «${ch[f]?.old ?? ""}» → «${ch[f]?.new ?? ""}»`).join("; ")}</div>}
                      <div>Статус: <b>{humanizeBusinessText(a.status, "В работе")}</b> · наблюдение: {humanizeBusinessText(a.effect_status || a.observation_window?.status, "Ожидает данных")} · публикация: {humanizeBusinessText(a.safe_feed_status, "Не подготовлена")}</div>
                      {a.kpi_after && <div>После изменения: {a.kpi_after.views ?? "—"} просмотров · {a.kpi_after.contacts ?? "—"} обращений · {a.kpi_after.conversion ?? "—"}%</div>}
                    </div>
                  })}
                </div>

                <div className="boris-f2-e56f695a465a">
                  <label className="boris-f2-200d23dd578d">План по направлению (необязательно)</label>
                  <div className="boris-f2-b39f797acc2b">Введите начало id ваших объявлений — например boris-shkaf. Борис покажет, что улучшить именно в этом направлении. Оставьте пустым, чтобы посмотреть по всем.</div>
                  <div className="boris-f2-a5804c9176e1">
                    <input value={kpiPlanIdPrefix} onChange={e => setKpiPlanIdPrefix(e.target.value)} style={{...inputStyle, flex:1}} placeholder="boris-shkaf-..." />
                    <Button className="boris-btn-hover boris-f2-cf0e8474c5a6" onClick={getKpiPlan}>
                      Показать план по лидам
                    </Button>
                  </div>
                  {kpiPlan && (
                    <div className="boris-f2-832a5bb60604">
                      <div className="boris-f2-52c8cf7afe01">План для «{kpiPlan.direction}» ({kpiPlan.affected_items} объявлений)</div>
                      <ol className="boris-f2-050068336b93">
                        {kpiPlan.steps.map((s: string, i: number) => <li key={i} className="boris-f2-d982937dda0f">{s}</li>)}
                      </ol>
                      <Button className="boris-btn-hover boris-f2-cf7f2c0c42ae" onClick={executeKpiPlan} disabled={kpiPlanExecuting}>
                        {kpiPlanExecuting ? "⏳ Выполняю..." : "✅ Одобрить и внедрить"}
                      </Button>
                    </div>
                  )}
                </div>
              </BorisCard>

              <BorisCard className="b-panel b-card-eq boris-g1-card">
                <div className="boris-f2-40eee6f1a1fd">
                  <h3 className="b-title boris-f2-ea8ce7c7e635"><span className="b-icon-sm boris-f2-e179ca50e9d4 boris-g1-icon">🔄</span>Перепубликация</h3>
                  <Button className="boris-btn-hover boris-f2-f2b717e5985b" onClick={loadRepublishCandidates} disabled={republishLoading}>
                    {republishLoading ? "⏳..." : "🔄 Обновить"}
                  </Button>
                </div>
                <div className="boris-f2-efa48b908bd0">
                  <div>
                    <label className="boris-f2-5c5fa1b234c9">Мин. просмотров без контакта</label>
                    <input type="number" value={Number(republishMinViews) === 0 ? "" : republishMinViews} onChange={e => setRepublishMinViews(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <div>
                    <label className="boris-f2-5c5fa1b234c9">Дней без просмотров</label>
                    <input type="number" value={Number(republishZeroDays) === 0 ? "" : republishZeroDays} onChange={e => setRepublishZeroDays(Number(e.target.value))} style={{...inputStyle, width:"100%", boxSizing:"border-box"}} />
                  </div>
                  <label className="boris-f2-f4ba3c9dd4f8">
                    <input type="checkbox" checked={republishEnabled} onChange={e => setRepublishEnabled(e.target.checked)} />
                    Включено
                  </label>
                </div>
                <div className="boris-f2-e719c609826c">
                  <Button className="boris-btn-hover" onClick={saveRepublishSettings} style={{background: republishSettingsSaved ? "var(--boris-blue-soft)" : "var(--boris-blue)", color: republishSettingsSaved ? "var(--boris-blue)" : "var(--boris-bg)", border: republishSettingsSaved ? "1px solid var(--boris-blue)" : "none", borderRadius:"10px", padding:"9px 14px", fontSize:"15px", fontWeight:"bold", cursor:"pointer"}}>
                    {republishSettingsSaved ? "✓ Сохранено" : "Сохранить правила перепубликации"}
                  </Button>
                  <Button className="boris-btn-hover boris-f2-a94042aaa708" onClick={deleteRepublishSettings}>
                    🗑 Сбросить
                  </Button>
                </div>
                <p className="boris-f2-a6dd61d4390b">
                  {republishTotal !== null && `Проверено объявлений в фиде: ${republishTotal}.`}
                </p>
                {republishCandidates.length === 0 ? (
                  <p className="boris-f2-96046ead62a3">Кандидатов на снятие нет — все объявления эффективны или ещё слишком новые для оценки.</p>
                ) : (
                  <>
                    <div className="boris-f2-ae1456d7b46b">
                      {republishCandidates.map((c: any) => (
                        <div key={c.id} className="boris-f2-7d97bf25a61e">
                          <div className="boris-f2-b26be2f96183">{c.title}</div>
                          <div className="boris-f2-de4c8a9423c7">{humanizeBusinessText(c.reason,"Причина уточняется")}</div>
                        </div>
                      ))}
                    </div>
                    <Button className="boris-btn-hover boris-f2-3b991c9a9054" onClick={applyRepublish}>
                      🗑 Снять {republishCandidates.length} и создать замену
                    </Button>
                  </>
                )}
              </BorisCard>

              <div className="b-cell">
            <BorisCard className="b-panel b-card-eq boris-f2-8b9688e6e090 boris-g1-card">
              <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-fc42933d2a24 boris-g1-icon">📈</span>Аналитика выдачи по городам</h3>
              <p className="b-sub">Узнай сколько объявлений, какие цены и названия у конкурентов</p>
              <div className="boris-f2-79a1c5a5db96">
                <label className="b-label">Запрос</label>
                <input value={analysisQuery} onChange={e => setAnalysisQuery(e.target.value)} placeholder="продажа тротуарной плитки" className="b-input" />
              </div>
              <div className="boris-f2-7dde5e56b3f3">
                <label className="b-label">Города (через запятую)</label>
                <input value={analysisCities} onChange={e => setAnalysisCities(e.target.value)} placeholder="Москва, Санкт-Петербург" className="b-input" />
              </div>
              <Button className="b-btn b-btn-primary" onClick={runAnalysis}>
                📊 Запустить анализ
              </Button>
            </BorisCard>
            {analysisLoading && <p className="boris-f2-de52905d633b">БОРИС анализирует выдачу...{analysisProgress ? ` (город ${analysisProgress.done} из ${analysisProgress.total})` : ""}</p>}
            {analysisResults.length > 0 && (
              <div className="boris-f2-c0b553b558c8">
                {analysisResults.map((r: any, i: number) => (
                  <div key={i} className="boris-f2-8ca8ff789792">
                    <div className="boris-f2-1e7e7b12364a">📍 {r.city}</div>
                    {r.error ? <div className="boris-f2-4e518f114f40">{r.error}</div> : (
                      <>
                        <div style={{display:"grid", gridTemplateColumns: r.total_count ? "1fr 1fr" : "1fr 1fr", gap:"8px", marginBottom:"8px"}}>
                          <div className="boris-f2-75f1ce275fd1">
                            <div className="boris-f2-96046ead62a3">В выборке</div>
                            <div className="boris-f2-0ae69d771622">{r.count_found}</div>
                          </div>
                          <div className="boris-f2-75f1ce275fd1">
                            <div className="boris-f2-96046ead62a3">Ср. цена</div>
                            <div className="boris-f2-0ae69d771622">{r.avg_price || "—"}</div>
                          </div>
                        </div>
                        {r.total_count && (
                          <div className="boris-f2-9da97a0e298c">
                            <div className="boris-f2-274b6124e0e0">Всего объявлений в базе Avito</div>
                            <div className="boris-f2-6d20d2d2002c">{r.total_count.toLocaleString("ru-RU")}</div>
                          </div>
                        )}
                        <div className="boris-f2-cc477ae01908">
                          Показан реальный порядок первой страницы выдачи Авито через модуль BORIS для браузера. Это срез выдачи, а не полный объём рынка.
                        </div>
                        {r.own_positions?.length > 0 ? (
                          <div className="boris-f2-d55856efd7e2">
                            Ваши позиции: {r.own_positions.map((x: any) => `№${x.position} — ${x.title}`).join(" · ")}
                          </div>
                        ) : (
                          <div className="boris-f2-cc477ae01908">Ваших активных объявлений в первых {r.count_on_page || r.count_found || 20} позициях не найдено.</div>
                        )}
                        {typeof r.promoted_count === "number" && (
                          <div className="boris-f2-cc477ae01908">Продвигаемых объявлений в срезе: {r.promoted_count}</div>
                        )}
                        {(r.min_price || r.max_price) && (
                          <div className="boris-f2-d55856efd7e2">
                            Цены: {r.min_price || "?"}–{r.max_price || "?"} ₽
                            {r.from_cache && <span className="boris-f2-637d9820b075">(из кэша)</span>}
                          </div>
                        )}
                        {r.top5?.length > 0 && (
                          <div className="boris-f2-162811ca157a">
                            <div className="boris-f2-9ade317dcdce">Топ конкурентов в фактическом порядке выдачи:</div>
                            {r.top5.map((item: any, idx: number) => (
                              <div key={idx} className="boris-f2-24f38799f06e">
                                <div className="boris-f2-518da517eeca">
                                  <a href={item.url} target="_blank" rel="noreferrer" className="boris-f2-c7f47f1b0a13">{item.title}</a>
                                  <span className="boris-f2-ef1230f7e4ea">{item.price ? item.price + " ₽" : "—"}</span>
                                </div>
                                <div className="boris-f2-92cc9bab1829">📷 {item.photos_count ?? "?"} фото</div>
                                {item.advantages && <div className="boris-f2-2fe8aea37286">💡 {item.advantages}</div>}
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
            <BorisCard className="b-panel b-card-eq boris-g1-card">
              <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-3ab1bb33aea8 boris-g1-icon">📊</span>Анализ спроса (Яндекс.Вордстат)</h3>
              <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Зачем это нужно</summary><p className="boris-f2-183644454912">Узнайте реальный объём поисковых запросов по вашей нише, похожие запросы и долю коммерческих (с намерением купить) среди них — это помогает понять, стоит ли расширять направление.</p></details>
              <div className="boris-f2-8d05cacc41d6">
                <input value={wordstatQuery} onChange={e => setWordstatQuery(e.target.value)} placeholder="например: шкаф купе на заказ"
                  className="b-input boris-f2-7623f0554520" onKeyDown={e => { if (e.key === "Enter") analyzeWordstat(); }} />
                <Button className="b-btn b-btn-primary boris-f2-a9efa5449f9f" onClick={analyzeWordstat} disabled={wordstatLoading}>
                  {wordstatLoading ? "⏳ Анализирую..." : "🔍 Проверить спрос"}
                </Button>
              </div>

              {wordstatResult && wordstatResult.status === "error" && (
                <div className="boris-f2-8ef9224398cc">
                  {wordstatResult.message}
                </div>
              )}

              {wordstatResult && wordstatResult.status === "ok" && (
                <div className="boris-f2-67d96171210f">
                  <div className="boris-f2-fcca2d984753">
                    <div>
                      <div className="boris-f2-96046ead62a3">Всего запросов в месяц</div>
                      <div className="boris-f2-177eacbfc07c">{wordstatResult.total_count?.toLocaleString("ru-RU")}</div>
                    </div>
                    <div>
                      <div className="boris-f2-96046ead62a3">Доля коммерческих запросов</div>
                      <div className="boris-f2-5fd5675c3ec9">{wordstatResult.commercial_share_percent}%</div>
                    </div>
                  </div>

                  <div className="boris-f2-0a1d28e275a3">
                    <div>
                      <div className="b-label">Топ запросов</div>
                      <div className="boris-f2-09fa8778ccc8">
                        {wordstatResult.top_requests?.map((r: any, i: number) => (
                          <div key={i} className="boris-f2-286f6e1b1c3e">
                            <span>{r.phrase}</span><span className="boris-f2-6607ac287d11">{r.count?.toLocaleString("ru-RU")}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="b-label">Похожие запросы</div>
                      <div className="boris-f2-09fa8778ccc8">
                        {wordstatResult.associations?.map((r: any, i: number) => (
                          <div key={i} className="boris-f2-286f6e1b1c3e">
                            <span>{r.phrase}</span><span className="boris-f2-6607ac287d11">{r.count?.toLocaleString("ru-RU")}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </BorisCard>
            </div>

            {wordstatHistory.length > 0 && (
              <BorisCard className="b-panel boris-g1-card">
                <h4 className="b-title">📚 История проверок</h4>
                <div className="boris-f2-f67b86e0fb4c">
                  {wordstatHistory.map((h: any, i: number) => (
                    <div key={i}
                      onClick={() => { setWordstatQuery(h.query); setWordstatResult(h); }} className="boris-f2-4d72df668da6">
                      <span className="boris-f2-de52905d633b">{h.query}</span>
                      <span className="boris-f2-6607ac287d11">{h.total_count?.toLocaleString("ru-RU")} запросов/мес · {h.commercial_share_percent}% коммерческих</span>
                    </div>
                  ))}
                </div>
              </BorisCard>
            )}
          </div>
        )}

        {activeTab === "sales" && (
          <div className="boris-f2-2e40e87e54ae">
            {salesView === "home" && (
              <>
              <div className="boris-f2-7527de288d06">
                Два разных помощника для продаж. <b>Менеджер</b> общается с вашими покупателями в чатах Avito вместо вас. <b>Руководитель отдела продаж</b> контролирует работу ваших менеджеров: разбирает звонки и переписки, находит ошибки, даёт отчёты. Выберите, что открыть:
              </div>
              <div data-block="sales-home-cards" className="boris-f2-ab0a16882a04">
                <div onClick={()=>{ setSalesView("mop"); loadAllLimits(); }} className="boris-btn-hover boris-f2-46a4337cd852">
                  <div className="boris-f2-79ba27f21f87">🤝</div>
                  <div className="boris-f2-93385049749c">ИИ Менеджер по продажам</div>
                  <div className="boris-f2-a075d34d6126">Отвечает покупателям в чатах Avito за секунды — 24/7, ночью и в выходные, не упуская горячего клиента.</div>
                  <div className="boris-f2-4e5963f33aba">
                    <div className="boris-f2-b7bb20e44bbf"><span>⚡</span><span>Отвечает за секунды, 24/7 — ночью и в выходные</span></div>
                    <div className="boris-f2-b7bb20e44bbf"><span>🎯</span><span>Ведёт диалог к цели: берёт телефон, договаривается о шаге</span></div>
                    <div className="boris-f2-b7bb20e44bbf"><span>💰</span><span>Горячие лиды сразу вам в Telegram</span></div>
                    <div className="boris-f2-b7bb20e44bbf"><span>📊</span><span>Воронка: кто в процессе, думает, оставил телефон, отвалился</span></div>
                  </div>
                  <div className="boris-f2-58df9b55ef83">Открыть менеджера →</div>
                </div>
                <div onClick={()=>{ setSalesView("rop"); loadRopCalls(); loadSavedReports(); loadRopLimits(); loadRopSetup(); loadAllLimits(); }} className="boris-btn-hover boris-f2-962465de97ab">
                  <div className="boris-f2-79ba27f21f87">🧠</div>
                  <div className="boris-f2-15295984ffd0">ИИ Руководитель отдела продаж</div>
                  <div className="boris-f2-a075d34d6126">Разбирает переписки менеджеров: находит где сливаются лиды, какие возражения не отрабатываются, и что улучшить в скрипте. Готовит рекомендации и может сам переписать скрипт.</div>
                  <div className="boris-f2-4e5963f33aba">
                    <div className="boris-f2-d37162fdccae"><span>📞</span><span>Слушает и разбирает каждый звонок по чек-листу</span></div>
                    <div className="boris-f2-d37162fdccae"><span>🎯</span><span>Находит, где менеджер теряет клиента</span></div>
                    <div className="boris-f2-d37162fdccae"><span>⭐</span><span>Показывает сильные моменты, паразитов и точки роста</span></div>
                    <div className="boris-f2-d37162fdccae"><span>📄</span><span>Отчёты по звонкам и перепискам с рекомендациями</span></div>
                  </div>
                  <div className="boris-f2-49264b5961cf">Открыть РОПа →</div>
                </div>
              </div>
              </>
            )}
            {salesView !== "home" && (
              <Button onClick={()=>setSalesView("home")} className="boris-f2-bdfe76b7dd41">← Назад к выбору</Button>
            )}
{salesView === "rop" && ropLimits && !ropLimits.minutes?.unlimited && (ropLimits.period?.active !== true || ropShowPacks) && (
  <div data-block="rop-packages" className="boris-f2-489e16440bc3">
    <div className="boris-f2-47f0643dc590">ИИ Руководитель отдела продаж</div>
    <div className="boris-f2-9a1cb46559dd">{ropShowPacks ? "Докупка объёма до конца текущего периода" : "Чтобы начать, подключите базовый пакет на 30 дней. Дополнительные пакеты можно докупить после."}</div>
    {ropShowPacks && (
      <Button onClick={()=>setRopShowPacks(false)} className="boris-f2-86c710b7cd8e">← Назад</Button>
    )}
    {!ropShowPacks && (
      <BorisCard className="boris-card-hover boris-f2-bafdf65185a0 boris-g1-card">
        <div  className="boris-f2-b8e8eb6b48c3"/>
        <div className="boris-f2-5848c290c0a7">
          <div className="boris-f2-5430fc7dc36a">🧠</div>
          <div>
            <div className="boris-f2-a727c1f6bd68">Базовый пакет</div>
            <div className="boris-f2-5a313cf1694a">на 30 дней</div>
          </div>
        </div>
        <div className="boris-f2-e736ec0cf924">20 000 ₽</div>
        <div className="boris-f2-975023d72653">
          <div>🎧 1 500 минут разбора звонков</div>
          <div>💬 450 разборов переписок</div>
          <div>📄 30 отчётов по звонкам + 30 по перепискам</div>
        </div>
        <TermsGate product="rop" pack="rop1500" accountId={currentAccount}><RobokassaButton invoiceId="" pack="rop1500" accountId={currentAccount} /></TermsGate>
        <Button onClick={()=>openInvoice("rop1500")} className="boris-f2-3b07cbf41f32">🧾 Оплатить по счёту (для юрлиц)</Button>
      </BorisCard>
    )}
    {ropShowPacks && (
      <div className="boris-f2-b74385e7a52e">
        {[
          {pack:"rop_a300",  t:"+300 минут",   p:"5 000 ₽",  d:"300 мин · 90 переписок · 5 + 5 отчётов"},
          {pack:"rop_a500",  t:"+500 минут",   p:"7 500 ₽",  d:"500 мин · 150 переписок · 10 + 10 отчётов"},
          {pack:"rop_a700",  t:"+700 минут",   p:"10 500 ₽", d:"700 мин · 200 переписок · 15 + 15 отчётов"},
          {pack:"rop_a1000", t:"+1 000 минут", p:"14 000 ₽", d:"1000 мин · 300 переписок · 20 + 20 отчётов"},
          {pack:"rop_a1500", t:"+1 500 минут", p:"21 000 ₽", d:"1500 мин · 400 переписок · 30 + 30 отчётов"},
          {pack:"rop_a2000", t:"+2 000 минут", p:"26 000 ₽", d:"2000 мин · 550 переписок · 40 + 40 отчётов"},
        ].map((it:any)=>(
          <BorisCard key={it.pack} className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card">
            <div  className="boris-f2-f2082c9afe40"/>
            <div className="b-ref-ic boris-f2-394906a129bd">🧠</div>
            <div className="boris-f2-242b783ffd40">{it.t}</div>
            <div className="boris-f2-c852af6b2ccb">{it.p}</div>
            <div className="boris-f2-22897d0f6dd7">{it.d}</div>
            <div className="boris-f2-ff71126d36e1"><TermsGate product="rop" pack={it.pack} accountId={currentAccount}><RobokassaButton invoiceId="" pack={it.pack} accountId={currentAccount} /></TermsGate></div>
          </BorisCard>
        ))}
      </div>
    )}
  </div>
)}
{salesView === "rop" && (ropLimits?.minutes?.unlimited || ropLimits?.period?.active === true) && !ropShowPacks && (
  <div data-block="rop-calls-block" className="boris-f2-c3a12e6531d8">
    <div className="boris-f2-6102b8ff572f">
      <div className="boris-f2-4ef3b23597f6">📞 Разбор звонков</div>
      {ropLimits && !ropLimits.minutes?.unlimited && (
        <div data-el="rop-limits" className="boris-f2-d8f2a93513e0">
          <div style={{flex:1, minWidth:"150px", background: ropLimits.minutes.left>0 ? "#F4F3FF" : "#FEF3F2", border:"1px solid "+(ropLimits.minutes.left>0?"#D9D6FE":"#FDA29B"), borderRadius:"10px", padding:"10px 12px"}}>
            <div className="boris-f2-6c2427e9980a">Минуты разбора</div>
            <div style={{fontSize:"18px", fontWeight:800, color: ropLimits.minutes.left>0?"#3E1C96":"#B42318"}}>{ropLimits.minutes.left} <span className="boris-f2-41f3fc20e030">из {ropLimits.minutes.purchased}</span></div>
          </div>
          <div style={{flex:1, minWidth:"150px", background: ropLimits.reports_calls.left>0 ? "#F4F3FF" : "#FEF3F2", border:"1px solid "+(ropLimits.reports_calls.left>0?"#D9D6FE":"#FDA29B"), borderRadius:"10px", padding:"10px 12px"}}>
            <div className="boris-f2-6c2427e9980a">Отчёты по звонкам</div>
            <div style={{fontSize:"18px", fontWeight:800, color: ropLimits.reports_calls.left>0?"#3E1C96":"#B42318"}}>{ropLimits.reports_calls.left} <span className="boris-f2-41f3fc20e030">из {ropLimits.reports_calls.purchased}</span></div>
          </div>
          <div style={{flex:1, minWidth:"150px", background: ropLimits.reports_chats.left>0 ? "#F4F3FF" : "#FEF3F2", border:"1px solid "+(ropLimits.reports_chats.left>0?"#D9D6FE":"#FDA29B"), borderRadius:"10px", padding:"10px 12px"}}>
            <div className="boris-f2-6c2427e9980a">Отчёты по перепискам</div>
            <div style={{fontSize:"18px", fontWeight:800, color: ropLimits.reports_chats.left>0?"#3E1C96":"#B42318"}}>{ropLimits.reports_chats.left} <span className="boris-f2-41f3fc20e030">из {ropLimits.reports_chats.purchased}</span></div>
          </div>
          {(ropLimits.minutes.left<=0 || ropLimits.reports_calls.left<=0) && (
            <Button onClick={()=>setRopShowPacks(true)} className="boris-f2-12937d38928e">Докупить объём →</Button>
          )}
        </div>
      )}
      {ropSetup && !(ropSetup.ready && ropSetup.chats > 0) && (
        <div data-el="rop-setup" className="boris-f2-5bd4eb3696d2">
          <div className="boris-f2-6b2b845beaa7">🧠 Чтобы я заработал в полную силу</div>
          <div className="boris-f2-f67b86e0fb4c">
            {ropSetup.steps.map((st:any)=>(
              <div key={st.key} className="boris-f2-2a3060bc7f5a">
                <span className="boris-f2-61521f1d2b56">{st.ok ? "✅" : "⬜️"}</span>
                <div className="boris-f2-7623f0554520">
                  <div style={{fontSize:"15px", fontWeight:600, color: st.ok ? "#027A48" : "var(--boris-text)"}}>{st.title}</div>
                  {!st.ok && <div className="boris-f2-dfb36b35816e">{st.hint}</div>}
                </div>
                {!st.ok && st.key === "chats" && (
                  <Button onClick={syncChats} disabled={syncing} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor: syncing?"default":"pointer", opacity: syncing?0.6:1, whiteSpace:"nowrap"}}>{syncing ? "Тяну…" : "Подтянуть переписки"}</Button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {allLimits && (
        <div data-el="rop-limits-cards" className="boris-f2-59d100aa537b">
          {limitCard("Минуты разбора", allLimits.rop.minutes.left, allLimits.rop.minutes.purchased, "мин", "linear-gradient(135deg,#9B87F5,#7C5CFC)")}
          {limitCard("Отчёты по звонкам", allLimits.rop.reports_calls.left, allLimits.rop.reports_calls.purchased, "шт", "linear-gradient(135deg,var(--boris-blue),var(--boris-blue))")}
          {limitCard("Отчёты по перепискам", allLimits.rop.reports_chats.left, allLimits.rop.reports_chats.purchased, "шт", "linear-gradient(135deg,#32D583,#12805C)")}
        </div>
      )}
      <details data-el="rop-hint" className="boris-f2-9743c10ce2c2">
        <summary className="boris-f2-efa0dc9af2a2">❓ Как работает разбор звонков</summary>
        <div className="boris-f2-6a98c446690e">
          <div className="boris-f2-6c002e218094"><b className="boris-f2-a5bb441a3ee8">1.</b> Борис забирает звонки из колтрекинга Avito — кто звонил, когда, сколько говорили, ответили или пропустили.</div>
          <div className="boris-f2-6c002e218094"><b className="boris-f2-a5bb441a3ee8">2.</b> Запись разговора расшифровывается в текст.</div>
          <div className="boris-f2-6c002e218094"><b className="boris-f2-a5bb441a3ee8">3.</b> Разговор проверяется по чек-листу продаж: представился, выявил потребность, назвал цену, отработал возражения, закрыл на следующий шаг.</div>
          <div className="boris-f2-6c002e218094"><b className="boris-f2-a5bb441a3ee8">4.</b> Борис отмечает сильные моменты, слова-паразиты (и чем их заменить), точки роста и советы по речи.</div>
          <div><b className="boris-f2-a5bb441a3ee8">5.</b> По кнопке «Скачать отчёт» — PDF за выбранный период: воронка, разбивка по менеджерам и разбор каждого звонка.</div>
        </div>
      </details>
      <Button onClick={loadRopCalls} disabled={ropCallsLoading} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor: ropCallsLoading?"default":"pointer", opacity: ropCallsLoading?0.6:1}}>{ropCallsLoading ? "Загрузка..." : "🔄 Загрузить звонки"}</Button>
      <div data-el="report-controls" className="boris-f2-eac581c2251a">
        <input type="date" value={reportFrom} onChange={e=>setReportFrom(e.target.value)}  className="boris-f2-9ca25e521aed"/>
        <span className="boris-f2-b68dda3a3439">—</span>
        <input type="date" value={reportTo} onChange={e=>setReportTo(e.target.value)}  className="boris-f2-9ca25e521aed"/>
        <select data-el="report-depth" value={reportMaxCalls} onChange={e=>setReportMaxCalls(Number(e.target.value))} title="Сколько звонков разобрать в отчёте" className="boris-f2-9ca25e521aed">
          <option value={5}>5 звонков</option>
          <option value={10}>10 звонков</option>
          <option value={15}>15 звонков</option>
          <option value={30}>30 звонков</option>
          <option value={50}>50 звонков</option>
        </select>
        <Button onClick={downloadRopReport} disabled={reportLoading} style={{fontSize:"13px", fontWeight:600, color:"#5925DC", background:"#F4F3FF", border:"1px solid #D9D6FE", borderRadius:"8px", padding:"8px 14px", cursor: reportLoading?"default":"pointer", opacity: reportLoading?0.6:1}}>{reportLoading ? "Готовлю PDF..." : "📄 Скачать отчёт"}</Button>
        <Button onClick={downloadChatsReport} disabled={chatsRepLoading} style={{fontSize:"13px", fontWeight:600, color:"var(--b-ok)", background:"#ECFDF3", border:"1px solid #A6F4C5", borderRadius:"8px", padding:"8px 14px", cursor: chatsRepLoading?"default":"pointer", opacity: chatsRepLoading?0.6:1}}>{chatsRepLoading ? "Разбираю переписки..." : "💬 Отчёт по перепискам"}</Button>
        <Button onClick={()=>trainManager(false)} disabled={trainLoading} style={{fontSize:"13px", fontWeight:600, color:"var(--b-warn)", background:"#FFFAEB", border:"1px solid #FEDF89", borderRadius:"8px", padding:"8px 14px", cursor: trainLoading?"default":"pointer", opacity: trainLoading?0.6:1}}>{trainLoading ? "Готовлю…" : "🎓 Обучить ИИ-менеджера"}</Button>
      </div>
    </div>
    {ropCalls && ropCalls.status === "no_access" && (<div className="boris-f2-c638b2a16ecf">Нет доступа к колтрекингу Avito. Нужен тариф Расширенный/Максимальный с включённым Коллтрекингом и доступом ключа к CallTracking API.</div>)}
    {ropCalls && ropCalls.status === "ok" && (
      <div>
        <div className="boris-f2-2f6112e44e32">
          <div className="boris-f2-b5cc082dd7cc"><div className="boris-f2-e5d4f9e1489b">{ropCalls.total}</div><div className="boris-f2-98cb934029bf">Всего звонков</div></div>
          <div className="boris-f2-6deca15a7a93"><div className="boris-f2-3c5fe7427c85">{ropCalls.answered} · {ropCalls.answered_pct}%</div><div className="boris-f2-98cb934029bf">Отвечено</div></div>
          <div className="boris-f2-3471d411a032"><div className="boris-f2-22a06f2f6229">{ropCalls.missed} · {ropCalls.missed_pct}%</div><div className="boris-f2-98cb934029bf">Пропущено</div></div>
          <div className="boris-f2-13de4aee74dd"><div className="boris-f2-9f6988c10810">{ropCalls.total_minutes} мин</div><div className="boris-f2-98cb934029bf">разговоров</div></div>
        </div>
        <div className="boris-f2-f67b86e0fb4c">
          {(ropCalls.calls||[]).map((c:any)=>(
            <div key={c.call_id} className="boris-f2-9c984f919f7f">
              <div className="boris-f2-632009407c6b">
                <div className="boris-f2-6c2427e9980a"><b>{c.buyer_phone||"номер скрыт"}</b> · {(c.call_time||"").slice(0,16).replace("T"," ")} · {c.talk_duration>0 ? Math.round(c.talk_duration/60*10)/10+" мин" : "не отвечен"}</div>
                {c.talk_duration>0 && <Button onClick={()=>analyzeRopCall(c.call_id)} className="boris-f2-9933ea8fad56">Разобрать звонок</Button>}
              </div>
              {ropDetailFor===c.call_id && ropDetail && (
                <div className="boris-f2-b7a456b47941">
                  {ropDetail.loading && <div className="boris-f2-6607ac287d11">🎙 Расшифровываю и разбираю звонок...</div>}
                  {ropDetail.status==="not_ready" && <div className="boris-f2-146025a3f736">Запись ещё не готова (появляется до 30 мин после звонка).</div>}
                  {ropDetail.status==="error" && <div className="boris-f2-5659a18ecccb">Ошибка: {humanizeBusinessText(ropDetail.message,"не удалось разобрать звонок")}</div>}
                  {ropDetail.status==="ok" && ropDetail.analysis && (
                    <div>
                      <div className="boris-f2-e4ad4a163b5e"><b>{ropDetail.analysis.client_name||"Собеседник"}</b> · <span className="boris-f2-4f6b82ef2f20">{humanizeBusinessText(ropDetail.analysis.role,"Собеседник")}</span> · балл <b>{ropDetail.analysis.score}/100</b></div>
                      <div className="boris-f2-94d11c1b52fa">
                        {(ropDetail.analysis.checklist||[]).map((it:any,i:number)=>(<div key={i} style={{fontSize:"13px", color: it.done?"#027A48":"#B42318"}}>{it.done?"✅":"❌"} {it.item}</div>))}
                      </div>
                      {ropDetail.analysis.failed_at && <div className="boris-f2-d982937dda0f"><b className="boris-f2-5659a18ecccb">Провал:</b> {ropDetail.analysis.failed_at}</div>}
                      {ropDetail.analysis.recommendation && <div><b className="boris-f2-f153efea0508">Совет:</b> {ropDetail.analysis.recommendation}</div>}
                      {(ropDetail.analysis.filler_phrases||[]).length>0 && (<div className="boris-f2-b1ecc496e0c7"><b className="boris-f2-146025a3f736">🗣 Фразы-паразиты:</b><div className="boris-f2-b7ecdfaf5aa3">{ropDetail.analysis.filler_phrases.map((x:string,i:number)=><span key={i} className="boris-f2-45610cde3064">{x}</span>)}</div></div>)}
                      {(ropDetail.analysis.strong_moments||[]).length>0 && (<div className="boris-f2-b1ecc496e0c7"><b className="boris-f2-f153efea0508">⭐ Крутые фишки:</b><ul className="boris-f2-7b9fed767c56">{ropDetail.analysis.strong_moments.map((x:string,i:number)=><li key={i} className="boris-f2-bc6da60315b4">{x}</li>)}</ul></div>)}
                      {(ropDetail.analysis.client_hooks||[]).length>0 && (<div className="boris-f2-b1ecc496e0c7"><b className="boris-f2-f55ed7925da1">🎣 Зацепило клиента:</b><ul className="boris-f2-7b9fed767c56">{ropDetail.analysis.client_hooks.map((x:string,i:number)=><li key={i} className="boris-f2-6c2427e9980a">{x}</li>)}</ul></div>)}
                      {(ropDetail.analysis.client_turnoffs||[]).length>0 && (<div className="boris-f2-b1ecc496e0c7"><b className="boris-f2-5659a18ecccb">🧊 Оттолкнуло:</b><ul className="boris-f2-7b9fed767c56">{ropDetail.analysis.client_turnoffs.map((x:string,i:number)=><li key={i} className="boris-f2-6c2427e9980a">{x}</li>)}</ul></div>)}
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
            {salesView === "mop" && salesItems.length === 0 && (<div className="boris-f2-e0ebb78c5274"><div className="boris-f2-fa67ab694c2a"><div className="boris-f2-e59f0f96c3e5">С чего начать</div><div className="boris-f2-cb57d9bd0ff7">Объявлений для менеджера нет. Сначала опубликуйте объявления на Avito.</div></div><Button className="boris-btn-hover boris-f2-faac849ef21d" onClick={() => setActiveTab("listings")}>Перейти к объявлениям</Button></div>)}
            {salesView === "mop" && allLimits && (
              <div data-el="mop-limits" className="boris-f2-aee817443320">
                {limitCard("Сообщения менеджера", allLimits.mop.left, allLimits.mop.purchased, "шт", "linear-gradient(135deg,#FDB022,#F79009)")}
              </div>
            )}
            <BorisCard className="b-panel b-card-eq boris-g1-card" style={{width:"100%", display: (salesView === "mop" && (managerBalance?.unlimited || managerBalance?.active === true)) ? undefined : "none"}}>
              <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-e179ca50e9d4 boris-g1-icon">🤝</span>Виртуальный менеджер по продажам</h3>
              <div className="b-meta boris-f2-a3a5568692be">Борис сам отвечает покупателям в чатах Avito по вашему прайсу и скриптам — отмечайте галочками объявления, где он должен работать</div>
              <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Как работает подтверждение ответов</summary><p className="boris-f2-183644454912">Отвечает клиентам в чатах Avito от лица вашей компании. Каждый ответ приходит вам на подтверждение (✅ отправить / ✏️ поправить / ❌ пропустить) — в Telegram и здесь.</p></details>
              {salesUsage && (
                <div className="boris-f2-32bc92ff25d1">
                  📊 За месяц: {salesUsage.calls || 0} ответов · себестоимость ~{(salesUsage.cost_rub || 0).toFixed(2)}₽
                </div>
              )}
            </BorisCard>

            <BorisCard className="b-panel b-card-eq boris-g1-card" style={{width:"100%", display: (salesView === "mop") ? undefined : "none"}}>
              <div className="boris-f2-4b122a01f365">
                <h3 className="b-title boris-f2-2223b51c2b1b"><span className="b-icon-sm boris-f2-3ab1bb33aea8 boris-g1-icon">📋</span>Объявления для менеджера</h3>
                {(!managerBalance || (!managerBalance.unlimited && managerBalance.active !== true)) && (
  <div data-block="mop-packages" className="boris-f2-83b63caf047e">
    <div className="boris-f2-47f0643dc590">ИИ Менеджер по продажам</div>
    <div className="boris-f2-9a1cb46559dd">Подключите базовый пакет на 30 дней. Дополнительные сообщения можно докупить после.</div>
    <BorisCard className="boris-card-hover boris-f2-234a4891d533 boris-g1-card">
      <div  className="boris-f2-0c0091162270"/>
      <div className="boris-f2-5848c290c0a7">
        <div className="boris-f2-1ec79ae16acf">🤝</div>
        <div>
          <div className="boris-f2-a727c1f6bd68">Базовый пакет</div>
          <div className="boris-f2-1cb86fafe66b">на 30 дней</div>
        </div>
      </div>
      <div className="boris-f2-e736ec0cf924">10 000 ₽</div>
      <div className="boris-f2-975023d72653">💬 1 700 сообщений покупателям в чатах Avito</div>
      <TermsGate product="mop" pack="msg1700" accountId={currentAccount}><RobokassaButton invoiceId="" pack="msg1700" accountId={currentAccount} /></TermsGate>
    </BorisCard>
    {(managerBalance?.active === true || managerBalance?.unlimited) && (<>
    <div className="boris-f2-47c9265beeb1">Дополнительные сообщения</div>
    <div className="boris-f2-9f2141c314b2">
      {[
        {pack:"msg2000", t:"+2 000 сообщений", p:"8 000 ₽"},
        {pack:"msg3000", t:"+3 000 сообщений", p:"11 000 ₽"},
      ].map((it:any)=>(
        <BorisCard key={it.pack} className="boris-card-hover boris-f2-f6bac6124541 boris-g1-card">
          <div  className="boris-f2-63fae23744eb"/>
          <div className="b-ref-ic boris-f2-59af67595616">💬</div>
          <div className="boris-f2-242b783ffd40">{it.t}</div>
          <div className="boris-f2-f49284a311a5">{it.p}</div>
          <div className="boris-f2-ff71126d36e1"><TermsGate product="mop" pack={it.pack} accountId={currentAccount}><RobokassaButton invoiceId="" pack={it.pack} accountId={currentAccount} /></TermsGate></div>
        </BorisCard>
      ))}
    </div>
    </>)}
  </div>
)}
                {managerBalance?.active === true && (
  <div data-block="manager-balance-block" style={{margin:"0 0 16px", padding:"14px 16px", borderRadius:"12px", border:"1px solid var(--boris-border)", background: managerBalance.active ? "#F6FEF9" : "#FFFBFA"}}>
    <div className="boris-f2-bfcbb5f32b3e">
      <div>
        <div className="boris-f2-29be9e0cb513">
          {managerBalance.active ? "✅ Пакет менеджера активен" : "⛔ Пакет менеджера не оплачен"}
        </div>
        <div className="boris-f2-f7a23c0d3f31">
          Куплено {managerBalance.purchased} · использовано {managerBalance.used} · осталось <b style={{color: managerBalance.left > 100 ? "#027A48" : "#B54708"}}>{managerBalance.left}</b> сообщений
        </div>
      </div>
      <Button onClick={() => setTariffsFinanceMode(true)} className="boris-f2-c84cb07c658e">
        Докупить сообщения →
      </Button>
    </div>
    {managerBalance.purchased > 0 && (
      <div className="boris-f2-46cb4fb1c67c">
        <div style={{height:"100%", width: `${Math.max(0, Math.min(100, Math.round(managerBalance.left / managerBalance.purchased * 100)))}%`, background: managerBalance.left > 100 ? "#12B76A" : "#F79009"}} />
      </div>
    )}
  </div>
)}
{managerBalance?.active === true && (
  <div data-block="reminders-settings-block" className="boris-f2-65a566f4c4a4">
    <div className="boris-f2-83ba15bdba44">
      <div className="boris-f2-29be9e0cb513">🔔 Напоминания по застрявшим лидам</div>
      <label className="boris-f2-bf5e42d69f4f">
        <input type="checkbox" checked={!!reminderSettings.enabled} onChange={e=>saveReminderSettings({...reminderSettings, enabled:e.target.checked})} />
        {reminderSettings.enabled ? "Вкл" : "Выкл"}
      </label>
    </div>
    {reminderSettings.enabled && (
      <div className="boris-f2-9374e8421025">
        <div className="boris-f2-cbfc2d0836fc">Напоминать в Telegram по стадиям:</div>
        <div className="boris-f2-eee7876950a8">
          {["ушёл подумать","оставил телефон","в процессе"].map(st => {
            const on = (reminderSettings.stages||[]).includes(st);
            return (
              <Button key={st} onClick={()=>toggleReminderStage(st)} style={{fontSize:"13px", padding:"7px 12px", borderRadius:"8px", cursor:"pointer", border: on?"1px solid #7F56D9":"1px solid var(--boris-border)", background: on?"#F4F3FF":"#fff", color: on?"#5925DC":"var(--boris-text-2)", fontWeight: on?600:400}}>
                {on ? "✓ " : ""}{st}
              </Button>
            );
          })}
        </div>
        <div className="boris-f2-93904488e9e7">
          Напоминать через
          <input type="number" min={1} max={30} value={Number(reminderSettings.delay_days) === 0 ? "" : reminderSettings.delay_days}
            onChange={e=>saveReminderSettings({...reminderSettings, delay_days: Math.max(1, parseInt(e.target.value)||1)})}
  className="boris-f2-0d7f5ad805c7"/>
          дн. молчания клиента
        </div>
        {reminderSaved && <div className="boris-f2-976ceb9ee572">✓ Сохранено</div>}
      </div>
    )}
  </div>
)}

{salesView === "rop" && (
  <div data-block="rop-reports-block" className="boris-f2-c3a12e6531d8">
    <div className="boris-f2-2d030424a3b5">
      <div className="boris-f2-4ef3b23597f6">📁 Отчёты</div>
      <div className="boris-f2-0889360d6347">
        <Button onClick={loadSavedReports} className="boris-f2-33bcb75c55f2">🔄 Обновить</Button>
        <Button onClick={()=>deleteSavedReports(false)} disabled={selectedReports.length===0} style={{fontSize:"13px", fontWeight:600, color:"var(--b-danger-text)", background:"#fff", border:"1.5px solid #F04438", borderRadius:"8px", padding:"7px 12px", cursor: selectedReports.length? "pointer":"default", opacity: selectedReports.length?1:0.5}}>Удалить выбранные</Button>
        <Button onClick={()=>deleteSavedReports(true)} disabled={savedReports.length===0} style={{fontSize:"13px", fontWeight:600, color:"var(--b-danger-text)", background:"#fff", border:"1.5px solid #F04438", borderRadius:"8px", padding:"7px 12px", cursor: savedReports.length? "pointer":"default", opacity: savedReports.length?1:0.5}}>Удалить все</Button>
      </div>
    </div>
    <div className="boris-f2-0cfd15ff8345">Готовые отчёты хранятся <b>30 дней</b>, затем удаляются безвозвратно. Скачивание сохранённого отчёта — бесплатно, повторный разбор звонков не запускается.</div>
    {savedReports.length === 0 && <div className="boris-f2-e88a7eb51065">Пока нет сохранённых отчётов. Сформируйте отчёт кнопкой «📄 Скачать отчёт» выше — он появится здесь.</div>}
    {savedReports.length > 0 && (
      <div className="boris-f2-f67b86e0fb4c">
        {savedReports.map((rp:any)=>(
          <div key={rp.id} className="boris-f2-c0cc97166d65">
            <input type="checkbox" checked={selectedReports.includes(rp.id)} onChange={()=>setSelectedReports(prev=> prev.includes(rp.id) ? prev.filter(x=>x!==rp.id) : [...prev, rp.id])}  className="boris-f2-ee84c4c4ffbb"/>
            <div className="boris-f2-fa67ab694c2a">
              <div className="boris-f2-aadd58ac5106">
                <span style={{fontSize:"11px", fontWeight:700, borderRadius:"20px", padding:"2px 10px", background: rp.kind==="chats" ? "#ECFDF3" : "var(--boris-blue-soft)", color: rp.kind==="chats" ? "#027A48" : "var(--boris-blue)"}}>{rp.kind==="chats" ? "Переписки" : "Звонки"}</span>
                <span>{rp.date_from} — {rp.date_to}</span>
              </div>
              <div className="boris-f2-6e54a943515e">{rp.created_at} · {rp.kind==="chats" ? "диалогов" : "звонков"} {rp.calls_total} · разобрано {rp.calls_analyzed} · {rp.size_kb} КБ · хранится ещё {rp.days_left} дн.</div>
            </div>
            <Button onClick={()=>downloadSavedReport(rp.id)} className="boris-f2-b05c0e8a20bd">Скачать</Button>
          </div>
        ))}
      </div>
    )}
  </div>
)}
{managerBalance?.active === true && (
  <div data-block="dlg-analysis-block" className="boris-f2-2dfe94e58a8d">
    <div className="boris-f2-bfcbb5f32b3e">
      <div className="boris-f2-29be9e0cb513">📊 Анализ диалогов на эффективность</div>
      <div className="boris-f2-b9bbe540d342">
        {dlgAnalysis && dlgAnalysis.summary && (
          <Button onClick={()=>setDlgAnalysisOpen(!dlgAnalysisOpen)} className="boris-f2-5aca7c9605a1">{dlgAnalysisOpen ? "Свернуть" : "Показать"}</Button>
        )}
        <Button onClick={runDlgAnalysis} disabled={dlgAnalysisLoading} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"8px 14px", cursor: dlgAnalysisLoading?"default":"pointer", opacity: dlgAnalysisLoading?0.6:1}}>{dlgAnalysisLoading ? "Анализирую..." : "🔄 Анализировать"}</Button>
      </div>
    </div>
    {dlgAnalysisOpen && dlgAnalysis && dlgAnalysis.summary && (
      <div className="boris-f2-ed72051ca236">
        {dlgAnalysis.generated_at && <div className="boris-f2-6d42b6fc2b27">Обновлено {new Date(dlgAnalysis.generated_at).toLocaleString("ru-RU")} · диалогов: {dlgAnalysis.analyzed}</div>}
        {(dlgAnalysis.summary.bottlenecks||[]).length>0 && (<div className="boris-f2-af3fee87a1e7"><b className="boris-f2-5659a18ecccb">🔻 Где сливаются лиды:</b><ul className="boris-f2-20ccda653c33">{dlgAnalysis.summary.bottlenecks.map((x:string,i:number)=><li key={i} className="boris-f2-c6f92fb33803">{x}</li>)}</ul></div>)}
        {(dlgAnalysis.summary.objections||[]).length>0 && (<div className="boris-f2-af3fee87a1e7"><b className="boris-f2-146025a3f736">🗣 Частые возражения:</b><ul className="boris-f2-20ccda653c33">{dlgAnalysis.summary.objections.map((x:string,i:number)=><li key={i} className="boris-f2-c6f92fb33803">{x}</li>)}</ul></div>)}
        {(dlgAnalysis.summary.script_tips||[]).length>0 && (<div className="boris-f2-af3fee87a1e7"><b className="boris-f2-f153efea0508">✅ Что улучшить в скрипте:</b><ul className="boris-f2-20ccda653c33">{dlgAnalysis.summary.script_tips.map((x:string,i:number)=><li key={i} className="boris-f2-c6f92fb33803">{x}</li>)}</ul></div>)}
        {(dlgAnalysis.lost||[]).length>0 && (<div className="boris-f2-67630394752d"><b className="boris-f2-de52905d633b">Разбор ушедших лидов:</b>{dlgAnalysis.lost.map((l:any,i:number)=><div key={i} className="boris-f2-733f411effdf">• {humanizeBusinessText(l.reason,"Причина потери уточняется")}</div>)}</div>)}
      </div>
    )}
  </div>
)}
{dlgAnalysis && dlgAnalysis.summary && (
  <div data-block="script-buttons-block" className="boris-f2-035cfacd10a9">
    <div className="boris-f2-f2baf716b71c">📝 Скрипт для менеджера по этим рекомендациям</div>
    <div className="boris-f2-760171c609da">
      <Button onClick={makeScriptAuto} disabled={scriptGenLoading} style={{fontSize:"13px", fontWeight:600, color:"#fff", background:"#7F56D9", border:"none", borderRadius:"8px", padding:"9px 16px", cursor: scriptGenLoading?"default":"pointer", opacity: scriptGenLoading?0.6:1}}>{scriptGenLoading ? "Борис пишет скрипт..." : "✍️ Сделай скрипт сам"}</Button>
      <Button onClick={()=>setScriptManualOpen(!scriptManualOpen)} className="boris-f2-3bc31281cc8a">✏️ Я напишу сам скрипт</Button>
      {scriptSaved && <span className="boris-f2-2a48ca45e765">✓ Сохранено в активный скрипт менеджера</span>}
    </div>
    {scriptManualOpen && (
      <div className="boris-f2-9374e8421025">
        <textarea value={scriptManualText} onChange={e=>setScriptManualText(e.target.value)} placeholder="Напишите скрипт для менеджера: правила ведения диалога, отработку возражений, как вести к контакту..." rows={5}  className="boris-f2-544be6848539"/>
                <div className="boris-f2-2fd7789b397a">
                  <Button onClick={()=>pcCheck("script","manager",scriptManualText)} className="boris-f2-33bcb75c55f2">🧠 Проверить промт Борисом</Button>
                  {pcField==="script" && pcLoading && <div className="boris-f2-d4f2034c960f">Борис читает…</div>}
                  {pcField==="script" && pcData && !pcLoading && (
                    <div className="boris-f2-54ac54c00c71">
                      <div className="boris-f2-316f53899bf6">{pcData.verdict}</div>
                      {(pcData.issues||[]).map((it:any,i:number)=>(
                        <div key={i} className="boris-f2-206d5b50e31f">• <b>{it.problem}</b> — {it.fix}</div>
                      ))}
                      {pcData.improved && (
                        <Button onClick={()=>{ setScriptManualText(pcData.improved); setPcData(null); }} className="boris-f2-a79b259c99b7">✍️ Заменить на улучшенный</Button>
                      )}
                    </div>
                  )}
                </div>
        <Button onClick={saveManualScript} className="boris-f2-4ae90575a2ab">Сохранить скрипт</Button>
      </div>
    )}
    {scriptGen && (
      <div className="boris-f2-d4cd9c3db14c">{scriptGen}</div>
    )}
  </div>
)}
{salesLeadStats && salesLeadStats.total > 0 && (
  <div data-block="lead-funnel-block" className="boris-f2-66cb175922c9">
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
          <div className="boris-f2-e41894b51ae0">{st.label}</div>
        </div>
      );
    })}
    <div className="boris-f2-9c8fefd7ea65">
      <div className="boris-f2-9f858cadfe26">{salesLeadStats.phone_conversion}%</div>
      <div className="boris-f2-e41894b51ae0">Конверсия в телефон</div>
    </div>
  </div>
)}
{salesLeadOpen && (
  <div data-block="lead-list-block" className="boris-f2-b06a8d0c1bda">
    <div className="boris-f2-2bcda3a2ea43">Лиды: {salesLeadOpen}</div>
    {salesLeadListLoading && <div className="boris-f2-be57dac96f01">Загрузка...</div>}
    {!salesLeadListLoading && salesLeadList.length === 0 && <div className="boris-f2-be57dac96f01">Нет лидов на этой стадии</div>}
    {salesLeadList.map((L:any, i:number) => (
      <div key={i} style={{display:"flex", flexWrap:"wrap", alignItems:"center", gap:"10px", padding:"10px 0", borderTop: i>0 ? "1px solid var(--boris-border-soft)" : "none"}}>
        <div className="boris-f2-bd110a61e5c3">
          <div className="boris-f2-9a938ed79996">{L.item_title || "Объявление не определено"}</div>
          <div className="boris-f2-e41894b51ae0">{L.last_text || ""} · {L.msg_count} сообщ.</div>
        </div>
        {L.phone && (
          <a href={`tel:${L.phone}`} className="boris-f2-bfb5d476669e">📞 {L.phone}</a>
        )}
        <a href={L.chat_url} target="_blank" rel="noopener noreferrer" className="boris-f2-74e1769773f2">💬 Открыть чат</a>
        {L.item_url && (
          <a href={L.item_url} target="_blank" rel="noopener noreferrer" className="boris-f2-d4b1764000de">📄 Объявление</a>
        )}
      </div>
    ))}
  </div>
)}
<Button className="boris-btn-hover boris-f2-77baf42af913" onClick={loadSalesItems}>
                  {salesLoading ? "Загрузка..." : "🔄 Обновить список"}
                </Button>
              </div>
              <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Как работают отметки</summary><p className="boris-f2-183644454912">Отметьте галочками объявления, по которым менеджер должен вести переписку. Если ничего не отмечено — менеджер работает по всем вашим объявлениям (кроме вакансий, они отсеиваются автоматически). Дальше нажимать ничего не нужно: как только вы отметили объявления, менеджер сразу начинает отвечать покупателям в их чатах, а результаты появляются в воронке лидов выше. Каждый ответ приходит вам на подтверждение в Telegram. Дальше нажимать ничего не нужно: как только вы отметили объявления, менеджер сразу начинает отвечать покупателям в их чатах, а результаты появляются в воронке лидов выше. Каждый ответ приходит вам на подтверждение в Telegram.</p></details>

              {salesItems.length === 0 && !salesLoading && (
                <p className="boris-f2-de52905d633b">Нажмите «Обновить», чтобы загрузить список объявлений.</p>
              )}

              <div className="boris-f2-f67b86e0fb4c">
                {salesItems.map((item) => (
                  <label key={item.item_id} style={{display:"flex", alignItems:"center", gap:"12px", background:"var(--boris-surface)", borderRadius:"8px", padding:"12px 14px", cursor:"pointer", border: salesWhitelist.includes(item.item_id) ? "1px solid #F79009" : "1px solid #EEF2FA"}}>
                    <input
                      type="checkbox"
                      checked={salesWhitelist.includes(item.item_id)}
                      onChange={() => toggleSalesWhitelist(item.item_id)}

                     className="boris-f2-af736c30d5ef"/>
                    <span style={{flex:1, fontSize:"15px", color: item.is_job_posting ? "var(--boris-text-3)" : "var(--boris-text)"}}>
                      {item.title}
                      {cityFromUrl(item.url) && <span className="boris-f2-81939b092298">· {cityFromUrl(item.url)}</span>}
                      {item.is_job_posting && <span className="boris-f2-596961164eac">вакансия — не для менеджера</span>}
                    </span>
                  </label>
                ))}
              </div>
            </BorisCard>
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
            {icon:"⚙️", t:"Админ-панель", d:"Меняете тексты, цены и заявки сами, без программиста", g:"linear-gradient(135deg,var(--boris-blue),var(--boris-blue))"},
            {icon:"🔍", t:"Внутреннее SEO", d:"Настройка мета-тегов и заголовков под поиск", g:"linear-gradient(135deg,#34D399,#12B76A)"},
            {icon:"✏️", t:"Лёгкое редактирование", d:"Правки контента прямо в панели, за минуту", g:"linear-gradient(135deg,#A78BFA,#7C3AED)"},
            {icon:"📊", t:"Клиенты и заявки внутри сайта", d:"Заявки собираются и хранятся в кабинете (по желанию)", g:"linear-gradient(135deg,#FBBF24,#F79009)"},
            {icon:"🎯", t:"Маркетинг-фишки", d:"Калькуляторы, таймеры, всплывающие акции, квизы", g:"linear-gradient(135deg,#F87171,#EF4444)"},
            {icon:"🧠", t:"Копирайтинг + НЛП", d:"Продающие тексты по проверенным формулам", g:"linear-gradient(135deg,#22D3EE,#0891B2)"},
          ];
          return (
          <div className="boris-f2-cad980f4b707">
            {/* ГЕРОЙ */}
            <BorisCard className="b-panel boris-f2-b60f8bfddfe7">
              <div className="boris-f2-0888eb7156b9">
                <div className="boris-f2-4a5c081831e1">
                  <div className="boris-f2-896c7bb859af">🌐 Создание сайтов под ключ</div>
                  <h2 className="boris-f2-b417b729e2c3">Сайт, который продаёт<br/>за вас</h2>
                  <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Что входит в сайт</summary><p className="boris-f2-183644454912">Современный дизайн, продающий копирайтинг, админка и все маркетинг-инструменты. Готовый сайт под ваш бизнес — быстро и без головной боли.</p></details>
                  <Button onClick={() => setSiteOrderOpen(true)} className="boris-btn-hover boris-f2-ffa932c06236">🚀 Заказать сайт</Button>
                </div>
                <div className="boris-f2-933b817d4d03">
                  <div className="boris-f2-83afb3950ea3">🎁 ЦЕНА ПО ПОДПИСКЕ</div>
                  <div className="boris-f2-6119f0817fd3">25 000 ₽</div>
                  <div className="boris-f2-b267d06df36c">50 000 ₽</div>
                  <div className="boris-f2-14d807926d0d">{hasSub ? "Ваша подписка активна ✅" : "При активной подписке тарифа «Автопилот 1»"}</div>
                </div>
              </div>
            </BorisCard>

            {/* ПРИМЕРЫ РАБОТ — карусель как на лендинге */}
            <h3 className="b-title boris-f2-845f0c54ca2f"><span className="b-icon-sm boris-f2-79a4d50add5b boris-g1-icon">🖼</span>Примеры наших работ</h3>
            <p className="b-sub boris-f2-5b6aad9a3fd6">Реальные сайты, сделанные для разных ниш — от бетонного завода до бьюти-студии</p>
            <div className="sb-marquee boris-f2-562d94e7217b">
              <div className="sb-marquee-track">
                {[0,1].map(dup => screens.map((sc,i) => (
                  <div key={dup + "-" + i} className="sb-slide boris-f2-9855cb49dbaa" onClick={() => { setSbLightbox(`/images/_sitebuild/screens/${sc.img}`); setSbZoom(1); }}>
                    <img src={`/images/_sitebuild/screens/${sc.img}`} alt={sc.title} loading="lazy"  className="boris-f2-61f1bb6c9e17"/>
                    <div className="boris-f2-9681bee9e88b">
                      <span className="boris-f2-47d89a03d912">{sc.title}</span>
                      <span className="boris-f2-dc4d44def1df">{sc.tag}</span>
                    </div>
                  </div>
                )))}
              </div>
            </div>

            {/* ВИДЕО */}
            <h3 className="b-title boris-f2-845f0c54ca2f"><span className="b-icon-sm boris-f2-3cd824b25201 boris-g1-icon">🎬</span>Как это выглядит вживую</h3>
            <p className="b-sub boris-f2-5b6aad9a3fd6">Живые записи готовых сайтов — посмотрите, как всё работает</p>
            {userRole === "owner" && (
              <BorisCard className="b-panel boris-f2-1de900a8c268">
                <div className="boris-f2-10b40a7ee9e0">🔧 Загрузка видео (видно только вам)</div>
                <div className="boris-f2-36370d6a0265">
                  <input value={sbVideoTitle} onChange={e => setSbVideoTitle(e.target.value)} placeholder="Название видео (напр. Сайт грузоперевозки)" style={{...inputStyle, width:"auto", minWidth:"280px"}} />
                  <label className="boris-btn-hover boris-f2-ff65694deead">
                    {sbVideoUploading ? "⏳ Загрузка..." : "🎬 Выбрать видео"}
                    <input type="file" accept="video/*" disabled={sbVideoUploading} onChange={e => sbUploadVideo(e.target.files)}  className="boris-f2-c8be1ccba60b"/>
                  </label>
                  {sbVideoMsg && <span style={{fontSize:"14px", color: sbVideoMsg.startsWith("✅") ? "#12B76A" : "#F04438"}}>{sbVideoMsg}</span>}
                </div>
              </BorisCard>
            )}
            {sbVideos.length > 0 ? (
              <div className="sb-marquee boris-f2-2a28d13f8dfd">
                <div className="sb-marquee-track sb-video-track">
                  {[...sbVideos, ...sbVideos].map((v,i) => (
                    <div key={i} className="sb-slide">
                      <video src={v.url} controls preload="metadata"  className="boris-f2-c576eb62e222"/>
                      <div className="boris-f2-9681bee9e88b">
                        <span className="boris-f2-47d89a03d912">{v.title}</span>
                        {userRole === "owner" && (
                          <span className="boris-f2-6b00e75351fc">
                            <Button onClick={() => sbRenameVideo(v.filename, v.title)} className="boris-f2-692d8ac3140b">✏️ Переименовать</Button>
                            <Button onClick={() => sbDeleteVideo(v.filename)} className="boris-f2-f20bf8b7b8c0">🗑 Удалить</Button>
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <BorisCard className="b-panel boris-f2-ba623a625f77">Видео пока не добавлены</BorisCard>
            )}

            {/* ВОЗМОЖНОСТИ */}
            <div className="boris-f2-2dce2ae7eae8">
              {features.map((f,i) => (
                <BorisCard key={i} className="b-panel b-card-eq boris-btn-hover boris-f2-420a7387966e boris-g1-card">
                  <span className="b-icon-sm boris-g1-icon" style={{background:f.g, marginBottom:"12px"}}>{f.icon}</span>
                  <div className="boris-f2-3c11d48ee51a">{f.t}</div>
                  <div className="boris-f2-a61395ac1f1e">{f.d}</div>
                </BorisCard>
              ))}
            </div>

            {userRole === "owner" && (
              <BorisCard className="b-panel boris-f2-55e7e01b0b3e">
                <div className="boris-f2-10b40a7ee9e0">🔧 Загрузка скриншотов (видно только вам)</div>
                <div className="boris-f2-36370d6a0265">
                  <select className="b-select" value={sbUploadName} onChange={e => setSbUploadName(e.target.value)} style={{...inputStyle, width:"auto", minWidth:"220px"}}>
                    <option value="beton_zavod.png">Бетонный завод</option>
                    <option value="almaznoe_burenie.png">Алмазное бурение</option>
                    <option value="gruzoperevozki.png">Грузоперевозки</option>
                    <option value="arenda_spectehniki.png">Аренда спецтехники</option>
                    <option value="landshaftnyy_dizayn.png">Ландшафтный дизайн</option>
                    <option value="lazernaya_epilyaciya.png">Лазерная эпиляция</option>
                    <option value="stihi_pesni_na_zakaz.png">Стихи и песни</option>
                  </select>
                  <label className="boris-btn-hover boris-f2-ff65694deead">
                    {sbUploading ? "⏳ Загрузка..." : "📁 Выбрать файл"}
                    <input type="file" accept="image/*" disabled={sbUploading} onChange={e => sbUploadScreen(e.target.files)}  className="boris-f2-c8be1ccba60b"/>
                  </label>
                  {sbUploadMsg && <span style={{fontSize:"14px", color: sbUploadMsg.startsWith("✅") ? "#12B76A" : "#F04438"}}>{sbUploadMsg}</span>}
                </div>
              </BorisCard>
            )}
            {/* МОДАЛКА ЗАЯВКИ */}
            {siteOrderOpen && (
              <div onClick={() => { setSiteOrderOpen(false); setSiteOrderDone(false); }} className="boris-f2-4a1722eaef66">
                <div onClick={e => e.stopPropagation()} className="boris-f2-af5bd04ccbfb">
                  {siteOrderDone ? (
                    <div className="boris-f2-d89f31f6be08">
                      <div className="boris-f2-5a27f7cb1853">✅</div>
                      <h3 className="boris-f2-698214a3705a">Заявка отправлена!</h3>
                      <p className="boris-f2-2974d3ad2876">Свяжемся с вами в ближайшее время по указанному телефону.</p>
                      <Button onClick={() => { setSiteOrderOpen(false); setSiteOrderDone(false); setSiteOrderName(""); setSiteOrderPhone(""); setSiteOrderNiche(""); setSiteOrderComment(""); }} className="boris-btn-hover boris-f2-faf2a05cf442">Закрыть</Button>
                    </div>
                  ) : (
                    <>
                      <h3 className="boris-f2-3484d2ea4ccf">🚀 Заказать сайт</h3>
                      <p className="boris-f2-69cdbf8e4968">Оставьте контакты — рассчитаем стоимость под ваш бизнес и свяжемся с вами.</p>
                      <div className="boris-f2-62c135d5726a">
                        <input value={siteOrderName} onChange={e => setSiteOrderName(e.target.value)} placeholder="Ваше имя" style={inputStyle} />
                        <input value={siteOrderPhone} onChange={e => setSiteOrderPhone(e.target.value)} placeholder="Телефон * (обязательно)" style={inputStyle} />
                        <input value={siteOrderNiche} onChange={e => setSiteOrderNiche(e.target.value)} placeholder="Сфера бизнеса (например: доставка цветов)" style={inputStyle} />
                        <textarea value={siteOrderComment} onChange={e => setSiteOrderComment(e.target.value)} placeholder="Комментарий, пожелания (необязательно)" rows={3} style={{...inputStyle, resize:"vertical" as any}} />
                        <Button onClick={submitSiteOrder} disabled={siteOrderSending} className="boris-btn-hover" style={{background: siteOrderSending ? "var(--boris-border)" : "var(--boris-blue)", color:"#fff", border:"none", borderRadius:"10px", padding:"14px", fontWeight:700, fontSize:"16px", cursor: siteOrderSending ? "default" : "pointer"}}>{siteOrderSending ? "⏳ Отправка..." : "Отправить заявку"}</Button>
                        <Button onClick={() => setSiteOrderOpen(false)} className="boris-f2-bde5945dc016">Отмена</Button>
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
              <div onClick={() => setSbLightbox(null)} className="boris-f2-f1f0aa143bfa">
                <Button onClick={(e) => { e.stopPropagation(); setSbLightbox(null); }} className="boris-f2-f4a409c31038">✕ Закрыть</Button>
                <div className="boris-f2-bb10a8cab478">
                  <Button onClick={(e) => { e.stopPropagation(); setSbZoom(z => Math.max(1, z - 0.25)); }} className="boris-f2-63d3d950476f">−</Button>
                  <Button onClick={(e) => { e.stopPropagation(); setSbZoom(1); }} className="boris-f2-428a1c183a05">{Math.round(sbZoom*100)}%</Button>
                  <Button onClick={(e) => { e.stopPropagation(); setSbZoom(z => Math.min(4, z + 0.25)); }} className="boris-f2-63d3d950476f">+</Button>
                </div>
                <img src={sbLightbox} alt="Просмотр" onClick={e => e.stopPropagation()} onWheel={(e) => { e.stopPropagation(); setSbZoom(z => Math.min(4, Math.max(1, z - e.deltaY * 0.002))); }} style={{maxWidth:"92vw", maxHeight:"92vh", objectFit:"contain", borderRadius:"10px", boxShadow:"0 20px 60px rgba(0,0,0,0.5)", transform:`scale(${sbZoom})`, transition:"transform 0.15s", cursor: sbZoom > 1 ? "zoom-out" : "zoom-in"}} />
              </div>
            )}
          </div>
          );
        })()}

        {activeTab === "chatbots" && (
          <div className="boris-f2-779cd717b857">
            <h3 className="boris-f2-92a3f3c23f8e">Чат-боты ИИ</h3>
            <p className="boris-f2-de52905d633b">Скоро: чат-боты для соцсетей и мессенджеров на базе ИИ.</p>
          </div>
        )}

        {activeTab === "company" && (
          <BorisCard className="b-panel b-card-eq boris-f2-cad980f4b707 boris-g1-card">
            <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-3ab1bb33aea8 boris-g1-icon">🏢</span>Информация о компании</h3>
            <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Зачем это заполнять</summary><p className="boris-f2-183644454912">Борис берёт отсюда факты для текстов объявлений и ответов клиентам. Чем полнее заполнено — тем точнее результат и меньше выдумок.</p></details>
            <div className="boris-f2-5d29161d2e34">
              <div>
                <div className="boris-f2-3217374ec54a">🧠 Научите Бориса про ваш бизнес</div>
                <div className="boris-f2-a16cc290ede7">Доставка, гарантии, товары — Борис будет точнее отвечать вашим покупателям. Можно текстом или файлом.</div>
              </div>
              <Button onClick={() => kbOpenModal(currentAccount)} className="boris-f2-caaed2005b62">Обучить Бориса</Button>
            </div>
            <div className="boris-f2-55c88de1e221">
              <div>
                <label className="boris-f2-f96c1ba7f13c">Сайт</label>
                <input value={companyForm.website} onChange={e => setCompanyForm((p: any) => ({...p, website: e.target.value}))} placeholder="https://..." style={inputStyle} />
              </div>
              <div>
                <label className="boris-f2-f96c1ba7f13c">Сфера</label>
                <input value={companyForm.niche} onChange={e => setCompanyForm((p: any) => ({...p, niche: e.target.value}))} placeholder="Например: шкафы-купе на заказ" style={inputStyle} />
              </div>
              <div>
                <label className="boris-f2-f96c1ba7f13c">Описание</label>
                <textarea value={companyForm.description} onChange={e => setCompanyForm((p: any) => ({...p, description: e.target.value}))} rows={4} style={{...inputStyle, resize:"vertical" as any}} />
                <div className="boris-f2-2fd7789b397a">
                  <Button onClick={()=>pcCheck("descr","manager",companyForm.description)} className="boris-f2-33bcb75c55f2">🧠 Проверить промт Борисом</Button>
                  {pcField==="descr" && pcLoading && <div className="boris-f2-d4f2034c960f">Борис читает…</div>}
                  {pcField==="descr" && pcData && !pcLoading && (
                    <div className="boris-f2-54ac54c00c71">
                      <div className="boris-f2-316f53899bf6">{pcData.verdict}</div>
                      {(pcData.issues||[]).map((it:any,i:number)=>(
                        <div key={i} className="boris-f2-206d5b50e31f">• <b>{it.problem}</b> — {it.fix}</div>
                      ))}
                      {pcData.improved && (
                        <Button onClick={()=>{ setCompanyForm((p:any)=>({...p, description: pcData.improved})); setPcData(null); }} className="boris-f2-a79b259c99b7">✍️ Заменить на улучшенный</Button>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div>
                <label className="boris-f2-f96c1ba7f13c">Преимущества</label>
                <textarea value={companyForm.advantages} onChange={e => setCompanyForm((p: any) => ({...p, advantages: e.target.value}))} rows={3} style={{...inputStyle, resize:"vertical" as any}} />
                <div className="boris-f2-2fd7789b397a">
                  <Button onClick={()=>pcCheck("adv","manager",companyForm.advantages)} className="boris-f2-33bcb75c55f2">🧠 Проверить промт Борисом</Button>
                  {pcField==="adv" && pcLoading && <div className="boris-f2-d4f2034c960f">Борис читает…</div>}
                  {pcField==="adv" && pcData && !pcLoading && (
                    <div className="boris-f2-54ac54c00c71">
                      <div className="boris-f2-316f53899bf6">{pcData.verdict}</div>
                      {(pcData.issues||[]).map((it:any,i:number)=>(
                        <div key={i} className="boris-f2-206d5b50e31f">• <b>{it.problem}</b> — {it.fix}</div>
                      ))}
                      {pcData.improved && (
                        <Button onClick={()=>{ setCompanyForm((p:any)=>({...p, advantages: pcData.improved})); setPcData(null); }} className="boris-f2-a79b259c99b7">✍️ Заменить на улучшенный</Button>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div className="boris-f2-c5ead3da39ec">
                <div className="boris-f2-7424a3f3fa00">
                  <label className="boris-f2-f96c1ba7f13c">Тон общения</label>
                  <input value={companyForm.tone} onChange={e => setCompanyForm((p: any) => ({...p, tone: e.target.value}))} placeholder="Например: дружелюбный, деловой" style={inputStyle} />
                </div>
                <Button className="boris-btn-hover" onClick={saveCompanyForm} disabled={companySaving} style={{background: companySaving ? "var(--boris-border)" : "var(--boris-blue)", color:"var(--boris-bg)", border:"none", borderRadius:"10px", padding:"0 28px", fontWeight:"bold", cursor: companySaving ? "default" : "pointer", whiteSpace:"nowrap", height:"46px", marginBottom:"1px"}}>
                  {companySaving ? "⏳ Сохраняю..." : "💾 Сохранить"}
                </Button>
                {companySavedMsg && <div className="boris-f2-ba3e6094ad37">{companySavedMsg}</div>}
                {/* Файл сразу попадает в знания — отдельного «обучения» не требуется */}
                <label style={{alignSelf:"center", color: kbFileBusy ? "var(--boris-text-3)" : "var(--boris-blue)", fontSize:"15px", fontWeight:600, cursor: kbFileBusy ? "default" : "pointer"}}>
                  {kbFileBusy ? "Читаю файл…" : "📎 Загрузить файл (прайс, описание услуг)"}
                  <input type="file" disabled={kbFileBusy}
                    accept=".pdf,.docx,.xlsx,.xlsm,.txt,.md,.csv"
                    onChange={async (e) => {
                      const f = e.target.files && e.target.files[0];
                      if (!f) return;
                      setKbFileBusy(true); setKbFileMsg("");
                      try {
                        const fd = new FormData();
                        fd.append("file", f);
                        const r = await apiFetch(`/api/memory/upload_file?account_id=${encodeURIComponent(currentAccount)}`, { method: "POST", body: fd });
                        const d = await r.json();
                        const _kbOk = !!(d && d.status === "ok");
                        setKbFileMsg(_kbOk ? ("✅ " + d.message) : ("⚠️ " + ((d && d.message) || "Не удалось прочитать файл")));
                        if (_kbOk) { try { window.dispatchEvent(new Event("boris:data-changed")); } catch {} }
                      } catch { setKbFileMsg("⚠️ Не удалось загрузить файл"); }
                      setKbFileBusy(false);
                      e.target.value = "";
                    }}  className="boris-f2-c8be1ccba60b"/>
                </label>
                {kbFileMsg && <div style={{color: kbFileMsg.startsWith("✅") ? "#12B76A" : "#B54708", fontSize:"14px", alignSelf:"center"}}>{kbFileMsg}</div>}
              </div>
            </div>
          </BorisCard>
        )}

        {activeTab === "settings" && (
          <BorisCard className="b-panel boris-g1-card">
            <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-042ef8607ebd boris-g1-icon">🛡</span>Автопилот и безопасность</h3>
            <div className="b-meta boris-f2-a3a5568692be">Борис сам управляет ставками и продвижением по вашим правилам — вы задаёте рамки, он работает круглосуточно. Здесь же лимиты и защита от лишних трат.</div>
            <details className="boris-f2-489e16440bc3"><summary className="boris-f2-c0e3ed53fc0c">Подробнее о подтверждениях</summary><p className="boris-f2-183644454912">Управляйте тем, когда Борису можно менять объявления на Авито без вашего подтверждения. По умолчанию — всегда спрашивать.</p></details>

            <div className="boris-f2-9e1236020ccc">
              <BorisCard className="b-card b-stat boris-f2-8319cc73dc84">
                <span className="b-stat-ico boris-f2-042ef8607ebd boris-g1-card">🛡</span>
                <div>
                  <div className="b-meta boris-f2-3320a6b66c6f">Режим автопилота</div>
                  <div className="boris-f2-cbb8ee99b11f">{autopilotMode==="goal_auto" ? "Автопилот по цели" : autopilotMode==="always_auto" ? "Работает сам" : autopilotMode==="dates" ? "Сам по датам" : "Спрашивает каждый раз"}</div>
                </div>
              </BorisCard>
              <BorisCard className="b-card b-stat boris-f2-50f4e355286a">
                <span className="b-stat-ico boris-f2-79a4d50add5b boris-g1-card">📋</span>
                <div>
                  <div className="b-meta boris-f2-870773467861">Записей в журнале</div>
                  <BorisCard className="b-stat-num boris-g1-card"><CountUp to={auditLog.length} delay={110} /></BorisCard>
                </div>
              </BorisCard>
              <BorisCard className="b-card b-stat boris-f2-0bd0caea1f4c">
                <span className="b-stat-ico boris-g1-card boris-g1-icon" style={{background: republishCandidates.length > 0 ? "linear-gradient(135deg,#FDB022,#F79009)" : "linear-gradient(135deg,var(--boris-text-3),var(--boris-text-2))"}}>🔄</span>
                <div>
                  <div className="b-meta boris-f2-870773467861">Кандидатов на переустановку</div>
                  <BorisCard className="b-stat-num boris-g1-card"><CountUp to={republishCandidates.length} delay={220} /></BorisCard>
                </div>
              </BorisCard>
            </div>

            <div className="boris-f2-7dde5e56b3f3">
              <label className="b-label boris-f2-51142c356773">⚙️ Режим работы Бориса</label>
              <div className="boris-f2-2e1ca338d758">
                <label className="b-card b-fcard" style={{display:"flex", flexDirection:"row", alignItems:"center", textAlign:"left", gap:"16px", cursor:"pointer", padding:"20px 22px", background: autopilotMode==="always_ask" ? "var(--boris-blue-soft)" : "var(--boris-surface)", borderColor: autopilotMode==="always_ask" ? "var(--boris-blue)" : "var(--boris-border)", animationDelay:"0.05s"}}>
                  <input type="radio" checked={autopilotMode==="always_ask"} onChange={() => setAutopilotMode("always_ask")}  className="boris-f2-206845fbafed"/>
                  <span className="b-stat-ico boris-f2-9b8b21123da7 boris-g1-card">🔒</span>
                  <div className="boris-f2-367da999e5a7">
                    <div style={{fontWeight:700, fontSize:"16px", color: autopilotMode==="always_ask" ? "var(--boris-blue)" : "var(--boris-text)", marginBottom:"3px"}}>Спрашивать каждый раз (рекомендуется)</div>
                    <div className="b-meta boris-f2-e733f75bdd61">Борис показывает что хочет изменить и ждёт вашего «да»</div>
                  </div>
                </label>
                <label className="b-card b-fcard" style={{display:"flex", flexDirection:"row", alignItems:"center", textAlign:"left", gap:"16px", cursor:"pointer", padding:"20px 22px", background: autopilotMode==="always_auto" ? "var(--boris-blue-soft)" : "var(--boris-surface)", borderColor: autopilotMode==="always_auto" ? "var(--boris-blue)" : "var(--boris-border)", animationDelay:"0.10s"}}>
                  <input type="radio" checked={autopilotMode==="always_auto"} onChange={() => setAutopilotMode("always_auto")}  className="boris-f2-206845fbafed"/>
                  <span className="b-stat-ico boris-f2-d168238a51aa boris-g1-card">⚡</span>
                  <div className="boris-f2-367da999e5a7">
                    <div style={{fontWeight:700, fontSize:"16px", color: autopilotMode==="always_auto" ? "var(--boris-blue)" : "var(--boris-text)", marginBottom:"3px"}}>Работать сам всегда</div>
                    <div className="b-meta boris-f2-e733f75bdd61">Борис действует сам, без подтверждений. Включает также почасовую корректировку ставок в разделе «Советник».</div>
                  </div>
                </label>
                <label className="b-card b-fcard" style={{display:"flex", flexDirection:"row", alignItems:"center", textAlign:"left", gap:"16px", cursor:"pointer", padding:"20px 22px", background: autopilotMode==="goal_auto" ? "var(--boris-blue-soft)" : "var(--boris-surface)", borderColor: autopilotMode==="goal_auto" ? "var(--boris-blue)" : "var(--boris-border)", animationDelay:"0.15s"}}>
                  <input type="radio" checked={autopilotMode==="goal_auto"} onChange={() => setAutopilotMode("goal_auto")} className="boris-f2-206845fbafed"/>
                  <span className="b-stat-ico boris-g1-card boris-g1-icon">🎯</span>
                  <div className="boris-f2-367da999e5a7">
                    <div style={{fontWeight:700, fontSize:"16px", color: autopilotMode==="goal_auto" ? "var(--boris-blue)" : "var(--boris-text)", marginBottom:"3px"}}>Автопилот по цели</div>
                    <div className="b-meta boris-f2-e733f75bdd61">Борис самостоятельно оптимизирует рекламу к вашему индивидуальной цели. Действия выполняются только в разрешённых рамках и сохраняются в журнале.</div>
                  </div>
                </label>
                <label className="b-card b-fcard" style={{display:"flex", flexDirection:"row", alignItems:"center", textAlign:"left", gap:"16px", cursor:"pointer", padding:"20px 22px", background: autopilotMode==="dates" ? "var(--boris-blue-soft)" : "var(--boris-surface)", borderColor: autopilotMode==="dates" ? "var(--boris-blue)" : "var(--boris-border)", animationDelay:"0.20s"}}>
                  <input type="radio" checked={autopilotMode==="dates"} onChange={() => setAutopilotMode("dates")}  className="boris-f2-206845fbafed"/>
                  <span className="b-stat-ico boris-f2-79a4d50add5b boris-g1-card">🏖</span>
                  <div className="boris-f2-367da999e5a7">
                    <div style={{fontWeight:700, fontSize:"16px", color: autopilotMode==="dates" ? "var(--boris-blue)" : "var(--boris-text)", marginBottom:"3px"}}>Работать сам только в отпуске</div>
                    <div className="b-meta boris-f2-e733f75bdd61">Борис действует сам только в выбранные даты</div>
                  </div>
                </label>
              </div>
            </div>

            {autopilotMode==="dates" && (
              <div className="boris-f2-59bbd8603ce8">
                <div className="boris-f2-7623f0554520"><label className="boris-f2-f96c1ba7f13c">С</label><input type="date" value={autopilotFrom} onChange={e => setAutopilotFrom(e.target.value)} style={inputStyle} /></div>
                <div className="boris-f2-7623f0554520"><label className="boris-f2-f96c1ba7f13c">По</label><input type="date" value={autopilotTo} onChange={e => setAutopilotTo(e.target.value)} style={inputStyle} /></div>
              </div>
            )}

            <Button className="boris-btn-hover boris-f2-eb831a6b609c" onClick={saveAutopilot}>Сохранить настройки</Button>

            <div className="boris-f2-1f29d3e4bfb9">
              <div className="boris-f2-bcbb844d17df">
                <h4 className="boris-f2-5a9a6dee1167"><span className="b-icon-sm boris-f2-ac305422ad3b boris-g1-icon">📋</span>Журнал действий Бориса</h4>
                <Button className="boris-btn-hover boris-f2-3b07920a9828" onClick={loadAuditLog}>Обновить</Button>
              </div>
              {auditLog.length === 0 ? (
                <p className="boris-f2-96046ead62a3">Пока нет записей. Здесь появятся все действия Бориса с объявлениями.</p>
              ) : (
                <div className="boris-f2-f67b86e0fb4c">
                  {auditLog.map((e: any, i: number) => (
                    <BorisCard key={i} className="boris-card-hover boris-f2-94d805126c48 boris-g1-card">
                      <div className="boris-f2-cb5e98f99778">
                        <span>{e.who || (e.actor === "user" ? "👤 Вы" : e.actor === "director" ? "👑 Директор" : "Борис")}</span>
                        <span className="boris-f2-8ff99612674d">{(e.ts || "").replace("T", " ")}</span>
                      </div>
                      <div className="boris-f2-012919a7cda4">{e.human ? e.human.replace(/^(👤 Вы|Борис|👑 Директор)\s*/, "") : (<><b>{e.action}</b>{e.details ? ` — ${e.details}` : ""}</>)}</div>
                    </BorisCard>
                  ))}
                </div>
              )}

                          </div>
          </BorisCard>
        )}

        {activeTab === "billing" && (
          <BorisCard className="b-panel boris-g1-card">
            <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-681bd3569745 boris-g1-icon">💳</span>Лимиты подписки</h3>
            {!billingData ? (
              <p className="boris-f2-96046ead62a3">Загрузка…</p>
            ) : (
              <>
                <BorisCard className="b-card b-stat boris-g1-card" style={{marginBottom:"20px", animationDelay:"0s", minHeight:"130px", background: billingData.tier === "none" ? "var(--boris-surface)" : "linear-gradient(135deg,#F2FBF7,var(--boris-surface))"}}>
                  <span className="b-stat-ico boris-g1-card boris-g1-icon" style={{background: billingData.tier === "none" ? "linear-gradient(135deg,var(--boris-text-3),var(--boris-text-2))" : "linear-gradient(135deg,#3DBE93,#12805C)"}}>{billingData.tier === "none" ? "🔒" : "⭐"}</span>
                  <div>
                    <div className="b-meta boris-f2-4f4237238fab">Ваш тариф</div>
                    <div className="boris-f2-ae00af9646ad">{billingData.tier_name}</div>
                    <div className="b-meta boris-f2-a3a5568692be">
                      {billingData.tier === "none" ? "Тариф не подключён" : `До обновления лимитов: ${billingData.days_left} дн.`}
                    </div>
                  </div>
                </BorisCard>

                <div className="boris-f2-ef110a887bf9">
                  {Object.entries(billingData.usage || {}).map(([unit, info]: [string, any], idx: number) => {
                    const pct = info.limit > 0 ? Math.min(100, Math.round(info.used / info.limit * 100)) : (info.used > 0 ? 100 : 0);
                    const bar = pct >= 100 ? "linear-gradient(90deg,#F97066,#F04438)" : pct >= 80 ? "linear-gradient(90deg,#FDB022,#F79009)" : "linear-gradient(90deg,#3DBE93,#12805C)";
                    const ico: any = { listings: "📋", banners: "🎨", templates: "📝", images: "🖼" };
                    const grd: any = { listings: "linear-gradient(135deg,var(--boris-blue),var(--boris-blue))", banners: "linear-gradient(135deg,#F06AA8,#D93D86)", templates: "linear-gradient(135deg,#7C5CFC,#5B3FD9)", images: "linear-gradient(135deg,#F7A440,#E8850B)" };
                    return (
                      <BorisCard key={unit} className="b-card b-stat boris-g1-card" style={{flexDirection:"column", alignItems:"stretch", justifyContent:"center", gap:"16px", animationDelay:`${0.08 + idx * 0.09}s`, minHeight:"150px", padding:"24px 26px"}}>
                        <div className="boris-f2-f62967eb81d6">
                          <span className="b-stat-ico boris-g1-card boris-g1-icon" style={{background: grd[unit] || "linear-gradient(135deg,var(--boris-text-3),var(--boris-text-2))"}}>{ico[unit] || "📦"}</span>
                          <div className="boris-f2-a07604d33451">
                            <div className="boris-f2-b55f53653e78">{info.name}</div>
                            <div className="b-meta boris-f2-549f4a948597">{info.limit > 0 ? `${info.used} из ${info.limit}` : "лимит не задан"}</div>
                          </div>
                          <div style={{fontSize:"26px", fontWeight:800, color: pct >= 100 ? "#F04438" : pct >= 80 ? "#F79009" : "#12805C"}}>{info.limit > 0 ? `${pct}%` : "—"}</div>
                        </div>
                        <div className="boris-f2-e4ab95f32875">
                          <div style={{width:`${pct}%`, height:"100%", background: bar, borderRadius:"999px", transition:"width 1s cubic-bezier(.22,1,.36,1)", transitionDelay:`${0.2 + idx * 0.09}s`}} />
                        </div>
                      </BorisCard>
                    );
                  })}
                </div>

                {billingData.tier === "none" && (
                  <BorisCard className="b-card boris-f2-8226c724ed0f">
                    <span className="b-stat-ico boris-f2-29d4aef36f63 boris-g1-card">🔔</span>
                    <div>
                      <div className="boris-f2-f9af1ab50602">Тариф не подключён</div>
                      <div className="b-meta boris-f2-8c680f25d6e7">Борис пока не может создавать объявления и баннеры. Обратитесь для подключения тарифа.</div>
                    </div>
                  </BorisCard>
                )}
              </>
            )}
          </BorisCard>
        )}

        {activeTab === "plan" && (
          <div className="boris-f2-0a95cb90e637">
            {planItems.length === 0 && (<div className="boris-f2-e0ebb78c5274"><div className="boris-f2-fa67ab694c2a"><div className="boris-f2-e59f0f96c3e5">С чего начать</div><div className="boris-f2-cb57d9bd0ff7">Задач пока нет. Борис создаст их сам, когда вы запустите рекламу.</div></div><Button className="boris-btn-hover boris-f2-faac849ef21d" onClick={() => setActiveTab("listings")}>Запустить рекламу</Button></div>)}
            {/* БЛОК: задачи в очереди по аккаунту со статусами */}
            <BorisCard className="b-panel b-card-eq account-tasks-block boris-f2-cad980f4b707 boris-g1-card">
              <div className="boris-f2-a794b88522b7">
                <h3 className="b-title boris-f2-ea8ce7c7e635"><span className="b-icon-sm boris-f2-18a679d87ff3 boris-g1-icon">⚙️</span>Что Борис делает прямо сейчас</h3>
                <span className="boris-f2-0e36be9f6646">
                  🟡 {accountTasks.filter(t => t.status === "queued").length} в очереди · 🔵 {accountTasks.filter(t => t.status === "running").length} выполняется · ✅ {accountTasks.filter(t => t.status === "done").length} готово
                </span>
              </div>
              {accountTasks.length === 0 ? (
                <p className="boris-f2-19be9ae6188a">Пока нет задач в очереди по этому аккаунту</p>
              ) : (
                <div className="boris-f2-315be772b3b9">
                  {accountTasks.map(t => {
                    const st = t.status === "queued" ? {icon:"🟡", label:"В очереди", color:"#E08A2C"}
                             : t.status === "running" ? {icon:"🔵", label:"Выполняется", color:"var(--boris-blue)"}
                             : t.status === "done" ? {icon:"✅", label:"Готово", color:"#1E8E5A"}
                             : t.status === "error" ? {icon:"❌", label:"Ошибка", color:"#D64545"}
                             : {icon:"⚪", label:humanizeBusinessText(t.status,"Состояние уточняется"), color:"var(--boris-text-3)"};
                    const typeLabels: Record<string, string> = {generate_images:"Генерация картинок", city_analysis:"Анализ конкурентов", report_back:"Отчёт Бориса", ab_test:"A/B тест", avito_categories_sync:"Синхронизация категорий", execute_plan_subtask:"Выполнение плана", enrich_descriptions:"Улучшение описаний", parse_site:"Выгрузка товаров с сайта", product_description:"Написание описания товара", product_banner:"Создание баннера", fetch_all_galleries:"Загрузка фото товаров", generate_ads:"Создание объявлений", feed_generate:"Сборка фида для Avito"};
                    const typeLabel = typeLabels[t.task_type] || humanizeBusinessText(t.task_type,"Задача BORIS");
                    return (
                      <div key={t.id} style={{display:"flex", justifyContent:"space-between", alignItems:"center", padding:"10px 14px", background:"#F9FBFF", borderRadius:"8px", borderLeft:`3px solid ${st.color}`}}>
                        <div className="boris-f2-7e30d285d288">
                          <span className="boris-f2-6c28d08961ad">{st.icon}</span>
                          <div>
                            <div className="boris-f2-29be9e0cb513">{typeLabel}</div>
                            <div className="boris-f2-b68dda3a3439">№{t.id} · {new Date(t.created_at).toLocaleString("ru-RU", {day:"2-digit", month:"2-digit", hour:"2-digit", minute:"2-digit"})}</div>
                          </div>
                        </div>
                        <span style={{fontSize:"13px", fontWeight:600, color:st.color}}>{st.label}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </BorisCard>

            <BorisCard className="b-panel b-card-eq boris-f2-cad980f4b707 boris-g1-card">
              <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-18a679d87ff3 boris-g1-icon">✅</span>Задачи и план</h3>
              <p className="b-sub">Список задач по этому аккаунту — вносите вручную или Борис фиксирует сам при выполнении оркестратором.</p>

            <div className="boris-f2-f67e6e3ba330">
              <textarea value={newPlanItemText} onChange={e => setNewPlanItemText(e.target.value)} placeholder="Новая задача..." rows={2} style={{...inputStyle, flex:1, resize:"vertical", minHeight:"44px", fontFamily:"inherit", fontSize:"15px"}} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addPlanItem(); } }} />
              <Button className="boris-btn-hover boris-f2-4cd9c549f6d6" onClick={addPlanItem}>+ Добавить</Button>
            </div>

            <div className="boris-f2-f67b86e0fb4c">
              {planItems.map((item: any) => (
                <div key={item.id} className="b-task-row boris-f2-9e4d4bf15c39">
                  <select className="b-select boris-f2-246b48efe2ef" value={item.status} onChange={e => updatePlanItemStatus(item.id, e.target.value)}>
                    <option value="planned">📋 Запланировано</option>
                    <option value="needs_launch">🚀 Требует запуска</option>
                    <option value="in_progress">⏳ В процессе</option>
                    <option value="done">✅ Сделано</option>
                    <option value="cancelled">❌ Отменено</option>
                  </select>
                  <div className="boris-f2-7623f0554520">
                    <div style={{color:"var(--boris-text)", fontSize:"15px", lineHeight:"1.6", textDecoration: item.status === "cancelled" ? "line-through" : "none", opacity: item.status === "done" ? 0.6 : 1}}>{item.text}</div>
                    <div className="boris-f2-5fb72d884786">{item.source === "boris" ? "Борис" : "👤 Вручную"}{item.due_date ? ` · до ${item.due_date.slice(0,10)}` : ""}</div>
                  </div>
                  <Button className="boris-btn-hover boris-f2-7642841aa62f" onClick={() => executePlanItem(item.id)} disabled={item.status === "running"}>
                    {item.status === "running" ? "Выполняется..." : "▶️ Сделай"}
                  </Button>
                  <Button className="boris-btn-hover boris-f2-ed9c6fefa922" onClick={() => deletePlanItem(item.id)}>×</Button>
                </div>
              ))}
              {planItems.length === 0 && <p className="boris-f2-39d6cccefd15">📭 Пока нет задач по этому аккаунту — добавьте первую вручную выше</p>}
            </div>
            </BorisCard>

            <BorisCard className="b-panel b-card-eq boris-f2-cad980f4b707 boris-g1-card">
              <h3 className="b-title boris-f2-16f2d512882a"><span className="b-icon-sm boris-f2-042ef8607ebd boris-g1-icon">📦</span>Пакетный запуск направлений</h3>
              <details className="boris-f2-a82d7062cc5e"><summary className="boris-f2-c0e3ed53fc0c">Подробнее</summary><p className="b-sub">Опишите одним текстом несколько товарных направлений — Борис разберёт их и запустит по конвейеру на каждое. Ничего не запускается без вашего подтверждения.</p></details>

              <textarea value={batchPromptText} onChange={e => setBatchPromptText(e.target.value)}
                placeholder={"Например: Кухни под ключ — 5 объявлений, 3 баннера, цены 50-300к;\nШкафы-купе — 4 объявления, 2 баннера;\nГардеробные на заказ — 6 объявлений"}
                disabled={batchParsing || !!batchPreview} rows={5}
                style={{...inputStyle, width:"100%", resize:"vertical", fontFamily:"inherit", marginBottom:"14px"}} />

              {!batchPreview ? (
                <Button className="boris-btn-hover" onClick={parseBatchPrompt} disabled={batchParsing}
                  style={{background: batchParsing ? "var(--boris-border)" : "#7C5CFC", color: batchParsing ? "var(--boris-text-2)" : "var(--boris-surface)", border:"none", borderRadius:"8px", padding:"11px 20px", fontWeight:"bold", cursor: batchParsing ? "wait" : "pointer"}}>
                  {batchParsing ? "⏳ Разбираю..." : "🔍 Разобрать"}
                </Button>
              ) : (
                <div>
                  <div className="boris-f2-090b5f597343">
                    <div className="boris-f2-e8c9118003b7">
                      Понял так: {batchPreview.total_directions} направлени{batchPreview.total_directions === 1 ? "е" : "й"}, ~{batchPreview.total_ads} объявлений, ~{batchPreview.total_banners} баннеров, ~{batchPreview.estimated_cost_rub}₽, ~{batchPreview.estimated_minutes} мин.
                    </div>
                    <div className="boris-f2-ac90f44811d6">
                      {batchPreview.directions.map((d: any, i: number) => (
                        <div key={i} className="boris-f2-518c8acb87cd">
                          {i + 1}) <b>{d.direction}</b> — {d.count} объявл., {d.banner_count} баннер{d.banner_count === 1 ? "" : d.banner_count < 5 ? "а" : "ов"}, цены {d.price_from.toLocaleString("ru-RU")}–{d.price_to.toLocaleString("ru-RU")}₽
                        </div>
                      ))}
                    </div>
                  </div>

                  {batchPreview.warning && (
                    <div className="boris-f2-40a7d4da96e2">
                      ⚠️ {batchPreview.warning}{batchConfirmedBig ? " — нажмите «Запустить всё» ещё раз для подтверждения." : ""}
                    </div>
                  )}

                  <div className="boris-f2-37bfc2fc415a">
                    <Button className="boris-btn-hover" onClick={launchBatch} disabled={batchLaunching}
                      style={{flex:1, background: batchLaunching ? "var(--boris-border)" : "#12805C", color: batchLaunching ? "var(--boris-text-2)" : "var(--boris-surface)", border:"none", borderRadius:"8px", padding:"11px", fontWeight:"bold", cursor: batchLaunching ? "wait" : "pointer"}}>
                      {batchLaunching ? "⏳ Запускаю..." : batchPreview.big_batch && !batchConfirmedBig ? "⚠️ Всё равно запустить всё" : "✅ Запустить всё"}
                    </Button>
                    <Button className="boris-btn-hover boris-f2-27ac5f27e34d" onClick={() => setBatchPreview(null)} disabled={batchLaunching}
>
                      ✏️ Поправить
                    </Button>
                    <Button className="boris-btn-hover boris-f2-985000dff604" onClick={() => { setBatchPreview(null); setBatchPromptText(""); setBatchConfirmedBig(false); }} disabled={batchLaunching}
>
                      ❌ Отмена
                    </Button>
                  </div>
                </div>
              )}
            </BorisCard>
          </div>
        )}
      </div>

      {trainData && (
        <div onClick={()=>setTrainData(null)} className="boris-f2-2cb2d4eafc86">
          <div onClick={e=>e.stopPropagation()} className="boris-f2-3f58684d5fc4">
            <div className="boris-f2-3b99a31f5644">🎓 РОП обучает ИИ-менеджера</div>
            <div className="boris-f2-3740881efa64">На основе разбора: {trainData.based_on?.calls || 0} звонков и {trainData.based_on?.chats || 0} переписок{trainData.based_on?.avg_score ? `, средний балл ${trainData.based_on.avg_score}` : ""}.</div>
            <div className="boris-f2-e411a461121f">{trainData.rules}</div>
            <div className="boris-f2-cede0e9e4685">Правила добавятся отдельным блоком в скрипт ИИ-менеджера. Ваш текст не пострадает — при повторном обучении обновится только этот блок.</div>
            <div className="boris-f2-6b00e75351fc">
              <Button onClick={()=>setTrainData(null)} className="boris-f2-5e0aabc19880">Отмена</Button>
              <Button onClick={()=>trainManager(true)} disabled={trainLoading} style={{flex:1, background:"var(--boris-blue)", color:"#fff", border:"none", borderRadius:"10px", padding:"11px 18px", fontWeight:700, cursor:"pointer", fontSize:"15px", opacity: trainLoading?0.6:1}}>{trainLoading ? "Записываю…" : "Применить к ИИ-менеджеру"}</Button>
            </div>
          </div>
        </div>
      )}
      {pwStep !== "off" && (
        <div onClick={()=>setPwStep("off")} className="boris-f2-252455fa6413">
          <div onClick={e=>e.stopPropagation()} className="boris-f2-2196d179e912">
            <div className="boris-f2-a794b88522b7">
              <div className="boris-f2-0c9626a4337f">✨ Помощь в написании промта</div>
              <Button onClick={()=>setPwStep("off")} className="boris-f2-c78a426a3893">×</Button>
            </div>
            {pwStep === "task" && (
              <div>
                <div className="boris-f2-98b4835640d4">Для какой задачи нужен промт?</div>
                <div className="boris-f2-f67b86e0fb4c">
                  {PW_TASKS.map((t:any)=>(
                    <Button key={t.id} onClick={()=>pwPickTask(t)} className="boris-btn-hover boris-f2-3d0a8aeceb8c">
                      <span className="boris-f2-ef97e5456825">{t.icon}</span>{t.name}
                    </Button>
                  ))}
                </div>
              </div>
            )}
            {pwStep === "questions" && pwTask && (
              <div>
                <div className="boris-f2-98b4835640d4">{pwTask.name} — ответьте на вопросы, Борис соберёт готовый текст</div>
                <div className="boris-f2-2e1ca338d758">
                  {pwTask.qs.map((q:string,i:number)=>(
                    <div key={i}>
                      <div className="boris-f2-ecd02af9a13c">{i+1}. {q}</div>
                      <textarea value={pwAnswers[i]||""} onChange={e=>{const a=[...pwAnswers]; a[i]=e.target.value; setPwAnswers(a);}} rows={2}
  className="boris-f2-56dcb8c406ae"/>
                    </div>
                  ))}
                </div>
                <div className="boris-f2-bf7e542503ca">
                  <Button onClick={()=>setPwStep("task")} className="boris-f2-5e0aabc19880">Назад</Button>
                  <Button onClick={pwGenerate} className="boris-f2-f811b397f9e6">Собрать промт →</Button>
                </div>
              </div>
            )}
            {pwStep === "result" && (
              <div>
                {pwLoading && <div className="boris-f2-e1a0b231f542">Борис пишет промт…</div>}
                {!pwLoading && (
                  <div>
                    <div className="boris-f2-e411a461121f">{pwResult}</div>
                    <div className="boris-f2-8b270c6435c8">
                      Скопируйте этот текст и вставьте в <b>{pwTask ? pwTask.where : ""}</b>, затем нажмите «Сохранить». После сохранения Борис изучит инструкцию и станет применять её в работе.
                    </div>
                    <div className="boris-f2-6b00e75351fc">
                      <Button onClick={()=>{navigator.clipboard.writeText(pwResult); alert("Промт скопирован — вставьте его в нужное поле и сохраните");}} className="boris-f2-f811b397f9e6">Скопировать</Button>
                      <Button onClick={()=>setPwStep("questions")} className="boris-f2-5e0aabc19880">Изменить ответы</Button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
      {borisOpen && (
        <div className="boris-f2-26eabeab1574">
          <div className="boris-f2-1944d35633a2">
            <span className="boris-f2-8baa2a1e8844">Борис — помощник</span>
            <Button className="boris-btn-hover boris-f2-a53f52a888cd" onClick={() => setBorisOpen(false)}>×</Button>
          </div>
          <div className="boris-f2-2e4ede691e85">
            {notifications.map((n: any, ni: number) => (
              <div key={ni} className="boris-f2-aae2a87345eb">
                <div className="boris-f2-1e0a8ff8c454">📊 Борис вернулся с результатом</div>
                <div className="boris-f2-7d2cd51844c2">{n.text}</div>
                <Button className="boris-btn-hover boris-f2-fe50257115dd" onClick={() => markNotificationRead(n.ts)}>Прочитано</Button>
              </div>
            ))}
            {borisHistory.length === 0 && (
              <div className="boris-f2-96046ead62a3">Привет! Я Борис 👋 Спроси меня о чём угодно: как пользоваться сервисом, куда нажать, что делать. Помогу разобраться.</div>
            )}
            {borisHistory.map((m: any, i: number) => (
              <div key={i} style={{alignSelf: m.role==="user" ? "flex-end" : "flex-start", maxWidth:"90%", background: m.role==="user" ? "var(--boris-blue)" : "var(--boris-surface)", color:"var(--boris-text)", padding:"10px 14px", borderRadius:"12px", fontSize:"23px", lineHeight:1.4, whiteSpace:"pre-wrap"}}>
                {m.content}
                {m.plan && (
                  <div className="boris-f2-00467ea76bd7">
                    {m.plan.steps.map((step: any, si: number) => (
                      <div key={si} className="boris-f2-4e980330a99a">{stepLabel(step)}</div>
                    ))}
                    {m.plan.clarifying_question && (
                      <div className="boris-f2-1f5300ea9760">❓ {m.plan.clarifying_question}</div>
                    )}
                    {!m.planResolved && (
                      <div className="boris-f2-3fed563e917c">
                        <Button className="boris-btn-hover boris-f2-3da096912514" onClick={() => acceptPlan(m.plan, i)}>🤝 Бери в работу, Борис</Button>
                        <Button className="boris-btn-hover boris-f2-376583938b1d" onClick={() => rejectPlan(i)}>✏️ Поправить</Button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {borisLoading && <div className="boris-f2-e8c76063708f">Борис печатает…</div>}
          </div>
          {/* Плашка поддержки внутри помощника */}
          <div className="boris-f2-d6697719473a">
            <Button onClick={pwStart} className="boris-f2-a7b10d9e99c1">✨ Помоги написать промт</Button>
            {!supportOpen ? (
              <Button onClick={()=>{setSupportOpen(true); setSupSent(false);}} className="boris-f2-daee02b75942">📮 Обратиться в поддержку</Button>
            ) : supSent ? (
              <div className="boris-f2-f080b2e28c41">
                ✅ Обращение{supTicket ? " №" + supTicket : ""} принято. Ответим в рабочие часы — ежедневно с 9:00 до 18:00 по Москве. Ответ придёт сюда, в кабинет, и в Telegram.
                <Button onClick={()=>{setSupportOpen(false);}} className="boris-f2-12498cd552d3">закрыть</Button>
              </div>
            ) : (
              <div>
                <div className="boris-f2-5a9738ff9ea1">
                  <span className="boris-f2-44b38035c82e">📮 Поддержка</span>
                  <Button onClick={()=>setSupportOpen(false)} className="boris-f2-dbe51c4af708">✕</Button>
                </div>
                <div className="boris-f2-70ce45459ed9">
                  Работаем ежедневно с 9:00 до 18:00 по Москве. Написать можно в любое время — ответим в рабочие часы.
                </div>
                <input placeholder="Ваше имя" value={supName} onChange={e=>setSupName(e.target.value)}  className="boris-f2-49681e89671b"/>
                <input placeholder="Телефон / Telegram / почта" value={supContact} onChange={e=>setSupContact(e.target.value)}  className="boris-f2-49681e89671b"/>
                <textarea placeholder="Ваше сообщение" value={supText} onChange={e=>setSupText(e.target.value)} rows={2}  className="boris-f2-2ce9e632343d"/>
                <Button onClick={sendSupport} disabled={supSending} className="boris-f2-8b6d8acf0d91">{supSending?"Отправка...":"Отправить"}</Button>
                <Button onClick={loadMyTickets} className="boris-f2-479c23253106">Мои обращения</Button>
                {supMyOpen && (
                  <div className="boris-f2-ef52733787bf">
                    {supMy.length === 0 ? (
                      <div className="boris-f2-6774eea0e0db">Обращений пока не было.</div>
                    ) : supMy.map((t:any) => (
                      <div key={t["номер"]} className="boris-f2-50ba035cf7e5">
                        <div className="boris-f2-6c2427e9980a">
                          <b>№{t["номер"]}</b> · <span style={{color: t["статус"]==="решено" ? "#12805C" : t["статус"]==="в работе" ? "#E8850B" : "var(--boris-text-2)"}}>{t["статус"]}</span>
                        </div>
                        <div className="boris-f2-e41894b51ae0">{t["текст"]}</div>
                        {(t["ответы"]||[]).map((rp:any, i:number) => (
                          <div key={i} style={{fontSize:"13px", marginTop:"4px", padding:"6px 8px", background: rp["кто"]==="поддержка" ? "#F0F4FF" : "#F8FAFF", borderRadius:"8px", color:"var(--boris-text-2)"}}>
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
          <div className="boris-f2-1136532a4984">
            <input value={borisInput} onChange={e => setBorisInput(e.target.value)} onKeyDown={e => { if(e.key==="Enter") askBoris(); }} placeholder="Спроси Бориса…"
  className="boris-f2-91f1c0e0f357"/>
            <Button className="boris-btn-hover boris-f2-45117500ae13" onClick={askBoris}>➤</Button>
          </div>
        </div>
      )}

      {!borisOpen && borisCurrentQ && borisBubbleOpen && (
        <div className="b-boris-bubble boris-f2-2865171f04ac">
          <div className="boris-f2-5a9738ff9ea1">
            <span className="boris-f2-960cbf4db453">БОРИС СПРАШИВАЕТ</span>
            <Button onClick={() => setBorisBubbleOff(true)} title="Больше не показывать подсказки" className="boris-f2-f75e8565f71f">×</Button>
          </div>
          <div className="boris-f2-dee1db4978c5">{borisCurrentQ.text}</div>
          <div className="boris-f2-b9bbe540d342">
            {borisCurrentQ.actions.map((a: any) => (
              <Button key={a.label} className="boris-btn-hover boris-f2-946405ad44e9" onClick={() => { setActiveTab(a.go); setBorisAsked((prev: any) => ({...prev, [borisCurrentQ.id]: true})); }}
>{a.label}</Button>
            ))}
            <Button className="boris-btn-hover boris-f2-42fc0fd1ec23" onClick={() => setBorisAsked((prev: any) => ({...prev, [borisCurrentQ.id]: true}))}
>Позже</Button>
          </div>
          <div  className="boris-f2-4509816a1893"/>
        </div>
      )}

      <Button className={["b-boris-fab" + (borisMood === "alert" ? " b-boris-alert" : ""), "boris-f2-1caaeebcec62"].filter(Boolean).join(" ")} onClick={() => { if (borisCurrentQ && !borisBubbleOpen) { setBorisBubbleOpen(true); } else { setBorisBubbleOpen(false); setBorisOpen(!borisOpen); } }}>
        {borisOpen ? <span className="boris-f2-28fb59d9485a">×</span> : <Mascot size={44} interactive={true} mood={borisMood} />}
        {!borisOpen && notifications.length > 0 && (
          <span className="boris-f2-f7b5f3a56f09">{notifications.length}</span>
        )}
      </Button>

      {showPipelineForm && (
        <div
          onClick={() => { if (!directionStarting) setShowPipelineForm(false); }} className="boris-f2-39e28d65ac7c">
          <div onClick={(e:any) => e.stopPropagation()} className="boris-f2-3b6ed6ab4dc6">
            <h3 className="boris-f2-5e907f3edc62">🚀 Конвейер направления</h3>
            <p className="boris-f2-e7c5bd14a445">Полный цикл по одному товарному направлению: тексты → категория → баннеры → проверка → публикация.</p>

            <label className="boris-f2-200d23dd578d">Направление</label>
            <input value={directionForm.direction} onChange={e => setDirectionForm({...directionForm, direction: e.target.value})}
              placeholder="Например, Гардеробные на заказ" disabled={directionStarting}
  className="boris-f2-89692fce466b"/>

            <div className="boris-f2-6894e065411c">
              <div>
                <label className="boris-f2-200d23dd578d">Объявлений</label>
                <input type="number" min={1} value={Number(directionForm.count) === 0 ? "" : directionForm.count} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, count: Number(e.target.value)})}
  className="boris-f2-2f19a0435dd7"/>
              </div>
              <div>
                <label className="boris-f2-200d23dd578d">Баннеров</label>
                <input type="number" min={1} value={Number(directionForm.bannerCount) === 0 ? "" : directionForm.bannerCount} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, bannerCount: Number(e.target.value)})}
  className="boris-f2-2f19a0435dd7"/>
              </div>
              <div>
                <label className="boris-f2-200d23dd578d">Цена от</label>
                <input type="number" min={0} value={Number(directionForm.priceFrom) === 0 ? "" : directionForm.priceFrom} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, priceFrom: Number(e.target.value)})}
  className="boris-f2-2f19a0435dd7"/>
              </div>
              <div>
                <label className="boris-f2-200d23dd578d">Цена до</label>
                <input type="number" min={0} value={Number(directionForm.priceTo) === 0 ? "" : directionForm.priceTo} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, priceTo: Number(e.target.value)})}
  className="boris-f2-2f19a0435dd7"/>
              </div>
            </div>

            <div className="boris-f2-b9341501b114">Уникализировать:</div>
            <div className="boris-f2-787f43e1c325">
              <label className="boris-f2-dede1240812e">
                <input type="checkbox" checked={directionForm.uniquifyTitles} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, uniquifyTitles: e.target.checked})} /> заголовки
              </label>
              <label className="boris-f2-dede1240812e">
                <input type="checkbox" checked={directionForm.uniquifyDescriptions} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, uniquifyDescriptions: e.target.checked})} /> описания
              </label>
            </div>

            <div className="boris-f2-b9341501b114">После готовности:</div>
            <div className="boris-f2-bcaed909275f">
              <label className="boris-f2-dede1240812e">
                <input type="checkbox" checked={directionForm.autoCheck} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, autoCheck: e.target.checked})} /> проверить партию
              </label>
              <label className="boris-f2-dede1240812e">
                <input type="checkbox" checked={directionForm.autoPublish} disabled={directionStarting}
                  onChange={e => setDirectionForm({...directionForm, autoPublish: e.target.checked})} /> опубликовать автоматически
              </label>
            </div>

            <div className="boris-f2-37bfc2fc415a">
              <Button className="boris-btn-hover" onClick={runDirectionPipeline} disabled={directionStarting}
                style={{flex:1, background: directionStarting ? "var(--boris-border)" : "#12805C", color: directionStarting ? "var(--boris-text-2)" : "var(--boris-surface)", border:"none", borderRadius:"8px", padding:"12px", fontWeight:"bold", cursor: directionStarting ? "wait" : "pointer"}}>
                {directionStarting ? "⏳ Запускаю..." : "🚀 Запустить конвейер"}
              </Button>
              <Button className="boris-btn-hover" onClick={() => setShowPipelineForm(false)} disabled={directionStarting}
                style={{background:"var(--boris-surface)", color:"var(--boris-text-2)", border:"1px solid var(--boris-border)", borderRadius:"8px", padding:"12px 20px", cursor: directionStarting ? "default" : "pointer"}}>
                Закрыть
              </Button>
            </div>
          </div>
        </div>
      )}

      {editingItemId && (
        <div onClick={() => setEditingItemId(null)} className="boris-f2-39e28d65ac7c">
          <div onClick={(e:any) => e.stopPropagation()} className="boris-f2-c16516408c36">
            <h3 className="boris-f2-ab7a0549a1dc">✏️ Редактирование объявления</h3>
            <label className="boris-f2-200d23dd578d">Заголовок</label>
            <input value={editItemForm.title} onChange={e => setEditItemForm({...editItemForm, title: e.target.value})}
  className="boris-f2-1486e5b8c2ad"/>
            <label className="boris-f2-200d23dd578d">Цена</label>
            <input type="number" value={Number(editItemForm.price) === 0 ? "" : editItemForm.price} onChange={e => setEditItemForm({...editItemForm, price: Number(e.target.value)})}
  className="boris-f2-27cf221f533c"/>
            <label className="boris-f2-200d23dd578d">Описание</label>
            <textarea value={editItemForm.description} onChange={e => setEditItemForm({...editItemForm, description: e.target.value})} rows={10}
  className="boris-f2-a5728e69f7ee"/>
            <div className="boris-f2-37bfc2fc415a">
              <Button className="boris-btn-hover" onClick={saveEditItem} disabled={editItemSaving} style={{flex:1, background: editItemSaving ? "var(--boris-border)" : "var(--boris-blue)", color:"var(--boris-bg)", border:"none", borderRadius:"10px", padding:"12px", fontWeight:"bold", cursor: editItemSaving ? "wait" : "pointer"}}>
                {editItemSaving ? "Сохраняю..." : "✅ Сохранить"}
              </Button>
              <Button className="boris-btn-hover boris-f2-cc812ecc9f36" onClick={() => setEditingItemId(null)}>Отмена</Button>
            </div>
          </div>
        </div>
      )}

      {aiPreview && (
        <div onClick={() => setAiPreview(null)} className="boris-f2-39e28d65ac7c">
          <div onClick={(e:any) => e.stopPropagation()} className="boris-f2-20f9502cd133">
            <h3 className="boris-f2-ab7a0549a1dc">Результат генерации ИИ</h3>
            {aiPreview.images[0] && <img src={aiPreview.images[0]}  className="boris-f2-a4880151119c"/>}
            <label className="boris-f2-200d23dd578d">Заголовок</label>
            <textarea value={aiPreview.title} onChange={e => updateAiPreview("title", e.target.value)} rows={2}
  className="boris-f2-c197b41fe481"/>
            <div className="boris-f2-5f0e18046928">
              <label className="boris-f2-96046ead62a3">Описание</label>
              <Button className="boris-btn-hover boris-f2-6420ef49d699" onClick={applyBoldPreview}>Ж</Button>
            </div>
            <textarea id="ai-preview-desc" value={aiPreview.description} onChange={e => updateAiPreview("description", e.target.value)} rows={10}
  className="boris-f2-7ff4f8f41c1b"/>
            <div className="boris-f2-5135dd715e5e">Предпросмотр:</div>
            <div dangerouslySetInnerHTML={{__html: aiPreview.description}}  className="boris-f2-487e721346d4"/>
            <div className="boris-f2-d4268e91708c">
              <Button className="boris-btn-hover boris-f2-b68f130f1f82" onClick={applyAiPreview}>✅ Применить к карточке</Button>
              <Button className="boris-btn-hover boris-f2-cc812ecc9f36" onClick={() => setAiPreview(null)}>Отмена</Button>
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
        message="Борис будет САМ менять ставки каждый час в рамках предохранителей (потолок ставки, шаг не более 10%, суточный лимит бюджета). Это реальные траты на вашем аккаунте."
        confirmText="Включить" cancelText="Отмена"
        onConfirm={doToggleAutopilot} onCancel={() => setAutopilotModalOpen(false)} />
      <ConfirmModal open={payModalAccount !== null} kind="confirm" title="💳 Отметить оплату клиента"
        confirmText="Сохранить" cancelText="Отмена"
        onConfirm={savePayment} onCancel={() => setPayModalAccount(null)}>
        <div className="boris-f2-2e1ca338d758">
          <div>
            <div className="boris-f2-206d5b50e31f">Сумма оплаты, ₽</div>
            <input type="number" value={Number(payAmount) === 0 ? "" : payAmount} onChange={e => setPayAmount(e.target.value)} placeholder="7000"  className="boris-f2-955f7989a722"/>
          </div>
          <div>
            <div className="boris-f2-206d5b50e31f">Период, дней</div>
            <input type="number" value={Number(payPeriod) === 0 ? "" : payPeriod} onChange={e => setPayPeriod(e.target.value)} placeholder="30"  className="boris-f2-955f7989a722"/>
          </div>
          <div className="boris-f2-b68dda3a3439">Напоминания: 🟡 за 10 дней · 🔴 за 4 дня до конца.</div>
        </div>
      </ConfirmModal>
            {borisModal.open && (
                          <div onClick={closeBorisModal} className="boris-f2-2783e11c6d6e">
                <div onClick={e => e.stopPropagation()} className="boris-f2-ac712986e09f">
                  <div className="boris-f2-1b6d66a19529">{borisModal.title}</div>
                  <div className="boris-f2-4976c09e2ce0">{borisModal.message}</div>
                  <div className="boris-f2-6060124ddbec">
                    {borisModal.onConfirm && (
                      <Button className="boris-btn-hover boris-f2-3c6c7d7175f9" onClick={closeBorisModal}>Отмена</Button>
                    )}
                    <Button className="boris-btn-hover boris-f2-b808cb2238b2" onClick={() => { const cb = borisModal.onConfirm; closeBorisModal(); if (cb) cb(); }}>{borisModal.confirmText}</Button>
                  </div>
                </div>
              </div>
            )}

      {kbModalOpen && (
        <div onClick={() => setKbModalOpen(false)} className="boris-f2-75d61e485a9a">
          <div onClick={e => e.stopPropagation()} className="boris-f2-6358954eb67e">
            <div className="boris-f2-7488648a373d">
              <div className="boris-f2-29c425886f33">🧠</div>
              <div className="boris-f2-7623f0554520">
                <div className="boris-f2-84648c12f056">Обучить Бориса</div>
                <div className="boris-f2-e41894b51ae0">{kbModalAccount==="global" ? "Общие знания — для всех клиентов" : "Знания об этом клиенте"}</div>
              </div>
              <Button onClick={() => setKbModalOpen(false)} className="boris-f2-2a421a9cad50">✕</Button>
            </div>
            <div className="boris-f2-f75e26e1fb36">
              <div className="boris-f2-afd5bb1e8a56">
                <label className="boris-f2-aa3b68a6a257">Текст знания</label>
                <span className="boris-f2-b68dda3a3439">{kbText.length} символов</span>
              </div>
              <textarea value={kbText} onChange={e => setKbText(e.target.value)} placeholder="Например: доставка по вашему городу за 1 день. Или вставь текст из документа — Борис запомнит."  className="boris-f2-2fd54ef8b520"/>
              <label
                onDragOver={e => { e.preventDefault(); setKbDragOver(true); }}
                onDragLeave={() => setKbDragOver(false)}
                onDrop={e => { e.preventDefault(); setKbDragOver(false); Array.from(e.dataTransfer.files).forEach(f => kbUploadFile(f as File)); }}
                style={{marginTop:"18px", display:"block", border:`2px dashed ${kbDragOver ? "var(--boris-blue)" : "#D3D9E4"}`, borderRadius:"14px", padding:"26px 20px", textAlign:"center", background: kbDragOver ? "#EAF3FF" : "#F9FAFC", cursor:"pointer", transition:"all 0.2s"}}>
                <input type="file" multiple accept=".pdf,.docx,.txt,.md,.csv" onChange={e => { Array.from(e.target.files||[]).forEach(f => kbUploadFile(f as File)); e.target.value=""; }}  className="boris-f2-c8be1ccba60b"/>
                <div className="boris-f2-2778029a5819">📎</div>
                <div className="boris-f2-290d6d7e0a6d">Перетащи файл или нажми</div>
                <div className="boris-f2-ff909e414344">PDF, Word, txt — Борис прочитает текст</div>
              </label>
              {kbFiles.some((f:any)=>f.facts && f.facts.length>0) && (
                <div className="boris-f2-8a359a76ebec">
                  {kbFiles.filter((f:any)=>f.facts && f.facts.length).map((f:any)=>(
                    <div key={"fr-"+f.id} className="boris-f2-9374e8421025">
                      <div className="boris-f2-93e24f3d4ded">
                        Из файла «{f.name}» я понял {f.facts.length} фрагментов — отметьте, что запомнить
                      </div>
                      <div className="boris-f2-79f8a56a053a">
                        {f.facts.map((fr:any)=>(
                          <label key={f.id+"-"+fr.i} className="boris-f2-cf4ca4644061">
                            <input type="checkbox" checked={(f.keep||[]).includes(fr.i)} onChange={()=>kbToggleFact(f.id, fr.i)}  className="boris-f2-b7ecdfaf5aa3"/>
                            <span className="boris-f2-7623f0554520">
                              <span className="boris-f2-6f529e29f14f">{fr.name}</span>
                              <span className="boris-f2-af695054edbb">{String(fr.value||"").slice(0,180)}</span>
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {kbFiles.length > 0 && (
                <div className="boris-f2-34801765ea00">
                  {kbFiles.map((f: any) => (
                    <div key={f.id} className="boris-f2-6ec6aac3b1f5">
                      <span className="boris-f2-9b49c245f092">{f.status==="done" ? "✅" : f.status==="error" ? "❌" : "⏳"}</span>
                      <div className="boris-f2-a07604d33451">
                        <div className="boris-f2-9233632f9f8b">{f.name}</div>
                        <div style={{fontSize:"12px", color: f.status==="done" ? "#12805C" : f.status==="error" ? "#C4320A" : "var(--boris-text-2)", marginTop:"2px"}}>
                          {f.status==="done" ? `текст извлечён · ${f.chars||0} символов` : f.status==="error" ? "не удалось прочитать" : "читаю…"}
                        </div>
                      </div>
                      <Button onClick={() => setKbFiles((prev: any[]) => prev.filter(x => x.id!==f.id))} className="boris-f2-88ae1d3c8876">✕</Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="boris-f2-ea642f74a3c1">
              <Button onClick={() => setKbModalOpen(false)} className="boris-f2-cffb4dfbc4a5">Отмена</Button>
              <Button onClick={kbSubmit} disabled={kbSaving} className="boris-f2-ce825ad62f10">{kbSaving ? "Сохраняю…" : "Обучить Бориса"}</Button>
            </div>
          </div>
        </div>
      )}
    </div>


  );
}
