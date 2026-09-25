import fs from 'node:fs';
import { UserAgent, Registerer, RegistererState, UserAgentState } from 'sip.js';

const m=JSON.parse(fs.readFileSync('/tmp/boris_media_register_qa.json','utf8'));
const uri=UserAgent.makeURI(m.sip_uri);
if(!uri) throw new Error('bad uri');
const ua=new UserAgent({
  uri,
  transportOptions:{server:m.signaling_url},
  authorizationUsername:m.authorization_username,
  authorizationPassword:m.session_token,
  contactName:m.session_id,
  displayName:'BORIS QA',
  logBuiltinEnabled:true,
  logLevel:'debug',
});
ua.stateChange.addListener((s)=>console.log('UA_STATE',s));
const reg=new Registerer(ua,{expires:Math.max(30,Math.min(180,Number(m.register_expires_seconds)||90))});
let registered=false;
reg.stateChange.addListener((state)=>{
  console.log('REG_STATE',state);
  if(state===RegistererState.Registered){registered=true;console.log('SIPJS_REGISTERED');}
});
await ua.start();
console.log('UA_STARTED');
await reg.register();
console.log('REGISTER_SENT');
const deadline=Date.now()+8000;
while(!registered && Date.now()<deadline) await new Promise(r=>setTimeout(r,100));
if(!registered) throw new Error('register timeout');
await new Promise(r=>setTimeout(r,2000));
await reg.unregister().catch(()=>{});
await ua.stop().catch(()=>{});
console.log('SIPJS_DONE');
