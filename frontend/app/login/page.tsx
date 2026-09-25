"use client";
import { useState, useEffect } from "react";
import { Mascot } from '../components/Mascot';
import { useRouter } from "next/navigation";
import TurnstileBox from "../lib/turnstile";
import { registerUser, persistSession, rememberVerification } from "../lib/register";
import { resolvePostAuthRoute } from "../lib/onboardingClient";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [company, setCompany] = useState("");
  const [planned, setPlanned] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState("");
  const [otpChallenge, setOtpChallenge] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [otpEmail, setOtpEmail] = useState("");

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

  const getDeviceKey = () => {
    let key = localStorage.getItem("boris_device_key") || "";
    if (!key) {
      key = typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : "dev_" + Date.now() + "_" + Math.random().toString(36).slice(2);
      localStorage.setItem("boris_device_key", key);
    }
    return key;
  };

  const finishLogin = async (data: any) => {
    persistSession(data, false);
    if (data.user?.status === "pending_verification") {
      rememberVerification(data);
      router.push("/verify");
      return;
    }
    const phoneReturn = localStorage.getItem("boris_post_login_return");
    if (phoneReturn === "/phone-app") {
      localStorage.removeItem("boris_post_login_return");
      router.push(phoneReturn);
    } else {
      router.push(await resolvePostAuthRoute(data.user));
    }
  };

  const handleOtpVerify = async () => {
    setError(""); setNotice("");
    if (!otpChallenge || !/^\d{6}$/.test(otpCode)) {
      setError("Введите шестизначный код из письма");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login/verify-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ challenge_id: otpChallenge, code: otpCode, device_key: getDeviceKey() }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "ok") {
        setError(data.message || data.detail || "Неверный код");
        setLoading(false);
        return;
      }
      setOtpChallenge(""); setOtpCode(""); setOtpEmail("");
      await finishLogin(data);
    } catch {
      setError("Не удалось подтвердить код");
      setLoading(false);
    }
  };

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
        body: JSON.stringify({ email, password, device_key: getDeviceKey() }),
      });
      const data = await res.json();

      if (!res.ok || data.status === "error") {
        setError(data.message || data.detail || "Ошибка входа");
        setLoading(false);
        return;
      }

      if (data.status === "otp_required") {
        setOtpChallenge(String(data.challenge_id || ""));
        setOtpEmail(String(data.email_masked || email));
        setOtpCode("");
        setNotice(data.message || "Код входа отправлен на почту главного администратора.");
        setLoading(false);
        return;
      }

      await finishLogin(data);
    } catch (e) {
      setError("Не удалось связаться с сервером");
      setLoading(false);
    }
  };

  return (
    <div className="boris-f3-5ec71747f75ff2">
      <style>{`
        .boris-login-btn { transition: box-shadow 0.2s ease, transform 0.2s ease; }
        .boris-login-btn:hover:not(:disabled) { box-shadow: 0 4px 14px rgba(47,111,237,0.35); transform: translateY(-1px); }
        .boris-login-input:focus { outline: none; border-color: var(--boris-blue) !important; box-shadow: 0 0 0 3px rgba(47,111,237,0.12); }
        .boris-login-switch:hover { color: var(--boris-blue) !important; }
      `}</style>
      <div className="boris-f3-097ea813ca3816">
        <div className="boris-f3-ded15b2cbcc769">
          <Mascot size={56} interactive={false} />
          <h1 className="boris-f3-558f82db59a6f3">БОРИС</h1>
          <p className="boris-f3-8ae40026f8ce40">
            {mode === "login" ? "Войдите в свой аккаунт" : "Создайте аккаунт"}
          </p>
        </div>

        <div className="boris-f3-7564ec607ed8ff">
          <label className="boris-f3-916bac74f4620e">Email</label>
          <input
            className="boris-login-input boris-f3-0dbf61c02b0088"
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="your@email.com"

          />
        </div>

        <div className="boris-f3-3c4a3d4400740b">
          <label className="boris-f3-916bac74f4620e">Пароль</label>
          <input
            className="boris-login-input boris-f3-0dbf61c02b0088"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="••••••••"
            onKeyDown={e => e.key === "Enter" && (otpChallenge ? handleOtpVerify() : handleSubmit())}

          />
        </div>

        {mode === "login" && otpChallenge && (
          <div className="boris-f3-3c4a3d4400740b">
            <label className="boris-f3-916bac74f4620e">Код из письма {otpEmail ? `· ${otpEmail}` : ""}</label>
            <input
              className="boris-login-input boris-f3-0dbf61c02b0088"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={otpCode}
              onChange={e => setOtpCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="000000"
              onKeyDown={e => e.key === "Enter" && handleOtpVerify()}
            />
            <div style={{fontSize:12,color:"#64748b",marginTop:6}}>Это новый телефон или браузер. После правильного кода устройство станет доверенным.</div>
          </div>
        )}

        {mode === "register" && (
          <>
            <div className="boris-f3-7564ec607ed8ff">
              <label className="boris-f3-916bac74f4620e">Название компании</label>
              <input
                className="boris-login-input boris-f3-0dbf61c02b0088"
                type="text"
                value={company}
                onChange={e => setCompany(e.target.value)}
                placeholder="ООО СтройПлит"

              />
            </div>
            <div className="boris-f3-3c4a3d4400740b">
              <label className="boris-f3-916bac74f4620e">Планируется аккаунтов Авито</label>
              <input
                className="boris-login-input boris-f3-0dbf61c02b0088"
                type="text"
                value={planned}
                onChange={e => setPlanned(e.target.value)}
                placeholder="1-3"

              />
            </div>
            <TurnstileBox onToken={setToken} resetKey={captchaKey} />
          </>
        )}

        {error && (
          <div className="boris-f3-91663a8b35f9c7">
            {error}
          </div>
        )}

        {notice && (
          <div className="boris-f3-4726c30c591844">
            {notice}
          </div>
        )}

        <button
          className="boris-login-btn"
          onClick={otpChallenge ? handleOtpVerify : handleSubmit}
          disabled={loading}
          style={{width:"100%", background: loading ? "var(--boris-border)" : "var(--boris-blue)", color:"#FFFFFF", border:"none", borderRadius:"8px", padding:"12px", fontSize:"15px", fontWeight:"bold", cursor: loading ? "default" : "pointer", marginBottom:"16px"}}>
          {loading ? "Загрузка..." : (mode === "register" ? "Зарегистрироваться" : otpChallenge ? "Подтвердить код" : "Войти")}
        </button>

        <div className="boris-f3-5a2dfef236e7d7">
          <button
            className="boris-login-switch boris-f3-c429d98e13aa49"
            onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); setNotice(""); setOtpChallenge(""); setOtpCode(""); setOtpEmail(""); }}
>
            {mode === "login" ? "Нет аккаунта? Зарегистрироваться" : "Уже есть аккаунт? Войти"}
          </button>
        </div>
      </div>
    </div>
  );
}
