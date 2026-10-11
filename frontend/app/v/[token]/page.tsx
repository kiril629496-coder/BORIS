import type { Metadata } from 'next';
import PublicReportClient from './PublicReportClient';

type Props = { params: Promise<{ token: string }> };

async function report(token: string) {
  try {
    const r = await fetch('https://boris-ai.pro/api/viral/public/' + encodeURIComponent(token), {
      cache: 'no-store',
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

export async function generateMetadata({params}: Props): Promise<Metadata> {
  const {token} = await params;
  const data = await report(token);
  const company = String(data?.company?.name || 'вашего бизнеса');
  const points = Array.isArray(data?.top_opportunities) ? Math.min(3, data.top_opportunities.length) : 3;
  const title = data
    ? `BORIS нашёл ${points} точки роста для ${company}`
    : 'Персональный разбор бизнеса от BORIS';
  const description = 'Анализ спроса, рекламы, обработки клиентов и возможностей роста — без технической кухни.';
  const publicOrigin = 'https://boris-ai.pro';
  const publicPath = `/v/${encodeURIComponent(token)}`;
  const publicUrl = publicOrigin + publicPath;
  const imageUrl = publicUrl + '/opengraph-image';
  return {
    metadataBase: new URL(publicOrigin),
    title,
    description,
    robots: {index: false, follow: false},
    alternates: {canonical: publicUrl},
    openGraph: {
      title,
      description,
      type: 'website',
      url: publicUrl,
      images: [{url: imageUrl, width: 1200, height: 630}],
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
      images: [imageUrl],
    },
  };
}

export default async function Page({params}: Props) {
  const {token} = await params;
  return <PublicReportClient token={token} />;
}
