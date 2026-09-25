"use client";

import { useMemo, useState } from "react";
import { Page, PageHeader } from "../../ui";
import source from "./ads.json";

type City = "penza" | "moscow";
type AdSource = {
  category: string;
  titlePenza: string;
  textPenza: string;
  locationPenza: string;
  locationMoscow: string;
  photos: string[];
};

const adsSource = source as AdSource[];

function toMoscow(text: string) {
  return text
    .replaceAll("Пензен", "Москов")
    .replaceAll("Пензе", "Москве")
    .replaceAll("Пенза", "Москва")
    .replaceAll("пенза", "москва");
}

function cleanTitle(title: string) {
  return title
    .replace(/,?\s*(Пенза|Москва)\s*$/i, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function formatText(text: string) {
  return text.split("\n").map((line, i) => {
    const t = line.trim();
    if (!t) return <br key={`b-${i}`} />;
    const heading = t.length < 90 && (t === t.toUpperCase() || t.endsWith(":"));
    return heading
      ? <div key={i} style={{fontWeight:850,color:"#10233f",marginTop:i?14:0,marginBottom:5}}>{t}</div>
      : <div key={i} style={{marginBottom:4}}>{line}</div>;
  });
}

export default function AndreyCampaignPreview() {
  const [city, setCity] = useState<City>("penza");

  const ads = useMemo(() => adsSource.map((ad) => {
    const rawTitle = city === "penza" ? ad.titlePenza : toMoscow(ad.titlePenza);
    return {
      ...ad,
      title: cleanTitle(rawTitle),
      text: city === "penza" ? ad.textPenza : toMoscow(ad.textPenza),
      location: city === "penza" ? ad.locationPenza : ad.locationMoscow,
    };
  }), [city]);

  return (
    <Page>
      <div style={{maxWidth:1460,margin:"0 auto",paddingBottom:40}}>
        <PageHeader title="BORIS — готовые объявления перед публикацией" subtitle="Андрей · мебель на заказ · Пенза и Москва" />

        <div style={{display:"flex",gap:10,flexWrap:"wrap",margin:"0 0 24px"}}>
          {(["penza","moscow"] as City[]).map((key) => (
            <button key={key} onClick={() => setCity(key)} style={{
              border:city===key?"2px solid #2563eb":"1px solid #d7dee8",
              background:city===key?"#e8f1ff":"#fff",
              color:city===key?"#174ea6":"#344054",borderRadius:999,padding:"10px 16px",
              fontSize:14,fontWeight:800,cursor:"pointer"
            }}>
              {key==="penza"?"Пенза":"Москва"} · {adsSource.length} объявлений
            </button>
          ))}
        </div>

        {ads.map((ad,index) => (
          <article key={`${city}-${index}`} style={{
            background:"#fff",border:"1px solid #e3e8ef",borderRadius:20,
            padding:22,marginBottom:28,boxShadow:"0 8px 28px rgba(16,35,63,.05)"
          }}>
            <div style={{fontSize:14,color:"#667085",fontWeight:700,marginBottom:12}}>
              Андрей · {ad.category} · объявление {index+1} из {ads.length} · изображений {ad.photos.length}
            </div>

            <div style={{display:"flex",gap:10,flexWrap:"wrap",marginBottom:12}}>
              <span style={{padding:"9px 13px",borderRadius:999,fontWeight:800,background:"#e8f1ff",color:"#174ea6"}}>📍 Локация размещения: {ad.location}</span>
              <span style={{padding:"9px 13px",borderRadius:999,fontWeight:800,background:"#f2eaff",color:"#6938a8"}}>🎯 Категория: {ad.category}</span>
            </div>

            <h2 style={{fontSize:"clamp(24px,3vw,34px)",lineHeight:1.15,color:"#10233f",margin:"12px 0 18px",letterSpacing:"-.02em"}}>{ad.title}</h2>

            <div style={{display:"flex",gap:11,overflowX:"auto",paddingBottom:8,marginBottom:20}}>
              {ad.photos.map((src,i) => (
                <img key={src+i} src={src} alt={`${ad.category} ${i+1}`} loading="lazy" style={{width:220,height:220,objectFit:"cover",borderRadius:15,flex:"0 0 auto",border:"1px solid #e4e7ec"}} />
              ))}
            </div>

            <div style={{fontSize:17,lineHeight:1.72,maxWidth:960,color:"#24364f"}}>
              {formatText(ad.text)}
            </div>
          </article>
        ))}
      </div>
    </Page>
  );
}
