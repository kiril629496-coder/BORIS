"use client";

import React from "react";
import { Button } from "@/app/ui";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

type ScenarioGuideProps = {
  scenarioId: string | number;
  scenarioType?: string;
  step?: any;
  workflow?: any;
  allDone?: boolean;
  onRefresh: () => void;
  onDoNow?: () => void;
};

export default function ScenarioGuide({
  scenarioId,
  scenarioType,
  step,
  workflow,
  allDone,
  onRefresh,
  onDoNow,
}: ScenarioGuideProps) {
  const phase = allDone
    ? "done"
    : step?.owner_required
      ? "owner"
      : step?.auto
        ? "auto"
        : "manual";
  const stepKey = String(step?.key || "result");
  const storageKey =
    "boris_scenario_guide_seen:" +
    String(scenarioId) +
    ":" +
    stepKey +
    ":" +
    phase;

  const [ready, setReady] = React.useState(false);
  const [visible, setVisible] = React.useState(false);

  React.useEffect(() => {
    try {
      setVisible(!localStorage.getItem(storageKey));
    } catch {
      setVisible(true);
    }
    setReady(true);
  }, [storageKey]);

  const confirm = React.useCallback(() => {
    try {
      localStorage.setItem(storageKey, "1");
    } catch {}
    setVisible(false);
  }, [storageKey]);

  if (!ready || !visible) return null;

  let title = "Следующий шаг";
  let text = humanizeBusinessText(
    step?.hint || workflow?.next_action,
    "BORIS показывает следующий шаг сценария."
  );
  let primary = "Продолжить";
  let primaryAction = onDoNow || onRefresh;

  if (allDone) {
    title = "Результат готов";
    text = humanizeBusinessText(
      workflow?.result,
      "Сценарий дошёл до результата. Проверьте итог и следующий рекомендуемый шаг."
    );
    primary = "Посмотреть результат";
    primaryAction = onRefresh;
  } else if (step?.owner_required) {
    title = "Нужно ваше действие";
    text = humanizeBusinessText(
      step?.hint || workflow?.owner_required,
      "BORIS дошёл до шага, где требуется одно конкретное решение владельца."
    );
    primary = "Сделать сейчас";
    primaryAction = onDoNow || onRefresh;
  } else if (step?.auto) {
    title = "BORIS делает сам";
    text = humanizeBusinessText(
      step?.hint || workflow?.current_work,
      "Этот шаг автоматический. Вам ничего выполнять вручную не нужно."
    );
    primary = "Проверить статус";
    primaryAction = onRefresh;
  }

  return (
    <section
      data-testid="scenario-guide"
      data-guide-key={storageKey}
      data-guide-scenario={scenarioType || ""}
      style={{
        margin: "0 0 18px",
        border: "1px solid var(--boris-border)",
        borderRadius: 16,
        background: "#F7FAFF",
        padding: "16px 18px",
        boxShadow: "0 8px 24px rgba(52,128,254,.08)",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div
          aria-hidden="true"
          style={{
            width: 36,
            height: 36,
            borderRadius: 11,
            background: "var(--boris-blue)",
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flex: "0 0 auto",
            fontWeight: 800,
          }}
        >
          B
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: "var(--boris-blue)" }}>
            BORIS Guide
          </div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "var(--boris-text)", marginTop: 2 }}>
            {title}
          </div>
          <div
            data-testid="scenario-guide-text"
            style={{
              fontSize: 14,
              lineHeight: 1.55,
              color: "var(--boris-text-2)",
              marginTop: 6,
            }}
          >
            {text}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 14 }}>
            <Button
              data-testid="scenario-guide-primary"
              onClick={() => primaryAction()}
            >
              {primary}
            </Button>
            <Button
              data-testid="scenario-guide-later"
              onClick={() => setVisible(false)}
            >
              Позже
            </Button>
            <Button
              data-testid="scenario-guide-confirm"
              onClick={confirm}
            >
              Понятно
            </Button>
          </div>
          <div style={{ fontSize: 12, color: "var(--boris-text-2)", marginTop: 9 }}>
            После «Понятно» эта подсказка не повторится на этом шаге. На новом этапе BORIS покажет следующую.
          </div>
        </div>
      </div>
    </section>
  );
}
