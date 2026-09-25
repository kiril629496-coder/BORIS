(function(){
  try {
    if(window.__BORISRevenueTrackerStarted)return;
    var cs=document.currentScript;
    var u=cs&&cs.src?new URL(cs.src,location.href):null;
    var aid=u?u.searchParams.get('aid'):'';
    var key=u?u.searchParams.get('k'):'';
    function start(accountId,projectKey){
      if(!accountId||!projectKey||window.__BORISRevenueTrackerStarted)return;
      window.__BORISRevenueTrackerStarted=true;
      var vid=localStorage.getItem('boris_visitor_id');
      if(!vid){vid='v_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,12);localStorage.setItem('boris_visitor_id',vid)}
      var sid=sessionStorage.getItem('boris_session_id');
      if(!sid){sid='s_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,12);sessionStorage.setItem('boris_session_id',sid)}
      var q=new URLSearchParams(location.search), landing=sessionStorage.getItem('boris_landing_url');
      if(!landing){landing=location.href;sessionStorage.setItem('boris_landing_url',landing)}
      function send(type){
        var data={account_id:accountId,project_key:projectKey,event_type:type,visitor_id:vid,session_id:sid,source:q.get('utm_source')||'',medium:q.get('utm_medium')||'',campaign:q.get('utm_campaign')||'',content:q.get('utm_content')||'',term:q.get('utm_term')||'',yclid:q.get('yclid')||'',landing_url:landing,page_url:location.href,referrer:document.referrer||''};
        var body=JSON.stringify(data), url='/api/revenue-tracker/event';
        if(navigator.sendBeacon){navigator.sendBeacon(url,new Blob([body],{type:'application/json'}))}else{fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:body,keepalive:true,credentials:'same-origin'}).catch(function(){})}
      }
      window.BORISRevenueTrack=function(name){send(name==='lead'?'lead':'custom')};
      send('pageview');
      if(window.__BORISPendingRevenueLead){window.__BORISPendingRevenueLead=false;send('lead')}
      document.addEventListener('click',function(e){var a=e.target&&e.target.closest?e.target.closest('a[href^="tel:"]'):null;if(a)send('click_phone')},true);
      document.addEventListener('submit',function(){send('form_submit')},true);
    }
    if(aid&&key){start(aid,key);return}
    fetch('/api/revenue-tracker/site-config',{credentials:'same-origin',cache:'no-store'}).then(function(r){if(!r.ok)throw new Error('tracker_config_'+r.status);return r.json()}).then(function(c){start(c.account_id||'',c.project_key||'')}).catch(function(){});
  } catch(e) {}
})();
