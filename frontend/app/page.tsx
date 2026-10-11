import { redirect } from "next/navigation";
import LandingPage from "./LandingPageClient";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const query = await searchParams;

  if (first(query.utm_campaign) === "ocean") {
    const params = new URLSearchParams();
    for (const key of ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]) {
      const value = first(query[key]);
      if (value) params.set(key, value);
    }
    const suffix = params.toString();
    redirect(suffix ? `/audit?${suffix}` : "/audit");
  }

  return <LandingPage />;
}
