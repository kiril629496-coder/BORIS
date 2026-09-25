/**
 * BORIS AI — единое описание главного меню.
 * Authoritative navigation model: every production entry has a real route.
 */
import type { IconName } from "./index";

export type NavTone = "blue" | "red" | "orange" | "teal" | "cyan" | "violet" | "pink" | "green" | "slate";
export type MenuItem = { key: string; label: string; icon: IconName; href: string; tone?: NavTone };
export type MenuGroup = { group: string; items: MenuItem[] };

export const MENU: MenuGroup[] = [
  {
    group: "Работа",
    items: [
      { key: "home", label: "Главная", icon: "home", href: "/dashboard/home", tone: "blue" },
      { key: "clients-control", label: "Центр клиентов", icon: "clients", href: "/dashboard/clients-control", tone: "red" },
      { key: "journal", label: "Онлайн-задачи и разработка", icon: "file", href: "/dashboard/development", tone: "cyan" },
      { key: "factory-decisions", label: "Требуют решения", icon: "alert", href: "/dashboard/factory-decisions", tone: "red" },
      { key: "accounts", label: "Подключить аккаунты Авито", icon: "clients", href: "/dashboard/connect-avito", tone: "blue" },
      { key: "scenarios", label: "Бизнес-сценарии", icon: "star", href: "/dashboard/scenarios", tone: "teal" },
      { key: "command", label: "Спросите BORIS", icon: "ai", href: "/command", tone: "cyan" },
      { key: "messages", label: "Сообщения", icon: "messages", href: "/messages", tone: "blue" },
      { key: "listings", label: "Объявления", icon: "list", href: "/dashboard/campaign", tone: "violet" },
      { key: "stats", label: "Аналитика", icon: "chart", href: "/dashboard/analytics", tone: "cyan" },
      { key: "revenue-analytics", label: "Сквозная аналитика", icon: "target", href: "/dashboard/revenue-analytics", tone: "green" },
      { key: "phone", label: "Телефония", icon: "support", href: "/dashboard/phone", tone: "blue" },
      { key: "settings", label: "Режим BORISа", icon: "shield", href: "/dashboard/work-mode", tone: "slate" },
    ],
  },
  {
    group: "Рост",
    items: [
      { key: "marketing", label: "Маркетинг", icon: "briefcase", href: "/dashboard/marketing", tone: "pink" },
      { key: "director", label: "ИИ-директолог", icon: "target", href: "/dashboard/director", tone: "blue" },
      { key: "prospecting", label: "Поиск клиентов и Email", icon: "messages", href: "/dashboard/prospecting/manage", tone: "green" },
      { key: "acquisition", label: "Привлечение клиентов", icon: "target", href: "/dashboard/prospecting/acquisition", tone: "teal" },
      { key: "sales", label: "Продажи", icon: "coins", href: "/dashboard/crm", tone: "violet" },
    ],
  },
  {
    group: "Контент",
    items: [
      { key: "social", label: "Социальные сети", icon: "ads", href: "/social", tone: "teal" },
      { key: "video-factory", label: "Видео / Reels / Shorts · TEST", icon: "monitor", href: "/dashboard/video-factory", tone: "violet" },
      { key: "sitebuild", label: "Создание сайтов", icon: "globe", href: "/dashboard/sitebuild", tone: "teal" },
    ],
  },
  {
    group: "Знания",
    items: [
      { key: "memory", label: "База знаний", icon: "memory", href: "/memory", tone: "cyan" },
    ],
  },
  {
    group: "Настройки",
    items: [
      { key: "aisetup", label: "ИИ-сотрудники", icon: "ai", href: "/ai", tone: "blue" },
      { key: "ux-tester", label: "UX-тестировщик", icon: "search", href: "/dashboard/ux-tester", tone: "slate" },
      { key: "control-plane", label: "Правила BORIS", icon: "memory", href: "/dashboard/control-plane", tone: "cyan" },
      { key: "reliability", label: "System Guardian", icon: "shield", href: "/dashboard/reliability", tone: "green" },
      { key: "team", label: "Пользователи и доступы", icon: "user", href: "/team", tone: "violet" },
      { key: "billing", label: "Тарифы и финансы", icon: "card", href: "/dashboard/billing", tone: "green" },
      { key: "company", label: "Данные компании", icon: "building", href: "/dashboard/company", tone: "blue" },
    ],
  },
];

/** Ключ пункта по текущему адресу — для экранов вне монолита */
export function keyByPath(path: string): string {
  // Business-parent aliases: keep the simplified sidebar highlighted while
  // the user works in a nested tool that no longer has its own top-level item.
  if (path === "/rop" || path.startsWith("/rop/")) return "sales";
  if (path === "/reactivation" || path.startsWith("/reactivation/")) return "messages";
  if (path === "/dashboard/crm/integrations" || path.startsWith("/dashboard/crm/integrations/")) return "listings";
  const all: MenuItem[] = [];
  for (const g of MENU) for (const it of g.items) all.push(it);
  // длинные href первыми: /messages/setup не должен совпасть с /messages
  all.sort((a, b) => b.href.length - a.href.length);
  for (const it of all) {
    const routePath = it.href.split("?", 1)[0];
    if (path === routePath || path.startsWith(`${routePath}/`)) return it.key;
  }
  return "";
}
