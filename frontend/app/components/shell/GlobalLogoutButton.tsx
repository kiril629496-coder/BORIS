"use client";

import { logout } from "../../lib/api";
import css from "./shell.module.css";

export default function GlobalLogoutButton() {
  return (
    <button
      type="button"
      className={css.logoutBtn}
      onClick={logout}
      aria-label="Выйти"
      title="Выйти"
      data-testid="global-logout"
    >
      <svg
        className={css.logoutIcon}
        viewBox="0 0 24 24"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4" />
        <path d="M14 8l4 4-4 4" />
        <path d="M8 12h10" />
      </svg>
      <span className={css.logoutLabel}>Выйти</span>
    </button>
  );
}
