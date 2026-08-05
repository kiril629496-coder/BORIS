/**
 * BORIS AI — единое описание главного меню.
 * Переносится один в один из монолита: те же группы, порядок, ключи и адреса.
 * Менять состав только по согласованию.
 */
import type { IconName } from "./index";

export type MenuItem = { key: string; label: string; icon: IconName; href?: string };
export type MenuGroup = { group: string; items: MenuItem[] };

export const MENU: MenuGroup[] = [
  {
    group: "Работа",
    items: [
      { key: "home", label: "Главная", icon: "home", href: "/dashboard/home" },
      { key: "messages", label: "Сообщения", icon: "messages", href: "/messages" },
      { key: "reactivation", label: "Возврат клиентов", icon: "clients", href: "/reactivation" },
      { key: "listings", label: "Объявления", icon: "list" },
      { key: "plan", label: "Задачи и план", icon: "tasks" },
      { key: "settings", label: "Режим работы", icon: "shield" },
    ],
  },
  {
    group: "Рост",
    items: [
      { key: "marketing", label: "Маркетинг", icon: "briefcase" },
      { key: "sales", label: "Продажи", icon: "coins" },
    ],
  },
  {
    group: "Контент",
    items: [
      { key: "parser", label: "Выгрузка с сайта", icon: "upload" },
      { key: "webdesign", label: "Баннеры и картинки", icon: "palette" },
      { key: "social", label: "Социальные сети", icon: "ads", href: "/social" },
      { key: "slots", label: "Подключение аккаунтов", icon: "plug", href: "/messages/setup" },
      { key: "memory", label: "База знаний", icon: "memory", href: "/memory" },
      { key: "aisetup", label: "AI-сотрудники", icon: "ai", href: "/ai" },
      { key: "sitebuild", label: "Создание сайтов", icon: "globe" },
    ],
  },
  {
    group: "Система",
    items: [
      { key: "stats", label: "Статистика", icon: "chart" },
      { key: "billing", label: "Лимиты подписки", icon: "card" },
      { key: "company", label: "О компании", icon: "building" },
    ],
  },
];

/** Ключ пункта по текущему адресу — для экранов вне монолита */
export function keyByPath(path: string): string {
  const all: MenuItem[] = [];
  for (const g of MENU) for (const it of g.items) if (it.href) all.push(it);
  // длинные href первыми: /messages/setup не должен совпасть с /messages
  all.sort((a, b) => (b.href as string).length - (a.href as string).length);
  for (const it of all) if (path.startsWith(it.href as string)) return it.key;
  return "";
}
