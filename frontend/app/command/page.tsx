"use client";
/**
 * командный центр BORIS — первая точка входа пользователя в командную шину.
 *
 * Вся работа с API идёт через lib/commandClient. Страница НЕ разбирает
 * текст ответа: человекочитаемое показывает как есть, действия строит
 * по data (route, clients, числа) и по proposed_changes.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAccounts } from "../lib/AccountContext";
import {
  sendCommand, confirmCommand, cancelCommand,
  commandHistory, commandCapabilities,
  transcribeVoice, speakVoice,
  type CommandResult, type HistoryItem, type Capabilities,
} from "../lib/commandClient";
import { Page, PageHeader, Card, Button, Badge, Alert, EmptyState } from "../ui";
import { color, space, font } from "../ui/tokens";
import { humanizeBusinessText } from "../lib/humanizeBusinessText";
import styles from "./command.module.css";

const EXAMPLES = [
  "открой сообщения",
  "покажи статистику за неделю",
  "поставь мне завтра перезвонить Иванову",
  "найди клиента",
];

const RISK_LABEL: Record<string, string> = {
  green: "чтение",
  yellow: "изменение данных",
  red: "внешнее действие",
};

const STATUS_LABEL: Record<string, string> = {
  done: "выполнено",
  awaiting_confirm: "ждёт подтверждения",
  failed: "не выполнено",
  cancelled: "отменено",
  expired: "просрочено",
};

export default function CommandPage() {
  const router = useRouter();
  const pathname = usePathname();
  const { selectedAccountId, mode, accounts } = useAccounts();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<CommandResult | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [recording, setRecording] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voicePreset, setVoicePreset] = useState("business_male");
  const [voiceReply, setVoiceReply] = useState(true);
  const [voiceError, setVoiceError] = useState("");
  const [voiceNotice, setVoiceNotice] = useState("");
  const [recordSeconds, setRecordSeconds] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedRef = useRef(0);
  const recordTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const meterTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const voiceSignalRef = useRef({ frames: 0, voiced: 0, maxRms: 0, measured: false });
  // COMMAND_UI_AMBIGUOUS_RETRY_IDEMPOTENCY_V1: keep one business UUID across
  // an ambiguous network failure. A successful/HTTP server response clears it;
  // changing account/text creates a new economic intent instead of reusing a key.
  const commandIntentRef = useRef<{ key: string; signature: string } | null>(null);

  // В режиме «Все аккаунты» команду выполнить нельзя: бэкенд требует
  // конкретный account_id, режим all — состояние интерфейса.
  const account = mode === "all" ? "" : selectedAccountId || "";

  const reload = useCallback(async () => {
    setHistory(await commandHistory(15));
    if (account) setCaps(await commandCapabilities(account));
  }, [account]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    commandIntentRef.current = null;
  }, [account]);

  const send = useCallback(async () => {
    const t = text.trim();
    if (!t || busy || !account) return;
    setBusy(true);
    setRes(null);
    const signature = `${account}|text|${t}`;
    if (!commandIntentRef.current || commandIntentRef.current.signature !== signature) {
      const key = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
      commandIntentRef.current = { key, signature };
    }
    const r = await sendCommand(t, account, {
      route: pathname || "/command",
      section: "command",
      commandId: commandIntentRef.current.key,
    });
    // Only a network failure is ambiguous: the server may have executed while
    // the response was lost. Keep the UUID for an explicit retry in that case.
    if (r.error_code !== "network") commandIntentRef.current = null;
    setRes(r);
    setBusy(false);
    const directRoute = (r.data || {}).route as string | undefined;
    if (r.operation === "ui.navigate" && directRoute) {
      window.setTimeout(() => router.push(directRoute), 450);
    }
    if (r.status !== "awaiting_confirm") {
      setText("");
      reload();
    }
  }, [text, busy, account, reload, pathname, router]);

  const doConfirm = useCallback(async () => {
    if (!res || busy) return;
    setBusy(true);
    const r = await confirmCommand(res.command_id);
    setRes(r);
    setBusy(false);
    setText("");
    reload();
  }, [res, busy, reload]);

  const doCancel = useCallback(async () => {
    if (!res || busy) return;
    setBusy(true);
    await cancelCommand(res.command_id);
    setRes(null);
    setBusy(false);
    setText("");
    reload();
  }, [res, busy, reload]);

  const playSpeech = useCallback(async (answer: string) => {
    if (!account || !answer) return;
    setVoiceBusy(true);
    try {
      const out = await speakVoice(answer, account, voicePreset);
      const url = URL.createObjectURL(out.blob);
      const audio = new Audio(url);
      audio.volume = 1;
      // Голос BORIS должен быть заметно громче обычного media playback.
      // WebAudio gain > 1 усиливает сигнал; если API недоступен, остаётся обычный Audio.
      let audioContext: AudioContext | null = null;
      try {
        const AC = window.AudioContext || (window as any).webkitAudioContext;
        if (AC) {
          audioContext = new AC();
          const source = audioContext.createMediaElementSource(audio);
          const gain = audioContext.createGain();
          gain.gain.value = 1.45;
          source.connect(gain);
          gain.connect(audioContext.destination);
          if (audioContext.state === "suspended") await audioContext.resume();
        }
      } catch {
        audioContext = null;
      }
      audio.onended = () => {
        URL.revokeObjectURL(url);
        if (audioContext) void audioContext.close();
      };
      await audio.play();
    } finally { setVoiceBusy(false); }
  }, [account, voicePreset]);

  const toggleRecord = useCallback(async () => {
    if (recording) { recorderRef.current?.stop(); return; }
    setVoiceError("");
    setVoiceNotice("");
    setRecordSeconds(0);
    if (!account) { setVoiceError("Сначала выберите аккаунт."); return; }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setVoiceError("Этот браузер не поддерживает запись с микрофона."); return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio:true});
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      startedRef.current = Date.now();
      voiceSignalRef.current = { frames: 0, voiced: 0, maxRms: 0, measured: false };
      recordTimerRef.current = setInterval(() => {
        setRecordSeconds(Math.max(0, Math.floor((Date.now() - startedRef.current) / 1000)));
      }, 250);
      try {
        const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
        if (AudioCtx) {
          const actx: AudioContext = new AudioCtx();
          audioCtxRef.current = actx;
          voiceSignalRef.current.measured = true;
          const analyser = actx.createAnalyser();
          analyser.fftSize = 1024;
          actx.createMediaStreamSource(stream).connect(analyser);
          const samples = new Uint8Array(analyser.fftSize);
          meterTimerRef.current = setInterval(() => {
            analyser.getByteTimeDomainData(samples);
            let sum = 0;
            for (let i = 0; i < samples.length; i++) {
              const v = (samples[i] - 128) / 128;
              sum += v * v;
            }
            const rms = Math.sqrt(sum / samples.length);
            const sig = voiceSignalRef.current;
            sig.frames += 1;
            if (rms >= 0.012) sig.voiced += 1;
            if (rms > sig.maxRms) sig.maxRms = rms;
          }, 100);
        }
      } catch { /* meter is a safety enhancement; recording still works */ }
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      rec.onerror = () => { setVoiceError("Не удалось записать голос. Попробуйте ещё раз."); setRecording(false); };
      rec.onstop = async () => {
        stream.getTracks().forEach((t)=>t.stop());
        if (recordTimerRef.current) { clearInterval(recordTimerRef.current); recordTimerRef.current = null; }
        if (meterTimerRef.current) { clearInterval(meterTimerRef.current); meterTimerRef.current = null; }
        const actx = audioCtxRef.current; audioCtxRef.current = null;
        if (actx) { try { await actx.close(); } catch {} }
        setRecording(false); setVoiceBusy(true);
        try {
          const duration = Math.max(.1, (Date.now() - startedRef.current) / 1000);
          setRecordSeconds(Math.floor(duration));
          const sig = voiceSignalRef.current;
          const hasSpeech = !sig.measured || sig.voiced >= 4 || sig.maxRms >= 0.035;
          if (!hasSpeech) {
            const hint = "На связи. Скажите, что нужно сделать — найти клиента, создать объявление или показать цифры.";
            setVoiceNotice(hint);
            setText("");
            if (voiceReply) await playSpeech(hint);
            return;
          }
          const blob = new Blob(chunksRef.current, {type: rec.mimeType || "audio/webm"});
          if (!blob.size) throw new Error("Пустая запись");
          const tr = await transcribeVoice(blob, account, duration);
          const spoken = String(tr.text || "").trim();
          if (!spoken) {
            const hint = "На связи. Повторите, пожалуйста, вопрос — я слушаю.";
            setVoiceNotice(hint);
            if (voiceReply) await playSpeech(hint);
            return;
          }
          setText(spoken);
          const r = await sendCommand(spoken, account, {route: pathname || "/command", section:"command", source:"voice"});
          setRes(r); reload();
          const answer = String(r.answer || r.human_summary || r.clarify_question || r.message || "").trim();
          const voiceAnswer = r.status === "awaiting_confirm" && answer
            ? answer + ". Подтвердите действие на экране."
            : answer;
          if (voiceReply && voiceAnswer) await playSpeech(voiceAnswer);
          const directRoute = (r.data || {}).route as string | undefined;
          if (r.operation === "ui.navigate" && directRoute) {
            window.setTimeout(() => router.push(directRoute), 450);
          }
        } catch (e: any) {
          setVoiceError(e?.message || "Не удалось обработать голосовое. Попробуйте ещё раз.");
        } finally { setVoiceBusy(false); }
      };
      recorderRef.current = rec; rec.start(); setRecording(true);
    } catch (e: any) {
      setRecording(false);
      setVoiceError(e?.name === "NotAllowedError" ? "Разрешите доступ к микрофону в браузере и попробуйте снова." : "Не удалось включить микрофон.");
    }
  }, [recording, account, pathname, reload, voiceReply, playSpeech, router]);

  const route = (res?.data || {}).route as string | undefined;
  const routeTitle = (res?.data || {}).route_title as string | undefined;
  const resultHint = (res?.data || {}).hint as string | undefined;
  const resultSteps = ((res?.data || {}).steps || []) as any[];
  const clients = (res?.data || {}).clients as any[] | undefined;
  const pool = caps?.pool;
  // История приходит по пользователю и смешивает все его аккаунты —
  // без подписи список нечитаем.
  const accName = (id?: string | null) =>
    (accounts.find((a) => a.account_id === id)?.name || "").slice(0, 22);

  return (
      <Page className={styles.page}>
      <div className={styles.hero}>
        <div className={styles.borisMark}>B</div>
        <PageHeader
          title="Спросите BORIS"
          subtitle="Напишите задачу обычными словами. BORIS поймёт текущий аккаунт, покажет результат и отдельно попросит подтверждение перед важным внешним действием."
        />
      </div>

      {mode === "all" && (
        <Alert kind="warning">
          Выбран режим «Все аккаунты». Команда выполняется в одном аккаунте —
          выберите его в шапке.
        </Alert>
      )}
      {mode !== "all" && !account && accounts.length === 0 && (
        <Alert kind="warning">Сначала подключите аккаунт Авито.</Alert>
      )}
      {caps && caps.status === "error" && (
        <Alert kind="warning">
          {caps.error_code === "no_billing_owner"
            ? "У аккаунта не задан владелец — команды недоступны."
            : "командный центр BORIS для этого аккаунта не подключён."}
        </Alert>
      )}
      {caps && caps.status === "ok" && !caps.features?.command_center_enabled && (
        <Alert kind="warning">
          Для этого аккаунта командный центр BORIS не подключён — команды выполняться
          не будут. Выберите другой аккаунт или подключите тариф.
        </Alert>
      )}
      {caps && caps.status === "ok" && !!caps.features?.command_center_enabled &&
        !(caps.pool && caps.pool.purchased > 0) && (
        <Alert kind="warning">
          Пакет команд для этого аккаунта не оплачен.
        </Alert>
      )}

      <Card className={styles.card}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
          placeholder="Например: поставь мне завтра перезвонить Иванову"
          rows={3}
          disabled={!account || busy}
          style={{
            width: "100%", padding: space.md, borderRadius: 12,
            border: "1px solid " + color.line, ...font.body,
            resize: "vertical",
          }}
        />
        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(170px,1fr))",gap:8,marginTop:space.sm}}>
          {[
            ["business_male","👨‍💼","Деловой мужчина"],
            ["friendly_male","👨","Дружелюбный мужчина"],
            ["business_female","👩‍💼","Деловая женщина"],
            ["friendly_female","👩","Дружелюбная женщина"],
          ].map(([id,icon,label])=><button key={id} type="button" onClick={()=>setVoicePreset(id)} style={{border:voicePreset===id?"2px solid #315bea":"1px solid "+color.line,borderRadius:12,padding:"9px 10px",background:"#fff",cursor:"pointer",textAlign:"left",fontWeight:700}}>{icon} {label}</button>)}
        </div>
        <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginTop:10}}>
          <Button kind={recording ? "danger" : "ghost"} onClick={toggleRecord} disabled={!account || voiceBusy}>{recording ? `■ Остановить · ${String(Math.floor(recordSeconds / 60)).padStart(2,"0")}:${String(recordSeconds % 60).padStart(2,"0")}` : "🎙 Говорить с BORIS"}</Button>
          <label style={{...font.small,display:"flex",gap:6,alignItems:"center"}}><input type="checkbox" checked={voiceReply} onChange={(e)=>setVoiceReply(e.target.checked)}/> Отвечать голосом</label>
          {voiceBusy && <span style={font.small}>Обрабатываю голос…</span>}
          {voiceNotice && !voiceError && <span style={{...font.small,color:color.muted}}>{voiceNotice}</span>}
          {voiceError && <span style={{...font.small,color:"#b42318"}}>{voiceError}</span>}
        </div>
        <div style={{ display: "flex", gap: space.sm, alignItems: "center",
                      marginTop: space.sm, flexWrap: "wrap" }}>
          <Button onClick={send} disabled={!account || busy || !text.trim()}>
            {busy ? "Думаю…" : "Отправить"}
          </Button>
          <span style={{ ...font.small, color: color.muted }}>
            Ctrl+Enter — отправить
          </span>
          {pool && (
            <span style={{ marginLeft: "auto", ...font.small,
                           color: color.muted }}>
              Команд использовано {pool.used} из {pool.purchased}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: space.xs, flexWrap: "wrap",
                      marginTop: space.sm }}>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => setText(ex)}
              style={{
                border: "1px solid " + color.line, background: "transparent",
                borderRadius: 999, padding: "4px 12px", cursor: "pointer",
                ...font.small, color: color.muted,
              }}
            >
              {ex}
            </button>
          ))}
        </div>
      </Card>
      {res && (
        <Card className={styles.card}>
          <div style={{ display: "flex", gap: space.sm, alignItems: "center",
                        marginBottom: space.sm, flexWrap: "wrap" }}>
            <Badge>{STATUS_LABEL[res.status] || humanizeBusinessText(res.status,"Состояние уточняется")}</Badge>
            {res.risk_level && (
              <Badge>{RISK_LABEL[res.risk_level] || res.risk_level}</Badge>
            )}
            {!!res.units && <Badge>{res.units} команд(ы)</Badge>}
          </div>

          {res.clarify_question && (
            <p style={{ ...font.body }}>{res.clarify_question}</p>
          )}
          {res.human_summary && res.status === "awaiting_confirm" && (
            <p style={{ ...font.body }}>{res.human_summary}</p>
          )}
          {res.answer && (
            <p style={{ ...font.body, whiteSpace: "pre-line" }}>{res.answer}</p>
          )}
          {!res.answer && res.message && (
            <p style={{ ...font.body, color: color.muted }}>{res.message}</p>
          )}

          {resultHint && (
            <div style={{marginTop:space.sm,padding:"10px 12px",border:"1px solid "+color.line,borderRadius:12,background:"rgba(49,91,234,.04)",...font.small}}>
              <b>Что делать дальше:</b> {resultHint}
            </div>
          )}

          {!!resultSteps.length && (
            <div style={{marginTop:space.md}}>
              <div style={{...font.h3,marginBottom:8}}>Путь до результата</div>
              <div style={{display:"grid",gap:8}}>
                {resultSteps.map((st:any,i:number)=>(
                  <button key={i} type="button" onClick={()=>st.route && router.push(st.route)} style={{display:"grid",gridTemplateColumns:"34px 1fr auto",gap:10,alignItems:"center",width:"100%",textAlign:"left",border:"1px solid "+color.line,borderRadius:12,padding:"10px 12px",background:"#fff",cursor:st.route?"pointer":"default"}}>
                    <span style={{width:28,height:28,borderRadius:9,display:"grid",placeItems:"center",background:"rgba(49,91,234,.08)",fontWeight:800}}>{st.state === "done" ? "✓" : (st.n || i+1)}</span>
                    <span><b>{st.title}</b>{st.state === "active" ? <span style={{...font.small,marginLeft:8,color:color.muted}}>сейчас</span> : null}<br/><span style={{...font.small,color:color.muted}}>{st.hint}</span></span>
                    {st.route && <span style={{...font.small,fontWeight:700}}>Открыть →</span>}
                  </button>
                ))}
              </div>
            </div>
          )}

          {!!(res.proposed_changes || []).length && (
            <div style={{ marginTop: space.sm, borderTop: "1px solid " + color.line,
                          paddingTop: space.sm }}>
              {(res.proposed_changes || []).map((c, i) => (
                <div key={i} style={{ ...font.small, marginBottom: 4 }}>
                  <b>{c.field || c.entity}</b>{" — "}
                  <span style={{ color: color.muted }}>
                    {c.before === null || c.before === undefined || c.before === ""
                      ? "пусто"
                      : String(c.before)}
                  </span>
                  {" → "}
                  <b>{String(c.after)}</b>
                </div>
              ))}
            </div>
          )}

          <div style={{ display: "flex", gap: space.sm, marginTop: space.md,
                        flexWrap: "wrap" }}>
            {res.status === "awaiting_confirm" && (
              <>
                <Button onClick={doConfirm} disabled={busy}>Подтвердить</Button>
                <Button kind="secondary" onClick={doCancel} disabled={busy}>
                  Отменить
                </Button>
              </>
            )}
            {route && (
              <Button onClick={() => router.push(route)}>Перейти: {routeTitle || "в раздел"}</Button>
            )}
          </div>

          {!!(clients || []).length && (
            <div style={{ marginTop: space.md }}>
              {(clients || []).slice(0, 8).map((c: any, i: number) => (
                <div key={i} style={{ ...font.small, padding: "6px 0",
                                      borderTop: "1px solid " + color.line }}>
                  <b>{c.name}</b>
                  {c.item ? " · " + c.item : ""}
                  {c.has_phone ? " · телефон есть" : ""}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      <Card className={styles.card}>
        <h3 style={{ ...font.h3, marginBottom: space.sm }}>Последние команды</h3>
        {history.length === 0 ? (
          <EmptyState title="Пока пусто"
                      hint="Здесь появятся выполненные команды" />
        ) : (
          history.map((h) => (
            <div key={h.command_id}
                 style={{ display: "flex", gap: space.sm, padding: "8px 0",
                          borderTop: "1px solid " + color.line,
                          ...font.small, alignItems: "baseline" }}>
              <span style={{ color: color.muted, minWidth: 120 }}>
                {h.created_at ? h.created_at.slice(0, 16).replace("T", " ") : ""}
              </span>
              <span className="boris-f3-6253876a649a63">{h.summary || h.operation || "—"}</span>
              <span style={{ color: color.muted }}>{accName(h.account_id)}</span>
              <Badge>{STATUS_LABEL[h.status] || humanizeBusinessText(h.status,"Состояние уточняется")}</Badge>
            </div>
          ))
        )}
      </Card>
    </Page>
  );
}
