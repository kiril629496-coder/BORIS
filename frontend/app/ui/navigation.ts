import { MENU, type MenuGroup, type MenuItem } from "./menu";

export type NavigationItem = MenuItem;
export type NavigationGroup = MenuGroup;

export const NAVIGATION: NavigationGroup[] = MENU;

export function navigationItems(): NavigationItem[] {
  return NAVIGATION.flatMap((group) => group.items);
}

export function navigationItem(key: string): NavigationItem | undefined {
  return navigationItems().find((item) => item.key === key);
}

export function navigationHref(key: string): string | undefined {
  return navigationItem(key)?.href;
}

export function navigationHasKey(key: string): boolean {
  return Boolean(navigationItem(key));
}
