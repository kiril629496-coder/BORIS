"use client";
import { useEffect } from "react";
export default function PwaRegister() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    let reloading=false;
    const onController=()=>{ if(reloading)return; reloading=true; window.dispatchEvent(new CustomEvent("boris-pwa-updated")); setTimeout(()=>window.location.reload(),250); };
    navigator.serviceWorker.addEventListener("controllerchange",onController);
    const register=async()=>{
      try{
        const reg=await navigator.serviceWorker.register("/sw.js",{scope:"/"});
        await reg.update().catch(()=>undefined);
        if(reg.waiting) {
          if(window.location.pathname==="/messages/mop-training") reg.waiting.postMessage({type:"BORIS_SKIP_WAITING"});
          window.dispatchEvent(new CustomEvent("boris-pwa-update-ready",{detail:{registration:reg}}));
        }
        reg.addEventListener("updatefound",()=>{
          const w=reg.installing;if(!w)return;
          w.addEventListener("statechange",()=>{if(w.state==="installed"&&navigator.serviceWorker.controller)window.dispatchEvent(new CustomEvent("boris-pwa-update-ready",{detail:{registration:reg}}));});
        });
      }catch{}
    };
    const onVisible=()=>{if(document.visibilityState==="visible")void register()};
    if(document.readyState==="complete")void register(); else window.addEventListener("load",register,{once:true});
    document.addEventListener("visibilitychange",onVisible);
    const intervalMs=window.location.pathname==="/messages/mop-training"?2*60*1000:30*60*1000;
    const timer=window.setInterval(()=>void register(),intervalMs);
    return()=>{window.removeEventListener("load",register);document.removeEventListener("visibilitychange",onVisible);window.clearInterval(timer);navigator.serviceWorker.removeEventListener("controllerchange",onController)};
  },[]);
  return null;
}
