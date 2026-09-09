const CACHE='systems-ai-v2';
self.addEventListener('install',event=>{
  event.waitUntil((async()=>{
    const base=self.registration.scope;
    const core=[base,new URL('manifest.webmanifest',base).href,new URL('icons/icon-192.png',base).href,new URL('icons/icon-512.png',base).href];
    const cache=await caches.open(CACHE);
    await cache.addAll(core);
    await self.skipWaiting();
  })());
});
self.addEventListener('activate',event=>{
  event.waitUntil((async()=>{
    const keys=await caches.keys();
    await Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET')return;
  event.respondWith(fetch(event.request).catch(async()=>{
    const cached=await caches.match(event.request);
    if(cached)return cached;
    if(event.request.mode==='navigate')return caches.match(self.registration.scope);
    return Response.error();
  }));
});