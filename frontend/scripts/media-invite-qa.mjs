import fs from 'node:fs';
import { UserAgent, Registerer, RegistererState } from 'sip.js';

const m=JSON.parse(fs.readFileSync('/tmp/boris_media_invite_qa.json','utf8'));
const uri=UserAgent.makeURI(m.sip_uri);
if(!uri) throw new Error('bad uri');
let gotInvite=false;
let registered=false;
const ua=new UserAgent({
  uri,
  transportOptions:{server:m.signaling_url},
  authorizationUsername:m.authorization_username,
  authorizationPassword:m.session_token,
  contactName:m.session_id,
  displayName:'BORIS QA',
  logBuiltinEnabled:false,
  logConfiguration:false,
  delegate:{
    onInvite: async (invitation)=>{
      gotInvite=true;
      console.log('SIPJS_INVITE_RECEIVED');
      try{await invitation.reject()}catch{}
    }
  }
});
const reg=new Registerer(ua,{expires:90});
reg.stateChange.addListener((state)=>{
  if(state===RegistererState.Registered){registered=true;console.log('SIPJS_REGISTERED');}
});
await ua.start();
await reg.register();
let deadline=Date.now()+8000;
while(!registered && Date.now()<deadline) await new Promise(r=>setTimeout(r,100));
if(!registered) throw new Error('register timeout');
deadline=Date.now()+15000;
while(!gotInvite && Date.now()<deadline) await new Promise(r=>setTimeout(r,100));
if(!gotInvite) throw new Error('invite timeout');
await reg.unregister().catch(()=>{});
await ua.stop().catch(()=>{});
console.log('SIPJS_INVITE_DONE');
