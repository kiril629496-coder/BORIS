"use client";

/**
 * Общая логика кнопки «Назад / Закрыть» для всех оболочек BORIS.
 *
 * Своя история, а не router.back(): в кабинете много переходов через replace,
 * они не попадают в историю браузера, и стрелка уводит мимо продукта.
 *
 * Правила: соседние одинаковые маршруты не копятся, технические экраны
 * (логин, подтверждение почты, мастер первого запуска, лендинг) в стек не
 * попадают, использованная запись при возврате удаляется — иначе кнопка
 * начинает ходить между двумя страницами по кругу.
 */
import { useCallback, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

const KEY = "boris_nav_stack";
const MAX_DEPTH = 20;
const TECHNICAL = ["/login", "/verify", "/onboarding", "/manager", "/r/"];

function isTechnical(path: string): boolean {
  if (!path || path === "/") return true;
  for (let i = 0; i < TECHNICAL.length; i++) {
    if (path.startsWith(TECHNICAL[i])) return true;
  }
  return false;
}

function readStack(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(function (x) { return typeof x === "string"; }) : [];
  } catch (e) {
    return [];
  }
}

function writeStack(stack: string[]): void {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(stack));
  } catch (e) {
    // приватный режим или переполнение — навигация не должна ломаться
  }
}

/** Запомнить маршрут. Вызывается самим хуком при смене адреса. */
export function rememberRoute(path: string): void {
  if (typeof window === "undefined" || isTechnical(path)) return;
  const stack = readStack();
  if (stack.length && stack[stack.length - 1] === path) return;
  stack.push(path);
  while (stack.length > MAX_DEPTH) stack.shift();
  writeStack(stack);
}

export interface BackTarget {
  label: string;
  go: () => void;
}

/**
 * @param fallback куда идти, если истории нет: прямой вход, обновление
 *                 страницы или переход с внешнего сайта.
 * @param label    подпись: «← Назад» для страницы, «× Закрыть» для карточки.
 */
export function useBackTarget(fallback: string, label?: string): BackTarget {
  const router = useRouter();
  const pathname = usePathname() || "";

  useEffect(function () {
    rememberRoute(pathname);
  }, [pathname]);

  const go = useCallback(function () {
    const stack = readStack();
    while (stack.length && stack[stack.length - 1] === pathname) {
      stack.pop();
    }
    const prev = stack.pop();
    writeStack(stack);
    router.push(prev && prev !== pathname ? prev : fallback);
  }, [pathname, fallback, router]);

  return { label: label || "← Назад", go: go };
}
