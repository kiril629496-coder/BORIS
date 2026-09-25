import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  const softwareServices = [
    "mobile-app-development",
    "ios-development",
    "android-development",
    "cross-platform-app-development",
    "saas-development",
    "web-app-development",
    "crm-development",
    "ai-automation",
    "ai-agents",
    "telegram-bots",
    "api-integration",
    "1c-integration",
    "ip-telephony",
    "parsers",
    "marketplace-integration",
    "admin-panel-development",
    "devops",
  ];
  const softwareBlog = [
    "skolko-stoit-mobilnoe-prilozhenie",
    "chto-vhodit-v-saas-mvp",
    "kogda-biznesu-nuzhna-crm",
    "flutter-ili-native",
    "kak-avtomatizirovat-otdel-prodazh",
    "kak-svyazat-site-i-1c",
  ];

  return [
    { url: "https://boris-ai.pro/", lastModified: now, changeFrequency: "weekly", priority: 1 },
    { url: "https://boris-ai.pro/privacy", lastModified: now, changeFrequency: "monthly", priority: 0.3 },
    { url: "https://boris-ai.pro/oferta", lastModified: now, changeFrequency: "monthly", priority: 0.3 },
    { url: "https://boris-ai.pro/support", lastModified: now, changeFrequency: "monthly", priority: 0.4 },

    { url: "https://boris-ai.pro/software-dev/", lastModified: now, changeFrequency: "weekly", priority: 0.9 },
    { url: "https://boris-ai.pro/software-dev/services", lastModified: now, changeFrequency: "weekly", priority: 0.8 },
    ...softwareServices.map((slug) => ({
      url: `https://boris-ai.pro/software-dev/services/${slug}`,
      lastModified: now,
      changeFrequency: "weekly" as const,
      priority: 0.75,
    })),
    { url: "https://boris-ai.pro/software-dev/cases", lastModified: now, changeFrequency: "monthly", priority: 0.7 },
    { url: "https://boris-ai.pro/software-dev/cases/boris-ai", lastModified: now, changeFrequency: "monthly", priority: 0.85 },
    { url: "https://boris-ai.pro/software-dev/about", lastModified: now, changeFrequency: "monthly", priority: 0.6 },
    { url: "https://boris-ai.pro/software-dev/contacts", lastModified: now, changeFrequency: "monthly", priority: 0.6 },
    { url: "https://boris-ai.pro/software-dev/blog", lastModified: now, changeFrequency: "weekly", priority: 0.7 },
    ...softwareBlog.map((slug) => ({
      url: `https://boris-ai.pro/software-dev/blog/${slug}`,
      lastModified: now,
      changeFrequency: "monthly" as const,
      priority: 0.65,
    })),
  ];
}
