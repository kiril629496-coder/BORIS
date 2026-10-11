import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "BORIS Messenger",
  description: "Чаты, работа с клиентами и CRM в BORIS Messenger.",
  manifest: "/messenger/manifest.webmanifest",
  icons: {
    icon: [{ url: "/messenger/icon.svg", type: "image/svg+xml" }],
    apple: [{ url: "/messenger/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  appleWebApp: { capable: true, title: "BORIS Messenger", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  themeColor: "#101827",
  colorScheme: "light",
  viewportFit: "cover",
};

export default function MessengerLayout({ children }: { children: React.ReactNode }) {
  return children;
}
