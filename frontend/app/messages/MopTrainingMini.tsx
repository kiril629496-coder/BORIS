"use client";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import {
  Alert,
  Badge,
  Button,
} from "../ui";

import {
  getToken,
} from "../lib/api";


type TrainingMessage = {
  role: "client" | "manager";
  text: string;
};


type TrainingUsage = {
  calls?: number;
  input_tokens?: number;
  cached_input_tokens?: number;
  output_tokens?: number;
  cost_rub?: number;
};


type Training = {
  session_id: string;
  account_id: string;
  status: string;
  scenario: string;

  messages: TrainingMessage[];

  manager_turns: number;
  max_manager_turns: number;

  outcome?: string | null;

  usage?: TrainingUsage;

  review?: any;
};


function authHeaders() {
  const token = getToken();

  return {
    Authorization:
      "Bearer " + (token || ""),

    "Content-Type":
      "application/json",
  };
}


export default function MopTrainingMini(
  {
    accountId,
  }: {
    accountId: string;
  }
) {
  const [
    entitlement,
    setEntitlement,
  ] = useState<
    "loading" |
    "active" |
    "locked"
  >("loading");

  const [
    reason,
    setReason,
  ] = useState("");

  const [
    expanded,
    setExpanded,
  ] = useState(false);

  const [
    training,
    setTraining,
  ] = useState<Training | null>(
    null
  );

  const [
    draft,
    setDraft,
  ] = useState("");

  const [
    busy,
    setBusy,
  ] = useState(false);

  const [
    note,
    setNote,
  ] = useState("");

  const bottomRef =
    useRef<HTMLDivElement>(
      null
    );


  useEffect(() => {
    if (!accountId) {
      setEntitlement(
        "loading"
      );

      return;
    }

    setEntitlement(
      "loading"
    );

    setTraining(
      null
    );

    setExpanded(
      false
    );

    fetch(
      "/api/mop-training/status?account_id="
      + encodeURIComponent(
          accountId
        ),
      {
        headers:
          authHeaders(),
      }
    )
      .then(
        async (r) => {
          const j =
            await r.json();

          if (
            r.ok
          ) {
            // BORIS_MOP_TRAINING_FREE_ACCESS:
            // training is available to every valid account.
            // This does NOT unlock the paid combat MOP.
            setEntitlement(
              "active"
            );
          } else {
            setEntitlement(
              "locked"
            );

            setReason(
              j.reason || ""
            );
          }
        }
      )
      .catch(
        () => {
          setEntitlement(
            "locked"
          );

          setReason(
            "не удалось проверить пакет"
          );
        }
      );

  }, [accountId]);


  useEffect(() => {
    bottomRef.current
      ?.scrollIntoView({
        behavior: "smooth",
      });

  }, [
    training?.messages?.length,
  ]);


  async function start() {
    if (
      !accountId ||
      busy
    ) {
      return;
    }

    setBusy(true);
    setNote("");

    try {
      const r =
        await fetch(
          "/api/mop-training/start",
          {
            method: "POST",

            headers:
              authHeaders(),

            body:
              JSON.stringify({
                account_id:
                  accountId,
              }),
          }
        );

      const j =
        await r.json();

      if (!r.ok) {
        setNote(
          typeof j.detail
            === "string"
            ? j.detail
            : JSON.stringify(
                j.detail
              )
        );

        return;
      }

      setTraining(
        j.training
      );

      setExpanded(
        true
      );

    } catch {
      setNote(
        "Не удалось начать тренировку"
      );

    } finally {
      setBusy(false);
    }
  }


  async function sendReply() {
    if (
      !training ||
      !draft.trim() ||
      busy
    ) {
      return;
    }

    const text =
      draft.trim();

    setDraft("");
    setNote("");
    setBusy(true);

    try {
      const r =
        await fetch(
          "/api/mop-training/reply",
          {
            method: "POST",

            headers:
              authHeaders(),

            body:
              JSON.stringify({
                account_id:
                  accountId,

                session_id:
                  training.session_id,

                text,
              }),
          }
        );

      const j =
        await r.json();

      if (!r.ok) {
        setNote(
          typeof j.detail
            === "string"
            ? j.detail
            : JSON.stringify(
                j.detail
              )
        );

        return;
      }

      setTraining(
        j.training
      );

    } catch {
      setNote(
        "Ошибка связи с ИИ-клиентом"
      );

    } finally {
      setBusy(false);
    }
  }


  async function finish() {
    if (
      !training ||
      busy
    ) {
      return;
    }

    setNote("");
    setBusy(true);

    try {
      const r =
        await fetch(
          "/api/mop-training/finish",
          {
            method: "POST",

            headers:
              authHeaders(),

            body:
              JSON.stringify({
                account_id:
                  accountId,

                session_id:
                  training.session_id,
              }),
          }
        );

      const j =
        await r.json();

      if (!r.ok) {
        setNote(
          typeof j.detail
            === "string"
            ? j.detail
            : JSON.stringify(
                j.detail
              )
        );

        return;
      }

      setTraining(
        j.training
      );

      setExpanded(
        true
      );

    } catch {
      setNote(
        "Не удалось выполнить разбор РОПа"
      );

    } finally {
      setBusy(false);
    }
  }


  const usage =
    training?.usage || {};


  return (
    <div
      style={{
        border:
          "1px solid #DCE6F8",

        borderRadius:
          12,

        marginBottom:
          12,

        background:
          "#FFFFFF",

        overflow:
          "hidden",
      }}
    >

      <div
        style={{
          display:
            "flex",

          alignItems:
            "center",

          justifyContent:
            "space-between",

          gap:
            10,

          flexWrap:
            "wrap",

          padding:
            "11px 13px",

          background:
            "#F7FAFF",
        }}
      >

        <div>

          <div
            style={{
              display:
                "flex",

              alignItems:
                "center",

              gap:
                7,

              flexWrap:
                "wrap",
            }}
          >

            <b>
              🎯 Тренировка МОПа
            </b>

            {entitlement
              === "active"
              ? (
                <Badge
                  kind="success"
                >
                  ИИ-клиент
                </Badge>
              )
              : entitlement
                === "locked"
                ? (
                  <Badge>
                    🔒 пакет МОП
                  </Badge>
                )
                : (
                  <Badge>
                    проверяем
                  </Badge>
                )
            }

          </div>

          <div
            style={{
              fontSize:
                11,

              color:
                "#667085",

              marginTop:
                3,
            }}
          >
            Живой учебный диалог,
            а не проверка
            по ключевым словам
          </div>

        </div>


        <div
          style={{
            display:
              "flex",

            gap:
              7,

            flexWrap:
              "wrap",
          }}
        >

          {!training
            ? (
              <Button
                disabled={
                  entitlement
                    !== "active"
                  ||
                  busy
                }

                onClick={
                  start
                }
              >
                Начать
              </Button>
            )
            : (
              <>
                <Button
                  kind="ghost"

                  onClick={
                    () =>
                      setExpanded(
                        !expanded
                      )
                  }
                >
                  {expanded
                    ? "Свернуть"
                    : "Открыть"
                  }
                </Button>


                {training.status
                  !== "finished"
                  ? (
                    <Button
                      disabled={
                        busy
                      }

                      onClick={
                        finish
                      }
                    >
                      Завершить и разобрать
                    </Button>
                  )
                  : (
                    <Button
                      onClick={
                        () => {
                          setTraining(
                            null
                          );

                          setExpanded(
                            false
                          );
                        }
                      }
                    >
                      Новая
                    </Button>
                  )
                }
              </>
            )
          }

        </div>

      </div>


      {entitlement
        === "locked"
        ? (
          <div
            style={{
              padding:
                "9px 13px",

              color:
                "#667085",

              fontSize:
                12,
            }}
          >
            Тренировка доступна
            только при активном
            пакете МОП.
            {reason
              ? " · " + reason
              : ""
            }
          </div>
        )
        : null
      }


      {note
        ? (
          <div
            style={{
              padding:
                "9px 12px",
            }}
          >
            <Alert
              kind="warning"
            >
              {note}
            </Alert>
          </div>
        )
        : null
      }


      {expanded &&
       training
        ? (
          <div
            style={{
              borderTop:
                "1px solid #EAECF0",

              padding:
                12,
            }}
          >

            <div
              style={{
                fontSize:
                  11,

                color:
                  "#667085",

                marginBottom:
                  9,
              }}
            >
              Учебный клиент ·
              сообщения не уходят
              реальному человеку ·{" "}
              {training.manager_turns}
              /
              {training.max_manager_turns}
            </div>


            <div
              style={{
                padding:
                  10,

                borderRadius:
                  10,

                background:
                  "#F9FAFB",

                fontSize:
                  12,

                marginBottom:
                  10,
              }}
            >
              <b>
                Ситуация:
              </b>{" "}
              {training.scenario}
            </div>


            <div
              style={{
                maxHeight:
                  380,

                overflowY:
                  "auto",

                padding:
                  10,

                background:
                  "#F6F8FC",

                borderRadius:
                  10,
              }}
            >

              {training.messages.map(
                (
                  message,
                  i
                ) => (

                  <div
                    key={i}

                    style={{
                      display:
                        "flex",

                      justifyContent:
                        message.role
                          === "manager"
                          ? "flex-end"
                          : "flex-start",

                      marginBottom:
                        8,
                    }}
                  >

                    <div
                      style={{
                        maxWidth:
                          "80%",

                        padding:
                          "9px 11px",

                        borderRadius:
                          11,

                        background:
                          message.role
                            === "manager"
                            ? "#DDEBFF"
                            : "#FFFFFF",

                        border:
                          "1px solid #E4E7EC",

                        whiteSpace:
                          "pre-wrap",

                        lineHeight:
                          1.45,

                        fontSize:
                          13,
                      }}
                    >
                      {message.text}
                    </div>

                  </div>

                )
              )}

              {busy &&
               training.status
                 !== "finished"
                ? (
                  <div
                    style={{
                      color:
                        "#667085",

                      fontSize:
                        12,

                      padding:
                        5,
                    }}
                  >
                    Клиент думает…
                  </div>
                )
                : null
              }

              <div
                ref={
                  bottomRef
                }
              />

            </div>


            {training.status
              !== "finished"
              ? (
                <div
                  style={{
                    display:
                      "flex",

                    gap:
                      8,

                    alignItems:
                      "stretch",

                    marginTop:
                      9,
                  }}
                >

                  <textarea
                    value={
                      draft
                    }

                    onChange={
                      (e) =>
                        setDraft(
                          e.target.value
                        )
                    }

                    onKeyDown={
                      (e) => {
                        if (
                          e.key
                            === "Enter"
                          &&
                          !e.shiftKey
                        ) {
                          e.preventDefault();

                          sendReply();
                        }
                      }
                    }

                    placeholder=
                      "Ответьте учебному клиенту..."

                    style={{
                      flex:
                        1,

                      minHeight:
                        70,

                      border:
                        "1px solid #D0D5DD",

                      borderRadius:
                        9,

                      padding:
                        9,

                      resize:
                        "vertical",

                      fontFamily:
                        "inherit",
                    }}
                  />

                  <Button
                    disabled={
                      busy
                      ||
                      !draft.trim()
                    }

                    onClick={
                      sendReply
                    }
                  >
                    Отправить
                  </Button>

                </div>
              )
              : null
            }


            {training.review
              ? (
                <div
                  style={{
                    marginTop:
                      13,

                    padding:
                      14,

                    border:
                      "1px solid #DCE6F8",

                    borderRadius:
                      11,

                    background:
                      "#FFFFFF",
                  }}
                >

                  <div
                    style={{
                      fontSize:
                        17,

                      fontWeight:
                        850,
                    }}
                  >
                    Разбор РОПа ·{" "}
                    {training.review
                      .score ?? "—"}
                    /100
                  </div>


                  <div
                    style={{
                      marginTop:
                        7,

                      lineHeight:
                        1.5,

                      fontSize:
                        13,
                    }}
                  >
                    {training.review
                      .summary}
                  </div>


                  {training.review
                    .main_thinking_error
                    ? (
                      <div
                        style={{
                          marginTop:
                            11,
                        }}
                      >
                        <b>
                          Главная ошибка
                          мышления:
                        </b>
                        <br />
                        {training.review
                          .main_thinking_error}
                      </div>
                    )
                    : null
                  }


                  {training.review
                    .better_thinking
                    ? (
                      <div
                        style={{
                          marginTop:
                            9,
                        }}
                      >
                        <b>
                          Как стоило
                          мыслить:
                        </b>
                        <br />
                        {training.review
                          .better_thinking}
                      </div>
                    )
                    : null
                  }


                  {training.review
                    .turning_point
                    ?.why_it_mattered
                    ? (
                      <div
                        style={{
                          marginTop:
                            9,
                        }}
                      >
                        <b>
                          Переломный
                          момент:
                        </b>
                        <br />
                        {training.review
                          .turning_point
                          .why_it_mattered}
                      </div>
                    )
                    : null
                  }


                  {training.review
                    .example_better_reply
                    ? (
                      <div
                        style={{
                          marginTop:
                            9,

                          padding:
                            10,

                          borderRadius:
                            9,

                          background:
                            "#F5F8FF",
                        }}
                      >
                        <b>
                          Сильнее можно
                          было ответить:
                        </b>
                        <br />
                        {training.review
                          .example_better_reply}
                      </div>
                    )
                    : null
                  }


                  {training.review
                    .next_training_focus
                    ? (
                      <div
                        style={{
                          marginTop:
                            10,
                        }}
                      >
                        <b>
                          Следующий
                          навык:
                        </b>{" "}
                        {training.review
                          .next_training_focus}
                      </div>
                    )
                    : null
                  }


                  <div
                    style={{
                      marginTop:
                        12,

                      borderTop:
                        "1px solid #EAECF0",

                      paddingTop:
                        9,

                      fontSize:
                        11,

                      color:
                        "#667085",
                    }}
                  >
                    AI вызовов:{" "}
                    {usage.calls || 0}
                    {" · "}

                    input:{" "}
                    {usage.input_tokens || 0}
                    {" · "}

                    cached:{" "}
                    {usage.cached_input_tokens || 0}
                    {" · "}

                    output:{" "}
                    {usage.output_tokens || 0}
                    {" · "}

                    себестоимость:{" "}
                    <b>
                      {Number(
                        usage.cost_rub || 0
                      ).toFixed(2)}
                      ₽
                    </b>
                  </div>

                </div>
              )
              : null
            }

          </div>
        )
        : null
      }

    </div>
  );
}
