export type LaunchFlowKey =
  | "overview"
  | "ads"
  | "messages"
  | "crm"
  | "sales"
  | "tasks"
  | "analytics"
  | "integrations";

export type LaunchFlowItem = {
  key: LaunchFlowKey;
  label: string;
  description: string;
  href: string;
};

export const BORIS_LAUNCH_FLOW: LaunchFlowItem[] = [
  {
    key: "overview",
    label: "Запуск клиента",
    description: "Состояние и готовность клиента",
    href: "/dashboard/launch-client",
  },
  {
    key: "ads",
    label: "Объявления",
    description: "Каталог, кампании, баннеры и публикация",
    href: "/dashboard/campaign",
  },
  {
    key: "messages",
    label: "Сообщения",
    description: "Все входящие обращения",
    href: "/messages",
  },
  {
    key: "crm",
    label: "CRM",
    description: "Контакты, сделки и задачи",
    href: "/dashboard/crm",
  },
  {
    key: "sales",
    label: "Продажи",
    description: "Сделки и движение по воронке",
    href: "/dashboard/crm",
  },
  {
    key: "tasks",
    label: "Задачи",
    description: "Следующие действия команды",
    href: "/dashboard/crm/workspace?tab=tasks",
  },
  {
    key: "analytics",
    label: "Аналитика",
    description: "Результат и KPI",
    href: "/dashboard/monitoring",
  },
  {
    key: "integrations",
    label: "Интеграции",
    description: "Подключения внешних систем",
    href: "/dashboard/crm/integrations",
  },
];

export function launchFlowHref(key: LaunchFlowKey): string {
  return BORIS_LAUNCH_FLOW.find((item) => item.key === key)?.href
    ?? "/dashboard/launch-client";
}
