import { AccountProvider } from "./lib/AccountContext";
import type { Metadata } from "next";
import { Geist, Geist_Mono, Manrope, Inter } from "next/font/google";
import "./globals.css";
import BorisWidget from "./components/BorisWidget";

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

export const metadata: Metadata = {
  title: "БОРИС — умный сервис интернет-рекламы",
  description: "БОРИС автоматизирует объявления, тексты, баннеры и аналитику. Ваша реклама работает, пока вы занимаетесь бизнесом.",
  icons: { icon: "/icon.png" },
  openGraph: {
    title: "БОРИС — умный сервис интернет-рекламы",
    description: "Автоматизация объявлений, текстов, баннеров и аналитики. Реклама работает сама.",
    url: "https://boris-ai.pro",
    siteName: "БОРИС",
    type: "website",
  },
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
      <body className="min-h-full flex flex-col"><AccountProvider>{children}</AccountProvider>        <BorisWidget />
        <Metrika />
      </body>
    </html>
  );
}
