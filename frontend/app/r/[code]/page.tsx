"use client";
import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function RefRedirect() {
  const params = useParams();
  const router = useRouter();

  useEffect(() => {
    const raw = String(params?.code || "");
    const clean = raw.replace(/[^a-zA-Z0-9_-]/g, "").toLowerCase().slice(0, 40);
    if (clean) {
      document.cookie =
        "boris_ref=" + encodeURIComponent(clean) + "; path=/; max-age=" + 90 * 24 * 3600 + "; SameSite=Lax";
      fetch("/api/viral/events", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({event:"viral_referral_visit", referral_code:clean, source:"referral_redirect"})
      }).catch(() => {});
    }
    router.replace("/audit");
  }, [params, router]);

  return (
    <div style={{ minHeight: "60vh", display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "system-ui, sans-serif", color: "var(--boris-text-2)" }}>
      Открываем БОРИСа…
    </div>
  );
}
