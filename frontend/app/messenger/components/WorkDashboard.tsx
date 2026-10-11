"use client";

import styles from "../messenger.module.css";

export type WorkUrgentTask = {
  id: number;
  title: string;
  due: string;
  status: string;
  statusLabel: string;
};

type Props = {
  clientCount: number;
  humanCount: number;
  aiDraftCount: number;
  activeCount: number;
  urgentTasks: WorkUrgentTask[];
  urgentTotal: number;
  canManageTeam: boolean;
  onOpenInbox: () => void;
  onOpenHuman: () => void;
  onOpenAi: () => void;
  onOpenTask: (taskId: number) => void;
  onOpenCrm: () => void;
  onOpenGeneral: () => void;
  onOpenTeam: () => void;
};

export default function WorkDashboard({
  clientCount,
  humanCount,
  aiDraftCount,
  activeCount,
  urgentTasks,
  urgentTotal,
  canManageTeam,
  onOpenInbox,
  onOpenHuman,
  onOpenAi,
  onOpenTask,
  onOpenCrm,
  onOpenGeneral,
  onOpenTeam,
}: Props) {
  return <>
    <div className={styles.workHero}>
      <div>
        <span className={styles.kicker}>СЕГОДНЯ</span>
        <h2>Что требует внимания</h2>
        <p>Сразу переходите к клиенту, AI-черновику или CRM-задаче — без поиска по разделам.</p>
      </div>
      <span className={styles.workPulse}>{activeCount} активных действий</span>
    </div>

    <div className={styles.cardGrid}>
      <button className={styles.metricAction} onClick={onOpenInbox}>
        <span>Клиентские диалоги</span>
        <strong>{clientCount}</strong>
        <small>Открыть Inbox →</small>
      </button>
      <button className={styles.metricAction} onClick={onOpenHuman}>
        <span>Ведёт сотрудник</span>
        <strong>{humanCount}</strong>
        <small>Проверить HUMAN-диалоги →</small>
      </button>
      <button className={styles.metricAction} onClick={onOpenAi}>
        <span>AI ждёт решения</span>
        <strong>{aiDraftCount}</strong>
        <small>Проверить черновики →</small>
      </button>
    </div>

    <div className={styles.workQueue}>
      <div className={styles.taskTitle}>
        <strong>Срочные CRM-задачи</strong>
        <span>{urgentTotal}</span>
      </div>
      {urgentTasks.map(task => (
        <button
          type="button"
          className={styles.taskQueueItem}
          key={task.id}
          onClick={() => onOpenTask(task.id)}
        >
          <span>
            <strong>{task.title}</strong>
            <small>{task.due}</small>
          </span>
          <span className={styles.taskStatus} data-status={task.status}>{task.statusLabel}</span>
        </button>
      ))}
      {!urgentTasks.length && <small>Срочных CRM-задач нет.</small>}
    </div>

    <div className={styles.workAttention}>
      <div>
        <strong>Быстрые действия</strong>
        <small>Главные рабочие сценарии владельца и менеджера</small>
      </div>
      <div className={styles.workActions}>
        <button onClick={onOpenInbox}>Ответить клиенту</button>
        <button onClick={onOpenCrm}>Открыть CRM</button>
        <button onClick={onOpenGeneral}>Общий чат команды</button>
        {canManageTeam && <button onClick={onOpenTeam}>Управление командой</button>}
      </div>
    </div>
  </>;
}
