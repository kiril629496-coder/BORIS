import { AccountProvider } from "./lib/AccountContext";
import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Manrope, Inter } from "next/font/google";
import "./globals.css";
import NpsPrompt from "./components/NpsPrompt";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin", "cyrillic"],
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin", "cyrillic"],
});

import Metrika from "./Metrika";
import ScenarioNavBar from "./components/ScenarioNavigation";
import "./components/boris-ui/boris-ui.css";
import RouteStyleBridge from "./components/boris-ui/RouteStyleBridge";
import CategoryActionBridge from "./components/CategoryActionBridge";
import PwaRegister from "./components/PwaRegister";
import PwaInstallHint from "./components/PwaInstallHint";

export const metadata: Metadata = {
  title: "БОРИС — умный сервис интернет-рекламы",
  description: "БОРИС автоматизирует объявления, тексты, баннеры и аналитику. Ваша реклама работает, пока вы занимаетесь бизнесом.",
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [{ url: "/favicon.ico" }, { url: "/boris-icon-192.png", type: "image/png", sizes: "192x192" }],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  appleWebApp: { capable: true, title: "BORIS AI", statusBarStyle: "default" },
  openGraph: {
    title: "БОРИС — умный сервис интернет-рекламы",
    description: "Автоматизация объявлений, текстов, баннеров и аналитики. Реклама работает сама.",
    url: "https://boris-ai.pro",
    siteName: "БОРИС",
    type: "website",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#2563eb",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ru"
      className={`${geistSans.variable} ${geistMono.variable} ${manrope.variable} ${inter.variable} h-full antialiased`}
    >
      <head>
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="mobile-web-app-capable" content="yes" />
      </head>
      <body className="min-h-full flex flex-col">
        <PwaRegister /><PwaInstallHint /><RouteStyleBridge /><AccountProvider><CategoryActionBridge />
        {children}
        <NpsPrompt /></AccountProvider>
        <Metrika />
        <ScenarioNavBar />
      </body>
    </html>
  );
}