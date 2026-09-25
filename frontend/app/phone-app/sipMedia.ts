'use client';

import { Web } from 'sip.js';

export type BorisSipMediaEnvelope={
  transport:'sip_ws';
  signaling_protocol:'boris-sip-v1';
  signaling_url:string;
  session_id:string;
  session_token:string;
  authorization_username:string;
  sip_uri:string;
  register_expires_seconds:number;
  ttl_seconds:number;
  ice_servers?:RTCIceServer[];
};

export type BorisSipMediaState='connecting'|'registered'|'connected'|'ended'|'error';

export type BorisMediaTransportStats={
  packets_sent:number;
  packets_received:number;
  bytes_sent:number;
  bytes_received:number;
  candidate_pair_succeeded:boolean;
  ice_state:string;
  dtls_state:string;
};

export interface BorisSipMediaController{
  mute(muted:boolean):void;
  transportStats():Promise<BorisMediaTransportStats|null>;
  dispose():Promise<void>;
}

const validEnvelope=(m:any):m is BorisSipMediaEnvelope=>{
  if(!m||m.transport!=='sip_ws'||m.signaling_protocol!=='boris-sip-v1')return false;
  if(typeof m.signaling_url!=='string'||!m.signaling_url.startsWith('wss://'))return false;
  if(typeof m.session_id!=='string'||!/^bp_[A-Za-z0-9_]{1,61}$/.test(m.session_id))return false;
  if(m.authorization_username!==m.session_id)return false;
  if(typeof m.session_token!=='string'||m.session_token.length<16||m.session_token.length>2048)return false;
  if(typeof m.sip_uri!=='string'||!m.sip_uri.startsWith('sip:'+m.session_id+'@'))return false;
  const exp=Number(m.register_expires_seconds);
  return Number.isFinite(exp)&&exp>=30&&exp<=180;
};

export async function readBorisMediaTransportStats(pc:RTCPeerConnection|undefined|null):Promise<BorisMediaTransportStats|null>{
  if(!pc||pc.connectionState==='closed')return null;
  let report:RTCStatsReport;
  try{report=await pc.getStats()}catch{return null}
  let packetsSent=0,packetsReceived=0,bytesSent=0,bytesReceived=0;
  let candidatePairSucceeded=false,dtlsState='';
  let selectedCandidatePairId='';
  report.forEach((raw:any)=>{
    const kind=String(raw?.kind||raw?.mediaType||'').toLowerCase();
    if(raw?.type==='outbound-rtp'&&!raw?.isRemote&&(kind==='audio'||!kind)){
      packetsSent+=Math.max(0,Number(raw?.packetsSent||0));
      bytesSent+=Math.max(0,Number(raw?.bytesSent||0));
    }else if(raw?.type==='inbound-rtp'&&!raw?.isRemote&&(kind==='audio'||!kind)){
      packetsReceived+=Math.max(0,Number(raw?.packetsReceived||0));
      bytesReceived+=Math.max(0,Number(raw?.bytesReceived||0));
    }
    if(raw?.type==='transport'){
      if(typeof raw?.selectedCandidatePairId==='string')selectedCandidatePairId=raw.selectedCandidatePairId;
      if(typeof raw?.dtlsState==='string')dtlsState=raw.dtlsState.toLowerCase();
    }
  });
  report.forEach((raw:any)=>{
    if(raw?.type!=='candidate-pair'||String(raw?.state||'').toLowerCase()!=='succeeded')return;
    if((selectedCandidatePairId&&raw?.id===selectedCandidatePairId)||raw?.nominated===true||raw?.selected===true)candidatePairSucceeded=true;
  });
  const iceState=String(pc.iceConnectionState||'').toLowerCase();
  return {
    packets_sent:Math.floor(packetsSent),packets_received:Math.floor(packetsReceived),
    bytes_sent:Math.floor(bytesSent),bytes_received:Math.floor(bytesReceived),
    candidate_pair_succeeded:candidatePairSucceeded,ice_state:iceState,dtls_state:dtlsState,
  };
}

export async function startBorisSipMedia(
  envelope:any,
  options:{
    inputId?:string;
    outputId?:string;
    muted?:boolean;
    onState?:(state:BorisSipMediaState,detail?:string)=>void;
  }={}
):Promise<BorisSipMediaController>{
  if(!validEnvelope(envelope))throw new Error('invalid_boris_sip_media_envelope');
  const notify=(state:BorisSipMediaState,detail?:string)=>{try{options.onState?.(state,detail)}catch{}};
  let disposed=false;
  let currentMuted=!!options.muted;
  const remoteAudio=new Audio();
  remoteAudio.autoplay=true;
  (remoteAudio as any).playsInline=true;
  if(options.outputId&&typeof (remoteAudio as any).setSinkId==='function'){
    try{await (remoteAudio as any).setSinkId(options.outputId)}catch{}
  }

  const mediaFactory=async():Promise<MediaStream>=>{
    if(disposed)throw new Error('sip_media_disposed');
    const audio:MediaTrackConstraints|boolean=options.inputId
      ?{deviceId:{exact:options.inputId},echoCancellation:true,noiseSuppression:true,autoGainControl:true}
      :{echoCancellation:true,noiseSuppression:true,autoGainControl:true};
    const stream=await navigator.mediaDevices.getUserMedia({audio,video:false});
    for(const track of stream.getAudioTracks())track.enabled=!currentMuted;
    return stream;
  };

  let simple:InstanceType<typeof Web.SimpleUser>;
  const delegate:any={
    onServerConnect:()=>notify('connecting'),
    onServerDisconnect:(e?:Error)=>{if(!disposed)notify('error',e?.name||'sip_transport_disconnected')},
    onRegistered:()=>notify('registered'),
    onUnregistered:()=>{if(!disposed)notify('error','sip_unregistered')},
    onCallReceived:()=>{if(!disposed)void simple.answer().catch(()=>notify('error','sip_answer_failed'))},
    onCallAnswered:()=>{
      if(disposed)return;
      try{
        if(simple.remoteMediaStream){
          remoteAudio.srcObject=simple.remoteMediaStream;
          void remoteAudio.play().catch(()=>{});
        }
        if(currentMuted)simple.mute();else simple.unmute();
      }catch{}
      notify('connected');
    },
    onCallHangup:()=>{if(!disposed)notify('ended')},
  };

  simple=new Web.SimpleUser(envelope.signaling_url,{
    aor:envelope.sip_uri,
    delegate,
    reconnectionAttempts:3,
    reconnectionDelay:2,
    registererOptions:{expires:Math.max(30,Math.min(180,Number(envelope.register_expires_seconds)||90))},
    media:{constraints:{audio:true,video:false},remote:{audio:remoteAudio}},
    userAgentOptions:{
      authorizationUsername:envelope.authorization_username,
      authorizationPassword:envelope.session_token,
      contactName:envelope.session_id,
      displayName:'BORIS Phone',
      logBuiltinEnabled:false,
      logConfiguration:false,
      sessionDescriptionHandlerFactory:Web.defaultSessionDescriptionHandlerFactory(mediaFactory),
      sessionDescriptionHandlerFactoryOptions:{
        peerConnectionConfiguration:{iceServers:Array.isArray(envelope.ice_servers)?envelope.ice_servers:[]},
        iceGatheringTimeout:4000,
      },
    },
  });

  notify('connecting');
  try{
    await simple.connect();
    if(disposed)throw new Error('sip_media_disposed');
    await simple.register();
  }catch(e){
    notify('error',e instanceof Error?e.name:'sip_connect_failed');
    try{await simple.disconnect()}catch{}
    remoteAudio.srcObject=null;
    throw e;
  }

  return {
    mute(value:boolean){
      currentMuted=!!value;
      try{if(currentMuted)simple.mute();else simple.unmute()}catch{}
      for(const t of simple.localMediaStream?.getAudioTracks()||[])t.enabled=!currentMuted;
    },
    async transportStats(){
      const session=(simple as any)?.session;
      const handler=session?.sessionDescriptionHandler;
      return readBorisMediaTransportStats(handler?.peerConnection as RTCPeerConnection|undefined);
    },
    async dispose(){
      if(disposed)return;
      disposed=true;
      try{if(simple.localMediaStream||simple.remoteMediaStream)await simple.hangup()}catch{}
      try{await simple.unregister()}catch{}
      try{await simple.disconnect()}catch{}
      for(const t of simple.localMediaStream?.getTracks()||[])try{t.stop()}catch{}
      try{remoteAudio.pause()}catch{}
      remoteAudio.srcObject=null;
      notify('ended');
    },
  };
}
