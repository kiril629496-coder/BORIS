import { redirect } from "next/navigation";

/**
 * BORIS_HOME_COMMAND_CENTER_ENTRY
 *
 * Главный экран BORIS.
 * После входа пользователь попадает в Command Center.
 *
 * Command Center остаётся доступен и по своему исходному URL,
 * поэтому старые ссылки не ломаются.
 */
export default function DashboardHomePage() {
  redirect("/dashboard/command-center");
}
