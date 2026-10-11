(() => {
  "use strict";

  const CENTER_SOURCE = "BORIS_CHECKPOINT_CENTER";
  const EXT_SOURCE = "BORIS_CHECKPOINT_EXTENSION";
  const HISTORY_KEY = "borisSeoCheckpointCompletedV2";
  const EXPECTED_EXTENSION_VERSION = "1.2.0";
  const CAPTCHA_TYPES = new Set([
    "captcha_required",
    "captcha_terms_registration_required",
    "captcha_age_and_terms_required",
    "captcha_age_declaration_required",
  ]);
  const palettes = [
    ["#2563eb","#dbeafe"],["#7c3aed","#ede9fe"],["#ea580c","#ffedd5"],["#059669","#d1fae5"],
    ["#db2777","#fce7f3"],["#0891b2","#cffafe"],["#4f46e5","#e0e7ff"],["#dc2626","#fee2e2"]
  ];

  const state = {
    data:null,
    extensionConnected:false,
    extensionVersion:"",
    statuses:{},
    serverResults:{},
    selected:new Set(),
    query:"",
    filter:"all",
    project:"all",
    sort:"priority",
    acked:{},
    captchaMode:false,
    captchaIndex:0,
    captchaBatchOffset:0,
    completed:loadHistory(),
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    uniquePlatforms:$("uniquePlatforms"),entryCount:$("entryCount"),topCount:$("topCount"),captchaCount:$("captchaCount"),captchaModeCount:$("captchaModeCount"),
    visibleCount:$("visibleCount"),totalCount:$("totalCount"),quickFilters:$("quickFilters"),projectSelect:$("projectSelect"),sortSelect:$("sortSelect"),
    searchInput:$("searchInput"),desktopRows:$("desktopRows"),mobileCards:$("mobileCards"),desktopQueue:$("desktopQueue"),
    loadingState:$("loadingState"),emptyState:$("emptyState"),errorBox:$("errorBox"),bulkBar:$("bulkBar"),selectedCount:$("selectedCount"),
    selectAllCheckbox:$("selectAllCheckbox"),headerExtension:$("headerExtension"),sidebarExtension:$("sidebarExtension"),
    headerExtensionTitle:$("headerExtensionTitle"),sidebarExtensionTitle:$("sidebarExtensionTitle"),summaryExtensionTitle:$("summaryExtensionTitle"),
    extensionDownloadText:$("extensionDownloadText"),mobileSidebar:$("mobileSidebar"),mobileOverlay:$("mobileOverlay"),
    toast:$("toast"),doneHumanCount:$("doneHumanCount"),humanTotalCount:$("humanTotalCount"),humanProgressBar:$("humanProgressBar"),
    captchaProgress:$("captchaProgress"),termsProgress:$("termsProgress"),companyProgress:$("companyProgress"),
    captchaWorkMode:$("captchaWorkMode"),workModePosition:$("workModePosition"),workModeCurrent:$("workModeCurrent"),
    topFiveCards:$("topFiveCards"),nextFivePanel:$("nextFivePanel"),batchDoneCount:$("batchDoneCount"),nextFiveList:$("nextFiveList"),
  };

  function loadHistory(){
    try{
      const raw=JSON.parse(localStorage.getItem(HISTORY_KEY)||"[]");
      return Array.isArray(raw)?raw.filter(x=>x&&x.id):[];
    }catch{return[]}
  }
  function saveHistory(){localStorage.setItem(HISTORY_KEY,JSON.stringify(state.completed.slice(-500)))}
  function rememberDone(entry,source="human"){
    if(!entry||state.completed.some(x=>x.id===entry.id))return;
    state.completed.push({
      id:entry.id,platform:entry.platform,name:entry.name,checkpoint:entry.checkpoint,
      target_site:entry.target_site||null,action_url:entry.action_url,completedAt:new Date().toISOString(),source
    });
    saveHistory();
  }
  function esc(value){return String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]))}
  function safeUrl(value){try{const u=new URL(String(value||""),location.origin);return /^https?:$/.test(u.protocol)?u.href:"#"}catch{return"#"}}
  function displayUrl(value){try{return new URL(value).href}catch{return String(value||"")}}
  function checkpointLabel(value){
    const labels={
      captcha_required:"CAPTCHA",math_antibot_required:"Anti-bot проверка",terms_acceptance_required:"Принятие условий",
      registration_agreement_required:"Правила регистрации",captcha_terms_registration_required:"CAPTCHA + условия",
      captcha_age_and_terms_required:"CAPTCHA + возраст + условия",captcha_age_declaration_required:"CAPTCHA + возраст",
      truthful_company_identity_required:"Реальные данные компании",business_email_required:"Корпоративная почта",
      external_account_sso_required:"Внешний аккаунт / SSO",email_verification_required:"Подтверждение email",
      sms_or_verification_code_required:"Код подтверждения"
    };
    return labels[value]||value||"Ручное подтверждение";
  }
  function actionLabel(entry){
    const t=String(entry.checkpoint||"");
    if(t==="captcha_terms_registration_required"||t==="captcha_age_and_terms_required")return"Открыть CAPTCHA и условия";
    if(t==="captcha_required"||t==="captcha_age_declaration_required")return"Открыть CAPTCHA";
    if(t==="terms_acceptance_required"||t==="registration_agreement_required")return"Открыть условия";
    if(t==="truthful_company_identity_required")return"Открыть форму компании";
    if(t==="email_verification_required"||t==="business_email_required")return"Подтвердить email";
    if(t==="sms_or_verification_code_required")return"Ввести код";
    if(t==="math_antibot_required")return"Открыть проверку";
    if(t==="external_account_sso_required")return"Открыть вход";
    return"Открыть";
  }
  function humanActionText(entry){
    const t=String(entry.checkpoint||"");
    if(CAPTCHA_TYPES.has(t))return"ТВОЁ ДЕЙСТВИЕ — пройти CAPTCHA и отправить форму";
    if(t.includes("terms")||t==="registration_agreement_required")return"ТВОЁ ДЕЙСТВИЕ — проверить и принять условия, если согласен";
    if(t==="truthful_company_identity_required")return"ТВОЁ ДЕЙСТВИЕ — проверить реальные данные компании";
    if(t==="email_verification_required"||t==="business_email_required")return"ТВОЁ ДЕЙСТВИЕ — подтвердить email";
    if(t==="sms_or_verification_code_required")return"ТВОЁ ДЕЙСТВИЕ — ввести код подтверждения";
    if(t==="math_antibot_required")return"ТВОЁ ДЕЙСТВИЕ — пройти anti-bot проверку";
    return"ТВОЁ ДЕЙСТВИЕ — завершить ручное подтверждение";
  }
  function isCaptcha(entry){return CAPTCHA_TYPES.has(String(entry.checkpoint||""))}
  function isTerms(entry){const t=String(entry.checkpoint||"");return t.includes("terms")||t==="registration_agreement_required"||t==="terms_acceptance_required"}
  function isCompany(entry){return String(entry.checkpoint||"")==="truthful_company_identity_required"}
  function tierLabel(value){return value==="high"?"HIGH":value==="medium"?"MEDIUM":value==="low"?"LOW":"UNKNOWN"}
  function hashIndex(value,length){let h=0;for(const ch of String(value||""))h=(h*31+ch.charCodeAt(0))>>>0;return h%length}
  function initials(value){const parts=String(value||"").replace(/[^\p{L}\p{N}]+/gu," ").trim().split(/\s+/).filter(Boolean);if(!parts.length)return"B";if(parts.length===1)return parts[0].slice(0,2).toUpperCase();return(parts[0][0]+parts[1][0]).toUpperCase()}
  function projectsFor(entry){return entry.target_site?[entry.target_site]:(entry.target_sites||[])}
  function projectName(value){if(value==="boris-ai.pro")return"BORIS";if(value==="systems-ai-oko.ru")return"SYSTEMS";if(value==="avito-specialist.ru")return"AVITO";return String(value||"")}
  function projectSummary(entry){const p=projectsFor(entry);if(!p.length)return"1 действие";return entry.flow_kind==="account_onboarding"?`Открывает: ${p.map(projectName).join(" + ")}`:`Для: ${p.map(projectName).join(" + ")}`}
  function priorityScore(entry){return(entry.top_5_platform?10000:0)+Number(entry.human_effort_score||0)*10+Number(entry.iks||0)}
  function priorityMeta(entry){const s=Number(entry.human_effort_score||0);if(entry.top_5_platform||s>=600)return{label:"Высокий",bars:3,color:"#ef4444"};if(s>=350)return{label:"Средний",bars:2,color:"#f59e0b"};return{label:"Низкий",bars:1,color:"#94a3b8"}}

  function queueStatus(entry){
    const ext=state.statuses[entry.id]||{}, result=state.serverResults[entry.id]||{};
    if(result.error)return{tone:"error",label:"ОШИБКА",detail:"Нужна повторная проверка"};
    const v=String(result?.verification?.status||"").toLowerCase();
    if(v==="verified")return{tone:"verified",label:"VERIFIED",detail:"Ссылка подтверждена"};
    if(["live","published_unverified"].includes(v))return{tone:"live",label:"LIVE",detail:"Публикация найдена"};
    if(["ready","warming"].includes(v))return{tone:"ready",label:"ГОТОВО",detail:"BORIS продолжает сам"};
    if(v==="submitted_pending_verification")return{tone:"checking",label:"ПРОВЕРЯЕМ",detail:"Ждём проверку / модерацию"};
    if(ext.state==="submitted"||ext.state==="attempted")return{tone:"checking",label:"ПРОВЕРЯЕМ",detail:"BORIS проверяет результат"};
    if(ext.state==="filled")return{tone:"filled",label:"ЗАПОЛНЕНО",detail:"Остался ручной шаг"};
    if(ext.state==="opened")return{tone:"opened",label:"ОТКРЫТО",detail:"Вкладка открыта"};
    if(isCaptcha(entry))return{tone:"captcha",label:"CAPTCHA",detail:"Ожидает проверки"};
    return{tone:"action",label:"ПОДТВЕРЖДЕНИЕ",detail:"Нужно действие"};
  }

  async function apiFetch(url,options={}){
    const token=localStorage.getItem("boris_token")||"",headers=new Headers(options.headers||{});
    if(token)headers.set("Authorization","Bearer "+token);
    const response=await fetch(url,{...options,headers,cache:"no-store"});
    if(response.status===401){location.href="/login";throw new Error("Требуется авторизация")}
    return response;
  }
  function setError(message){if(!message){els.errorBox.hidden=true;els.errorBox.textContent="";return}els.errorBox.hidden=false;els.errorBox.textContent=message}
  function showToast(message){
    els.toast.textContent=message;els.toast.hidden=false;clearTimeout(showToast.timer);
    showToast.timer=setTimeout(()=>{els.toast.hidden=true},1900);
  }
  async function copyText(text){try{await navigator.clipboard.writeText(text);showToast("Ссылка скопирована")}catch{showToast("Не удалось скопировать")}}

  async function loadData(){
    els.loadingState.hidden=false;setError("");
    try{
      const response=await apiFetch("/seo-checkpoints-data"),payload=await response.json();
      if(!response.ok)throw new Error(payload?.detail||"Не удалось загрузить очередь");
      state.data=payload;
      state.selected=new Set([...state.selected].filter(id=>(payload.entries||[]).some(x=>x.id===id)));
      const max=Math.max(0,captchaEntries().length-1);state.captchaIndex=Math.min(state.captchaIndex,max);
      render();window.postMessage({source:CENTER_SOURCE,type:"SYNC_CONFIG"},"*");
    }catch(error){setError(error instanceof Error?error.message:String(error))}
    finally{els.loadingState.hidden=true}
  }

  function entries(){return state.data?.entries||[]}
  function captchaEntries(){return entries().filter(isCaptcha).sort((a,b)=>priorityScore(b)-priorityScore(a))}
  function topFiveGroups(){
    const groups=new Map();
    for(const e of entries()){
      if(!e.top_5_platform)continue;
      const current=groups.get(e.platform)||{platform:e.platform,entry:e,entries:[],projects:new Set(),score:priorityScore(e)};
      current.entries.push(e);current.score=Math.max(current.score,priorityScore(e));
      for(const p of projectsFor(e))current.projects.add(p);
      if(priorityScore(e)>priorityScore(current.entry))current.entry=e;
      groups.set(e.platform,current);
    }
    return [...groups.values()].sort((a,b)=>b.score-a.score).slice(0,5);
  }
  function topFiveEntries(){return topFiveGroups().map(g=>g.entry)}
  function topFiveOpenEntries(){return topFiveEntries()}
  function visibleEntries(){
    const q=state.query.trim().toLocaleLowerCase("ru"),rows=entries().filter(entry=>{
      const status=queueStatus(entry),projects=projectsFor(entry);
      if(state.filter==="top"&&!entry.top_5_platform)return false;
      if(state.filter==="captcha"&&!isCaptcha(entry))return false;
      if(state.filter==="ready"&&!["ready","live","verified"].includes(status.tone))return false;
      if(state.filter==="errors"&&status.tone!=="error")return false;
      if(state.project!=="all"&&!projects.includes(state.project))return false;
      if(!q)return true;
      return [entry.name,entry.host,entry.platform,entry.action,entry.action_url,checkpointLabel(entry.checkpoint),...projects].filter(Boolean).join(" ").toLocaleLowerCase("ru").includes(q);
    });
    rows.sort((a,b)=>state.sort==="iks"?Number(b.iks||0)-Number(a.iks||0):state.sort==="name"?String(a.name||"").localeCompare(String(b.name||""),"ru"):priorityScore(b)-priorityScore(a));
    return rows;
  }
  function readyCount(){return entries().filter(x=>["ready","live","verified"].includes(queueStatus(x).tone)).length}
  function errorCount(){return entries().filter(x=>queueStatus(x).tone==="error").length}

  function pill(text,tone=""){return`<span class="pill ${tone}">${esc(text)}</span>`}
  function platformMark(entry){const[fg,bg]=palettes[hashIndex(entry.platform||entry.name,palettes.length)];return`<span class="platform-mark" style="color:${fg};background:${bg}">${esc(initials(entry.name))}</span>`}
  function priorityHtml(entry){const p=priorityMeta(entry);return`<div class="priority"><span class="priority-bars">${[1,2,3].map(i=>`<i style="${i<=p.bars?`background:${p.color}`:""}"></i>`).join("")}</span><span>${p.label}</span></div>`}
  function statusHtml(entry){const s=queueStatus(entry);return`<div class="status-cell"><span class="status-badge ${s.tone}"><i></i>${esc(s.label)}</span><div class="status-detail">${esc(s.detail)}</div></div>`}
  function projectsHtml(entry){
    const projects=projectsFor(entry),out=[];
    if(entry.top_5_platform)out.push(pill("ТОП","blue"));if(isCaptcha(entry))out.push(pill("CAPTCHA","amber"));
    out.push(pill(`${tierLabel(entry.iks_tier)}${entry.iks?` · ИКС ${entry.iks}`:""}`,entry.iks_tier==="high"?"violet":entry.iks_tier==="medium"?"blue":""));
    projects.slice(0,1).forEach(p=>out.push(pill(projectName(p))));if(projects.length>1)out.push(pill("+"+(projects.length-1)));
    if(entry.flow_kind==="account_onboarding")out.push(pill("1 аккаунт","green"));return out.join("");
  }
  function urlHtml(entry,compact=false){
    const url=displayUrl(entry.action_url);
    return`<div class="url-cell"><a class="action-url ${compact?"compact":""}" href="${esc(safeUrl(url))}" target="_blank" rel="noreferrer" title="${esc(url)}">${esc(url)}</a><button class="copy-url-button" data-copy-url="${esc(url)}" type="button" title="Копировать ссылку">⧉</button></div>`;
  }
  function actionButtonHtml(entry){return`<button class="open-button entry-open" data-entry-id="${esc(entry.id)}" type="button">↗ ${esc(actionLabel(entry))}</button>`}

  function rowHtml(entry,index){
    const checked=state.selected.has(entry.id)?"checked":"",selected=checked?" selected":"";
    return`<div class="row-grid${selected}" data-entry-id="${esc(entry.id)}">
      <label class="check-cell"><input class="entry-check" type="checkbox" data-entry-id="${esc(entry.id)}" ${checked}></label>
      <div class="num-cell">${index+1}</div>
      <div class="platform-cell">${platformMark(entry)}<div class="platform-text"><strong>${esc(entry.name)}</strong><small>${esc(entry.host)}</small></div></div>
      <div class="task-cell"><strong>${esc(checkpointLabel(entry.checkpoint))}</strong><small>${esc(entry.action||humanActionText(entry))}</small></div>
      <div class="pills">${projectsHtml(entry)}</div>
      ${urlHtml(entry,true)}
      ${statusHtml(entry)}
      ${priorityHtml(entry)}
      ${actionButtonHtml(entry)}
      <a class="more-link" href="${esc(safeUrl(entry.action_url))}" target="_blank" rel="noreferrer">⋮</a>
    </div>`;
  }
  function cardHtml(entry,index){
    const checked=state.selected.has(entry.id)?"checked":"",selected=checked?" selected":"";
    return`<article class="mobile-card${selected}">
      <div class="mobile-card-top"><input class="entry-check" type="checkbox" data-entry-id="${esc(entry.id)}" ${checked}>${platformMark(entry)}
        <div class="mobile-card-main"><div class="mobile-card-name"><small>#${index+1}</small><b>${esc(entry.name)}</b></div><div class="mobile-host">${esc(entry.host)}</div></div>${statusHtml(entry)}
      </div>
      <div class="mobile-task"><strong>${esc(checkpointLabel(entry.checkpoint))}</strong><p>${esc(humanActionText(entry))}</p></div>
      <div class="pills" style="margin-top:10px">${projectsHtml(entry)}</div>
      <div class="mobile-url">${urlHtml(entry)}</div>
      <div class="mobile-bottom">${priorityHtml(entry)}<div class="mobile-actions">${actionButtonHtml(entry)}<button class="mobile-copy copy-url-button" data-copy-url="${esc(displayUrl(entry.action_url))}" type="button">⧉ Копировать</button></div></div>
    </article>`;
  }

  function renderTopFive(){
    els.topFiveCards.innerHTML=topFiveGroups().map((g,i)=>{
      const e=g.entry,projects=[...g.projects],tabs=g.entries.length;
      return`<article class="top-five-card">
        <div class="top-five-number">${i+1}</div>
        <div class="top-five-main"><strong>${esc(e.name)}</strong><small>${esc(checkpointLabel(e.checkpoint))} · Открывает: ${esc(projects.map(projectName).join(" + ")||"1 действие")}${tabs>1?` · ${tabs} вкладки`:""}</small>
        <a href="${esc(safeUrl(e.action_url))}" target="_blank" rel="noreferrer">${esc(displayUrl(e.action_url))}</a></div>
        <div class="top-five-actions"><button class="open-button top-group-open" data-platform="${esc(g.platform)}" type="button">↗ ${esc(actionLabel(e))}</button><button class="copy-url-button text-copy" data-copy-url="${esc(displayUrl(e.action_url))}" type="button">⧉ Копировать</button></div>
      </article>`;
    }).join("");
  }
  function completedByType(predicate){return state.completed.filter(x=>predicate(x)).length}
  function totalWithHistory(predicate){const active=entries().filter(predicate).length,done=completedByType(predicate);return active+done}
  function renderProgress(){
    const activePlatforms=new Set(entries().map(x=>x.platform));
    const completedPlatforms=new Set(state.completed.map(x=>x.platform));
    const donePlatforms=[...completedPlatforms].filter(p=>!activePlatforms.has(p)).length;
    const totalPlatforms=new Set([...activePlatforms,...completedPlatforms]).size;
    const pct=totalPlatforms?Math.min(100,Math.round(donePlatforms/totalPlatforms*100)):0;
    els.doneHumanCount.textContent=String(donePlatforms);els.humanTotalCount.textContent=String(totalPlatforms);els.humanProgressBar.style.width=pct+"%";
    const cDone=completedByType(x=>CAPTCHA_TYPES.has(String(x.checkpoint||""))),cTotal=totalWithHistory(isCaptcha);
    const tDone=completedByType(x=>String(x.checkpoint||"").includes("terms")||String(x.checkpoint||"")==="terms_acceptance_required"),tTotal=totalWithHistory(isTerms);
    const coDone=completedByType(x=>String(x.checkpoint||"")==="truthful_company_identity_required"),coTotal=totalWithHistory(isCompany);
    els.captchaProgress.textContent=`${cDone} / ${cTotal}`;els.termsProgress.textContent=`${tDone} / ${tTotal}`;els.companyProgress.textContent=`${coDone} / ${coTotal}`;
  }
  function renderNextFive(){
    const list=captchaEntries().slice(state.captchaBatchOffset,state.captchaBatchOffset+5);
    els.nextFivePanel.hidden=state.completed.length===0;
    els.batchDoneCount.textContent=String(state.completed.length);
    els.nextFiveList.innerHTML=list.map((e,i)=>`<li><span>${i+1}. ${esc(e.name)}${e.target_site?` — ${esc(projectName(e.target_site))}`:""}</span><small>${esc(checkpointLabel(e.checkpoint))}</small></li>`).join("");
  }
  function currentCaptcha(){const rows=captchaEntries();return rows.length?rows[Math.min(state.captchaIndex,rows.length-1)]:null}
  function renderWorkMode(){
    els.captchaWorkMode.hidden=!state.captchaMode;if(!state.captchaMode)return;
    const rows=captchaEntries(),e=currentCaptcha();if(!e){els.workModeCurrent.innerHTML="<strong>CAPTCHA-задач больше нет.</strong>";return}
    els.workModePosition.textContent=`${state.captchaIndex+1} из ${rows.length}`;
    els.workModeCurrent.innerHTML=`<div class="work-platform">${platformMark(e)}<div><strong>${esc(e.name)}</strong><small>${esc(projectSummary(e))}</small></div></div>
      <div class="work-meta">${pill("CAPTCHA","amber")}${pill(tierLabel(e.iks_tier),e.iks_tier==="high"?"violet":"blue")}${e.iks?pill("ИКС "+e.iks):""}</div>
      <a class="work-url" href="${esc(safeUrl(e.action_url))}" target="_blank" rel="noreferrer">${esc(displayUrl(e.action_url))}</a>
      <p>${esc(humanActionText(e))}</p>`;
    $("workOpenButton").textContent=actionLabel(e);
  }
  function renderFilters(){
    const items=[["all","Все",entries().length,"all"],["top","ТОП",topFiveEntries().length,"top"],["captcha","CAPTCHA",captchaEntries().length,"captcha"],["ready","Готовые",readyCount(),"ready"],["errors","Ошибки",errorCount(),"errors"]];
    els.quickFilters.innerHTML=items.map(([key,label,count,tone])=>`<button class="filter-chip ${state.filter===key?`active-${tone}`:""}" type="button" data-filter="${key}">${label} (${count})</button>`).join("");
  }
  function renderProjectOptions(){
    const set=new Set();for(const e of entries())for(const p of projectsFor(e))set.add(p);const current=state.project;
    els.projectSelect.innerHTML=`<option value="all">Все проекты</option>`+[...set].sort().map(p=>`<option value="${esc(p)}">${esc(projectName(p))}</option>`).join("");
    els.projectSelect.value=[...set,"all"].includes(current)?current:"all";state.project=els.projectSelect.value;
  }
  function renderExtension(){
    const c=state.extensionConnected;
    const current=String(state.extensionVersion||"");
    const upToDate=c&&current===EXPECTED_EXTENSION_VERSION;
    els.headerExtension.classList.toggle("connected",upToDate);
    els.sidebarExtension.classList.toggle("connected",upToDate);
    if(!c){
      els.headerExtensionTitle.textContent="Расширение не найдено";
      els.sidebarExtensionTitle.textContent="Нужно расширение";
      els.summaryExtensionTitle.textContent="Не найдено";
      els.extensionDownloadText.textContent="Скачать расширение";
    }else if(!upToDate){
      els.headerExtensionTitle.textContent=`Обновить расширение · v${current||"старое"}`;
      els.sidebarExtensionTitle.textContent=`Нужно обновить до v${EXPECTED_EXTENSION_VERSION}`;
      els.summaryExtensionTitle.textContent="Обновить";
      els.extensionDownloadText.textContent=`Скачать v${EXPECTED_EXTENSION_VERSION}`;
    }else{
      els.headerExtensionTitle.textContent=`Расширение подключено · v${current}`;
      els.sidebarExtensionTitle.textContent=`Расширение v${current} установлено`;
      els.summaryExtensionTitle.textContent="Подключено";
      els.extensionDownloadText.textContent="Скачать ещё раз";
    }
    document.querySelector(".extension-stat")?.classList.toggle("connected",upToDate);
  }
  function renderBulk(rows){els.bulkBar.hidden=state.selected.size===0;els.selectedCount.textContent=String(state.selected.size);els.selectAllCheckbox.checked=rows.length>0&&rows.every(x=>state.selected.has(x.id))}
  function render(){
    const rows=visibleEntries();els.uniquePlatforms.textContent=String(state.data?.summary?.unique_platforms||0);els.entryCount.textContent=String(state.data?.summary?.entries||entries().length);
    els.topCount.textContent=String(topFiveEntries().length);els.captchaCount.textContent=String(captchaEntries().length);els.captchaModeCount.textContent=String(captchaEntries().length);
    els.visibleCount.textContent=String(rows.length);els.totalCount.textContent=String(entries().length);
    renderFilters();renderProjectOptions();renderExtension();renderBulk(rows);renderTopFive();renderProgress();renderNextFive();renderWorkMode();
    els.emptyState.hidden=rows.length!==0;els.desktopQueue.hidden=rows.length===0;els.mobileCards.hidden=rows.length===0;els.desktopRows.innerHTML=rows.map(rowHtml).join("");els.mobileCards.innerHTML=rows.map(cardHtml).join("");
  }

  function openEntries(selected){
    if(!selected.length)return;
    const openedAt=new Date().toISOString();
    selected.forEach(e=>{
      state.statuses[e.id]={...(state.statuses[e.id]||{}),state:"opened",openedAt,pageUrl:e.action_url};
    });
    render();
    if(state.extensionConnected){
      window.postMessage({source:CENTER_SOURCE,type:"OPEN_ENTRIES",entryIds:selected.map(x=>x.id)},"*");
      return;
    }
    selected.slice(0,12).forEach(e=>window.open(e.action_url,"_blank","noopener,noreferrer"));
  }
  function openCaptchaBatch(offset){const rows=captchaEntries();if(!rows.length)return;state.captchaBatchOffset=Math.max(0,Math.min(offset,Math.max(0,rows.length-1)));const batch=rows.slice(state.captchaBatchOffset,state.captchaBatchOffset+5);openEntries(batch);showToast(`Открыто: ${batch.length} CAPTCHA`);renderNextFive()}
  function byId(id){return entries().find(x=>x.id===id)}
  async function completeEntry(entry,source="human"){
    try{
      const response=await apiFetch("/seo-checkpoints-complete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({entry_id:entry.id,platform:entry.platform,target_site:entry.target_site,page_url:(state.statuses[entry.id]||{}).pageUrl||entry.action_url})});
      const payload=await response.json();if(!response.ok)throw new Error(payload?.detail||"Ошибка проверки");
      state.serverResults[entry.id]=payload;rememberDone(entry,source);render();showToast("Передано BORIS — продолжение запущено");setTimeout(loadData,1200);return payload;
    }catch(error){state.serverResults[entry.id]={error:error instanceof Error?error.message:String(error)};render();showToast("BORIS не подтвердил выполнение");return null}
  }
  async function processCompletionStatuses(){
    if(!state.data)return;
    for(const [entryId,status] of Object.entries(state.statuses)){
      if(status?.state!=="submitted"||!status.submittedAt)continue;const sig=`${status.submittedAt}|${status.pageUrl||""}`;if(state.acked[entryId]===sig)continue;
      const entry=byId(entryId);if(!entry)continue;state.acked[entryId]=sig;const payload=await completeEntry(entry,"extension");
      if(payload)window.postMessage({source:CENTER_SOURCE,type:"ACK_ENTRY",entryId,submittedAt:status.submittedAt},"*");
    }
  }

  window.addEventListener("message",event=>{
    if(event.source!==window)return;const msg=event.data;if(!msg||msg.source!==EXT_SOURCE)return;
    if(msg.type==="READY"){
      state.extensionConnected=true;
      if(msg.extensionVersion)state.extensionVersion=String(msg.extensionVersion);
      renderExtension();
    }
    if(msg.type==="STATUS"){state.extensionConnected=true;state.statuses=msg.statuses||{};render();void processCompletionStatuses()}
    if(msg.type==="SYNC_ERROR"){state.extensionConnected=true;renderExtension();setError(`Расширение подключено, но не получило owner-конфигурацию: ${msg.error||"unknown"}`)}
  });

  document.addEventListener("click",event=>{
    const target=event.target.closest("[data-filter],.entry-open,.top-group-open,.entry-check,.copy-url-button");
    if(!target)return;
    if(target.dataset.filter){state.filter=target.dataset.filter;render();return}
    if(target.classList.contains("entry-open")){const e=byId(target.dataset.entryId);if(e)openEntries([e]);return}
    if(target.classList.contains("top-group-open")){const g=topFiveGroups().find(x=>x.platform===target.dataset.platform);if(g)openEntries(g.entries);return}
    if(target.classList.contains("copy-url-button")){void copyText(target.dataset.copyUrl||"");return}
    if(target.classList.contains("entry-check")){const id=target.dataset.entryId;if(target.checked)state.selected.add(id);else state.selected.delete(id);render()}
  });

  $("startCaptchaModeButton").addEventListener("click",()=>{state.captchaMode=true;state.captchaIndex=0;renderWorkMode();els.captchaWorkMode.scrollIntoView({behavior:"smooth",block:"center"})});
  $("closeCaptchaModeButton").addEventListener("click",()=>{state.captchaMode=false;renderWorkMode()});
  $("openCaptchaButton").addEventListener("click",()=>{state.filter="captcha";render();document.querySelector(".queue-shell")?.scrollIntoView({behavior:"smooth"})});
  $("openTopButton").addEventListener("click",()=>openEntries(topFiveOpenEntries()));
  $("topFiveOpenAllButton").addEventListener("click",()=>openEntries(topFiveOpenEntries()));
  $("openFirstFiveButton").addEventListener("click",()=>openCaptchaBatch(0));
  $("openNextFiveButton").addEventListener("click",()=>{const rows=captchaEntries();let next=state.captchaBatchOffset+5;if(next>=rows.length)next=0;openCaptchaBatch(next)});
  $("openAllCaptchaButton").addEventListener("click",()=>{const rows=captchaEntries();if(rows.length>10&&!confirm(`Открыть все ${rows.length} CAPTCHA-вкладок? Рекомендуется проходить по 5.`))return;openEntries(rows)});
  $("refreshButton").addEventListener("click",()=>void loadData());
  $("openSelectedButton").addEventListener("click",()=>openEntries(entries().filter(x=>state.selected.has(x.id))));
  $("clearSelectedButton").addEventListener("click",()=>{state.selected.clear();render()});
  $("selectAllCheckbox").addEventListener("change",event=>{for(const e of visibleEntries()){if(event.target.checked)state.selected.add(e.id);else state.selected.delete(e.id)}render()});
  $("resetFiltersButton").addEventListener("click",()=>{state.query="";state.filter="all";state.project="all";els.searchInput.value="";render()});
  els.searchInput.addEventListener("input",event=>{state.query=event.target.value;render()});els.projectSelect.addEventListener("change",event=>{state.project=event.target.value;render()});els.sortSelect.addEventListener("change",event=>{state.sort=event.target.value;render()});

  $("workOpenButton").addEventListener("click",()=>{const e=currentCaptcha();if(e)openEntries([e])});
  $("workCopyButton").addEventListener("click",()=>{const e=currentCaptcha();if(e)void copyText(displayUrl(e.action_url))});
  $("workSkipButton").addEventListener("click",()=>{const rows=captchaEntries();if(rows.length){state.captchaIndex=(state.captchaIndex+1)%rows.length;renderWorkMode()}});
  $("workDoneButton").addEventListener("click",async()=>{const e=currentCaptcha();if(!e)return;await completeEntry(e,"manual");const rows=captchaEntries();state.captchaIndex=Math.min(state.captchaIndex,Math.max(0,rows.length-1));renderWorkMode()});
  $("workOpenFiveButton").addEventListener("click",()=>openCaptchaBatch(Math.floor(state.captchaIndex/5)*5));
  $("workNextFiveButton").addEventListener("click",()=>{const rows=captchaEntries();if(!rows.length)return;state.captchaIndex=Math.min(rows.length-1,(Math.floor(state.captchaIndex/5)+1)*5);openCaptchaBatch(state.captchaIndex);renderWorkMode()});
  $("nextFiveOpenButton").addEventListener("click",()=>openCaptchaBatch(state.captchaBatchOffset));
  $("mobileMenuButton").addEventListener("click",()=>{els.mobileSidebar.classList.add("open");els.mobileOverlay.hidden=false;els.mobileOverlay.classList.add("show");els.mobileSidebar.setAttribute("aria-hidden","false")});
  const closeMenu=()=>{els.mobileSidebar.classList.remove("open");els.mobileOverlay.classList.remove("show");els.mobileOverlay.hidden=true;els.mobileSidebar.setAttribute("aria-hidden","true")};$("mobileCloseButton").addEventListener("click",closeMenu);els.mobileOverlay.addEventListener("click",closeMenu);
  window.addEventListener("keydown",event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){event.preventDefault();els.searchInput.focus()}});

  window.postMessage({source:CENTER_SOURCE,type:"PING"},"*");setInterval(()=>window.postMessage({source:CENTER_SOURCE,type:"POLL_STATUS"},"*"),2500);void loadData();
})();
