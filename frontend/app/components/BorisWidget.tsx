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

  // P0 05.08: помощник по промтам — НЕ основной ассистент BORIS.
  // Из клиентского пути убран целиком: ни в кабинете, ни в мастере
  // первого запуска, ни на экране анализа он висеть не должен —
  // человек принимает синий кружок за диалог и получает конструктор.
  // Остаётся только на своей странице до переосмысления.
  // 07.08: возвращён в клиентские разделы по требованию Кирилла — трое
  // сотрудников клиента начинают работу, им нужна точка «спросить и написать
  // в поддержку». Скрыт там, где мешает знакомству с продуктом: лендинг,
  // логин, мастер первого запуска, экран анализа, оферта и политика.
  const blocked = ["/", "/login", "/verify", "/onboarding", "/oferta", "/privacy"];
  const allowed = !blocked.some((b) => b === "/" ? pathname === "/" : pathname.startsWith(b));
  if (!allowed) return null;

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
        <div onClick={()=>setStep("off")} className="boris-f3-a80617f28b24c8">
          <div onClick={e=>e.stopPropagation()} className="boris-f3-5efe614a48ca73">
            <div className="boris-f3-ff0342ec9a5e5a">
              <div>
                <div className="boris-f3-551736d55f4166">✨ Помощь в написании промта</div>
                <a href="/support" className="boris-f3-a33c196bdfb33e">Нужен живой ответ — написать в поддержку →</a>
              </div>
              <button onClick={()=>setStep("off")} className="boris-f3-a2c984b050be8e">×</button>
            </div>
            {step === "task" && (
              <div className="boris-f3-a28c8d3bc812e9">
                <div className="boris-f3-e1beaa7b24e9ef">Для какой задачи нужен промт?</div>
                {PW_TASKS.map((t:any)=>(
                  <button key={t.id} onClick={()=>{setTask(t); setAnswers(new Array(t.qs.length).fill("")); setStep("questions");}} className="boris-f3-194678b0644811">{t.name}</button>
                ))}
              </div>
            )}
            {step === "questions" && task && (
              <div>
                <div className="boris-f3-1734b5238b36ac">{task.name} — ответьте на вопросы</div>
                <div className="boris-f3-85e7427e7ff5bd">
                  {task.qs.map((q:string,i:number)=>(
                    <div key={i}>
                      <div className="boris-f3-7bd5bf25c18042">{i+1}. {q}</div>
                      <textarea value={answers[i]||""} onChange={e=>{const a=[...answers]; a[i]=e.target.value; setAnswers(a);}} rows={2}  className="boris-f3-be31bafcd56f32"/>
                    </div>
                  ))}
                </div>
                <div className="boris-f3-6292ed69c3d191">
                  <button onClick={()=>setStep("task")} className="boris-f3-6127f98b55232e">Назад</button>
                  <button onClick={generate} className="boris-f3-62836065476bec">Собрать промт →</button>
                </div>
              </div>
            )}
            {step === "result" && (
              <div>
                {gen && <div className="boris-f3-c3b79877ad807f">Борис пишет промт…</div>}
                {!gen && (
                  <div>
                    <div className="boris-f3-e60fa87e373ab9">{result}</div>
                    <div className="boris-f3-3d681faf9b8e51">Скопируйте текст и вставьте в <b>{task ? task.where : ""}</b>, затем нажмите «Сохранить». После сохранения Борис изучит инструкцию и станет применять её в работе.</div>
                    <div className="boris-f3-4a5cec13ef324a">
                      <button onClick={()=>{navigator.clipboard.writeText(result); alert("Промт скопирован");}} className="boris-f3-62836065476bec">Скопировать</button>
                      <button onClick={()=>setStep("questions")} className="boris-f3-6127f98b55232e">Изменить ответы</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {open && (
        <div className="boris-f3-51b85d7bffde60">
          <div className="boris-f3-ed0ec35f6e1464">
            <span className="boris-f3-d03b870265f931">Борис — помощник</span>
            <button onClick={()=>setOpen(false)} className="boris-f3-36d5df214b4182">×</button>
          </div>
          <div className="boris-f3-1b6c7aac788446">
            <button onClick={()=>setStep("task")} className="boris-f3-6a39c4addb982d">✨ Помоги написать промт</button>
            <button onClick={()=>{window.location.href="/support";}} className="boris-f3-16473738c63b78">💬 Задать вопрос в поддержку</button>
            {history.length === 0 && <div className="boris-f3-f1da49e8f0b0b9">Привет! Я Борис 👋 Спросите меня о чём угодно: как пользоваться сервисом, куда нажать, что делать.</div>}
            {history.map((m:any,i:number)=>(
              <div key={i} style={{marginBottom:"10px", textAlign: m.role==="user" ? "right" : "left"}}>
                <div style={{display:"inline-block", maxWidth:"85%", background: m.role==="user" ? "var(--boris-blue)" : "#fff", color: m.role==="user" ? "#fff" : "var(--boris-text)", border: m.role==="user" ? "none" : "1px solid var(--boris-border)", borderRadius:"12px", padding:"9px 12px", fontSize:"16px", whiteSpace:"pre-wrap", textAlign:"left"}}>{m.content}</div>
              </div>
            ))}
            {loading && <div className="boris-f3-f1da49e8f0b0b9">Борис думает…</div>}
          </div>
          <div className="boris-f3-687b757d84fa93">
            <input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{ if(e.key==="Enter") ask(); }} placeholder="Спроси Бориса…"  className="boris-f3-aec219189bbf8f"/>
            <button onClick={ask} className="boris-f3-cc02fc3bffee20">➤</button>
          </div>
        </div>
      )}

      <button onClick={()=>setOpen(!open)} className="boris-f3-f0921ff20004fe">
        {open ? <span className="boris-f3-7bd1aa87bad624">×</span> : <Mascot size={44} interactive={true} />}
      </button>
    </>
  );
}
