const $ = (id) => document.getElementById(id);
const setStatus = (text, kind='') => { const el=$('status'); el.textContent=text; el.className='status '+kind; };
async function readJson(r){ const text=await r.text(); try{return text?JSON.parse(text):{};}catch{ throw new Error(r.ok?'BORIS вернул некорректный ответ':`Сервер BORIS недоступен (${r.status})`); } }

const SERVICE_LABELS={avito:'Avito',yandex:'Яндекс',google:'Google',gmail:'Gmail',youtube:'YouTube',vk:'VK',telegram:'Telegram',mailru:'Mail.ru'};
function renderServices(services={}){ const root=$('services'); if(!root) return; root.innerHTML=Object.entries(SERVICE_LABELS).map(([k,v])=>`<div class="svc ${services[k]?'on':''}">${services[k]?'✓':'○'} ${v}</div>`).join(''); }

async function getCfg(){ return await chrome.storage.local.get(['server','token','gateway_id','account_id','account_name','profile_instance_id']); }
async function ensureProfileInstanceId(){
  const c=await chrome.storage.local.get(['profile_instance_id']);
  if(c.profile_instance_id) return c.profile_instance_id;
  const id=(globalThis.crypto?.randomUUID?.() || ('profile-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,14)));
  await chrome.storage.local.set({profile_instance_id:id});
  return id;
}
async function init(){ await ensureProfileInstanceId(); const c=await getCfg(); $('server').value=c.server || 'https://boris-ai.pro'; renderServices({}); if(c.token){ const bound=c.account_name||c.account_id||c.gateway_id||'Gateway'; setStatus(`Подключено · ${bound}`,'ok'); try{ const r=await fetch((c.server||'https://boris-ai.pro')+'/api/browser-gateway/transport/status',{headers:{Authorization:'Bearer '+c.token}}); const d=await readJson(r); if(r.ok){ renderServices(d.gateway?.services||{}); if(d.gateway?.identity_mismatch){ setStatus(`Заблокировано · в этом профиле Chrome открыт другой аккаунт Avito`,'bad'); } else if(d.gateway?.account_id) { await chrome.storage.local.set({account_id:d.gateway.account_id,account_name:d.gateway.account_name||c.account_name||''}); } } }catch{} } }

$('pair').onclick = async () => {
  const server=$('server').value.trim().replace(/\/$/,'');
  const code=$('code').value.trim().toUpperCase();
  if(!server || !code){ setStatus('Укажите адрес BORIS и код подключения','bad'); return; }
  setStatus('Подключаю…');
  try{
    const profile_instance_id=await ensureProfileInstanceId();
    const r=await fetch(server+'/api/browser-gateway/transport/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,device_name:navigator.userAgent,extension_version:chrome.runtime.getManifest().version,profile_instance_id})});
    const d=await readJson(r); if(!r.ok) throw new Error(typeof d.detail==='string'?d.detail:'Ошибка подключения');
    await chrome.storage.local.set({server,token:d.token,gateway_id:d.gateway_id,account_id:d.account_id||'',account_name:d.account_name||'',profile_instance_id:d.profile_instance_id||profile_instance_id});
    setStatus(`Подключено · ${d.account_name||d.account_id||d.gateway_id}`,'ok'); $('code').value='';
    chrome.runtime.sendMessage({type:'gateway_wake'});
  }catch(e){ setStatus(e.message || 'Ошибка','bad'); }
};

$('check').onclick = async () => {
  const c=await getCfg(); if(!c.server || !c.token){ setStatus('Сначала подключите Gateway','bad'); return; }
  setStatus('Проверяю…');
  try{
    const r=await fetch(c.server+'/api/browser-gateway/transport/status',{headers:{Authorization:'Bearer '+c.token}});
    const d=await readJson(r); if(!r.ok) throw new Error(typeof d.detail==='string'?d.detail:'Нет связи');
    if(d.gateway?.identity_mismatch) setStatus('Заблокировано · открыт другой аккаунт Avito. Верните профиль этого клиента.','bad');
    else setStatus(`Связь есть · ${d.gateway?.state || 'idle'}${d.gateway?.avito_logged_in===false ? ' · войдите в Avito' : ''}`,'ok');
    renderServices(d.gateway?.services||{});
  }catch(e){ setStatus(e.message || 'Нет связи','bad'); }
};

init();


function runtimeMessage(message){
  return new Promise((resolve,reject)=>{
    chrome.runtime.sendMessage(message,response=>{
      const err=chrome.runtime.lastError;
      if(err) reject(new Error(err.message)); else resolve(response||{});
    });
  });
}

async function refreshVideoStatus(){
  const el=$('videoStatus'); if(!el) return;
  try{
    const s=await runtimeMessage({type:'recording_status'});
    if(s?.enabled){
      el.textContent=`Видео: включено${s.recording_active?' · идёт запись':''}`;
      el.className='status ok';
    }else{
      el.textContent='Видео: выключено'; el.className='status';
    }
  }catch(e){ el.textContent='Видео: статус недоступен'; el.className='status bad'; }
}

$('recordEnable').onclick=async()=>{
  const el=$('videoStatus'); el.textContent='Видео: включаю…'; el.className='status';
  try{
    const r=await runtimeMessage({type:'recording_enable',options:{include_audio:false}});
    if(!r?.ok) throw new Error(r?.error||'Не удалось включить видеорежим');
    el.textContent='Видео: включено · automation-вкладка готова'; el.className='status ok';
  }catch(e){ el.textContent='Видео: '+(e.message||'ошибка'); el.className='status bad'; }
};

$('recordDisable').onclick=async()=>{
  const el=$('videoStatus');
  try{
    const r=await runtimeMessage({type:'recording_disable'});
    if(!r?.ok) throw new Error(r?.error||'Не удалось выключить видеорежим');
    el.textContent='Видео: выключено'; el.className='status';
  }catch(e){ el.textContent='Видео: '+(e.message||'ошибка'); el.className='status bad'; }
};

refreshVideoStatus();
