"use client";
import { useState } from "react";
import { Mascot } from './Mascot';
import { usePathname } from "next/navigation";

async function api(input: string, init: RequestInit = {}): Promise<Response> {
  const token = typeof window !== "undefined" ? localStorage.getItem("boris_token") : null;
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}

const PW_TASKS: any[] = [
  { id:"banner", name:"Баннеры и картинки", where:"вкладка «Баннеры и картинки» → поле описания при генерации",
    qs:["Что рекламируем — товар или услуга, опишите подробно (тип, цвет, размер, комплектация)?","Какая ниша и город?","Главное преимущество, которое должно бросаться в глаза?","Фирменные цвета или пожелания по стилю?","Что нельзя показывать или писать?"] },
  { id:"social", name:"Посты в соцсети", where:"страница «Социальные сети» → поле «Указания боту (промт) — как писать посты»",
    qs:["О чём канал и для кого пишем?","Какой тон — деловой, дружеский, экспертный?","Что должно быть в каждом посте обязательно (хештеги, призыв, контакты)?","Чего избегать — темы, слова, стиль?","Нужны ли вопросы к аудитории и как часто звать в личку?"] },
  { id:"manager", name:"ИИ Менеджер по продажам", where:"вкладка «Продажи» → ИИ Менеджер → настройки ответов",
    qs:["Что продаёте и по какой цене (вилка)?","Какая цель диалога — взять телефон, записать на замер, довести до оплаты?","Какие частые возражения и как на них отвечать?","Что нельзя обещать клиенту?","Сроки, доставка, гарантии — что важно упомянуть?"] },
  { id:"rop", name:"ИИ Руководитель отдела продаж", where:"вкладка «Продажи» → ИИ РОП → чек-лист разбора",
    qs:["Что обязательно должен сделать менеджер в звонке?","Какие ошибки для вас критичны?","Какие слова и фразы запрещены в разговоре?","Какая цель звонка — замер, оплата, следующий шаг?","На что смотреть в первую очередь при разборе?"] },
  { id:"listing", name:"Объявления Avito", where:"вкладка «Объявления» → описание товара",
    qs:["Что продаёте — точное название и характеристики?","Ключевые преимущества перед конкурентами?","Цена и условия (торг, доставка, самовывоз)?","География — куда возите или где забирать?","Что обязательно указать в тексте?"] },
];

export default function BorisWidget() {
  const pathname = usePathname() || "";
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState<"off"|"task"|"questions"|"result">("off");
  const [task, setTask] = useState<any>(null);
  const [answers, setAnswers] = useState<string[]>([]);
  const [result, setResult] = useState("");
  const [gen, setGen] = useState(false);

  const hidden = pathname === "/" || pathname.startsWith("/dashboard") || pathname.startsWith("/login") || pathname.startsWith("/oferta") || pathname.startsWith("/privacy");
  if (hidden) return null;

  const ask = async () => {
    if (!input.trim()) return;
    const msg = input.trim();
    const h = [...history, {role:"user", content:msg}];
    setHistory(h); setInput(""); setLoading(true);
    try {
      const r = await api("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({message: msg, history: history.slice(-10)})});
      const d = await r.json();
      setHistory([...h, {role:"assistant", content: d.answer || "Не смог ответить, попробуйте ещё раз"}]);
    } catch { setHistory([...h, {role:"assistant", content:"Ошибка связи. Попробуйте снова."}]); }
    setLoading(false);
  };

  const generate = async () => {
    if (!task) return;
    setGen(true); setStep("result");
    const qa = task.qs.map((q:string,i:number)=> q + " Ответ: " + (answers[i] || "не указано")).join(" | ");
    const msg = "Ты помогаешь клиенту составить промт-инструкцию для ИИ по задаче: " + task.name + ". Ответы клиента: " + qa + ". Собери ГОТОВЫЙ текст инструкции на русском, который клиент вставит в настройки. Только сам текст инструкции, конкретными правилами, без вступлений. Структурируй короткими абзацами или списком.";
    try {
      const r = await api("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({message: msg, history: []})});
      const d = await r.json();
      setResult(d.answer || "Не удалось собрать промт");
    } catch { setResult("Ошибка связи. Попробуйте ещё раз."); }
    setGen(false);
  };

  return (
    <>
      {step !== "off" && (
        <div style={{position:"fixed", inset:0, background:"rgba(16,24,40,0.45)", zIndex:1200, display:"flex", alignItems:"center", justifyContent:"center", padding:"20px"}} onClick={()=>setStep("off")}>
          <div onClick={e=>e.stopPropagation()} style={{background:"#fff", borderRadius:"16px", width:"640px", maxWidth:"100%", maxHeight:"85vh", overflowY:"auto", padding:"24px", boxShadow:"0 20px 60px rgba(16,24,40,0.3)"}}>
            <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:"14px"}}>
              <div style={{fontSize:"18px", fontWeight:700, color:"#1D2939"}}>✨ Помощь в написании промта</div>
              <button onClick={()=>setStep("off")} style={{background:"none", border:"none", fontSize:"22px", color:"#98A2B3", cursor:"pointer"}}>×</button>
            </div>
            {step === "task" && (
              <div style={{display:"flex", flexDirection:"column", gap:"8px"}}>
                <div style={{fontSize:"15px", color:"#667085", marginBottom:"6px"}}>Для какой задачи нужен промт?</div>
                {PW_TASKS.map((t:any)=>(
                  <button key={t.id} onClick={()=>{setTask(t); setAnswers(new Array(t.qs.length).fill("")); setStep("questions");}} style={{textAlign:"left", background:"#fff", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"14px 16px", cursor:"pointer", fontSize:"15px", fontWeight:600, color:"#1D2939"}}>{t.name}</button>
                ))}
              </div>
            )}
            {step === "questions" && task && (
              <div>
                <div style={{fontSize:"15px", color:"#667085", marginBottom:"14px"}}>{task.name} — ответьте на вопросы</div>
                <div style={{display:"flex", flexDirection:"column", gap:"12px"}}>
                  {task.qs.map((q:string,i:number)=>(
                    <div key={i}>
                      <div style={{fontSize:"15px", color:"#1D2939", marginBottom:"5px"}}>{i+1}. {q}</div>
                      <textarea value={answers[i]||""} onChange={e=>{const a=[...answers]; a[i]=e.target.value; setAnswers(a);}} rows={2} style={{width:"100%", padding:"10px 12px", borderRadius:"10px", border:"1.5px solid #E3E7F0", fontSize:"15px", fontFamily:"inherit"}} />
                    </div>
                  ))}
                </div>
                <div style={{display:"flex", gap:"10px", marginTop:"16px"}}>
                  <button onClick={()=>setStep("task")} style={{background:"#fff", color:"#475467", border:"1.5px solid #E3E7F0", borderRadius:"10px", padding:"11px 18px", fontWeight:600, cursor:"pointer", fontSize:"15px"}}>Назад</button>
                  <button onClick={generate} style={{flex:1, background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"11px 18px", fontWeight:700, cursor:"pointer", fontSize:"15px"}}>Собрать промт →</button>
                </div>
              </div>
            )}
            {step === "result" && (
              <div>
                {gen && <div style={{fontSize:"15px", color:"#667085", padding:"20px 0"}}>Борис пишет промт…</div>}
                {!gen && (
                  <div>
                    <div style={{background:"#F9FAFB", border:"1px solid #E3E7F0", borderRadius:"12px", padding:"16px", fontSize:"15px", color:"#1D2939", whiteSpace:"pre-wrap", lineHeight:1.55, marginBottom:"14px"}}>{result}</div>
                    <div style={{background:"#EEF4FF", border:"1px solid #D0DEFF", borderRadius:"10px", padding:"12px 14px", fontSize:"14px", color:"#344054", marginBottom:"14px"}}>Скопируйте текст и вставьте в <b>{task ? task.where : ""}</b>, затем нажмите «Сохранить». После сохранения Борис изучит инструкцию и станет применять её в работе.</div>
                    <div style={{display:"flex", gap:"10px"}}>
                      <button onClick={()=>{navigator.clipboard.writeText(result); alert("Промт скопирован");}} style={{flex:1, background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"11px 18px", fontWeight:700, cursor:"pointer", fontSize:"15px"}}>Скопировать</button>
                      <button onClick={()=>setStep("questions")} style={{background:"#fff", color:"#475467", border:"1.5px solid #E3E7F0", borderRadius:"10px", padding:"11px 18px", fontWeight:600, cursor:"pointer", fontSize:"15px"}}>Изменить ответы</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {open && (
        <div style={{position:"fixed", bottom:"90px", right:"24px", width:"420px", maxWidth:"calc(100vw - 48px)", height:"560px", background:"#F6F7FB", border:"1px solid #2F6FED", borderRadius:"16px", display:"flex", flexDirection:"column", boxShadow:"0 8px 32px rgba(16,24,40,0.25)", zIndex:1000}}>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", padding:"16px", borderBottom:"1px solid #EEF2FA"}}>
            <span style={{fontWeight:"bold", color:"#2F6FED"}}>Борис — помощник</span>
            <button onClick={()=>setOpen(false)} style={{background:"none", border:"none", color:"#2F6FED", fontSize:"18px", cursor:"pointer"}}>×</button>
          </div>
          <div style={{flex:1, overflowY:"auto", padding:"14px"}}>
            <button onClick={()=>setStep("task")} style={{width:"100%", background:"#F4F3FF", color:"#5925DC", border:"1px solid #D9D6FE", borderRadius:"10px", padding:"9px", fontWeight:"bold", cursor:"pointer", fontSize:"14px", marginBottom:"10px"}}>✨ Помоги написать промт</button>
            <button onClick={()=>{window.location.href="/support";}} style={{width:"100%", background:"#EEF4FF", color:"#2456D8", border:"1px solid #D0DEFF", borderRadius:"10px", padding:"9px", fontWeight:"bold", cursor:"pointer", fontSize:"14px", marginBottom:"10px"}}>💬 Задать вопрос в поддержку</button>
            {history.length === 0 && <div style={{fontSize:"16px", color:"#667085"}}>Привет! Я Борис 👋 Спросите меня о чём угодно: как пользоваться сервисом, куда нажать, что делать.</div>}
            {history.map((m:any,i:number)=>(
              <div key={i} style={{marginBottom:"10px", textAlign: m.role==="user" ? "right" : "left"}}>
                <div style={{display:"inline-block", maxWidth:"85%", background: m.role==="user" ? "#2F6FED" : "#fff", color: m.role==="user" ? "#fff" : "#1D2939", border: m.role==="user" ? "none" : "1px solid #E3E7F0", borderRadius:"12px", padding:"9px 12px", fontSize:"16px", whiteSpace:"pre-wrap", textAlign:"left"}}>{m.content}</div>
              </div>
            ))}
            {loading && <div style={{fontSize:"16px", color:"#667085"}}>Борис думает…</div>}
          </div>
          <div style={{display:"flex", gap:"8px", padding:"12px", borderTop:"1px solid #EEF2FA"}}>
            <input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{ if(e.key==="Enter") ask(); }} placeholder="Спроси Бориса…" style={{flex:1, padding:"10px 12px", borderRadius:"10px", border:"1.5px solid #E3E7F0", fontSize:"16px"}} />
            <button onClick={ask} style={{background:"#2F6FED", color:"#fff", border:"none", borderRadius:"10px", padding:"10px 14px", cursor:"pointer", fontWeight:700}}>➤</button>
          </div>
        </div>
      )}

      <button onClick={()=>setOpen(!open)} style={{position:"fixed", bottom:"24px", right:"24px", width:"68px", height:"68px", borderRadius:"50%", background:"linear-gradient(135deg, #4C8DFF, #2F6FED)", border:"none", cursor:"pointer", boxShadow:"0 6px 20px rgba(47,111,237,0.45)", zIndex:1000, display:"flex", alignItems:"center", justifyContent:"center", fontSize:"30px"}}>
        {open ? <span style={{color:"#fff", fontSize:"26px"}}>×</span> : <Mascot size={44} interactive={true} />}
      </button>
    </>
  );
}
