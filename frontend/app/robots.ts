import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: ["/", "/privacy", "/oferta", "/support", "/software-dev/"],
        disallow: [
          "/api/",
          "/dashboard/",
          "/messages/",
          "/command/",
          "/manager/",
          "/rop/",
          "/memory/",
          "/training/",
          "/onboarding/",
          "/verify/",
          "/reactivation/",
          "/email-outreach/",
          "/agency/",
          "/ai/",
          "/listings/",
          "/journal/",
        ],
      },
    ],
    sitemap: "https://boris-ai.pro/sitemap.xml",
    host: "https://boris-ai.pro",
  };
}
