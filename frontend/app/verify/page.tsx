"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import TurnstileBox from "../lib/turnstile";
import {
  verifyEmail, resendCode, changeVerificationEmail,
  persistSession, rememberVerification, forgetVerification,
  VERIFY_ID_KEY, VERIFY_MASK_KEY,
} from "../lib/register";

const CELLS = 6;
const COOLDOWN_SEC = 60;

const cardStyle: React.CSSProperties = {
  background:"#FFFFFF", borderRadius:"16px", padding:"40px", width:"400px",
  boxSizing:"border-box", border:"1px solid #EEF2FA",
  boxShadow:"0 8px 24px rgba(16,24,40,0.08), 0 2px 6px rgba(16,24,40,0.04)",
};
const inputStyle: React.CSSProperties = {
  width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px",
  padding:"10px", color:"#1D2939", fontSize:"15px", boxSizing:"border-box",
};
const btnStyle = (disabled: boolean): React.CSSProperties => ({
  width:"100%", background: disabled ? "#E3E7F0" : "#2F6FED", color:"#FFFFFF",
  border:"none", borderRadius:"8px", padding:"12px", fontSize:"15px",
  fontWeight:"bold", cursor: disabled ? "default" : "pointer", marginBottom:"16px",
});

export default function VerifyEmail() {
  const router = useRouter();
  const [digits, setDigits] = useState<string[]>(Array(CELLS).fill(""));
  const [verificationId, setVerificationId] = useState("");
  const [masked, setMasked] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [locked, setLocked] = useState(false);
  const [seconds, setSeconds] = useState(COOLDOWN_SEC);
  const [token, setToken] = useState("");
  const [captchaKey, setCaptchaKey] = useState(0);
  const [showChange, setShowChange] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const cells = useRef<Array<HTMLInputElement | null>>([]);

  useEffect(() => {
    if (typeof localStorage === "undefined") return;
    setVerificationId(localStorage.getItem(VERIFY_ID_KEY) || "");
    setMasked(localStorage.getItem(VERIFY_MASK_KEY) || "");
    const t = setTimeout(() => cells.current[0]?.focus(), 50);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (seconds <= 0) return;
    const t = setTimeout(() => setSeconds(s => s - 1), 1000);
    return () => clearTimeout(t);
  }, [seconds]);

  const code = digits.join("");

  const setCell = (i: number, v: string) => {
    const next = digits.slice();
    next[i] = v;
    setDigits(next);
  };

  const onChange = (i: number, raw: string) => {
    const only = raw.replace(/\D/g, "");
    if (!only) { setCell(i, ""); return; }
    if (only.length === 1) {
      setCell(i, only);
      if (i < CELLS - 1) cells.current[i + 1]?.focus();
      return;
    }
    const next = digits.slice();
    for (let k = 0; k < only.length && i + k < CELLS; k++) next[i + k] = only[k];
    setDigits(next);
    const land = Math.min(i + only.length, CELLS - 1);
    cells.current[land]?.focus();
  };

  const onKeyDown = (i: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { submit(); return; }
    if (e.key === "Backspace" && !digits[i] && i > 0) {
      cells.current[i - 1]?.focus();
      setCell(i - 1, "");
    }
    if (e.key === "ArrowLeft" && i > 0) cells.current[i - 1]?.focus();
    if (e.key === "ArrowRight" && i < CELLS - 1) cells.current[i + 1]?.focus();
  };

  const onPaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const only = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, CELLS);
    if (!only) return;
    e.preventDefault();
    const next = Array(CELLS).fill("");
    for (let k = 0; k < only.length; k++) next[k] = only[k];
    setDigits(next);
    cells.current[Math.min(only.length, CELLS - 1)]?.focus();
  };

  const submit = async () => {
    setError(""); setNotice("");
    if (!verificationId) {
      setError("Не найден запрос на подтверждение. Запросите новый код.");
      return;
    }
    if (code.length !== CELLS) { setError("Введите все шесть цифр"); return; }
    setLoading(true);
    const r = await verifyEmail(verificationId, code);
    setLoading(false);
    if (!r.ok) {
      setError(r.error);
      if (r.code === "code_locked" || r.code === "code_expired" || r.code === "code_used") {
        setLocked(true);
      }
      setDigits(Array(CELLS).fill(""));
      cells.current[0]?.focus();
      return;
    }
    forgetVerification();
    persistSession(r.data, true);
    if (r.data.user?.role === "manager") router.push("/manager");
    else router.push("/dashboard/home");
  };

  const doResend = async () => {
    setError(""); setNotice("");
    if (!token) { setError("Подтвердите, что вы не робот."); return; }
    setLoading(true);
    const r = await resendCode(token);
    setLoading(false);
    setToken(""); setCaptchaKey(k => k + 1);
    if (!r.ok) { setError(r.error); if (r.retryAfter) setSeconds(r.retryAfter); return; }
    if (r.data.verification_id) {
      rememberVerification(r.data);
      setVerificationId(r.data.verification_id);
      if (r.data.email_masked) setMasked(r.data.email_masked);
    }
    setDigits(Array(CELLS).fill(""));
    setLocked(false);
    setSeconds(COOLDOWN_SEC);
    setNotice("Новый код отправлен. Предыдущий больше не действует.");
    cells.current[0]?.focus();
  };

  const doChangeEmail = async () => {
    setError(""); setNotice("");
    if (!newEmail) { setError("Укажите новый адрес"); return; }
    if (!token) { setError("Подтвердите, что вы не робот."); return; }
    setLoading(true);
    const r = await changeVerificationEmail(newEmail, token);
    setLoading(false);
    setToken(""); setCaptchaKey(k => k + 1);
    if (!r.ok) { setError(r.error); return; }
    if (r.data.verification_id) {
      rememberVerification(r.data);
      setVerificationId(r.data.verification_id);
    }
    if (r.data.email_masked) setMasked(r.data.email_masked);
    setDigits(Array(CELLS).fill(""));
    setLocked(false);
    setShowChange(false);
    setNewEmail("");
    setSeconds(COOLDOWN_SEC);
    setNotice("Код отправлен на новый адрес.");
    cells.current[0]?.focus();
  };

  return (
    <div style={{background:"linear-gradient(160deg, #F6F7FB 0%, #E7EFFE 100%)", minHeight:"100vh", display:"flex", alignItems:"center", justifyContent:"center", fontFamily:"var(--font-inter), Arial, Helvetica, sans-serif"}}>
      <style>{`
        .boris-login-btn { transition: box-shadow 0.2s ease, transform 0.2s ease; }
        .boris-login-btn:hover:not(:disabled) { box-shadow: 0 4px 14px rgba(47,111,237,0.35); transform: translateY(-1px); }
        .boris-code-cell:focus { outline: none; border-color: #2F6FED !important; box-shadow: 0 0 0 3px rgba(47,111,237,0.12); }
        .boris-login-input:focus { outline: none; border-color: #2F6FED !important; box-shadow: 0 0 0 3px rgba(47,111,237,0.12); }
        .boris-login-switch:hover { color: #2F6FED !important; }
      `}</style>

      <div style={cardStyle}>
        <div style={{textAlign:"center", marginBottom:"24px"}}>
          <div style={{fontSize:"39px", marginBottom:"10px"}}>📬</div>
          <h1 style={{color:"#1D2939", fontSize:"23px", margin:"0", fontWeight:800, fontFamily:"var(--font-manrope)"}}>Подтвердите почту</h1>
          <p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>
            Код отправлен на {masked || "указанный адрес"}
          </p>
        </div>

        <div style={{display:"flex", gap:"8px", justifyContent:"space-between", marginBottom:"20px"}}>
          {digits.map((d, i) => (
            <input
              key={i}
              ref={el => { cells.current[i] = el; }}
              className="boris-code-cell"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={1}
              value={d}
              onChange={e => onChange(i, e.target.value)}
              onKeyDown={e => onKeyDown(i, e)}
              onPaste={onPaste}
              style={{width:"46px", height:"54px", textAlign:"center", fontSize:"22px", fontWeight:700, background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", color:"#1D2939", boxSizing:"border-box"}}
            />
          ))}
        </div>

        {error && (
          <div style={{background:"#FDEDEC", border:"1px solid #F04438", color:"#F04438", fontSize:"15px", borderRadius:"8px", padding:"10px 14px", marginBottom:"16px", textAlign:"center"}}>
            {error}
          </div>
        )}
        {notice && (
          <div style={{background:"#ECFDF3", border:"1px solid #12B76A", color:"#027A48", fontSize:"15px", borderRadius:"8px", padding:"10px 14px", marginBottom:"16px", textAlign:"center"}}>
            {notice}
          </div>
        )}

        <button
          className="boris-login-btn"
          onClick={submit}
          disabled={loading || locked}
          style={btnStyle(loading || locked)}>
          {loading ? "Проверяем..." : "Подтвердить"}
        </button>

        <div style={{borderTop:"1px solid #EEF2FA", paddingTop:"16px"}}>
          {seconds > 0 && !locked ? (
            <p style={{color:"#667085", fontSize:"14px", textAlign:"center", margin:"0 0 8px"}}>
              Запросить новый код можно через {seconds} с
            </p>
          ) : (
            <>
              <TurnstileBox onToken={setToken} resetKey={captchaKey} />
              <button
                className="boris-login-btn"
                onClick={doResend}
                disabled={loading}
                style={btnStyle(loading)}>
                Отправить код повторно
              </button>
            </>
          )}

          <div style={{textAlign:"center"}}>
            <button
              className="boris-login-switch"
              onClick={() => { setShowChange(!showChange); setError(""); setNotice(""); }}
              style={{background:"none", border:"none", color:"#667085", fontSize:"15px", cursor:"pointer", textDecoration:"underline"}}>
              {showChange ? "Отмена" : "Указан неверный адрес?"}
            </button>
          </div>

          {showChange && (
            <div style={{marginTop:"16px"}}>
              <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Новый email</label>
              <input
                className="boris-login-input"
                type="email"
                value={newEmail}
                onChange={e => setNewEmail(e.target.value)}
                placeholder="your@email.com"
                style={{...inputStyle, marginBottom:"12px"}}
              />
              <TurnstileBox onToken={setToken} resetKey={captchaKey} />
              <button
                className="boris-login-btn"
                onClick={doChangeEmail}
                disabled={loading}
                style={btnStyle(loading)}>
                Сменить адрес и получить код
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
