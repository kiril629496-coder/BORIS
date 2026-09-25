import type { Metadata, Viewport } from 'next';

export const metadata: Metadata = {
  title: 'BORIS Phone',
  description: 'BORIS Phone — звонки, клиенты, задачи, CRM и AI-подсказки.',
  manifest: '/boris-phone.webmanifest',
  icons: {
    icon: [{ url: '/boris-icon-192.png', sizes: '192x192', type: 'image/png' }],
    apple: [{ url: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
  },
  appleWebApp: { capable: true, title: 'BORIS Phone', statusBarStyle: 'default' },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#2563eb',
};

export default function PhoneAppLayout({children}:{children:React.ReactNode}) { return children; }
