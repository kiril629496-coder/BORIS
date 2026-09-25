let recorder = null;
let mediaStream = null;
let chunks = [];
let recordingMeta = null;
let captureMeta = null;
let markers = [];
let frameCanvas = null;
let frameCtx = null;
let lastBlobUrl = null;

function stopTracks(){
  try{ for(const t of (mediaStream?.getTracks?.()||[])) t.stop(); }catch{}
  mediaStream = null;
  captureMeta = null;
}

function preferredMime(){
  const choices=['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'];
  return choices.find(x=>MediaRecorder.isTypeSupported(x))||'';
}

async function enableCaptureSession(msg){
  if(mediaStream?.active){
    return {ok:true,active:true,reused:true,tab_id:captureMeta?.tab_id||null,started_at:captureMeta?.started_at||null};
  }
  const streamId=String(msg.stream_id||'');
  if(!streamId) return {ok:false,error:'missing_stream_id'};
  const includeAudio=msg.include_audio===true;
  const videoConstraints={mandatory:{chromeMediaSource:'tab',chromeMediaSourceId:streamId}};
  const audioConstraints=includeAudio?{mandatory:{chromeMediaSource:'tab',chromeMediaSourceId:streamId}}:false;
  mediaStream=await navigator.mediaDevices.getUserMedia({video:videoConstraints,audio:audioConstraints});
  captureMeta={tab_id:Number(msg.tab_id||0)||null,started_at:new Date().toISOString(),include_audio:includeAudio};
  for(const track of mediaStream.getTracks()) track.addEventListener('ended',()=>{ captureMeta=null; });
  return {ok:true,active:true,tab_id:captureMeta.tab_id,started_at:captureMeta.started_at,tracks:mediaStream.getTracks().map(t=>({kind:t.kind,state:t.readyState}))};
}

async function disableCaptureSession(){
  if(recorder && recorder.state!=='inactive'){
    try{ recorder.stop(); }catch{}
  }
  stopTracks();
  recorder=null; chunks=[]; recordingMeta=null; markers=[]; frameCanvas=null; frameCtx=null;
  return {ok:true,active:false};
}

function beginRecorder(stream,msg,mode){
  if(recorder && recorder.state!=='inactive') return {ok:false,error:'recording_already_active'};
  if(lastBlobUrl){ try{ URL.revokeObjectURL(lastBlobUrl); }catch{} lastBlobUrl=null; }
  chunks=[]; markers=[];
  const mime=preferredMime();
  const opts={}; if(mime) opts.mimeType=mime;
  const bps=Math.max(300000,Math.min(Number(msg.video_bits_per_second||2500000),8000000));
  opts.videoBitsPerSecond=bps;
  recorder=new MediaRecorder(stream,opts);
  recordingMeta={
    started_at:new Date().toISOString(),
    filename:String(msg.filename||`BORIS-${Date.now()}.webm`),
    mime:recorder.mimeType||mime||'video/webm',
    video_bits_per_second:bps,
    include_audio:msg.include_audio===true,
    mode,
    server:String(msg.server||'').replace(/\/$/,''),
    token:String(msg.token||''),
    job_id:String(msg.job_id||''),
    account_id:String(msg.account_id||''),
    step_index:Number(msg.step_index||0),
    save_server:msg.save_server!==false,
  };
  recorder.ondataavailable=e=>{ if(e.data && e.data.size>0) chunks.push(e.data); };
  recorder.start(1000);
  return {ok:true,state:recorder.state,mime:recordingMeta.mime,started_at:recordingMeta.started_at,mode};
}

async function startRecording(msg){
  if(msg.stream_id && !mediaStream?.active) await enableCaptureSession(msg);
  if(!mediaStream?.active) return {ok:false,error:'capture_session_not_enabled'};
  return beginRecorder(mediaStream,msg,'tab_capture');
}

async function startTimelapse(msg){
  if(recorder && recorder.state!=='inactive') return {ok:false,error:'recording_already_active'};
  const fps=Math.max(1,Math.min(Number(msg.fps||2),2));
  frameCanvas=document.createElement('canvas');
  frameCanvas.width=Math.max(320,Math.min(Number(msg.width||1280),2560));
  frameCanvas.height=Math.max(180,Math.min(Number(msg.height||720),1440));
  frameCtx=frameCanvas.getContext('2d',{alpha:false});
  frameCtx.fillStyle='#000'; frameCtx.fillRect(0,0,frameCanvas.width,frameCanvas.height);
  const stream=frameCanvas.captureStream(fps);
  return beginRecorder(stream,{...msg,include_audio:false},'timelapse');
}

async function drawFrame(msg){
  if(!frameCanvas||!frameCtx||recordingMeta?.mode!=='timelapse') return {ok:false,error:'timelapse_not_active'};
  const src=String(msg.data_url||''); if(!src) return {ok:false,error:'frame_missing'};
  const img=new Image();
  await new Promise((resolve,reject)=>{ img.onload=resolve; img.onerror=()=>reject(new Error('frame_decode_failed')); img.src=src; });
  const cw=frameCanvas.width,ch=frameCanvas.height;
  const scale=Math.min(cw/img.naturalWidth,ch/img.naturalHeight);
  const w=Math.max(1,img.naturalWidth*scale),h=Math.max(1,img.naturalHeight*scale);
  const x=(cw-w)/2,y=(ch-h)/2;
  frameCtx.fillStyle='#000'; frameCtx.fillRect(0,0,cw,ch); frameCtx.drawImage(img,x,y,w,h);
  return {ok:true,width:cw,height:ch};
}

function addMarker(msg){
  if(!recordingMeta) return {ok:false,error:'recording_not_active'};
  const atMs=Math.max(0,Date.now()-Date.parse(recordingMeta.started_at));
  const row={at_ms:atMs,label:String(msg.label||msg.text||'marker').slice(0,240),meta:msg.meta||null};
  markers.push(row);
  return {ok:true,marker:row,count:markers.length};
}

async function uploadBlob(blob,meta){
  if(!meta?.save_server) return {ok:true,server_saved:false};
  if(!meta.server||!meta.token||!meta.job_id||!meta.account_id) return {ok:false,error:'video_upload_config_missing'};
  const q=new URLSearchParams({job_id:meta.job_id,account_id:meta.account_id,step_index:String(meta.step_index||0)});
  const r=await fetch(`${meta.server}/api/browser-gateway/transport/video-artifact?${q.toString()}`,{
    method:'POST',headers:{Authorization:`Bearer ${meta.token}`,'Content-Type':blob.type||'video/webm'},body:blob,
  });
  const text=await r.text(); let data={}; try{data=text?JSON.parse(text):{};}catch{}
  if(!r.ok) return {ok:false,error:data.detail||`video_upload_http_${r.status}`};
  return {ok:true,server_saved:true,server_path:data.path||null,server_bytes:Number(data.bytes||blob.size),server_mime:data.mime||blob.type||'video/webm'};
}

async function stopRecording(){
  if(!recorder || recorder.state==='inactive') return {ok:false,error:'recording_not_active'};
  const active=recorder; const meta={...recordingMeta};
  const stopped=new Promise(resolve=>active.addEventListener('stop',resolve,{once:true}));
  active.stop(); await stopped;
  const blob=new Blob(chunks,{type:meta.mime||'video/webm'});
  const durationMs=Math.max(0,Date.now()-Date.parse(meta.started_at||new Date().toISOString()));
  if(lastBlobUrl){ try{ URL.revokeObjectURL(lastBlobUrl); }catch{} }
  lastBlobUrl=URL.createObjectURL(blob);
  const upload=await uploadBlob(blob,meta);
  const out={
    ok:true,
    filename:meta.filename||`BORIS-${Date.now()}.webm`,mime:blob.type||'video/webm',bytes:blob.size,
    duration_ms:durationMs,started_at:meta.started_at||null,stopped_at:new Date().toISOString(),mode:meta.mode||'unknown',
    markers:[...markers],local_blob_url:lastBlobUrl,
    server_saved:!!upload.server_saved,server_path:upload.server_path||null,server_bytes:Number(upload.server_bytes||0)||null,
    server_mime:upload.server_mime||null,server_error:upload.ok===false?String(upload.error||'video_upload_failed'):null,
  };
  if(meta.mode==='timelapse'){
    try{ for(const t of active.stream?.getTracks?.()||[]) t.stop(); }catch{}
    frameCanvas=null; frameCtx=null;
  }
  recorder=null; chunks=[]; recordingMeta=null; markers=[];
  return out;
}

chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
  if(msg?.target!=='boris_offscreen_recorder') return;
  (async()=>{
    try{
      if(msg.type==='record_session_enable') sendResponse(await enableCaptureSession(msg));
      else if(msg.type==='record_session_disable') sendResponse(await disableCaptureSession());
      else if(msg.type==='record_start') sendResponse(await startRecording(msg));
      else if(msg.type==='record_start_timelapse') sendResponse(await startTimelapse(msg));
      else if(msg.type==='record_frame') sendResponse(await drawFrame(msg));
      else if(msg.type==='record_marker') sendResponse(addMarker(msg));
      else if(msg.type==='record_stop') sendResponse(await stopRecording());
      else if(msg.type==='record_status') sendResponse({ok:true,active:!!recorder&&recorder.state!=='inactive',state:recorder?.state||'inactive',recording:recordingMeta,capture_active:!!mediaStream?.active,capture:captureMeta,markers:[...markers]});
      else sendResponse({ok:false,error:'unknown_recorder_message'});
    }catch(e){ sendResponse({ok:false,error:String(e?.message||e)}); }
  })();
  return true;
});
