export function GET() {
  return Response.json({
    id: "/messenger",
    name: "BORIS Messenger",
    short_name: "Messenger",
    description: "Чаты, работа с клиентами и CRM в BORIS Messenger.",
    start_url: "/messenger",
    scope: "/messenger",
    display: "standalone",
    background_color: "#101827",
    theme_color: "#1d4ed8",
    orientation: "any",
    categories: ["business", "communication"],
    icons: [
      { src: "/messenger/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any maskable" },
      { src: "/messenger/apple-touch-icon.png", sizes: "180x180", type: "image/png", purpose: "any" },
    ],
    shortcuts: [
      { name: "Чаты", short_name: "Чаты", url: "/messenger", icons: [{ src: "/messenger/icon.svg", sizes: "any", type: "image/svg+xml" }] },
      { name: "Работа", short_name: "Работа", url: "/messenger?section=work", icons: [{ src: "/messenger/icon.svg", sizes: "any", type: "image/svg+xml" }] },
    ],
  }, { headers: { "Cache-Control": "no-store", "Content-Type": "application/manifest+json; charset=utf-8" } });
}
