"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

function resolveArea(pathname: string) {
  if (pathname === "/dashboard/home") return "home";
  if (pathname === "/dashboard") return "dashboard";
  if (pathname.startsWith("/dashboard/campaign")) return "campaign";
  if (pathname.startsWith("/listings")) return "listings";
  if (pathname.startsWith("/messages")) return "messages";
  if (pathname.startsWith("/rop")) return "rop";
  if (pathname.startsWith("/social")) return "social";
  if (pathname.startsWith("/training")) return "training";
  if (pathname.startsWith("/reactivation")) return "reactivation";
  if (pathname.startsWith("/memory")) return "memory";
  if (pathname.startsWith("/journal")) return "journal";
  if (pathname.startsWith("/command")) return "command";
  if (pathname.startsWith("/team")) return "team";
  return "other";
}

export default function RouteStyleBridge() {
  const pathname = usePathname();

  useEffect(() => {
    const body = document.body;

    body.dataset.borisRoute = pathname;
    body.dataset.borisArea = resolveArea(pathname);

    body.classList.add("boris-ui-v2");

    return () => {
      delete body.dataset.borisRoute;
      delete body.dataset.borisArea;
    };
  }, [pathname]);

  return null;
}
