import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();

  return [
    { url: "https://boris-ai.pro/", lastModified: now, changeFrequency: "weekly", priority: 1 },
    { url: "https://boris-ai.pro/privacy", lastModified: now, changeFrequency: "monthly", priority: 0.3 },
    { url: "https://boris-ai.pro/oferta", lastModified: now, changeFrequency: "monthly", priority: 0.3 },
    { url: "https://boris-ai.pro/support", lastModified: now, changeFrequency: "monthly", priority: 0.4 },

  ];
}
