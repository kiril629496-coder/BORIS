import Link from "next/link";

const steps=[
  ["01","Подключаем почту клиента","Письма уходят с корпоративной почты клиента. BORIS использует отдельный пароль приложения или безопасную авторизацию, проверяет SMTP для отправки и IMAP для получения ответов."],
  ["02","Собираем базу компаний","По нише и регионам BORIS находит сайты компаний, очищает дубли, исключает каталоги и нерелевантные домены, затем ищет рабочие email-контакты на сайтах."],
  ["03","Проверяем контакты","Адреса нормализуются, оцениваются по качеству и источнику. В кампанию попадают только контакты выше установленного порога. Отказы и отписки попадают в стоп-лист."],
  ["04","Готовим персональные письма","Для компании формируется тема и текст с переменными: компания, ниша, город и предложение. Перед запуском можно создать тестовый черновик прямо в почте клиента — без отправки."],
  ["05","Запускаем безопасно","Кампания работает с дневным лимитом и ограничением на один домен. Лимит можно наращивать постепенно, чтобы не перегревать новый отправляющий ящик."],
  ["06","Считаем открытия, ответы и лиды","В каждое письмо BORIS автоматически добавляет собственную невидимую метку открытия. В общей статистике видны отправки, примерная открываемость, повторные открытия, ответы и конверсия в лиды CRM."]
];

export default function EmailOutreachPublicPage(){
 return <main style={{minHeight:"100vh",background:"#f6f8fc",color:"#162033",fontFamily:"Inter,Arial,sans-serif"}}>
  <section style={{maxWidth:1180,margin:"0 auto",padding:"48px 24px 20px"}}>
   <div style={{display:"flex",justifyContent:"space-between",gap:24,alignItems:"center",flexWrap:"wrap"}}>
    <div style={{maxWidth:760}}>
     <div style={{fontSize:13,fontWeight:800,letterSpacing:1.2,color:"#4b68ff"}}>BORIS AI · B2B-ПОИСК КЛИЕНТОВ</div>
     <h1 style={{fontSize:"clamp(34px,5vw,58px)",lineHeight:1.03,margin:"14px 0 18px"}}>Поиск компаний и email-рассылки с почты клиента</h1>
     <p style={{fontSize:20,lineHeight:1.55,color:"#526079",margin:0}}>BORIS собирает целевую базу, находит рабочие контакты, готовит персональные письма, отправляет их с корпоративной почты клиента и превращает ответы в лиды CRM.</p>
    </div>
    <div style={{padding:"18px 22px",borderRadius:18,background:"#fff",boxShadow:"0 10px 30px rgba(40,60,120,.08)",minWidth:260}}>
      <b style={{display:"block",fontSize:18}}>Публичная демонстрация</b>
      <span style={{display:"block",marginTop:6,color:"#6b7890",lineHeight:1.45}}>Без доступа к почте, паролям, базе или боевым кампаниям.</span>
    </div>
   </div>
  </section>

  <section style={{maxWidth:1180,margin:"0 auto",padding:"20px 24px 52px"}}>
   <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(280px,1fr))",gap:16}}>
    {steps.map(([num,title,text])=><article key={num} style={{background:"#fff",border:"1px solid #e4e9f3",borderRadius:22,padding:24,boxShadow:"0 8px 24px rgba(30,50,100,.05)"}}>
      <div style={{width:44,height:44,borderRadius:14,display:"grid",placeItems:"center",background:"#eef2ff",color:"#4057d6",fontWeight:900}}>{num}</div>
      <h2 style={{fontSize:21,margin:"18px 0 10px"}}>{title}</h2>
      <p style={{margin:0,color:"#617087",lineHeight:1.6,fontSize:16}}>{text}</p>
    </article>)}
   </div>
  </section>

  <section style={{maxWidth:1180,margin:"0 auto",padding:"0 24px 52px"}}>
   <div style={{background:"#fff",border:"1px solid #e4e9f3",borderRadius:24,padding:28}}>
    <div style={{fontSize:13,fontWeight:800,letterSpacing:1,color:"#4b68ff"}}>АНАЛИТИКА РАССЫЛКИ · ПРИМЕР ИНТЕРФЕЙСА</div>
    <h2 style={{fontSize:32,margin:"10px 0 8px"}}>От отправки до ответа — в одной статистике</h2>
    <p style={{color:"#617087",lineHeight:1.6,margin:"0 0 22px"}}>BORIS показывает отправку, оценку доставки, жёсткие недоставки, примерную открываемость, технические загрузки изображения, повторные открытия, ответы и результат по каждой теме. Исторические письма без метки не смешиваются с новой статистикой.</p>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:14}}>
     {[["Отправлено","1 000"],["Доставлено","987"],["Жёсткие недоставки","13"],["С меткой","1 000 / 1 000"],["Вероятно открыли","38,4%"],["Ответили","4,8%"],["Повторно открыли","73"]].map(([k,v])=><div key={k} style={{padding:"18px",borderRadius:18,background:"#f6f8fc",border:"1px solid #e7ebf3"}}><span style={{display:"block",color:"#748096",fontSize:13}}>{k}</span><strong style={{display:"block",fontSize:25,marginTop:6}}>{v}</strong></div>)}
    </div>
    <p style={{fontSize:13,color:"#7b879a",lineHeight:1.5,margin:"18px 0 0"}}>Открытие — приблизительный сигнал: некоторые почтовые приложения загружают изображения автоматически. BORIS отделяет известные технические загрузки от более консервативной оценки вероятных человеческих открытий. Ответы и лиды фиксируются отдельно и считаются точнее.</p>
   </div>
  </section>

  <section style={{background:"#10182b",color:"#fff"}}>
   <div style={{maxWidth:1180,margin:"0 auto",padding:"42px 24px",display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))",gap:26}}>
    <div><div style={{fontSize:13,fontWeight:800,letterSpacing:1,color:"#91a3ff"}}>КАК ИДЁТ ОТПРАВКА</div><h2 style={{fontSize:30,margin:"10px 0"}}>Не с чужого сервиса — с почты клиента</h2><p style={{color:"#bac5d8",lineHeight:1.6}}>Получатель видит реальный корпоративный адрес клиента. Ответ приходит в тот же ящик и одновременно фиксируется BORIS для CRM и аналитики.</p></div>
    <div><div style={{fontSize:13,fontWeight:800,letterSpacing:1,color:"#91a3ff"}}>БЕЗОПАСНОСТЬ</div><h2 style={{fontSize:30,margin:"10px 0"}}>Основной пароль не нужен</h2><p style={{color:"#bac5d8",lineHeight:1.6}}>Для Mail.ru, Яндекса и Gmail используется отдельный пароль приложения или безопасная авторизация. Доступ можно отозвать на стороне почтового сервиса.</p></div>
    <div><div style={{fontSize:13,fontWeight:800,letterSpacing:1,color:"#91a3ff"}}>КОНТРОЛЬ</div><h2 style={{fontSize:30,margin:"10px 0"}}>Сначала черновик, потом запуск</h2><p style={{color:"#bac5d8",lineHeight:1.6}}>До отправки BORIS создаёт тестовый черновик в почте клиента. После проверки запускается кампания с дневным лимитом, стоп-листом и контролем ответов.</p></div>
   </div>
  </section>

  <section style={{maxWidth:1180,margin:"0 auto",padding:"46px 24px 70px"}}>
   <div style={{background:"#fff",borderRadius:24,padding:"30px",border:"1px solid #e4e9f3",display:"flex",justifyContent:"space-between",gap:20,alignItems:"center",flexWrap:"wrap"}}>
    <div><h2 style={{margin:"0 0 8px",fontSize:28}}>Боевая версия работает внутри кабинета BORIS</h2><p style={{margin:0,color:"#68758c"}}>Там подключается почта, создаются кампании, собирается аудитория, видны отправки и ответы.</p></div>
    <Link href="/login" style={{textDecoration:"none",background:"#3155e7",color:"#fff",padding:"14px 20px",borderRadius:14,fontWeight:800}}>Войти в BORIS</Link>
   </div>
  </section>
 </main>
}
