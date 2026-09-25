import fs from 'node:fs';
import { UserAgent, Registerer, RegistererState, SessionState } from 'sip.js';
import {
  RTCPeerConnection, MediaStreamTrack, RtpHeader, RtpPacket
} from 'file:///tmp/boris-webrtc-qa/node_modules/werift/lib/index.mjs';

const m=JSON.parse(fs.readFileSync('/tmp/boris_media_invite_qa.json','utf8'));
const uri=UserAgent.makeURI(m.sip_uri);
if(!uri) throw new Error('bad uri');

let registered=false,inviteSeen=false,established=false,peerConnected=false;
let rxPackets=0,rxBytes=0,txPackets=0,txDone=false;
let peer,audioTransceiver,txTrack;

function ulawSample(pcm){
  const BIAS=0x84, CLIP=32635;
  let sign=(pcm<0)?0x80:0;
  if(pcm<0) pcm=-pcm;
  if(pcm>CLIP) pcm=CLIP;
  pcm += BIAS;
  let exponent=7;
  for(let expMask=0x4000;(pcm & expMask)===0 && exponent>0; exponent--,expMask>>=1){}
  const mantissa=(pcm >> (exponent+3)) & 0x0f;
  return (~(sign | (exponent<<4) | mantissa)) & 0xff;
}
function tonePayload(frameIndex){
  const out=Buffer.alloc(160);
  const rate=8000,f=700,amp=9000;
  for(let i=0;i<160;i++){
    const n=frameIndex*160+i;
    const pcm=Math.round(Math.sin(2*Math.PI*f*n/rate)*amp);
    out[i]=ulawSample(pcm);
  }
  return out;
}

class WeriftDuplexSDH {
  constructor(){
    this.pc=new RTCPeerConnection();
    peer=this.pc;
    this.pc.connectionStateChange.subscribe((s)=>{
      console.log('WEBRTC_DUPLEX_CONNECTION_STATE',s);
      if(s==='connected') peerConnected=true;
    });
    this.pc.onTrack.subscribe((track)=>{
      console.log('WEBRTC_DUPLEX_TRACK',track.kind);
      if(track.kind==='audio'){
        track.onReceiveRtp.subscribe((pkt)=>{
          rxPackets++;
          rxBytes += Number(pkt?.payload?.length || 0);
        });
      }
    });
  }
  hasDescription(contentType){return String(contentType||'').toLowerCase().includes('application/sdp');}
  async setDescription(sdp){
    console.log('WEBRTC_REMOTE_SDP_LINES',JSON.stringify(String(sdp).split(/\\r?\\n/).filter(x=>/^(m=audio|c=IN|a=candidate|a=setup|a=fingerprint|a=ice-|a=rtcp-mux|a=(sendrecv|sendonly|recvonly))/.test(x))));
    await this.pc.setRemoteDescription({type:'offer',sdp});
    audioTransceiver=this.pc.getTransceivers().find((t)=>t.kind==='audio');
    if(!audioTransceiver) throw new Error('audio transceiver missing');
    txTrack=new MediaStreamTrack({kind:'audio'});
    audioTransceiver.setDirection('sendrecv');
    await audioTransceiver.sender.replaceTrack(txTrack);
  }
  async getDescription(){
    const answer=await this.pc.createAnswer();
    await this.pc.setLocalDescription(answer);
    if(this.pc.iceGatheringState!=='complete'){
      await this.pc.iceGatheringStateChange.watch((s)=>s==='complete',5000).catch(()=>{});
    }
    const local=this.pc.localDescription;
    if(!local?.sdp) throw new Error('local SDP missing');
    const direction=(local.sdp.match(/a=(sendrecv|sendonly|recvonly|inactive)/)||[])[1]||'unknown';
    console.log('WEBRTC_DUPLEX_LOCAL_SDP',this.pc.iceGatheringState,direction);
    console.log('WEBRTC_LOCAL_SDP_LINES',JSON.stringify(String(local.sdp).split(/\\r?\\n/).filter(x=>/^(m=audio|c=IN|a=candidate|a=setup|a=fingerprint|a=ice-|a=rtcp-mux|a=(sendrecv|sendonly|recvonly))/.test(x))));
    return {body:local.sdp,contentType:'application/sdp'};
  }
  async rollbackDescription(){}
  close(){try{this.pc.close();}catch{}}
  sendDtmf(){return false;}
}

async function sendTone(){
  const deadline=Date.now()+8000;
  while(!peerConnected && Date.now()<deadline) await new Promise(r=>setTimeout(r,50));
  if(!peerConnected) throw new Error('peer not connected for TX');
  const sender=audioTransceiver?.sender;
  if(!sender || !txTrack) throw new Error('audio sender/track missing');
  const codec=sender.codec;
  console.log('WEBRTC_TX_CODEC',JSON.stringify(codec||{}));
  const mime=String(codec?.mimeType||codec?.name||'').toLowerCase();
  if(!mime.includes('pcmu')) throw new Error('unexpected TX codec '+mime);
  const pt=Number(codec?.payloadType ?? 0);
  const ssrc=0x424f5249;
  for(let i=0;i<60;i++){
    const packet=new RtpPacket(new RtpHeader({
      version:2,payloadType:pt,sequenceNumber:(1200+i)&0xffff,
      timestamp:(900000+i*160)>>>0,ssrc,marker:i===0
    }),tonePayload(i));
    txTrack.writeRtp(packet);
    txPackets++;
    await new Promise(r=>setTimeout(r,20));
  }
  txDone=true;
  const stats=await sender.getStats().catch(()=>undefined);
  console.log('WEBRTC_TX_SENT',txPackets,'stats',stats?JSON.stringify([...stats.values()].filter(x=>x.type==='outbound-rtp')):'none');
}

const ua=new UserAgent({
  uri,
  transportOptions:{server:m.signaling_url},
  authorizationUsername:m.authorization_username,
  authorizationPassword:m.session_token,
  contactName:m.session_id,
  displayName:'BORIS WebRTC Duplex QA',
  logBuiltinEnabled:false,
  logConfiguration:false,
  sessionDescriptionHandlerFactory:()=>new WeriftDuplexSDH(),
  delegate:{
    onInvite: async (invitation)=>{
      inviteSeen=true;
      console.log('SIPJS_DUPLEX_INVITE_RECEIVED');
      invitation.stateChange.addListener((s)=>{
        if(s===SessionState.Established){established=true;console.log('SIPJS_DUPLEX_ESTABLISHED');}
      });
      try{
        await invitation.accept();
        await sendTone();
      }catch(e){
        console.error('SIPJS_DUPLEX_ACCEPT_ERROR',e?.stack||e);
      }
    }
  }
});
const reg=new Registerer(ua,{expires:90});
reg.stateChange.addListener((s)=>{
  if(s===RegistererState.Registered){registered=true;console.log('SIPJS_DUPLEX_REGISTERED');}
});
await ua.start();
await reg.register();
let deadline=Date.now()+8000;
while(!registered && Date.now()<deadline) await new Promise(r=>setTimeout(r,100));
if(!registered) throw new Error('register timeout');
deadline=Date.now()+15000;
while((!inviteSeen || !established || !peerConnected || rxPackets<5 || !txDone) && Date.now()<deadline){
  await new Promise(r=>setTimeout(r,100));
}
console.log('WEBRTC_DUPLEX_RESULT',JSON.stringify({inviteSeen,established,peerConnected,rxPackets,rxBytes,txPackets,txDone}));
if(!inviteSeen||!established||!peerConnected) throw new Error('duplex signaling/peer incomplete');
if(rxPackets<5||rxBytes<100) throw new Error('RTP RX insufficient');
if(!txDone||txPackets<60) throw new Error('RTP TX insufficient');
await reg.unregister().catch(()=>{});
await ua.stop().catch(()=>{});
try{peer?.close();}catch{}
console.log('WEBRTC_DUPLEX_QA_CLIENT_OK');
