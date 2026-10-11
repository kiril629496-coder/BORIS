"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {apiGet} from "../../lib/api";

export function editorDuration(seconds:number){
  const n=Math.max(0,Math.floor(seconds||0));
  return Math.floor(n/60)+":"+String(n%60).padStart(2,"0");
}
export default function useCampaignEditorJob(campaignId:string,onComplete:()=>void){
  const [job,setJob]=useState<any>(null);
  const callback=useRef(onComplete);callback.current=onComplete;
  const mounted=useRef(true);
  const scope=useRef(campaignId);scope.current=campaignId;
  const key="boris:campaign-editor-jobs:"+campaignId;
  const saved=useCallback(()=>{
    try{return JSON.parse(localStorage.getItem(key)||"[]") as number[];}catch{return [];}
  },[key]);
  const forget=useCallback((id:number)=>{
    localStorage.setItem(key,JSON.stringify(saved().filter(n=>n!==id)));
  },[key,saved]);
  const read=useCallback(async(id:number)=>{
    const data=await apiGet("/api/campaign/"+campaignId+"/editor-jobs/"+id);
    if(mounted.current && scope.current===campaignId)setJob(data.job);
    return data;
  },[campaignId]);
  useEffect(()=>{
    mounted.current=true;let cancelled=false;
    const poll=async()=>{
      for(const id of saved()){
        if(cancelled)return;
        try{
          const data=await read(id);
          if(["completed","partial","failed","cancelled","awaiting_confirmation"].includes(data.job.status)){
            forget(id);
            if(data.job.status==="completed")callback.current();
          }
        }catch{/* Keep the durable identity through a temporary network failure. */}
      }
    };
    void poll();const timer=setInterval(()=>void poll(),1500);
    return ()=>{cancelled=true;mounted.current=false;clearInterval(timer);};
  },[saved,forget,read]);
  const track=useCallback(async(initial:any)=>{
    const id=Number(initial.id);
    localStorage.setItem(key,JSON.stringify(Array.from(new Set([...saved(),id]))));
    setJob(initial);
    while(mounted.current && scope.current===campaignId){
      let data:any;
      try{data=await read(id);}catch{await new Promise(r=>setTimeout(r,1500));continue;}
      if(!mounted.current || scope.current!==campaignId)break;
      const state=data.job.status;
      if(["completed","partial","failed","cancelled","awaiting_confirmation"].includes(state)){
        forget(id);
        if(state==="completed")return data.result;
        const error:any=new Error(data.result?.message||data.job.error||"BORIS сохранил задачу. Проверьте её результат.");
        error.code=data.result?.code;error.raw=data.result;throw error;
      }
      await new Promise(r=>setTimeout(r,1000));
    }
    throw new Error("Задача сохранена. Результат появится после возвращения на страницу.");
  },[campaignId,forget,key,read,saved]);
  return {editorJob:job,trackEditorJob:track};
}
