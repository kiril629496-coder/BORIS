"use client";
import { EcShell } from "@/app/ui/EcShell";
import AvitoABTestsPanel from "../AvitoABTestsPanel";

export default function AvitoABTestsPage(){
  return <EcShell tab="ab-tests" title="АБ тесты рекламы Авито" subtitle="Какие заголовки, тексты и креативы реально дают больше обращений"><AvitoABTestsPanel /></EcShell>;
}
