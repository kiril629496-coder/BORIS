const icon = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="44" fill="#101827"/><path d="M36 48c0-10 8-18 18-18h84c10 0 18 8 18 18v66c0 10-8 18-18 18H92l-28 24v-24H54c-10 0-18-8-18-18V48Z" fill="#1d4ed8"/><path d="M62 70h68M62 96h48" stroke="#fff" stroke-width="12" stroke-linecap="round"/><circle cx="146" cy="42" r="18" fill="#60a5fa"/></svg>`;

export function GET() {
  return new Response(icon, { headers: { "Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "public, max-age=31536000, immutable" } });
}
