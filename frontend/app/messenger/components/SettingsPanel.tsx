"use client";

import { useEffect, useState } from "react";
import { Bell, ChevronRight, MonitorSmartphone, Palette, ShieldCheck, UsersRound, X } from "lucide-react";
import styles from "../messenger.module.css";

type Account = { account_id:string; name?:string; role?:string; can_reply?:boolean; can_manage_team?:boolean; can_view_reports?:boolean };
type MediaPermissionState = "granted"|"prompt"|"denied"|"unsupported";

export default function SettingsPanel({
  theme,accent,setTheme,setAccent,pushState,micPermission,cameraPermission,permissionHelp,enablePush,disablePush,installedApp,installLabel,installApp,showIosTip,hasUpdate,applyUpdate,accounts,currentAccountId,onOpenSecurity,onboarding,guideHref,onFinishOnboarding,
}:{
  theme:"light"|"dark"|"system";accent:"blue"|"graphite"|"sky";
  setTheme:(v:"light"|"dark"|"system")=>void;setAccent:(v:"blue"|"graphite"|"sky")=>void;
  pushState:"unknown"|"unsupported"|"off"|"on"|"denied";micPermission:MediaPermissionState;cameraPermission:MediaPermissionState;permissionHelp:string;enablePush:()=>void;disablePush:()=>void;
  installedApp:boolean;installLabel:string;installApp:()=>void;showIosTip:boolean;hasUpdate:boolean;applyUpdate:()=>void;
  accounts:Account[];currentAccountId:string;onOpenSecurity:()=>void;
  onboarding:boolean;guideHref:string;onFinishOnboarding:()=>void;
}) {
  const [accessOpen,setAccessOpen]=useState(false);
  const current=accounts.find(x=>x.account_id===currentAccountId);
  const pushLabel=pushState==="on"?"Включены на этом устройстве":pushState==="denied"?"Заблокированы браузером":pushState==="unsupported"?"Не поддерживаются":"Выключены";
  const pushStateKey=pushState==="on"?"on":pushState==="denied"?"denied":"off";
  const permissionLabel=(value:MediaPermissionState)=>value==="granted"?"Разрешён":value==="denied"?"Заблокирован":"Не запрошен";
  const permissionState=(value:MediaPermissionState)=>value==="granted"?"on":value==="denied"?"denied":"off";
  const mediaBlocked=micPermission==="denied"||cameraPermission==="denied";
  useEffect(()=>{
    if(!accessOpen)return;
    const onKey=(event:KeyboardEvent)=>{if(event.key==="Escape")setAccessOpen(false);};
    document.addEventListener("keydown",onKey);
    return()=>document.removeEventListener("keydown",onKey);
  },[accessOpen]);
  return <div className={styles.settingsGridV2}>
    {onboarding&&<section className={styles.onboardingWelcome}>
      <div className={styles.onboardingWelcomeCopy}>
        <span>Первый запуск</span>
        <h2>Добро пожаловать в BORIS Messenger</h2>
        <p>Настройте рабочее приложение один раз — дальше здесь будут чаты команды, клиентские обращения, CRM-задачи, напоминания и звонки.</p>
      </div>
      <div className={styles.onboardingSteps}>
        <article data-done={installedApp}><b>1</b><div><strong>Установите BORIS</strong><small>{installedApp?"Приложение уже установлено на этом устройстве.":"Добавьте BORIS на экран телефона или установите как приложение на компьютере."}</small></div>{!installedApp&&<button type="button" onClick={installApp}>{installLabel}</button>}</article>
        <article data-done={pushState==="on"}><b>2</b><div><strong>Включите уведомления</strong><small>{pushState==="on"?"Push уже включены.":"Чтобы не пропускать новые лиды, задачи, сообщения и входящие звонки."}</small></div>{pushState!=="on"&&pushState!=="unsupported"&&pushState!=="denied"&&<button type="button" onClick={enablePush}>Включить Push</button>}</article>
        <article><b>3</b><div><strong>Разрешите микрофон и камеру</strong><small>BORIS спросит разрешение при первом голосовом, видеосообщении или звонке.</small></div></article>
      </div>
      <div className={styles.onboardingActions}>
        <a href={guideHref} target="_blank" rel="noreferrer">Открыть инструкцию</a>
        <button type="button" onClick={onFinishOnboarding}>Перейти к работе</button>
      </div>
    </section>}
    <div className={styles.settingsColumns}>
      <div className={styles.settingsColumn}>
        <section className={[styles.settingsCard,styles.settingsCardAppearance].join(" ")}>
          <div className={styles.settingsCardHead}><span className={styles.settingsIcon}><Palette size={20}/></span><div><strong>Внешний вид</strong><small>Тема и акцент Messenger</small></div></div>
          <div className={styles.settingsField}><span>Тема</span><div className={styles.settingsSegmented}>{([["light","Светлая"],["dark","Тёмная"],["system","Системная"]] as const).map(([v,l])=><button type="button" key={v} className={theme===v?styles.settingsSegmentActive:""} onClick={()=>setTheme(v)} aria-pressed={theme===v}>{l}</button>)}</div></div>
          <div className={styles.settingsField}><span>Акцент</span><div className={styles.accentChoices}>{([["blue","Синий"],["graphite","Графит"],["sky","Светло-синий"]] as const).map(([v,l])=><button type="button" key={v} className={[styles.accentChoice,accent===v?styles.accentChoiceActive:""].filter(Boolean).join(" ")} data-accent={v} onClick={()=>setAccent(v)} aria-pressed={accent===v}><i/><span>{l}</span></button>)}</div></div>
          <div className={styles.themePreview}><div><span>Сотрудник</span><p>Добрый день! Проверил задачу.</p></div><div data-own="true"><span>Вы</span><p>Отлично, спасибо.</p></div></div>
        </section>

        <section className={styles.settingsCard}>
          <div className={styles.settingsCardHead}><span className={styles.settingsIcon}><MonitorSmartphone size={20}/></span><div><strong>Приложение BORIS Messenger</strong><small>{installedApp?"Открывается как отдельное приложение":"Сейчас работает в браузере"}</small></div></div>
          <div className={styles.settingsStateRow}><span className={styles.settingsStatus} data-state={installedApp?"on":"off"}>{installedApp?"Установлено":"Не установлено"}</span>{!installedApp&&<button type="button" className={styles.settingsPrimaryButton} onClick={installApp}>{installLabel}</button>}</div>
          {hasUpdate&&<div className={styles.settingsUpdate}><div><strong>Доступна новая версия</strong><small>Обновление установится без переустановки приложения.</small></div><button type="button" onClick={applyUpdate}>Обновить BORIS</button></div>}
        </section>
      </div>

      <div className={styles.settingsColumn}>
        <section className={styles.settingsCard}>
          <div className={styles.settingsCardHead}><span className={styles.settingsIcon}><Bell size={20}/></span><div><strong>Push-уведомления</strong><small>Сообщения, задачи и важные события BORIS</small></div></div>
          <div className={styles.settingsStateRow}><span className={styles.settingsStatus} data-state={pushStateKey}>{pushLabel}</span>{pushState==="on"?<button type="button" className={styles.settingsGhostButton} onClick={disablePush}>Выключить</button>:pushState!=="unsupported"&&pushState!=="denied"?<button type="button" className={styles.settingsPrimaryButton} onClick={enablePush}>Включить</button>:null}</div>
          {pushState==="denied"&&<p className={styles.settingsHint}>Разрешите уведомления в настройках браузера.</p>}
          {showIosTip&&<p className={styles.settingsHint}>На iPhone сначала установите BORIS Messenger на экран «Домой» и откройте его с синей иконки.</p>}
        </section>

        <section className={styles.settingsCard}>
          <div className={styles.settingsCardHead}><span className={styles.settingsIcon}><MonitorSmartphone size={20}/></span><div><strong>Разрешения устройства</strong><small>Для голосовых, видео и звонков</small></div></div>
          <div className={styles.settingsStateRow}><span><strong>Микрофон</strong><small> Нужен для голосовых сообщений и звонков.</small></span><span className={styles.settingsStatus} data-state={permissionState(micPermission)}>{permissionLabel(micPermission)}</span></div>
          <div className={styles.settingsStateRow}><span><strong>Камера</strong><small> Нужна для видеосообщений и видеозвонков.</small></span><span className={styles.settingsStatus} data-state={permissionState(cameraPermission)}>{permissionLabel(cameraPermission)}</span></div>
          <div className={styles.settingsStateRow}><span><strong>Уведомления</strong><small> Нужны, чтобы не пропускать сообщения и звонки.</small></span><span className={styles.settingsStatus} data-state={pushStateKey}>{pushState==="on"?"Разрешены":pushState==="denied"?"Заблокированы":"Не запрошены"}</span></div>
          {mediaBlocked&&<p className={styles.settingsHint}>{permissionHelp}</p>}
        </section>

        <section className={styles.settingsCard}>
          <div className={styles.settingsCardHead}><span className={styles.settingsIcon}><UsersRound size={20}/></span><div><strong>Доступ к аккаунтам</strong><small>{accounts.length} {accounts.length===1?"доступный аккаунт":"доступных аккаунтов"}</small></div></div>
          <button type="button" className={styles.settingsLinkRow} onClick={()=>setAccessOpen(true)} aria-haspopup="dialog"><span><strong>{current?.name||"Текущий аккаунт"}</strong><small>{current?.role==="OWNER"?"Владелец":"Рабочий доступ"}</small></span><ChevronRight size={18}/></button>
        </section>
      </div>
    </div>

    <section className={styles.settingsWideCard}>
      <div className={styles.settingsCardHead}><span className={styles.settingsIcon}><ShieldCheck size={20}/></span><div><strong>Доступ и безопасность</strong><small>Устройства, активные сеансы и документы Messenger</small></div></div>
      <button type="button" className={styles.settingsLinkRow} onClick={onOpenSecurity}><span><strong>Открыть безопасность и документы</strong><small>Сессии, пользовательское соглашение и политика конфиденциальности</small></span><ChevronRight size={18}/></button>
    </section>

    <section className={styles.settingsWideCard}>
      <div className={styles.settingsCardHead}><span className={styles.settingsIcon}><MonitorSmartphone size={20}/></span><div><strong>Инструкция для сотрудников</strong><small>Установка, Push, лиды, CRM, задачи, чаты и звонки</small></div></div>
      <a className={styles.settingsLinkRow} href={guideHref} target="_blank" rel="noreferrer"><span><strong>Открыть полную инструкцию</strong><small>iPhone, Android, Windows и Mac</small></span><ChevronRight size={18}/></a>
    </section>

    {accessOpen&&<div className={styles.accessBackdrop} onMouseDown={e=>{if(e.target===e.currentTarget)setAccessOpen(false);}}><aside className={styles.accessDrawer} role="dialog" aria-modal="true" aria-label="Доступ к аккаунтам"><div className={styles.accessDrawerHead}><div><strong>Доступ к аккаунтам</strong><small>Права этого пользователя</small></div><button type="button" onClick={()=>setAccessOpen(false)} aria-label="Закрыть"><X size={17}/></button></div><div className={styles.accessAccountList}>{accounts.map(a=><article key={a.account_id}><div><strong>{a.name||a.account_id}</strong><small>{a.role==="OWNER"?"Владелец":a.role==="MANAGER_LEAD"?"Старший менеджер":a.role==="MANAGER"?"Менеджер":a.role==="VIEWER"?"Наблюдатель":"Сотрудник"}</small></div><div className={styles.permissionChips}>{a.can_reply&&<span>Можно отвечать</span>}{a.can_manage_team&&<span>Можно управлять командой</span>}{a.can_view_reports!==false&&<span>Можно смотреть отчёты</span>}</div></article>)}</div></aside></div>}
  </div>;
}
