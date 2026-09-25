"use client";

import { useEffect } from "react";

export default function CategoryActionBridge() {
  useEffect(() => {
    function handler(e: MouseEvent) {
      const raw = e.target as HTMLElement | null;

      if (!raw) return;

      const el = raw.closest(
        "a,button,[role='button']"
      ) as HTMLElement | null;

      if (!el) return;

      const text = (
        el.textContent || ""
      )
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();

      if (
        text === "определить категорию" ||
        text.includes("определить категорию")
      ) {
        e.preventDefault();
        e.stopPropagation();

        window.location.href =
          "/dashboard/category";
      }
    }

    document.addEventListener(
      "click",
      handler,
      true
    );

    return () => {
      document.removeEventListener(
        "click",
        handler,
        true
      );
    };
  }, []);

  return null;
}
