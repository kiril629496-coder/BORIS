"use client";

import styles from "./guide.module.css";

const devices = [
  {
    title: "iPhone",
    image: "/messenger-guide/iphone.jpg",
    steps: ["Откройте BORIS в Safari.", "Нажмите «Поделиться».", "Выберите «На экран Домой».", "Откройте BORIS с новой синей иконки.", "Разрешите Push. Микрофон и камера будут запрошены при первом использовании."],
  },
  {
    title: "Android",
    image: "/messenger-guide/android.jpg",
    steps: ["Откройте BORIS в Chrome.", "Нажмите меню ⋮.", "Выберите «Установить приложение» или «Добавить на главный экран».", "Откройте установленный BORIS.", "Разрешите Push, микрофон и камеру по запросу."],
  },
  {
    title: "Windows",
    image: "/messenger-guide/windows.jpg",
    steps: ["Откройте BORIS в Chrome или Edge.", "Нажмите значок установки в адресной строке или откройте меню браузера.", "Выберите установку сайта как приложения.", "При желании закрепите BORIS на панели задач.", "Разрешите уведомления, микрофон и камеру."],
  },
  {
    title: "Mac",
    image: "/messenger-guide/mac.jpg",
    steps: ["Откройте BORIS в Chrome или Safari.", "В Chrome выберите установку страницы как приложения. В Safari — добавление веб-приложения в Dock.", "Откройте BORIS как отдельное приложение.", "Разрешите уведомления.", "При первом голосовом/видеозвонке разрешите микрофон и камеру."],
  },
];

export default function MessengerGuidePage() {
  return (
    <main className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.logo}>B</div>
        <div>
          <span>Инструкция для сотрудников</span>
          <h1>BORIS Messenger</h1>
          <p>Единое рабочее приложение для общения команды, новых лидов, CRM-задач, напоминаний, голосовых и видеозвонков.</p>
        </div>
        <div className={styles.heroActions}>
          <a href="/messenger" className={styles.primary}>Открыть Messenger</a>
          <button type="button" className={styles.secondary} onClick={() => window.print()}>Распечатать / сохранить PDF</button>
          <a href="/messenger-guide/BORIS-Messenger-Instruction.docx" className={styles.secondary} download>Скачать Word</a>
        </div>
      </header>

      <section className={styles.quick}>
        <article><b>1</b><div><strong>Получите приглашение</strong><p>Каждому сотруднику нужен свой персональный доступ. Общий логин на команду не используйте.</p></div></article>
        <article><b>2</b><div><strong>Примите приглашение и войдите</strong><p>Укажите рабочий email, придумайте пароль, примите актуальные документы BORIS и войдите.</p></div></article>
        <article><b>3</b><div><strong>Установите приложение</strong><p>После первого входа BORIS сам откроет экран установки и Push-уведомлений.</p></div></article>
        <article><b>4</b><div><strong>Начинайте работу</strong><p>Чаты команды, клиенты, CRM, задачи, напоминания и звонки доступны в одном интерфейсе.</p></div></article>
      </section>

      <section className={styles.block}>
        <div className={styles.blockTitle}>
          <span>Рабочий сценарий</span>
          <h2>Что сотрудник получает в BORIS</h2>
        </div>
        <div className={styles.flow}>
          <article><strong>Новый лид</strong><p>BORIS показывает новое обращение и помогает не оставить клиента без ответа.</p></article>
          <span>→</span>
          <article><strong>Быстрый ответ</strong><p>Откройте клиентский диалог, ответьте сами или продолжите работу после BORIS AI.</p></article>
          <span>→</span>
          <article><strong>CRM и следующий шаг</strong><p>Зафиксируйте задачу: перезвонить, получить размеры, отправить расчёт, назначить замер.</p></article>
          <span>→</span>
          <article><strong>Напоминание</strong><p>Просроченные и сегодняшние задачи видны в CRM, важные события приходят Push-уведомлениями.</p></article>
        </div>
      </section>

      <section className={styles.features}>
        <article><h3>Клиенты и лиды</h3><p>Входящие обращения, непрочитанные диалоги, ответственный сотрудник, режим HUMAN/BORIS и быстрый переход к работе.</p></article>
        <article><h3>CRM и задачи</h3><p>Задачи на сегодня, просроченные, запланированные и выполненные. У каждого клиента — понятный следующий шаг.</p></article>
        <article><h3>Командные чаты</h3><p>Общий чат, личные диалоги и рабочие группы. Можно отправлять файлы, голосовые и видеосообщения.</p></article>
        <article><h3>Звонки</h3><p>Личные и групповые аудио- и видеозвонки. В рабочей группе можно созвониться командой до 8 участников. При кратком обрыве сети BORIS пытается восстановить соединение.</p></article>
        <article><h3>Push-уведомления</h3><p>Новые сообщения, рабочие задачи, важные события и входящие звонки. Push включается отдельно на каждом устройстве.</p></article>
        <article><h3>Безопасность</h3><p>У каждого сотрудника отдельная учётная запись и роль. Доступ можно отозвать, а активные сеансы — контролировать.</p></article>
      </section>

      <section className={styles.block}>
        <div className={styles.blockTitle}>
          <span>Установка</span>
          <h2>iPhone, Android, Windows и Mac</h2>
          <p>Интерфейс один и тот же. Отличается только способ установки PWA.</p>
        </div>
        <div className={styles.devices}>
          {devices.map(device => (
            <article key={device.title} className={styles.deviceCard}>
              <img src={device.image} alt={"BORIS Messenger — " + device.title} />
              <div>
                <h3>{device.title}</h3>
                <ol>{device.steps.map(step => <li key={step}>{step}</li>)}</ol>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className={styles.block}>
        <div className={styles.blockTitle}><span>Каждый день</span><h2>Как не терять клиентов</h2></div>
        <div className={styles.rules}>
          <p><strong>Новый лид не должен остаться без ответа.</strong> Если клиент написал — откройте диалог и убедитесь, что есть ответ или понятный следующий шаг.</p>
          <p><strong>Не держите обещания в голове.</strong> Если нужно перезвонить позже — создайте или проверьте CRM-задачу.</p>
          <p><strong>Проверяйте просроченные задачи.</strong> Раздел CRM показывает, где требуется действие сотрудника.</p>
          <p><strong>Групповой звонок.</strong> Откройте рабочую группу и нажмите кнопку аудио- или видеозвонка. BORIS пригласит участников группы, а занятых сотрудников пропустит.</p>
          <p><strong>Не используйте общий логин.</strong> У каждого сотрудника должен быть собственный доступ BORIS.</p>
        </div>
      </section>

      <section className={styles.help}>
        <div><span>Если что-то не работает</span><h2>Быстрая проверка</h2></div>
        <ul>
          <li>Обновите страницу или полностью перезапустите установленный BORIS.</li>
          <li>Проверьте интернет/VPN.</li>
          <li>Убедитесь, что браузер разрешил уведомления, микрофон и камеру.</li>
          <li>Если доступ отозван или приглашение истекло — обратитесь к владельцу/администратору компании.</li>
        </ul>
        <a href="/messenger" className={styles.primary}>Перейти в BORIS Messenger</a>
      </section>
    </main>
  );
}
