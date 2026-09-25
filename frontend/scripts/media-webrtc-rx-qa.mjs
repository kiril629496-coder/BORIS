import fs from 'node:fs';
import { UserAgent, Registerer, RegistererState, SessionState } from 'sip.js';
import { RTCPeerConnection } from 'file:///tmp/boris-webrtc-qa/node_modules/werift/lib/index.mjs';

const m=JSON.parse(fs.readFileSync('/tmp/boris_media_invite_qa.json','utf8'));
const uri=UserAgent.makeURI(m.sip_uri);
if(!uri) throw new Error('bad uri');

let registered=false;
let inviteSeen=false;
let established=false;
let rxPackets=0;
let rxBytes=0;
let peerConnected=false;
let peer;

class WeriftReceiveSDH {
  constructor() {
    this.pc=new RTCPeerConnection();
    peer=this.pc;
    this.pc.connectionStateChange.subscribe((s)=>{
      console.log('WEBRTC_CONNECTION_STATE',s);
      if(s==='connected') peerConnected=true;
    });
    this.pc.onTrack.subscribe((track)=>{
      console.log('WEBRTC_TRACK',track.kind);
      track.onReceiveRtp.subscribe((pkt)=>{
        rxPackets++;
        rxBytes += Number(pkt?.payload?.length || 0);
      });
    });
  }
  hasDescription(contentType){ return String(contentType||'').toLowerCase().includes('application/sdp'); }
  async setDescription(sdp){
    await this.pc.setRemoteDescription({type:'offer',sdp});
  }
  async getDescription(){
    const answer=await this.pc.createAnswer();
    await this.pc.setLocalDescription(answer);
    if(this.pc.iceGatheringState!=='complete'){
      await this.pc.iceGatheringStateChange.watch((s)=>s==='complete',5000).catch(()=>{});
    }
    const local=this.pc.localDescription;
    if(!local?.sdp) throw new Error('werift local SDP missing');
    console.log('WEBRTC_LOCAL_SDP_READY',this.pc.iceGatheringState);
    return {body:local.sdp,contentType:'application/sdp'};
  }
  async rollbackDescription(){}
  close(){ try{this.pc.close();}catch{} }
  sendDtmf(){ return false; }
}

const ua=new UserAgent({
  uri,
  transportOptions:{server:m.signaling_url},
  authorizationUsername:m.authorization_username,
  authorizationPassword:m.session_token,
  contactName:m.session_id,
  displayName:'BORIS WebRTC QA',
  logBuiltinEnabled:false,
  logConfiguration:false,
  sessionDescriptionHandlerFactory:()=>new WeriftReceiveSDH(),
  delegate:{
    onInvite: async (invitation)=>{
      inviteSeen=true;
      console.log('SIPJS_WEBRTC_INVITE_RECEIVED');
      invitation.stateChange.addListener((s)=>{
        if(s===SessionState.Established){established=true;console.log('SIPJS_WEBRTC_ESTABLISHED');}
      });
      try{
        await invitation.accept();
      }catch(e){
        console.error('SIPJS_WEBRTC_ACCEPT_ERROR',e?.stack||e);
      }
    }
  }
});
const reg=new Registerer(ua,{expires:90});
reg.stateChange.addListener((s)=>{
  if(s===RegistererState.Registered){registered=true;console.log('SIPJS_WEBRTC_REGISTERED');}
});
await ua.start();
await reg.register();
let deadline=Date.now()+8000;
while(!registered && Date.now()<deadline) await new Promise(r=>setTimeout(r,100));
if(!registered) throw new Error('register timeout');
deadline=Date.now()+15000;
while((!inviteSeen || !established || !peerConnected || rxPackets<5) && Date.now()<deadline){
  await new Promise(r=>setTimeout(r,100));
}
console.log('WEBRTC_RX_RESULT',JSON.stringify({inviteSeen,established,peerConnected,rxPackets,rxBytes}));
if(!inviteSeen) throw new Error('invite timeout');
if(!established) throw new Error('session not established');
if(!peerConnected) throw new Error('webrtc peer not connected');
if(rxPackets<5 || rxBytes<100) throw new Error('webrtc RTP not received');
await reg.unregister().catch(()=>{});
await ua.stop().catch(()=>{});
try{peer?.close();}catch{}
console.log('WEBRTC_RX_QA_OK');
