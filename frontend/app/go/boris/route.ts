import { NextResponse } from "next/server";

export function GET() {
  const target = new URL("https://boris-ai.pro/");
  target.searchParams.set("utm_source", "email");
  target.searchParams.set("utm_medium", "outreach");
  target.searchParams.set("utm_campaign", "owner_b2b");
  target.searchParams.set("utm_content", "boris");
  return NextResponse.redirect(target, 302);
}
