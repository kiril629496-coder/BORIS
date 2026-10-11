"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, Bell, BrainCircuit, CalendarCheck2, Check, ChevronDown, Ellipsis, ExternalLink, LifeBuoy, MessageCircle, Mic, MicOff, Phone, PhoneOff, Search, ShieldCheck, SwitchCamera, UserRound, UsersRound, Video, VideoOff, X } from "lucide-react";
import SecurityPanel from "./components/SecurityPanel";
import WorkDashboard from "./components/WorkDashboard";
import ReportsPanel from "./components/ReportsPanel";
import MopControlPanel from "./components/MopControlPanel";
import SettingsPanel from "./components/SettingsPanel";
import SupportCenter, { type SupportTicket } from "./components/SupportCenter";
import styles from "./messenger.module.css";
import {
  cacheMessages,
  deleteBlob,
  enqueueOutbox,
  getBlob,
  listOutbox,
  loadCachedMessages,
  loadDraft,
  putBlob,
  removeOutbox,
  saveDraft,
  updateOutbox,
  type MessengerOutboxItem,
  type OutboxAttachment,
} from "./offline";

type Lead = { chat_id: string; item_title?: string; last_text?: string; phone?: string | null; contact_name?: string; stage?: string; assigned_to?: string | null; assignee_name?: string | null; owner_type?: string; unread_count?: number; last_msg_at?: string | number | null };
type InternalAttachment = { id?: number | string; file_name: string; content_type: string; size_bytes?: number | null; storage_ref?: string; download_url?: string; offline_blob_id?: string };
type VoiceAsset = { asset_id?: string; playback_url?: string; duration_ms?: number; waveform_peaks?: number[]; transcription_status?: "none" | "queued" | "processing" | "ready" | "no_speech" | "failed" | string; transcription_text?: string | null; transcription_language?: string | null; transcription_error_code?: string | null; local_blob_id?: string | null; local_name?: string | null };
type VideoAsset = { asset_id?: string; kind?: "video" | "video_note" | string; processing_status?: "queued" | "processing" | "ready" | "failed" | string; processing_error_code?: string | null; duration_ms?: number | null; width?: number | null; height?: number | null; mime?: string | null; size_bytes?: number | null; local_blob_id?: string | null; local_name?: string | null };
type Message = { id?: string | number; text?: string; body?: string; message?: string; message_type?: string; direction?: string; sender?: string; sender_user_id?: number; is_own?: boolean; created_at?: string; at?: string; author_label?: string; deleted_at?: string; delivery_status?: string; optimistic?: boolean; offline_id?: string; read_by_count?: number; pinned?: boolean; reactions?: Array<{ reaction: string; user_id: number }>; attachments?: InternalAttachment[]; voice?: VoiceAsset; video?: VideoAsset };
type MessengerCall = { call_id: string; account_id: string; conversation_id: number; caller_user_id: number; peer_user_id: number | null; call_mode?: "direct" | "group" | string; target_user_ids?: number[]; busy_user_ids?: number[]; participant_count?: number; call_type: "audio" | "video"; state: "created" | "ringing" | "accepted" | "connecting" | "connected" | "reconnecting" | "ended" | "missed" | "declined" | "busy" | "failed" | string; created_at?: string; ringing_at?: string | null; answered_at?: string | null; connected_at?: string | null; ended_at?: string | null; ended_reason?: string | null };
type GroupCallParticipant = { user_id: number; state: string; display_name?: string | null; joined_at?: string | null; left_at?: string | null; updated_at?: string | null; is_caller?: boolean };
type CallSignalEvent = { seq: number; actor_user_id: number; event_type: "offer" | "answer" | "ice" | "renegotiate" | "ice_restart" | "mute" | "camera" | "heartbeat" | string; payload?: Record<string, unknown>; created_at?: string };
type VideoCapabilities = { enabled: boolean; video_notes_enabled: boolean; runtime_contract?: string };
type CallCapabilities = { enabled: boolean; video_enabled: boolean; turn_enabled: boolean; runtime_contract?: string; ring_timeout_seconds?: number; heartbeat_timeout_seconds?: number };
type Task = { id: number; chat_id?: string; title?: string; status?: string; due_date?: string; due_time?: string; type?: string; comment?: string };
type AiDraft = { draft_id: string; account_id: string; avito_chat_id: string; text: string; status?: string };
type AccessibleAccount = { account_id: string; name?: string; status?: string; history_only?: boolean; unread?: number; attention?: number; role?: "OWNER" | "MANAGER_LEAD" | "MANAGER" | "VIEWER" | string; can_reply?: boolean; can_manage_team?: boolean; can_view_reports?: boolean };
type InternalPerson = { user_id: number; email: string; display_name?: string; role: "OWNER" | "ADMIN" | "MEMBER"; account_role?: string; presence?: "онлайн" | "недавно" | "офлайн" };
type InternalCandidate = { user_id: number; email: string; display_name?: string; account_role?: string; presence?: "онлайн" | "недавно" | "офлайн" };
type InternalReceipt = { user_id: number; email: string; role: string; last_read_message_id?: number | null };
type InternalThread = { id: string; title: string; kind: "Личный" | "Группа"; members: string[]; lastText: string; unread: number; pinned?: boolean; isGeneral?: boolean };
type TeamMembership = { id: number; user_id?: number | null; name: string; phone?: string; email?: string | null; role: string; status: string; last_login_at?: string | null; push_status?: string | null; invite_expires_at?: string | null; pwa_installed?: boolean | null; pwa_standalone_confirmed?: boolean | null; assigned_chats_count?: number | null; assigned_tasks_count?: number | null; is_account_owner?: boolean };
type BeforeInstallPromptEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> };
type MessengerRealtimeEvent = { id: number; type: string; entity_type: string; entity_id?: string | null; payload?: Record<string, unknown>; created_at?: string };
type InternalReportKind = "summary" | "finance" | "advertising" | "reactivation" | "ab_tests";
type InternalReportPreview = { kind: InternalReportKind; period: 1 | 7 | 30 | 60; data: any; title: string };

const STAGES = ["новый", "в процессе", "квалифицирован", "целевое действие", "оставил телефон", "ушёл подумать", "отвалился"];

function tokenHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("boris_token") || localStorage.getItem("boris_messenger_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  for (const [key, value] of Object.entries(tokenHeaders())) {
    if (!headers.has(key)) headers.set(key, value);
  }
  const response = await fetch(path, { ...options, cache: "no-store", headers });
  if (response.status === 401 && typeof window !== "undefined") {
    const returnTo = window.location.pathname + window.location.search;
    if (returnTo.startsWith("/messenger")) localStorage.setItem("boris_post_login_return", returnTo);
    localStorage.removeItem("boris_token");
    localStorage.removeItem("boris_messenger_token");
    window.location.replace("/messenger/login");
    throw new Error("Сессия истекла. Выполните вход снова.");
  }
  const raw = await response.text();
  let data: any = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch { data = {}; }
  if (!response.ok) {
    const detail = String(data?.detail?.message || data?.detail || data?.message || "").trim();
    if (response.status === 402) throw new Error("Тариф этого аккаунта закончился. Продлите доступ, чтобы продолжить работу.");
    if (response.status === 403) {
      const technical = /team\.manage|can_manage_team|account_id|forbidden_account|tenant|scope/i.test(detail);
      throw new Error(technical ? "У вашей роли нет доступа к этому разделу." : (detail || "У вашей роли нет права выполнить это действие."));
    }
    if (response.status === 428 && String(data?.detail?.code || "") === "messenger_legal_acceptance_required") {
      if (typeof window !== "undefined") {
        const returnTo = window.location.pathname + window.location.search;
        localStorage.setItem("boris_messenger_legal_return", returnTo);
        window.location.replace("/messenger/legal");
      }
      throw new Error("Нужно принять актуальные условия BORIS Messenger.");
    }
    if (response.status === 409) throw new Error(detail || "Действие уже выполнено другим сотрудником. Обновите состояние и повторите при необходимости.");
    if (response.status === 429) throw new Error("Слишком много действий. Повторите через несколько секунд.");
    if (response.status >= 500) throw new Error("BORIS временно недоступен. Попробуйте ещё раз.");
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data as T;
}

function formatTime(value?: string | number | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function taskStatusKey(task: Task): "today" | "overdue" | "planned" | "done" | "unknown" {
  const value = String(task.status || "").trim().toLowerCase();
  return value === "today" || value === "overdue" || value === "planned" || value === "done" ? value : "unknown";
}

function taskStatusLabel(task: Task): string {
  const key = taskStatusKey(task);
  return key === "today" ? "Сегодня" : key === "overdue" ? "Просрочено" : key === "planned" ? "Запланировано" : key === "done" ? "Выполнено" : (task.status || "Задача");
}

function internalRoleLabel(role: InternalPerson["role"]) {
  return role === "OWNER" ? "Владелец" : role === "ADMIN" ? "Администратор" : "Участник";
}

function accountRoleLabel(role?: string) {
  const value = String(role || "").toUpperCase();
  if (value === "OWNER") return "Владелец";
  if (value === "MANAGER_LEAD" || value === "ADMIN") return "Старший менеджер";
  if (value === "MANAGER" || value === "MEMBER") return "Менеджер";
  if (value === "VIEWER") return "Наблюдатель";
  return "Сотрудник";
}

function base64ToUint8Array(value: string): Uint8Array {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index++) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

function isIos(): boolean {
  return typeof navigator !== "undefined" && /iphone|ipad|ipod/i.test(navigator.userAgent);
}

function isStandalone(): boolean {
  return typeof window !== "undefined" && (
    window.matchMedia?.("(display-mode: standalone)")?.matches === true ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

function isTransientNetworkError(error: unknown): boolean {
  const message = (error instanceof Error ? error.message : String(error || "")).toLowerCase();
  return /failed to fetch|networkerror|network error|load failed|соединени|network request/.test(message);
}

type MediaPermissionState = "granted" | "prompt" | "denied" | "unsupported";

async function readMediaPermission(name: "microphone" | "camera"): Promise<MediaPermissionState> {
  if (typeof navigator === "undefined" || !navigator.permissions?.query) return "unsupported";
  try {
    const result = await navigator.permissions.query({ name } as PermissionDescriptor);
    return result.state === "granted" || result.state === "denied" ? result.state : "prompt";
  } catch {
    return "unsupported";
  }
}

function mediaPermissionHelp(): string {
  if (typeof navigator === "undefined") return "Откройте настройки браузера и разрешите доступ к микрофону и камере для BORIS Messenger.";
  const ua = navigator.userAgent.toLowerCase();
  if (/iphone|ipad|ipod/.test(ua)) return "На iPhone/iPad откройте «Настройки» → Safari (или BORIS Messenger) → Камера/Микрофон и разрешите доступ.";
  if (/android/.test(ua)) return "На Android откройте «Настройки» → Приложения → Chrome/BORIS Messenger → Разрешения и включите камеру и микрофон.";
  if (/chrome|chromium/.test(ua)) return "В Chrome нажмите значок настроек сайта слева от адреса → «Разрешения» → разрешите камеру и микрофон.";
  return "Откройте настройки сайта в браузере и разрешите доступ к микрофону и камере для BORIS Messenger.";
}

function mediaAccessError(kind: "microphone" | "camera" | "camera_and_microphone", error: unknown): string {
  const name = String((error as { name?: string } | null)?.name || "");
  const denied = name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError";
  const missing = name === "NotFoundError" || name === "DevicesNotFoundError";
  const busy = name === "NotReadableError" || name === "TrackStartError";
  if (kind === "microphone") {
    if (denied) return "Разрешите доступ к микрофону в настройках браузера/устройства.";
    if (missing) return "Микрофон не найден. Подключите микрофон и повторите.";
    if (busy) return "Микрофон сейчас занят другим приложением. Закройте его и повторите.";
    return "Не удалось включить микрофон. Проверьте разрешение и устройство.";
  }
  if (kind === "camera") {
    if (denied) return "Разрешите доступ к камере в настройках браузера/устройства.";
    if (missing) return "Камера не найдена. Подключите камеру или продолжите звонок без видео.";
    if (busy) return "Камера сейчас занята другим приложением. Звонок можно продолжить без видео.";
    return "Не удалось включить камеру. Звонок можно продолжить без видео.";
  }
  if (denied) return "Разрешите доступ к камере и микрофону в настройках браузера/устройства.";
  if (missing) return "Камера или микрофон не найдены. Проверьте подключённые устройства.";
  if (busy) return "Камера или микрофон сейчас заняты другим приложением.";
  return "Не удалось включить камеру или микрофон. Проверьте разрешения устройства.";
}

function parseInternalReportCommand(raw: string): { kind: InternalReportKind; period: 1 | 7 | 30 | 60 } | null {
  const value = raw.trim().toLowerCase().replace(/\s+/g, " ");
  if (!value.startsWith("/")) return null;
  if (value === "/баланс" || value === "/balance") return { kind: "finance", period: 7 };
  if (value === "/реактивация" || value === "/reactivation") return { kind: "reactivation", period: 7 };
  if (value === "/утро" || value === "/morning") return { kind: "summary", period: 7 };
  const advertising = value.match(/^\/(?:реклама|ads?)(?:\s+(1|7|30|60))?$/);
  if (advertising) return { kind: "advertising", period: Number(advertising[1] || 7) as 1 | 7 | 30 | 60 };
  const ab = value.match(/^\/(?:ab|аб)(?:\s+(7|30|60))?$/);
  if (ab) return { kind: "ab_tests", period: Number(ab[1] || 30) as 7 | 30 | 60 };
  return null;
}

function internalReportTitle(kind: InternalReportKind): string {
  return kind === "summary" ? "Утренний отчёт" : kind === "finance" ? "Финансы" : kind === "advertising" ? "Реклама" : kind === "reactivation" ? "Реактивация" : "A/B-тесты";
}

function reportMoney(value: unknown): string {
  const number = Number(value);
  return value === null || value === undefined || Number.isNaN(number) ? "—" : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(number) + " ₽";
}

function internalReportFacts(preview: InternalReportPreview): Array<{ label: string; value: string }> {
  const d = preview.data || {};
  if (preview.kind === "finance") return [
    { label: "Кошелёк", value: reportMoney(d.wallet_rub) },
    { label: "Расход сегодня", value: reportMoney(d.spend_today_rub) },
    { label: "За 7 дней", value: reportMoney(d.spend_7d_rub) },
  ];
  if (preview.kind === "advertising") return [
    { label: "Контакты", value: String(d.contacts ?? "—") },
    { label: "CPL", value: reportMoney(d.cpl_rub) },
    { label: "Расход", value: reportMoney(d.ad_spend_rub) },
  ];
  if (preview.kind === "reactivation") return [
    { label: "Можно вернуть", value: String(d.eligible_now ?? "—") },
    { label: "Проверить", value: String(d.needs_review ?? "—") },
    { label: "На паузе", value: String(d.cooldown ?? "—") },
  ];
  if (preview.kind === "ab_tests") return [
    { label: "Завершено", value: String(d.completed ?? "—") },
    { label: "Набирают данные", value: String(d.pending ?? "—") },
    { label: "Подготовлено", value: String(d.prepared ?? "—") },
  ];
  const finance = d.finance || {}, advertising = d.advertising || {}, reactivation = d.reactivation || {}, ab = d.ab_tests || {};
  return [
    { label: "Кошелёк", value: reportMoney(finance.wallet_rub) },
    { label: "Контакты", value: String(advertising.contacts ?? "—") },
    { label: "Реактивация", value: String(reactivation.eligible_now ?? "—") },
    { label: "A/B в работе", value: String(ab.pending ?? "—") },
  ];
}

function friendlyInboxError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error || "");
  const normalized = message.toLowerCase();
  if (normalized.includes("тариф") || normalized.includes("tariff") || normalized.includes("subscription") || normalized.includes("expired")) {
    return "Доступ к чатам временно закрыт: срок оплаченного тарифа закончился. После продления снова откроются диалоги и работа с клиентами.";
  }
  if (normalized.includes("доступ") || normalized.includes("access") || normalized.includes("forbidden") || normalized.includes("403")) {
    return "Нет доступа к чатам этого аккаунта.";
  }
  return "Не удалось загрузить чаты. Попробуйте обновить раздел или обратитесь к администратору.";
}


function VoiceMessage({ message, onStatus }: { message: Message; onStatus: (value: string) => void }) {
  const voice = message.voice;
  const [audioUrl, setAudioUrl] = useState("");
  const [isLocalAudio, setIsLocalAudio] = useState(Boolean(voice?.local_blob_id));
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [mediaDuration, setMediaDuration] = useState(Math.max(0, Number(voice?.duration_ms || 0) / 1000));
  const [speed, setSpeed] = useState<1 | 1.5 | 2>(1);
  const [transcript, setTranscript] = useState(voice?.transcription_text || "");
  const [transcriptionStatus, setTranscriptionStatus] = useState(voice?.transcription_status || "none");
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const streamUrlRef = useRef<string | null>(null);
  const ticketExpiresAtRef = useRef(0);
  const ticketRefreshRef = useRef<Promise<string> | null>(null);

  const mediaTime = (value: number) => {
    const safe = Math.max(0, Math.round(Number(value || 0)));
    return Math.floor(safe / 60) + ":" + String(safe % 60).padStart(2, "0");
  };

  const ensurePlaybackTicket = async (): Promise<string> => {
    if (!voice?.asset_id) throw new Error("Голосовое ещё не загружено");
    const cached = streamUrlRef.current;
    if (cached && ticketExpiresAtRef.current - Date.now() > 30_000) return cached;
    if (ticketRefreshRef.current) return ticketRefreshRef.current;
    const pending = (async () => {
      const response = await fetch("/api/internal-messenger/voice/" + voice.asset_id + "/playback-ticket", {
        method: "POST",
        headers: tokenHeaders(),
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Аудио временно недоступно");
      const payload = await response.json() as { stream_url?: string; expires_in?: number };
      const streamUrl = String(payload.stream_url || "");
      if (!streamUrl.startsWith("/api/messenger-workspace/voice/")) throw new Error("Некорректный адрес аудио");
      const ttlSeconds = Math.max(15, Number(payload.expires_in || 0));
      streamUrlRef.current = streamUrl;
      ticketExpiresAtRef.current = Date.now() + ttlSeconds * 1000;
      return streamUrl;
    })();
    ticketRefreshRef.current = pending;
    try {
      return await pending;
    } finally {
      ticketRefreshRef.current = null;
    }
  };

  useEffect(() => {
    let disposed = false;
    let objectUrl = "";
    const prepare = async () => {
      try {
        setPlaying(false);
        setPosition(0);
        setMediaDuration(Math.max(0, Number(voice?.duration_ms || 0) / 1000));
        streamUrlRef.current = null;
        ticketExpiresAtRef.current = 0;
        ticketRefreshRef.current = null;
        if (voice?.local_blob_id) {
          const stored = await getBlob(voice.local_blob_id);
          if (!stored?.blob || disposed) return;
          objectUrl = URL.createObjectURL(stored.blob);
          if (!disposed) {
            setIsLocalAudio(true);
            setAudioUrl(objectUrl);
          }
          return;
        }
        if (voice?.asset_id) {
          setIsLocalAudio(false);
          const streamUrl = await ensurePlaybackTicket();
          if (!disposed) setAudioUrl(streamUrl);
          return;
        }
        if (!disposed) setAudioUrl("");
      } catch {
        if (!disposed) setAudioUrl("");
      }
    };
    void prepare();
    return () => {
      disposed = true;
      const audio = audioRef.current;
      if (audio && !audio.paused) audio.pause();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      streamUrlRef.current = null;
      ticketExpiresAtRef.current = 0;
      ticketRefreshRef.current = null;
    };
  }, [voice?.asset_id, voice?.local_blob_id]);

  useEffect(() => {
    const pauseOther = (event: Event) => {
      const detail = (event as CustomEvent<{ messageId?: string }>).detail;
      if (detail?.messageId === String(message.id || "")) return;
      const audio = audioRef.current;
      if (audio && !audio.paused) audio.pause();
    };
    window.addEventListener("boris-messenger-voice-play", pauseOther as EventListener);
    return () => window.removeEventListener("boris-messenger-voice-play", pauseOther as EventListener);
  }, [message.id]);

  useEffect(() => {
    setTranscript(voice?.transcription_text || "");
    setTranscriptionStatus(voice?.transcription_status || "none");
    if (voice?.transcription_status !== "ready") setTranscriptOpen(false);
  }, [voice?.transcription_text, voice?.transcription_status]);

  useEffect(() => {
    if (!message.id || String(message.id).startsWith("offline:")) return;
    if (!["queued", "processing"].includes(String(transcriptionStatus))) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api<{ voice?: VoiceAsset }>("/api/internal-messenger/messages/" + String(message.id) + "/transcription");
        const next = result.voice;
        if (cancelled || !next) return;
        setTranscript(next.transcription_text || "");
        setTranscriptionStatus(next.transcription_status || "none");
      } catch {
        // Realtime/outbox reconciliation will retry; audio remains usable.
      }
    };
    const timer = window.setInterval(() => void poll(), 2500);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [message.id, transcriptionStatus]);

  const rotateSpeed = () => {
    const next: 1 | 1.5 | 2 = speed === 1 ? 1.5 : speed === 1.5 ? 2 : 1;
    setSpeed(next);
    if (audioRef.current) audioRef.current.playbackRate = next;
  };

  const togglePlay = async () => {
    const audio = audioRef.current;
    if (!audio || !audioUrl) return;
    try {
      if (audio.paused) {
        if (!isLocalAudio) {
          const streamUrl = await ensurePlaybackTicket();
          const absolute = new URL(streamUrl, window.location.origin).href;
          if (audio.src !== absolute) {
            audio.src = streamUrl;
            audio.load();
          } else if (audio.error) {
            audio.load();
          }
        }
        window.dispatchEvent(new CustomEvent("boris-messenger-voice-play", { detail: { messageId: String(message.id || "") } }));
        await audio.play();
      } else {
        audio.pause();
      }
    } catch {
      setPlaying(false);
      onStatus("Аудио временно недоступно");
    }
  };

  const seekVoice = async (value: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    try {
      if (!isLocalAudio) await ensurePlaybackTicket();
      audio.currentTime = Math.max(0, Math.min(mediaDuration || Number(voice?.duration_ms || 0) / 1000, value));
      setPosition(audio.currentTime || 0);
    } catch {
      onStatus("Не удалось перемотать голосовое");
    }
  };

  const refreshTranscript = async (retry = false) => {
    if (!message.id || String(message.id).startsWith("offline:")) return;
    setBusy(true);
    try {
      if (retry) await api("/api/internal-messenger/messages/" + String(message.id) + "/transcription/retry", { method: "POST" });
      const result = await api<{ voice?: VoiceAsset }>("/api/internal-messenger/messages/" + String(message.id) + "/transcription");
      const next = result.voice;
      setTranscript(next?.transcription_text || "");
      setTranscriptionStatus(next?.transcription_status || (retry ? "queued" : "none"));
      onStatus(retry ? "Расшифровка поставлена в очередь" : "Статус расшифровки обновлён");
    } catch (error) {
      onStatus("Расшифровка не обновлена: " + (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copyTranscript = async () => {
    const value = transcript.trim();
    if (!value || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      onStatus("Не удалось скопировать расшифровку");
    }
  };

  const declaredDuration = Math.max(0, Number(voice?.duration_ms || 0) / 1000);
  const duration = mediaDuration || declaredDuration;
  const peaks = (voice?.waveform_peaks || []).slice(0, 32);
  const shownTime = position > 0 ? position : duration;

  return <div className={styles.voiceCard}>
    <div className={styles.voiceTop}>
      <button type="button" className={styles.voicePlay} onClick={() => void togglePlay()} disabled={!audioUrl} aria-label={playing ? "Пауза" : "Воспроизвести голосовое"}>
        {playing ? "❚❚" : "▶"}
      </button>
      <div className={styles.voiceWaveWrap}>
        <span className={styles.voiceWave} aria-hidden="true">{peaks.length ? peaks.map((peak, index) => <i key={index} style={{ height: String(Math.max(4, Math.min(26, Number(peak || 0) / 3))) + "px" }} />) : <i />}</span>
        <input className={styles.voiceSeek} type="range" min={0} max={Math.max(duration, 0.1)} step={0.1} value={Math.min(position, Math.max(duration, 0.1))} onChange={event => void seekVoice(Number(event.target.value))} aria-label="Позиция голосового сообщения" disabled={!audioUrl} />
      </div>
      <small>{duration ? mediaTime(shownTime) + " / " + mediaTime(duration) : "Голосовое"}</small>
      <button type="button" className={styles.voiceSpeed} onClick={rotateSpeed} aria-label="Скорость воспроизведения">{speed}×</button>
    </div>
    {audioUrl ? <audio
      ref={audioRef}
      className={styles.voiceAudioEngine}
      preload="metadata"
      src={audioUrl}
      onLoadedMetadata={event => {
        const audio = event.currentTarget;
        if (Number.isFinite(audio.duration) && audio.duration > 0) setMediaDuration(audio.duration);
        audio.playbackRate = speed;
      }}
      onTimeUpdate={event => {
        setPosition(event.currentTarget.currentTime || 0);
        if (!isLocalAudio && ticketExpiresAtRef.current - Date.now() <= 30_000) void ensurePlaybackTicket().catch(() => undefined);
      }}
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onEnded={event => {
        setPlaying(false);
        setPosition(0);
        event.currentTarget.currentTime = 0;
      }}
      onError={() => {
        setPlaying(false);
        if (!isLocalAudio) ticketExpiresAtRef.current = 0;
      }}
    /> : <small className={styles.voicePending}>{voice?.local_blob_id ? "Ожидает отправки" : "Готовим аудио…"}</small>}
    <div className={styles.voiceTranscript}>
      {transcriptionStatus === "ready" && transcript ? <button type="button" onClick={() => setTranscriptOpen(value => !value)}>{transcriptOpen ? "Скрыть текст" : "Показать текст"}</button> :
       transcriptionStatus === "no_speech" ? <span>Речь не обнаружена</span> :
       transcriptionStatus === "failed" ? <button type="button" disabled={busy} onClick={() => void refreshTranscript(true)}>{busy ? "Повторяем…" : "Повторить расшифровку"}</button> :
       transcriptionStatus === "queued" ? <span>Расшифровка в очереди</span> :
       transcriptionStatus === "processing" ? <span>Расшифровываем…</span> :
       !voice?.local_blob_id && message.id ? <button type="button" disabled={busy} onClick={() => void refreshTranscript(false)}>{busy ? "Проверяем…" : "Проверить расшифровку"}</button> : null}
    </div>
    {transcriptOpen && transcriptionStatus === "ready" && transcript ? <div className={styles.voiceTranscriptBox}>
      <div className={styles.voiceTranscriptHead}>
        <small>Автоматическая расшифровка</small>
        <button type="button" onClick={() => void copyTranscript()} aria-label="Скопировать расшифровку">{copied ? "Скопировано" : "Скопировать"}</button>
      </div>
      <p>{transcript}</p>
    </div> : null}
  </div>;
}


function VideoMessage({ message, onStatus }: { message: Message; onStatus: (value: string) => void }) {
  const video = message.video;
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [src, setSrc] = useState("");
  const [poster, setPoster] = useState("");
  const [local, setLocal] = useState(Boolean(video?.local_blob_id));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let disposed = false;
    let objectUrl = "";
    const prepare = async () => {
      setSrc("");
      setPoster("");
      try {
        if (video?.local_blob_id) {
          const stored = await getBlob(video.local_blob_id);
          if (!stored?.blob || disposed) return;
          objectUrl = URL.createObjectURL(stored.blob);
          if (!disposed) {
            setLocal(true);
            setSrc(objectUrl);
          }
          return;
        }
        if (!video?.asset_id || video.processing_status !== "ready") return;
        setBusy(true);
        const response = await fetch("/api/internal-messenger/video/" + video.asset_id + "/playback-ticket", {
          method: "POST",
          headers: tokenHeaders(),
          credentials: "same-origin",
          cache: "no-store",
        });
        if (!response.ok) throw new Error("Видео временно недоступно");
        const payload = await response.json() as { stream_url?: string; thumbnail_url?: string };
        const streamUrl = String(payload.stream_url || "");
        const thumbnailUrl = String(payload.thumbnail_url || "");
        if (!streamUrl.startsWith("/api/messenger-workspace/video/")) throw new Error("Некорректный адрес видео");
        if (!disposed) {
          setLocal(false);
          setSrc(streamUrl);
          setPoster(thumbnailUrl);
        }
      } catch (error) {
        if (!disposed) onStatus((error as Error).message || "Видео временно недоступно");
      } finally {
        if (!disposed) setBusy(false);
      }
    };
    void prepare();
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      const node = videoRef.current;
      if (node && !node.paused) node.pause();
    };
  }, [video?.asset_id, video?.local_blob_id, video?.processing_status]);

  const isNote = message.message_type === "video_note" || video?.kind === "video_note";
  const seconds = Math.max(0, Math.round(Number(video?.duration_ms || 0) / 1000));
  const duration = seconds ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "";

  if (video?.processing_status === "failed") {
    return <div className={styles.videoPending}><strong>Видео не обработано</strong><small>Попробуйте отправить запись ещё раз.</small></div>;
  }
  if (!src) {
    return <div className={styles.videoPending}><strong>{local ? "Видео ожидает отправки" : "Обрабатываем видео…"}</strong><small>{busy ? "Готовим безопасное воспроизведение" : "Сообщение появится автоматически после обработки"}</small></div>;
  }
  return <div className={isNote ? styles.videoNoteCard : styles.videoCard}>
    <video
      ref={videoRef}
      className={isNote ? styles.videoNotePlayer : styles.videoPlayer}
      src={src}
      poster={poster || undefined}
      controls
      playsInline
      preload="metadata"
      onError={() => onStatus(local ? "Локальное видео не открылось" : "Видео временно недоступно")}
    />
    {duration ? <small className={styles.videoDuration}>{duration}</small> : null}
  </div>;
}


type MessengerSelectOption = { value: string; label: string; note?: string };

function MessengerSelect({
  value, options, onChange, ariaLabel, disabled = false, compact = false,
}: {
  value: string;
  options: MessengerSelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  disabled?: boolean;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find(option => option.value === value) || options[0];

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        rootRef.current?.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')?.focus();
      }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && rootRef.current?.contains(document.activeElement)) {
        const items = Array.from(rootRef.current.querySelectorAll<HTMLButtonElement>('[role="option"]'));
        if (!items.length) return;
        event.preventDefault();
        const current = items.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : event.key === "ArrowDown" ? (current + 1) % items.length : (current <= 0 ? items.length - 1 : current - 1);
        items[next]?.focus();
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  return <div ref={rootRef} className={`${styles.messengerSelect} ${compact ? styles.messengerSelectCompact : ""}`}>
    <button
      type="button"
      className={`${styles.messengerSelectButton} ${open ? styles.messengerSelectButtonOpen : ""}`}
      onClick={() => !disabled && setOpen(current => !current)}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-haspopup="listbox"
      aria-expanded={open}
    >
      <span><strong>{selected?.label || "Выберите"}</strong>{selected?.note && <small>{selected.note}</small>}</span>
      <ChevronDown size={15} />
    </button>
    {open && <div className={styles.messengerSelectOptions} role="listbox" aria-label={ariaLabel}>
      {options.map(option => <button
        type="button"
        role="option"
        aria-selected={option.value === value}
        key={option.value || "__empty"}
        className={option.value === value ? styles.messengerSelectOptionActive : ""}
        onClick={() => { onChange(option.value); setOpen(false); rootRef.current?.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')?.focus(); }}
      >
        <span><strong>{option.label}</strong>{option.note && <small>{option.note}</small>}</span>
        {option.value === value && <Check size={14} />}
      </button>)}
    </div>}
  </div>;
}


function CallStreamVideo({ stream, muted = false, className, label }: {
  stream: MediaStream;
  muted?: boolean;
  className?: string;
  label?: string;
}) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (node.srcObject !== stream) node.srcObject = stream;
    void node.play().catch(() => undefined);
    return () => {
      if (node.srcObject === stream) node.srcObject = null;
    };
  }, [stream]);
  return <span className={styles.groupVideoTile}>
    <video ref={ref} className={className} autoPlay playsInline muted={muted} />
    {label ? <small>{label}</small> : null}
  </span>;
}

export default function MessengerWorkspacePage() {
  const [accountId, setAccountId] = useState("");
  const [workspaceLeaving, setWorkspaceLeaving] = useState(false);
  const internalSendBusyRef = useRef(false);
  const [internalSending, setInternalSending] = useState(false);
  const [accounts, setAccounts] = useState<AccessibleAccount[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selected, setSelected] = useState<Lead | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [accountTasks, setAccountTasks] = useState<Task[]>([]);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDueDate, setTaskDueDate] = useState("");
  const [taskDueTime, setTaskDueTime] = useState("");
  const [taskCreating, setTaskCreating] = useState(false);
  const [editingTaskId, setEditingTaskId] = useState<number | null>(null);
  const [editingTaskTitle, setEditingTaskTitle] = useState("");
  const [editingTaskDate, setEditingTaskDate] = useState("");
  const [editingTaskTime, setEditingTaskTime] = useState("");
  const [taskUpdating, setTaskUpdating] = useState(false);
  const [taskDeletingId, setTaskDeletingId] = useState<number | null>(null);
  const [aiDrafts, setAiDrafts] = useState<AiDraft[]>([]);
  const [aiDraftEdits, setAiDraftEdits] = useState<Record<string, string>>({});
  const [aiDraftBusy, setAiDraftBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("Проверка доступа…");
  const [legalReady, setLegalReady] = useState(false);
  const [toast, setToast] = useState<{ text: string; kind: "info" | "error" } | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [ownershipMenuOpen, setOwnershipMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [clientFilter, setClientFilter] = useState<"all" | "unread" | "human" | "ai" | "mine" | "unassigned" | "overdue">("all");
  const [mobilePane, setMobilePane] = useState<"inbox" | "chat" | "crm">("inbox");
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);
  const [section, setSection] = useState<"chats" | "clients" | "work" | "crm" | "reports" | "mop_control" | "support" | "team" | "settings" | "security">("chats");
  const [firstRunOnboarding, setFirstRunOnboarding] = useState(false);
  const [chatMode, setChatMode] = useState<"internal" | "clients">("internal");
  const [internalThreads, setInternalThreads] = useState<InternalThread[]>([]);
  const [generalReadyAccount, setGeneralReadyAccount] = useState("");
  const [realtimeState, setRealtimeState] = useState<"connecting" | "online" | "reconnecting" | "offline">("connecting");
  const [realtimeTick, setRealtimeTick] = useState(0);
  const [internalUnreadOnly, setInternalUnreadOnly] = useState(false);
  const [internalQuery, setInternalQuery] = useState("");
  const [internalSelected, setInternalSelected] = useState<InternalThread | null>(null);
  const [internalConversationId, setInternalConversationId] = useState<number | null>(null);
  const [internalMessages, setInternalMessages] = useState<Message[]>([]);
  const [internalHasMore, setInternalHasMore] = useState(false);
  const [internalNextBeforeId, setInternalNextBeforeId] = useState<number | null>(null);
  const [internalOlderLoading, setInternalOlderLoading] = useState(false);
  const internalLoadedConversationRef = useRef<number | null>(null);
  const internalHistoryRef = useRef<HTMLDivElement | null>(null);
  const internalComposerRef = useRef<HTMLTextAreaElement | null>(null);
  const newChatPanelRef = useRef<HTMLDivElement | null>(null);
  const newChatSearchRef = useRef<HTMLInputElement | null>(null);
  const moreMenuRef = useRef<HTMLDivElement | null>(null);
  const internalNearBottomRef = useRef(true);
  const internalInitialScrollRef = useRef<number | null>(null);
  const internalRestoreScrollRef = useRef<{ height: number; top: number } | null>(null);
  const [internalMembers, setInternalMembers] = useState<InternalPerson[]>([]);
  const [internalReceipts, setInternalReceipts] = useState<InternalReceipt[]>([]);
  const [internalPeople, setInternalPeople] = useState<InternalCandidate[]>([]);
  const [internalPeopleLoading, setInternalPeopleLoading] = useState(false);
  const [selectedPersonId, setSelectedPersonId] = useState<number | null>(null);
  const [newChatOpen, setNewChatOpen] = useState(false);
  const [newChatMode, setNewChatMode] = useState<"dm" | "group">("dm");
  const [newChatSearch, setNewChatSearch] = useState("");
  const [newDmPersonId, setNewDmPersonId] = useState<number | null>(null);
  const [newGroupMemberIds, setNewGroupMemberIds] = useState<number[]>([]);
  const [newGroupTitle, setNewGroupTitle] = useState("");
  const [chatCreating, setChatCreating] = useState(false);
  const [newChatError, setNewChatError] = useState("");
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);
  const [platformAdmin, setPlatformAdmin] = useState(false);
  const [supportCategoryMenuOpen, setSupportCategoryMenuOpen] = useState(false);
  const [internalPane, setInternalPane] = useState<"list" | "chat" | "info">("list");
  const [internalDraft, setInternalDraft] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<InternalAttachment[]>([]);
  const [voiceState, setVoiceState] = useState<"idle" | "permission" | "recording" | "paused" | "processing">("idle");
  const [voiceSeconds, setVoiceSeconds] = useState(0);
  type VideoCaptureState = "idle" | "permission" | "recording" | "preview" | "processing";
  const [videoState, setVideoState] = useState<VideoCaptureState>("idle");
  const videoStateRef = useRef<VideoCaptureState>("idle");
  const videoRestartRef = useRef<{ kind: "video" | "video_note"; facing: "user" | "environment" } | null>(null);
  const setVideoCaptureState = (next: VideoCaptureState) => {
    videoStateRef.current = next;
    setVideoState(next);
  };
  const [videoSeconds, setVideoSeconds] = useState(0);
  const [videoMode, setVideoMode] = useState<"video" | "video_note">("video_note");
  const [videoFacing, setVideoFacing] = useState<"user" | "environment">("user");
  const [videoPreviewUrl, setVideoPreviewUrl] = useState("");
  const [outboxCount, setOutboxCount] = useState(0);
  const [mediaUploadProgress, setMediaUploadProgress] = useState<number | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const voiceChunksRef = useRef<Blob[]>([]);
  const voiceCancelRef = useRef(false);
  const voiceStartedAtRef = useRef(0);
  const voiceTimerRef = useRef<number | null>(null);
  const voiceContextRef = useRef<{ conversationId: number; scope: string; replyTo: number | null } | null>(null);
  const videoRecorderRef = useRef<MediaRecorder | null>(null);
  const videoStreamRef = useRef<MediaStream | null>(null);
  const videoChunksRef = useRef<Blob[]>([]);
  const videoBlobRef = useRef<Blob | null>(null);
  const videoStartedAtRef = useRef(0);
  const videoTimerRef = useRef<number | null>(null);
  const videoLiveRef = useRef<HTMLVideoElement | null>(null);
  const videoContextRef = useRef<{ conversationId: number; scope: string; replyTo: number | null; kind: "video" | "video_note" } | null>(null);
  const [activeCall, setActiveCall] = useState<MessengerCall | null>(null);
  const [incomingCall, setIncomingCall] = useState<MessengerCall | null>(null);
  const incomingCallRef = useRef<MessengerCall | null>(null);
  const [callMuted, setCallMuted] = useState(false);
  const [callCameraEnabled, setCallCameraEnabled] = useState(false);
  const [callFacing, setCallFacing] = useState<"user" | "environment">("user");
  const [callConnectedAt, setCallConnectedAt] = useState<number | null>(null);
  const [callElapsed, setCallElapsed] = useState(0);
  const [callStatus, setCallStatus] = useState("");
  const [callMinimized, setCallMinimized] = useState(false);
  const [groupCallParticipants, setGroupCallParticipants] = useState<GroupCallParticipant[]>([]);
  const [groupRemoteStreams, setGroupRemoteStreams] = useState<Record<number, MediaStream>>({});
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const groupPeerRefs = useRef<Map<number, RTCPeerConnection>>(new Map());
  const groupPendingIceRef = useRef<Map<number, RTCIceCandidateInit[]>>(new Map());
  const groupOfferSentRef = useRef<Set<number>>(new Set());
  const pendingIceRef = useRef<RTCIceCandidateInit[]>([]);
  const pendingClientCallIdRef = useRef<string | null>(null);
  const [videoCapabilities, setVideoCapabilities] = useState<VideoCapabilities>({ enabled: false, video_notes_enabled: false });
  const [callCapabilities, setCallCapabilities] = useState<CallCapabilities>({ enabled: false, video_enabled: false, turn_enabled: false });
  const videoMessagesEnabled = videoCapabilities.enabled;
  const videoNotesEnabled = videoCapabilities.enabled && videoCapabilities.video_notes_enabled;
  const audioCallsEnabled = callCapabilities.enabled && callCapabilities.turn_enabled;
  const videoCallsEnabled = audioCallsEnabled && callCapabilities.video_enabled;
  const localCallStreamRef = useRef<MediaStream | null>(null);
  const remoteCallStreamRef = useRef<MediaStream | null>(null);
  const localCallVideoRef = useRef<HTMLVideoElement | null>(null);
  const remoteCallVideoRef = useRef<HTMLVideoElement | null>(null);
  const callSignalSeqRef = useRef(0);
  const callSignalTimerRef = useRef<number | null>(null);
  const callStateTimerRef = useRef<number | null>(null);
  const callOfferSentRef = useRef(false);
  const turnRelayReportedRef = useRef(false);
  const outboxFlushRef = useRef(false);
  const draftHydratedConversationRef = useRef<number | null>(null);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [internalComposerMenuOpen, setInternalComposerMenuOpen] = useState(false);
  const [internalReportMenuOpen, setInternalReportMenuOpen] = useState(false);
  const [internalReportPreview, setInternalReportPreview] = useState<InternalReportPreview | null>(null);
  const [internalReportLoading, setInternalReportLoading] = useState(false);
  const [groupTitleDraft, setGroupTitleDraft] = useState("");
  const [internalReplyTo, setInternalReplyTo] = useState<string | null>(null);
  const [internalEditing, setInternalEditing] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | number | null>(null);
  const [reactionMenuMessageId, setReactionMenuMessageId] = useState<string | number | null>(null);
  const [messageActionMenuId, setMessageActionMenuId] = useState<string | number | null>(null);
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");
  const [accent, setAccent] = useState<"blue" | "graphite" | "sky">("blue");
  const [pushState, setPushState] = useState<"unknown" | "unsupported" | "off" | "on" | "denied">("unknown");
  const [micPermission, setMicPermission] = useState<MediaPermissionState>("unsupported");
  const [cameraPermission, setCameraPermission] = useState<MediaPermissionState>("unsupported");
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [installedApp, setInstalledApp] = useState(false);
  const [pwaUpdateRegistration, setPwaUpdateRegistration] = useState<ServiceWorkerRegistration | null>(null);
  const [teamMembers, setTeamMembers] = useState<TeamMembership[]>([]);
  const [teamLoading, setTeamLoading] = useState(false);
  const [teamError, setTeamError] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteName, setInviteName] = useState("");
  const [invitePhone, setInvitePhone] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"MANAGER_LEAD" | "MANAGER" | "VIEWER">("MANAGER");
  const [lastInviteUrl, setLastInviteUrl] = useState("");
  const sendKey = useRef<string | null>(null);
  const deepLinkAppliedRef = useRef<string>("");
  const internalDeepLinkAppliedRef = useRef<string>("");
  const inboxRequestSeq = useRef(0);
  const lastToastRef = useRef<{ text: string; at: number }>({ text: "", at: 0 });

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const [microphone, camera] = await Promise.all([
        readMediaPermission("microphone"),
        readMediaPermission("camera"),
      ]);
      if (cancelled) return;
      setMicPermission(microphone);
      setCameraPermission(camera);
    };
    void refresh();
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    incomingCallRef.current = incomingCall;
  }, [incomingCall?.call_id, incomingCall?.state]);

  const recordMessengerTelemetry = async (
    event: "recordings_started" | "voice_failed" | "video_started" | "call_failed" | "reconnect_attempt" | "reconnect_success" | "turn_relay_used" | "push_clicked" | "deeplink_opened",
    details: Record<string, string | number | boolean> = {},
    entityType?: "call" | "conversation" | "media",
    entityId?: string,
  ) => {
    if (!accountId) return;
    try {
      await api("/api/internal-messenger/telemetry", {
        method: "POST",
        body: JSON.stringify({
          account_id: accountId,
          event,
          entity_type: entityType || null,
          entity_id: entityId || null,
          details,
        }),
      });
    } catch {
      // Telemetry is intentionally best-effort and must never break Messenger UX.
    }
  };

  useEffect(() => {
    if (!accountId || typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("push_click") !== "1") return;
    url.searchParams.delete("push_click");
    window.history.replaceState(window.history.state, "", url.toString());
    void recordMessengerTelemetry("push_clicked", { source: "push" });
    void recordMessengerTelemetry("deeplink_opened", { source: "push" });
  }, [accountId]);

  const currentAccountAccess = useMemo(
    () => accounts.find(account => account.account_id === accountId) || null,
    [accounts, accountId],
  );
  const currentAccountRole = String(currentAccountAccess?.role || "").toUpperCase();
  const canReply = currentAccountRole !== "VIEWER" && Boolean(currentAccountAccess?.can_reply);
  const canManageTeam = currentAccountRole !== "VIEWER" && Boolean(currentAccountAccess?.can_manage_team || currentAccountRole === "OWNER");
  const canViewReports = currentAccountAccess?.can_view_reports !== false;
  const offlineScope = useMemo(
    () => accountId && currentUserId ? `${accountId}:${currentUserId}` : "",
    [accountId, currentUserId],
  );
  const newChatCandidates = useMemo(() => {
    const needle = newChatSearch.trim().toLowerCase();
    return internalPeople.filter(person => !needle || (
      (person.display_name || "").toLowerCase().includes(needle)
      || person.email.toLowerCase().includes(needle)
      || accountRoleLabel(person.account_role).toLowerCase().includes(needle)
    ));
  }, [internalPeople, newChatSearch]);
  const selectedGroupPeople = useMemo(
    () => internalPeople.filter(person => newGroupMemberIds.includes(person.user_id)),
    [internalPeople, newGroupMemberIds],
  );

  useEffect(() => {
    setLeads([]);
    setSelected(null);
    setMessages([]);
    setTasks([]);
    setAccountTasks([]);
    setAiDrafts([]);
    setInternalThreads([]);
    setInternalSelected(null);
    setInternalConversationId(null);
    setInternalMessages([]);
    setInternalMembers([]);
    setTeamMembers([]);
    setTeamError("");
    setLastInviteUrl("");
    setInviteOpen(false);
    setNewChatOpen(false);
    setNewChatMode("dm");
    setNewChatSearch("");
    setNewDmPersonId(null);
    setNewGroupMemberIds([]);
    setNewGroupTitle("");
    setNewChatError("");
    setChatCreating(false);
  }, [accountId]);

  useEffect(() => {
    if (!currentUserId) {
      setVideoCapabilities({ enabled: false, video_notes_enabled: false });
      setCallCapabilities({ enabled: false, video_enabled: false, turn_enabled: false });
      return;
    }
    let cancelled = false;
    const loadMediaCapabilities = async () => {
      const [videoResult, callResult] = await Promise.allSettled([
        api<VideoCapabilities>("/api/internal-messenger/video/capabilities"),
        api<CallCapabilities>("/api/internal-messenger/calls/capabilities"),
      ]);
      if (cancelled) return;
      setVideoCapabilities(videoResult.status === "fulfilled"
        ? { enabled: Boolean(videoResult.value.enabled), video_notes_enabled: Boolean(videoResult.value.video_notes_enabled), runtime_contract: videoResult.value.runtime_contract }
        : { enabled: false, video_notes_enabled: false });
      setCallCapabilities(callResult.status === "fulfilled"
        ? {
            enabled: Boolean(callResult.value.enabled),
            video_enabled: Boolean(callResult.value.video_enabled),
            turn_enabled: Boolean(callResult.value.turn_enabled),
            runtime_contract: callResult.value.runtime_contract,
            ring_timeout_seconds: callResult.value.ring_timeout_seconds,
            heartbeat_timeout_seconds: callResult.value.heartbeat_timeout_seconds,
          }
        : { enabled: false, video_enabled: false, turn_enabled: false });
    };
    void loadMediaCapabilities();
    const timer = window.setInterval(() => void loadMediaCapabilities(), 15_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [currentUserId]);

  const openSupportChat = async (category = "Нужна помощь") => {
    if (!accountId) { setStatus("Выберите аккаунт."); return; }
    setSupportCategoryMenuOpen(false);
    try {
      const context = {
        current_url: window.location.href,
        section,
        browser: navigator.userAgent,
        device: /mobile|android|iphone|ipad/i.test(navigator.userAgent) ? "mobile" : "desktop",
        pwa: isStandalone() ? "pwa" : "browser",
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
        module: section,
      };
      const result = await api<{ ticket_id: number; conversation_id: number }>("/api/support/thread/open", {
        method: "POST",
        body: JSON.stringify({
          account_id: accountId,
          category,
          module: section,
          priority: category === "Не работает" || category === "Вопрос по оплате" ? "P1" : "P2",
          context,
        }),
      });
      const url = new URL(window.location.href);
      url.searchParams.set("account_id", accountId);
      url.searchParams.set("section", "chats");
      url.searchParams.set("internal_conversation_id", String(result.conversation_id));
      url.searchParams.set("support_ticket_id", String(result.ticket_id));
      window.location.assign(url.toString());
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Не удалось открыть поддержку.");
    }
  };

  const openOwnerSupportTicket = (ticket: SupportTicket) => {
    if (!ticket.conversation_id) { setStatus("У обращения пока нет чата."); return; }
    const url = new URL(window.location.href);
    url.searchParams.set("account_id", ticket.account_id);
    url.searchParams.set("section", "chats");
    url.searchParams.set("internal_conversation_id", String(ticket.conversation_id));
    url.searchParams.set("support_ticket_id", String(ticket.ticket_id));
    window.location.assign(url.toString());
  };

  const openMoreSection = (next: "crm" | "reports" | "mop_control" | "support" | "team" | "settings" | "security") => {
    setSection(next);
    setMobileMoreOpen(false);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.set("section", next);
      if (next !== "reports") {
        url.searchParams.delete("report");
        url.searchParams.delete("period_days");
        url.searchParams.delete("date_from");
        url.searchParams.delete("date_to");
      }
      window.history.pushState({ messengerSection: next }, "", url.toString());
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  useEffect(() => {
    if (typeof window === "undefined") return;
    const applySectionFromUrl = () => {
      const params = new URLSearchParams(window.location.search);
      const requested = params.get("section");
      const onboarding = params.get("onboarding") === "1";
      setFirstRunOnboarding(onboarding);
      if (onboarding) {
        setSection("settings");
        setMobileMoreOpen(false);
      } else if (requested === "chats" || requested === "clients") {
        setSection(requested);
        setChatMode(requested === "clients" ? "clients" : "internal");
        setMobileMoreOpen(false);
      } else if (requested === "crm" || requested === "reports" || requested === "mop_control" || requested === "support" || requested === "team" || requested === "settings" || requested === "security") {
        setSection(requested);
        setMobileMoreOpen(false);
      } else {
        const primary = window.history.state?.messengerSection === "clients" ? "clients" : "chats";
        setSection(primary);
        setChatMode(primary === "clients" ? "clients" : "internal");
        setMobileMoreOpen(false);
      }
    };
    applySectionFromUrl();
    window.addEventListener("popstate", applySectionFromUrl);
    return () => window.removeEventListener("popstate", applySectionFromUrl);
  }, []);

  useEffect(() => {
    if (section === "team" && currentAccountAccess && !canManageTeam) {
      setSection("chats");
      setChatMode("internal");
      setInternalPane("chat");
      setTeamError("У вашей роли нет доступа к управлению командой.");
    }
  }, [section, currentAccountAccess, canManageTeam]);

  useEffect(() => {
    const text = status.trim();
    if (!text || text === "Проверка доступа…" || text.startsWith("Аккаунт ") || text === "Выберите доступный аккаунт BORIS, чтобы открыть чаты") return;
    const now = Date.now();
    if (lastToastRef.current.text === text && now - lastToastRef.current.at < 10000) return;
    lastToastRef.current = { text, at: now };
    const isError = /(не |ошиб|недоступ|законч|нет прав|не удалось|не отправ)/i.test(text);
    setToast({ text, kind: isError ? "error" : "info" });
    const timer = window.setTimeout(() => setToast(null), isError ? 5000 : 3200);
    return () => window.clearTimeout(timer);
  }, [status]);

  const visibleInternalThreads = useMemo(() => {
    const needle = internalQuery.trim().toLowerCase();
    return internalThreads.filter(thread => (!internalUnreadOnly || thread.unread > 0) && (!needle || (thread.title + " " + thread.lastText + " " + thread.members.join(" ")).toLowerCase().includes(needle)));
  }, [internalThreads, internalUnreadOnly, internalQuery]);

  const closeNewChat = () => {
    setNewChatOpen(false);
    document.querySelector<HTMLButtonElement>('[data-new-chat-trigger="true"]')?.focus();
    setNewChatError("");
    setNewChatSearch("");
  };

  const toggleNewChat = () => {
    setNewChatOpen(current => {
      const next = !current;
      if (next) {
        setNewChatMode("dm");
        setNewChatSearch("");
        setNewDmPersonId(null);
        setNewGroupMemberIds([]);
        setNewGroupTitle("");
        setNewChatError("");
      }
      return next;
    });
  };

  useEffect(() => {
    if (!newChatOpen || !accountId) {
      setInternalPeopleLoading(false);
      return;
    }
    if (currentUserId == null) {
      setInternalPeopleLoading(true);
      return;
    }
    let cancelled = false;
    setInternalPeopleLoading(true);
    void api<{ people?: InternalCandidate[] }>(
      `/api/internal-messenger/people?account_id=${encodeURIComponent(accountId)}`,
    ).then(result => {
      if (cancelled) return;
      const people = (result.people || []).filter(person => person.user_id !== currentUserId);
      setInternalPeople(people);
      setSelectedPersonId(current => current && people.some(person => person.user_id === current) ? current : (people[0]?.user_id || null));
    }).catch(error => {
      if (!cancelled && !isTransientNetworkError(error)) {
        setNewChatError("Не удалось загрузить сотрудников. Попробуйте ещё раз.");
      }
    }).finally(() => {
      if (!cancelled) setInternalPeopleLoading(false);
    });
    return () => { cancelled = true; };
  }, [newChatOpen, accountId, currentUserId]);

  useEffect(() => {
    if (!newChatOpen) return;
    const focusTimer = window.setTimeout(() => newChatSearchRef.current?.focus(), 40);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNewChat();
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest?.('[data-new-chat-trigger="true"]')) return;
      if (newChatPanelRef.current && target && !newChatPanelRef.current.contains(target)) closeNewChat();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [newChatOpen]);

  useEffect(() => {
    if (!mobileMoreOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMobileMoreOpen(false); document.querySelector<HTMLButtonElement>('[data-more-trigger="true"]')?.focus(); }
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest?.('[data-more-trigger="true"]')) return;
      if (moreMenuRef.current && target && !moreMenuRef.current.contains(target)) setMobileMoreOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [mobileMoreOpen]);

  const clearMoreSectionUrl = (primary: "chats" | "clients") => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.delete("section");
    url.searchParams.delete("report");
    url.searchParams.delete("period_days");
    url.searchParams.delete("date_from");
    url.searchParams.delete("date_to");
    window.history.pushState({ messengerSection: primary }, "", url.toString());
  };

  const openGeneralTeamChat = () => {
    setSection("chats");
    clearMoreSectionUrl("chats");
    setChatMode("internal");
    setInternalUnreadOnly(false);
    setInternalQuery("");
    const general = internalThreads.find(thread => thread.isGeneral);
    if (general) {
      setInternalSelected(general);
      setInternalConversationId(Number(general.id));
      setInternalPane("chat");
      return;
    }
    setInternalPane("chat");
    setStatus("Открываем общий чат команды…");
  };

  useEffect(() => {
    if (section !== "team" || !accountId || !canManageTeam) return;
    let cancelled = false;
    const loadTeam = async () => {
      setTeamLoading(true);
      setTeamError("");
      try {
        const result = await api<{ members?: TeamMembership[] }>("/api/team/memberships?account_id=" + encodeURIComponent(accountId));
        if (!cancelled) setTeamMembers(result.members || []);
      } catch (error) {
        if (!cancelled) {
          setTeamMembers([]);
          setTeamError((error as Error).message || "Не удалось загрузить команду.");
        }
      } finally {
        if (!cancelled) setTeamLoading(false);
      }
    };
    void loadTeam();
    return () => { cancelled = true; };
  }, [section, accountId, canManageTeam, realtimeTick]);

  const createTeamInvite = async () => {
    if (!accountId || !inviteName.trim() || !invitePhone.trim()) {
      setStatus("Для приглашения укажите имя и телефон сотрудника");
      return;
    }
    try {
      const result = await api<{ invite_url?: string }>("/api/team/invite", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId, name: inviteName.trim(), phone: invitePhone.trim(), email: inviteEmail.trim() || null, role: inviteRole, expires_hours: 72 }),
      });
      setLastInviteUrl(result.invite_url || "");
      setInviteName(""); setInvitePhone(""); setInviteEmail("");
      setStatus("Приглашение создано. Ссылка действует 72 часа.");
      const refreshed = await api<{ members?: TeamMembership[] }>("/api/team/memberships?account_id=" + encodeURIComponent(accountId));
      setTeamMembers(refreshed.members || []);
    } catch (error) {
      setStatus("Приглашение не создано: " + (error as Error).message);
    }
  };

  const changeTeamRole = async (member: TeamMembership, role: "MANAGER_LEAD" | "MANAGER" | "VIEWER") => {
    try {
      await api("/api/team/memberships/" + member.id, { method: "PATCH", body: JSON.stringify({ role }) });
      setTeamMembers(current => current.map(item => item.id === member.id ? { ...item, role } : item));
      setStatus("Роль сотрудника обновлена.");
    } catch (error) {
      setStatus("Роль не изменена: " + (error as Error).message);
    }
  };

  const revokePendingInvite = async (member: TeamMembership) => {
    if (!window.confirm("Отозвать приглашение для " + (member.name || member.email || member.phone || "сотрудника") + "?")) return;
    try {
      await api("/api/team/memberships/" + member.id + "/revoke-invite", { method: "POST" });
      setTeamMembers(current => current.map(item => item.id === member.id ? { ...item, status: "REVOKED" } : item));
      setStatus("Приглашение отозвано.");
    } catch (error) {
      setStatus("Приглашение не отозвано: " + (error as Error).message);
    }
  };

  const revokeTeamMember = async (member: TeamMembership) => {
    if (!member.user_id || !accountId) { setStatus("Приглашение ещё не принято."); return; }
    if (!window.confirm("Отозвать доступ у " + (member.name || member.email || "сотрудника") + "?")) return;
    try {
      await api("/api/team/revoke", { method: "POST", body: JSON.stringify({ user_id: member.user_id, account_id: accountId }) });
      setTeamMembers(current => current.filter(item => item.id !== member.id));
      setStatus("Доступ сотрудника отозван.");
    } catch (error) {
      setStatus("Доступ не отозван: " + (error as Error).message);
    }
  };

  const copyInviteUrl = async () => {
    if (!lastInviteUrl) return;
    try { await navigator.clipboard.writeText(lastInviteUrl); setStatus("Ссылка приглашения скопирована."); }
    catch { setStatus("Не удалось скопировать ссылку автоматически."); }
  };

  const copyInvitePackage = async () => {
    if (!lastInviteUrl || typeof window === "undefined") return;
    const guideUrl = window.location.origin + "/messenger/guide";
    const text = [
      "Вас пригласили в BORIS Messenger.",
      "",
      "1. Откройте персональное приглашение:",
      lastInviteUrl,
      "",
      "2. После регистрации войдите в BORIS — первый запуск сам предложит установить приложение и включить уведомления.",
      "",
      "3. Инструкция для iPhone, Android, Windows и Mac:",
      guideUrl,
      "",
      "После входа разрешите уведомления. Микрофон и камера понадобятся для голосовых, видеосообщений и звонков.",
    ].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Готовое сообщение для сотрудника скопировано.");
    } catch {
      setStatus("Не удалось скопировать сообщение автоматически.");
    }
  };

  useEffect(() => {
    let cancelled = false;
    const checkLegal = async () => {
      try {
        const state = await api<{ accepted?: boolean }>("/api/legal/messenger/status");
        if (cancelled) return;
        if (state.accepted) {
          setLegalReady(true);
          return;
        }
        localStorage.setItem("boris_messenger_legal_return", window.location.pathname + window.location.search);
        window.location.replace("/messenger/legal");
      } catch (error) {
        if (!cancelled && !isTransientNetworkError(error)) setStatus((error as Error).message);
        if (!cancelled && isTransientNetworkError(error) && !navigator.onLine) setRealtimeState("offline");
      }
    };
    void checkLegal();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!legalReady) return;
    let cancelled = false;
    const resolveAccountContext = async () => {
      try {
        const [result, me] = await Promise.all([
          api<{ accounts?: AccessibleAccount[] }>("/api/messenger-workspace/accounts"),
          api<{ user?: { id?: number; platform_admin?: boolean } }>("/api/auth/me"),
        ]);
        if (cancelled) return;
        setCurrentUserId(me.user?.id ?? null);
        setPlatformAdmin(Boolean(me.user?.platform_admin));
        const permitted = (result.accounts || []).filter(account => account.account_id);
        setAccounts(permitted);
        const requested = new URLSearchParams(window.location.search).get("account_id")
          || localStorage.getItem("boris_account_id")
          || localStorage.getItem("account_id")
          || "";
        const selectedAccount = permitted.find(account => account.account_id === requested)?.account_id
          || permitted[0]?.account_id
          || "";
        setAccountId(selectedAccount);
        if (!selectedAccount) {
          setStatus("Нет доступных аккаунтов для этого пользователя");
        } else if (requested && requested !== selectedAccount) {
          setStatus(`Выбран разрешённый аккаунт ${selectedAccount}`);
        }
      } catch (error) {
        if (!cancelled) {
          setAccounts([]);
          setAccountId("");
          setStatus(`Не удалось определить доступные аккаунты: ${(error as Error).message}`);
        }
      }
    };
    void resolveAccountContext();
    return () => { cancelled = true; };
  }, [legalReady]);

  useEffect(() => {
    if (!accountId) { setGeneralReadyAccount(""); return; }
    let cancelled = false;
    setGeneralReadyAccount("");
    const ensureGeneral = async () => {
      try {
        await api("/api/internal-messenger/general/ensure", { method: "POST", body: JSON.stringify({ account_id: accountId }) });
        if (!cancelled) setGeneralReadyAccount(accountId);
      } catch (error) {
        if (!cancelled) {
          setGeneralReadyAccount(accountId);
          setStatus("Общий чат подключается. Пока доступен текущий список внутренних чатов.");
          window.setTimeout(() => { if (!cancelled) void ensureGeneral(); }, 5000);
        }
      }
    };
    void ensureGeneral();
    return () => { cancelled = true; };
  }, [accountId]);

  useEffect(() => {
    if (!accountId || generalReadyAccount !== accountId) return;
    let stopped = false;
    const controller = new AbortController();
    const cursorKey = "boris_messenger_event_cursor:" + accountId;
    let cursor = Number(localStorage.getItem(cursorKey) || "0") || 0;
    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("boris-messenger:" + accountId) : null;
    const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

    const acceptEvent = (event: MessengerRealtimeEvent, broadcast = true) => {
      const eventId = Number(event.id || 0);
      if (!eventId || eventId <= cursor) return;
      cursor = eventId;
      localStorage.setItem(cursorKey, String(cursor));
      setRealtimeTick(value => value + 1);
      if (broadcast) channel?.postMessage({ id: eventId });
    };

    if (channel) {
      channel.onmessage = message => {
        const eventId = Number(message.data?.id || 0);
        if (eventId > cursor) {
          cursor = eventId;
          localStorage.setItem(cursorKey, String(cursor));
          setRealtimeTick(value => value + 1);
        }
      };
    }

    let activeStreamController: AbortController | null = null;
    const onOffline = () => { setRealtimeState("offline"); activeStreamController?.abort(); };
    const onOnline = () => { setRealtimeState("reconnecting"); activeStreamController?.abort(); };
    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);

    const run = async () => {
      let backoff = 1000;
      let fallbackHealthy = false;
      while (!stopped) {
        if (!navigator.onLine) {
          fallbackHealthy = false;
          setRealtimeState("offline");
          await sleep(2000);
          continue;
        }
        if (!fallbackHealthy) setRealtimeState(cursor > 0 ? "reconnecting" : "connecting");
        const streamController = new AbortController();
        activeStreamController = streamController;
        const forwardOuterAbort = () => streamController.abort();
        controller.signal.addEventListener("abort", forwardOuterAbort, { once: true });
        let connectTimer: number | null = window.setTimeout(() => streamController.abort(), 3500);
        try {
          const headers = new Headers(tokenHeaders());
          headers.set("Accept", "text/event-stream");
          const response = await fetch(
            "/api/internal-messenger/events/stream?account_id=" + encodeURIComponent(accountId) + "&after_id=" + encodeURIComponent(String(cursor)),
            { headers, cache: "no-store", signal: streamController.signal },
          );
          if (connectTimer !== null) {
            window.clearTimeout(connectTimer);
            connectTimer = null;
          }
          if (response.status === 401) {
            localStorage.removeItem("boris_token");
            localStorage.removeItem("boris_messenger_token");
            localStorage.setItem("boris_post_login_return", window.location.pathname + window.location.search);
            window.location.replace("/messenger/login");
            return;
          }
          if (response.status === 403) {
            setWorkspaceLeaving(true);
            for (const key of ["boris_account_id", "account_id", "boris_currentAccount"]) localStorage.removeItem(key);
            window.location.replace("/messenger");
            return;
          }
          if (!response.ok || !response.body) throw new Error("SSE HTTP " + response.status);
          fallbackHealthy = false;
          setRealtimeState("online");
          backoff = 1000;
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (!stopped) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let boundary = buffer.indexOf("\n\n");
            while (boundary >= 0) {
              const block = buffer.slice(0, boundary);
              buffer = buffer.slice(boundary + 2);
              let eventId = 0;
              const dataLines: string[] = [];
              for (const line of block.split("\n")) {
                if (line.startsWith("id:")) eventId = Number(line.slice(3).trim()) || 0;
                if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
              }
              if (dataLines.length) {
                try {
                  const event = JSON.parse(dataLines.join("\n")) as MessengerRealtimeEvent;
                  if (!event.id && eventId) event.id = eventId;
                  acceptEvent(event);
                } catch {
                  // Ignore one malformed event; reconnect/reconciliation remains available.
                }
              }
              boundary = buffer.indexOf("\n\n");
            }
          }
          if (!stopped) throw new Error("SSE stream closed");
        } catch (error) {
          if (stopped || controller.signal.aborted) return;
          if (!navigator.onLine) {
            fallbackHealthy = false;
            setRealtimeState("offline");
            await sleep(2000);
            continue;
          }
          // REALTIME_POLLING_FALLBACK_V1:
          // Some browser/proxy chains can terminate a healthy SSE stream. Keep
          // Messenger operational and visually online while the ordinary events
          // endpoint is reachable, then retry SSE silently in the next cycle.
          try {
            const pollHeaders = new Headers(tokenHeaders());
            const pollResponse = await fetch(
              "/api/internal-messenger/events?account_id=" + encodeURIComponent(accountId)
                + "&after_id=" + encodeURIComponent(String(cursor)) + "&limit=100",
              { headers: pollHeaders, cache: "no-store", signal: controller.signal },
            );
            if (pollResponse.status === 401) {
              localStorage.removeItem("boris_token");
              localStorage.removeItem("boris_messenger_token");
              localStorage.setItem("boris_post_login_return", window.location.pathname + window.location.search);
              window.location.replace("/messenger/login");
              return;
            }
            if (pollResponse.ok) {
              const pollData = await pollResponse.json() as { items?: MessengerRealtimeEvent[] };
              for (const item of pollData.items || []) acceptEvent(item);
              fallbackHealthy = true;
              setRealtimeState("online");
              backoff = 1000;
              await sleep(2500);
              continue;
            }
          } catch {
            // Fall through to bounded reconnect state/backoff.
          }
          fallbackHealthy = false;
          setRealtimeState("reconnecting");
          await sleep(backoff);
          backoff = Math.min(backoff * 2, 10000);
        } finally {
          if (connectTimer !== null) window.clearTimeout(connectTimer);
          controller.signal.removeEventListener("abort", forwardOuterAbort);
          if (activeStreamController === streamController) activeStreamController = null;
        }
      }
    };

    void run();
    return () => {
      stopped = true;
      controller.abort();
      channel?.close();
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
    };
  }, [accountId, generalReadyAccount]);

  useEffect(() => {
    if (!accountId || generalReadyAccount !== accountId || section !== "chats" || chatMode !== "internal") return;
    let cancelled = false;
    const loadInternal = async () => {
      try {
        const [result, peopleResult] = await Promise.all([
          api<{ conversations?: Array<{ id: number; kind: "DM" | "GROUP"; title?: string | null; unread_count?: number; last_body?: string | null; is_general?: boolean }> }>(`/api/internal-messenger/conversations?account_id=${encodeURIComponent(accountId)}${internalQuery.trim() ? `&q=${encodeURIComponent(internalQuery.trim())}` : ""}`),
          api<{ people?: InternalCandidate[] }>(`/api/internal-messenger/people?account_id=${encodeURIComponent(accountId)}`),
        ]);
        if (cancelled) return;
        const people = (peopleResult.people || []).filter(person => person.user_id !== currentUserId);
        setInternalPeople(people);
        setSelectedPersonId(current => current && people.some(person => person.user_id === current) ? current : (people[0]?.user_id || null));
        const rows = (result.conversations || []).map(thread => ({
          id: String(thread.id),
          title: thread.is_general ? "Общий чат" : (thread.title || (thread.kind === "GROUP" ? "Рабочая группа" : "Личный чат")),
          kind: thread.kind === "GROUP" ? "Группа" : "Личный",
          members: [],
          lastText: thread.last_body || "Сообщений пока нет",
          unread: thread.unread_count || 0,
          isGeneral: Boolean(thread.is_general),
        } satisfies InternalThread));
        setInternalThreads(rows);
        setInternalSelected(current => {
          const refreshed = current && rows.find(row => row.id === current.id);
          return refreshed ? { ...refreshed, members: current!.members } : rows[0] || null;
        });
        setInternalConversationId(current => current && rows.some(row => Number(row.id) === current) ? current : (rows[0] ? Number(rows[0].id) : null));
      } catch {
        if (!cancelled && !navigator.onLine) setRealtimeState("offline");
      }
    };
    const initial = window.setTimeout(() => void loadInternal(), internalQuery.trim() ? 250 : 0);
    // THREAD_LIST_RECONCILIATION_V1: SSE is the fast path, but a visible
    // Messenger also reconciles its conversation list periodically. This heals
    // missed conversation.created/member events without requiring F5.
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadInternal();
    }, 2500);
    return () => { cancelled = true; window.clearTimeout(initial); window.clearInterval(timer); };
  }, [accountId, generalReadyAccount, section, chatMode, currentUserId, internalQuery, realtimeState, realtimeTick]);

  useEffect(() => {
    if (typeof window === "undefined" || !internalThreads.length) return;
    const requested = Number(new URLSearchParams(window.location.search).get("internal_conversation_id") || 0);
    if (!requested) { internalDeepLinkAppliedRef.current = ""; return; }
    const key = accountId + ":" + requested;
    if (internalDeepLinkAppliedRef.current === key) return;
    const thread = internalThreads.find(item => Number(item.id) === requested);
    if (!thread) return;
    internalDeepLinkAppliedRef.current = key;
    setSection("chats");
    setChatMode("internal");
    setInternalSelected(thread);
    setInternalConversationId(requested);
    setInternalPane("chat");
  }, [internalThreads]);

  useEffect(() => {
    setInternalReportPreview(null);
    setInternalComposerMenuOpen(false);
    setInternalReportMenuOpen(false);
  }, [accountId, internalConversationId]);

  useEffect(() => {
    draftHydratedConversationRef.current = null;
    if (!offlineScope || !internalConversationId) {
      setInternalDraft("");
      return;
    }
    let cancelled = false;
    setInternalDraft("");
    void loadDraft(offlineScope, internalConversationId)
      .then(value => {
        if (cancelled) return;
        draftHydratedConversationRef.current = internalConversationId;
        setInternalDraft(value);
      })
      .catch(() => {
        if (!cancelled) draftHydratedConversationRef.current = internalConversationId;
      });
    return () => { cancelled = true; };
  }, [offlineScope, internalConversationId]);

  useEffect(() => {
    if (!offlineScope || !internalConversationId || internalEditing) return;
    if (draftHydratedConversationRef.current !== internalConversationId) return;
    const timer = window.setTimeout(() => {
      void saveDraft(offlineScope, internalConversationId, internalDraft).catch(() => undefined);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [offlineScope, internalConversationId, internalDraft, internalEditing]);

  useEffect(() => {
    if (!offlineScope) {
      setOutboxCount(0);
      return;
    }
    void listOutbox(offlineScope)
      .then(items => setOutboxCount(items.length))
      .catch(() => setOutboxCount(0));
  }, [offlineScope, realtimeTick]);

  useEffect(() => {
    if (!offlineScope || !internalConversationId) return;
    let cancelled = false;
    void listOutbox(offlineScope).then(items => {
      if (cancelled) return;
      const pending = items.filter(item => item.conversationId === internalConversationId);
      if (!pending.length) return;
      const optimistic: Message[] = pending.map(item => {
        if (item.kind === "voice") {
          return {
            id: "offline:" + item.id,
            offline_id: item.id,
            message_type: "voice",
            body: "",
            is_own: true,
            direction: "out",
            sender: "manager",
            author_label: "Вы",
            created_at: new Date(item.createdAt).toISOString(),
            delivery_status: item.lastError ? "Повторная отправка" : "Ожидает сети",
            optimistic: true,
            voice: {
              local_blob_id: item.voiceBlobId || null,
              asset_id: item.voiceAssetId || undefined,
              transcription_status: "none",
            },
          };
        }
        const remote = (item.remoteAttachments || []).map(value => value as InternalAttachment);
        const local = (item.localAttachments || []).map(value => ({
          id: "offline:" + value.blobId,
          file_name: value.name,
          content_type: value.type,
          size_bytes: value.size,
          storage_ref: "offline:" + value.blobId,
          offline_blob_id: value.blobId,
        } satisfies InternalAttachment));
        return {
          id: "offline:" + item.id,
          offline_id: item.id,
          body: item.body,
          text: item.body,
          is_own: true,
          direction: "out",
          sender: "manager",
          author_label: "Вы",
          created_at: new Date(item.createdAt).toISOString(),
          delivery_status: item.lastError ? "Повторная отправка" : "Ожидает сети",
          optimistic: true,
          attachments: [...remote, ...local],
        };
      });
      setInternalMessages(current => {
        const existing = new Set(current.map(message => message.offline_id).filter(Boolean));
        return [...current, ...optimistic.filter(message => !existing.has(message.offline_id))];
      });
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [offlineScope, internalConversationId]);

  useEffect(() => {
    if (!internalConversationId || section !== "chats" || chatMode !== "internal") return;
    let cancelled = false;
    const isNewConversation = internalLoadedConversationRef.current !== internalConversationId;
    if (isNewConversation) {
      internalLoadedConversationRef.current = internalConversationId;
      internalInitialScrollRef.current = internalConversationId;
      internalNearBottomRef.current = true;
      setInternalMessages([]);
      setInternalHasMore(false);
      setInternalNextBeforeId(null);
    }
    const loadInternalMessages = async () => {
      try {
        const [result, memberResult, receiptResult] = await Promise.all([
          api<{ messages?: Message[]; has_more?: boolean; next_before_id?: number | null }>(`/api/internal-messenger/conversations/${internalConversationId}/messages?limit=100`),
          api<{ members?: InternalPerson[] }>(`/api/internal-messenger/conversations/${internalConversationId}/members`),
          api<{ receipts?: InternalReceipt[] }>(`/api/internal-messenger/conversations/${internalConversationId}/read-receipts`),
        ]);
        if (!cancelled) {
          const loaded = result.messages || [];
          if (offlineScope) void cacheMessages(offlineScope, internalConversationId, loaded).catch(() => undefined);
          const members = memberResult.members || [];
          setInternalMessages(current => {
            const optimistic = current.filter(message => message.optimistic);
            if (isNewConversation) return [...loaded, ...optimistic];
            const byId = new Map<string, Message>();
            [...current, ...loaded].forEach(message => byId.set(String(message.id), message));
            return [...byId.values()].sort((a, b) => {
              if (a.optimistic && !b.optimistic) return 1;
              if (!a.optimistic && b.optimistic) return -1;
              return Number(a.id || 0) - Number(b.id || 0);
            });
          });
          if (isNewConversation) {
            setInternalHasMore(Boolean(result.has_more));
            setInternalNextBeforeId(result.next_before_id ?? (loaded[0]?.id ? Number(loaded[0].id) : null));
          }
          setInternalMembers(members);
          setInternalReceipts(receiptResult.receipts || []);
          setInternalSelected(current => current ? { ...current, members: members.map(member => member.email) } : current);
          setInternalThreads(current => current.map(thread => thread.id === String(internalConversationId) ? { ...thread, unread: 0, members: members.map(member => member.email) } : thread));
          const last = loaded[loaded.length - 1];
          if (last?.id) void api(`/api/internal-messenger/conversations/${internalConversationId}/read?message_id=${encodeURIComponent(String(last.id))}`, { method: "POST" }).catch(() => undefined);
        }
      } catch {
        if (!cancelled && !navigator.onLine) {
          setRealtimeState("offline");
          if (offlineScope) {
            try {
              const cached = await loadCachedMessages<Message>(offlineScope, internalConversationId);
              if (!cancelled && cached.length) setInternalMessages(cached);
            } catch {
              // Offline cache is best-effort; never hide the chat shell.
            }
          }
        }
      }
    };
    void loadInternalMessages();
    // MESSAGE_HISTORY_RECONCILIATION_V1: SSE remains the fast path, while
    // the currently open conversation self-heals in case a browser/proxy drops
    // one event. This prevents stale chats without requiring F5.
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadInternalMessages();
    }, 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [internalConversationId, section, chatMode, realtimeState, realtimeTick, offlineScope]);

  const loadOlderInternalMessages = async () => {
    if (!internalConversationId || !internalHasMore || !internalNextBeforeId || internalOlderLoading) return;
    const history = internalHistoryRef.current;
    if (history) internalRestoreScrollRef.current = { height: history.scrollHeight, top: history.scrollTop };
    setInternalOlderLoading(true);
    try {
      const result = await api<{ messages?: Message[]; has_more?: boolean; next_before_id?: number | null }>(
        `/api/internal-messenger/conversations/${internalConversationId}/messages?limit=100&before_id=${encodeURIComponent(String(internalNextBeforeId))}`,
      );
      const older = result.messages || [];
      setInternalMessages(current => {
        const byId = new Map<string, Message>();
        [...older, ...current].forEach(message => byId.set(String(message.id), message));
        return [...byId.values()].sort((a, b) => Number(a.id || 0) - Number(b.id || 0));
      });
      setInternalHasMore(Boolean(result.has_more));
      setInternalNextBeforeId(result.next_before_id ?? (older[0]?.id ? Number(older[0].id) : null));
      setStatus(older.length ? `Загружено более ранних сообщений: ${older.length}` : "Более ранних сообщений нет.");
    } catch (error) {
      setStatus("Не удалось загрузить ранние сообщения: " + (error as Error).message);
    } finally {
      setInternalOlderLoading(false);
    }
  };

  useEffect(() => {
    const history = internalHistoryRef.current;
    if (!history || !internalConversationId) return;
    const frame = window.requestAnimationFrame(() => {
      const restore = internalRestoreScrollRef.current;
      if (restore) {
        history.scrollTop = Math.max(0, history.scrollHeight - restore.height + restore.top);
        internalRestoreScrollRef.current = null;
        return;
      }
      if (internalInitialScrollRef.current === internalConversationId) {
        history.scrollTop = history.scrollHeight;
        internalInitialScrollRef.current = null;
        internalNearBottomRef.current = true;
        return;
      }
      if (internalNearBottomRef.current) history.scrollTop = history.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [internalMessages.length, internalConversationId]);

  const loadInbox = async (searchTerm = query.trim()) => {
    if (!accountId) { setStatus("Выберите доступный аккаунт BORIS, чтобы открыть чаты"); return; }
    const requestId = ++inboxRequestSeq.current;
    setLoading(true);
    try {
      const searchParam = searchTerm ? `&q=${encodeURIComponent(searchTerm)}` : "";
      const results = await Promise.all(STAGES.map(stage => api<{ leads?: Lead[] }>(`/api/messenger-workspace/leads?account_id=${encodeURIComponent(accountId)}&stage=${encodeURIComponent(stage)}${searchParam}`)));
      if (requestId !== inboxRequestSeq.current) return;
      const unique = new Map<string, Lead>();
      results.forEach(result => (result.leads || []).forEach(lead => unique.set(lead.chat_id, lead)));
      const rows = [...unique.values()];
      setLeads(rows);
      setSelected(current => current ? (rows.find(row => row.chat_id === current.chat_id) || current) : (rows.length && window.innerWidth > 900 ? rows[0] : null));
      setStatus(searchTerm ? `Найдено диалогов: ${rows.length}` : `Аккаунт ${accountId} · ${rows.length} диалогов · обновлено ${new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`);
    } catch (error) {
      if (requestId === inboxRequestSeq.current) {
        if (isTransientNetworkError(error) && !navigator.onLine) setRealtimeState("offline");
        else setStatus(friendlyInboxError(error));
      }
    } finally {
      if (requestId === inboxRequestSeq.current) setLoading(false);
    }
  };

  const loadPendingDrafts = async () => {
    if (!accountId) { setAiDrafts([]); return; }
    try {
      const result = await api<{ drafts?: AiDraft[] }>("/api/messenger-workspace/pending-drafts?account_id=" + encodeURIComponent(accountId));
      const drafts = result.drafts || [];
      setAiDrafts(drafts);
      setAiDraftEdits(current => {
        const next = { ...current };
        for (const draft of drafts) if (!(draft.draft_id in next)) next[draft.draft_id] = draft.text || "";
        for (const key of Object.keys(next)) if (!drafts.some(draft => draft.draft_id === key)) delete next[key];
        return next;
      });
    } catch {
      if (!navigator.onLine) setRealtimeState("offline");
    }
  };

  const actOnAiDraft = async (draft: AiDraft, action: "approve" | "discard") => {
    if (aiDraftBusy) return;
    setAiDraftBusy(draft.draft_id);
    try {
      const result = await api<{ status?: string; message?: string }>("/api/messenger-workspace/draft/" + action, {
        method: "POST",
        body: JSON.stringify({ draft_id: draft.draft_id, text: action === "approve" ? (aiDraftEdits[draft.draft_id] || draft.text || "") : null }),
      });
      if (result.status === "error") throw new Error(result.message || "Действие не выполнено");
      setAiDrafts(current => current.filter(item => item.draft_id !== draft.draft_id));
      setAiDraftEdits(current => { const next = { ...current }; delete next[draft.draft_id]; return next; });
      setStatus(action === "approve" ? "AI-ответ подтверждён и отправлен." : "AI-черновик отклонён.");
      if (selected) await Promise.all([loadInbox(), loadContext(selected)]);
    } catch (error) {
      setStatus((action === "approve" ? "AI-ответ не отправлен: " : "Черновик не отклонён: ") + (error as Error).message);
    } finally {
      setAiDraftBusy(null);
    }
  };

  const loadContext = async (lead: Lead | null) => {
    if (!lead || !accountId) { setMessages([]); setTasks([]); return; }
    try {
      const [history, taskResult] = await Promise.all([
        api<{ messages?: Message[]; items?: Message[] }>(`/api/messenger-workspace/dialog?account_id=${encodeURIComponent(accountId)}&avito_chat_id=${encodeURIComponent(lead.chat_id)}&format=json`),
        api<{ tasks?: Task[] }>(`/api/messenger-workspace/tasks?account_id=${encodeURIComponent(accountId)}&chat_id=${encodeURIComponent(lead.chat_id)}`),
      ]);
      const authoritative = history.messages || history.items || [];
      setMessages(current => {
        const optimistic = current.filter(message => message.optimistic);
        const pending = optimistic.filter(local => !authoritative.some(remote => {
          const localId = String(local.id || "");
          const remoteId = String(remote.id || "");
          if (localId && remoteId && !localId.startsWith("local-send:") && localId === remoteId) return true;
          const localText = String(local.text || local.body || local.message || "").trim();
          const remoteText = String(remote.text || remote.body || remote.message || "").trim();
          const remoteOutgoing = remote.direction === "out" || remote.sender === "manager" || remote.is_own;
          return Boolean(localText && remoteOutgoing && localText === remoteText);
        }));
        return [...authoritative, ...pending];
      });
      setTasks(taskResult.tasks || []);
    } catch { if (!navigator.onLine) setRealtimeState("offline"); }
  };

  const loadAccountTasks = async () => {
    if (!accountId) { setAccountTasks([]); return; }
    try {
      const result = await api<{ tasks?: Task[] }>(`/api/messenger-workspace/tasks?account_id=${encodeURIComponent(accountId)}`);
      setAccountTasks(result.tasks || []);
    } catch {
      if (!navigator.onLine) setRealtimeState("offline");
    }
  };

  useEffect(() => { void loadInbox(); void loadPendingDrafts(); void loadAccountTasks(); }, [accountId, realtimeTick]);
  useEffect(() => {
    if (!accountId) return;
    const timer = window.setTimeout(() => void loadInbox(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query, accountId]);
  useEffect(() => {
    void loadContext(selected);
    if (selected) {
      setMobilePane("chat");
      const draftKey = "boris_messenger_client_draft:" + accountId + ":" + selected.chat_id;
      setDraft(localStorage.getItem(draftKey) || "");
    } else {
      setDraft("");
    }
  }, [selected?.chat_id, accountId]);
  useEffect(() => {
    if (!accountId || !leads.length) return;
    const params = new URLSearchParams(window.location.search);
    const requestedTaskId = Number(params.get("task_id") || 0);
    const requestedChatId = String(params.get("client_chat_id") || "").trim();
    const task = requestedTaskId ? accountTasks.find(item => item.id === requestedTaskId) : null;
    if (requestedTaskId && !task) return;
    const targetChatId = requestedChatId || String(task?.chat_id || "").trim();
    if (!targetChatId) return;
    const deepLinkKey = accountId + ":" + targetChatId + ":" + String(requestedTaskId || "");
    if (deepLinkAppliedRef.current === deepLinkKey) return;
    const lead = leads.find(item => String(item.chat_id) === targetChatId);
    if (!lead) return;
    deepLinkAppliedRef.current = deepLinkKey;
    setSection("clients");
    setChatMode("clients");
    setSelected(lead);
    setMobilePane(requestedTaskId ? "crm" : "chat");
    setStatus(requestedTaskId ? "Открыта CRM-задача из уведомления." : "Открыт клиентский диалог из уведомления.");
  }, [accountId, leads, accountTasks]);

  useEffect(() => {
    if (!selected) return;
    const refresh = () => { if (document.visibilityState === "visible") { void loadContext(selected); void loadPendingDrafts(); } };
    const timer = window.setInterval(refresh, 4000);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [selected?.chat_id, accountId]);

  const visibleLeads = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const overdueChats = new Set(accountTasks.filter(task => taskStatusKey(task) === "overdue" && task.chat_id).map(task => String(task.chat_id)));
    return leads.filter(lead => {
      const matchesSearch = !needle || `${lead.contact_name || ""} ${lead.last_text || ""} ${lead.item_title || ""} ${lead.phone || ""} ${lead.chat_id || ""} ${lead.stage || ""}`.toLowerCase().includes(needle);
      if (!matchesSearch) return false;
      if (clientFilter === "unread") return Number(lead.unread_count || 0) > 0;
      if (clientFilter === "human") return lead.owner_type === "human";
      if (clientFilter === "ai") return lead.owner_type !== "human";
      if (clientFilter === "mine") return Boolean(currentUserId && String(lead.assigned_to || "") === String(currentUserId));
      if (clientFilter === "unassigned") return !lead.assigned_to;
      if (clientFilter === "overdue") return overdueChats.has(String(lead.chat_id));
      return true;
    });
  }, [leads, query, clientFilter, accountTasks, currentUserId]);

  const take = async (kind: "take" | "release" | "human" | "mop") => {
    if (!selected) return;
    const path = kind === "take" ? "/api/messenger-workspace/ownership/take" : kind === "release" ? "/api/messenger-workspace/ownership/release" : `/api/messenger-workspace/ownership/handoff?action=${kind}`;
    try { await api(path, { method: "POST", body: JSON.stringify({ account_id: accountId, avito_chat_id: selected.chat_id }) }); await Promise.all([loadInbox(), loadContext(selected)]); setStatus(kind === "mop" ? "Диалог возвращён BORIS" : kind === "release" ? "Назначение снято; режим AI/HUMAN не изменён" : kind === "human" ? "AI остановлен, диалог ведётся вручную" : "Диалог взят сотрудником"); }
    catch (error) { setStatus(`Действие не выполнено: ${(error as Error).message}`); }
  };

  const updateClientDraft = (value: string) => {
    setDraft(value);
    if (!selected || !accountId) return;
    const draftKey = "boris_messenger_client_draft:" + accountId + ":" + selected.chat_id;
    if (value) localStorage.setItem(draftKey, value);
    else localStorage.removeItem(draftKey);
  };

  const send = async () => {
    if (!selected || !draft.trim() || sending || !canReply) return;
    if (!navigator.onLine || realtimeState === "offline") {
      setStatus("Нет соединения с BORIS. Текст сохранён — отправьте его после восстановления связи.");
      return;
    }
    const text = draft.trim();
    const key = sendKey.current || crypto.randomUUID();
    sendKey.current = key;
    setSending(true);
    try {
      const result = await api<{ provider_message_id?: string | null; delivery_status?: string; avito_message?: Record<string, unknown> | null }>("/api/messenger-workspace/send", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({ account_id: accountId, avito_chat_id: selected.chat_id, text, idempotency_key: key }),
      });
      const providerId = result.provider_message_id ? String(result.provider_message_id) : "";
      const optimistic: Message = {
        id: providerId || `local-send:${key}`,
        text,
        direction: "out",
        sender: "manager",
        is_own: true,
        author_label: "Вы",
        created_at: new Date().toISOString(),
        delivery_status: result.delivery_status || "sent",
        optimistic: true,
      };
      setMessages(current => current.some(message =>
        (providerId && String(message.id || "") === providerId)
        || ((message.direction === "out" || message.sender === "manager" || message.is_own)
          && String(message.text || message.body || message.message || "").trim() === text)
      ) ? current : [...current, optimistic]);
      setDraft("");
      localStorage.removeItem("boris_messenger_client_draft:" + accountId + ":" + selected.chat_id);
      sendKey.current = null;
      setStatus("Сообщение отправлено");
      await loadInbox();
      window.setTimeout(() => { void loadContext(selected); }, 1200);
    } catch (error) {
      setStatus(`Ответ не отправлен: ${(error as Error).message}. Текст сохранён — можно повторить отправку.`);
    } finally {
      setSending(false);
    }
  };

  useEffect(() => {
    const saved = localStorage.getItem("boris_messenger_theme") as "light" | "dark" | "system" | null;
    if (saved === "light" || saved === "dark" || saved === "system") setTheme(saved);
  }, []);
  useEffect(() => {
    localStorage.setItem("boris_messenger_theme", theme);
    document.documentElement.dataset.messengerTheme = theme;
  }, [theme]);
  useEffect(() => {
    const saved = localStorage.getItem("boris_messenger_accent") as "blue" | "graphite" | "sky" | null;
    if (saved === "blue" || saved === "graphite" || saved === "sky") setAccent(saved);
  }, []);
  useEffect(() => {
    localStorage.setItem("boris_messenger_accent", accent);
  }, [accent]);
  useEffect(() => {
    if (accountId) void detectMessengerPush();
  }, [accountId]);

  useEffect(() => {
    const onUpdateReady = (event: Event) => {
      const detail = (event as CustomEvent<{ registration?: ServiceWorkerRegistration }>).detail;
      if (detail?.registration?.waiting) setPwaUpdateRegistration(detail.registration);
    };
    window.addEventListener("boris-pwa-update-ready", onUpdateReady as EventListener);
    return () => window.removeEventListener("boris-pwa-update-ready", onUpdateReady as EventListener);
  }, []);

  const applyPwaUpdate = () => {
    const waiting = pwaUpdateRegistration?.waiting;
    if (!waiting) {
      setPwaUpdateRegistration(null);
      setStatus("Обновление уже применено.");
      return;
    }
    if (voiceState !== "idle" || videoState !== "idle") {
      setStatus("Сначала завершите или удалите текущую запись медиа.");
      return;
    }
    if (activeCall) {
      setStatus("Обновление применится после завершения звонка.");
      return;
    }
    if (outboxCount > 0 || internalSending || attachmentUploading) {
      setStatus("Сначала дождитесь отправки сообщений и файлов из очереди.");
      return;
    }
    setStatus("Обновляем BORIS Messenger…");
    waiting.postMessage({ type: "BORIS_SKIP_WAITING" });
  };

  useEffect(() => {
    setInstalledApp(isStandalone());
    const captureInstall = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as BeforeInstallPromptEvent);
    };
    const onInstalled = () => {
      setInstallPrompt(null);
      setInstalledApp(true);
      setStatus("BORIS Messenger установлен на устройство.");
    };
    window.addEventListener("beforeinstallprompt", captureInstall);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", captureInstall);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  const installMessenger = async () => {
    if (installedApp || isStandalone()) {
      setInstalledApp(true);
      setStatus("BORIS Messenger уже установлен на этом устройстве.");
      return;
    }
    if (installPrompt) {
      try {
        await installPrompt.prompt();
        const choice = await installPrompt.userChoice;
        if (choice.outcome === "accepted") {
          setInstalledApp(true);
          setStatus("Установка BORIS Messenger подтверждена.");
        } else {
          setStatus("Установка отменена. Её можно запустить позже.");
        }
        setInstallPrompt(null);
      } catch (error) {
        setStatus("Не удалось запустить установку: " + (error as Error).message);
      }
      return;
    }
    setStatus(isIos() ? "На iPhone: Поделиться → На экран «Домой» → Добавить." : "Установка через браузер пока недоступна. Откройте меню браузера → «Установить приложение».");
  };

  const detectMessengerPush = async () => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      setPushState("unsupported");
      return;
    }
    if (Notification.permission === "denied") {
      setPushState("denied");
      return;
    }
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      setPushState(subscription ? "on" : "off");
    } catch {
      setPushState("off");
    }
  };

  const enableMessengerPush = async () => {
    if (!accountId) return;
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        throw new Error("Этот браузер не поддерживает push-уведомления");
      }
      if (isIos() && !isStandalone()) {
        throw new Error("На iPhone сначала добавьте BORIS Messenger на экран «Домой» и откройте его с синей иконки");
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setPushState(permission === "denied" ? "denied" : "off");
        throw new Error("Разрешение на уведомления не выдано");
      }
      const key = await api<{ public_key?: string }>(`/api/lead-notifications/vapid-public-key?account_id=${encodeURIComponent(accountId)}`);
      if (!key.public_key) throw new Error("Push пока не настроен");
      const registration = await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: base64ToUint8Array(key.public_key) as BufferSource,
        });
      }
      await api("/api/lead-notifications/subscribe", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId, subscription: subscription.toJSON() }),
      });
      setPushState("on");
      setStatus("Push включён: новые сообщения сотрудников будут приходить на это устройство");
    } catch (error) {
      setStatus(`Push не включён: ${(error as Error).message}`);
    }
  };

  const disableMessengerPush = async () => {
    if (!accountId || !("serviceWorker" in navigator)) return;
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await api("/api/lead-notifications/unsubscribe", {
          method: "POST",
          body: JSON.stringify({ account_id: accountId, endpoint: subscription.endpoint }),
        });
        await subscription.unsubscribe();
      }
      setPushState("off");
      setStatus("Push выключен на этом устройстве");
    } catch (error) {
      setStatus(`Push не выключен: ${(error as Error).message}`);
    }
  };

  const storePendingAttachmentOffline = async (file: File) => {
    if (!offlineScope) throw new Error("Локальное хранилище ещё не готово");
    const stored = await putBlob(offlineScope, file, file.name || "file");
    const attachment: InternalAttachment = {
      id: "offline:" + stored.id,
      file_name: stored.name,
      content_type: stored.type || file.type || "application/octet-stream",
      size_bytes: stored.size,
      storage_ref: "offline:" + stored.id,
      offline_blob_id: stored.id,
    };
    setPendingAttachments(current => [...current, attachment]);
    return attachment;
  };

  const uploadInternalAttachment = async (file: File) => {
    if (!internalConversationId) { setStatus("Сначала выберите чат"); return; }
    setAttachmentUploading(true);
    try {
      if (!navigator.onLine || realtimeState === "offline") {
        await storePendingAttachmentOffline(file);
        setStatus("Файл сохранён на устройстве и будет отправлен после восстановления сети: " + file.name);
        return;
      }
      const form = new FormData();
      form.append("file", file);
      const result = await api<{ upload?: InternalAttachment }>(`/api/internal-messenger/conversations/${internalConversationId}/attachments/upload`, { method: "POST", body: form });
      if (result.upload) {
        setPendingAttachments(current => [...current, result.upload as InternalAttachment]);
        setStatus("Файл готов к отправке: " + result.upload.file_name);
      }
    } catch (error) {
      if (isTransientNetworkError(error)) {
        try {
          await storePendingAttachmentOffline(file);
          setRealtimeState("offline");
          setStatus("Связь прервалась. Файл сохранён на устройстве: " + file.name);
        } catch (storageError) {
          setStatus("Файл не сохранён: " + (storageError as Error).message);
        }
      } else {
        setStatus("Файл не загружен: " + (error as Error).message);
      }
    } finally {
      setAttachmentUploading(false);
    }
  };

  const removePendingAttachment = (attachment: InternalAttachment, index: number) => {
    if (attachment.offline_blob_id) void deleteBlob(attachment.offline_blob_id).catch(() => undefined);
    setPendingAttachments(current => current.filter((_, itemIndex) => itemIndex !== index));
  };

  const downloadInternalAttachment = async (attachment: InternalAttachment) => {
    if (!attachment.download_url) return;
    try {
      const response = await fetch(attachment.download_url, { headers: tokenHeaders(), cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = attachment.file_name || "file";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setStatus("Файл не скачан: " + (error as Error).message);
    }
  };

  const uploadStoredAttachment = async (conversationId: number, attachment: OutboxAttachment): Promise<InternalAttachment> => {
    const stored = await getBlob(attachment.blobId);
    if (!stored) throw new Error("Локальный файл не найден");
    const form = new FormData();
    form.append("file", stored.blob, stored.name || attachment.name || "file");
    const result = await api<{ upload?: InternalAttachment }>(
      `/api/internal-messenger/conversations/${conversationId}/attachments/upload`,
      { method: "POST", body: form },
    );
    if (!result.upload) throw new Error("Сервер не подтвердил загрузку файла");
    return result.upload;
  };


  const uploadMediaBlob = async (
    conversationId: number,
    kind: "voice" | "video" | "video_note",
    stored: { blob: Blob; name: string },
  ): Promise<Record<string, any>> => {
    setMediaUploadProgress(0);
    try {
      return await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/internal-messenger/conversations/" + conversationId + "/attachments/upload?attachment_kind=" + kind);
        const headers = tokenHeaders();
        for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
        xhr.upload.onprogress = event => {
          if (event.lengthComputable && event.total > 0) {
            setMediaUploadProgress(Math.max(0, Math.min(100, Math.round(event.loaded * 100 / event.total))));
          }
        };
        xhr.onerror = () => reject(new Error("Не удалось загрузить медиа. Проверьте соединение."));
        xhr.onload = () => {
          let payload: any = {};
          try { payload = xhr.responseText ? JSON.parse(xhr.responseText) : {}; } catch { payload = {}; }
          if (xhr.status === 401) {
            reject(new Error("Сессия истекла. Выполните вход снова."));
            return;
          }
          if (xhr.status < 200 || xhr.status >= 300) {
            const detail = String(payload?.detail || payload?.message || "");
            reject(new Error(detail || (xhr.status >= 500 ? "BORIS временно недоступен. Попробуйте ещё раз." : "HTTP " + xhr.status)));
            return;
          }
          resolve(payload);
        };
        const form = new FormData();
        form.append("file", stored.blob, stored.name);
        xhr.send(form);
      });
    } finally {
      setMediaUploadProgress(null);
    }
  };

  const deliverOutboxItem = async (source: MessengerOutboxItem): Promise<void> => {
    if (!navigator.onLine) throw new Error("network error: offline");
    let item: MessengerOutboxItem = {
      ...source,
      remoteAttachments: [...(source.remoteAttachments || [])],
      localAttachments: [...(source.localAttachments || [])],
    };

    if (item.kind === "text") {
      while ((item.localAttachments || []).length) {
        const local = item.localAttachments![0];
        const uploaded = await uploadStoredAttachment(item.conversationId, local);
        item = {
          ...item,
          remoteAttachments: [...(item.remoteAttachments || []), uploaded as unknown as Record<string, unknown>],
          localAttachments: item.localAttachments!.slice(1),
          cleanupBlobIds: [...(item.cleanupBlobIds || []), local.blobId],
        };
        await updateOutbox(item);
      }
      const created = await api<{ message?: Message }>(
        "/api/internal-messenger/conversations/" + item.conversationId + "/messages",
        {
          method: "POST",
          body: JSON.stringify({
            body: item.body,
            reply_to_message_id: item.replyTo,
            client_message_id: item.clientMessageId,
          }),
        },
      );
      if (!created.message?.id) throw new Error("Сервер не подтвердил сообщение");
      const attached: InternalAttachment[] = [];
      for (const attachment of item.remoteAttachments || []) {
        const result = await api<{ attachment?: InternalAttachment }>(
          "/api/internal-messenger/messages/" + created.message.id + "/attachments",
          { method: "POST", body: JSON.stringify(attachment) },
        );
        if (result.attachment) attached.push(result.attachment);
      }
      const delivered: Message = {
        ...created.message, attachments: attached, is_own: true, direction: "out",
        sender: "manager", delivery_status: "DELIVERED",
      };
      if (internalConversationId === item.conversationId) {
        setInternalMessages(current => {
          const filtered = current.filter(message => message.offline_id !== item.id && String(message.id) !== String(delivered.id));
          return [...filtered, delivered];
        });
      }
      setInternalThreads(current => current.map(thread => thread.id === String(item.conversationId)
        ? { ...thread, lastText: item.body, unread: 0 } : thread));
    } else if (item.kind === "voice") {
      let voiceAssetId = item.voiceAssetId || null;
      let voiceMeta: VoiceAsset | undefined;
      if (!voiceAssetId) {
        if (!item.voiceBlobId) throw new Error("Локальная запись не найдена");
        const stored = await getBlob(item.voiceBlobId);
        if (!stored) throw new Error("Локальная запись не найдена");
        const uploaded = await uploadMediaBlob(item.conversationId, "voice", { blob: stored.blob, name: stored.name || "voice.webm" });
        voiceMeta = uploaded.upload as VoiceAsset | undefined;
        voiceAssetId = String((uploaded.upload as any)?.asset_id || "") || null;
        if (!voiceAssetId) throw new Error("Сервер не подтвердил голосовое");
        item = { ...item, voiceAssetId };
        await updateOutbox(item);
      }
      const created = await api<{ message?: Message }>(
        "/api/internal-messenger/conversations/" + item.conversationId + "/messages",
        {
          method: "POST",
          body: JSON.stringify({
            message_type: "voice",
            voice_asset_id: voiceAssetId,
            reply_to_message_id: item.replyTo,
            client_message_id: item.clientMessageId,
          }),
        },
      );
      if (!created.message?.id) throw new Error("Сервер не подтвердил голосовое сообщение");
      const delivered: Message = {
        ...created.message,
        message_type: "voice",
        voice: voiceMeta || { asset_id: voiceAssetId, playback_url: "/api/internal-messenger/voice/" + voiceAssetId, transcription_status: "queued" },
        is_own: true, direction: "out", sender: "manager", delivery_status: "DELIVERED",
      };
      if (internalConversationId === item.conversationId) {
        setInternalMessages(current => {
          const filtered = current.filter(message => message.offline_id !== item.id && String(message.id) !== String(delivered.id));
          return [...filtered, delivered];
        });
      }
      setInternalThreads(current => current.map(thread => thread.id === String(item.conversationId)
        ? { ...thread, lastText: "Голосовое сообщение", unread: 0 } : thread));
    } else {
      const videoKind: "video" | "video_note" = item.kind === "video_note" ? "video_note" : "video";
      let videoAssetId = item.videoAssetId || null;
      let videoMeta: VideoAsset | undefined;
      if (!videoAssetId) {
        if (!item.videoBlobId) throw new Error("Локальное видео не найдено");
        const stored = await getBlob(item.videoBlobId);
        if (!stored) throw new Error("Локальное видео не найдено");
        const uploaded = await uploadMediaBlob(item.conversationId, videoKind, { blob: stored.blob, name: stored.name || "video.webm" });
        videoMeta = uploaded.upload as VideoAsset | undefined;
        videoAssetId = String((uploaded.upload as any)?.asset_id || "") || null;
        if (!videoAssetId) throw new Error("Сервер не подтвердил видео");
        item = { ...item, videoAssetId };
        await updateOutbox(item);
      }
      const created = await api<{ message?: Message }>(
        "/api/internal-messenger/conversations/" + item.conversationId + "/messages",
        {
          method: "POST",
          body: JSON.stringify({
            message_type: videoKind,
            video_asset_id: videoAssetId,
            reply_to_message_id: item.replyTo,
            client_message_id: item.clientMessageId,
          }),
        },
      );
      if (!created.message?.id) throw new Error("Сервер не подтвердил видеосообщение");
      const delivered: Message = {
        ...created.message,
        message_type: videoKind,
        video: videoMeta || { asset_id: videoAssetId, kind: videoKind, processing_status: "queued" },
        is_own: true, direction: "out", sender: "manager", delivery_status: "DELIVERED",
      };
      if (internalConversationId === item.conversationId) {
        setInternalMessages(current => {
          const filtered = current.filter(message => message.offline_id !== item.id && String(message.id) !== String(delivered.id));
          return [...filtered, delivered];
        });
      }
      setInternalThreads(current => current.map(thread => thread.id === String(item.conversationId)
        ? { ...thread, lastText: videoKind === "video_note" ? "Видеосообщение" : "Видео", unread: 0 } : thread));
    }

    await removeOutbox(item.id);
    for (const blobId of item.cleanupBlobIds || []) await deleteBlob(blobId).catch(() => undefined);
    if (item.voiceBlobId) await deleteBlob(item.voiceBlobId).catch(() => undefined);
    if (item.videoBlobId) await deleteBlob(item.videoBlobId).catch(() => undefined);
    setOutboxCount(current => Math.max(0, current - 1));
    setRealtimeTick(value => value + 1);
  };

  const markOutboxFailure = async (item: MessengerOutboxItem, error: unknown) => {
    let latest = item;
    try {
      latest = (await listOutbox(item.scope)).find(candidate => candidate.id === item.id) || item;
    } catch {
      latest = item;
    }
    const updated = {
      ...latest,
      attempts: (latest.attempts || 0) + 1,
      lastError: error instanceof Error ? error.message : String(error || "send_failed"),
    };
    await updateOutbox(updated).catch(() => undefined);
  };

  const flushOutbox = async () => {
    if (!offlineScope || outboxFlushRef.current || !navigator.onLine) return;
    outboxFlushRef.current = true;
    try {
      const items = await listOutbox(offlineScope);
      setOutboxCount(items.length);
      for (const item of items) {
        if (!navigator.onLine) break;
        try {
          await deliverOutboxItem(item);
        } catch (error) {
          await markOutboxFailure(item, error);
          if (isTransientNetworkError(error)) {
            setRealtimeState("offline");
            break;
          }
        }
      }
      const remaining = await listOutbox(offlineScope);
      setOutboxCount(remaining.length);
    } finally {
      outboxFlushRef.current = false;
    }
  };

  const queueTextOutbox = async (messageBody: string): Promise<MessengerOutboxItem> => {
    if (!internalConversationId || !offlineScope) throw new Error("Чат ещё не готов");
    const id = crypto.randomUUID();
    const localAttachments: OutboxAttachment[] = pendingAttachments
      .filter(attachment => Boolean(attachment.offline_blob_id))
      .map(attachment => ({
        blobId: String(attachment.offline_blob_id),
        name: attachment.file_name,
        type: attachment.content_type,
        size: Number(attachment.size_bytes || 0),
      }));
    const remoteAttachments = pendingAttachments
      .filter(attachment => !attachment.offline_blob_id)
      .map(attachment => ({ ...attachment })) as Array<Record<string, unknown>>;
    const item: MessengerOutboxItem = {
      id,
      scope: offlineScope,
      conversationId: internalConversationId,
      kind: "text",
      body: messageBody,
      replyTo: internalReplyTo ? Number(internalReplyTo) : null,
      clientMessageId: crypto.randomUUID(),
      remoteAttachments,
      localAttachments,
      createdAt: Date.now(),
      attempts: 0,
    };
    await enqueueOutbox(item);
    setOutboxCount(current => current + 1);
    const optimistic: Message = {
      id: "offline:" + id,
      offline_id: id,
      body: messageBody,
      text: messageBody,
      is_own: true,
      direction: "out",
      sender: "manager",
      author_label: "Вы",
      created_at: new Date().toISOString(),
      delivery_status: navigator.onLine ? "Отправляется" : "Ожидает сети",
      optimistic: true,
      attachments: pendingAttachments,
    };
    setInternalMessages(current => [...current, optimistic]);
    setInternalThreads(current => current.map(thread => thread.id === String(internalConversationId)
      ? { ...thread, lastText: messageBody, unread: 0 }
      : thread));
    return item;
  };

  const voiceFileName = (mime: string) => {
    const value = mime.toLowerCase();
    return value.includes("mp4") || value.includes("aac") ? "voice.m4a" : value.includes("ogg") ? "voice.ogg" : "voice.webm";
  };

  const queueVoiceBlob = async (blob: Blob, context: { conversationId: number; scope: string; replyTo: number | null }) => {
    const stored = await putBlob(context.scope, blob, voiceFileName(blob.type));
    const id = crypto.randomUUID();
    const item: MessengerOutboxItem = {
      id,
      scope: context.scope,
      conversationId: context.conversationId,
      kind: "voice",
      body: "",
      replyTo: context.replyTo,
      clientMessageId: crypto.randomUUID(),
      voiceBlobId: stored.id,
      createdAt: Date.now(),
      attempts: 0,
    };
    await enqueueOutbox(item);
    setOutboxCount(current => current + 1);
    if (internalConversationId === context.conversationId) {
      setInternalMessages(current => [...current, {
        id: "offline:" + id,
        offline_id: id,
        message_type: "voice",
        body: "",
        is_own: true,
        direction: "out",
        sender: "manager",
        author_label: "Вы",
        created_at: new Date().toISOString(),
        delivery_status: navigator.onLine ? "Отправляется" : "Ожидает сети",
        optimistic: true,
        voice: { local_blob_id: stored.id, local_name: stored.name, transcription_status: "none" },
      }]);
    }
    setInternalThreads(current => current.map(thread => thread.id === String(context.conversationId)
      ? { ...thread, lastText: "Голосовое сообщение", unread: 0 }
      : thread));
    if (navigator.onLine) {
      try {
        await deliverOutboxItem(item);
        setStatus("Голосовое сообщение отправлено");
      } catch (error) {
        await markOutboxFailure(item, error);
        if (isTransientNetworkError(error)) setRealtimeState("offline");
        setStatus(isTransientNetworkError(error) ? "Голосовое сохранено и будет отправлено после восстановления сети" : "Голосовое пока не отправлено: " + (error as Error).message);
      }
    } else {
      setStatus("Голосовое сохранено и будет отправлено после восстановления сети");
    }
  };

  const clearVoiceCapture = () => {
    if (voiceTimerRef.current !== null) {
      window.clearInterval(voiceTimerRef.current);
      voiceTimerRef.current = null;
    }
    mediaStreamRef.current?.getTracks().forEach(track => track.stop());
    mediaStreamRef.current = null;
    mediaRecorderRef.current = null;
    voiceChunksRef.current = [];
    voiceStartedAtRef.current = 0;
    setVoiceSeconds(0);
  };

  const startVoiceRecording = async () => {
    if (!canReply || !internalConversationId || !offlineScope || voiceState !== "idle") return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setStatus("Запись голосовых не поддерживается на этом устройстве");
      return;
    }
    setVoiceState("permission");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      setMicPermission("granted");
      mediaStreamRef.current = stream;
      const candidates = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"];
      const mimeType = candidates.find(candidate => MediaRecorder.isTypeSupported(candidate)) || "";
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      voiceChunksRef.current = [];
      voiceCancelRef.current = false;
      voiceContextRef.current = {
        conversationId: internalConversationId,
        scope: offlineScope,
        replyTo: internalReplyTo ? Number(internalReplyTo) : null,
      };
      recorder.ondataavailable = event => {
        if (event.data?.size) voiceChunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        void recordMessengerTelemetry(
          "voice_failed",
          { source: "browser", media_kind: "voice", reason: "media_recorder_error" },
          "conversation",
          String(internalConversationId),
        );
        setStatus("Не удалось продолжить запись голосового");
      };
      recorder.onstop = () => {
        const chunks = [...voiceChunksRef.current];
        const cancelled = voiceCancelRef.current;
        const context = voiceContextRef.current;
        const outputType = recorder.mimeType || mimeType || chunks[0]?.type || "audio/webm";
        clearVoiceCapture();
        voiceContextRef.current = null;
        if (cancelled || !chunks.length || !context) {
          setVoiceState("idle");
          return;
        }
        const blob = new Blob(chunks, { type: outputType });
        setVoiceState("processing");
        void queueVoiceBlob(blob, context)
          .catch(error => setStatus("Голосовое не сохранено: " + (error as Error).message))
          .finally(() => setVoiceState("idle"));
      };
      recorder.start(250);
      voiceStartedAtRef.current = Date.now();
      setVoiceSeconds(0);
      setVoiceState("recording");
      void recordMessengerTelemetry(
        "recordings_started",
        { source: "browser", media_kind: "voice" },
        "conversation",
        String(internalConversationId),
      );
      voiceTimerRef.current = window.setInterval(() => {
        const elapsed = Math.floor((Date.now() - voiceStartedAtRef.current) / 1000);
        setVoiceSeconds(elapsed);
        if (elapsed >= 600 && mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
          voiceCancelRef.current = false;
          setVoiceState("processing");
          mediaRecorderRef.current.stop();
        }
      }, 250);
    } catch (error) {
      clearVoiceCapture();
      setVoiceState("idle");
      const permissionError = String((error as { name?: string } | null)?.name || "");
      if (permissionError === "NotAllowedError" || permissionError === "SecurityError" || permissionError === "PermissionDeniedError") {
        setMicPermission("denied");
      } else {
        void readMediaPermission("microphone").then(setMicPermission);
      }
      void recordMessengerTelemetry(
        "voice_failed",
        { source: "browser", media_kind: "voice", reason: permissionError || "media_access_failed" },
        "conversation",
        String(internalConversationId),
      );
      setStatus(mediaAccessError("microphone", error));
    }
  };

  const toggleVoicePause = () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder) return;
    if (recorder.state === "recording") {
      recorder.pause();
      setVoiceState("paused");
    } else if (recorder.state === "paused") {
      recorder.resume();
      setVoiceState("recording");
    }
  };

  const finishVoiceRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    voiceCancelRef.current = false;
    setVoiceState("processing");
    recorder.stop();
  };

  const cancelVoiceRecording = () => {
    const recorder = mediaRecorderRef.current;
    voiceCancelRef.current = true;
    if (recorder && recorder.state !== "inactive") {
      setVoiceState("processing");
      recorder.stop();
    } else {
      clearVoiceCapture();
      setVoiceState("idle");
    }
  };


  const videoFileName = (mime: string) => {
    const value = String(mime || "").toLowerCase();
    return value.includes("mp4") || value.includes("quicktime") ? "video.mp4" : "video.webm";
  };

  const queueVideoBlob = async (
    blob: Blob,
    context: { conversationId: number; scope: string; replyTo: number | null; kind: "video" | "video_note" },
  ) => {
    const stored = await putBlob(context.scope, blob, videoFileName(blob.type));
    const id = crypto.randomUUID();
    const item: MessengerOutboxItem = {
      id,
      scope: context.scope,
      conversationId: context.conversationId,
      kind: context.kind,
      body: "",
      replyTo: context.replyTo,
      clientMessageId: crypto.randomUUID(),
      videoBlobId: stored.id,
      createdAt: Date.now(),
      attempts: 0,
    };
    await enqueueOutbox(item);
    setOutboxCount(current => current + 1);
    if (internalConversationId === context.conversationId) {
      setInternalMessages(current => [...current, {
        id: "offline:" + id,
        offline_id: id,
        message_type: context.kind,
        body: "",
        is_own: true,
        direction: "out",
        sender: "manager",
        author_label: "Вы",
        created_at: new Date().toISOString(),
        delivery_status: navigator.onLine ? "Отправляется" : "Ожидает сети",
        optimistic: true,
        video: {
          kind: context.kind,
          local_blob_id: stored.id,
          local_name: stored.name,
          processing_status: "queued",
        },
      }]);
    }
    setInternalThreads(current => current.map(thread => thread.id === String(context.conversationId)
      ? { ...thread, lastText: context.kind === "video_note" ? "Видеосообщение" : "Видео", unread: 0 }
      : thread));
    if (navigator.onLine) {
      try {
        await deliverOutboxItem(item);
        setStatus(context.kind === "video_note" ? "Видеосообщение отправлено" : "Видео отправлено");
      } catch (error) {
        await markOutboxFailure(item, error);
        if (isTransientNetworkError(error)) setRealtimeState("offline");
        setStatus(isTransientNetworkError(error)
          ? "Видео сохранено и будет отправлено после восстановления сети"
          : "Видео пока не отправлено: " + (error as Error).message);
      }
    } else {
      setStatus("Видео сохранено и будет отправлено после восстановления сети");
    }
  };

  const queueVideoFile = async (file: File, kind: "video" | "video_note" = "video") => {
    if (!canReply || !internalConversationId || !offlineScope) return;
    if (!videoMessagesEnabled || (kind === "video_note" && !videoNotesEnabled)) {
      setStatus("Видеосообщения ещё не активированы");
      return;
    }
    if (!file.type.startsWith("video/")) {
      setStatus("Выберите видеофайл");
      return;
    }
    if (file.size > 150 * 1024 * 1024) {
      setStatus("Видео больше 150 МБ. Выберите файл поменьше.");
      return;
    }
    const context = {
      conversationId: internalConversationId,
      scope: offlineScope,
      replyTo: internalReplyTo ? Number(internalReplyTo) : null,
      kind,
    };
    try {
      await queueVideoBlob(file, context);
      setInternalReplyTo(null);
    } catch (error) {
      setStatus("Видео не сохранено: " + (error as Error).message);
    }
  };

  const stopVideoTracks = () => {
    if (videoTimerRef.current !== null) {
      window.clearInterval(videoTimerRef.current);
      videoTimerRef.current = null;
    }
    videoStreamRef.current?.getTracks().forEach(track => track.stop());
    videoStreamRef.current = null;
    videoRecorderRef.current = null;
    videoChunksRef.current = [];
    videoStartedAtRef.current = 0;
    setVideoSeconds(0);
    if (videoLiveRef.current) videoLiveRef.current.srcObject = null;
  };

  const resetVideoCapture = () => {
    videoRestartRef.current = null;
    stopVideoTracks();
    if (videoPreviewUrl) URL.revokeObjectURL(videoPreviewUrl);
    setVideoPreviewUrl("");
    videoBlobRef.current = null;
    videoContextRef.current = null;
    setVideoCaptureState("idle");
  };

  const startVideoRecording = async (kind: "video" | "video_note" = "video_note", facingOverride?: "user" | "environment") => {
    if (!canReply || !internalConversationId || !offlineScope || videoStateRef.current !== "idle") return;
    if (!videoMessagesEnabled || (kind === "video_note" && !videoNotesEnabled)) {
      setStatus("Видеосообщения ещё не активированы");
      return;
    }
    if (voiceState !== "idle") {
      setStatus("Сначала завершите голосовую запись");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setStatus("Запись видео не поддерживается на этом устройстве");
      return;
    }
    const facing = facingOverride || videoFacing;
    setVideoMode(kind);
    setVideoCaptureState("permission");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: {
          facingMode: { ideal: facing },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
      });
      setMicPermission("granted");
      setCameraPermission("granted");
      videoStreamRef.current = stream;
      const candidates = [
        "video/webm;codecs=vp8,opus",
        "video/webm;codecs=vp9,opus",
        "video/mp4;codecs=h264,aac",
        "video/mp4",
        "video/webm",
      ];
      const mimeType = candidates.find(candidate => MediaRecorder.isTypeSupported(candidate)) || "";
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 1800000 }) : new MediaRecorder(stream);
      videoRecorderRef.current = recorder;
      videoChunksRef.current = [];
      videoContextRef.current = {
        conversationId: internalConversationId,
        scope: offlineScope,
        replyTo: internalReplyTo ? Number(internalReplyTo) : null,
        kind,
      };
      recorder.ondataavailable = event => {
        if (event.data?.size) videoChunksRef.current.push(event.data);
      };
      recorder.onerror = () => setStatus("Не удалось продолжить запись видео");
      recorder.onstop = () => {
        const chunks = [...videoChunksRef.current];
        const context = videoContextRef.current;
        const outputType = recorder.mimeType || mimeType || chunks[0]?.type || "video/webm";
        const capturedSeconds = Math.max(1, Math.floor((Date.now() - videoStartedAtRef.current) / 1000));
        stopVideoTracks();
        if (!context || !chunks.length) {
          videoContextRef.current = null;
          setVideoCaptureState("idle");
          const restart = videoRestartRef.current;
          videoRestartRef.current = null;
          if (restart) {
            window.queueMicrotask(() => void startVideoRecording(restart.kind, restart.facing));
          }
          return;
        }
        const blob = new Blob(chunks, { type: outputType });
        setVideoSeconds(capturedSeconds);
        videoBlobRef.current = blob;
        if (videoPreviewUrl) URL.revokeObjectURL(videoPreviewUrl);
        const url = URL.createObjectURL(blob);
        setVideoPreviewUrl(url);
        setVideoCaptureState("preview");
      };
      recorder.start(250);
      videoStartedAtRef.current = Date.now();
      setVideoSeconds(0);
      setVideoCaptureState("recording");
      void recordMessengerTelemetry(
        "video_started",
        { source: "browser", media_kind: kind },
        "conversation",
        String(internalConversationId),
      );
      window.requestAnimationFrame(() => {
        if (videoLiveRef.current) {
          videoLiveRef.current.srcObject = stream;
          void videoLiveRef.current.play().catch(() => undefined);
        }
      });
      const limit = kind === "video_note" ? 120 : 300;
      videoTimerRef.current = window.setInterval(() => {
        const elapsed = Math.floor((Date.now() - videoStartedAtRef.current) / 1000);
        setVideoSeconds(elapsed);
        if (elapsed >= limit && videoRecorderRef.current && videoRecorderRef.current.state !== "inactive") {
          videoRecorderRef.current.stop();
        }
      }, 250);
    } catch (error) {
      stopVideoTracks();
      videoContextRef.current = null;
      setVideoCaptureState("idle");
      void Promise.all([readMediaPermission("microphone"), readMediaPermission("camera")]).then(([microphone, camera]) => {
        setMicPermission(microphone);
        setCameraPermission(camera);
      });
      setStatus(mediaAccessError("camera_and_microphone", error));
    }
  };

  const finishVideoRecording = () => {
    const recorder = videoRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    recorder.stop();
  };

  const cancelVideoRecording = () => {
    const recorder = videoRecorderRef.current;
    videoContextRef.current = null;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    } else {
      resetVideoCapture();
    }
  };

  const retakeVideo = () => {
    const context = videoContextRef.current;
    const kind = context?.kind || videoMode;
    resetVideoCapture();
    window.setTimeout(() => void startVideoRecording(kind), 0);
  };

  const switchVideoCamera = () => {
    const context = videoContextRef.current;
    const kind = context?.kind || videoMode;
    const nextFacing: "user" | "environment" = videoFacing === "user" ? "environment" : "user";
    setVideoFacing(nextFacing);
    if (videoState === "recording") {
      videoRestartRef.current = { kind, facing: nextFacing };
      videoContextRef.current = null;
      const recorder = videoRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        recorder.stop();
      } else {
        stopVideoTracks();
        setVideoCaptureState("idle");
        const restart = videoRestartRef.current;
        videoRestartRef.current = null;
        if (restart) window.queueMicrotask(() => void startVideoRecording(restart.kind, restart.facing));
      }
    }
  };

  const sendVideoPreview = async () => {
    const blob = videoBlobRef.current;
    const context = videoContextRef.current;
    if (!blob || !context) {
      resetVideoCapture();
      return;
    }
    setVideoCaptureState("processing");
    try {
      await queueVideoBlob(blob, context);
      setInternalReplyTo(null);
      if (videoPreviewUrl) URL.revokeObjectURL(videoPreviewUrl);
      setVideoPreviewUrl("");
      videoBlobRef.current = null;
      videoContextRef.current = null;
      setVideoCaptureState("idle");
    } catch (error) {
      setVideoCaptureState("preview");
      setStatus("Видео не сохранено: " + (error as Error).message);
    }
  };


  const callDurationLabel = (seconds: number) => {
    const value = Math.max(0, Math.floor(seconds));
    return Math.floor(value / 60) + ":" + String(value % 60).padStart(2, "0");
  };

  const updateCallState = async (
    callId: string,
    action: "accept" | "decline" | "connecting" | "connected" | "reconnecting" | "end",
  ): Promise<MessengerCall | null> => {
    try {
      const result = await api<{ call?: MessengerCall }>(
        `/api/internal-messenger/calls/${callId}/transition`,
        {
          method: "POST",
          body: JSON.stringify({
            action,
            session_id: action === "accept" ? crypto.randomUUID() : null,
          }),
        },
      );
      if (result.call) setActiveCall(result.call);
      return result.call || null;
    } catch (error) {
      if (action !== "end") setCallStatus((error as Error).message);
      return null;
    }
  };

  const sendCallSignal = async (
    callId: string,
    eventType: "offer" | "answer" | "ice" | "renegotiate" | "ice_restart" | "mute" | "camera" | "heartbeat",
    payload: Record<string, unknown>,
  ) => {
    await api(`/api/internal-messenger/calls/${callId}/signal`, {
      method: "POST",
      body: JSON.stringify({ event_type: eventType, payload }),
    });
  };

  const prepareGroupLocalStream = async (call: MessengerCall): Promise<MediaStream> => {
    if (localCallStreamRef.current) return localCallStreamRef.current;
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Звонки не поддерживаются на этом устройстве");
    const audioConstraints: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    let stream: MediaStream;
    if (call.call_type === "video") {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: audioConstraints,
          video: { facingMode: { ideal: callFacing }, width: { ideal: 1280 }, height: { ideal: 720 } },
        });
        setMicPermission("granted");
        setCameraPermission("granted");
      } catch (videoError) {
        const videoErrorName = String((videoError as { name?: string } | null)?.name || "");
        if (["NotAllowedError","SecurityError","PermissionDeniedError"].includes(videoErrorName)) setCameraPermission("denied");
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints, video: false });
          setMicPermission("granted");
          setCallStatus("Камера недоступна — групповой звонок продолжается со звуком");
        } catch (audioError) {
          const audioErrorName = String((audioError as { name?: string } | null)?.name || "");
          if (["NotAllowedError","SecurityError","PermissionDeniedError"].includes(audioErrorName)) setMicPermission("denied");
          throw new Error(mediaAccessError("microphone", audioError));
        }
      }
    } else {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints, video: false });
        setMicPermission("granted");
      } catch (audioError) {
        const audioErrorName = String((audioError as { name?: string } | null)?.name || "");
        if (["NotAllowedError","SecurityError","PermissionDeniedError"].includes(audioErrorName)) setMicPermission("denied");
        throw new Error(mediaAccessError("microphone", audioError));
      }
    }
    localCallStreamRef.current = stream;
    setCallMuted(false);
    setCallCameraEnabled(stream.getVideoTracks().length > 0);
    for (const videoTrack of stream.getVideoTracks()) {
      videoTrack.onended = () => {
        setCallCameraEnabled(false);
        setCallStatus("Камера отключена — звонок продолжается со звуком");
        void sendCallSignal(call.call_id, "camera", { enabled: false }).catch(() => undefined);
      };
    }
    return stream;
  };

  const flushGroupPendingIce = async (remoteUserId: number, peer: RTCPeerConnection) => {
    if (!peer.remoteDescription) return;
    const queued = groupPendingIceRef.current.get(remoteUserId) || [];
    if (!queued.length) return;
    groupPendingIceRef.current.set(remoteUserId, []);
    for (const candidate of queued) {
      try { await peer.addIceCandidate(candidate); }
      catch {
        const next = groupPendingIceRef.current.get(remoteUserId) || [];
        next.push(candidate);
        groupPendingIceRef.current.set(remoteUserId, next);
      }
    }
  };

  const prepareGroupPeer = async (call: MessengerCall, remoteUserId: number): Promise<RTCPeerConnection> => {
    const existing = groupPeerRefs.current.get(remoteUserId);
    if (existing) return existing;
    const stream = await prepareGroupLocalStream(call);
    const ice = await api<{ iceServers?: RTCIceServer[] }>("/api/internal-messenger/calls/ice-config");
    const peer = new RTCPeerConnection({ iceServers: ice.iceServers || [] });
    groupPeerRefs.current.set(remoteUserId, peer);
    for (const track of stream.getTracks()) peer.addTrack(track, stream);
    peer.ontrack = event => {
      const remote = event.streams?.[0] || new MediaStream([event.track]);
      setGroupRemoteStreams(current => ({ ...current, [remoteUserId]: remote }));
    };
    peer.onicecandidate = event => {
      if (!event.candidate) return;
      void sendCallSignal(call.call_id, "ice", {
        target_user_id: remoteUserId,
        candidate: event.candidate.toJSON(),
      }).catch(() => undefined);
    };
    peer.onconnectionstatechange = () => {
      const state = peer.connectionState;
      if (state === "connected") {
        setCallConnectedAt(current => current || Date.now());
        setCallStatus("На связи");
        void updateCallState(call.call_id, "connected");
        void reportTurnRelayUsage(peer, call);
      } else if (state === "disconnected") {
        setCallStatus("Восстанавливаем групповой звонок…");
        void updateCallState(call.call_id, "reconnecting");
      } else if (state === "failed") {
        setCallStatus("Переподключаем участника…");
        void updateCallState(call.call_id, "reconnecting");
        if (Number(currentUserId || 0) < remoteUserId) {
          try {
            peer.restartIce();
            void (async () => {
              const offer = await peer.createOffer({ iceRestart: true });
              await peer.setLocalDescription(offer);
              await sendCallSignal(call.call_id, "ice_restart", {
                target_user_id: remoteUserId,
                description: peer.localDescription,
              });
            })().catch(() => undefined);
          } catch {
            // Polling + participant state remains authoritative.
          }
        }
      }
    };
    return peer;
  };

  const syncGroupPeers = async (call: MessengerCall) => {
    const result = await api<{ items?: GroupCallParticipant[] }>(`/api/internal-messenger/calls/${call.call_id}/participants`);
    const participants = result.items || [];
    setGroupCallParticipants(participants);
    const me = Number(currentUserId || 0);
    const activeRemoteIds = participants
      .filter(item => item.user_id !== me && ["accepted","connected"].includes(String(item.state)))
      .map(item => Number(item.user_id));

    for (const [remoteUserId, peer] of groupPeerRefs.current.entries()) {
      if (activeRemoteIds.includes(remoteUserId)) continue;
      peer.ontrack = null;
      peer.onicecandidate = null;
      peer.onconnectionstatechange = null;
      peer.close();
      groupPeerRefs.current.delete(remoteUserId);
      groupOfferSentRef.current.delete(remoteUserId);
      groupPendingIceRef.current.delete(remoteUserId);
      setGroupRemoteStreams(current => {
        const next = { ...current };
        delete next[remoteUserId];
        return next;
      });
    }

    for (const remoteUserId of activeRemoteIds) {
      if (!me || me > remoteUserId || groupOfferSentRef.current.has(remoteUserId)) continue;
      const peer = await prepareGroupPeer(call, remoteUserId);
      groupOfferSentRef.current.add(remoteUserId);
      try {
        const offer = await peer.createOffer();
        await peer.setLocalDescription(offer);
        await sendCallSignal(call.call_id, "offer", {
          target_user_id: remoteUserId,
          description: peer.localDescription,
        });
        await updateCallState(call.call_id, "connecting");
      } catch {
        groupOfferSentRef.current.delete(remoteUserId);
      }
    }
  };

  const attachCallStreams = () => {
    const localNode = localCallVideoRef.current;
    if (localNode && localCallStreamRef.current && localNode.srcObject !== localCallStreamRef.current) {
      localNode.srcObject = localCallStreamRef.current;
      localNode.muted = true;
      void localNode.play().catch(() => undefined);
    }
    const remoteNode = remoteCallVideoRef.current;
    if (remoteNode && remoteCallStreamRef.current && remoteNode.srcObject !== remoteCallStreamRef.current) {
      remoteNode.srcObject = remoteCallStreamRef.current;
      void remoteNode.play().catch(() => undefined);
    }
  };

  const reportTurnRelayUsage = async (peer: RTCPeerConnection, call: MessengerCall) => {
    if (turnRelayReportedRef.current) return;
    try {
      const stats = await peer.getStats();
      let selectedPair: any = null;
      stats.forEach(report => {
        const row = report as any;
        if (row.type === "transport" && row.selectedCandidatePairId) {
          selectedPair = stats.get(row.selectedCandidatePairId) || selectedPair;
        }
      });
      if (!selectedPair) {
        stats.forEach(report => {
          const row = report as any;
          if (
            !selectedPair
            && row.type === "candidate-pair"
            && row.state === "succeeded"
            && (row.nominated === true || row.selected === true)
          ) {
            selectedPair = row;
          }
        });
      }
      if (!selectedPair) return;
      const local: any = selectedPair.localCandidateId ? stats.get(selectedPair.localCandidateId) : null;
      const remote: any = selectedPair.remoteCandidateId ? stats.get(selectedPair.remoteCandidateId) : null;
      const relay = local?.candidateType === "relay" || remote?.candidateType === "relay";
      if (!relay) return;
      turnRelayReportedRef.current = true;
      await recordMessengerTelemetry(
        "turn_relay_used",
        {
          source: "webrtc_stats",
          relay: true,
          transport: String(local?.protocol || remote?.protocol || "").slice(0, 24),
          protocol: String(selectedPair?.protocol || "").slice(0, 24),
          call_type: call.call_type,
        },
        "call",
        call.call_id,
      );
    } catch {
      // Connection telemetry never changes call behavior.
    }
  };

  const prepareCallPeer = async (call: MessengerCall): Promise<RTCPeerConnection> => {
    if (peerRef.current) {
      attachCallStreams();
      return peerRef.current;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === "undefined") {
      throw new Error("Звонки не поддерживаются на этом устройстве");
    }
    const ice = await api<{ iceServers?: RTCIceServer[] }>("/api/internal-messenger/calls/ice-config");
    const wantsVideo = call.call_type === "video";
    const audioConstraints: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    let stream: MediaStream;
    let videoAvailable = false;
    if (wantsVideo) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: audioConstraints,
          video: {
            facingMode: { ideal: callFacing },
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
        });
        setMicPermission("granted");
        setCameraPermission("granted");
        videoAvailable = stream.getVideoTracks().length > 0;
      } catch (videoError) {
        const videoErrorName = String((videoError as { name?: string } | null)?.name || "");
        if (videoErrorName === "NotAllowedError" || videoErrorName === "SecurityError" || videoErrorName === "PermissionDeniedError") {
          setCameraPermission("denied");
        } else {
          void readMediaPermission("camera").then(setCameraPermission);
        }
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints, video: false });
          setMicPermission("granted");
          setCallStatus("Камера недоступна — звонок продолжается со звуком");
        } catch (audioError) {
          const audioErrorName = String((audioError as { name?: string } | null)?.name || "");
          if (audioErrorName === "NotAllowedError" || audioErrorName === "SecurityError" || audioErrorName === "PermissionDeniedError") {
            setMicPermission("denied");
          } else {
            void readMediaPermission("microphone").then(setMicPermission);
          }
          throw new Error(mediaAccessError("microphone", audioError));
        }
      }
    } else {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints, video: false });
        setMicPermission("granted");
      } catch (audioError) {
        const audioErrorName = String((audioError as { name?: string } | null)?.name || "");
        if (audioErrorName === "NotAllowedError" || audioErrorName === "SecurityError" || audioErrorName === "PermissionDeniedError") {
          setMicPermission("denied");
        } else {
          void readMediaPermission("microphone").then(setMicPermission);
        }
        throw new Error(mediaAccessError("microphone", audioError));
      }
    }
    localCallStreamRef.current = stream;
    setCallMuted(false);
    setCallCameraEnabled(videoAvailable);
    const peer = new RTCPeerConnection({ iceServers: ice.iceServers || [] });
    peerRef.current = peer;
    for (const track of stream.getTracks()) peer.addTrack(track, stream);
    for (const videoTrack of stream.getVideoTracks()) {
      videoTrack.onended = () => {
        setCallCameraEnabled(false);
        setCallStatus("Камера отключена — звонок продолжается со звуком");
        void sendCallSignal(call.call_id, "camera", { enabled: false }).catch(() => undefined);
      };
    }
    peer.ontrack = event => {
      const remote = event.streams?.[0] || new MediaStream([event.track]);
      remoteCallStreamRef.current = remote;
      const node = remoteCallVideoRef.current;
      if (node) {
        node.srcObject = remote;
        void node.play().catch(() => undefined);
      }
    };
    peer.onicecandidate = event => {
      if (!event.candidate) return;
      void sendCallSignal(call.call_id, "ice", { candidate: event.candidate.toJSON() }).catch(() => undefined);
    };
    peer.onconnectionstatechange = () => {
      const state = peer.connectionState;
      if (state === "connected") {
        setCallConnectedAt(current => current || Date.now());
        setCallStatus("На связи");
        void updateCallState(call.call_id, "connected");
        void reportTurnRelayUsage(peer, call);
      } else if (state === "disconnected") {
        setCallStatus("Восстанавливаем соединение…");
        void updateCallState(call.call_id, "reconnecting");
      } else if (state === "failed") {
        setCallStatus("Переподключаем звонок…");
        void updateCallState(call.call_id, "reconnecting");
        if (Number(currentUserId || 0) === Number(call.caller_user_id)) {
          try {
            peer.restartIce();
            void (async () => {
              const offer = await peer.createOffer({ iceRestart: true });
              await peer.setLocalDescription(offer);
              await sendCallSignal(call.call_id, "ice_restart", { description: peer.localDescription });
            })().catch(() => undefined);
          } catch {
            // State polling will close the call if recovery fails.
          }
        }
      }
    };
    window.requestAnimationFrame(attachCallStreams);
    return peer;
  };

  const clearCallMedia = () => {
    if (callSignalTimerRef.current !== null) {
      window.clearInterval(callSignalTimerRef.current);
      callSignalTimerRef.current = null;
    }
    if (callStateTimerRef.current !== null) {
      window.clearInterval(callStateTimerRef.current);
      callStateTimerRef.current = null;
    }
    const peer = peerRef.current;
    peerRef.current = null;
    if (peer) {
      peer.ontrack = null;
      peer.onicecandidate = null;
      peer.onconnectionstatechange = null;
      peer.close();
    }
    for (const groupPeer of groupPeerRefs.current.values()) {
      groupPeer.ontrack = null;
      groupPeer.onicecandidate = null;
      groupPeer.onconnectionstatechange = null;
      groupPeer.close();
    }
    groupPeerRefs.current.clear();
    groupPendingIceRef.current.clear();
    groupOfferSentRef.current.clear();
    setGroupCallParticipants([]);
    setGroupRemoteStreams({});
    localCallStreamRef.current?.getTracks().forEach(track => track.stop());
    localCallStreamRef.current = null;
    remoteCallStreamRef.current = null;
    if (localCallVideoRef.current) localCallVideoRef.current.srcObject = null;
    if (remoteCallVideoRef.current) remoteCallVideoRef.current.srcObject = null;
    callSignalSeqRef.current = 0;
    callOfferSentRef.current = false;
    turnRelayReportedRef.current = false;
    pendingIceRef.current = [];
    setCallMuted(false);
    setCallCameraEnabled(false);
    setCallConnectedAt(null);
    setCallElapsed(0);
  };

  const closeCallUi = () => {
    pendingClientCallIdRef.current = null;
    clearCallMedia();
    setActiveCall(null);
    setIncomingCall(null);
    setCallStatus("");
    setCallMinimized(false);
    setRealtimeTick(value => value + 1);
  };

  const flushPendingIce = async (peer: RTCPeerConnection) => {
    if (!peer.remoteDescription || !pendingIceRef.current.length) return;
    const queued = pendingIceRef.current.splice(0);
    for (const candidate of queued) {
      try {
        await peer.addIceCandidate(candidate);
      } catch {
        // A later ICE restart can recover candidates rejected by the browser.
      }
    }
  };

  const processCallSignals = async (call: MessengerCall) => {
    const result = await api<{ items?: CallSignalEvent[]; next_after_seq?: number; call?: MessengerCall }>(
      "/api/internal-messenger/calls/" + call.call_id + "/events?after_seq=" + String(callSignalSeqRef.current) + "&limit=100",
    );
    if (result.call) {
      setActiveCall(current => current?.call_id === result.call?.call_id ? result.call || current : current);
      if (["ended", "missed", "declined", "busy", "failed"].includes(String(result.call.state))) {
        window.setTimeout(closeCallUi, 250);
        return;
      }
    }

    if (call.call_mode === "group") {
      await syncGroupPeers(result.call || call);
      for (const event of result.items || []) {
        callSignalSeqRef.current = Math.max(callSignalSeqRef.current, Number(event.seq || 0));
        if (Number(event.actor_user_id) === Number(currentUserId || 0)) continue;
        const targetUserId = Number((event.payload as any)?.target_user_id || 0);
        if (targetUserId && targetUserId !== Number(currentUserId || 0)) continue;
        const remoteUserId = Number(event.actor_user_id);
        if (!remoteUserId) continue;
        const peer = await prepareGroupPeer(call, remoteUserId);
        if ((event.event_type === "offer" || event.event_type === "renegotiate" || event.event_type === "ice_restart") && event.payload?.description) {
          await peer.setRemoteDescription(event.payload.description as RTCSessionDescriptionInit);
          await flushGroupPendingIce(remoteUserId, peer);
          const answer = await peer.createAnswer();
          await peer.setLocalDescription(answer);
          await sendCallSignal(call.call_id, "answer", {
            target_user_id: remoteUserId,
            description: peer.localDescription,
          });
          await updateCallState(call.call_id, "connecting");
        } else if (event.event_type === "answer" && event.payload?.description) {
          if (!peer.currentRemoteDescription) {
            await peer.setRemoteDescription(event.payload.description as RTCSessionDescriptionInit);
            await flushGroupPendingIce(remoteUserId, peer);
          }
        } else if (event.event_type === "ice" && event.payload?.candidate) {
          const candidate = event.payload.candidate as RTCIceCandidateInit;
          if (!peer.remoteDescription) {
            const pending = groupPendingIceRef.current.get(remoteUserId) || [];
            pending.push(candidate);
            groupPendingIceRef.current.set(remoteUserId, pending);
          } else {
            try { await peer.addIceCandidate(candidate); }
            catch {
              const pending = groupPendingIceRef.current.get(remoteUserId) || [];
              pending.push(candidate);
              groupPendingIceRef.current.set(remoteUserId, pending);
            }
          }
        } else if (event.event_type === "mute" || event.event_type === "camera") {
          // Presence only; remote tracks remain authoritative.
        }
      }
      if (result.next_after_seq) {
        callSignalSeqRef.current = Math.max(callSignalSeqRef.current, Number(result.next_after_seq));
      }
      return;
    }

    for (const event of result.items || []) {
      callSignalSeqRef.current = Math.max(callSignalSeqRef.current, Number(event.seq || 0));
      if (Number(event.actor_user_id) === Number(currentUserId || 0)) continue;
      const peer = await prepareCallPeer(call);
      if ((event.event_type === "offer" || event.event_type === "renegotiate" || event.event_type === "ice_restart") && event.payload?.description) {
        await peer.setRemoteDescription(event.payload.description as RTCSessionDescriptionInit);
        await flushPendingIce(peer);
        const answer = await peer.createAnswer();
        await peer.setLocalDescription(answer);
        await sendCallSignal(call.call_id, "answer", { description: peer.localDescription });
        await updateCallState(call.call_id, "connecting");
      } else if (event.event_type === "answer" && event.payload?.description) {
        if (!peer.currentRemoteDescription) {
          await peer.setRemoteDescription(event.payload.description as RTCSessionDescriptionInit);
          await flushPendingIce(peer);
        }
      } else if (event.event_type === "ice" && event.payload?.candidate) {
        const candidate = event.payload.candidate as RTCIceCandidateInit;
        if (!peer.remoteDescription) {
          pendingIceRef.current.push(candidate);
        } else {
          try {
            await peer.addIceCandidate(candidate);
          } catch {
            pendingIceRef.current.push(candidate);
          }
        }
      } else if (event.event_type === "mute") {
        // Remote mute state is informational and does not alter our tracks.
      }
    }
    if (result.next_after_seq) callSignalSeqRef.current = Math.max(callSignalSeqRef.current, Number(result.next_after_seq));
  };

  const beginCallerOffer = async (call: MessengerCall) => {
    if (callOfferSentRef.current) return;
    callOfferSentRef.current = true;
    try {
      const peer = await prepareCallPeer(call);
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      await sendCallSignal(call.call_id, "offer", { description: peer.localDescription });
      await updateCallState(call.call_id, "connecting");
      setCallStatus("Соединяем…");
    } catch (error) {
      callOfferSentRef.current = false;
      if (isTransientNetworkError(error)) {
        setCallStatus("Восстанавливаем соединение…");
        return;
      }
      setStatus("Не удалось подключить микрофон или камеру: " + (error as Error).message);
      try {
        await updateCallState(call.call_id, "end");
      } finally {
        closeCallUi();
      }
    }
  };

  const refreshActiveCall = async (call: MessengerCall) => {
    try {
      const result = await api<{ call?: MessengerCall }>(`/api/internal-messenger/calls/${call.call_id}`);
      const next = result.call;
      if (!next) return;
      setActiveCall(next);
      if (["ended", "missed", "declined", "busy", "failed"].includes(String(next.state))) {
        if (next.state === "missed") setStatus("На звонок не ответили");
        if (next.state === "declined") setStatus("Звонок отклонён");
        closeCallUi();
        return;
      }
      const iAmCaller = Number(currentUserId || 0) === Number(next.caller_user_id);
      if (next.call_mode === "group") {
        await syncGroupPeers(next);
        if (["accepted", "connecting", "connected", "reconnecting"].includes(String(next.state))) {
          await processCallSignals(next);
        }
        return;
      }
      if (iAmCaller && ["accepted", "connecting", "reconnecting"].includes(String(next.state)) && !callOfferSentRef.current) {
        await beginCallerOffer(next);
      }
      if (["accepted", "connecting", "connected", "reconnecting"].includes(String(next.state))) {
        await processCallSignals(next);
      }
    } catch (error) {
      if (isTransientNetworkError(error)) setCallStatus("Восстанавливаем соединение…");
    }
  };

  const startMessengerCall = async (callType: "audio" | "video") => {
    if (!canReply || !internalConversationId) return;
    if (!internalSelected || internalSelected.isGeneral || !["Личный","Группа"].includes(internalSelected.kind)) return;
    if (!audioCallsEnabled || (callType === "video" && !videoCallsEnabled)) {
      setStatus("Звонки ещё не активированы");
      return;
    }
    if (!navigator.onLine) {
      setStatus("Для звонка нужно подключение к интернету");
      return;
    }
    if (activeCall) {
      setStatus("Сначала завершите текущий звонок");
      return;
    }
    try {
      setCallStatus("Вызываем…");
      callSignalSeqRef.current = 0;
      callOfferSentRef.current = false;
      const clientCallId = pendingClientCallIdRef.current || crypto.randomUUID();
      pendingClientCallIdRef.current = clientCallId;
      const result = await api<{ call?: MessengerCall }>(
        `/api/internal-messenger/conversations/${internalConversationId}/calls`,
        {
          method: "POST",
          body: JSON.stringify({ client_call_id: clientCallId, call_type: callType }),
        },
      );
      if (!result.call) throw new Error("Сервер не подтвердил звонок");
      pendingClientCallIdRef.current = null;
      setActiveCall(result.call);
      setIncomingCall(null);
      setCallStatus("Вызов…");
    } catch (error) {
      setCallStatus("");
      setStatus("Звонок не начат: " + (error as Error).message);
    }
  };

  const acceptIncomingCall = async () => {
    const call = incomingCall;
    if (!call) return;
    try {
      setCallStatus("Подключаем…");
      setActiveCall(call);
      setIncomingCall(null);
      incomingCallRef.current = null;
      callSignalSeqRef.current = 0;
      callOfferSentRef.current = false;
      if (call.call_mode === "group") await prepareGroupLocalStream(call);
      else await prepareCallPeer(call);
      const result = await api<{ call?: MessengerCall }>(
        `/api/internal-messenger/calls/${call.call_id}/transition`,
        {
          method: "POST",
          body: JSON.stringify({ action: "accept", session_id: crypto.randomUUID() }),
        },
      );
      if (!result.call) throw new Error("Сервер не подтвердил принятие звонка");
      setActiveCall(result.call);
      if (result.call.call_mode === "group") await syncGroupPeers(result.call);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || "");
      if (/звонок уже изменился|already changed/i.test(message)) {
        clearCallMedia();
        setActiveCall(null);
        setIncomingCall(null);
        incomingCallRef.current = null;
        setCallStatus("");
        setStatus("Звонок принят на другом устройстве");
        setRealtimeTick(value => value + 1);
        return;
      }
      if (isTransientNetworkError(error)) {
        clearCallMedia();
        setActiveCall(null);
        setIncomingCall(call);
        incomingCallRef.current = call;
        setCallStatus("");
        setStatus("Связь прервалась при принятии звонка. Проверьте сеть и попробуйте ещё раз.");
        return;
      }
      setStatus("Не удалось принять звонок: " + message);
      await updateCallState(call.call_id, "decline");
      closeCallUi();
    }
  };

  const declineIncomingCall = async () => {
    const call = incomingCall;
    if (!call) return;
    setIncomingCall(null);
    try {
      await updateCallState(call.call_id, "decline");
    } finally {
      setActiveCall(null);
      setCallStatus("");
      setRealtimeTick(value => value + 1);
    }
  };

  const endActiveCall = async () => {
    const call = activeCall;
    if (!call) return;
    try {
      await updateCallState(call.call_id, "end");
    } finally {
      closeCallUi();
    }
  };

  const toggleCallMute = () => {
    const stream = localCallStreamRef.current;
    if (!stream) return;
    const nextMuted = !callMuted;
    for (const track of stream.getAudioTracks()) track.enabled = !nextMuted;
    setCallMuted(nextMuted);
    if (activeCall) void sendCallSignal(activeCall.call_id, "mute", { muted: nextMuted }).catch(() => undefined);
  };

  const toggleCallCamera = () => {
    const stream = localCallStreamRef.current;
    if (!stream || activeCall?.call_type !== "video") return;
    const tracks = stream.getVideoTracks();
    if (!tracks.length) return;
    const nextEnabled = !callCameraEnabled;
    for (const track of tracks) track.enabled = nextEnabled;
    setCallCameraEnabled(nextEnabled);
    if (activeCall) void sendCallSignal(activeCall.call_id, "camera", { enabled: nextEnabled }).catch(() => undefined);
  };

  const switchCallCamera = async () => {
    const call = activeCall;
    const peer = peerRef.current;
    const stream = localCallStreamRef.current;
    if (!call || call.call_type !== "video" || !stream) return;
    if (call.call_mode !== "group" && !peer) return;
    const nextFacing: "user" | "environment" = callFacing === "user" ? "environment" : "user";
    try {
      const replacement = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: nextFacing }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      const nextTrack = replacement.getVideoTracks()[0];
      if (!nextTrack) throw new DOMException("Камера не найдена", "NotFoundError");
      setCameraPermission("granted");
      nextTrack.onended = () => {
        setCallCameraEnabled(false);
        setCallStatus("Камера отключена — звонок продолжается со звуком");
        void sendCallSignal(call.call_id, "camera", { enabled: false }).catch(() => undefined);
      };
      if (call.call_mode === "group") {
        const senders = Array.from(groupPeerRefs.current.values())
          .map(value => value.getSenders().find(sender => sender.track?.kind === "video"))
          .filter((value): value is RTCRtpSender => Boolean(value));
        if (!senders.length) throw new Error("Видеоканалы группы ещё не готовы");
        await Promise.all(senders.map(sender => sender.replaceTrack(nextTrack)));
      } else {
        if (!peer) throw new Error("Соединение звонка ещё не готово");
        const sender = peer.getSenders().find(value => value.track?.kind === "video");
        if (!sender) throw new Error("Видеоканал не готов");
        await sender.replaceTrack(nextTrack);
      }
      for (const old of stream.getVideoTracks()) {
        stream.removeTrack(old);
        old.stop();
      }
      stream.addTrack(nextTrack);
      setCallFacing(nextFacing);
      setCallCameraEnabled(true);
      attachCallStreams();
    } catch (error) {
      void readMediaPermission("camera").then(setCameraPermission);
      setStatus(mediaAccessError("camera", error));
    }
  };

  useEffect(() => {
    if (!accountId || !currentUserId || activeCall || !audioCallsEnabled) return;
    let cancelled = false;
    const checkIncoming = async () => {
      try {
        const result = await api<{ items?: MessengerCall[] }>(
          `/api/internal-messenger/calls/incoming?account_id=${encodeURIComponent(accountId)}`,
        );
        if (cancelled) return;
        const incoming = (result.items || [])[0] || null;
        if (incoming) {
          incomingCallRef.current = incoming;
          setIncomingCall(incoming);
          return;
        }
        const previous = incomingCallRef.current;
        incomingCallRef.current = null;
        setIncomingCall(null);
        if (previous?.state === "ringing") {
          try {
            const latest = await api<{ call?: MessengerCall }>(`/api/internal-messenger/calls/${previous.call_id}`);
            if (cancelled) return;
            const state = String(latest.call?.state || "");
            if (["accepted", "connecting", "connected", "reconnecting"].includes(state)) {
              setStatus("Звонок принят на другом устройстве");
              setToast({ text: "Звонок принят на другом устройстве", kind: "info" });
            } else if (state === "missed") {
              setStatus("Пропущенный звонок");
              setToast({ text: "Пропущенный звонок", kind: "info" });
            } else if (state === "declined") {
              setStatus("Звонок отклонён");
              setToast({ text: "Звонок отклонён", kind: "info" });
            }
          } catch {
            // The incoming list is authoritative; a later reconciliation can refresh the history.
          }
        }
      } catch {
        // Calls may still be feature-gated during staged activation.
      }
    };
    void checkIncoming();
    const timer = window.setInterval(() => void checkIncoming(), 2200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [accountId, currentUserId, activeCall?.call_id, realtimeTick, audioCallsEnabled]);

  useEffect(() => {
    const call = activeCall;
    if (!call) return;
    void refreshActiveCall(call);
    callStateTimerRef.current = window.setInterval(() => void refreshActiveCall(call), 900);
    return () => {
      if (callStateTimerRef.current !== null) {
        window.clearInterval(callStateTimerRef.current);
        callStateTimerRef.current = null;
      }
    };
  }, [activeCall?.call_id, currentUserId]);

  useEffect(() => {
    if (!activeCall) return;
    const call = activeCall;
    const timer = window.setInterval(() => {
      if (callConnectedAt) setCallElapsed(Math.max(0, Math.floor((Date.now() - callConnectedAt) / 1000)));
    }, 1000);
    const heartbeat = () => {
      void sendCallSignal(call.call_id, "heartbeat", {
        state: peerRef.current?.connectionState || call.state,
      }).catch(() => undefined);
    };
    heartbeat();
    const heartbeatTimer = window.setInterval(heartbeat, 10_000);
    const onOffline = () => {
      setCallStatus("Нет сети. Восстанавливаем звонок…");
      void updateCallState(call.call_id, "reconnecting");
    };
    const onOnline = () => {
      setCallStatus("Переподключаем звонок…");
      void refreshActiveCall(call);
    };
    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);
    return () => {
      window.clearInterval(timer);
      window.clearInterval(heartbeatTimer);
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
    };
  }, [activeCall?.call_id, callConnectedAt]);


  useEffect(() => {
    if (!incomingCall) return;
    let context: AudioContext | null = null;
    let timer: number | null = null;
    const ring = () => {
      try {
        const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext;
        if (!AudioContextCtor) return;
        if (!context) context = new AudioContextCtor();
        void context.resume().catch(() => undefined);
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.frequency.value = 720;
        gain.gain.value = 0.035;
        oscillator.connect(gain);
        gain.connect(context.destination);
        oscillator.start();
        oscillator.stop(context.currentTime + 0.32);
      } catch {
        // Browsers can block autoplay audio; Push/vibration remains available.
      }
    };
    ring();
    timer = window.setInterval(ring, 1800);
    if ("vibrate" in navigator) {
      try { navigator.vibrate([300, 180, 300]); } catch {}
    }
    return () => {
      if (timer !== null) window.clearInterval(timer);
      if ("vibrate" in navigator) {
        try { navigator.vibrate(0); } catch {}
      }
      if (context) void context.close().catch(() => undefined);
    };
  }, [incomingCall?.call_id]);

  useEffect(() => () => {
    clearCallMedia();
    stopVideoTracks();
    mediaStreamRef.current?.getTracks().forEach(track => track.stop());
  }, []);

  const toggleInternalReaction = async (message: Message, reaction = "👍") => {
    if (!message.id || !currentUserId) return;
    const mine = (message.reactions || []).some(item => item.user_id === currentUserId && item.reaction === reaction);
    try {
      const path = mine ? "/api/internal-messenger/messages/" + message.id + "/reactions/" + encodeURIComponent(reaction) : "/api/internal-messenger/messages/" + message.id + "/reactions";
      await api(path, { method: mine ? "DELETE" : "POST", ...(mine ? {} : { body: JSON.stringify({ reaction }) }) });
      setInternalMessages(current => current.map(item => String(item.id) === String(message.id)
        ? { ...item, reactions: mine ? (item.reactions || []).filter(value => !(value.user_id === currentUserId && value.reaction === reaction)) : [...(item.reactions || []), { user_id: currentUserId, reaction }] }
        : item));
    } catch (error) {
      setStatus("Реакция не изменена: " + (error as Error).message);
    }
  };

  const toggleInternalPin = async (message: Message) => {
    if (!message.id) return;
    try {
      await api("/api/internal-messenger/messages/" + message.id + "/pin", { method: message.pinned ? "DELETE" : "POST" });
      setInternalMessages(current => current.map(item => String(item.id) === String(message.id) ? { ...item, pinned: !message.pinned } : item));
      setStatus(message.pinned ? "Сообщение откреплено" : "Сообщение закреплено");
    } catch (error) {
      setStatus("Закрепление не изменено: " + (error as Error).message);
    }
  };

  const deleteInternalMessage = async (message: Message) => {
    if (!message.id) return;
    if (!window.confirm("Удалить это сообщение?")) return;
    try {
      await api("/api/internal-messenger/messages/" + message.id, { method: "DELETE" });
      setInternalMessages(current => current.map(item => String(item.id) === String(message.id) ? { ...item, body: "Сообщение удалено", text: "Сообщение удалено" } : item));
      setStatus("Сообщение удалено");
    } catch (error) {
      setStatus("Сообщение не удалено: " + (error as Error).message);
    }
  };

  const loadInternalReportPreview = async (kind: InternalReportKind, period: 1 | 7 | 30 | 60 = 7) => {
    if (!accountId || !internalConversationId || !canViewReports || internalReportLoading) return;
    setInternalReportLoading(true);
    setInternalComposerMenuOpen(false);
    setInternalReportMenuOpen(false);
    try {
      const endpoint = kind === "ab_tests" ? "ab-tests" : kind;
      const suffix = ["summary", "advertising", "ab_tests"].includes(kind) ? "&period_days=" + period : "";
      const result = await api<{ data?: any }>("/api/messenger-reports/" + endpoint + "?account_id=" + encodeURIComponent(accountId) + suffix);
      setInternalReportPreview({ kind, period, data: result.data || {}, title: internalReportTitle(kind) });
      setInternalDraft("");
      setPendingAttachments([]);
      setStatus("Сводка BORIS готова. Проверьте и подтвердите отправку.");
    } catch (error) {
      setStatus("Сводка не подготовлена: " + (error as Error).message);
    } finally {
      setInternalReportLoading(false);
    }
  };

  const sendInternalReportPreview = async () => {
    if (!internalReportPreview || !internalConversationId || internalReportLoading) return;
    setInternalReportLoading(true);
    try {
      await api("/api/messenger-reports/share", {
        method: "POST",
        body: JSON.stringify({
          account_id: accountId,
          conversation_id: internalConversationId,
          report_type: internalReportPreview.kind,
          period_days: internalReportPreview.period,
        }),
      });
      setInternalReportPreview(null);
      setInternalDraft("");
      setStatus("Сводка BORIS отправлена в чат.");
      setRealtimeTick(value => value + 1);
    } catch (error) {
      setStatus("Сводка не отправлена: " + (error as Error).message);
    } finally {
      setInternalReportLoading(false);
    }
  };

  const sendInternal = async () => {
    if (internalSendBusyRef.current) return;
    if (!internalConversationId || (!internalDraft.trim() && !pendingAttachments.length)) return;
    if (!internalEditing && !pendingAttachments.length) {
      const reportCommand = parseInternalReportCommand(internalDraft);
      if (reportCommand) {
        if (!navigator.onLine || realtimeState === "offline") {
          setStatus("Сводка BORIS требует соединения. Черновик сохранён.");
          return;
        }
        await loadInternalReportPreview(reportCommand.kind, reportCommand.period);
        return;
      }
    }

    internalSendBusyRef.current = true;
    setInternalSending(true);
    try {
      if (internalEditing) {
        if (!editingMessageId || !internalDraft.trim()) throw new Error("Не выбрано сообщение для редактирования");
        if (!navigator.onLine || realtimeState === "offline") {
          setStatus("Изменение сообщения требует соединения. Текст сохранён в черновике.");
          return;
        }
        const updated = await api<{ message?: Message }>(
          `/api/internal-messenger/messages/${editingMessageId}`,
          { method: "PATCH", body: JSON.stringify({ body: internalDraft.trim() }) },
        );
        setInternalMessages(current => current.map(message => String(message.id) === String(editingMessageId)
          ? { ...message, ...(updated.message || {}), body: updated.message?.body || internalDraft.trim(), text: updated.message?.text || internalDraft.trim() }
          : message));
        setStatus("Сообщение сотрудника изменено");
      } else {
        const messageBody = internalDraft.trim()
          || (pendingAttachments.length === 1 ? "📎 " + pendingAttachments[0].file_name : "📎 Вложения: " + pendingAttachments.length);
        const item = await queueTextOutbox(messageBody);

        setInternalDraft("");
        setPendingAttachments([]);
        setInternalReplyTo(null);
        draftHydratedConversationRef.current = internalConversationId;
        void saveDraft(offlineScope, internalConversationId, "").catch(() => undefined);

        if (navigator.onLine && realtimeState !== "offline") {
          try {
            await deliverOutboxItem(item);
            setStatus(pendingAttachments.length ? "Сообщение и файлы отправлены" : "Сообщение отправлено");
          } catch (error) {
            await markOutboxFailure(item, error);
            if (isTransientNetworkError(error)) setRealtimeState("offline");
            setStatus(isTransientNetworkError(error)
              ? "Связь прервалась. Сообщение сохранено и будет отправлено автоматически."
              : "Сообщение сохранено в очереди: " + (error as Error).message);
          }
        } else {
          setStatus("Нет связи. Сообщение сохранено и будет отправлено автоматически.");
        }
      }

      setInternalEditing(false);
      setEditingMessageId(null);
      if (internalEditing) {
        setInternalReplyTo(null);
        setInternalDraft("");
      }
    } catch (error) {
      setStatus("Внутреннее сообщение не сохранено: " + (error as Error).message);
    } finally {
      internalSendBusyRef.current = false;
      setInternalSending(false);
    }
  };

  useEffect(() => {
    if (!offlineScope || realtimeState !== "online" || !navigator.onLine) return;
    void flushOutbox();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && navigator.onLine) void flushOutbox();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [offlineScope, realtimeState]);

  useEffect(() => {
    return () => {
      voiceCancelRef.current = true;
      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try { recorder.stop(); } catch { /* already stopping */ }
      } else {
        if (voiceTimerRef.current !== null) window.clearInterval(voiceTimerRef.current);
        mediaStreamRef.current?.getTracks().forEach(track => track.stop());
      }
    };
  }, [accountId, internalConversationId]);

  const focusInternalComposer = () => {
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => internalComposerRef.current?.focus()));
  };

  const createInternalDmFor = async (userId: number): Promise<boolean> => {
    if (!accountId || !userId || chatCreating) {
      if (!userId) setNewChatError("Выберите сотрудника для личного чата.");
      return false;
    }
    const person = internalPeople.find(item => item.user_id === userId) || internalMembers.find(item => item.user_id === userId);
    setChatCreating(true);
    setNewChatError("");
    try {
      const created = await api<{ conversation_id: number; deduplicated?: boolean }>(
        "/api/internal-messenger/conversations",
        { method: "POST", body: JSON.stringify({ account_id: accountId, kind: "DM", member_user_ids: [userId] }) },
      );
      const label = person?.display_name || person?.email || "Личный чат";
      const thread: InternalThread = {
        id: String(created.conversation_id),
        title: label,
        kind: "Личный",
        members: person ? [person.display_name || person.email] : [],
        lastText: "Начните общение",
        unread: 0,
      };
      setInternalThreads(current => {
        const existing = current.find(item => item.id === thread.id);
        return existing ? current.map(item => item.id === thread.id ? { ...item, ...thread } : item) : [thread, ...current];
      });
      setInternalSelected(thread);
      setInternalConversationId(created.conversation_id);
      setInternalPane("chat");
      setRealtimeTick(value => value + 1);
      closeNewChat();
      setNewDmPersonId(null);
      setStatus(created.deduplicated ? "Открыт существующий личный чат" : "Личный чат создан");
      focusInternalComposer();
      return true;
    } catch (error) {
      const message = (error as Error).message || "Не удалось создать чат.";
      setNewChatError(message);
      setStatus("Не удалось создать чат. Повторите.");
      return false;
    } finally {
      setChatCreating(false);
    }
  };

  const createInternalDm = async () => {
    if (!newDmPersonId) {
      setNewChatError("Выберите сотрудника для личного чата.");
      return;
    }
    await createInternalDmFor(newDmPersonId);
  };

  const toggleNewGroupMember = (userId: number) => {
    setNewGroupMemberIds(current => current.includes(userId)
      ? current.filter(value => value !== userId)
      : [...current, userId]);
    setNewChatError("");
  };

  const createInternalGroup = async () => {
    const title = newGroupTitle.trim();
    if (!accountId || chatCreating) return;
    if (!title) {
      setNewChatError("Введите название группы.");
      return;
    }
    if (!newGroupMemberIds.length) {
      setNewChatError("Выберите хотя бы одного участника.");
      return;
    }
    setChatCreating(true);
    setNewChatError("");
    try {
      const created = await api<{ conversation_id: number; deduplicated?: boolean }>(
        "/api/internal-messenger/conversations",
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId,
            kind: "GROUP",
            title,
            member_user_ids: newGroupMemberIds,
          }),
        },
      );
      const memberNames = selectedGroupPeople.map(person => person.display_name || person.email);
      const group: InternalThread = {
        id: String(created.conversation_id),
        title,
        kind: "Группа",
        members: memberNames,
        lastText: created.deduplicated ? "Группа уже была создана" : "Группа создана. Напишите первое сообщение.",
        unread: 0,
      };
      setInternalThreads(current => {
        const existing = current.find(item => item.id === group.id);
        return existing ? current.map(item => item.id === group.id ? { ...item, ...group } : item) : [group, ...current];
      });
      setInternalSelected(group);
      setInternalConversationId(created.conversation_id);
      setInternalPane("chat");
      setRealtimeTick(value => value + 1);
      setNewGroupTitle("");
      setNewGroupMemberIds([]);
      closeNewChat();
      setStatus(created.deduplicated ? "Открыта уже созданная группа" : "Группа создана");
      focusInternalComposer();
    } catch (error) {
      const message = (error as Error).message || "Не удалось создать группу.";
      setNewChatError(message);
      setStatus("Не удалось создать группу. Повторите.");
    } finally {
      setChatCreating(false);
    }
  };

  const addSelectedInternalMember = async () => {
    if (!internalConversationId || !selectedPersonId) { setStatus("Выберите сотрудника"); return; }
    try {
      await api(`/api/internal-messenger/conversations/${internalConversationId}/members`, { method: "POST", body: JSON.stringify({ user_id: selectedPersonId, role: "MEMBER" }) });
      await refreshInternalMembers(internalConversationId);
      setStatus("Сотрудник добавлен в группу");
    } catch (error) { setStatus(`Сотрудник не добавлен: ${(error as Error).message}`); }
  };

  const renameInternalGroup = async () => {
    if (!internalConversationId || internalSelected?.kind !== "Группа" || !groupTitleDraft.trim()) return;
    try {
      const result = await api<{ title?: string }>(`/api/internal-messenger/conversations/${internalConversationId}`, { method: "PATCH", body: JSON.stringify({ title: groupTitleDraft.trim() }) });
      const title = result.title || groupTitleDraft.trim();
      setInternalSelected(current => current ? { ...current, title } : current);
      setInternalThreads(current => current.map(thread => thread.id === String(internalConversationId) ? { ...thread, title } : thread));
      setGroupTitleDraft("");
      setStatus("Название группы обновлено");
    } catch (error) { setStatus(`Название не изменено: ${(error as Error).message}`); }
  };

  const refreshInternalMembers = async (conversationId: number | null = internalConversationId) => {
    if (!conversationId) return;
    const result = await api<{ members?: InternalPerson[] }>(`/api/internal-messenger/conversations/${conversationId}/members`);
    const members = result.members || [];
    setInternalMembers(members);
    setInternalSelected(current => current ? { ...current, members: members.map(member => member.email) } : current);
  };

  const changeInternalMemberRole = async (person: InternalPerson, role: "ADMIN" | "MEMBER") => {
    if (!internalConversationId) return;
    try {
      await api(`/api/internal-messenger/conversations/${internalConversationId}/members/${person.user_id}`, { method: "PATCH", body: JSON.stringify({ role }) });
      await refreshInternalMembers();
      setStatus(role === "ADMIN" ? "Участник назначен администратором" : "Роль изменена на участника");
    } catch (error) { setStatus(`Роль не изменена: ${(error as Error).message}`); }
  };

  const removeInternalMember = async (person: InternalPerson) => {
    if (!internalConversationId) return;
    try {
      await api(`/api/internal-messenger/conversations/${internalConversationId}/members/${person.user_id}`, { method: "DELETE" });
      await refreshInternalMembers();
      setStatus("Участник удалён из группы");
    } catch (error) { setStatus(`Участник не удалён: ${(error as Error).message}`); }
  };

  const transferInternalOwnership = async (person: InternalPerson) => {
    if (!internalConversationId) return;
    try {
      await api(`/api/internal-messenger/conversations/${internalConversationId}/transfer-ownership`, { method: "POST", body: JSON.stringify({ new_owner_user_id: person.user_id }) });
      await refreshInternalMembers();
      setStatus("Владение группой передано");
    } catch (error) { setStatus(`Владение не передано: ${(error as Error).message}`); }
  };

  const leaveInternalGroup = async () => {
    if (!internalConversationId || !internalSelected || internalSelected.kind !== "Группа" || internalSelected.isGeneral) return;
    if (!window.confirm(`Покинуть группу «${internalSelected.title}»?`)) return;
    try {
      await api(`/api/internal-messenger/conversations/${internalConversationId}/leave`, { method: "POST" });
      setInternalThreads(current => current.filter(thread => thread.id !== String(internalConversationId)));
      setInternalMessages([]);
      setInternalMembers([]);
      setInternalReceipts([]);
      setStatus("Вы покинули рабочую группу.");
      openGeneralTeamChat();
    } catch (error) {
      setStatus(`Не удалось покинуть группу: ${(error as Error).message}`);
    }
  };

  const cancelInternalEdit = () => {
    setInternalEditing(false);
    setEditingMessageId(null);
    setInternalDraft("");
  };

  const createCrmTask = async () => {
    if (!selected || !accountId) return;
    if (!taskTitle.trim() || !taskDueDate) { setStatus("Для задачи укажите название и дату."); return; }
    setTaskCreating(true);
    try {
      await api("/api/messenger-workspace/tasks", {
        method: "POST",
        body: JSON.stringify({
          account_id: accountId,
          chat_id: selected.chat_id,
          due_date: taskDueDate,
          due_time: taskDueTime,
          type: "позвонить",
          title: taskTitle.trim(),
          comment: "",
        }),
      });
      setTaskTitle("");
      setTaskDueDate("");
      setTaskDueTime("");
      await Promise.all([loadContext(selected), loadAccountTasks()]);
      setStatus("CRM-задача создана.");
    } catch (error) {
      setStatus("CRM-задача не создана: " + (error as Error).message);
    } finally {
      setTaskCreating(false);
    }
  };

  const startTaskEdit = (task: Task) => {
    setEditingTaskId(task.id);
    setEditingTaskTitle(task.title || "");
    setEditingTaskDate(task.due_date || "");
    setEditingTaskTime(task.due_time || "");
  };

  const cancelTaskEdit = () => {
    setEditingTaskId(null);
    setEditingTaskTitle("");
    setEditingTaskDate("");
    setEditingTaskTime("");
  };

  const saveTaskEdit = async (task: Task) => {
    if (!selected || !accountId || editingTaskId !== task.id) return;
    if (!editingTaskTitle.trim() || !editingTaskDate) {
      setStatus("Для задачи нужны название и дата.");
      return;
    }
    setTaskUpdating(true);
    try {
      await api("/api/messenger-workspace/tasks/update", {
        method: "POST",
        body: JSON.stringify({
          account_id: accountId,
          task_id: task.id,
          title: editingTaskTitle.trim(),
          due_date: editingTaskDate,
          due_time: editingTaskTime,
        }),
      });
      cancelTaskEdit();
      await Promise.all([loadContext(selected), loadAccountTasks()]);
      setStatus("CRM-задача обновлена.");
    } catch (error) {
      setStatus("CRM-задача не обновлена: " + (error as Error).message);
    } finally {
      setTaskUpdating(false);
    }
  };

  const openTaskClient = (task: Task) => {
    const lead = task.chat_id ? leads.find(item => item.chat_id === task.chat_id) : null;
    if (!lead) {
      setStatus("Клиент этой задачи не найден в текущем Inbox.");
      return;
    }
    setSelected(lead);
    setSection("clients");
    setChatMode("clients");
    setMobilePane("crm");
  };

  const deleteCrmTask = async (task: Task) => {
    if (!accountId || !canReply || taskDeletingId) return;
    if (!window.confirm(`Удалить задачу «${task.title || "Задача"}»?`)) return;
    setTaskDeletingId(task.id);
    try {
      await api("/api/messenger-workspace/tasks/delete", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId, task_id: task.id }),
      });
      if (editingTaskId === task.id) cancelTaskEdit();
      await Promise.all([loadContext(selected), loadAccountTasks()]);
      setStatus("CRM-задача удалена.");
    } catch (error) {
      setStatus("CRM-задача не удалена: " + (error as Error).message);
    } finally {
      setTaskDeletingId(null);
    }
  };

  const renderCrmTask = (task: Task) => (
    <div className={styles.task} key={task.id}>
      {editingTaskId === task.id ? (
        <div className={styles.taskEditor}>
          <input value={editingTaskTitle} onChange={event => setEditingTaskTitle(event.target.value)} aria-label="Название задачи" />
          <div>
            <input type="date" value={editingTaskDate} onChange={event => setEditingTaskDate(event.target.value)} aria-label="Дата задачи" />
            <input type="time" value={editingTaskTime} onChange={event => setEditingTaskTime(event.target.value)} aria-label="Время задачи" />
          </div>
          <div className={styles.taskEditorActions}>
            <button type="button" onClick={cancelTaskEdit} disabled={taskUpdating}>Отмена</button>
            <button type="button" onClick={() => void saveTaskEdit(task)} disabled={!canReply || taskUpdating || !editingTaskTitle.trim() || !editingTaskDate}>{taskUpdating ? "Сохраняем…" : "Сохранить"}</button>
          </div>
        </div>
      ) : (
        <>
          <div><strong>{task.title || "Задача"}</strong><small>{task.due_date || "Без срока"}{task.due_time ? " · " + task.due_time : ""} · <span className={styles.taskStatus} data-status={taskStatusKey(task)}>{taskStatusLabel(task)}</span></small></div>
          <div className={styles.taskActions}>
            {canReply && taskStatusKey(task) !== "done" && <button type="button" onClick={() => startTaskEdit(task)}>Изменить</button>}
            {canReply && taskStatusKey(task) !== "done" && <button type="button" aria-label="Завершить задачу" onClick={() => void markTaskDone(task)}>✓</button>}
            {canReply && <button type="button" className={styles.dangerAction} disabled={taskDeletingId === task.id} onClick={() => void deleteCrmTask(task)}>{taskDeletingId === task.id ? "…" : "Удалить"}</button>}
          </div>
        </>
      )}
    </div>
  );

  const markTaskDone = async (task: Task) => {
    try { await api("/api/messenger-workspace/tasks/done", { method: "POST", body: JSON.stringify({ account_id: accountId, task_id: task.id }) }); await Promise.all([loadContext(selected), loadAccountTasks()]); }
    catch (error) { setStatus(`CRM task не обновлена: ${(error as Error).message}`); }
  };

  if (workspaceLeaving) return <main className={styles.page}><div className={styles.legalGateLoading} role="status">Проверяем доступ к аккаунту…</div></main>;

  if (!legalReady) {
    return <main className={styles.page} data-theme={theme} data-accent={accent}><div className={styles.legalGateLoading}><span className={styles.logo}>B</span><div><strong>BORIS Messenger</strong><small>{realtimeState === "offline" ? "Нет связи. Проверка условий продолжится после восстановления соединения." : "Проверяем актуальные условия использования…"}</small></div></div></main>;
  }

  const myInternalRole = internalMembers.find(member => member.user_id === currentUserId)?.role;
  const internalReplyMessage = internalReplyTo
    ? internalMessages.find(message => String(message.id) === String(internalReplyTo))
    : null;
  const internalPinnedMessages = internalMessages.filter(message => message.pinned && !message.deleted_at).slice(-3);
  const activeCallTitle = activeCall ? (internalThreads.find(thread => Number(thread.id) === Number(activeCall.conversation_id))?.title || "Сотрудник") : "";
  const incomingCallTitle = incomingCall ? (internalThreads.find(thread => Number(thread.id) === Number(incomingCall.conversation_id))?.title || "Сотрудник") : "";

  return <main className={styles.page} data-theme={theme} data-accent={accent}>
    {toast && <div className={styles.toast} data-kind={toast.kind} role="status"><span>{toast.text}</span><button type="button" onClick={() => setToast(null)} aria-label="Закрыть уведомление">×</button></div>}
    {incomingCall && !activeCall ? <div className={styles.incomingCallBackdrop} role="dialog" aria-modal="true" aria-label="Входящий звонок"><div className={styles.incomingCallCard}><span className={styles.incomingCallAvatar}>{incomingCallTitle.slice(0,1).toUpperCase() || "B"}</span><small>{incomingCall.call_mode === "group" ? (incomingCall.call_type === "video" ? "Входящий групповой видеозвонок" : "Входящий групповой аудиозвонок") : incomingCall.call_type === "video" ? "Входящий видеозвонок" : "Входящий аудиозвонок"}</small><h2>{incomingCallTitle}</h2><div className={styles.incomingCallActions}><button type="button" className={styles.callReject} onClick={() => void declineIncomingCall()} aria-label="Отклонить"><PhoneOff size={23} /></button><button type="button" className={styles.callAccept} onClick={() => void acceptIncomingCall()} aria-label="Принять"><Phone size={23} /></button></div></div></div> : null}
    {activeCall ? callMinimized ? <button type="button" className={styles.callMiniBar} onClick={() => { setCallMinimized(false); window.requestAnimationFrame(attachCallStreams); }}><span><Phone size={16} /><strong>{activeCallTitle}</strong><small>{callStatus || activeCall.state}</small></span><b>{callDurationLabel(callElapsed)}</b></button> : <div className={styles.callBackdrop} role="dialog" aria-modal="true" aria-label="Активный звонок"><div className={styles.callPanel} data-video={activeCall.call_type === "video" ? "true" : "false"}><header><div><small>{activeCall.call_mode === "group" ? (activeCall.call_type === "video" ? "Групповой видеозвонок" : "Групповой аудиозвонок") : activeCall.call_type === "video" ? "Видеозвонок" : "Аудиозвонок"}</small><h2>{activeCallTitle}</h2><span>{callStatus || (activeCall.state === "ringing" ? "Вызов…" : activeCall.state)}</span></div><button type="button" onClick={() => setCallMinimized(true)}>Свернуть</button></header><div className={styles.callMediaStage}>{activeCall.call_mode === "group" ? <div className={styles.groupCallGrid} data-video={activeCall.call_type === "video" ? "true" : "false"}>{activeCall.call_type === "audio" ? <><div className={styles.groupAudioGrid}>{groupCallParticipants.filter(person => ["accepted","connected"].includes(String(person.state))).map(person => <span key={person.user_id} className={styles.groupAudioPerson}><b>{(person.display_name || "У").slice(0,1).toUpperCase()}</b><small>{person.user_id === currentUserId ? "Вы" : person.display_name || "Участник"}</small></span>)}</div>{Object.entries(groupRemoteStreams).map(([uid, stream]) => <CallStreamVideo key={uid} stream={stream} className={styles.callAudioEngine} label="" />)}</> : <>{Object.entries(groupRemoteStreams).map(([uid, stream]) => { const person = groupCallParticipants.find(item => item.user_id === Number(uid)); return <CallStreamVideo key={uid} stream={stream} className={styles.groupRemoteVideo} label={person?.display_name || "Участник"} />; })}{localCallStreamRef.current ? <CallStreamVideo stream={localCallStreamRef.current} muted className={styles.groupLocalVideo} label="Вы" /> : null}</>}</div> : <>{activeCall.call_type === "audio" ? <div className={styles.callAudioAvatar}>{activeCallTitle.slice(0,1).toUpperCase() || "B"}</div> : null}<video ref={remoteCallVideoRef} className={activeCall.call_type === "video" ? styles.callRemoteVideo : styles.callAudioEngine} autoPlay playsInline /><video ref={localCallVideoRef} className={activeCall.call_type === "video" ? styles.callLocalVideo : styles.callAudioEngine} autoPlay muted playsInline /></>}</div><div className={styles.callTimer}>{callConnectedAt ? callDurationLabel(callElapsed) : callStatus || "Соединяем…"}</div><div className={styles.callControls}><button type="button" onClick={toggleCallMute} aria-label={callMuted ? "Включить микрофон" : "Выключить микрофон"}>{callMuted ? <MicOff size={21} /> : <Mic size={21} />}</button>{activeCall.call_type === "video" ? <><button type="button" onClick={toggleCallCamera} aria-label={callCameraEnabled ? "Выключить камеру" : "Включить камеру"}>{callCameraEnabled ? <Video size={21} /> : <VideoOff size={21} />}</button><button type="button" onClick={() => void switchCallCamera()} aria-label="Сменить камеру"><SwitchCamera size={21} /></button></> : null}<button type="button" className={styles.callHangup} onClick={() => void endActiveCall()} aria-label="Завершить звонок"><PhoneOff size={22} /></button></div></div></div> : null}
    <header className={styles.header}><div className={styles.brand}><span className={styles.logo}>B</span><div><strong>BORIS Messenger</strong><small>Отдельное рабочее пространство для переписки по выбранному аккаунту</small></div></div><div className={styles.headerActions}><span className={styles.statusDot} data-state={realtimeState} /><span className={styles.connectionLabel}>{realtimeState === "online" ? "Онлайн" : realtimeState === "offline" ? "Нет связи" : realtimeState === "connecting" ? "Подключение…" : "Переподключение…"}</span>{accounts.length > 1 ? <MessengerSelect
    compact
    value={accountId}
    ariaLabel="Разрешённый аккаунт"
    options={[{ value: "", label: "Аккаунт не выбран" }, ...accounts.map(account => ({
      value: account.account_id,
      label: account.name || account.account_id,
      note: account.role === "OWNER" ? "Владелец" : account.role === "VIEWER" ? "Наблюдатель" : "Рабочий аккаунт",
    }))]}
    onChange={value => {
      if (!value || value === accountId) return;
      setWorkspaceLeaving(true);
      for (const key of ["boris_account_id", "account_id", "boris_currentAccount"]) localStorage.setItem(key, value);
      window.location.assign("/messenger?account_id=" + encodeURIComponent(value));
    }}
  /> : <span className={styles.account}>{accounts[0]?.name || accountId || "Аккаунт не выбран"}</span>}</div></header>
    {status ? <div className={styles.statusBanner} role="status" aria-live="polite" aria-atomic="true">{status}</div> : null}
    <nav className={styles.nav} aria-label="Навигация Messenger">
      <button title="Чаты" className={section === "chats" ? styles.navActive : ""} onClick={openGeneralTeamChat}>
        <span className={styles.navIcon}><MessageCircle size={19} strokeWidth={2.15} /></span><span className={styles.navLabel}>Чаты</span>
      </button>
      <button title="Клиенты" className={section === "clients" ? styles.navActive : ""} onClick={() => { setSection("clients"); setChatMode("clients"); clearMoreSectionUrl("clients"); }}>
        <span className={styles.navIcon}><UsersRound size={19} strokeWidth={2.15} /></span><span className={styles.navLabel}>Клиенты</span>
      </button>
      <button data-more-trigger="true" className={styles.moreTrigger} title="Ещё" onClick={() => setMobileMoreOpen(current => !current)} aria-expanded={mobileMoreOpen} aria-controls="messenger-mobile-more">
        <span className={styles.navIcon}><Ellipsis size={20} strokeWidth={2.3} /></span><span className={styles.navLabel}>Ещё</span>
      </button>
    </nav>
    {mobileMoreOpen && <div ref={moreMenuRef} id="messenger-mobile-more" className={styles.mobileMoreSheet} role="dialog" aria-label="Ещё">
      <div className={styles.moreMenuHead}><div><strong>Ещё</strong><small>Управление Messenger</small></div><button type="button" className={styles.moreMenuX} onClick={() => setMobileMoreOpen(false)} aria-label="Закрыть меню"><X size={17} /></button></div>
      <button className={`${styles.moreMenuItem} ${section === "crm" ? styles.moreMenuItemActive : ""}`} onClick={() => openMoreSection("crm")}><span className={styles.moreMenuIcon}><CalendarCheck2 size={18} /></span><span className={styles.moreMenuText}><strong>Задачи и напоминания</strong><small>Мои задачи и дедлайны</small></span><span className={styles.moreMenuArrow}>›</span></button>
      {canViewReports && <button className={`${styles.moreMenuItem} ${section === "reports" ? styles.moreMenuItemActive : ""}`} onClick={() => openMoreSection("reports")}><span className={styles.moreMenuIcon}><BarChart3 size={18} /></span><span className={styles.moreMenuText}><strong>Отчёты и сводки</strong><small>Деньги, реклама, реактивация и A/B-тесты</small></span><span className={styles.moreMenuArrow}>›</span></button>}
      <button className={`${styles.moreMenuItem} ${section === "mop_control" ? styles.moreMenuItemActive : ""}`} onClick={() => openMoreSection("mop_control")}><span className={styles.moreMenuIcon}><BrainCircuit size={18} /></span><span className={styles.moreMenuText}><strong>Контроль МОП</strong><small>Проверка ответов и обучение BORIS</small></span><span className={styles.moreMenuArrow}>›</span></button>
      {platformAdmin && <button className={`${styles.moreMenuItem} ${section === "support" ? styles.moreMenuItemActive : ""}`} onClick={() => openMoreSection("support")}><span className={styles.moreMenuIcon}><LifeBuoy size={18} /></span><span className={styles.moreMenuText}><strong>Поддержка клиентов</strong><small>Все обращения компаний в одном окне</small></span><span className={styles.moreMenuArrow}>›</span></button>}
      {!currentAccountAccess && <div className={`${styles.moreMenuItem} ${styles.moreMenuItemLoading}`} role="status" aria-live="polite"><span className={styles.moreMenuIcon}><UsersRound size={18} /></span><span className={styles.moreMenuText}><strong>Команда и доступы</strong><small>Проверяем права…</small></span><span className={styles.moreMenuArrow}>›</span></div>}
      {canManageTeam && <button className={`${styles.moreMenuItem} ${section === "team" ? styles.moreMenuItemActive : ""}`} onClick={() => openMoreSection("team")}><span className={styles.moreMenuIcon}><UsersRound size={18} /></span><span className={styles.moreMenuText}><strong>Команда и доступы</strong><small>Сотрудники и роли</small></span><span className={styles.moreMenuArrow}>›</span></button>}
      <button className={`${styles.moreMenuItem} ${section === "settings" ? styles.moreMenuItemActive : ""}`} onClick={() => openMoreSection("settings")}><span className={styles.moreMenuIcon}><Bell size={18} /></span><span className={styles.moreMenuText}><strong>Уведомления и приложение</strong><small>Push и установка</small></span><span className={styles.moreMenuArrow}>›</span></button>
      <button className={`${styles.moreMenuItem} ${section === "security" ? styles.moreMenuItemActive : ""}`} onClick={() => openMoreSection("security")}><span className={styles.moreMenuIcon}><ShieldCheck size={18} /></span><span className={styles.moreMenuText}><strong>Безопасность и документы</strong><small>Устройства и соглашения</small></span><span className={styles.moreMenuArrow}>›</span></button>
      <div className={styles.moreMenuSeparator} />
      <button className={styles.moreMenuItem} onClick={() => { window.location.href = "/dashboard"; }}><span className={styles.moreMenuIcon}><ExternalLink size={18} /></span><span className={styles.moreMenuText}><strong>Открыть основной BORIS</strong><small>Перейти в рабочую систему</small></span><span className={styles.moreMenuArrow}>↗</span></button>
    </div>}
    {section === "chats" && chatMode === "internal" && <section className={styles.internalSurface} data-internal-pane={internalPane}>
      <div className={styles.internalGrid}><aside className={styles.internalList}><div className={styles.chatListHead}><div><strong>Чаты</strong><small>{internalThreads.length}</small></div><div className={styles.chatListTools}><button type="button" data-new-chat-trigger="true" className={styles.newChatButton} title="Новый чат" aria-label="Новый чат" aria-expanded={newChatOpen} disabled={!canReply} onClick={toggleNewChat}>＋</button></div></div>{newChatOpen && <div ref={newChatPanelRef} className={styles.newChatMenu} role="dialog" aria-label="Новый чат">
  <div className={styles.newChatHead}><div><strong>Новый чат</strong><small>{newChatMode === "dm" ? "Выберите коллегу для диалога" : "Добавьте коллег и назовите группу"}</small></div><button type="button" onClick={closeNewChat} aria-label="Закрыть"><X size={16} /></button></div>
  <div className={styles.newChatSegments} role="tablist" aria-label="Тип нового чата">
    <button type="button" className={newChatMode === "dm" ? styles.newChatSegmentActive : ""} onClick={() => { setNewChatMode("dm"); setNewChatError(""); }}><UserRound size={15} />Личный чат</button>
    <button type="button" className={newChatMode === "group" ? styles.newChatSegmentActive : ""} onClick={() => { setNewChatMode("group"); setNewChatError(""); }}><UsersRound size={15} />Группа</button>
  </div>
  {newChatMode === "group" && <label className={styles.newChatField}><span>Название группы</span><input value={newGroupTitle} onChange={event => { setNewGroupTitle(event.target.value); setNewChatError(""); }} placeholder="Например: Отдел продаж" aria-label="Название новой группы" maxLength={160} /></label>}
  <label className={styles.newChatSearch}><Search size={16} /><input ref={newChatSearchRef} value={newChatSearch} onChange={event => setNewChatSearch(event.target.value)} placeholder="Имя или почта сотрудника" aria-label="Поиск сотрудника" /></label>
  {newChatMode === "group" && selectedGroupPeople.length > 0 && <div className={styles.newChatChips}>{selectedGroupPeople.map(person => <button type="button" key={person.user_id} onClick={() => toggleNewGroupMember(person.user_id)}>{person.display_name || person.email}<X size={12} /></button>)}</div>}
  <div className={styles.newChatPeople}>
    {newChatCandidates.length ? newChatCandidates.map(person => {
      const selected = newChatMode === "dm" ? newDmPersonId === person.user_id : newGroupMemberIds.includes(person.user_id);
      return <button type="button" key={person.user_id} className={selected ? styles.newChatPersonActive : ""} onClick={() => { if (newChatMode === "dm") { setNewDmPersonId(person.user_id); setNewChatError(""); } else toggleNewGroupMember(person.user_id); }}>
        <span className={styles.newChatAvatar}>{(person.display_name || person.email || "С").slice(0,1).toUpperCase()}</span>
        <span className={styles.newChatPersonText}><strong>{person.display_name || person.email}</strong><small>{accountRoleLabel(person.account_role)}{person.presence ? ` · ${person.presence}` : ""}</small></span>
        <span className={styles.newChatCheck}>{selected ? <Check size={15} /> : ""}</span>
      </button>;
    }) : internalPeopleLoading && !internalPeople.length ? <div className={styles.newChatEmpty}>
      <UsersRound size={24} />
      <strong>Загружаем сотрудников…</strong>
      <small>Список команды появится здесь через несколько секунд.</small>
    </div> : <div className={styles.newChatEmpty}>
      <UsersRound size={24} />
      <strong>{internalPeople.length ? "Никого не нашли" : "Пока некого добавить в чат"}</strong>
      <small>{internalPeople.length ? "Попробуйте ввести другое имя или почту." : "Сначала добавьте сотрудника в команду аккаунта."}</small>
      {canManageTeam && !internalPeople.length && <button type="button" onClick={() => { closeNewChat(); setSection("team"); }}>Добавить сотрудника</button>}
    </div>}
  </div>
  {newChatError && <div className={styles.newChatError}>{newChatError}</div>}
  <button type="button" className={styles.newChatSubmit} onClick={() => void (newChatMode === "dm" ? createInternalDm() : createInternalGroup())} disabled={!canReply || chatCreating || (newChatMode === "dm" ? !newDmPersonId : (!newGroupTitle.trim() || !newGroupMemberIds.length))}>
    {chatCreating ? "Создаём…" : newChatMode === "dm" ? "Начать диалог" : "Создать группу"}
  </button>
</div>}<input className={styles.search} value={internalQuery} onChange={event => setInternalQuery(event.target.value)} placeholder="Поиск чатов" aria-label="Поиск внутренних чатов" /><div className={styles.internalTabs}><button className={!internalUnreadOnly ? styles.tabActive : ""} onClick={() => setInternalUnreadOnly(false)}>Все чаты</button><button className={internalUnreadOnly ? styles.tabActive : ""} onClick={() => setInternalUnreadOnly(true)}>Непрочитанные</button></div>{visibleInternalThreads.length ? visibleInternalThreads.map(thread => <button key={thread.id} className={`${styles.internalRow} ${thread.isGeneral ? styles.generalRow : ""} ${internalSelected?.id === thread.id ? styles.internalRowActive : ""}`} onClick={() => { setInternalSelected(thread); setInternalConversationId(Number(thread.id)); setInternalPane("chat"); }}><span className={`${styles.avatar} ${thread.isGeneral ? styles.generalAvatar : ""}`}>{thread.isGeneral ? "👥" : thread.kind === "Группа" ? "#" : thread.title.slice(0, 1)}</span><span><strong>{thread.title}</strong><small>{thread.isGeneral ? "Вся команда" : thread.kind} · {thread.lastText}</small></span>{thread.unread > 0 && <b>{thread.unread}</b>}</button>) : <div className={styles.internalListEmpty}>{internalQuery ? <><Search size={22} /><strong>Ничего не найдено</strong><small>Попробуйте другой запрос.</small></> : internalUnreadOnly ? <><MessageCircle size={22} /><strong>Нет непрочитанных чатов</strong><small>Все сообщения уже просмотрены.</small></> : <><MessageCircle size={24} /><strong>Здесь появятся ваши рабочие чаты</strong><small>Напишите сотруднику или создайте группу.</small><button type="button" onClick={toggleNewChat} disabled={!canReply}>Создать чат</button></>}</div>}</aside><section className={styles.internalChat}><div className={styles.internalChatHead}><div><span className={styles.kicker}>{internalSelected?.isGeneral ? "Вся команда" : internalSelected?.kind || "Чат"}</span><h2>{internalSelected?.title || "Выберите чат"}</h2><small>{internalSelected?.isGeneral ? `${internalMembers.length} участников · ${internalMembers.filter(member => member.presence === "онлайн").length} онлайн` : internalSelected?.members.join(" · ")}</small></div><div className={styles.internalHeaderActions}>{(internalSelected?.kind === "Личный" || (internalSelected?.kind === "Группа" && !internalSelected?.isGeneral)) && canReply && audioCallsEnabled ? <><button type="button" className={styles.callHeaderButton} onClick={() => void startMessengerCall("audio")} disabled={Boolean(activeCall)} aria-label="Аудиозвонок"><Phone size={18} /></button>{videoCallsEnabled ? <button type="button" className={styles.callHeaderButton} onClick={() => void startMessengerCall("video")} disabled={Boolean(activeCall)} aria-label="Видеозвонок"><Video size={18} /></button> : null}</> : null}<button className={styles.secondaryAction} onClick={() => setInternalPane("list")}>← Чаты</button><button className={styles.secondaryAction} onClick={() => setInternalPane("info")}>Участники</button></div></div>{internalSelected?.title === "Поддержка BORIS" ? <div className={styles.supportChatIntro}><LifeBuoy size={18} /><span><strong>Поддержка BORIS</strong><small>Напишите по работе BORIS. Аккаунт и текущий раздел уже переданы поддержке автоматически.</small></span></div> : null}{internalPinnedMessages.length ? <div className={styles.pinnedStrip}><strong>Закреплённые</strong>{internalPinnedMessages.map(message => <button type="button" key={String(message.id)} onClick={() => document.getElementById("internal-message-" + String(message.id))?.scrollIntoView({ behavior: "smooth", block: "center" })}>{String(message.body || message.text || "Сообщение").slice(0, 80)}</button>)}</div> : null}{internalHasMore && <div className={styles.historyPager}><button type="button" onClick={() => void loadOlderInternalMessages()} disabled={internalOlderLoading}>{internalOlderLoading ? "Загружаем…" : "Загрузить более ранние сообщения"}</button></div>}<div ref={internalHistoryRef} className={styles.internalHistory} onScroll={event => { const node = event.currentTarget; internalNearBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96; }}>{internalMessages.length ? internalMessages.map(message => <div id={"internal-message-" + String(message.id)} key={String(message.id)} className={`${styles.internalBubble} ${(message.body || message.text || "").startsWith("Системное событие: ") ? styles.systemEvent : ""} ${message.is_own || message.direction === "out" || message.sender === "manager" ? styles.internalBubbleOut : ""}`}>{!(message.body || message.text || "").startsWith("Системное событие: ") && <strong>{message.is_own ? "Вы" : message.author_label || message.sender || "Сотрудник"}</strong>}{message.message_type === "voice" || message.voice ? <VoiceMessage message={message} onStatus={setStatus} /> : message.message_type === "video" || message.message_type === "video_note" || message.video ? <VideoMessage message={message} onStatus={setStatus} /> : <p>{(message.body || message.text || "").replace(/^Системное событие:\s*/, "")}</p>}{message.attachments?.length ? <div className={styles.attachmentList}>{message.attachments.map(attachment => <button type="button" className={styles.attachmentCard} key={attachment.id || attachment.file_name} onClick={() => void downloadInternalAttachment(attachment)}>📎 <span>{attachment.file_name}</span><small>{attachment.size_bytes ? `${Math.max(1, Math.round(attachment.size_bytes / 1024))} КБ` : "Файл"}</small></button>)}</div> : null}{message.reactions?.length ? <div className={styles.messageReactionSummary} aria-label="Реакции сообщения">{Array.from(new Set(message.reactions.map(item => item.reaction))).map(reaction => <span key={reaction}>{reaction} {message.reactions!.filter(item => item.reaction === reaction).length}</span>)}</div> : null}<small>{formatTime(message.created_at || message.at)}{message.is_own && message.id ? message.optimistic ? " · " + (message.delivery_status || "Ожидает сети") : " · Отправлено" + (Math.max(0, internalReceipts.filter(receipt => Number(receipt.last_read_message_id || 0) >= Number(message.id)).length - 1) > 0 ? " · Прочитано " + String(Math.max(0, internalReceipts.filter(receipt => Number(receipt.last_read_message_id || 0) >= Number(message.id)).length - 1)) : "") : ""}</small><div className={styles.messageActions}><button type="button" className={styles.mobileMessageMore} onClick={() => setMessageActionMenuId(current => String(current) === String(message.id) ? null : (message.id ?? null))} aria-expanded={String(messageActionMenuId) === String(message.id)}>⋯</button><span className={`${styles.messageActionList} ${String(messageActionMenuId) === String(message.id) ? styles.messageActionListOpen : ""}`}><button aria-label="Ответить" onClick={() => setInternalReplyTo(String(message.id))} disabled={!canReply || message.optimistic}>Ответить</button><span className={styles.reactionPicker}><button onClick={() => setReactionMenuMessageId(current => String(current) === String(message.id) ? null : (message.id ?? null))} disabled={!canReply} aria-expanded={String(reactionMenuMessageId) === String(message.id)}>😊{message.reactions?.length ? " " + message.reactions.length : ""}</button>{String(reactionMenuMessageId) === String(message.id) && <span className={styles.reactionMenu}>{(["👍","✅","❤️","😂"] as const).map(reaction => <button type="button" key={reaction} onClick={() => { void toggleInternalReaction(message, reaction); setReactionMenuMessageId(null); }}>{reaction}{(message.reactions || []).some(item => item.user_id === currentUserId && item.reaction === reaction) ? " ✓" : ""}</button>)}</span>}</span><button aria-label={message.pinned ? "Открепить" : "Закрепить"} onClick={() => void toggleInternalPin(message)} disabled={!canManageTeam || message.optimistic}>{message.pinned ? "Открепить" : "Закрепить"}</button>{message.is_own && <><button aria-label="Изменить" onClick={() => { setInternalReplyTo(null); setInternalEditing(true); setEditingMessageId(message.id ?? null); setInternalDraft(message.body || message.text || ""); setMessageActionMenuId(null); }} disabled={!canReply || message.optimistic}>Изменить</button><button aria-label="Удалить" onClick={() => { setMessageActionMenuId(null); void deleteInternalMessage(message); }} disabled={!canReply || message.optimistic}>Удалить</button></>}</span></div></div>) : <div className={styles.internalChatEmpty}><MessageCircle size={30} /><strong>{internalSelected ? "Начните разговор" : "Выберите чат"}</strong><small>{internalSelected ? "Сообщения команды будут храниться здесь." : "Откройте диалог слева или создайте новый."}</small></div>}</div><div className={styles.internalComposer}>{internalEditing ? <span className={styles.replyBanner}>Редактирование сообщения · <button onClick={cancelInternalEdit}>Отмена</button></span> : internalReplyTo ? <span className={styles.replyBanner}>Ответ: {String(internalReplyMessage?.body || internalReplyMessage?.text || "сообщение").slice(0, 90)} · <button onClick={() => setInternalReplyTo(null)}>Отмена</button></span> : null}{internalReportPreview&&<div className={styles.composerReportPreview}><div className={styles.composerReportHead}><div><span>Сводка BORIS</span><strong>{internalReportPreview.title}{["advertising","ab_tests","summary"].includes(internalReportPreview.kind)?` · ${internalReportPreview.period} дней`:""}</strong></div><button type="button" onClick={()=>setInternalReportPreview(null)} aria-label="Закрыть сводку">×</button></div><div className={styles.composerReportFacts}>{internalReportFacts(internalReportPreview).map(item=><span key={item.label}><small>{item.label}</small><strong>{item.value}</strong></span>)}</div><div className={styles.composerReportActions}><button type="button" className={styles.secondaryAction} onClick={()=>setInternalReportPreview(null)} disabled={internalReportLoading}>Отмена</button><button type="button" className={styles.primaryAction} onClick={()=>void sendInternalReportPreview()} disabled={internalReportLoading}>{internalReportLoading?"Отправляем…":"Вставить в чат"}</button></div></div>}{voiceState !== "idle" ? <div className={styles.voiceRecorder} aria-live="polite"><span className={styles.voiceRecorderState}><i />{voiceState === "permission" ? "Запрашиваем микрофон…" : voiceState === "processing" ? "Сохраняем голосовое…" : voiceState === "paused" ? "Запись на паузе" : "Идёт запись"}<strong>{Math.floor(voiceSeconds / 60) + ":" + String(voiceSeconds % 60).padStart(2, "0")}</strong></span>{voiceState === "recording" || voiceState === "paused" ? <span className={styles.voiceRecorderActions}><button type="button" onClick={toggleVoicePause}>{voiceState === "paused" ? "Продолжить" : "Пауза"}</button><button type="button" onClick={cancelVoiceRecording}>Удалить</button><button type="button" className={styles.voiceSendButton} onClick={finishVoiceRecording}>Отправить</button></span> : null}</div> : videoState !== "idle" ? <div className={styles.videoRecorder} data-kind={videoMode} aria-live="polite"><div className={styles.videoRecorderPreview}>{videoState === "preview" && videoPreviewUrl ? <video src={videoPreviewUrl} controls playsInline /> : videoState === "processing" ? <div className={styles.videoRecorderWait}><Video size={30} /><strong>Сохраняем видео…</strong></div> : <video ref={videoLiveRef} autoPlay muted playsInline />}</div><div className={styles.videoRecorderMeta}><span>{videoMode === "video_note" ? "Видеосообщение" : "Видео"}</span><strong>{Math.floor(videoSeconds / 60) + ":" + String(videoSeconds % 60).padStart(2, "0")}</strong></div>{videoState === "recording" ? <div className={styles.videoRecorderActions}><button type="button" onClick={switchVideoCamera} aria-label="Сменить камеру"><SwitchCamera size={17} /> Камера</button><button type="button" onClick={cancelVideoRecording}>Удалить</button><button type="button" className={styles.videoStopButton} onClick={finishVideoRecording}>Стоп</button></div> : videoState === "preview" ? <div className={styles.videoRecorderActions}><button type="button" onClick={retakeVideo}>Переснять</button><button type="button" onClick={resetVideoCapture}>Удалить</button><button type="button" className={styles.voiceSendButton} onClick={() => void sendVideoPreview()}>Отправить</button></div> : null}</div> : <textarea ref={internalComposerRef} value={internalDraft} onChange={event => setInternalDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (canReply && !attachmentUploading && (internalDraft.trim() || pendingAttachments.length)) void sendInternal(); } }} disabled={!canReply} placeholder={internalEditing ? "Изменить сообщение…" : internalSelected?.isGeneral ? "Написать команде…" : "Написать сообщение…"} rows={2} />}{pendingAttachments.length ? <div className={styles.pendingAttachments}>{pendingAttachments.map((attachment, index) => <span key={`${attachment.file_name}-${index}`}>📎 {attachment.file_name}<button type="button" onClick={() => removePendingAttachment(attachment, index)}>×</button></span>)}</div> : null}<div className={styles.composerTools}><div className={styles.composerPlusWrap}><button type="button" className={styles.attachButton} onClick={()=>{setInternalComposerMenuOpen(v=>!v);setInternalReportMenuOpen(false);}} disabled={attachmentUploading || !internalConversationId || !canReply} aria-expanded={internalComposerMenuOpen} aria-label="Добавить">{attachmentUploading?"…":"＋"}</button>{internalComposerMenuOpen&&!internalReportMenuOpen&&<div className={styles.composerQuickMenu}><label className={styles.composerMenuItem}>📎 <span><strong>Фото или файл</strong><small>JPG, PNG, PDF и другие разрешённые файлы</small></span><input type="file" hidden disabled={attachmentUploading || !internalConversationId || !canReply} onChange={event=>{const file=event.target.files?.[0];if(file)void uploadInternalAttachment(file);event.currentTarget.value="";setInternalComposerMenuOpen(false);}}/></label>{videoMessagesEnabled ? <><label className={styles.composerMenuItem}>🎬 <span><strong>Видео из галереи</strong><small>MP4, MOV, WebM · до 150 МБ</small></span><input type="file" accept="video/*" hidden disabled={!internalConversationId || !canReply} onChange={event=>{const file=event.target.files?.[0];if(file)void queueVideoFile(file,"video");event.currentTarget.value="";setInternalComposerMenuOpen(false);}}/></label><button type="button" className={styles.composerMenuItem} onClick={()=>{setInternalComposerMenuOpen(false);void startVideoRecording("video");}}>📹 <span><strong>Записать видео</strong><small>Камера + микрофон, с предпросмотром</small></span></button></> : null}{videoNotesEnabled ? <button type="button" className={styles.composerMenuItem} onClick={()=>{setInternalComposerMenuOpen(false);void startVideoRecording("video_note");}}>◉ <span><strong>Видеосообщение</strong><small>Быстрый круглый формат</small></span></button> : null}{canViewReports&&<button type="button" className={styles.composerMenuItem} onClick={()=>setInternalReportMenuOpen(v=>!v)}>📊 <span><strong>Сводка BORIS</strong><small>Финансы, реклама, реактивация и A/B</small></span><b>›</b></button>}</div>}{internalComposerMenuOpen&&internalReportMenuOpen&&<div className={styles.composerReportMenu}><button type="button" onClick={()=>void loadInternalReportPreview("summary",7)}>☀️ Утренний отчёт</button><button type="button" onClick={()=>void loadInternalReportPreview("finance",7)}>💳 Финансы</button><button type="button" onClick={()=>void loadInternalReportPreview("advertising",7)}>📈 Реклама · 7 дней</button><button type="button" onClick={()=>void loadInternalReportPreview("reactivation",7)}>🔁 Реактивация</button><button type="button" onClick={()=>void loadInternalReportPreview("ab_tests",30)}>🧪 A/B · 30 дней</button></div>}</div>{voiceState === "idle" && videoState === "idle" && !internalEditing && !internalDraft.trim() && !pendingAttachments.length ? <span className={styles.mediaQuickActions}>{!platformAdmin && internalSelected?.title !== "Поддержка BORIS" ? <span className={styles.supportQuickWrap}><button type="button" className={styles.supportQuickButton} onClick={() => setSupportCategoryMenuOpen(value => !value)} disabled={!canReply || !accountId} aria-label="Спросить поддержку" title="Спросить поддержку">?</button>{supportCategoryMenuOpen ? <span className={styles.supportCategoryMenu} role="menu" aria-label="Категория поддержки">{["Не работает","Нужна помощь","Вопрос по объявлениям","Вопрос по МОП","Вопрос по оплате","Вопрос по Messenger","Другое"].map(category => <button type="button" role="menuitem" key={category} onClick={() => void openSupportChat(category)}>{category}</button>)}</span> : null}</span> : null}<button type="button" className={styles.voiceRecordButton} onClick={() => void startVoiceRecording()} disabled={!canReply || !internalConversationId} aria-label="Записать голосовое сообщение"><Mic size={19} /></button>{videoNotesEnabled ? <button type="button" className={styles.videoRecordButton} onClick={() => void startVideoRecording("video_note")} disabled={!canReply || !internalConversationId} aria-label="Записать видеосообщение"><Video size={19} /></button> : null}</span> : voiceState === "idle" && videoState === "idle" ? <button className={styles.primaryAction} onClick={sendInternal} disabled={!canReply || attachmentUploading || internalSending || internalReportLoading || (!internalDraft.trim() && !pendingAttachments.length)}>{internalEditing ? "Сохранить" : "Отправить"}</button> : null}{mediaUploadProgress !== null ? <span className={styles.uploadProgress}>Загрузка {mediaUploadProgress}%</span> : null}{outboxCount > 0 ? <span className={styles.outboxStatus}>В очереди: {outboxCount}</span> : null}</div></div></section><aside className={styles.internalInfo}><h3>Участники</h3>{internalMembers.length ? internalMembers.map(person => <div className={styles.personRow} key={person.user_id}><span className={styles.avatar}>{(person.display_name || person.email || "С").slice(0, 1).toUpperCase()}</span><span><strong>{person.display_name || person.email}</strong><small>{internalRoleLabel(person.role)}{person.presence ? ` · ${person.presence}` : ""}</small></span>{internalSelected?.isGeneral && person.user_id !== currentUserId && <span className={styles.messageActions}><button onClick={() => void createInternalDmFor(person.user_id)} disabled={!canReply}>Написать</button></span>}{internalSelected?.kind === "Группа" && !internalSelected?.isGeneral && person.user_id !== currentUserId && <span className={styles.messageActions}>{canReply && myInternalRole === "OWNER" && person.role !== "OWNER" && <><button onClick={() => void changeInternalMemberRole(person, person.role === "ADMIN" ? "MEMBER" : "ADMIN")}>{person.role === "ADMIN" ? "Снять администратора" : "Сделать администратором"}</button><button onClick={() => void transferInternalOwnership(person)}>Передать владение</button><button onClick={() => void removeInternalMember(person)}>Удалить</button></>}{canReply && myInternalRole === "ADMIN" && person.role === "MEMBER" && <button onClick={() => void removeInternalMember(person)}>Удалить</button>}</span>}</div>) : <div className={styles.empty}>Участники появятся после выбора чата.</div>}<div className={styles.groupActions}>{internalSelected?.isGeneral && <div className={styles.generalInfo}><strong>Вся команда компании</strong><small>Состав этого чата синхронизируется автоматически из раздела «Команда». Приглашённые сотрудники появляются здесь после принятия доступа.</small></div>}{internalSelected?.kind === "Группа" && !internalSelected?.isGeneral && <>{canReply && (myInternalRole === "OWNER" || myInternalRole === "ADMIN") && <><input className={styles.search} value={groupTitleDraft} onChange={event => setGroupTitleDraft(event.target.value)} placeholder="Новое название группы" /><button onClick={() => void renameInternalGroup()}>Переименовать группу</button></>}{canReply && (myInternalRole === "OWNER" || myInternalRole === "ADMIN") && <><MessengerSelect ariaLabel="Сотрудник для добавления в группу" value={internalMembers.some(member => member.user_id === selectedPersonId) ? "" : String(selectedPersonId || "")} options={[{ value: "", label: "Выберите сотрудника" }, ...internalPeople.filter(person => !internalMembers.some(member => member.user_id === person.user_id)).map(person => ({ value: String(person.user_id), label: person.display_name || person.email }))]} onChange={value => setSelectedPersonId(value ? Number(value) : null)} /><button onClick={() => void addSelectedInternalMember()} disabled={!selectedPersonId || internalMembers.some(member => member.user_id === selectedPersonId)}>Добавить выбранного сотрудника</button></>}{myInternalRole !== "OWNER" && <button className={styles.dangerAction} onClick={() => void leaveInternalGroup()}>Покинуть группу</button>}</>}{canManageTeam && <button onClick={() => setSection("team")}>Управление командой</button>}</div></aside></div>
    </section>}
    {section !== "chats" && section !== "clients" && <section className={styles.workspaceSurface}>
      {section !== "team" && <div className={styles.surfaceHead}><div><span className={styles.kicker}>{section.toUpperCase()}</span><h1>{section === "work" ? "Работа" : section === "crm" ? "Задачи и напоминания" : section === "reports" ? "Отчёты и сводки" : section === "mop_control" ? "Контроль МОП" : section === "support" ? "Поддержка клиентов" : section === "security" ? "Безопасность" : "Настройки Messenger"}</h1><p className={styles.surfaceSub}>{section === "reports" ? `Что хотите узнать по ${currentAccountAccess?.name || "выбранному аккаунту"}?` : section === "mop_control" ? "Проверяйте ответы BORIS и обучайте МОП на своей обратной связи" : section === "support" ? "Все клиентские обращения и быстрый переход в нужный аккаунт" : section === "settings" ? "Персональные настройки этого устройства" : accounts.length ? `Текущий аккаунт: ${currentAccountAccess?.name || "выбран"}` : "Для этой сессии нет доступных аккаунтов"}</p></div><span className={styles.accountChip}>{currentAccountAccess?.name || "Аккаунт не выбран"}</span></div>}
      {section === "work" && <WorkDashboard
        clientCount={leads.length}
        humanCount={leads.filter(lead => lead.owner_type === "human").length}
        aiDraftCount={aiDrafts.length}
        activeCount={leads.filter(lead => lead.owner_type === "human").length + aiDrafts.length + accountTasks.filter(task => taskStatusKey(task) !== "done").length}
        urgentTotal={accountTasks.filter(task => ["overdue", "today"].includes(taskStatusKey(task))).length}
        urgentTasks={accountTasks.filter(task => ["overdue", "today"].includes(taskStatusKey(task))).slice(0, 5).map(task => ({
          id: task.id,
          title: task.title || "Задача",
          due: (task.due_date || "Без даты") + (task.due_time ? " · " + task.due_time : ""),
          status: taskStatusKey(task),
          statusLabel: taskStatusLabel(task),
        }))}
        canManageTeam={canManageTeam}
        onOpenInbox={() => { setSection("clients"); setChatMode("clients"); setMobilePane("inbox"); }}
        onOpenHuman={() => { setSection("clients"); setChatMode("clients"); setClientFilter("human"); setMobilePane("inbox"); }}
        onOpenAi={() => { setSection("clients"); setChatMode("clients"); setMobilePane("chat"); }}
        onOpenTask={taskId => { const task = accountTasks.find(item => item.id === taskId); if (task) openTaskClient(task); }}
        onOpenCrm={() => setSection("crm")}
        onOpenGeneral={openGeneralTeamChat}
        onOpenTeam={() => setSection("team")}
      />}
      {section === "reports" && <ReportsPanel accountId={accountId} accountName={currentAccountAccess?.name || accountId || "Аккаунт"} canViewReports={canViewReports} onStatus={setStatus} />}
      {section === "mop_control" && <MopControlPanel accountId={accountId} accountName={currentAccountAccess?.name || accountId || "Аккаунт"} onStatus={setStatus} />}
      {section === "support" && <SupportCenter platformAdmin={platformAdmin} onOpenTicket={openOwnerSupportTicket} onStatus={setStatus} />}
      {section === "crm" && <><div className={styles.cardGrid}><article className={styles.metricCard}><span>Сегодня</span><strong>{accountTasks.filter(task => taskStatusKey(task) === "today").length}</strong><small>Задачи на сегодня</small></article><article className={styles.metricCard}><span>Просрочено</span><strong>{accountTasks.filter(task => taskStatusKey(task) === "overdue").length}</strong><small>Требуют действия</small></article><article className={styles.metricCard}><span>Запланировано</span><strong>{accountTasks.filter(task => taskStatusKey(task) === "planned").length}</strong><small>Будущие задачи</small></article><article className={styles.metricCard}><span>Выполнено</span><strong>{accountTasks.filter(task => taskStatusKey(task) === "done").length}</strong><small>Закрытые задачи</small></article></div><div className={styles.detailCard}><h2>Срочные задачи аккаунта</h2><p>Просроченные и задачи на сегодня по всем клиентам текущего аккаунта.</p><div className={styles.taskQueue}>{accountTasks.filter(task => ["overdue","today"].includes(taskStatusKey(task))).slice(0, 5).map(task => <button type="button" className={styles.taskQueueItem} key={task.id} onClick={() => openTaskClient(task)}><span><strong>{task.title || "Задача"}</strong><small>{task.due_date || "Без даты"}{task.due_time ? " · " + task.due_time : ""}</small></span><span className={styles.taskStatus} data-status={taskStatusKey(task)}>{taskStatusLabel(task)}</span></button>)}{!accountTasks.some(task => ["overdue","today"].includes(taskStatusKey(task))) && <small>Срочных задач нет.</small>}</div></div><div className={styles.detailCard}><h2>{selected?.contact_name || "Задачи по клиентам"}</h2>{selected ? <><p>{selected.phone || "Телефон не указан"} · {selected.item_title || "Объявление не указано"}</p><div className={styles.facts}><div><span>Ответственный</span><strong>{selected.assignee_name || selected.assigned_to || "Не назначен"}</strong></div><div><span>Режим</span><strong>{selected.owner_type === "human" ? "Сотрудник" : "BORIS AI"}</strong></div></div><div className={styles.tasks}><div className={styles.taskTitle}><strong>Задачи клиента</strong><span>{tasks.length}</span></div>{tasks.length ? tasks.map(renderCrmTask) : <small>По выбранному клиенту задач нет.</small>}</div><button className={styles.primaryAction} onClick={() => { setSection("clients"); setChatMode("clients"); setMobilePane("crm"); }}>Открыть клиента и задачи</button></> : <><p>Выберите клиентский диалог, чтобы открыть задачи и ответственного.</p><button className={styles.primaryAction} onClick={() => { setSection("clients"); setChatMode("clients"); setMobilePane("inbox"); }}>Открыть клиентские диалоги</button></>}</div></>}
      {section === "team" && <div className={styles.teamWorkspace}>
        <div className={styles.teamToolbar}>
          <div>
            <h2>Команда</h2>
            <p>{teamMembers.filter(member => member.status === "ACTIVE").length} участников · {teamMembers.filter(member => member.status === "INVITED").length} приглашений</p>
          </div>
          <button className={styles.primaryAction} onClick={() => { setLastInviteUrl(""); setInviteOpen(true); }} disabled={!canManageTeam}>＋ Пригласить</button>
        </div>
        {teamError && <div className={styles.teamInlineError} role="status"><strong>Нет доступа к команде</strong><span>{teamError}</span></div>}
        <div className={styles.teamMembersList}>
          {teamLoading ? <div className={styles.teamEmpty}>Загружаем команду…</div> : teamMembers.length ? teamMembers.map(member => <div className={styles.teamMemberRow} key={member.is_account_owner ? "owner-" + String(member.user_id) : String(member.id)}>
            <span className={styles.teamMemberAvatar}>{(member.name || member.email || member.phone || "С").slice(0,1).toUpperCase()}</span>
            <span className={styles.teamMemberMain}>
              <span className={styles.teamMemberName}><strong>{member.name || member.email || member.phone || "Сотрудник"}</strong>{member.is_account_owner && <em>Владелец</em>}</span>
              <small>{member.email || member.phone || "Контакт не указан"}</small>
            </span>
            <span className={styles.teamMemberMeta}>
              <span className={styles.teamStatus} data-status={member.status}>{member.status === "ACTIVE" ? "Активен" : member.status === "INVITED" ? "Приглашён" : "Доступ отозван"}</span>
              {member.last_login_at && <small>Вход: {new Date(member.last_login_at).toLocaleDateString("ru-RU")}</small>}
            </span>
            <span className={styles.teamMemberDevice}>
              <span className={styles.teamReadiness} data-ready={member.status === "ACTIVE" && Boolean(member.pwa_installed || member.pwa_standalone_confirmed) && member.push_status === "on"}>
                {member.status === "INVITED" ? "Ждём принятия" : member.status === "REVOKED" ? "Доступ отозван" : (member.pwa_installed || member.pwa_standalone_confirmed) && member.push_status === "on" ? "Готов к работе" : "Настройте устройство"}
              </span>
              <small>{member.push_status === "on" ? "Push включён" : "Push —"} · {member.pwa_installed || member.pwa_standalone_confirmed ? "Приложение установлено" : "PWA —"}</small>
              {!member.is_account_owner && <small>Чаты: {Number(member.assigned_chats_count || 0)} · задачи: {Number(member.assigned_tasks_count || 0)}</small>}
            </span>
            <span className={styles.teamMemberActions}>
              {!member.is_account_owner && member.status !== "REVOKED" && member.user_id && <MessengerSelect
    compact
    value={member.role}
    disabled={!canManageTeam}
    ariaLabel={"Роль " + (member.name || member.email || "сотрудника")}
    options={[
      { value: "MANAGER_LEAD", label: "Старший менеджер", note: "Клиенты, CRM и команда" },
      { value: "MANAGER", label: "Менеджер", note: "Клиенты, CRM и чаты" },
      { value: "VIEWER", label: "Наблюдатель", note: "Только просмотр" },
    ]}
    onChange={value => void changeTeamRole(member, value as "MANAGER_LEAD" | "MANAGER" | "VIEWER")}
  />}
              {!member.is_account_owner && member.user_id ? <button className={styles.ghostDanger} onClick={() => void revokeTeamMember(member)} disabled={!canManageTeam}>Отозвать</button> : !member.is_account_owner && member.status === "INVITED" ? <button className={styles.ghostDanger} onClick={() => void revokePendingInvite(member)} disabled={!canManageTeam}>Отменить</button> : null}
            </span>
          </div>) : <div className={styles.teamEmpty}><span className={styles.teamEmptyIcon}>👥</span><strong>Добавьте первого сотрудника</strong><p>Приглашённый сотрудник появится в общем чате после принятия доступа.</p><button className={styles.primaryAction} disabled={!canManageTeam} onClick={() => setInviteOpen(true)}>＋ Пригласить</button></div>}
        </div>
        {inviteOpen && <div className={styles.inviteBackdrop} role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setInviteOpen(false); }}>
          <aside className={styles.inviteDrawer} role="dialog" aria-modal="true" aria-label="Пригласить сотрудника">
            <div className={styles.inviteDrawerHead}><div><span>Новый участник</span><h3>Пригласить в BORIS</h3></div><button type="button" onClick={() => setInviteOpen(false)} aria-label="Закрыть">×</button></div>
            <label>Имя<input value={inviteName} onChange={event => setInviteName(event.target.value)} placeholder="Например, Анна" /></label>
            <label>Телефон<input value={invitePhone} onChange={event => setInvitePhone(event.target.value)} placeholder="+7 900 000-00-00" /></label>
            <label>Email <small>необязательно</small><input value={inviteEmail} onChange={event => setInviteEmail(event.target.value)} placeholder="name@company.ru" /></label>
            <div className={styles.roleCards}>
              {([["MANAGER_LEAD","Старший менеджер","Клиенты, CRM и управление командой"],["MANAGER","Менеджер","Клиенты, CRM и внутренние чаты"],["VIEWER","Наблюдатель","Только просмотр"]] as const).map(([value,title,description]) => <button type="button" key={value} className={inviteRole === value ? [styles.roleCard,styles.roleCardActive].join(" ") : styles.roleCard} onClick={() => setInviteRole(value)}><strong>{title}</strong><small>{description}</small></button>)}
            </div>
            <button className={styles.inviteSubmit} onClick={() => void createTeamInvite()} disabled={!canManageTeam || !inviteName.trim() || !invitePhone.trim()}>Создать приглашение</button>
            {lastInviteUrl && <div className={styles.inviteResult}><strong>Приглашение готово</strong><p>Ссылка действует 72 часа. Отправьте сотруднику приглашение и универсальную инструкцию BORIS.</p><div><code>{lastInviteUrl}</code><button type="button" onClick={() => void copyInviteUrl()}>Скопировать приглашение</button></div><div className={styles.inviteGuideActions}><a className={styles.inviteGuideLink} href="/messenger/guide" target="_blank" rel="noreferrer">Открыть инструкцию</a><button type="button" onClick={async () => { try { await navigator.clipboard.writeText(window.location.origin + "/messenger/guide"); setStatus("Ссылка на инструкцию скопирована."); } catch { setStatus("Откройте инструкцию и скопируйте ссылку вручную."); } }}>Скопировать инструкцию</button><button type="button" onClick={() => void copyInvitePackage()}>Скопировать всё для сотрудника</button></div></div>}
          </aside>
        </div>}
      </div>}
      {section === "security" && <SecurityPanel />}
      {section === "settings" && <SettingsPanel theme={theme} accent={accent} setTheme={setTheme} setAccent={setAccent} pushState={pushState} micPermission={micPermission} cameraPermission={cameraPermission} permissionHelp={mediaPermissionHelp()} enablePush={() => void enableMessengerPush()} disablePush={() => void disableMessengerPush()} installedApp={installedApp} installLabel={installedApp ? "Установлено" : installPrompt ? "Установить на устройство" : isIos() ? "Как установить на iPhone" : "Установить на устройство"} installApp={() => void installMessenger()} showIosTip={isIos() && !installedApp && pushState !== "on"} hasUpdate={Boolean(pwaUpdateRegistration)} applyUpdate={applyPwaUpdate} accounts={accounts} currentAccountId={accountId} onOpenSecurity={() => openMoreSection("security")} onboarding={firstRunOnboarding} guideHref="/messenger/guide" onFinishOnboarding={() => { setFirstRunOnboarding(false); setSection("chats"); setChatMode("internal"); setInternalPane("list"); if (typeof window !== "undefined") { const url = new URL(window.location.href); url.searchParams.delete("onboarding"); url.searchParams.delete("section"); window.history.replaceState({ messengerSection: "chats" }, "", url.toString()); window.scrollTo({ top: 0, behavior: "smooth" }); } }} />}
    </section>}
    <section className={`${styles.shell} ${section !== "clients" || chatMode !== "clients" ? styles.shellHidden : ""}`} data-mobile-pane={mobilePane}>
      <aside className={`${styles.inbox} ${mobilePane === "inbox" ? styles.mobileVisible : ""}`}><div className={styles.panelHead}><div><span className={styles.kicker}>INBOX</span><h1>Диалоги</h1></div><button className={styles.refresh} onClick={() => void loadInbox()} disabled={loading}>{loading ? "…" : "↻"}</button></div><input className={styles.search} value={query} onChange={event => setQuery(event.target.value)} placeholder="Поиск клиента или сообщения" /><div className={styles.clientFilters}>{([["all","Все"],["unread","Непрочитанные"],["human","HUMAN"],["ai","BORIS"],["mine","Мои"],["unassigned","Без ответственного"],["overdue","Просроченные"]] as const).map(([value,label]) => <button type="button" key={value} className={clientFilter === value ? styles.tabActive : ""} onClick={() => setClientFilter(value)}>{label}</button>)}</div><div className={styles.count}>{visibleLeads.length} диалогов</div><div className={styles.rows}>{visibleLeads.map(lead => <button key={lead.chat_id} className={`${styles.row} ${selected?.chat_id === lead.chat_id ? styles.active : ""}`} onClick={() => { setSelected(lead); setMobilePane("chat"); }}><span className={styles.avatar}>{(lead.contact_name || "К").slice(0, 1).toUpperCase()}</span><span className={styles.rowBody}><span className={styles.rowTitle}><strong>{lead.contact_name || "Клиент Avito"}</strong><time>{formatTime(lead.last_msg_at)}</time></span><small>{lead.last_text || "Новое обращение"}</small><span className={styles.rowMeta}><i data-owner={lead.owner_type === "human" ? "human" : "ai"}>{lead.owner_type === "human" ? (lead.assignee_name ? `HUMAN · ${lead.assignee_name}` : "HUMAN") : "BORIS"}</i>{Number(lead.unread_count || 0) > 0 && <b>{lead.unread_count}</b>}</span></span></button>)}</div></aside>
      <section className={`${styles.chat} ${mobilePane === "chat" ? styles.mobileVisible : ""}`}><div className={styles.mobileNav}><button onClick={() => setMobilePane("inbox")}>← Чаты</button><button onClick={() => setMobilePane("crm")}>Задачи →</button></div>{selected ? <><div className={styles.chatHead}><div><span className={styles.kicker}>ДИАЛОГ</span><h2>{selected.contact_name || "Клиент Avito"}</h2><small>{selected.item_title || `chat ${selected.chat_id}`}</small></div><div className={styles.owner}><span className={selected.owner_type === "human" ? styles.human : styles.ai}>{selected.owner_type === "human" ? "СОТРУДНИК" : "BORIS AI"}</span><small>{selected.assignee_name || selected.assigned_to || "Не назначен"}</small></div></div><div className={styles.actions}><span className={styles.ownershipStatus}>{selected.owner_type === "human" ? (selected.assignee_name || selected.assigned_to ? `Диалог ведёт ${selected.assignee_name || selected.assigned_to}` : "Диалог ведётся вручную") : "BORIS отвечает"}</span><button className={styles.ownershipToggle} onClick={() => setOwnershipMenuOpen(current => !current)} disabled={!canReply} aria-expanded={ownershipMenuOpen}>Управление</button>{ownershipMenuOpen && <div className={styles.ownershipMenu} role="menu"><button onClick={() => { setOwnershipMenuOpen(false); void take("take"); }}>Взять себе</button><button onClick={() => { setOwnershipMenuOpen(false); void take("human"); }}>Вести вручную</button><button onClick={() => { setOwnershipMenuOpen(false); void take("release"); }}>Снять назначение</button><button onClick={() => { setOwnershipMenuOpen(false); void take("mop"); }}>Вернуть BORIS</button></div>}</div>{aiDrafts.filter(item => item.avito_chat_id === selected.chat_id).map(draft => <div className={styles.aiDraftCard} key={draft.draft_id}><div className={styles.aiDraftHead}><div><span className={styles.kicker}>BORIS AI · ЧЕРНОВИК</span><strong>Ответ ждёт подтверждения</strong></div><span className={styles.ai}>DRAFT</span></div><textarea value={aiDraftEdits[draft.draft_id] ?? draft.text} onChange={event => setAiDraftEdits(current => ({ ...current, [draft.draft_id]: event.target.value }))} rows={4} aria-label="Текст AI-черновика" /><div className={styles.aiDraftActions}><button className={styles.secondaryAction} disabled={!canReply || aiDraftBusy === draft.draft_id} onClick={() => void actOnAiDraft(draft, "discard")}>Отклонить</button><button className={styles.primaryAction} disabled={!canReply || aiDraftBusy === draft.draft_id || !(aiDraftEdits[draft.draft_id] ?? draft.text).trim()} onClick={() => void actOnAiDraft(draft, "approve")}>{aiDraftBusy === draft.draft_id ? "Выполняем…" : "Отправить ответ"}</button></div></div>)}<div className={styles.history}>{messages.length ? messages.map((message, index) => <div key={`${message.id || index}`} className={`${styles.bubble} ${message.direction === "out" || message.sender === "manager" ? styles.out : ""}`}><p>{message.text || message.body || message.message || ""}</p><small>{message.author_label || (message.direction === "out" ? "Сотрудник" : "Клиент")} · {formatTime(message.created_at || message.at)}{(message.direction === "out" || message.sender === "manager" || message.is_own) && message.delivery_status ? ` · ${String(message.delivery_status).toLowerCase() === "sent" ? "Отправлено" : String(message.delivery_status)}` : ""}</small></div>) : <div className={styles.empty}>История диалога появится после загрузки.</div>}</div><div className={styles.composer}><textarea value={draft} onChange={event => updateClientDraft(event.target.value)} placeholder={canReply ? "Ответ сотрудника…" : "Режим просмотра"} rows={3} disabled={!canReply} /><div><span>Ответ сохранится в истории и включит HUMAN ownership</span><button onClick={() => void send()} disabled={!canReply || !draft.trim() || sending || realtimeState === "offline"}>{realtimeState === "offline" ? "Нет связи" : sending ? "Отправка…" : "Отправить"} ↗</button></div></div></> : <div className={styles.empty}>Выберите диалог в Inbox.</div>}</section>
      <aside className={`${styles.crm} ${mobilePane === "crm" ? styles.mobileVisible : ""}`}><div className={styles.mobileNav}><button onClick={() => setMobilePane("chat")}>← Чат</button></div>{selected ? <><div className={styles.panelHead}><div><span className={styles.kicker}>ЗАДАЧИ / КЛИЕНТ</span><h2>{selected.contact_name || "Клиент"}</h2></div><span className={styles.live} /></div><div className={styles.contact}><span className={styles.avatar}>{(selected.contact_name || "К").slice(0, 1).toUpperCase()}</span><div><strong>{selected.phone || "Контакт без телефона"}</strong><small>{selected.item_title || "Объявление не указано"}</small></div></div><div className={styles.facts}><div><span>Ответственный</span><strong>{selected.assignee_name || selected.assigned_to || "Не назначен"}</strong></div><div><span>Режим ответа</span><strong>{selected.owner_type === "human" ? "Сотрудник" : "BORIS AI"}</strong></div></div><div className={styles.tasks}><div className={styles.taskTitle}><strong>Новая задача</strong><span>{taskCreating ? "…" : "+"}</span></div><input className={styles.search} value={taskTitle} onChange={event => setTaskTitle(event.target.value)} placeholder="Что сделать" disabled={!canReply} /><input className={styles.search} type="date" value={taskDueDate} onChange={event => setTaskDueDate(event.target.value)} aria-label="Дата задачи" disabled={!canReply} /><input className={styles.search} type="time" value={taskDueTime} onChange={event => setTaskDueTime(event.target.value)} aria-label="Время задачи" disabled={!canReply} /><button className={styles.primaryAction} disabled={!canReply || taskCreating || !taskTitle.trim() || !taskDueDate} onClick={() => void createCrmTask()}>{taskCreating ? "Создаём…" : "Создать задачу"}</button><div className={styles.taskTitle}><strong>Задачи и напоминания</strong><span>{tasks.length}</span></div>{tasks.length ? tasks.map(renderCrmTask) : <small>Задач и напоминаний пока нет.</small>}</div></> : <div className={styles.empty}>Задачи клиента появятся после выбора диалога.</div>}</aside>
    </section>
    <footer className={styles.footer}><span>{realtimeState === "online" ? "Синхронизация активна" : realtimeState === "offline" ? "Нет соединения" : "Синхронизация…"}</span><span>{currentAccountAccess?.name || ""}</span></footer>
  </main>;
}
