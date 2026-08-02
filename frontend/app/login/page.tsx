"use client";
import { useState, useEffect } from "react";
import { Mascot } from '../components/Mascot';
import { useRouter } from "next/navigation";
import TurnstileBox from "../lib/turnstile";
import { registerUser, persistSession, rememberVerification } from "../lib/register";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [company, setCompany] = useState("");
  const [planned, setPlanned] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState("");

  /* Сообщение, оставленное api.ts при выходе из-за неподтверждённой почты.
     Показываем один раз и сразу убираем ключ. */
  useEffect(() => {
    const note = localStorage.getItem("boris_login_note");
    if (note) {
      setError(note);
      localStorage.removeItem("boris_login_note");
    }
  }, []);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState("");
  const [captchaKey, setCaptchaKey] = useState(0);
  const router = useRouter();

  const handleSubmit = async () => {
    setError(""); setNotice("");
    if (!email || !password) {
      setError("Заполните email и пароль");
      return;
    }
    setLoading(true);
    try {
      if (mode === "register") {
        const r = await registerUser({
          email, password, accountName: company, plannedAccounts: planned,
          turnstileToken: token,
        });
        if (!r.ok) {
          setError(r.error);
          setToken(""); setCaptchaKey(k => k + 1);
          setLoading(false);
          return;
        }
        rememberVerification(r.data);
        const saved = persistSession(r.data, true);
        if (!saved) {
          setNotice("Мы отправили письмо на указанный адрес. Если аккаунт уже существует, войдите.");
          setMode("login");
          setToken(""); setCaptchaKey(k => k + 1);
          setLoading(false);
          return;
        }
        router.push("/verify");
        return;
      }
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();

      if (!res.ok || data.status === "error") {
        setError(data.message || data.detail || "Ошибка входа");
        setLoading(false);
        return;
      }

      persistSession(data, false);
      if (data.user?.status === "pending_verification") {
        rememberVerification(data);
        router.push("/verify");
        return;
      }
      if (data.user.role === "manager") { router.push("/manager"); } else { router.push("/dashboard/home"); }
    } catch (e) {
      setError("Не удалось связаться с сервером");
      setLoading(false);
    }
  };

  return (
    <div style={{background:"linear-gradient(160deg, #F6F7FB 0%, #E7EFFE 100%)", minHeight:"100vh", display:"flex", alignItems:"center", justifyContent:"center", fontFamily:"var(--font-inter), Arial, Helvetica, sans-serif"}}>
      <style>{`
        .boris-login-btn { transition: box-shadow 0.2s ease, transform 0.2s ease; }
        .boris-login-btn:hover:not(:disabled) { box-shadow: 0 4px 14px rgba(47,111,237,0.35); transform: translateY(-1px); }
        .boris-login-input:focus { outline: none; border-color: #2F6FED !important; box-shadow: 0 0 0 3px rgba(47,111,237,0.12); }
        .boris-login-switch:hover { color: #2F6FED !important; }
      `}</style>
      <div style={{background:"#FFFFFF", borderRadius:"16px", padding:"40px", width:"400px", boxSizing:"border-box", border:"1px solid #EEF2FA", boxShadow:"0 8px 24px rgba(16,24,40,0.08), 0 2px 6px rgba(16,24,40,0.04)"}}>
        <div style={{textAlign:"center", marginBottom:"28px"}}>
          <Mascot size={56} interactive={false} />
          <h1 style={{color:"#1D2939", fontSize:"23px", margin:"0", fontWeight:800, fontFamily:"var(--font-manrope)"}}>БОРИС</h1>
          <p style={{color:"#667085", fontSize:"15px", margin:"8px 0 0"}}>
            {mode === "login" ? "Войдите в свой аккаунт" : "Создайте аккаунт"}
          </p>
        </div>

        <div style={{marginBottom:"16px"}}>
          <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Email</label>
          <input
            className="boris-login-input"
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="your@email.com"
            style={{width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px", boxSizing:"border-box"}}
          />
        </div>

        <div style={{marginBottom:"20px"}}>
          <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Пароль</label>
          <input
            className="boris-login-input"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="••••••••"
            onKeyDown={e => e.key === "Enter" && handleSubmit()}
            style={{width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px", boxSizing:"border-box"}}
          />
        </div>

        {mode === "register" && (
          <>
            <div style={{marginBottom:"16px"}}>
              <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Название компании</label>
              <input
                className="boris-login-input"
                type="text"
                value={company}
                onChange={e => setCompany(e.target.value)}
                placeholder="ООО СтройПлит"
                style={{width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px", boxSizing:"border-box"}}
              />
            </div>
            <div style={{marginBottom:"20px"}}>
              <label style={{color:"#667085", fontSize:"15px", display:"block", marginBottom:"6px"}}>Планируется аккаунтов Авито</label>
              <input
                className="boris-login-input"
                type="text"
                value={planned}
                onChange={e => setPlanned(e.target.value)}
                placeholder="1-3"
                style={{width:"100%", background:"#FFFFFF", border:"1px solid #E3E7F0", borderRadius:"8px", padding:"10px", color:"#1D2939", fontSize:"15px", boxSizing:"border-box"}}
              />
            </div>
            <TurnstileBox onToken={setToken} resetKey={captchaKey} />
          </>
        )}

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
          onClick={handleSubmit}
          disabled={loading}
          style={{width:"100%", background: loading ? "#E3E7F0" : "#2F6FED", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"12px", fontSize:"15px", fontWeight:"bold", cursor: loading ? "default" : "pointer", marginBottom:"16px"}}>
          {loading ? "Загрузка..." : (mode === "login" ? "Войти" : "Зарегистрироваться")}
        </button>

        <div style={{textAlign:"center"}}>
          <button
            className="boris-login-switch"
            onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); setNotice(""); }}
            style={{background:"none", border:"none", color:"#667085", fontSize:"15px", cursor:"pointer", textDecoration:"underline"}}>
            {mode === "login" ? "Нет аккаунта? Зарегистрироваться" : "Уже есть аккаунт? Войти"}
          </button>
        </div>
      </div>
    </div>
  );
}
