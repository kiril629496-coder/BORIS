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
        "boris_ref=" + encodeURIComponent(clean) + "; path=/; max-age=" + 90 * 24 * 3600;
    }
    router.replace("/");
  }, [params, router]);

  return (
    <div style={{ minHeight: "60vh", display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "system-ui, sans-serif", color: "#667085" }}>
      Открываем БОРИСа…
    </div>
  );
}
