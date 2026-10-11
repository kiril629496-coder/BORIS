import { ImageResponse } from 'next/og';

export const runtime = 'nodejs';
export const alt = 'Персональный разбор бизнеса от BORIS';
export const size = {width: 1200, height: 630};
export const contentType = 'image/png';

type Props = { params: Promise<{ token: string }> };

export default async function Image({params}: Props) {
  const {token} = await params;
  let company = 'Ваш бизнес';
  let score: number | null = null;
  let demand: number | null = null;
  try {
    const r = await fetch('https://boris-ai.pro/api/viral/public/' + encodeURIComponent(token), {cache: 'no-store'});
    if (r.ok) {
      const data = await r.json();
      company = String(data?.company?.name || company).slice(0, 90);
      score = Number.isFinite(Number(data?.growth_score?.total)) ? Number(data.growth_score.total) : null;
      demand = Number.isFinite(Number(data?.demand?.total_count)) ? Number(data.demand.total_count) : null;
    }
  } catch {}

  return new ImageResponse(
    <div style={{
      width:'100%',height:'100%',display:'flex',flexDirection:'column',justifyContent:'space-between',
      padding:'64px 72px',background:'#071020',color:'#fff',fontFamily:'sans-serif',position:'relative',
    }}>
      <div style={{display:'flex',position:'absolute',width:420,height:420,right:-120,top:-120,borderRadius:999,background:'rgba(82,109,255,.24)'}} />
      <div style={{display:'flex',position:'absolute',width:330,height:330,left:-100,bottom:-160,borderRadius:999,background:'rgba(139,92,246,.20)'}} />
      <div style={{display:'flex',fontSize:28,fontWeight:800,letterSpacing:'-1px'}}>BORIS <span style={{color:'#60a5fa',marginLeft:9}}>AUDIT</span></div>
      <div style={{display:'flex',flexDirection:'column',maxWidth:920}}>
        <div style={{display:'flex',fontSize:21,fontWeight:800,color:'#93c5fd',textTransform:'uppercase',letterSpacing:'3px'}}>Персональный разбор бизнеса</div>
        <div style={{display:'flex',fontSize:66,lineHeight:1.02,fontWeight:900,letterSpacing:'-4px',marginTop:20}}>BORIS нашёл точки роста для «{company}»</div>
      </div>
      <div style={{display:'flex',gap:18}}>
        <div style={{display:'flex',flexDirection:'column',padding:'18px 24px',borderRadius:24,background:'rgba(255,255,255,.09)'}}>
          <div style={{display:'flex',fontSize:38,fontWeight:900}}>{score ?? '—'}<span style={{fontSize:18,color:'rgba(255,255,255,.45)',marginTop:16,marginLeft:5}}>/100</span></div>
          <div style={{display:'flex',fontSize:16,color:'rgba(255,255,255,.6)',marginTop:4}}>Потенциал роста</div>
        </div>
        <div style={{display:'flex',flexDirection:'column',padding:'18px 24px',borderRadius:24,background:'rgba(255,255,255,.09)'}}>
          <div style={{display:'flex',fontSize:38,fontWeight:900}}>{demand ?? '—'}</div>
          <div style={{display:'flex',fontSize:16,color:'rgba(255,255,255,.6)',marginTop:4}}>целевых запросов в месяц</div>
        </div>
        <div style={{display:'flex',alignItems:'center',marginLeft:'auto',fontSize:20,fontWeight:800,color:'#bfdbfe'}}>Проверить свой бизнес →</div>
      </div>
    </div>,
    size,
  );
}
