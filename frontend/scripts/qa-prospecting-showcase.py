#!/usr/bin/env python3
from __future__ import annotations
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
showcase = root / "app/dashboard/prospecting/page.tsx"
css = root / "app/dashboard/prospecting/page.module.css"
manage = root / "app/dashboard/prospecting/manage/page.tsx"
manage_css = root / "app/dashboard/prospecting/manage/page.module.css"
dashboard_layout = root / "app/dashboard/layout.tsx"

errors: list[str] = []

def need(cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)

need(showcase.is_file(), "missing showcase page")
need(css.is_file(), "missing showcase css")
need(manage.is_file(), "missing manage page")
need(manage_css.is_file(), "missing manage css")
need(dashboard_layout.is_file(), "missing dashboard layout")

if errors:
    print("PROSPECTING_SHOWCASE_QA=FAIL")
    for e in errors:
        print(" -", e)
    raise SystemExit(2)

p = showcase.read_text(encoding="utf-8")
c = css.read_text(encoding="utf-8")
m = manage.read_text(encoding="utf-8")
mc = manage_css.read_text(encoding="utf-8")
dl = dashboard_layout.read_text(encoding="utf-8")

for forbidden in ("fetch(", "/api/", "boris_token", "email_address", "<input", "<textarea", "<select", "<form"):
    need(forbidden not in p, f"showcase contains forbidden live/secret pattern: {forbidden}")

ls_calls = re.findall(r'localStorage\.getItem\(([^)]+)\)', p)
need(all("boris_user_role" in x for x in ls_calls), "showcase reads localStorage other than owner-role hint")

ids = re.findall(r'id:"([A-E])"', p)
need(ids == list("ABCDE"), f"expected A-E exactly once, got {ids}")
banners = re.findall(r'banner:"([^"]+)"', p)
need(len(banners) == 5, f"expected 5 banners, got {len(banners)}")
need(all(x.startswith("/reviews/") and x.endswith(".jpg") for x in banners), f"unexpected banner sources: {banners}")
need(p.count("chips:[") >= 5, "each A/B variant must have banner benefit chips")
need(p.count('cta:"') >= 5, "each A/B variant must have CTA")
need("ПОЛНЫЙ ТЕКСТ" in p and "Так письмо выглядит для холодной базы" in p, "full letter presentation missing")
need("<details" not in p, "full letter must not be hidden behind details")

need("CRM И ОБРАБОТКА ОТВЕТОВ" in p, "CRM section missing")
need("возможна интеграция с вашей CRM" in p, "CRM integration statement missing")
for price in ("10 000 ₽", "14 000 ₽", "18 000 ₽"):
    need(price in p, f"pricing tier missing: {price}")
need("≈1000 писем" in p or "≈ 1000 писем" in p, "monthly mail volume statement missing")

need("@media(max-width:700px)" in c, "700px mobile breakpoint missing")
need("@media(max-width:420px)" in c, "420px mobile breakpoint missing")
need(".variantGrid{grid-template-columns:1fr!important}" in c, "single-column mobile A/B grid missing")
need("touch-action:pan-y" in c, "touch scrolling for full letter missing")
need("-webkit-overflow-scrolling:touch" in c, "iOS momentum scrolling missing")
need("overflow-x:hidden!important" in c, "horizontal overflow guard missing")
need("COMPACT A/B SHOWCASE V7" in c, "compact A/B card contract missing")
need("FINAL CREATIVE BANNERS + MOBILE QA V8" in c, "creative banner/mobile contract missing")
need("VISIBLE CLIENT BANNERS V9" in c, "visible client banner contract missing")
need("font-size:18px!important" in c and ".bannerContent>b" in c, "banner offer/CTA visibility contract missing")

need("/api/prospecting/scale/" in m, "management page lost prospecting API")
need("Authorization:" in m and "Bearer" in m, "management page lost bearer auth")
need('credentials:"include"' in m, "management page lost credentialed API requests")
need("mailbox" in m.lower() and "campaign" in m.lower(), "management page no longer contains mail/campaign controls")
for marker in ("Быстрый расчёт","Цена без лишних согласований","Без срыва поставки","Под задачу объекта","Простой заказ в 3 шага"):
    need(marker in m, f"management A/B copy out of sync: {marker}")
for banner in ("materials.jpg","construction.jpg","machinery.jpg","paving.jpg","towtruck.jpg"):
    need(banner in m, f"management A/B banner out of sync: {banner}")
need(m.count('label:"') >= 5, "management page lost five A/B defaults")
need("можно получить ориентир по цене заранее" in m, "management A copy is not the full sales version")
need("ab_stats" in m and "abResultGrid" in m, "management A/B result UI missing")
need("ab_decision" in m and "Решение BORIS" in m, "management A/B decision UI missing")
need("A/B RESULT MINI-DASHBOARD V1" in mc and ".abResultGrid" in mc, "management A/B result styles missing")
need("BORIS A/B DECISION V2" in mc and ".abDecision" in mc, "management A/B decision styles missing")

# The showcase must be public without onboarding/profile, while /manage stays guarded.
need('pathname === "/dashboard/prospecting"' in dl, "public exact prospecting bypass missing")
need('pathname.startsWith("/dashboard/prospecting")' not in dl, "prospecting bypass is too broad and could expose /manage")
need('pathname.includes("/dashboard/prospecting")' not in dl, "prospecting bypass is too broad and could expose /manage")
need('return <OnboardingGate>{children}</OnboardingGate>' in dl, "dashboard onboarding guard missing")

if errors:
    print("PROSPECTING_SHOWCASE_QA=FAIL")
    for e in errors:
        print(" -", e)
    raise SystemExit(1)

print("PROSPECTING_SHOWCASE_QA=PASS")
print("SHOWCASE_SECRET_ISOLATION=PASS")
print("SHOWCASE_AB5=PASS")
print("SHOWCASE_MOBILE_STATIC=PASS")
print("PROSPECTING_MANAGE_CONTOUR=PASS")
