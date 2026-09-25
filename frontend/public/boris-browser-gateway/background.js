const POLL_MINUTES = 0.5;
const GATEWAY_PROTOCOL_VERSION = 1;
const GATEWAY_FEATURES = [
  'recipe_runtime_v1','inspect_form','fill_semantic','check_semantic','submit_semantic',
  'assert_value','wait_page_state','extract_links','human_checkpoint','single_automation_window',
  'heartbeat_keepalive','token_rotation','local_credentials','privacy_screenshot',
  'record_tab_v2','record_session_once','record_marker','server_video_artifact','demo_pause','highlight_element'
];
let running = false;
let lastHeartbeat = 0;

function armGateway(){
  chrome.alarms.create('boris-gateway-poll', { delayInMinutes: 0.1, periodInMinutes: POLL_MINUTES });
  tick();
}
chrome.runtime.onInstalled.addListener(armGateway);
chrome.runtime.onStartup.addListener(armGateway);
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'boris-gateway-poll') tick(); });
chrome.runtime.onMessage.addListener((m,_sender,sendResponse) => {
  if(m?.target==='boris_offscreen_recorder') return;
  if(m?.type==='gateway_wake'){ tick(); return; }
  if(['recording_enable','recording_disable','recording_status'].includes(String(m?.type||''))){
    (async()=>{
      try{
        if(m.type==='recording_enable') sendResponse(await enableVideoModeFromUser(m.options||{}));
        else if(m.type==='recording_disable') sendResponse(await disableVideoMode());
        else sendResponse(await videoModeStatus());
      }catch(e){ sendResponse({ok:false,error:String(e?.message||e)}); }
    })();
    return true;
  }
});
chrome.tabs.onActivated.addListener(() => tick());
chrome.windows.onFocusChanged.addListener(() => tick());

async function cfg(){ return await chrome.storage.local.get(['server','token']); }
async function call(path, init={}){
  const c=await cfg(); if(!c.server || !c.token) throw new Error('Gateway not paired');
  const headers={...(init.headers||{}),Authorization:'Bearer '+c.token};
  if(init.body && !headers['Content-Type']) headers['Content-Type']='application/json';
  const r=await fetch(c.server+path,{...init,headers});
  const d=await r.json().catch(()=>({})); if(!r.ok) throw new Error(d.detail || `HTTP ${r.status}`); return d;
}
async function detectServices(){
  const tabs=await chrome.tabs.query({});
  const services={avito:false,yandex:false,google:false,gmail:false,youtube:false,vk:false,telegram:false,mailru:false};
  for(const t of tabs){
    let h=''; try{ h=new URL(t.url||'').hostname.toLowerCase(); }catch{}
    if(h==='avito.ru'||h.endsWith('.avito.ru')) services.avito=true;
    if(h.endsWith('.yandex.ru')) services.yandex=true;
    if(h==='accounts.google.com'||h==='myaccount.google.com') services.google=true;
    if(h==='mail.google.com') services.gmail=true;
    if(h==='studio.youtube.com'||h==='youtube.com'||h==='www.youtube.com') services.youtube=true;
    if(h==='vk.com'||h.endsWith('.vk.com')) services.vk=true;
    if(h==='web.telegram.org') services.telegram=true;
    if(h==='e.mail.ru'||h==='account.mail.ru'||h.endsWith('.mail.ru')) services.mailru=true;
  }
  return services;
}
async function heartbeat(state='idle', currentUrl='', loggedIn=null, currentCapability=null){
  try{
    const services=await detectServices();
    const hb=await call('/api/browser-gateway/transport/heartbeat',{method:'POST',body:JSON.stringify({state,current_url:currentUrl,services,current_capability:currentCapability,extension_version:chrome.runtime.getManifest().version,protocol_version:GATEWAY_PROTOCOL_VERSION,features:GATEWAY_FEATURES})});
    if(hb?.rotate_required){
      const rotated=await call('/api/browser-gateway/transport/rotate-token',{method:'POST'});
      if(rotated?.token) await chrome.storage.local.set({token:rotated.token});
    }
    // Keep legacy Avito readiness compatible with the existing mass-editor UI.
    await call('/api/avito/browser-gateway/heartbeat',{method:'POST',body:JSON.stringify({state,current_url:currentUrl,avito_logged_in:loggedIn})});
    lastHeartbeat=Date.now();
  }catch{}
}
async function progress(job,event,itemId=null,payload={}){
  return await call('/api/avito/browser-gateway/progress',{method:'POST',body:JSON.stringify({job_id:job.job_id,account_id:job.account_id,item_id:itemId,event,payload})});
}
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));

async function installCrowdKeepalive(tabId){
  if(!tabId) return false;
  try{
    const rows=await chrome.scripting.executeScript({
      target:{tabId},
      func:()=>{
        try{
          if(globalThis.__BORIS_GATEWAY_KEEPALIVE_TIMER){
            clearInterval(globalThis.__BORIS_GATEWAY_KEEPALIVE_TIMER);
          }
          const wake=()=>{ try{ chrome.runtime.sendMessage({type:'gateway_wake',source:'crowd_tab_keepalive'}); }catch{} };
          wake();
          globalThis.__BORIS_GATEWAY_KEEPALIVE_TIMER=setInterval(wake,12000);
          return {ok:true};
        }catch(e){ return {ok:false,error:String(e?.message||e)}; }
      },
    });
    return rows?.[0]?.result?.ok===true;
  }catch{
    return false;
  }
}

async function tick(){
  if(running) return;
  const c=await cfg(); if(!c.server || !c.token) return;
  running=true;
  try{
    if(Date.now()-lastHeartbeat>20000) await heartbeat('idle');
    const d=await call('/api/browser-gateway/transport/next-job');
    if(d.status==='job' && d.job) await executeJob(d.job);
  }catch(e){ await heartbeat('error','',null); }
  finally{
    running=false;
    // Fast Crowd conveyor while the service worker is alive; the alarm remains
    // the durable fallback if Chrome suspends the worker.
    try{ setTimeout(()=>tick(),1200); }catch{}
    try{ chrome.alarms.create('boris-gateway-poll',{delayInMinutes:0.1,periodInMinutes:POLL_MINUTES}); }catch{}
  }
}


let borisAutomationWindowId=null;
let borisAutomationTabId=null;
async function rememberAutomationTab(tab){
  if(!tab?.id) return;
  borisAutomationTabId=tab.id;
  borisAutomationWindowId=tab.windowId||null;
  await chrome.storage.local.set({crowd_automation_window:{
    tab_id:Number(tab.id),
    window_id:Number(tab.windowId||0),
    saved_at:new Date().toISOString(),
  }});
}
async function recoverAutomationTab(){
  const got=await chrome.storage.local.get(['crowd_automation_window']);
  const row=got?.crowd_automation_window||null;
  if(!row) return null;
  const tab=Number(row.tab_id)?await chrome.tabs.get(Number(row.tab_id)).catch(()=>null):null;
  if(tab?.id){ await rememberAutomationTab(tab); return tab; }
  if(Number(row.window_id)){
    const w=await chrome.windows.get(Number(row.window_id),{populate:true}).catch(()=>null);
    const t=(w?.tabs||[])[0]||null;
    if(t?.id){ await rememberAutomationTab(t); return t; }
  }
  return null;
}
async function ensureAutomationTab(url='about:blank',humanHandoff=false){
  try{
    const desiredState=humanHandoff?'normal':'minimized';
    const desiredFocus=!!humanHandoff;
    if(!borisAutomationTabId && !borisAutomationWindowId){
      const recovered=await recoverAutomationTab();
      if(recovered?.id){
        if(url && recovered.url!==url) await chrome.tabs.update(recovered.id,{url,active:true});
        if(recovered.windowId) try{ await chrome.windows.update(recovered.windowId,{focused:desiredFocus,state:desiredState}); }catch{}
        const live=await chrome.tabs.get(recovered.id);
        await rememberAutomationTab(live);
        return live;
      }
    }
    if(borisAutomationTabId){
      const existing=await chrome.tabs.get(borisAutomationTabId).catch(()=>null);
      if(existing?.id){
        if(url && existing.url!==url) await chrome.tabs.update(existing.id,{url,active:true});
        if(existing.windowId) try{ await chrome.windows.update(existing.windowId,{focused:desiredFocus,state:desiredState}); }catch{}
        const live=await chrome.tabs.get(existing.id);
        await rememberAutomationTab(live);
        return live;
      }
    }
    if(borisAutomationWindowId){
      const w=await chrome.windows.get(borisAutomationWindowId,{populate:true}).catch(()=>null);
      const t=(w?.tabs||[])[0];
      if(t?.id){
        borisAutomationTabId=t.id;
        if(url && t.url!==url) await chrome.tabs.update(t.id,{url,active:true});
        try{ await chrome.windows.update(w.id,{focused:desiredFocus,state:desiredState}); }catch{}
        const live=await chrome.tabs.get(t.id);
        await rememberAutomationTab(live);
        return live;
      }
    }
    const w=await chrome.windows.create({url,focused:desiredFocus,type:'normal',state:desiredState});
    const t=(w.tabs||[])[0] || (await chrome.tabs.query({windowId:w.id}))[0];
    await rememberAutomationTab(t);
    return t;
  }catch(e){
    borisAutomationWindowId=null; borisAutomationTabId=null;
    throw e;
  }
}
async function releaseAutomationTab(){
  try{
    const tab=await recoverAutomationTab();
    if(tab?.windowId) await chrome.windows.update(tab.windowId,{focused:false,state:'minimized'});
  }catch{}
}

async function rememberCrowdHandoff(job,tab){
  if(!job?.job_id || !tab?.id) return;
  await chrome.storage.local.set({crowd_handoff:{
    job_id:String(job.job_id),
    tab_id:Number(tab.id),
    window_id:Number(tab.windowId||0),
    platform:String(job?.metadata?.platform||''),
    saved_at:new Date().toISOString(),
  }});
}
async function recoverCrowdHandoff(job){
  const got=await chrome.storage.local.get(['crowd_handoff']);
  const h=got?.crowd_handoff||null;
  if(h && String(h.job_id||'')===String(job?.job_id||'') && Number(h.tab_id)){
    const saved=await chrome.tabs.get(Number(h.tab_id)).catch(()=>null);
    if(saved?.id){
      borisAutomationTabId=saved.id;
      borisAutomationWindowId=saved.windowId||Number(h.window_id)||null;
      return saved;
    }
  }
  const wanted=String(job?.metadata?.registration_url||job?.metadata?.post_url||'').trim();
  let wantedHost='';
  try{ wantedHost=new URL(wanted).hostname.replace(/^www\./,'').toLowerCase(); }catch{}
  const tabs=await chrome.tabs.query({});
  const match=tabs.find(t=>{
    try{
      const host=new URL(String(t.url||'')).hostname.replace(/^www\./,'').toLowerCase();
      return !!wantedHost && host===wantedHost;
    }catch{return false;}
  })||null;
  if(match?.id){
    borisAutomationTabId=match.id;
    borisAutomationWindowId=match.windowId||null;
    await rememberCrowdHandoff(job,match);
    return match;
  }
  return null;
}
async function clearCrowdHandoff(job){
  const got=await chrome.storage.local.get(['crowd_handoff']);
  const h=got?.crowd_handoff||null;
  if(!h || String(h.job_id||'')===String(job?.job_id||'')) await chrome.storage.local.remove(['crowd_handoff']);
}

async function openEditTab(itemId){
  const candidates=[
    `https://www.avito.ru/items/edit/${itemId}`,
    `https://www.avito.ru/profile/items/${itemId}/edit`,
    `https://www.avito.ru/edit/${itemId}`
  ];
  let tab=null; const diagnostics=[];
  for(const url of candidates){
    if(!tab) {
      // Reuse ONE dedicated BORIS automation window for the whole account.
      // Never create a new Chrome window per item: that was wasting RAM on Mac.
      tab=await ensureAutomationTab(url);
    } else {
      tab=await chrome.tabs.update(tab.id,{url,active:true});
      try{ if(tab.windowId) await chrome.windows.update(tab.windowId,{focused:false,state:'minimized'}); }catch{}
    }
    try{ await waitTab(tab.id,22000); }catch(e){ diagnostics.push({attempted_url:url,error:String(e?.message||e)}); continue; }
    const deadline=Date.now()+14000; let lastProbe=null;
    while(Date.now()<deadline){
      const probe=await inject(tab.id,probePage,[]).catch(e=>({probe_error:String(e?.message||e)}));
      lastProbe=probe;
      if(probe?.blocked) throw new Error(`${probe.reason || 'Avito blocked'} | ${JSON.stringify({attempted_url:url,probe})}`);
      if(probe?.loginRequired) throw new Error(`Avito просит войти в аккаунт в Chrome | ${JSON.stringify({attempted_url:url,probe})}`);
      if(probe?.looksEditable) return tab;
      await sleep(700);
    }
    const live=await chrome.tabs.get(tab.id).catch(()=>null);
    diagnostics.push({attempted_url:url,final_url:live?.url||lastProbe?.url||'',title:live?.title||'',probe:lastProbe});
  }
  if(tab?.id) await chrome.tabs.remove(tab.id).catch(()=>{});
  throw new Error(`Редактор объявления Avito не открылся | diagnostics=${JSON.stringify(diagnostics).slice(0,1400)}`);
}

function waitTab(tabId,timeout){
  return new Promise((resolve,reject)=>{
    let done=false;
    const timer=setTimeout(()=>{ if(done)return; done=true; chrome.tabs.onUpdated.removeListener(listener); reject(new Error('Таймаут загрузки страницы')); },timeout);
    const listener=(id,info)=>{ if(id===tabId && info.status==='complete' && !done){done=true;clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);resolve();} };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(t=>{ if(t.status==='complete'&&!done){done=true;clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);resolve();} }).catch(()=>{});
  });
}
async function inject(tabId,func,args){ const out=await chrome.scripting.executeScript({target:{tabId},func,args}); return out?.[0]?.result; }

async function ensureRecorderOffscreen(){
  try{
    if(chrome.offscreen?.hasDocument && await chrome.offscreen.hasDocument()) return true;
  }catch{}
  try{
    await chrome.offscreen.createDocument({url:'offscreen.html',reasons:['USER_MEDIA'],justification:'Record the dedicated BORIS automation tab for demo and QA videos.'});
    return true;
  }catch(e){
    const msg=String(e?.message||e);
    if(/single offscreen document|already exists/i.test(msg)) return true;
    throw e;
  }
}

function recorderMessage(message){
  return new Promise((resolve,reject)=>{
    chrome.runtime.sendMessage({target:'boris_offscreen_recorder',...message},response=>{
      const err=chrome.runtime.lastError;
      if(err) reject(new Error(err.message));
      else resolve(response||{ok:false,error:'empty_recorder_response'});
    });
  });
}

async function videoModeStatus(){
  await ensureRecorderOffscreen();
  const status=await recorderMessage({type:'record_status'}).catch(e=>({ok:false,error:String(e?.message||e)}));
  const saved=await chrome.storage.local.get(['recording_mode_enabled','recording_tab_id','recording_enabled_at']);
  return {ok:status?.ok!==false,enabled:!!status?.capture_active,capture_active:!!status?.capture_active,recording_active:!!status?.active,tab_id:status?.capture?.tab_id||saved.recording_tab_id||null,enabled_at:status?.capture?.started_at||saved.recording_enabled_at||null,state:status?.state||'inactive',error:status?.error||null};
}

async function enableVideoModeFromUser(options={}){
  await ensureRecorderOffscreen();
  let tab=await recoverAutomationTab();
  if(!tab?.id) tab=await ensureAutomationTab('about:blank',false);
  if(!tab?.id) throw new Error('automation_tab_missing');
  if(tab.windowId){
    try{ await chrome.windows.update(tab.windowId,{state:'normal',focused:false}); }catch{}
    try{ await chrome.tabs.update(tab.id,{active:true}); }catch{}
  }
  const streamId=await chrome.tabCapture.getMediaStreamId({targetTabId:tab.id});
  const out=await recorderMessage({type:'record_session_enable',stream_id:streamId,tab_id:tab.id,include_audio:options.include_audio===true});
  if(!out?.ok) throw new Error(out?.error||'record_session_enable_failed');
  await chrome.storage.local.set({recording_mode_enabled:true,recording_tab_id:tab.id,recording_enabled_at:new Date().toISOString()});
  return {ok:true,enabled:true,tab_id:tab.id,window_id:tab.windowId||null,started_at:out.started_at||null};
}

async function disableVideoMode(){
  await ensureRecorderOffscreen();
  const out=await recorderMessage({type:'record_session_disable'}).catch(e=>({ok:false,error:String(e?.message||e)}));
  await chrome.storage.local.remove(['recording_mode_enabled','recording_tab_id','recording_enabled_at']);
  return {ok:out?.ok!==false,enabled:false,error:out?.error||null};
}

let activeRecordingTabId=null;
let activeRecordingMasked=false;
let activeRecordingMode=null;
let timelapseCaptureStop=false;
let timelapseCaptureLoop=null;

async function startTabRecording(tab,job,options={}){
  if(!tab?.id) throw new Error('recording_tab_missing');
  await ensureRecorderOffscreen();
  if(tab.windowId){
    try{ await chrome.windows.update(tab.windowId,{state:'normal',focused:false}); }catch{}
    try{ await chrome.tabs.update(tab.id,{active:true}); }catch{}
  }
  const safeName=String(options.filename||`BORIS-${job?.job_id||'demo'}-${new Date().toISOString().replace(/[:.]/g,'-')}.webm`).replace(/[\\/:*?"<>|]+/g,'-').slice(0,180);
  if(options.mask_sensitive!==false || (options.redact_selectors||[]).length){
    await inject(tab.id,privacyMask,[options.redact_selectors||[]]).catch(()=>null);
    activeRecordingMasked=true;
  }
  const c=await cfg();
  const common={
    filename:safeName,
    include_audio:options.include_audio===true,
    video_bits_per_second:Number(options.video_bits_per_second||2500000),
    server:String(c.server||'').replace(/\/$/,''),
    token:String(c.token||''),
    job_id:String(job?.job_id||''),
    account_id:String(job?.account_id||''),
    step_index:Number(options.step_index||0),
    save_server:options.save_server!==false,
    tab_id:tab.id,
  };

  let out=null;
  try{
    const status=await recorderMessage({type:'record_status'}).catch(()=>null);
    const reusable=!!status?.capture_active && Number(status?.capture?.tab_id||0)===Number(tab.id);
    if(reusable){
      out=await recorderMessage({type:'record_start',...common});
    }else{
      const streamId=await chrome.tabCapture.getMediaStreamId({targetTabId:tab.id});
      out=await recorderMessage({type:'record_start',stream_id:streamId,...common});
    }
    if(!out?.ok) throw new Error(out?.error||'record_start_failed');
    activeRecordingMode='tab_capture';
  }catch(primaryError){
    if(options.allow_timelapse_fallback===false){
      if(activeRecordingMasked) await inject(tab.id,privacyUnmask,[]).catch(()=>{});
      activeRecordingMasked=false; activeRecordingTabId=null; activeRecordingMode=null;
      throw primaryError;
    }
    out=await recorderMessage({
      type:'record_start_timelapse',
      ...common,
      fps:Math.max(1,Math.min(Number(options.fps||2),2)),
      width:Number(options.width||1280),height:Number(options.height||720),
      video_bits_per_second:Number(options.video_bits_per_second||1600000),
      include_audio:false,
    });
    if(!out?.ok){
      if(activeRecordingMasked) await inject(tab.id,privacyUnmask,[]).catch(()=>{});
      activeRecordingMasked=false; activeRecordingTabId=null; activeRecordingMode=null;
      throw new Error(out?.error||String(primaryError?.message||primaryError));
    }
    activeRecordingMode='timelapse';
    timelapseCaptureStop=false;
    timelapseCaptureLoop=(async()=>{
      while(!timelapseCaptureStop && activeRecordingMode==='timelapse'){
        const started=Date.now();
        try{
          if(tab.windowId) await chrome.tabs.update(tab.id,{active:true}).catch(()=>{});
          const frame=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'jpeg',quality:Math.max(35,Math.min(Number(options.frame_quality||78),95))});
          await recorderMessage({type:'record_frame',data_url:frame}).catch(()=>{});
        }catch{}
        const elapsed=Date.now()-started;
        await sleep(Math.max(520-elapsed,80));
      }
    })();
  }
  activeRecordingTabId=tab.id;
  return {...out,filename:safeName,tab_id:tab.id,window_id:tab.windowId||null,privacy_guard:activeRecordingMasked,mode:activeRecordingMode};
}

async function stopTabRecording(options={}){
  let out=null;
  try{
    if(activeRecordingMode==='timelapse'){
      timelapseCaptureStop=true;
      if(timelapseCaptureLoop){
        await Promise.race([timelapseCaptureLoop,sleep(1500)]).catch(()=>{});
      }
    }
    out=await recorderMessage({type:'record_stop'});
    if(!out?.ok) throw new Error(out?.error||'record_stop_failed');
    const filename=String(options.filename||out.filename||`BORIS-${Date.now()}.webm`).replace(/[\\/:*?"<>|]+/g,'-').slice(0,180);
    let downloadId=null;
    let downloadSource=null;
    if(options.download_local!==false){
      const c=await cfg();
      let url='';
      if(out.server_path){
        url=String(c.server||'').replace(/\/$/,'')+String(out.server_path||'');
        downloadSource='server';
      }else if(out.local_blob_url){
        url=String(out.local_blob_url);
        downloadSource='local_blob';
      }
      if(url){
        downloadId=await chrome.downloads.download({url,filename:`BORIS/${filename}`,saveAs:options.save_as===true,conflictAction:'uniquify'}).catch(()=>null);
      }
    }
    if(options.download_local!==false && !downloadId && !out.server_saved) throw new Error(out.server_error||'recording_local_save_failed');
    return {ok:true,filename,download_id:downloadId,download_source:downloadSource,mime:out.mime||'video/webm',bytes:Number(out.bytes||0),duration_ms:Number(out.duration_ms||0),started_at:out.started_at||null,stopped_at:out.stopped_at||null,server_saved:!!out.server_saved,server_path:out.server_path||null,server_error:out.server_error||null,local_download:!!downloadId,markers:out.markers||[],privacy_guard:activeRecordingMasked};
  }finally{
    if(activeRecordingMasked && activeRecordingTabId){
      await inject(activeRecordingTabId,privacyUnmask,[]).catch(()=>{});
    }
    activeRecordingMasked=false;
    activeRecordingTabId=null;
    activeRecordingMode=null;
    timelapseCaptureStop=true;
    timelapseCaptureLoop=null;
  }
}

async function ensureFieldSurface(tabId,labels){
  const wanted=(labels||[]).map(x=>String(x||'').trim()).filter(Boolean);
  for(let attempt=0;attempt<4;attempt++){
    const scan=await inject(tabId,scanFields,[]).catch(()=>null);
    const have=new Set((scan?.fields||[]).map(f=>String(f?.label||'').trim().toLowerCase()));
    if(wanted.some(x=>have.has(x.toLowerCase()))) return scan;
    const nav=await inject(tabId,openFieldSurface,[wanted]).catch(()=>null);
    if(!nav?.clicked) return scan;
    await sleep(900);
  }
  return await inject(tabId,scanFields,[]).catch(()=>null);
}

async function fetchPhotoPayloads(raw){
  const urls=[...new Set(String(raw||'').split(/\n|\r|,\s*(?=https?:\/\/)/i).map(x=>x.trim()).filter(x=>/^https:\/\//i.test(x)))].slice(0,10);
  const files=[];
  for(let i=0;i<urls.length;i++){
    const r=await fetch(urls[i],{credentials:'omit'});
    if(!r.ok) continue;
    const blob=await r.blob();
    if(!String(blob.type||'').startsWith('image/')) continue;
    const bytes=new Uint8Array(await blob.arrayBuffer());
    let binary='';
    for(let p=0;p<bytes.length;p+=32768) binary+=String.fromCharCode(...bytes.subarray(p,p+32768));
    const ext=(blob.type.split('/')[1]||'jpg').replace('jpeg','jpg').replace(/[^a-z0-9]/gi,'')||'jpg';
    files.push({name:`boris-${Date.now()}-${i+1}.${ext}`,type:blob.type||'image/jpeg',base64:btoa(binary)});
  }
  return {files};
}


const SITE_ADAPTERS=[
  {id:'avito',hosts:['avito.ru','www.avito.ru']},
  {id:'yandex',hosts:['mail.yandex.ru','id.yandex.ru','passport.yandex.ru']},
  {id:'google',hosts:['accounts.google.com','myaccount.google.com','mail.google.com']},
  {id:'youtube',hosts:['studio.youtube.com','youtube.com','www.youtube.com']},
  {id:'vk',hosts:['vk.com']},
  {id:'telegram',hosts:['web.telegram.org']},
  {id:'mailru',hosts:['e.mail.ru','account.mail.ru']},
];
function adapterForUrl(raw){
  let h=''; try{h=new URL(raw).hostname.toLowerCase();}catch{return null;}
  return SITE_ADAPTERS.find(a=>a.hosts.some(x=>h===x||h.endsWith('.'+x)))||null;
}

async function recipeDomAction(kind,payload={}){
  const visible=el=>{ try{ const r=el?.getBoundingClientRect?.(); const s=getComputedStyle(el); return !!el && !el.disabled && r && r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; }catch{return false;} };
  const clean=s=>String(s||'').replace(/\s+/g,' ').trim();
  const labelFor=el=>{
    const id=el?.id||'';
    let label='';
    try{ if(id) label=document.querySelector(`label[for="${CSS.escape(id)}"]`)?.innerText||''; }catch{}
    if(!label) label=el?.closest?.('label')?.innerText||el?.closest?.('.formRow,.form-group,.field,.inputGroup')?.querySelector?.('label,dt,.label,.formRow-label')?.innerText||'';
    return clean(label);
  };
  const fieldBlob=el=>clean([labelFor(el),el?.getAttribute?.('name'),el?.id,el?.getAttribute?.('placeholder'),el?.getAttribute?.('aria-label'),el?.getAttribute?.('autocomplete'),el?.getAttribute?.('data-validation-url')].filter(Boolean).join(' ')).toLowerCase();
  const rootFor=o=>{
    if(o?.form_selector){ try{ const x=document.querySelector(o.form_selector); if(x) return x; }catch{} }
    const forms=[...document.querySelectorAll('form')];
    let best=null,bestScore=-1;
    for(const f of forms){
      let score=0;
      if(f.querySelector('input[autocomplete="username"],input[type="email"],input[autocomplete="email"]')) score+=5;
      if(f.querySelector('input[type="password"],input[autocomplete*="password"]')) score+=5;
      if(f.querySelector('textarea,[contenteditable="true"]')) score+=2;
      if(f.querySelector('button[type="submit"],input[type="submit"]')) score+=2;
      if(score>bestScore){best=f;bestScore=score;}
    }
    return best||document;
  };
  const rolePatterns={
    username:/user.?name|username|login|логин|имя пользователя|ник|nickname|member/i,
    email:/e-?mail|email|электронн|почт/i,
    password:/password|pass|парол/i,
    title:/title|subject|topic|theme|заголов|тема|назван/i,
    body:/body|message|content|description|text|post|сообщен|описан|текст/i,
    phone:/phone|tel|телефон|мобил/i,
    url:/url|website|site|link|сайт|ссылк/i,
    search:/search|query|keyword|поиск|запрос/i,
    name:/name|author|имя|фио/i,
  };
  const candidatesFor=(root,role)=>{
    let nodes=[...root.querySelectorAll('input,textarea,[contenteditable="true"]')].filter(visible);
    nodes=nodes.filter(el=>{
      const type=String(el.getAttribute?.('type')||'').toLowerCase();
      if(['hidden','submit','button','reset','file','image'].includes(type)) return false;
      if(type==='checkbox'||type==='radio') return false;
      return true;
    });
    return nodes.map(el=>{
      const type=String(el.getAttribute?.('type')||'').toLowerCase();
      const ac=String(el.getAttribute?.('autocomplete')||'').toLowerCase();
      const blob=fieldBlob(el); let score=visible(el)?10:0;
      if(el.required) score+=8;
      if(role==='username' && ac==='username') score+=120;
      if(role==='email' && ac==='email') score+=120;
      if(role==='email' && type==='email') score+=55;
      if(role==='password' && (type==='password'||/password/.test(ac))) score+=120;
      if(role==='body' && (el.tagName==='TEXTAREA'||el.getAttribute?.('contenteditable')==='true')) score+=45;
      if(role==='title' && el.tagName==='INPUT') score+=10;
      if(role==='phone' && (type==='tel'||ac==='tel')) score+=70;
      if(role==='url' && (type==='url'||ac==='url')) score+=70;
      if(rolePatterns[role]?.test(blob)) score+=45;
      for(const hint of (payload.hints||[])){ if(blob.includes(String(hint).toLowerCase())) score+=35; }
      if((role==='username'||role==='email') && /autocomplete\s*off/.test(blob) && !el.required) score-=35;
      if(/captcha|recaptcha|hcaptcha|turnstile|robot|робот|otp|2fa/.test(blob)) score-=200;
      return {el,score,blob};
    }).sort((a,b)=>b.score-a.score);
  };
  const findField=(o,role)=>{
    const root=rootFor(o);
    for(const sel of (o?.selector_hints||[])){ try{ const el=root.querySelector(sel); if(el&&visible(el)) return el; }catch{} }
    return candidatesFor(root,role)[0]?.el||null;
  };
  const setValue=(el,value)=>{
    const raw=String(value??'');
    if(el.getAttribute?.('contenteditable')==='true'){
      el.focus(); el.textContent=raw; el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:raw})); el.dispatchEvent(new Event('change',{bubbles:true})); el.blur(); return raw;
    }
    let v=raw; const max=Number(el.maxLength||0); if(max>0&&v.length>max) v=v.slice(0,max);
    const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;
    if(setter) setter.call(el,v); else el.value=v;
    el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
    try{el.focus();el.blur();}catch{}
    return v;
  };
  const buttonText=el=>clean(el?.innerText||el?.value||el?.getAttribute?.('aria-label')||el?.getAttribute?.('title')||'');

  if(kind==='inspect_form'){
    const root=rootFor(payload); const fields=[];
    for(const el of [...root.querySelectorAll('input,textarea,select,[contenteditable="true"]')]){
      if(!visible(el)) continue;
      const type=String(el.getAttribute?.('type')||el.tagName||'').toLowerCase();
      const sensitive=type==='password'||/password|secret|token|recovery|otp|2fa/i.test(fieldBlob(el));
      fields.push({tag:el.tagName,type,name:String(el.getAttribute?.('name')||'').slice(0,160),id:String(el.id||'').slice(0,160),autocomplete:String(el.getAttribute?.('autocomplete')||'').slice(0,100),required:!!el.required,maxlength:Number(el.maxLength||0),label:labelFor(el).slice(0,300),placeholder:String(el.getAttribute?.('placeholder')||'').slice(0,300),checked:'checked' in el?!!el.checked:null,sensitive});
      if(fields.length>=120) break;
    }
    const buttons=[...root.querySelectorAll('button,input[type="submit"],input[type="button"],a[role="button"]')].filter(visible).slice(0,40).map(el=>({tag:el.tagName,type:String(el.getAttribute?.('type')||''),text:buttonText(el).slice(0,300),name:String(el.getAttribute?.('name')||'').slice(0,160),id:String(el.id||'').slice(0,160)}));
    return {ok:true,url:location.href,title:document.title,fields,buttons,form_found:root!==document};
  }

  if(kind==='fill_semantic'){
    const role=String(payload.role||'text').toLowerCase(); const el=findField(payload,role);
    if(!el) return payload.optional===true?{ok:true,skipped:true,reason:'semantic_field_not_found',role,url:location.href}:{ok:false,error:'semantic_field_not_found',role,url:location.href};
    if(role==='password' && payload.allow_local_secret!==true) return {ok:false,error:'password_requires_local_secret',role};
    const expected=setValue(el,payload.value??'');
    await new Promise(r=>setTimeout(r,Number(payload.verify_delay_ms||350)));
    const current=el.getAttribute?.('contenteditable')==='true'?String(el.textContent||''):String(el.value||'');
    const persisted=current===expected && expected.length>0;
    return {ok:persisted,role,persisted,field:{tag:el.tagName,type:String(el.getAttribute?.('type')||''),name:String(el.getAttribute?.('name')||'').slice(0,160),id:String(el.id||'').slice(0,160),autocomplete:String(el.getAttribute?.('autocomplete')||''),label:labelFor(el).slice(0,300),maxlength:Number(el.maxLength||0)},value_length:current.length,value:role==='password'?undefined:current,error:persisted?null:'semantic_value_not_persisted',url:location.href};
  }

  if(kind==='check_semantic'){
    const root=rootFor(payload); let boxes=[];
    for(const sel of (payload.selector_hints||[])){ try{ const el=root.querySelector(sel); if(el&&visible(el)){boxes=[el];break;} }catch{} }
    if(!boxes.length) boxes=[...root.querySelectorAll('input[type="checkbox"],input[type="radio"]')].filter(visible);
    const want=(payload.label_contains||payload.hints||[]).map(x=>String(x).toLowerCase());
    let el=boxes.find(x=>!want.length||want.some(w=>fieldBlob(x).includes(w)))||null;
    if(!el) return payload.optional===true?{ok:true,skipped:true,reason:'semantic_checkbox_not_found'}:{ok:false,error:'semantic_checkbox_not_found'};
    const desired=payload.checked!==false;
    if(!!el.checked!==desired) el.click();
    return {ok:!!el.checked===desired,checked:!!el.checked,label:labelFor(el).slice(0,300),url:location.href};
  }

  if(kind==='submit_semantic'){
    const root=rootFor(payload); let btn=null;
    for(const sel of (payload.selector_hints||[])){ try{ const el=root.querySelector(sel); if(el&&visible(el)){btn=el;break;} }catch{} }
    if(!btn){
      const buttons=[...root.querySelectorAll('button[type="submit"],input[type="submit"],button:not([type]),a[role="button"]')].filter(visible);
      const words=(payload.text_contains||['register','sign up','submit','publish','post','save','создать','зарегистр','отправ','опубликов','сохран']).map(x=>String(x).toLowerCase());
      btn=buttons.find(el=>words.some(w=>buttonText(el).toLowerCase().includes(w)))||buttons[0]||null;
    }
    if(!btn) return {ok:false,error:'semantic_submit_not_found'};
    if(btn.disabled||btn.getAttribute?.('aria-disabled')==='true') return {ok:false,error:'semantic_submit_disabled',text:buttonText(btn).slice(0,300)};
    const text=buttonText(btn).slice(0,300); btn.scrollIntoView({block:'center'}); btn.click();
    return {ok:true,clicked:true,text,url:location.href};
  }

  if(kind==='assert_value'){
    const role=String(payload.role||'text').toLowerCase(); const el=findField(payload,role);
    if(!el) return {ok:false,error:'semantic_field_not_found',role};
    const current=el.getAttribute?.('contenteditable')==='true'?String(el.textContent||''):String(el.value||'');
    const nonempty=current.trim().length>0; const expected=payload.value==null?null:String(payload.value);
    const matches=expected==null?nonempty:current===expected;
    return {ok:matches,role,nonempty,matches,value_length:current.length,value:role==='password'?undefined:current,url:location.href};
  }

  if(kind==='page_state'){
    const text=clean(document.body?.innerText||'').slice(0,50000); const low=text.toLowerCase();
    const present=(payload.selector_present||[]).map(sel=>{try{return !!document.querySelector(sel);}catch{return false;}});
    const absent=(payload.selector_absent||[]).map(sel=>{try{return !document.querySelector(sel);}catch{return true;}});
    const any=(payload.text_any||[]).map(x=>String(x).toLowerCase()).some(x=>low.includes(x));
    const none=(payload.text_none||[]).every(x=>!low.includes(String(x).toLowerCase()));
    const urlChanged=payload.start_url?String(location.href).replace(/\/$/,'')!==String(payload.start_url).replace(/\/$/,''):false;
    const ok=(!payload.selector_present?.length||present.every(Boolean))&&(!payload.selector_absent?.length||absent.every(Boolean))&&(!payload.text_any?.length||any)&&(!payload.text_none?.length||none)&&(!payload.require_url_changed||urlChanged);
    return {ok,url:location.href,title:document.title,selector_present:present,selector_absent:absent,text_any:any,text_none:none,url_changed:urlChanged,text_excerpt:text.slice(0,4000)};
  }

  if(kind==='extract_links'){
    const out=[]; let rx=null; try{ if(payload.href_regex) rx=new RegExp(payload.href_regex,'i'); }catch{}
    for(const a of [...document.querySelectorAll('a[href]')]){
      const href=String(a.href||''); const text=buttonText(a); let host=''; try{host=new URL(href).hostname.toLowerCase();}catch{}
      if(payload.host_contains && !host.includes(String(payload.host_contains).toLowerCase())) continue;
      if(rx && !rx.test(href)) continue;
      if(payload.text_contains && !text.toLowerCase().includes(String(payload.text_contains).toLowerCase())) continue;
      out.push({href,text:text.slice(0,500)}); if(out.length>=Math.min(Number(payload.limit||50),100)) break;
    }
    return {ok:true,url:location.href,links:out};
  }

  if(kind==='highlight_element'){
    const root=rootFor(payload); let el=null;
    for(const sel of (payload.selector_hints||[])){ try{ const x=root.querySelector(sel); if(x&&visible(x)){el=x;break;} }catch{} }
    if(!el && payload.role) el=findField(payload,String(payload.role).toLowerCase());
    if(!el) return {ok:false,error:'highlight_target_not_found'};
    const ms=Math.max(150,Math.min(Number(payload.duration_ms||900),5000));
    const oldOutline=el.style.outline||''; const oldOffset=el.style.outlineOffset||''; const oldTransition=el.style.transition||'';
    el.scrollIntoView({block:'center',behavior:payload.smooth===false?'auto':'smooth'});
    el.style.transition='outline 120ms ease'; el.style.outline='3px solid currentColor'; el.style.outlineOffset='4px';
    await new Promise(r=>setTimeout(r,ms));
    el.style.outline=oldOutline; el.style.outlineOffset=oldOffset; el.style.transition=oldTransition;
    return {ok:true,duration_ms:ms,url:location.href};
  }

  if(kind==='human_probe'){
    const start=String(payload.start_url||'').replace(/\/$/,''); const current=String(location.href||'').replace(/\/$/,'');
    const urlChanged=!!start&&current!==start;
    let challengeNodes=[];
    for(const sel of (payload.challenge_selectors||[])){ try{challengeNodes.push(...document.querySelectorAll(sel));}catch{} }
    challengeNodes.push(...document.querySelectorAll('iframe,input,textarea'));
    challengeNodes=[...new Set(challengeNodes)].filter(el=>{
      const blob=fieldBlob(el); const tag=String(el.tagName||'').toUpperCase();
      if(tag==='IFRAME') return /captcha|recaptcha|hcaptcha|turnstile|challenge|human|robot/.test(blob+String(el.getAttribute?.('src')||''));
      return /captcha|recaptcha|hcaptcha|turnstile|challenge|human|robot|verification question|проверочн|контрольн/i.test(blob);
    }).filter(visible);
    if(!challengeNodes.length && payload.optional===true){
      return {ok:true,ready:true,no_challenge:true,challenge_visible:false,url:location.href};
    }
    let answered=false;
    for(const el of challengeNodes){
      if('checked' in el && el.checked) answered=true;
      const val=String(el.value||'').trim(); if(val) answered=true;
    }
    for(const sel of ['textarea[name="g-recaptcha-response"]','textarea[name="h-captcha-response"]','input[name*="turnstile" i]']){ try{ const el=document.querySelector(sel); if(String(el?.value||'').trim()) answered=true; }catch{} }
    let completion=false;
    for(const sel of (payload.completion_selectors||[])){ try{ if(document.querySelector(sel)){completion=true;break;} }catch{} }
    const formGone=payload.form_selector?(()=>{try{return !document.querySelector(payload.form_selector);}catch{return false;}})():false;
    const ready=!!(urlChanged||completion||formGone||answered);
    return {ok:true,ready,answered,url_changed:urlChanged,completion,form_gone:formGone,challenge_visible:challengeNodes.length>0,url:location.href};
  }

  return {ok:false,error:'unknown_recipe_dom_action',kind};
}

const SENSITIVE_SELECTORS=[
  'input[type="password"]','input[name*="password" i]','input[autocomplete="current-password"]','input[autocomplete="new-password"]',
  '[data-testid*="password" i]','[class*="password" i]','[id*="password" i]',
  '[class*="recovery" i]','[id*="recovery" i]','[class*="token" i]','[id*="token" i]',
  '[class*="secret" i]','[id*="secret" i]','[class*="bank" i]','[id*="bank" i]'
];

function safeReadPage(){
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const clone=document.body.cloneNode(true);
  for(const el of clone.querySelectorAll('script,style,noscript,input[type="password"],textarea[name*="password" i]')) el.remove();
  for(const el of clone.querySelectorAll('input,textarea')){
    const type=(el.getAttribute('type')||'').toLowerCase();
    const name=(el.getAttribute('name')||'').toLowerCase();
    if(type==='password'||/password|secret|token|2fa|otp|recovery/.test(name)) el.setAttribute('value','[REDACTED]');
    else el.setAttribute('value','');
  }
  return {title:document.title,url:location.href,text:clean(clone.innerText).slice(0,30000)};
}
function readAvitoSearch(){
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const money=s=>{
    const m=clean(s).match(/(\d[\d\s\u00a0]{1,15})\s*₽/);
    if(!m) return null;
    const n=Number(m[1].replace(/\D/g,'')); return Number.isFinite(n)?n:null;
  };
  const cards=[...document.querySelectorAll('[data-marker="item"],article[data-marker*="item"],div[data-marker="item"]')];
  const seen=new Set(); const items=[];
  for(const card of cards){
    const a=card.querySelector('a[href*="avito.ru"],a[href*="/"]');
    const href=a?.href||'';
    const idm=href.match(/_(\d{6,})(?:\?|$)/) || href.match(/\/items\/(\d+)/);
    const itemId=idm?.[1]||'';
    const titleEl=card.querySelector('[data-marker="item-title"],h3,[itemprop="name"]');
    const title=clean(titleEl?.innerText||a?.innerText||'');
    if(!title || (!itemId && !href)) continue;
    const key=itemId||href; if(seen.has(key)) continue; seen.add(key);
    const text=clean(card.innerText||'');
    const seller=clean(card.querySelector('[data-marker*="seller"],[data-marker*="company"],[class*="seller"]')?.innerText||'');
    const location=clean(card.querySelector('[data-marker*="location"],[class*="geo"],[class*="location"]')?.innerText||'');
    const priceEl=card.querySelector('[data-marker="item-price"],[itemprop="price"],[class*="price"]');
    const price=money(priceEl?.innerText||text);
    const promoted=/продвиж|реклама|premium|vip|x[l|2|5]|выделен/i.test(text);
    items.push({position:items.length+1,item_id:itemId||null,title:title.slice(0,300),price,url:href||null,seller:seller.slice(0,220)||null,location:location.slice(0,220)||null,promoted,text:text.slice(0,900)});
    if(items.length>=60) break;
  }
  return {ok:true,url:location.href,title:document.title,count:items.length,items};
}
function readAvitoProItems(){
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const idFromHref=href=>{
    const raw=String(href||'');
    const m=raw.match(/_(\d{6,})(?:\?|#|$)/) || raw.match(/\/(?:items|item)\/(\d{6,})(?:\/|\?|#|$)/) || raw.match(/[?&](?:itemId|item_id|id)=(\d{6,})/i);
    return m?.[1]||'';
  };
  const rankFromText=text=>{
    const t=clean(text);
    const hidden=/Скрыто\s*из\s*поисковой\s*выдачи/i.test(t);
    const m=t.match(/(?<!\d)(\d{1,4})\s*место(?:\s*([↑↓])\s*(\d{1,4}))?/i);
    if(m){
      const magnitude=Number(m[3]||0); const arrow=m[2]||'';
      return {position:Number(m[1]),delta:arrow==='↑'?magnitude:arrow==='↓'?-magnitude:0,visibility:'visible'};
    }
    return {position:null,delta:null,visibility:hidden?'hidden':'unknown'};
  };
  const anchors=[...document.querySelectorAll('a[href]')];
  const seen=new Set(); const items=[];
  for(const a of anchors){
    const href=a.href||''; const itemId=idFromHref(href); if(!itemId || seen.has(itemId)) continue;
    let node=a; let chosen=null;
    for(let depth=0;node && depth<9;depth++,node=node.parentElement){
      const text=clean(node.innerText||'');
      if(text.length<12 || text.length>5000) continue;
      if(/Продвинуть|Рассылка|новых чатов|дн(?:я|ей|ь) на Авито|Скрыто\s*из\s*поисковой\s*выдачи|\d{1,4}\s*место/i.test(text)) { chosen=node; break; }
    }
    if(!chosen) continue;
    const text=clean(chosen.innerText||''); const rank=rankFromText(text);
    const title=clean(a.innerText||chosen.querySelector('h3,[data-marker*="title"],[itemprop="name"]')?.innerText||'').slice(0,300);
    // Require Avito Pro evidence; generic navigation links carrying numbers are ignored.
    if(rank.position==null && rank.visibility==='unknown' && !/Продвинуть|Рассылка|новых чатов/i.test(text)) continue;
    seen.add(itemId);
    items.push({item_id:itemId,title:title||null,url:href,position:rank.position,delta:rank.delta,visibility:rank.visibility,text:text.slice(0,1400)});
    if(items.length>=80) break;
  }
  const pages=[]; const pageSeen=new Set();
  for(const a of anchors){
    const label=clean(a.innerText||a.getAttribute('aria-label')||'');
    if(!/^\d{1,3}$/.test(label)) continue;
    const href=a.href||''; if(!href || !/avito\.ru/i.test(href)) continue;
    // Pagination links live close to the "из N объявлений" footer. Keep only
    // links whose nearby container looks like a pager to avoid unrelated digits.
    let node=a; let pager=false;
    for(let depth=0;node && depth<5;depth++,node=node.parentElement){
      const t=clean(node.innerText||'');
      if(/из\s+\d+\s+объявлен/i.test(t) || /•••/.test(t) || /Показать ещё/i.test(t)){pager=true;break;}
    }
    if(!pager || pageSeen.has(href)) continue; pageSeen.add(href); pages.push({page:Number(label),url:href});
  }
  pages.sort((a,b)=>a.page-b.page);
  const summary=clean(document.body?.innerText||'').match(/(\d+)\s+из\s+(\d+)\s+объявлен/i);
  return {ok:true,reader:'avito_pro_items_v1',url:location.href,title:document.title,count:items.length,items,pages,visible_to:Number(summary?.[1]||0)||null,total:Number(summary?.[2]||0)||null};
}

function findSafeElement(selector){
  if(!selector || /password|secret|token|cookie|recovery|otp|2fa/i.test(selector)) return {ok:false,error:'Sensitive selector blocked'};
  const el=document.querySelector(selector);
  if(!el) return {ok:false,error:'Element not found'};
  const r=el.getBoundingClientRect();
  return {ok:true,text:(el.innerText||el.getAttribute('aria-label')||el.getAttribute('title')||'').trim().slice(0,1000),rect:{x:r.x,y:r.y,width:r.width,height:r.height},tag:el.tagName};
}
function clickSafe(selector){
  if(!selector || /password|secret|token|cookie|recovery|otp|2fa/i.test(selector)) return {ok:false,error:'Sensitive selector blocked'};
  const el=document.querySelector(selector); if(!el) return {ok:false,error:'Element not found'};
  el.scrollIntoView({block:'center',behavior:'instant'}); el.click(); return {ok:true};
}
function inputSafe(selector,value){
  if(!selector || /password|secret|token|cookie|recovery|otp|2fa/i.test(selector)) return {ok:false,error:'Sensitive field blocked'};
  const el=document.querySelector(selector); if(!el) return {ok:false,error:'Element not found'};
  if((el.getAttribute('type')||'').toLowerCase()==='password') return {ok:false,error:'Password fields are never readable/writable'};
  const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  const set=Object.getOwnPropertyDescriptor(proto,'value')?.set; if(set) set.call(el,String(value??'')); else el.value=String(value??'');
  el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return {ok:true};
}
function selectSafe(selector,value){
  if(!selector || /password|secret|token|cookie|recovery|otp|2fa/i.test(selector)) return {ok:false,error:'Sensitive selector blocked'};
  const el=document.querySelector(selector); if(!el || el.tagName!=='SELECT') return {ok:false,error:'Select not found'};
  const opt=[...el.options].find(o=>o.value===String(value)||o.text.trim()===String(value)); if(!opt) return {ok:false,error:'Option not found'};
  el.value=opt.value; el.dispatchEvent(new Event('change',{bubbles:true})); return {ok:true,value:opt.text};
}
function scrollSafe(selector,value){
  if(selector){ const el=document.querySelector(selector); if(!el) return {ok:false,error:'Element not found'}; el.scrollIntoView({block:'center',behavior:'smooth'}); }
  else window.scrollBy({top:Number(value||600),behavior:'smooth'});
  return {ok:true};
}
function privacyMask(extra=[]){
  const selectors=[...SENSITIVE_SELECTORS,...(extra||[])]; let count=0;
  for(const sel of selectors){
    let nodes=[]; try{nodes=[...document.querySelectorAll(sel)];}catch{continue;}
    for(const el of nodes){
      el.dataset.borisPrivacyMasked='1'; el.dataset.borisOldFilter=el.style.filter||''; el.dataset.borisOldColor=el.style.color||'';
      el.style.filter='blur(12px)'; el.style.color='transparent'; el.style.userSelect='none'; count++;
    }
  }
  return {ok:true,count};
}
function privacyUnmask(){
  for(const el of document.querySelectorAll('[data-boris-privacy-masked="1"]')){
    el.style.filter=el.dataset.borisOldFilter||''; el.style.color=el.dataset.borisOldColor||''; el.style.userSelect='';
    delete el.dataset.borisPrivacyMasked; delete el.dataset.borisOldFilter; delete el.dataset.borisOldColor;
  }
  return {ok:true};
}
async function captureDiagnosticFrame(job,tab,seq,stage){
  if(!job?.metadata?.diagnostic_capture && !String(job?.route?.reason||'').includes('pilot')) return null;
  if(!tab?.id || !tab?.windowId) return null;
  try{
    // captureVisibleTab needs an active tab in the target window, but the window
    // itself stays unfocused/minimized so the owner's Mac workflow is untouched.
    await chrome.tabs.update(tab.id,{active:true}).catch(()=>{});
    const dataUrl=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'png'});
    const art=await saveScreenshotArtifact(job,Number(seq||0),dataUrl);
    await progress(job,'diagnostic_frame',null,{stage,path:art.path,seq:Number(seq||0)}).catch(()=>{});
    return art.path;
  }catch(e){
    await progress(job,'diagnostic_frame_failed',null,{stage,error:String(e?.message||e),seq:Number(seq||0)}).catch(()=>{});
    return null;
  }
}

async function crowdLocalCredentials(job){
  const platform=String(job?.metadata?.platform||job?.domain||'crowd').toLowerCase().replace(/[^a-z0-9_.-]/g,'_').slice(0,100)||'crowd';
  const key='crowd_credentials:'+platform;
  const got=await chrome.storage.local.get([key]);
  let row=got[key];
  if(!row || !row.password){
    const bytes=new Uint8Array(18); crypto.getRandomValues(bytes);
    const tail=[...bytes].map(b=>(b%36).toString(36)).join('');
    const username=String(job?.metadata?.username||('borisai'+tail.slice(0,7))).replace(/[^A-Za-z0-9_.-]/g,'').slice(0,30)||('borisai'+tail.slice(0,7));
    const password='B!'+tail.slice(0,8)+'a9'+tail.slice(8,16)+'Z7';
    row={platform,username,password,email:String(job?.metadata?.email||''),created_at:new Date().toISOString()};
    await chrome.storage.local.set({[key]:row});
  }else if(job?.metadata?.email && row.email!==String(job.metadata.email)){
    row={...row,email:String(job.metadata.email),updated_at:new Date().toISOString()};
    await chrome.storage.local.set({[key]:row});
  }
  return row;
}

async function crowdPrefillRegistration(payload){
  const visible=el=>{ const r=el?.getBoundingClientRect?.(); return !!el && !el.disabled && r && r.width>0 && r.height>0; };
  const pause=(ms)=>new Promise(r=>setTimeout(r,ms));
  const setValue=(el,value)=>{
    if(!el || value==null || value==='') return false;
    const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;
    if(setter) setter.call(el,String(value)); else el.value=String(value);
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    try{ el.focus(); el.blur(); }catch{}
    return true;
  };
  const forms=[...document.querySelectorAll('form')];
  const regForm=forms.find(f=>
    f.querySelector('input[autocomplete="username"][required],input[autocomplete="email"][required],input[autocomplete="new-password"][required],input[type="password"][required]')
  )||forms.find(f=>f.querySelector('input[autocomplete="username"],input[autocomplete="email"],input[type="password"]'))||null;
  if(!regForm) return {ok:false,error:'registration_form_not_found',url:location.href};
  const pick=(sels)=>{ for(const sel of sels){ try{ for(const el of regForm.querySelectorAll(sel)){ if(visible(el)) return el; } }catch{} } return null; };
  const user=pick(['input[autocomplete="username"][required]','input[autocomplete="username"]','input[data-validation-url*="username" i][required]','input[data-validation-url*="username" i]']);
  const email=pick(['input[autocomplete="email"][required]','input[type="email"][required]','input[autocomplete="email"]','input[type="email"]']);
  const pass=[...regForm.querySelectorAll('input[autocomplete="new-password"][required],input[type="password"][required],input[autocomplete="new-password"],input[type="password"]')].filter(visible).filter((el,i,a)=>a.indexOf(el)===i).slice(0,2);
  const out={ok:true,url:location.href,username:false,email:false,password_fields:0,username_field:!!user,email_field:!!email,password_field_count:pass.length,terms_checked:0,profile_fields_filled:0,verified_after_fill:false};
  let actualUsername=String(payload.username||'');
  if(user){
    const max=Number(user.maxLength||0);
    if(max>0 && actualUsername.length>max) actualUsername=actualUsername.slice(0,max);
    setValue(user,actualUsername);
  }
  if(email) setValue(email,payload.email||'');
  for(const el of pass) setValue(el,payload.password||'');

  const profileValue='Использую веб-системы, CRM, SaaS и инструменты автоматизации; занимаюсь разработкой и интеграцией программного обеспечения для бизнеса.';
  const profileCandidates=[...regForm.querySelectorAll('textarea,input[type="text"]')].filter(visible);
  for(const el of profileCandidates){
    if(el===user || el===email || String(el.value||'').trim()) continue;
    const id=el.id||'';
    const name=el.getAttribute('name')||'';
    const label=(id?document.querySelector(`label[for="${CSS.escape(id)}"]`):null)?.innerText||el.closest('label')?.innerText||el.parentElement?.innerText||'';
    const blob=String(`${label} ${name} ${id} ${el.getAttribute('placeholder')||''}`).toLowerCase();
    if(/captcha|recaptcha|hcaptcha|turnstile|robot|робот|captcha_question/.test(blob)) continue;
    if(/describe.{0,30}system|system\s*\(?s\)?|your system|опишите.{0,30}систем/.test(blob)){
      if(setValue(el,profileValue)) out.profile_fields_filled++;
    }
  }
  const checkboxes=[...regForm.querySelectorAll('input[type="checkbox"]')].filter(visible);
  for(const cb of checkboxes){
    const id=cb.id||'';
    const label=(id?document.querySelector(`label[for="${CSS.escape(id)}"]`):null)?.innerText||cb.closest('label')?.innerText||cb.parentElement?.innerText||'';
    const low=String(label).toLowerCase();
    if(/услов|правил|terms|privacy|конфиденц|соглас/.test(low) && !/captcha|robot|робот/.test(low) && !cb.checked){ cb.click(); }
    if(cb.checked && /услов|правил|terms|privacy|конфиденц|соглас/.test(low)) out.terms_checked++;
  }

  await pause(350);
  out.username=!!user && String(user.value||'').trim()===actualUsername.trim() && !!actualUsername.trim();
  out.email=!!email && String(email.value||'').trim().toLowerCase()===String(payload.email||'').trim().toLowerCase() && !!String(payload.email||'').trim();
  out.password_fields=pass.filter(el=>String(el.value||'')===String(payload.password||'') && !!String(payload.password||'')).length;
  out.username_value=out.username?actualUsername:'';
  out.verified_after_fill=true;
  const usernameReady=!out.username_field || out.username;
  const emailReady=!out.email_field || out.email;
  const passwordReady=out.password_field_count===0 || out.password_fields===out.password_field_count;
  out.complete=!!(out.email_field && usernameReady && emailReady && passwordReady);
  if(!out.complete){ out.ok=false; out.error='registration_fields_not_persisted'; }
  return out;
}

function crowdSubmitRegistration(){
  const forms=[...document.querySelectorAll('form')];
  const form=forms.find(f=>f.querySelector('input[type="password"],input[type="email"],input[autocomplete="username"]'))||forms[0]||null;
  if(!form) return {ok:false,error:'registration_form_not_found'};
  const btn=form.querySelector('button[type="submit"],input[type="submit"]');
  if(!btn) return {ok:false,error:'registration_submit_not_found'};
  if(btn.disabled || btn.getAttribute('aria-disabled')==='true') return {ok:false,error:'registration_submit_disabled'};
  btn.scrollIntoView({block:'center'}); btn.click();
  return {ok:true,clicked:true,url:location.href};
}

function crowdHumanCheckpointProbe(){
  const challengeFrame=(el)=>{
    if(!el || String(el.tagName||'').toUpperCase()!=='IFRAME') return false;
    const blob=String((el.getAttribute('src')||'')+' '+(el.getAttribute('title')||'')+' '+(el.getAttribute('name')||'')).toLowerCase();
    return /captcha|recaptcha|hcaptcha|turnstile|challenge|human|robot/.test(blob);
  };
  const root=document.documentElement;
  const active=document.activeElement;
  const now=Date.now();
  if(challengeFrame(active)){
    root.dataset.borisCrowdChallengeTouched=String(now);
    root.dataset.borisCrowdChallengeSeen='1';
  }
  const touched=Number(root.dataset.borisCrowdChallengeTouched||0);
  const interacted=root.dataset.borisCrowdChallengeSeen==='1';
  const forms=[...document.querySelectorAll('form')];
  const form=forms.find(f=>f.querySelector('input[type="password"],input[type="email"],input[autocomplete="username"]'))||null;
  const btn=form?.querySelector('button[type="submit"],input[type="submit"]')||null;
  const submitEnabled=!!btn && !btn.disabled && btn.getAttribute('aria-disabled')!=='true';
  const challengeVisible=[...document.querySelectorAll('iframe')].some(el=>{
    if(!challengeFrame(el)) return false;
    const r=el.getBoundingClientRect(); return r.width>0 && r.height>0;
  });
  const leftChallenge=!!(interacted && !challengeFrame(active) && touched && (now-touched)>=450);
  const formGone=!form;
  return {
    ok:true,
    interacted,
    ready:!!(formGone || (leftChallenge && submitEnabled)),
    form_gone:formGone,
    submit_enabled:submitEnabled,
    challenge_visible:challengeVisible,
    url:location.href,
  };
}

function crowdResetHumanCheckpointProbe(){
  delete document.documentElement.dataset.borisCrowdChallengeTouched;
  delete document.documentElement.dataset.borisCrowdChallengeSeen;
  return {ok:true,url:location.href};
}

async function waitCrowdHumanCheckpoint(tabId,timeoutMs=600000,currentCapability='crowd_registration'){
  const deadline=Date.now()+Math.min(Math.max(Number(timeoutMs||600000),30000),900000);
  let last=null;
  let lastWaitHeartbeat=0;
  while(Date.now()<deadline){
    last=await inject(tabId,crowdHumanCheckpointProbe,[]).catch(()=>null);
    if(last?.ready) return {...last,timeout:false};
    if(Date.now()-lastWaitHeartbeat>=15000){
      const live=await chrome.tabs.get(tabId).catch(()=>null);
      await heartbeat('waiting_human',live?.url||last?.url||'',null,currentCapability).catch(()=>{});
      lastWaitHeartbeat=Date.now();
    }
    await sleep(250);
  }
  return {...(last||{}),ok:false,ready:false,timeout:true};
}

async function waitRecipeHumanCheckpoint(tabId,options={},timeoutMs=600000,currentCapability='browser_recipe'){
  const deadline=Date.now()+Math.min(Math.max(Number(timeoutMs||600000),30000),900000);
  let last=null;
  let lastWaitHeartbeat=0;
  while(Date.now()<deadline){
    last=await inject(tabId,recipeDomAction,['human_probe',options||{}]).catch(()=>null);
    if(last?.ready) return {...last,timeout:false};
    if(Date.now()-lastWaitHeartbeat>=15000){
      const live=await chrome.tabs.get(tabId).catch(()=>null);
      await heartbeat('waiting_human',live?.url||last?.url||'',null,currentCapability).catch(()=>{});
      lastWaitHeartbeat=Date.now();
    }
    await sleep(300);
  }
  return {...(last||{}),ok:false,ready:false,timeout:true};
}

async function waitRecipePageState(tabId,options={},timeoutMs=15000){
  const deadline=Date.now()+Math.min(Math.max(Number(timeoutMs||15000),500),120000);
  let last=null;
  while(Date.now()<deadline){
    last=await inject(tabId,recipeDomAction,['page_state',options||{}]).catch(()=>null);
    if(last?.ok) return {...last,timeout:false};
    await sleep(350);
  }
  return {...(last||{}),ok:false,timeout:true,error:'wait_page_state_timeout'};
}

async function recipeStepPayload(job,step){
  const out={...(step?.options||{})};
  if(step?.selector && !out.selector_hints) out.selector_hints=[step.selector];
  if(step?.value!==undefined && step?.value!==null && out.value===undefined) out.value=step.value;
  if(step?.text!==undefined && step?.text!==null && out.value===undefined) out.value=step.text;
  const source=String(out.value_source||'');
  if(source.startsWith('local_credential.')){
    const creds=await crowdLocalCredentials(job);
    const key=source.split('.',2)[1]||'';
    if(!['username','email','password'].includes(key)) throw new Error('unsupported_local_credential_source');
    out.value=String(creds?.[key]||'');
    if(key==='password') out.allow_local_secret=true;
  }
  return out;
}

function crowdClassifyRegistrationPage(){
  const text=String(document.body?.innerText||'').replace(/\s+/g,' ').trim();
  const low=text.toLowerCase();
  const hasRegistrationForm=!!document.querySelector('form input[type="password"],form input[autocomplete="new-password"]');
  let state='unknown';
  if(/проверьте.{0,40}почт|подтвердите.{0,40}почт|ожидает.{0,50}подтвержден|письм.{0,60}подтвержден|verification email|confirm.{0,30}email|activation email|активац.{0,30}почт/.test(low)) state='email_verification_required';
  else if(/регистрац.{0,30}(заверш|успеш)|account.{0,20}created|registration.{0,20}(complete|successful)|welcome|добро пожаловать/.test(low) && !hasRegistrationForm) state='registered';
  else if(/выйти|logout|log out|профил|my account|личный кабинет/.test(low) && !hasRegistrationForm) state='registered';
  else if(hasRegistrationForm) state='registration_form';
  return {ok:true,url:location.href,title:document.title,state,text:text.slice(0,1600)};
}

function crowdGmailOpenVerificationEmail(payload){
  const domain=String(payload?.domain||'').replace(/^www\./,'').toLowerCase();
  const username=String(payload?.username||'').toLowerCase();
  const phrases=['подтверж','активац','confirm','verify','verification','activation'];
  const visible=el=>{ const r=el?.getBoundingClientRect?.(); return !!el && r && r.width>0 && r.height>0; };
  const rows=[...document.querySelectorAll('tr,div[role="main"] [role="link"],div[role="main"] [role="button"],div.zA')].filter(visible);
  let best=null;
  for(const row of rows){
    const text=String(row.innerText||row.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim();
    const low=text.toLowerCase();
    if(!text) continue;
    const domainHit=domain && low.includes(domain);
    const userHit=username && low.includes(username);
    const phraseHit=phrases.some(p=>low.includes(p));
    if((domainHit||userHit) && phraseHit){ best=row; break; }
    if(!best && (domainHit||userHit)) best=row;
  }
  if(!best) return {ok:false,error:'verification_email_not_found',domain};
  best.scrollIntoView({block:'center'}); best.click();
  return {ok:true,clicked:true,domain};
}

function crowdGmailExtractVerificationLink(payload){
  const domain=String(payload?.domain||'').replace(/^www\./,'').toLowerCase();
  const anchors=[...document.querySelectorAll('a[href]')];
  const candidates=[];
  for(const a of anchors){
    let href=String(a.href||a.getAttribute('href')||'').trim();
    if(!href) continue;
    try{
      const u=new URL(href,location.href);
      if(u.hostname==='www.google.com' && u.pathname==='/url' && u.searchParams.get('q')) href=u.searchParams.get('q');
    }catch{}
    let host='';
    try{ host=new URL(href,location.href).hostname.replace(/^www\./,'').toLowerCase(); }catch{}
    const text=String(a.innerText||a.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim().toLowerCase();
    const domainHit=!!domain && (host===domain || host.endsWith('.'+domain));
    if(!domainHit) continue;
    const intent=/confirm|verify|verification|activate|activation|account-confirm|подтверд|активац/.test((href+' '+text).toLowerCase());
    candidates.push({href,text,intent});
  }
  const pick=candidates.find(x=>x.intent)||candidates[0]||null;
  if(!pick) return {ok:false,error:'verification_link_not_found',domain};
  return {ok:true,href:pick.href,domain,intent:!!pick.intent};
}

function crowdVerifyAccountReady(){
  const text=String(document.body?.innerText||'').replace(/\s+/g,' ').trim();
  const low=text.toLowerCase();
  const pending=/ожидает.{0,50}подтвержден|письм.{0,50}подтвержден|confirm.{0,40}email|verify.{0,40}email|activation.{0,40}email|активац.{0,40}почт/.test(low);
  const logged=/выход|logout|log out|ваш профиль|your profile|ваша уч[её]тная запись|my account|личный кабинет/.test(low);
  const ready=!!(logged && !pending);
  return {ok:true,ready,pending_email:pending,logged_in:logged,url:location.href,title:document.title,text:text.slice(0,1200)};
}

function crowdPrefillPost(payload){
  const visible=el=>{ const r=el?.getBoundingClientRect?.(); return !!el && !el.disabled && r && r.width>0 && r.height>0; };
  const setValue=(el,value)=>{
    if(!el) return false;
    if(el.isContentEditable){ el.focus(); el.innerText=String(value||''); el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:String(value||'')})); return true; }
    const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;
    if(setter) setter.call(el,String(value||'')); else el.value=String(value||'');
    el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return true;
  };
  const title=[...document.querySelectorAll('input[name*="title" i],input[name*="subject" i],input[id*="title" i],input[id*="subject" i]')].find(visible)||null;
  const body=[...document.querySelectorAll('textarea[name*="message" i],textarea[name*="body" i],textarea[name*="description" i],textarea,[contenteditable="true"]')].find(visible)||null;
  if(!body) return {ok:false,error:'post_body_not_found',url:location.href};
  const titleFilled=title?setValue(title,payload.title||''):false;
  const bodyFilled=setValue(body,payload.text||'');
  return {ok:!!bodyFilled,url:location.href,title_filled:titleFilled,body_filled:bodyFilled};
}

function crowdSubmitPost(){
  const visible=el=>{ const r=el?.getBoundingClientRect?.(); return !!el && !el.disabled && r && r.width>0 && r.height>0; };
  const body=[...document.querySelectorAll('textarea[name*="message" i],textarea[name*="body" i],textarea[name*="description" i],textarea,[contenteditable="true"]')].find(visible)||null;
  const form=body?.closest('form')||[...document.querySelectorAll('form')].find(f=>f.querySelector('textarea,[contenteditable="true"]'))||null;
  if(!form) return {ok:false,error:'post_form_not_found'};
  const buttons=[...form.querySelectorAll('button[type="submit"],input[type="submit"],button')].filter(visible);
  const preferred=buttons.find(b=>/опубликов|размест|создать тему|отправ|post thread|create thread|submit|publish/i.test(String(b.innerText||b.value||b.getAttribute('aria-label')||'')))||buttons[0];
  if(!preferred) return {ok:false,error:'post_submit_not_found'};
  preferred.scrollIntoView({block:'center'}); preferred.click();
  return {ok:true,clicked:true,url:location.href};
}

function crowdVerifyPost(payload){
  const text=String(document.body?.innerText||'').replace(/\s+/g,' ').trim();
  const html=String(document.documentElement?.innerHTML||'');
  const title=String(payload?.title||'').trim();
  const target=String(payload?.target_url||'').trim();
  const start=String(payload?.start_url||'').replace(/\/$/,'');
  const current=String(location.href||'').replace(/\/$/,'');
  const evidence=!!((title && text.toLowerCase().includes(title.toLowerCase().slice(0,80))) || (target && (text.includes(target)||html.includes(target))));
  const navigated=!!current && current!==start;
  return {ok:true,url:location.href,verified:!!(evidence&&navigated),evidence,navigated,title:document.title};
}

async function browserProgress(job,event,stepIndex=null,payload={}){
  return await call('/api/browser-gateway/transport/browser-progress',{method:'POST',body:JSON.stringify({job_id:job.job_id,account_id:job.account_id,event,step_index:stepIndex,payload})});
}
async function saveScreenshotArtifact(job,stepIndex,dataUrl){
  const m=String(dataUrl||'').match(/^data:(image\/(?:png|jpeg));base64,(.+)$/); if(!m) throw new Error('Screenshot format invalid');
  return await call('/api/browser-gateway/transport/artifact',{method:'POST',body:JSON.stringify({job_id:job.job_id,account_id:job.account_id,image_base64:m[2],mime:m[1],step_index:stepIndex})});
}
async function waitForSelector(tabId,selector,timeout=15000){
  const deadline=Date.now()+Math.min(Math.max(Number(timeout||15000),500),60000);
  while(Date.now()<deadline){ const r=await inject(tabId,findSafeElement,[selector]).catch(()=>null); if(r?.ok) return r; await sleep(400); }
  throw new Error('wait_for timeout');
}
async function executeBrowserJob(job){
  const crowd=String(job?.capability||'').startsWith('crowd_');
  if(['WRITE','PUBLISH','SENSITIVE'].includes(String(job.risk_level||'READ')) && !job.confirmed) throw new Error('Owner confirmation required');
  await browserProgress(job,'job_started'); await heartbeat('working','',null,job.capability);
  let tab=null;
  try{
    let startIndex=0;
    if(job.current_step!==null && job.current_step!==undefined && Number.isFinite(Number(job.current_step))){
      startIndex=Math.min((job.steps||[]).length,Number(job.current_step)+1);
    }
    const previousStep=startIndex>0 ? (job.steps||[])[startIndex-1] : null;
    if(crowd && previousStep?.action==='crowd_human_checkpoint'){
      tab=await recoverCrowdHandoff(job);
      if(!tab?.id) throw new Error('crowd_handoff_tab_missing');
      // Gateway 3 migration: old 2.x jobs could report filled=true without
      // proving that the real visible fields retained their values. When such
      // a job is resumed from waiting_human, replay the prefill before letting
      // the user spend another CAPTCHA or submitting an empty form.
      const steps=job.steps||[];
      const prefillIndex=steps.findIndex(s=>s?.action==='crowd_prefill_registration');
      if(prefillIndex>=0){
        const prior=(job.results||[]).find(r=>Number(r?.step_index)===prefillIndex)?.result||{};
        if(prior?.verified_after_fill!==true) startIndex=prefillIndex;
      }
    }
    for(let i=startIndex;i<(job.steps||[]).length;i++){
      const step=job.steps[i]||{}; let result={ok:true};
      if(step.action==='open_url'){
        if(!step.url) throw new Error('open_url requires url');
        if(!adapterForUrl(step.url) && !crowd) throw new Error('Domain is not allowed by Browser Gateway adapter registry');
        if(tab?.id){
          tab=await chrome.tabs.update(tab.id,{url:step.url,active:!!crowd});
        }else{
          tab=await ensureAutomationTab(step.url,false);
        }
        await waitTab(tab.id,step.timeout_ms||22000);
        if(crowd) await installCrowdKeepalive(tab.id);
        result={ok:true,url:(await chrome.tabs.get(tab.id)).url};
      }else if(step.action==='get_tabs'){
        const tabs=await chrome.tabs.query({}); result={ok:true,tabs:tabs.map(t=>({id:t.id,title:t.title,url:t.url,active:t.active}))};
      }else if(step.action==='activate_tab'){
        const id=Number(step.value); tab=await chrome.tabs.update(id,{active:true}); result={ok:true,id};
      }else if(step.action==='close_tab'){
        const id=Number(step.value||tab?.id); if(id) await chrome.tabs.remove(id); result={ok:true,id}; tab=null;
      }else{
        if(!tab?.id) throw new Error('BORIS isolated job has no dedicated tab; refusing to touch owner active tab');
        if(step.action==='get_current_url') result={ok:true,url:(await chrome.tabs.get(tab.id)).url};
        else if(step.action==='get_page_title') result={ok:true,title:(await chrome.tabs.get(tab.id)).title};
        else if(step.action==='read_page') result=await inject(tab.id,safeReadPage,[]);
        else if(step.action==='read_avito_search'){ await sleep(900); result=await inject(tab.id,readAvitoSearch,[]); }
        else if(step.action==='read_avito_pro_items'){ await sleep(900); result=await inject(tab.id,readAvitoProItems,[]); }
        else if(step.action==='find_element') result=await inject(tab.id,findSafeElement,[step.selector]);
        else if(step.action==='click') result=await inject(tab.id,clickSafe,[step.selector]);
        else if(step.action==='input_text') result=await inject(tab.id,inputSafe,[step.selector,step.value??step.text??'']);
        else if(step.action==='select') result=await inject(tab.id,selectSafe,[step.selector,step.value]);
        else if(step.action==='scroll') result=await inject(tab.id,scrollSafe,[step.selector,step.value]);
        else if(step.action==='wait_for') result=await waitForSelector(tab.id,step.selector,step.timeout_ms);
        else if(step.action==='demo_pause'){
          const ms=Math.max(0,Math.min(Number(step?.options?.ms??step.value??step.timeout_ms??700),10000));
          await sleep(ms); result={ok:true,paused_ms:ms};
        }else if(step.action==='highlight_element'){
          const payload=await recipeStepPayload(job,step); result=await inject(tab.id,recipeDomAction,['highlight_element',payload]);
        }else if(step.action==='record_start'){
          result=await startTabRecording(tab,job,{...(step.options||{}),step_index:i});
        }else if(step.action==='record_marker'){
          const payload=step.options||{};
          result=await recorderMessage({type:'record_marker',label:String(payload.label||step.text||step.value||`step-${i}`),meta:{step_index:i,action:payload.action||null,url:(await chrome.tabs.get(tab.id)).url}});
        }else if(step.action==='record_stop'){
          result=await stopTabRecording(step.options||{});
        }else if(step.action==='inspect_form'){
          const payload=await recipeStepPayload(job,step); result=await inject(tab.id,recipeDomAction,['inspect_form',payload]);
        }else if(step.action==='fill_semantic'){
          const payload=await recipeStepPayload(job,step); result=await inject(tab.id,recipeDomAction,['fill_semantic',payload]);
          if(result?.ok && String(payload?.value_source||'')==='local_credential.username' && result?.value){
            const platform=String(job?.metadata?.platform||job?.domain||'crowd').toLowerCase().replace(/[^a-z0-9_.-]/g,'_').slice(0,100)||'crowd';
            const key='crowd_credentials:'+platform;
            const got=await chrome.storage.local.get([key]);
            const row=got?.[key]||{};
            if(String(row.username||'')!==String(result.value)) await chrome.storage.local.set({[key]:{...row,username:String(result.value),updated_at:new Date().toISOString()}});
          }
        }else if(step.action==='check_semantic'){
          const payload=await recipeStepPayload(job,step); result=await inject(tab.id,recipeDomAction,['check_semantic',payload]);
        }else if(step.action==='submit_semantic'){
          const payload=await recipeStepPayload(job,step); result=await inject(tab.id,recipeDomAction,['submit_semantic',payload]);
          if(result?.ok && Number(payload.after_click_wait_ms||0)>0) await sleep(Math.min(Number(payload.after_click_wait_ms),10000));
        }else if(step.action==='assert_value'){
          const payload=await recipeStepPayload(job,step); result=await inject(tab.id,recipeDomAction,['assert_value',payload]);
        }else if(step.action==='wait_page_state'){
          const payload=await recipeStepPayload(job,step); result=await waitRecipePageState(tab.id,payload,step.timeout_ms);
        }else if(step.action==='extract_links'){
          const payload=await recipeStepPayload(job,step); result=await inject(tab.id,recipeDomAction,['extract_links',payload]);
        }else if(step.action==='human_checkpoint'){
          const payload=await recipeStepPayload(job,step);
          const live=await chrome.tabs.get(tab.id);
          await rememberCrowdHandoff(job,live); await installCrowdKeepalive(tab.id);
          if(live?.windowId) await chrome.windows.update(live.windowId,{focused:true,state:'normal'}).catch(()=>{});
          await browserProgress(job,'human_checkpoint_ready',i,{platform:String(job?.metadata?.platform||''),url:live?.url||'',auto_resume:true,recipe_runtime:true});
          await heartbeat('waiting_human',live?.url||'',null,job.capability);
          payload.start_url=payload.start_url||live?.url||'';
          const human=await waitRecipeHumanCheckpoint(tab.id,payload,step.timeout_ms||600000,job.capability);
          if(!human?.ready) return;
          await browserProgress(job,'job_started'); await heartbeat('working',human?.url||live?.url||'',null,job.capability);
          result={ok:true,manual_checkpoint_completed:true,auto_resume:true,recipe_runtime:true,...human};
        }else if(step.action==='crowd_prefill_registration'){
          const creds=await crowdLocalCredentials(job);
          const filled=await inject(tab.id,crowdPrefillRegistration,[{username:creds.username,email:creds.email,password:creds.password}]);
          if(filled?.username_value && filled.username_value!==creds.username){
            const platform=String(job?.metadata?.platform||job?.domain||'crowd').toLowerCase().replace(/[^a-z0-9_.-]/g,'_').slice(0,100)||'crowd';
            const key='crowd_credentials:'+platform;
            const normalized={...creds,username:String(filled.username_value),updated_at:new Date().toISOString()};
            await chrome.storage.local.set({[key]:normalized});
          }
          result={ok:filled?.ok!==false,url:filled?.url||'',credentials_local_only:true,username_filled:!!filled?.username,email_filled:!!filled?.email,password_fields:Number(filled?.password_fields||0),username_field:!!filled?.username_field,email_field:!!filled?.email_field,password_field_count:Number(filled?.password_field_count||0),terms_checked:Number(filled?.terms_checked||0),complete:!!filled?.complete,verified_after_fill:!!filled?.verified_after_fill,error:filled?.error||null};
          if(!result.ok || !result.complete || !result.verified_after_fill) throw new Error(result.error||'registration_fields_incomplete');
        }else if(step.action==='crowd_human_checkpoint'){
          const live=await chrome.tabs.get(tab.id);
          await rememberCrowdHandoff(job,live);
          await installCrowdKeepalive(tab.id);
          await inject(tab.id,crowdResetHumanCheckpointProbe,[]).catch(()=>{});
          if(live?.windowId) await chrome.windows.update(live.windowId,{focused:true,state:'normal'}).catch(()=>{});
          await browserProgress(job,'human_checkpoint_ready',i,{platform:String(job?.metadata?.platform||''),url:live?.url||'',auto_resume:true});
          await heartbeat('waiting_human',live?.url||'',null,job.capability);
          const human=await waitCrowdHumanCheckpoint(tab.id,600000,job.capability);
          if(!human?.ready) return;
          await browserProgress(job,'job_started');
          await heartbeat('working',live?.url||'',null,job.capability);
          result={ok:true,manual_checkpoint_completed:true,auto_resume:true,url:human?.url||live?.url||''};
        }else if(step.action==='crowd_submit_registration'){
          let classified=null;
          let clicked=null;
          for(let submitTry=0;submitTry<4;submitTry++){
            clicked=await inject(tab.id,crowdSubmitRegistration,[]);
            if(!clicked?.ok) throw new Error(clicked?.error||'registration_submit_failed');
            await sleep(900);
            classified=await inject(tab.id,crowdClassifyRegistrationPage,[]).catch(()=>({ok:false,state:'unknown'}));
            if(classified?.state!=='registration_form') break;
            const probe=await inject(tab.id,crowdHumanCheckpointProbe,[]).catch(()=>null);
            if(!probe?.challenge_visible) throw new Error('registration_form_still_present');
            const live=await chrome.tabs.get(tab.id);
            await rememberCrowdHandoff(job,live);
            await installCrowdKeepalive(tab.id);
            await inject(tab.id,crowdResetHumanCheckpointProbe,[]).catch(()=>{});
            await browserProgress(job,'human_checkpoint_ready',Math.max(0,i-1),{platform:String(job?.metadata?.platform||''),url:live?.url||'',retry:true,auto_resume:true});
            await heartbeat('waiting_human',live?.url||'',null,job.capability);
            if(live?.windowId) await chrome.windows.update(live.windowId,{focused:true,state:'normal'}).catch(()=>{});
            const human=await waitCrowdHumanCheckpoint(tab.id,600000,job.capability);
            if(!human?.ready) return;
            await browserProgress(job,'job_started');
            await heartbeat('working',live?.url||'',null,job.capability);
          }
          if(classified?.state==='registration_form') throw new Error('registration_form_still_present');
          result={ok:true,clicked:true,url:classified?.url||'',registration_state:classified?.state||'unknown',title:classified?.title||'',page_text:classified?.text||''};
          if(tab?.windowId) await chrome.windows.update(tab.windowId,{focused:false,state:'minimized'}).catch(()=>{});
        }else if(step.action==='crowd_gmail_open_verification_email'){
          const opened=await inject(tab.id,crowdGmailOpenVerificationEmail,[{domain:job?.metadata?.source_domain||job?.domain||'',username:job?.metadata?.username||''}]);
          if(!opened?.ok) throw new Error(opened?.error||'verification_email_not_found');
          await sleep(1200); result=opened;
        }else if(step.action==='crowd_gmail_follow_verification_link'){
          const link=await inject(tab.id,crowdGmailExtractVerificationLink,[{domain:job?.metadata?.source_domain||job?.domain||''}]);
          if(!link?.ok || !link?.href) throw new Error(link?.error||'verification_link_not_found');
          tab=await chrome.tabs.update(tab.id,{url:link.href,active:false});
          await waitTab(tab.id,step.timeout_ms||22000);
          result={ok:true,url:(await chrome.tabs.get(tab.id)).url,verification_link_followed:true};
        }else if(step.action==='crowd_verify_account_ready'){
          result=await inject(tab.id,crowdVerifyAccountReady,[]);
          if(!result?.ready) throw new Error(result?.pending_email?'email_verification_still_pending':'account_not_ready_after_verification');
        }else if(step.action==='crowd_prefill_post'){
          const filled=await inject(tab.id,crowdPrefillPost,[{title:step.value||job?.metadata?.post_title||'',text:step.text||job?.metadata?.post_text||''}]);
          result={ok:filled?.ok!==false,url:filled?.url||'',title_filled:!!filled?.title_filled,body_filled:!!filled?.body_filled,error:filled?.error||null};
        }else if(step.action==='crowd_submit_post'){
          const clicked=await inject(tab.id,crowdSubmitPost,[]);
          if(!clicked?.ok) throw new Error(clicked?.error||'post_submit_failed');
          await sleep(3000);
          result={ok:true,clicked:true,url:(await chrome.tabs.get(tab.id)).url};
        }else if(step.action==='crowd_verify_post'){
          result=await inject(tab.id,crowdVerifyPost,[{title:job?.metadata?.post_title||'',target_url:job?.metadata?.target_url||'',start_url:job?.metadata?.post_url||''}]);
        }else if(step.action==='screenshot' || step.action==='screenshot_element'){
          await inject(tab.id,privacyMask,[step.redact_selectors||[]]);
          await chrome.tabs.update(tab.id,{active:true}); await sleep(250);
          const dataUrl=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'png'});
          await inject(tab.id,privacyUnmask,[]).catch(()=>{});
          const art=await saveScreenshotArtifact(job,i,dataUrl); result={ok:true,screenshot_path:art.path,privacy_guard:true};
        }else if(step.action==='upload_file' || step.action==='download_file'){
          throw new Error(`${step.action} requires an approved site adapter and is not available as a raw primitive`);
        }else throw new Error('Unsupported action '+step.action);
      }
      if(result?.ok===false) throw new Error(result.error||('Step failed: '+step.action));
      await browserProgress(job,'step_result',i,result); await heartbeat('working',tab?.url||'',null,job.capability);
    }
    await browserProgress(job,'job_completed'); await heartbeat('idle','',null,null);
    if(crowd){ await clearCrowdHandoff(job); await releaseAutomationTab(); }
  }catch(e){ await browserProgress(job,'job_failed',null,{error:String(e?.message||e)}).catch(()=>{}); await heartbeat('error',tab?.url||'',null,job.capability); }
}

async function executeJob(job){
  if(job?.kind==='browser') return await executeBrowserJob(job);
  await progress(job,'job_started');
  await heartbeat('working');
  let tab=null;
  const items=(job.items||[]);
  try{
    for(let i=0;i<items.length;i++){
      const item=items[i];
      const runStatus=String(item.run_status||'');
      const attempts=Number(item.attempts||0);
      if((job.mode==='scan' && runStatus==='scanned') || runStatus==='verified' || runStatus==='skipped' || (runStatus==='failed' && attempts>=3)) continue;
      const itemStartedAt=Date.now();
      try{
        tab=await openEditTab(item.id);
        await captureDiagnosticFrame(job,tab,i*20+1,'editor_open');
        const scan=await inject(tab.id,scanFields,[]);
        if(!scan || scan.blocked) throw new Error(scan?.reason || 'Не удалось прочитать характеристики');
        await progress(job,'item_scanned',item.id,{index:i+1,fields:scan.fields||[]});

        if(job.mode==='scan' || !Object.keys(item.changes||{}).length){
          await sleep(900+Math.floor(Math.random()*700));
          continue;
        }

        const allChanges=item.changes||{};
        const rules=job.rules||{};
        const photoKeys=Object.keys(allChanges).filter(k=>/фото|phot/i.test(String(rules?.[k]?.label||k)));
        const regularChanges={...allChanges};
        for(const k of photoKeys) delete regularChanges[k];
        const changedFields=[];
        let expectedPhotos=0;

        for(const k of photoKeys){
          const pack=await fetchPhotoPayloads(String(allChanges[k]||''));
          if(!pack.files.length) throw new Error('Не удалось получить фотографии из медиатеки BORIS');
          const photoResult=await inject(tab.id,setPhotoFiles,[pack.files]);
          if(!photoResult?.ok) throw new Error(photoResult?.error||'Поле фотографий Avito не найдено');
          expectedPhotos=Math.max(expectedPhotos,photoResult.count||pack.files.length);
          changedFields.push(k);
        }

        let edit={saved:false,changed_fields:[],skipped_fields:[]};
        if(Object.keys(regularChanges).length){
          const requestedLabels=Object.keys(regularChanges).map(k=>String(rules?.[k]?.label||k).replace(/^custom:/,''));
          const surfaceScan=await ensureFieldSurface(tab.id,requestedLabels);
          if(surfaceScan?.fields?.length) await progress(job,'item_scanned',item.id,{index:i+1,fields:surfaceScan.fields});
          edit=await inject(tab.id,applyChanges,[regularChanges,rules]);
          await captureDiagnosticFrame(job,tab,i*20+2,'after_field_change');
          if(!edit) throw new Error('Редактор не вернул результат');
          if(edit.blocked) throw new Error(edit.reason||'Avito остановил редактирование');
          changedFields.push(...(edit.changed_fields||[]));
        }
        if(!changedFields.length){
          await progress(job,'item_skipped',item.id,{index:i+1,reason:edit?.error||'Ни одно выбранное поле не найдено',diagnostics:edit?.diagnostics||[]});
          continue;
        }
        await captureDiagnosticFrame(job,tab,i*20+3,'before_save');
        let saveResult=await inject(tab.id,saveCurrentForm,[]);
        const saveTrail=[];
        let promoClicks=0;
        for(let backTry=0;!saveResult?.saved && backTry<10;backTry++){
          if(saveResult?.promo_step) promoClicks += 1;
          saveTrail.push({try:backTry+1,url:tab.url||'',action:saveResult?.action||'',promo:!!saveResult?.promo_step,wait:!!saveResult?.promo_wait,error:saveResult?.error||''});
          await sleep(saveResult?.promo_wait ? 1000 : saveResult?.promo_step ? 900 : saveResult?.main_saved ? 2600 : saveResult?.nested_saved ? 900 : 900);
          await chrome.tabs.get(tab.id).then(t=>{ if(t?.url) tab=t; }).catch(()=>{});
          saveResult=await inject(tab.id,saveCurrentForm,[]).catch(()=>null);
          if(backTry<6) await captureDiagnosticFrame(job,tab,i*20+4+backTry,`save_step_${backTry+1}`);
        }
        if(!saveResult?.saved){
          const tail=saveTrail.slice(-5).map(x=>`${x.try}:${x.action||x.error||x.url}`).join(' | ');
          const diag=saveResult?.diagnostics ? ` · diag=${JSON.stringify(saveResult.diagnostics).slice(0,1800)}` : '';
          throw new Error((saveResult?.error||'Не удалось пройти все шаги сохранения объявления')+(tail?` · ${tail}`:'')+diag);
        }
        await progress(job,'item_changed',item.id,{index:i+1,changed_fields:changedFields,promo_clicks:promoClicks,save_trace:saveTrail.slice(-8),terminal_url:saveResult?.url||tab.url||''});
        await sleep(1200);

        // Verification must re-read the saved Avito form, but Avito often sends the
        // tab to a promo/profile screen after Save. Reloading that terminal screen
        // gives a false failure. Prefer history-back to the just-saved editor, then
        // fall back to reopening the editor explicitly.
        let afterScan=null;
        let verifyProbe=null;
        const tryVerifyCurrent=async()=>{
          const deadline=Date.now()+9000;
          while(Date.now()<deadline){
            const probe=await inject(tab.id,probePage,[]).catch(()=>null);
            verifyProbe=probe;
            if(probe?.blocked) throw new Error(probe.reason||'Avito blocked');
            if(probe?.loginRequired) throw new Error('Avito просит войти в аккаунт в Chrome');
            if(probe?.looksEditable){
              const scan=await inject(tab.id,scanFields,[]).catch(()=>null);
              if(scan?.fields?.length) return scan;
            }
            await sleep(500);
          }
          return null;
        };
        afterScan=await tryVerifyCurrent();
        if(!afterScan){
          try{
            await chrome.tabs.goBack(tab.id);
            await waitTab(tab.id,16000).catch(()=>{});
            await sleep(700);
            afterScan=await tryVerifyCurrent();
          }catch(_e){}
        }
        if(!afterScan){
          try{
            tab=await openEditTab(item.id);
            afterScan=await inject(tab.id,scanFields,[]).catch(()=>null);
          }catch(_e){}
        }
        const regularChanged=changedFields.filter(k=>!photoKeys.includes(k));
        if(regularChanged.length){
          const verifyLabels=regularChanged.map(k=>String(rules?.[k]?.label||k).replace(/^custom:/,''));
          const nestedScan=await ensureFieldSurface(tab.id,verifyLabels);
          if(nestedScan?.fields?.length) afterScan=nestedScan;
        }
        const verify=regularChanged.length ? verifyScannedChanges(regularChanges,regularChanged,rules,afterScan?.fields||[]) : {ok:true,verified_fields:[]};
        const photoVerify=photoKeys.length ? await inject(tab.id,verifyPhotoCount,[expectedPhotos]) : {ok:true};
        if(verify?.ok && photoVerify?.ok){
          await progress(job,'item_verified',item.id,{index:i+1,verified_fields:[...(verify.verified_fields||[]),...photoKeys],duration_ms:Date.now()-itemStartedAt});
        }else{
          await progress(job,'item_failed',item.id,{index:i+1,error:'Проверка после сохранения не подтвердила все изменённые поля',duration_ms:Date.now()-itemStartedAt});
        }
        await heartbeat('working',tab.url||'',true);
        await sleep(1400+Math.floor(Math.random()*1400));
      }catch(e){
        const msg=String(e?.message||e);
        if(/войти|captcha|капч|доступ ограничен|blocked/i.test(msg)){
          await progress(job,'job_blocked',item.id,{error:msg});
          throw e;
        }
        await progress(job,'item_failed',item.id,{index:i+1,error:msg});
      }
    }
    await progress(job,'job_completed');
    await heartbeat('idle','',true);
  }catch(e){ await heartbeat('blocked',tab?.url||'',false); }
  finally{ await releaseAutomationTab(); }
}

function openFieldSurface(labels){
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const norm=s=>clean(s).toLowerCase();
  const wanted=(labels||[]).map(norm).filter(Boolean);
  const current=[...document.querySelectorAll('label,legend,[class*="label"],[class*="title"],h1,h2,h3')].map(e=>norm(e.innerText));
  if(wanted.some(x=>current.includes(x))) return {clicked:false,already:true};

  // Most Avito category-specific fields live behind the «Характеристики» row.
  const candidates=[...document.querySelectorAll('button,a,[role="button"],[data-marker]')];
  const trigger=candidates.find(e=>{
    const t=norm(e.innerText||e.getAttribute('aria-label')||e.getAttribute('title')||'');
    return t==='характеристики' || t.startsWith('характеристики ' ) || t.includes('характеристики объявления');
  });
  if(trigger){ trigger.click(); return {clicked:true,target:'characteristics'}; }
  return {clicked:false};
}

function probePage(){
  const text=(document.body?.innerText||'').toLowerCase();
  const url=location.href.toLowerCase();
  const blocked=text.includes('доступ ограничен')||text.includes('проблема с ip')||text.includes('captcha')||text.includes('капча');
  const loginRequired=url.includes('/login')||text.includes('войти или зарегистрироваться');
  const save=[...document.querySelectorAll('button')].some(b=>/сохранить|продолжить|опубликовать/i.test((b.innerText||'').trim()));
  const titleInput=[...document.querySelectorAll('input,textarea')].some(el=>{
    const own=(el.getAttribute('aria-label')||el.getAttribute('placeholder')||'').toLowerCase();
    if(own.includes('название объявления')) return true;
    if(el.id){ const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if((l?.innerText||'').toLowerCase().includes('название объявления')) return true; }
    return false;
  });
  const hasEditorFields=text.includes('название объявления') && (text.includes('цена') || text.includes('описание'));
  const editUrl=/\/items\/edit\/\d+/.test(url)||/\/profile\/items\/\d+\/edit/.test(url)||/\/edit\/\d+/.test(url);
  return {blocked,reason:blocked?'Avito показал защитную проверку/ограничение':null,loginRequired,looksEditable:editUrl&&(titleInput||hasEditorFields||save),url:location.href};
}

function scanFields(){
  const text=(document.body?.innerText||'').toLowerCase();
  if(text.includes('доступ ограничен')||text.includes('captcha')||text.includes('капча')) return {blocked:true,reason:'Avito показал защитную проверку'};
  const fields=[]; const seen=new Set();
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const labelFor=(el)=>{
    if(el.id){ const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(l) return clean(l.innerText).slice(0,140); }
    const parent=el.closest('label'); if(parent) return clean(parent.innerText).slice(0,140);
    const wrap=el.closest('[data-marker],fieldset,[class*="field"],[class*="input"],[class*="form"]');
    if(wrap){ const l=wrap.querySelector('label,legend,[class*="label"],[class*="title"]'); if(l) return clean(l.innerText).slice(0,140); }
    return clean(el.getAttribute('aria-label')||el.name||el.placeholder||'').slice(0,140);
  };
  const add=(f)=>{ const label=clean(f.label); if(!label||label.length>140) return; const key=label.toLowerCase(); if(seen.has(key)) return; seen.add(key); fields.push({...f,label}); };

  // Text/select/checkbox controls. Radio buttons are handled as groups below,
  // otherwise Avito option labels such as «Распашные» become fake fields.
  for(const el of document.querySelectorAll('input,select,textarea,[role="combobox"]')){
    const type=(el.getAttribute('type')||el.getAttribute('role')||el.tagName||'').toLowerCase();
    if(['hidden','submit','button','file','radio'].includes(type)) continue;
    const label=labelFor(el); if(!label) continue;
    let value='';
    if(el.tagName==='SELECT') value=el.options?.[el.selectedIndex]?.text||el.value||'';
    else if(type==='checkbox') value=el.checked ? 'Да' : 'Нет';
    else value=el.value||el.getAttribute('aria-valuetext')||'';
    let options=[];
    const wrap=el.closest('fieldset,[data-marker],[class*="field"],[class*="input"],[class*="form"]')||el.parentElement;
    if(el.tagName==='SELECT') options=[...el.options].map(x=>clean(x.text)).filter(Boolean);
    else if(wrap) options=[...wrap.querySelectorAll('option,[role="option"]')].map(x=>clean(x.innerText)).filter(x=>x&&x.length<100);
    add({label,type,value:clean(value),options:[...new Set(options)].slice(0,120)});
  }

  const radios=[...document.querySelectorAll('input[type="radio"]')];
  const groups=new Map();
  for(const r of radios){
    const key=r.name||r.getAttribute('data-marker')||`radio-${r.value}`;
    if(!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(r);
  }
  const inferRadioLabel=(opts,root,name)=>{
    const norm=opts.map(x=>clean(x).toLowerCase());
    const has=(...vals)=>vals.some(v=>norm.includes(v.toLowerCase()));
    if(has('Новое')&&has('Б/у')) return 'Состояние';
    if(has('Уголок')&&has('Ограждение')) return 'Тип душевого ограждения';
    if(has('Прозрачное стекло')&&has('Матовое стекло')) return 'Тип стекла';
    if(has('Раздвижные')&&has('Распашные')) return 'Тип дверей';
    if(norm.some(x=>x.startsWith('В наличии'))&&norm.some(x=>x.startsWith('Под заказ'))) return 'Наличие';
    if(has('Звонки и сообщения')&&has('Только звонки')&&has('Только сообщения')) return 'Способ связи';
    const candidates=[...root.querySelectorAll('legend,h1,h2,h3,h4,[class*="title"],[class*="label"]')]
      .map(x=>clean(x.innerText)).filter(x=>x&&x.length<100&&!opts.some(o=>clean(o)===x));
    if(candidates.length) return candidates[0];
    return /^params?\[/i.test(name||'') ? '' : clean(name||'');
  };
  for(const [name,group] of groups.entries()){
    if(group.length<2) continue;
    const opts=group.map(r=>labelFor(r)).filter(Boolean);
    if(opts.length<2) continue;
    let root=group[0].parentElement;
    for(let depth=0;root&&depth<7;depth++,root=root.parentElement){ if(group.every(r=>root.contains(r))) break; }
    root=root||group[0].parentElement||document.body;
    const label=inferRadioLabel(opts,root,name);
    if(!label) continue;
    const checked=group.find(r=>r.checked);
    const value=checked ? labelFor(checked) : '';
    add({label,type:'radio',value:clean(value),options:[...new Set(opts)].slice(0,120)});
  }
  return {blocked:false,fields};
}

async function setPhotoFiles(files){
  try{
    const input=document.querySelector('input[type="file"][accept*="image"],input[type="file"]');
    if(!input) return {ok:false,error:'Поле загрузки фотографий не найдено'};
    const dt=new DataTransfer();
    for(const item of files||[]){
      const bin=atob(item.base64||'');
      const bytes=new Uint8Array(bin.length);
      for(let i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
      dt.items.add(new File([bytes],item.name||'boris.jpg',{type:item.type||'image/jpeg'}));
    }
    if(!dt.files.length) return {ok:false,error:'Файлы фотографий пустые'};
    input.files=dt.files;
    input.dispatchEvent(new Event('input',{bubbles:true}));
    input.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise(r=>setTimeout(r,1800));
    return {ok:true,count:dt.files.length};
  }catch(e){ return {ok:false,error:String(e?.message||e)}; }
}

async function saveCurrentForm(){
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const visible=(el)=>{ try{ const r=el.getBoundingClientRect(); const st=getComputedStyle(el); return r.width>0&&r.height>0&&st.visibility!=='hidden'&&st.display!=='none'; }catch{return false;} };
  const findAction=(re)=>{
    const textOf=el=>clean(el.innerText||el.getAttribute('aria-label')||el.getAttribute('title')||'');
    // First match genuine interactive controls. Avito often wraps a real button
    // in a data-marker div whose innerText is identical; choosing the wrapper
    // makes .click() a no-op.
    const interactive=[...document.querySelectorAll('button,a,[role="button"],div[tabindex]')].filter(visible);
    const direct=interactive.find(el=>re.test(textOf(el)));
    if(direct) return direct;
    const wrappers=[...document.querySelectorAll('[data-marker],span')].filter(visible);
    const hit=wrappers.find(el=>re.test(textOf(el)));
    if(!hit) return null;
    const child=hit.querySelector?.('button,a,[role="button"],div[tabindex]');
    return (child&&visible(child)?child:null) || hit.closest('button,a,[role="button"],div[tabindex]') || hit;
  };
  const pageText=clean(document.body?.innerText||'');
  const hasMainForm=!!document.querySelector('input[name="title"],textarea[name="description"],[data-marker*="title"],[data-marker*="price"]')
    || (/название объявления/i.test(pageText) && /цена/i.test(pageText));
  const nestedCharacteristics=/характеристики/i.test(pageText) && !hasMainForm;
  const promoUrl=/\/cpxpromo\/|cpxpromo=|\/pro\/performance|performance\?/i.test(location.href);

  // Avito can render these actions as nested spans/divs rather than native buttons.
  // They are mandatory post-save wizard steps and must be exhausted before verify.
  const promo=findAction(/^(продолжить без изменений|продолжить без услуг)$/i);
  if(promo){
    promo.scrollIntoView({block:'center',inline:'center'});
    promo.click();
    return {saved:false,navigating_back:true,promo_step:true,action:clean(promo.innerText||'')};
  }

  // Avito can open category characteristics in a modal/drawer over the main edit form.
  // In that state the main form remains in DOM, so hasMainForm alone cannot tell us
  // that we must first commit the nested surface. Prefer visible dialog-local actions.
  const dialog=[...document.querySelectorAll('[role=dialog],[aria-modal=true],[class*=modal],[class*=drawer],[class*=sheet]')].find(visible);
  const dialogAction=(re)=>{
    if(!dialog) return null;
    const nodes=[...dialog.querySelectorAll('button,a,[role=button],[data-marker],div[tabindex],span')].filter(visible);
    const hit=nodes.find(el=>re.test(clean(el.innerText||el.getAttribute('aria-label')||el.getAttribute('title')||'')));
    return hit ? (hit.closest('button,a,[role=button],[data-marker],div[tabindex]')||hit) : null;
  };
  const nestedSave=dialogAction(/^(готово|применить|сохранить|сохранить изменения|продолжить)$/i);
  if(nestedSave){
    const txt=clean(nestedSave.innerText||nestedSave.getAttribute('aria-label')||'');
    nestedSave.scrollIntoView({block:'center',inline:'center'}); nestedSave.click();
    return {saved:false,navigating_back:true,nested_saved:true,action:txt||'nested_commit'};
  }

  // Avito may append an icon/technical text (for example "timing") inside
  // the visible Save button. Match the semantic prefix, not only pristine innerText.
  const save=findAction(/^сохранить(?: изменения)?(?:\s+(?:timing|arrow_forward|chevron_right))?$/i)
    || findAction(/^сохранить изменения\b/i)
    || findAction(/^(сохранить и продолжить|продолжить|опубликовать|готово|применить)(?:\s+.*)?$/i);
  if(save){
    const saveText=clean(save.innerText||save.getAttribute('aria-label')||save.getAttribute('title')||'');
    const disabled=!!save.disabled || save.getAttribute('aria-disabled')==='true';
    if(disabled) return {saved:false,error:'Кнопка сохранения Avito заблокирована',diagnostics:{action:saveText,disabled:true,url:location.href}};
    save.scrollIntoView({block:'center',inline:'center'});
    // Fire a full pointer/mouse sequence on the real control. React-based Avito
    // forms can ignore HTMLElement.click() on a nested marker wrapper.
    for(const type of ['pointerdown','mousedown','pointerup','mouseup','click']){
      try{
        const E=(type.startsWith('pointer') && typeof PointerEvent!=='undefined') ? PointerEvent : MouseEvent;
        save.dispatchEvent(new E(type,{bubbles:true,cancelable:true,view:window,pointerType:'mouse'}));
      }catch(_e){}
    }
    try{ save.focus({preventScroll:true}); }catch{}
    save.dataset.borisSaveClicked=String(Date.now());
    if(nestedCharacteristics) return {saved:false,navigating_back:true,nested_saved:true,action:saveText};
    return {saved:false,navigating_back:true,main_saved:true,action:saveText};
  }

  // If Avito is on a known promo page but UI is still hydrating, do not mark the job saved.
  // The caller will retry after a short wait and click the required action when it appears.
  if(promoUrl || /продолжить без изменений|продолжить без услуг|заметный внешний вид/i.test(pageText)){
    return {saved:false,navigating_back:true,promo_wait:true,error:'Ждём обязательный шаг Avito после сохранения'};
  }

  if(/характеристики/i.test(pageText)){
    const back=findAction(/^(назад|вернуться)$/i) || [...document.querySelectorAll('button,a,[role="button"]')].find(b=>{
      try{ const r=b.getBoundingClientRect(); return visible(b)&&r.top>=80&&r.top<360&&r.left>=120&&r.left<430&&!clean(b.innerText)&&!!b.querySelector('svg'); }catch{return false;}
    });
    if(back){ back.click(); return {saved:false,navigating_back:true}; }
    if(history.length>1){ history.back(); return {saved:false,navigating_back:true}; }
  }

  const stillInWizard=/items\/edit|profile\/items\/.*\/edit|\/edit\/\d+|\/cpxpromo\/|cpxpromo=|\/pro\/performance/i.test(location.href);
  if(!stillInWizard) return {saved:true,terminal:true,url:location.href};
  const actions=[...document.querySelectorAll('button,a,[role="button"],[data-marker]')]
    .filter(visible).map(e=>clean(e.innerText||e.getAttribute('aria-label')||e.getAttribute('title')||'')).filter(Boolean).slice(0,40);
  return {saved:false,error:'Шаг сохранения Avito не распознан',diagnostics:{url:location.href,title:document.title,page_text:pageText.slice(-3000),actions}};
}

function verifyPhotoCount(expected){
  const input=document.querySelector('input[type="file"][accept*="image"],input[type="file"]');
  const root=input?.closest('[data-marker],section,form,[class*="photo"],[class*="image"]')||input?.parentElement;
  if(!root) return {ok:false,count:0};
  const imgs=[...root.querySelectorAll('img')].filter(img=>{
    const src=String(img.getAttribute('src')||'');
    return /^https?:|^blob:|^data:image/i.test(src) && (img.naturalWidth>80 || img.width>80);
  });
  return {ok:imgs.length>=Math.max(1,Number(expected||1)),count:imgs.length};
}

async function applyChanges(changes,rules){
  const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const norm=s=>clean(s).toLowerCase();
  const aliases={brand:['Бренд'],product_subtype:['Подтип товара','Вид','Тип товара','Тип душевого ограждения'],glass_type:['Тип стекла','Стекло'],door_type:['Тип дверей','Двери']};
  const diagnostics=[];
  const dispatch=(el)=>{ for(const ev of ['input','change','blur']) el.dispatchEvent(new Event(ev,{bubbles:true})); };
  const nativeSet=(el,value)=>{
    const proto=el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const desc=Object.getOwnPropertyDescriptor(proto,'value');
    if(desc?.set) desc.set.call(el,value); else el.value=value;
    dispatch(el);
  };
  const optionLabel=(el)=>{
    if(el.id){ const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(l) return clean(l.innerText); }
    return clean(el.closest('label')?.innerText||el.getAttribute('aria-label')||'');
  };
  const knownRadioGroup=(label)=>{
    const l=norm(label);
    const groups=[
      ['состояние',['Новое','Б/у']],
      ['тип душевого ограждения',['Уголок','Ограждение','Шторка для ванны','Дверь']],
      ['тип стекла',['Прозрачное стекло','Матовое стекло','Рифлёное стекло','Пластиковое стекло']],
      ['тип дверей',['Раздвижные','Распашные','Складные','Без дверей']],
      ['наличие',['В наличии','Под заказ']],
      ['способ связи',['Звонки и сообщения','Только звонки','Только сообщения']]
    ];
    return groups.find(([k])=>l===k)?.[1]||null;
  };
  async function setRadioGroup(label,value){
    const expected=knownRadioGroup(label);
    const radios=[...document.querySelectorAll('input[type="radio"]')];
    let candidates=radios;
    if(expected){ candidates=radios.filter(r=>expected.some(x=>norm(optionLabel(r)).startsWith(norm(x)))); }
    const target=candidates.find(r=>norm(optionLabel(r))===norm(value) || norm(optionLabel(r)).startsWith(norm(value)));
    if(!target) return false;
    target.click();
    target.checked=true;
    target.dispatchEvent(new Event('change',{bubbles:true}));
    await sleep(250);
    return target.checked || norm(optionLabel(target)).startsWith(norm(value));
  }
  async function setByLabel(label,value){
    if(await setRadioGroup(label,value)) return true;
    const fieldLabelFor=(el)=>{
      if(el.id){ const l=document.querySelector(`label[for=\"${CSS.escape(el.id)}\"]`); if(l) return clean(l.innerText); }
      const parent=el.closest('label'); if(parent) return clean(parent.innerText);
      const wrap=el.closest('[data-marker],fieldset,[class*=\"field\"],[class*=\"input\"],[class*=\"form\"]');
      if(wrap){ const l=wrap.querySelector('label,legend,[class*=\"label\"],[class*=\"title\"]'); if(l) return clean(l.innerText); }
      return clean(el.getAttribute('aria-label')||el.name||el.placeholder||'');
    };
    const direct=[...document.querySelectorAll('input:not([type=\"hidden\"]):not([type=\"radio\"]),textarea,[role=\"combobox\"],select')]
      .find(el=>norm(fieldLabelFor(el))===norm(label));
    const labels=[...document.querySelectorAll('label,legend,[class*=\"label\"],[class*=\"title\"],span,div')].filter(e=>norm(e.innerText)===norm(label));
    if(direct){
      labels.unshift({tagName:'BORIS_DIRECT',htmlFor:'',innerText:label,parentElement:direct.parentElement,closest:()=>direct.closest('fieldset,[data-marker],[class*=\"field\"],[class*=\"input\"],[class*=\"form\"]')||direct.parentElement,__borisDirect:direct});
    }
    for(const lab of labels.slice(0,15)){
      let linked=lab.__borisDirect||null;
      if(!linked && lab.tagName==='LABEL' && lab.htmlFor){ try{ linked=document.getElementById(lab.htmlFor); }catch{} }
      let root=(linked?.closest?.('fieldset,[data-marker],[class*="field"],[class*="input"],[class*="form"]'))||lab.closest('fieldset,[data-marker],[class*="field"],[class*="input"],[class*="form"]')||lab.parentElement;
      if(!root) continue;
      const exact=[...root.querySelectorAll('label,[role="option"],[role="radio"],button')].find(e=>norm(e.innerText)===norm(value)||norm(e.innerText).startsWith(norm(value)));
      if(exact){ exact.click(); await sleep(250); return true; }
      // When a <label for=...> or the scan-equivalent direct match resolved
      // the field, operate on that exact control. Querying the surrounding Avito
      // container can otherwise pick the title/price input before Brand.
      const input=linked || root.querySelector('input:not([type="hidden"]):not([type="radio"]),textarea,[role="combobox"],select');
      if(input){
        if(input.tagName==='SELECT'){
          const opt=[...input.options].find(o=>norm(o.text)===norm(value)||norm(o.value)===norm(value));
          if(opt){ nativeSet(input,opt.value); return true; }
        }else if(norm(label)==='бренд' || input.getAttribute('role')==='combobox' || input.getAttribute('aria-autocomplete') || input.getAttribute('autocomplete')==='off'){
          // Avito stores an internal autocomplete entity id. Plain text is not a
          // committed value, so never report success until a real suggestion is selected.
          input.focus();
          nativeSet(input,'');
          input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'deleteContentBackward',data:null}));
          await sleep(60);
          // Set the whole query in one native input transaction. Human-like
          // per-character delays made one field ~1.5s slower with no benefit.
          nativeSet(input,String(value));
          input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:String(value)}));
          input.dispatchEvent(new KeyboardEvent('keyup',{key:String(value).slice(-1),bubbles:true}));
          await sleep(280);
          const nodes=[...document.querySelectorAll('[role="option"],li,[data-marker*="suggest"],[data-marker*="option"],[class*="suggest"],[class*="option"]')]
            .filter(e=>e!==input && e.offsetParent!==null && clean(e.innerText));
          const activeId=input.getAttribute('aria-activedescendant');
          const active=activeId ? document.getElementById(activeId) : null;
          const exact=nodes.find(e=>norm(e.innerText)===norm(value));
          const pref=nodes.find(e=>norm(e.innerText).startsWith(norm(value)+' '));
          const opt=exact || (active && norm(active.innerText)===norm(value) ? active : null) || pref;
          if(!opt){
            const suggestions=nodes.map(e=>clean(e.innerText)).filter(Boolean).slice(0,20);
            // Avito may render the suggestion popup in a portal that our DOM scan
            // cannot see. First try the same keyboard commit a human uses.
            input.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',code:'ArrowDown',keyCode:40,which:40,bubbles:true}));
            input.dispatchEvent(new KeyboardEvent('keyup',{key:'ArrowDown',code:'ArrowDown',keyCode:40,which:40,bubbles:true}));
            await sleep(120);
            input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
            input.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
            await sleep(220);
            let current=clean(input.value||input.getAttribute('aria-valuetext')||'');
            let listOpen=input.getAttribute('aria-expanded')==='true';
            if(norm(current)===norm(value) && !listOpen){
              input.dispatchEvent(new Event('change',{bubbles:true}));
              input.blur(); await sleep(120);
              diagnostics.push({label,value,reason:'autocomplete_keyboard_committed',current,suggestions});
              return true;
            }
            // Some Avito autocomplete fields accept a new/free-text value even when
            // the suggestion service returns no DOM option. Save it and let reopen
            // verification be the source of truth; never mark the item verified here.
            current=clean(input.value||'');
            if(norm(current)===norm(value)){
              input.dispatchEvent(new Event('change',{bubbles:true}));
              input.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',code:'Tab',keyCode:9,which:9,bubbles:true}));
              input.blur(); await sleep(180);
              diagnostics.push({label,value,reason:'autocomplete_free_text_pending_verify',current,suggestions});
              return true;
            }
            diagnostics.push({label,value,reason:'autocomplete_no_option',current,suggestions,listOpen});
            return false;
          }
          for(const type of ['pointerdown','mousedown','pointerup','mouseup','click']){
            try{
              const E=(type.startsWith('pointer') && typeof PointerEvent!=='undefined') ? PointerEvent : MouseEvent;
              opt.dispatchEvent(new E(type,{bubbles:true,cancelable:true,view:window,pointerType:'mouse'}));
            }catch(_e){}
          }
          await sleep(180);
          input.dispatchEvent(new Event('change',{bubbles:true}));
          input.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',code:'Tab',keyCode:9,which:9,bubbles:true}));
          input.blur(); await sleep(120);
          const current=clean(input.value||input.getAttribute('aria-valuetext')||'');
          const listOpen=input.getAttribute('aria-expanded')==='true';
          const ok=!listOpen && norm(current)===norm(value);
          if(!ok) diagnostics.push({label,value,reason:'autocomplete_not_committed',current,listOpen});
          return ok;
        }else{
          input.focus(); nativeSet(input,value); await sleep(180);
          return norm(input.value)===norm(value) || clean(input.value)===clean(value);
        }
      }
    }
    diagnostics.push({label,value,reason:'field_control_not_resolved'});
    return false;
  }
  const changed=[]; const skipped=[];
  for(const [key,value] of Object.entries(changes||{})){
    const rule=rules?.[key]||{};
    const labels=rule.label?[rule.label]:(aliases[key]||[key.replace(/^custom:/,'')]);
    let ok=false;
    for(const label of labels){ if(await setByLabel(label,String(value))){ok=true;break;} }
    if(ok) changed.push(key); else skipped.push(key);
  }
  if(!changed.length) return {saved:false,changed_fields:changed,skipped_fields:skipped,error:'Поля не найдены или значение не поддерживается Avito',diagnostics};
  return {saved:false,changed_fields:changed,skipped_fields:skipped};
}


function verifyScannedChanges(changes,changedFields,rules,fields){
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const norm=s=>clean(s).toLowerCase();
  const list=Array.isArray(fields)?fields:[];
  const verified=[];
  for(const key of changedFields||[]){
    const expected=String(changes?.[key]??'').trim();
    const label=String(rules?.[key]?.label||key).replace(/^custom:/,'').trim();
    if(!expected||!label) continue;
    const field=list.find(f=>norm(f?.label)===norm(label));
    if(!field) continue;
    const actual=String(field.value??field.current??'').trim();
    if(norm(actual)===norm(expected)) verified.push(key);
  }
  return {ok:verified.length===(changedFields||[]).length,verified_fields:verified};
}

function verifyChanges(changes,changedFields){
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const norm=s=>clean(s).toLowerCase();
  const verified=[];
  const optionLabel=(el)=>{
    if(el.id){ const l=document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if(l) return clean(l.innerText); }
    return clean(el.closest('label')?.innerText||el.getAttribute('aria-label')||'');
  };
  for(const key of changedFields||[]){
    const expected=String(changes?.[key]??'').trim();
    if(!expected) continue;
    const label=String(key||'').replace(/^custom:/,'');
    let ok=false;

    // Radio/checkbox verification uses actual checked state, never page text.
    const radios=[...document.querySelectorAll('input[type="radio"]')];
    const radio=radios.find(r=>r.checked && (norm(optionLabel(r))===norm(expected)||norm(optionLabel(r)).startsWith(norm(expected))));
    if(radio) ok=true;

    if(!ok){
      const labels=[...document.querySelectorAll('label,legend,[class*="label"],[class*="title"]')].filter(e=>norm(e.innerText)===norm(label));
      for(const lab of labels.slice(0,12)){
        const root=lab.closest('fieldset,[data-marker],[class*="field"],[class*="input"],[class*="form"]')||lab.parentElement;
        if(!root) continue;
        const input=root.querySelector('input:not([type="hidden"]):not([type="radio"]),textarea,select,[role="combobox"]');
        if(input){
          const actual=input.tagName==='SELECT' ? (input.options?.[input.selectedIndex]?.text||input.value||'') : (input.value||input.getAttribute('aria-valuetext')||'');
          if(norm(actual)===norm(expected)){ ok=true; break; }
        }
        const checked=root.querySelector('input[type="checkbox"]');
        if(checked && ['да','true','1','включено'].includes(norm(expected)) && checked.checked){ ok=true; break; }
      }
    }
    if(ok) verified.push(key);
  }
  return {ok:verified.length===(changedFields||[]).length,verified_fields:verified};
}

chrome.alarms.create('boris-gateway-poll',{periodInMinutes:POLL_MINUTES});
tick();
