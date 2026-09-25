"use client";

import React from "react";

type Props = {
  accountId?: string;
  accountName?: string;
  chatId?: string;
};

function buildQuery(
  accountId?: string,
  chatId?: string,
) {
  const q = new URLSearchParams();

  if (
    accountId &&
    accountId !== "all"
  ) {
    q.set(
      "account_id",
      accountId,
    );
  }

  if (chatId) {
    q.set(
      "chat_id",
      chatId,
    );
  }

  const value = q.toString();

  return value
    ? `?${value}`
    : "";
}

const card: React.CSSProperties = {
  border:
    "1px solid #e3e8f2",
  borderRadius: 18,
  background: "#ffffff",
  overflow: "hidden",
  boxShadow:
    "0 8px 24px rgba(24,39,75,.06)",
};

const iframe: React.CSSProperties = {
  width: "100%",
  height: 540,
  border: 0,
  display: "block",
  background: "#ffffff",
};

export default function BorisMessageWorkspaceModules({
  accountId,
  accountName,
  chatId,
}: Props) {
  const query = buildQuery(
    accountId,
    chatId,
  );

  return (
    <section
      id="boris-r171-workspace"
      data-boris-r171="workspace"
      style={{
        marginTop: 18,
        padding: 18,
        borderRadius: 22,
        border:
          "1px solid #dfe6f2",
        background:
          "linear-gradient(180deg,#f8fbff 0%,#ffffff 100%)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent:
            "space-between",
          gap: 16,
          marginBottom: 14,
        }}
      >
        <div>
          <div
            style={{
              fontSize: 11,
              fontWeight: 900,
              letterSpacing: 1,
              color: "#2f7cff",
              textTransform:
                "uppercase",
            }}
          >
            BORIS · ЕДИНОЕ ОКНО
          </div>

          <div
            style={{
              marginTop: 4,
              fontSize: 21,
              fontWeight: 850,
              color: "#172033",
            }}
          >
            Рабочее место менеджера
          </div>

          <div
            style={{
              marginTop: 4,
              fontSize: 13,
              color: "#7b8496",
            }}
          >
            {accountName ||
              "Все подключенные аккаунты"}
            {chatId
              ? " · выбран диалог"
              : " · диалог не выбран"}
          </div>
        </div>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent:
              "flex-end",
            gap: 7,
          }}
        >
          <span
            style={{
              padding:
                "7px 10px",
              borderRadius: 999,
              background: "#edf5ff",
              color: "#2f7cff",
              fontSize: 11,
              fontWeight: 800,
            }}
          >
            CRM
          </span>

          <span
            style={{
              padding:
                "7px 10px",
              borderRadius: 999,
              background: "#fff0f5",
              color: "#d93672",
              fontSize: 11,
              fontWeight: 800,
            }}
          >
            MOP COMBAT
          </span>

          <span
            style={{
              padding:
                "7px 10px",
              borderRadius: 999,
              background: "#effbf5",
              color: "#169b6b",
              fontSize: 11,
              fontWeight: 800,
            }}
          >
            ГАЛЕРЕЯ
          </span>

          <span
            style={{
              padding:
                "7px 10px",
              borderRadius: 999,
              background: "#f4f0ff",
              color: "#7252dc",
              fontSize: 11,
              fontWeight: 800,
            }}
          >
            НАСТРОЙКИ
          </span>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            "repeat(4,minmax(0,1fr))",
          gap: 10,
          marginBottom: 14,
        }}
      >
        <a
          href="#boris-crm"
          style={{
            ...card,
            padding: 15,
            textDecoration:
              "none",
          }}
        >
          <div
            style={{
              fontSize: 23,
            }}
          >
            👥
          </div>

          <div
            style={{
              marginTop: 6,
              fontSize: 14,
              fontWeight: 850,
              color: "#172033",
            }}
          >
            Клиент / сделка
          </div>

          <div
            style={{
              marginTop: 3,
              fontSize: 11,
              color: "#818a9b",
            }}
          >
            карточка · сделка · задачи
          </div>
        </a>

        <a
          href="#boris-mop"
          style={{
            ...card,
            padding: 15,
            textDecoration:
              "none",
          }}
        >
          <div
            style={{
              fontSize: 23,
            }}
          >
            🥊
          </div>

          <div
            style={{
              marginTop: 6,
              fontSize: 14,
              fontWeight: 850,
              color: "#172033",
            }}
          >
            Тренировка МОП
          </div>

          <div
            style={{
              marginTop: 3,
              fontSize: 11,
              color: "#818a9b",
            }}
          >
            боевая тренировка
          </div>
        </a>

        <a
          href="#boris-gallery"
          style={{
            ...card,
            padding: 15,
            textDecoration:
              "none",
          }}
        >
          <div
            style={{
              fontSize: 23,
            }}
          >
            🖼️
          </div>

          <div
            style={{
              marginTop: 6,
              fontSize: 14,
              fontWeight: 850,
              color: "#172033",
            }}
          >
            Галерея
          </div>

          <div
            style={{
              marginTop: 3,
              fontSize: 11,
              color: "#818a9b",
            }}
          >
            фото · контент
          </div>
        </a>

        <a
          href="#boris-settings"
          style={{
            ...card,
            padding: 15,
            textDecoration:
              "none",
          }}
        >
          <div
            style={{
              fontSize: 23,
            }}
          >
            ⚙️
          </div>

          <div
            style={{
              marginTop: 6,
              fontSize: 14,
              fontWeight: 850,
              color: "#172033",
            }}
          >
            Настройки
          </div>

          <div
            style={{
              marginTop: 3,
              fontSize: 11,
              color: "#818a9b",
            }}
          >
            подключение · параметры
          </div>
        </a>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            "repeat(2,minmax(0,1fr))",
          gap: 14,
        }}
      >
        <div
          id="boris-crm"
          style={card}
        >
          <div
            style={{
              padding:
                "13px 15px",
              borderBottom:
                "1px solid #edf0f5",
              display: "flex",
              justifyContent:
                "space-between",
              alignItems:
                "center",
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 850,
                  color: "#172033",
                }}
              >
                👥 Клиент и сделка
              </div>

              <div
                style={{
                  marginTop: 3,
                  fontSize: 11,
                  color: "#8992a3",
                }}
              >
                Контекст выбранного аккаунта
              </div>
            </div>

            <a
              href={`/dashboard/crm${query}`}
              style={{
                fontSize: 11,
                color: "#2f7cff",
                fontWeight: 800,
              }}
            >
              открыть полностью →
            </a>
          </div>

          <iframe
            title="Клиенты и сделки BORIS"
            src={`/dashboard/crm${query}`}
            style={iframe}
          />
        </div>

        <div
          id="boris-mop"
          style={card}
        >
          <div
            style={{
              padding:
                "13px 15px",
              borderBottom:
                "1px solid #edf0f5",
              display: "flex",
              justifyContent:
                "space-between",
              alignItems:
                "center",
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 850,
                  color: "#172033",
                }}
              >
                🥊 Тренировка МОП · Боевая тренировка
              </div>

              <div
                style={{
                  marginTop: 3,
                  fontSize: 11,
                  color: "#8992a3",
                }}
              >
                Сотрудник сам оценивает и обучает МОП
              </div>
            </div>

            <a
              href={`/messages/mop-training${query}`}
              style={{
                fontSize: 11,
                color: "#d93672",
                fontWeight: 800,
              }}
            >
              открыть полностью →
            </a>
          </div>

          <iframe
            title="BORIS Тренировка МОП"
            src={`/messages/mop-training${query}`}
            style={iframe}
          />
        </div>

        <div
          id="boris-gallery"
          style={card}
        >
          <div
            style={{
              padding:
                "13px 15px",
              borderBottom:
                "1px solid #edf0f5",
              display: "flex",
              justifyContent:
                "space-between",
              alignItems:
                "center",
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 850,
                  color: "#172033",
                }}
              >
                🖼️ Галерея
              </div>

              <div
                style={{
                  marginTop: 3,
                  fontSize: 11,
                  color: "#8992a3",
                }}
              >
                Фото и контент
              </div>
            </div>

            <a
              href="/dashboard/campaign/photo"
              style={{
                fontSize: 11,
                color: "#169b6b",
                fontWeight: 800,
              }}
            >
              открыть полностью →
            </a>
          </div>

          <iframe
            title="BORIS Gallery"
            src="/dashboard/campaign/photo"
            style={iframe}
          />
        </div>

        <div
          id="boris-settings"
          style={card}
        >
          <div
            style={{
              padding:
                "13px 15px",
              borderBottom:
                "1px solid #edf0f5",
              display: "flex",
              justifyContent:
                "space-between",
              alignItems:
                "center",
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 850,
                  color: "#172033",
                }}
              >
                ⚙️ Настройки сообщений
              </div>

              <div
                style={{
                  marginTop: 3,
                  fontSize: 11,
                  color: "#8992a3",
                }}
              >
                Подключение и параметры
              </div>
            </div>

            <a
              href="/messages/setup"
              style={{
                fontSize: 11,
                color: "#7252dc",
                fontWeight: 800,
              }}
            >
              открыть полностью →
            </a>
          </div>

          <iframe
            title="BORIS Message Settings"
            src="/messages/setup"
            style={iframe}
          />
        </div>
      </div>
    </section>
  );
}
