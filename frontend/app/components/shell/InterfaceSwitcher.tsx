"use client";

import css from "./shell.module.css";
import { navigateToOld, OldMode } from "../../lib/api";

/* Окно переключения интерфейсов. Показывается только по кнопке,
   постоянно не висит. Контекст сохраняется мостом навигации. */
type Props = { open: boolean; onClose: () => void; tab?: string; mode?: OldMode | null };

export default function InterfaceSwitcher({ open, onClose, tab = "listings", mode = null }: Props) {
  if (!open) return null;
  return (
    <div className={css.overlay} onClick={onClose} role="dialog" aria-modal="true">
      <div className={css.modal} onClick={(e: any) => e.stopPropagation()} data-testid="iface-switcher">
        <h3 className={css.modalTitle}>Переключение интерфейса</h3>

        <button className={css.opt + " " + css.optCur} onClick={onClose}>
          <div style={{ flex: 1 }}>
            <div className={css.optName}>Новый интерфейс</div>
            <div className={css.optDesc}>Современный вид с новыми возможностями</div>
          </div>
          <span className={css.badge}>Текущий</span>
        </button>

        <button className={css.opt} onClick={() => navigateToOld(tab, mode)} data-testid="go-old">
          <div style={{ flex: 1 }}>
            <div className={css.optName}>Старый интерфейс</div>
            <div className={css.optDesc}>Классический вид со всеми прежними функциями</div>
          </div>
        </button>

        <div className={css.note}>
          Ваши данные и настройки сохраняются. Переключайтесь в любой момент.
        </div>
      </div>
    </div>
  );
}
