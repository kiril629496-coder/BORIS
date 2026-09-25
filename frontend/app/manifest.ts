import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "BORIS AI — продажи и маркетинг",
    short_name: "BORIS",
    description: "Управление рекламой, продажами, сообщениями, клиентами и аналитикой BORIS.",
    start_url: "/dashboard/command-center?source=pwa",
    scope: "/",
    display: "standalone",
    background_color: "#f6f8fc",
    theme_color: "#2563eb",
    orientation: "any",
    categories: ["business", "productivity"],
    icons: [
      { src: "/boris-icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/boris-icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/boris-icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "Главная", short_name: "Главная", url: "/dashboard/command-center", icons: [{ src: "/boris-icon-192.png", sizes: "192x192", type: "image/png" }] },
      { name: "Сообщения", short_name: "Сообщения", url: "/messages", icons: [{ src: "/boris-icon-192.png", sizes: "192x192", type: "image/png" }] },
      { name: "Спросить BORIS", short_name: "BORIS", url: "/command", icons: [{ src: "/boris-icon-192.png", sizes: "192x192", type: "image/png" }] },
      { name: "Объявления", short_name: "Объявления", url: "/dashboard/campaign", icons: [{ src: "/boris-icon-192.png", sizes: "192x192", type: "image/png" }] },
    ],
  };
}
