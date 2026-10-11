"use client";
import { EcShell } from "@/app/ui/EcShell";
import AvitoABTestsPanel from "../AvitoABTestsPanel";

export default function AvitoABTestsPage(){
  return <EcShell tab="ab-tests" title="Результаты A/B тестов" subtitle="Гипотезы, фактическая статистика Avito и следующие действия BORIS"><AvitoABTestsPanel /></EcShell>;
}
