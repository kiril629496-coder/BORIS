"use client";

import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

type Scenario = {
  index: number;
  angle_code?: string;
  angle_label?: string;
  scene_code?: string;
  scene_label?: string;
  status?: string;
  attempts?: number;
  output_url?: string | null;
  output_exists?: boolean;
  output_bytes?: number | null;
  identity_score?: number | null;
  quality_score?: number | null;
  last_error?: string | null;
};

type Reference = {
  id?: string;
  original_name?: string;
  filename?: string;
  size?: number;
};

type Job = {
  id: string;
  object_id?: string;
  object_type?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  references?: Reference[];
  scenarios?: Scenario[];
  stats?: Record<string, number>;
};

const API = "/api/photo-factory-v33";

const DEFAULT_PROOF_JOB =
  "pf_1787347120_6bdc3ae65c";

function statusLabel(status?: string) {
  switch (status) {
    case "complete":
      return "Готово";
    case "processing":
      return "Генерируется";
    case "queued":
      return "В очереди";
    case "rejected":
      return "Нужен повтор";
    case "failed":
      return "Ошибка";
    default:
      return "Подготовка";
  }
}

function statusTone(status?: string) {
  switch (status) {
    case "complete":
      return {
        bg: "#ecfdf3",
        color: "#15803d",
        border: "#bbf7d0",
      };
    case "processing":
      return {
        bg: "#eff6ff",
        color: "#1d4ed8",
        border: "#bfdbfe",
      };
    case "queued":
      return {
        bg: "#f8fafc",
        color: "#475569",
        border: "#e2e8f0",
      };
    case "rejected":
    case "failed":
      return {
        bg: "#fef2f2",
        color: "#b91c1c",
        border: "#fecaca",
      };
    default:
      return {
        bg: "#f8fafc",
        color: "#475569",
        border: "#e2e8f0",
      };
  }
}

function bytes(value?: number | null) {
  if (!value) return "";

  if (value < 1024) {
    return `${value} B`;
  }

  if (value < 1024 * 1024) {
    return `${Math.round(value / 1024)} KB`;
  }

  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value?: string) {
  if (!value) return "—";

  try {
    return new Date(value).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

function ProgressBar({
  value,
}: {
  value: number;
}) {
  return (
    <div
      style={{
        width: "100%",
        height: 8,
        borderRadius: 999,
        background: "#eaf0f7",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${Math.max(
            0,
            Math.min(100, value),
          )}%`,
          height: "100%",
          borderRadius: 999,
          background:
            "linear-gradient(90deg, #2563eb 0%, #60a5fa 100%)",
          transition: "width 300ms ease",
        }}
      />
    </div>
  );
}

function SkeletonCard() {
  return (
    <div
      style={{
        borderRadius: 22,
        border: "1px solid #e8edf5",
        background: "#fff",
        overflow: "hidden",
        minHeight: 380,
      }}
    >
      <div
        style={{
          aspectRatio: "4 / 3",
          background:
            "linear-gradient(90deg,#f1f5f9 25%,#f8fafc 37%,#f1f5f9 63%)",
          backgroundSize: "400% 100%",
          animation: "borisSkeleton 1.4s ease infinite",
        }}
      />
      <div style={{ padding: 18 }}>
        <div
          style={{
            height: 16,
            borderRadius: 8,
            width: "56%",
            background: "#eef2f7",
            marginBottom: 10,
          }}
        />
        <div
          style={{
            height: 12,
            borderRadius: 8,
            width: "74%",
            background: "#f3f6fa",
          }}
        />
      </div>
    </div>
  );
}

export default function PhotoFactoryGalleryPage() {
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retrying, setRetrying] =
    useState<number | null>(null);
  const [showTech, setShowTech] =
    useState(false);

  useEffect(() => {
    const params = new URLSearchParams(
      window.location.search,
    );

    const fromQuery = params.get("job");

    const fromStorage =
      window.localStorage.getItem(
        "boris_photo_factory_last_job",
      );

    const initial =
      fromQuery ||
      fromStorage ||
      DEFAULT_PROOF_JOB;

    setJobId(initial);
  }, []);

  const load = useCallback(
    async (silent = false) => {
      if (!jobId) return;

      if (!silent) {
        setLoading(true);
      }

      try {
        const response = await fetch(
          `${API}/jobs/${encodeURIComponent(
            jobId,
          )}`,
          {
            cache: "no-store",
            headers: {
              Authorization: `Bearer ${window.localStorage.getItem("boris_token") || ""}`,
            },
          },
        );

        if (!response.ok) {
          throw new Error(
            `Не удалось загрузить фотосессию`,
          );
        }

        const payload =
          await response.json();

        setJob(payload.job);
        setError("");

        window.localStorage.setItem(
          "boris_photo_factory_last_job",
          jobId,
        );
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Не удалось загрузить фотосессию",
        );
      } finally {
        if (!silent) {
          setLoading(false);
        }
      }
    },
    [jobId],
  );

  useEffect(() => {
    if (!jobId) return;

    load();

    const timer = window.setInterval(
      () => {
        load(true);
      },
      3000,
    );

    return () =>
      window.clearInterval(timer);
  }, [jobId, load]);

  const scenarios = job?.scenarios || [];
  const references = job?.references || [];

  const complete = useMemo(
    () =>
      scenarios.filter(
        (item) =>
          item.status === "complete",
      ).length,
    [scenarios],
  );

  const processing = useMemo(
    () =>
      scenarios.filter(
        (item) =>
          item.status === "processing",
      ).length,
    [scenarios],
  );

  const failed = useMemo(
    () =>
      scenarios.filter(
        (item) =>
          item.status === "failed" ||
          item.status === "rejected",
      ).length,
    [scenarios],
  );

  const total = scenarios.length;

  const progress =
    total > 0
      ? Math.round(
          (complete / total) * 100,
        )
      : 0;

  async function retry(index: number) {
    if (!job) return;

    setRetrying(index);

    try {
      const response = await fetch(
        `${API}/jobs/${encodeURIComponent(
          job.id,
        )}/retry`,
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/json",
            Authorization: `Bearer ${window.localStorage.getItem("boris_token") || ""}`,
          },
          body: JSON.stringify({
            scenario_index: index,
          }),
        },
      );

      const payload =
        await response.json();

      if (!response.ok) {
        throw new Error(
          payload?.detail ||
            "Не удалось повторить кадр",
        );
      }

      await load(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Не удалось повторить кадр",
      );
    } finally {
      setRetrying(null);
    }
  }

  return (
    <>
      <style jsx global>{`
        @keyframes borisSkeleton {
          0% {
            background-position: 100% 0;
          }
          100% {
            background-position: -100% 0;
          }
        }

        .boris-photo-card {
          transition:
            transform 180ms ease,
            box-shadow 180ms ease,
            border-color 180ms ease;
        }

        .boris-photo-card:hover {
          transform: translateY(-2px);
          box-shadow:
            0 18px 40px rgba(15, 23, 42, 0.08);
          border-color: #d7e2f2;
        }

        .boris-photo-action {
          transition:
            background 160ms ease,
            color 160ms ease,
            border-color 160ms ease;
        }

        @media (max-width: 820px) {
          .boris-session-grid {
            grid-template-columns: 1fr !important;
          }

          .boris-stats-grid {
            grid-template-columns:
              repeat(2, minmax(0, 1fr)) !important;
          }

          .boris-gallery-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>

      <main
        style={{
          minHeight: "100vh",
          background:
            "linear-gradient(180deg,#f8fbff 0%,#f6f8fb 28%,#f8fafc 100%)",
          padding: "28px 24px 80px",
        }}
      >
        <div
          style={{
            maxWidth: 1480,
            margin: "0 auto",
          }}
        >
          <header
            style={{
              display: "flex",
              justifyContent:
                "space-between",
              gap: 24,
              alignItems: "center",
              marginBottom: 28,
              flexWrap: "wrap",
            }}
          >
            <div>
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "7px 11px",
                  borderRadius: 999,
                  background: "#eff6ff",
                  color: "#2563eb",
                  fontSize: 12,
                  fontWeight: 700,
                  marginBottom: 12,
                }}
              >
                AI ФОТОСТУДИЯ
              </div>

              <h1
                style={{
                  margin: 0,
                  fontSize: "clamp(30px, 4vw, 46px)",
                  letterSpacing: "-0.035em",
                  lineHeight: 1.06,
                  color: "#0f172a",
                }}
              >
                ИИ-фотостудия
              </h1>

              <p
                style={{
                  margin: "10px 0 0",
                  color: "#64748b",
                  fontSize: 15,
                  maxWidth: 660,
                  lineHeight: 1.6,
                }}
              >
                БОРИС создаёт реалистичные фотографии вашего объекта.
                Каждый кадр проверяется на качество и соответствие оригиналу.
              </p>
            </div>

            <div
              style={{
                display: "flex",
                gap: 10,
                flexWrap: "wrap",
              }}
            >
              <a
                href="/dashboard/photo-factory"
                style={{
                  textDecoration: "none",
                  padding: "12px 16px",
                  borderRadius: 12,
                  border: "1px solid #dbe4ef",
                  color: "#0f172a",
                  background: "#fff",
                  fontWeight: 700,
                  fontSize: 14,
                }}
              >
                + Новая фотосессия
              </a>

              <button
                onClick={() =>
                  setShowTech((v) => !v)
                }
                style={{
                  padding: "12px 14px",
                  borderRadius: 12,
                  border: "1px solid #dbe4ef",
                  color: "#64748b",
                  background: "#fff",
                  fontWeight: 600,
                  fontSize: 13,
                  cursor: "pointer",
                }}
              >
                {showTech
                  ? "Скрыть детали"
                  : "Технические детали"}
              </button>
            </div>
          </header>

          {showTech ? (
            <section
              style={{
                background: "#0f172a",
                color: "#cbd5e1",
                borderRadius: 16,
                padding: 16,
                marginBottom: 20,
                fontFamily:
                  "ui-monospace,SFMono-Regular,Menlo,monospace",
                fontSize: 12,
                lineHeight: 1.7,
              }}
            >
              <div>
                Статус задачи: {jobId ? "выполняется" : "ожидает запуска"}
              </div>
              <div>
                BORIS автоматически обновляет результат во время обработки.
              </div>
            </section>
          ) : null}

          {error ? (
            <section
              style={{
                padding: 16,
                marginBottom: 20,
                borderRadius: 14,
                background: "#fff1f2",
                border: "1px solid #fecdd3",
                color: "#be123c",
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              {error}
            </section>
          ) : null}

          {job ? (
            <>
              <section
                className="boris-session-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns:
                    "minmax(0, 1.35fr) minmax(340px, .65fr)",
                  gap: 20,
                  marginBottom: 20,
                }}
              >
                <div
                  style={{
                    background: "#fff",
                    border:
                      "1px solid #e5ebf3",
                    borderRadius: 22,
                    padding: 22,
                    boxShadow:
                      "0 8px 30px rgba(15,23,42,.035)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent:
                        "space-between",
                      gap: 18,
                      alignItems: "flex-start",
                      marginBottom: 22,
                      flexWrap: "wrap",
                    }}
                  >
                    <div>
                      <div
                        style={{
                          fontSize: 12,
                          color: "#94a3b8",
                          marginBottom: 6,
                          fontWeight: 700,
                          textTransform:
                            "uppercase",
                          letterSpacing:
                            ".05em",
                        }}
                      >
                        Объект
                      </div>

                      <div
                        style={{
                          fontSize: 24,
                          fontWeight: 800,
                          color: "#0f172a",
                          letterSpacing:
                            "-.02em",
                        }}
                      >
                        {job.object_id ||
                          "Объект съёмки"}
                      </div>

                      <div
                        style={{
                          marginTop: 7,
                          color: "#64748b",
                          fontSize: 13,
                        }}
                      >
                        Создано{" "}
                        {formatDate(
                          job.created_at,
                        )}
                      </div>
                    </div>

                    <div
                      style={{
                        ...statusTone(
                          job.status,
                        ),
                        padding: "8px 12px",
                        borderRadius: 999,
                        border: `1px solid ${
                          statusTone(
                            job.status,
                          ).border
                        }`,
                        fontSize: 12,
                        fontWeight: 800,
                      }}
                    >
                      {statusLabel(
                        job.status,
                      )}
                    </div>
                  </div>

                  <div
                    style={{
                      display: "flex",
                      gap: 10,
                      flexWrap: "wrap",
                    }}
                  >
                    {references
                      .slice(0, 6)
                      .map(
                        (
                          ref,
                          index,
                        ) => (
                          <div
                            key={
                              ref.id ||
                              index
                            }
                            title={
                              ref.original_name ||
                              "Исходник"
                            }
                            style={{
                              width: 78,
                              height: 78,
                              borderRadius: 14,
                              border:
                                "1px solid #e5ebf3",
                              background:
                                "linear-gradient(135deg,#f8fafc,#eef3f8)",
                              display: "flex",
                              alignItems:
                                "center",
                              justifyContent:
                                "center",
                              color:
                                "#64748b",
                              fontSize: 12,
                              fontWeight: 700,
                              textAlign:
                                "center",
                              padding: 8,
                              overflow:
                                "hidden",
                            }}
                          >
                            Фото{" "}
                            {index + 1}
                          </div>
                        ),
                      )}

                    {references.length >
                    6 ? (
                      <div
                        style={{
                          width: 78,
                          height: 78,
                          borderRadius: 14,
                          border:
                            "1px dashed #cbd5e1",
                          display: "flex",
                          alignItems:
                            "center",
                          justifyContent:
                            "center",
                          color:
                            "#64748b",
                          fontSize: 12,
                          fontWeight: 700,
                        }}
                      >
                        +
                        {references.length -
                          6}
                      </div>
                    ) : null}
                  </div>
                </div>

                <div
                  style={{
                    background:
                      "linear-gradient(145deg,#0f172a,#172554)",
                    color: "#fff",
                    borderRadius: 22,
                    padding: 22,
                    boxShadow:
                      "0 14px 34px rgba(15,23,42,.16)",
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      color: "#93c5fd",
                      fontWeight: 800,
                      letterSpacing:
                        ".05em",
                      textTransform:
                        "uppercase",
                      marginBottom: 8,
                    }}
                  >
                    Прогресс
                  </div>

                  <div
                    style={{
                      display: "flex",
                      alignItems:
                        "baseline",
                      gap: 8,
                      marginBottom: 6,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 42,
                        fontWeight: 850,
                        letterSpacing:
                          "-.04em",
                      }}
                    >
                      {progress}%
                    </div>

                    <div
                      style={{
                        color:
                          "#cbd5e1",
                        fontSize: 14,
                      }}
                    >
                      готово
                    </div>
                  </div>

                  <div
                    style={{
                      margin: "14px 0 18px",
                    }}
                  >
                    <ProgressBar
                      value={progress}
                    />
                  </div>

                  <div
                    style={{
                      color: "#cbd5e1",
                      fontSize: 13,
                      lineHeight: 1.6,
                    }}
                  >
                    {complete} из {total}{" "}
                    кадров готовы
                    {processing > 0
                      ? ` · ${processing} генерируются`
                      : ""}
                    {failed > 0
                      ? ` · ${failed} требуют внимания`
                      : ""}
                  </div>
                </div>
              </section>

              <section
                className="boris-stats-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns:
                    "repeat(4, minmax(0, 1fr))",
                  gap: 12,
                  marginBottom: 28,
                }}
              >
                {[
                  {
                    label: "Всего кадров",
                    value: total,
                  },
                  {
                    label: "Готово",
                    value: complete,
                  },
                  {
                    label:
                      "Генерируется",
                    value: processing,
                  },
                  {
                    label:
                      "Нужен повтор",
                    value: failed,
                  },
                ].map((item) => (
                  <div
                    key={item.label}
                    style={{
                      background: "#fff",
                      border:
                        "1px solid #e5ebf3",
                      borderRadius: 16,
                      padding: "15px 17px",
                    }}
                  >
                    <div
                      style={{
                        color:
                          "#94a3b8",
                        fontSize: 12,
                        marginBottom: 5,
                        fontWeight: 700,
                      }}
                    >
                      {item.label}
                    </div>

                    <div
                      style={{
                        color:
                          "#0f172a",
                        fontSize: 24,
                        fontWeight: 850,
                      }}
                    >
                      {item.value}
                    </div>
                  </div>
                ))}
              </section>

              <div
                style={{
                  display: "flex",
                  justifyContent:
                    "space-between",
                  alignItems: "center",
                  gap: 16,
                  marginBottom: 16,
                  flexWrap: "wrap",
                }}
              >
                <div>
                  <h2
                    style={{
                      margin: 0,
                      fontSize: 24,
                      color: "#0f172a",
                      letterSpacing:
                        "-.025em",
                    }}
                  >
                    Готовые кадры
                  </h2>

                  <div
                    style={{
                      marginTop: 6,
                      color: "#64748b",
                      fontSize: 13,
                    }}
                  >
                    Галерея обновляется
                    автоматически
                  </div>
                </div>
              </div>

              <section
                className="boris-gallery-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns:
                    "repeat(auto-fill, minmax(320px, 1fr))",
                  gap: 18,
                }}
              >
                {loading && !job
                  ? Array.from({
                      length: 6,
                    }).map((_, index) => (
                      <SkeletonCard
                        key={index}
                      />
                    ))
                  : null}

                {scenarios.map(
                  (item) => {
                    const tone =
                      statusTone(
                        item.status,
                      );

                    return (
                      <article
                        key={item.index}
                        className="boris-photo-card"
                        style={{
                          background: "#fff",
                          border:
                            "1px solid #e5ebf3",
                          borderRadius: 22,
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            position:
                              "relative",
                            aspectRatio:
                              "4 / 3",
                            background:
                              "#eef2f7",
                          }}
                        >
                          {item.output_url &&
                          item.output_exists ? (
                            <img
                              src={
                                item.output_url
                              }
                              alt={
                                item.angle_label ||
                                `Кадр ${item.index}`
                              }
                              style={{
                                width:
                                  "100%",
                                height:
                                  "100%",
                                objectFit:
                                  "cover",
                                display:
                                  "block",
                              }}
                            />
                          ) : (
                            <div
                              style={{
                                position:
                                  "absolute",
                                inset: 0,
                                display:
                                  "flex",
                                flexDirection:
                                  "column",
                                alignItems:
                                  "center",
                                justifyContent:
                                  "center",
                                color:
                                  "#64748b",
                                padding: 24,
                                textAlign:
                                  "center",
                              }}
                            >
                              <div
                                style={{
                                  width: 46,
                                  height: 46,
                                  borderRadius:
                                    14,
                                  background:
                                    "#fff",
                                  border:
                                    "1px solid #e2e8f0",
                                  display:
                                    "flex",
                                  alignItems:
                                    "center",
                                  justifyContent:
                                    "center",
                                  marginBottom:
                                    12,
                                  fontSize:
                                    20,
                                }}
                              >
                                {item.status ===
                                "processing"
                                  ? "⚙"
                                  : item.status ===
                                        "failed" ||
                                      item.status ===
                                        "rejected"
                                    ? "!"
                                    : "◌"}
                              </div>

                              <div
                                style={{
                                  fontWeight:
                                    800,
                                  color:
                                    "#334155",
                                }}
                              >
                                {statusLabel(
                                  item.status,
                                )}
                              </div>

                              {item.status ===
                              "processing" ? (
                                <div
                                  style={{
                                    marginTop:
                                      8,
                                    fontSize:
                                      12,
                                  }}
                                >
                                  БОРИС создаёт
                                  новый кадр
                                </div>
                              ) : null}
                            </div>
                          )}

                          <div
                            style={{
                              position:
                                "absolute",
                              top: 12,
                              left: 12,
                              padding:
                                "7px 10px",
                              borderRadius:
                                999,
                              background:
                                "rgba(255,255,255,.94)",
                              backdropFilter:
                                "blur(8px)",
                              color:
                                "#0f172a",
                              fontSize: 11,
                              fontWeight:
                                800,
                              boxShadow:
                                "0 4px 14px rgba(15,23,42,.08)",
                            }}
                          >
                            Кадр #
                            {item.index}
                          </div>

                          <div
                            style={{
                              position:
                                "absolute",
                              top: 12,
                              right: 12,
                              padding:
                                "7px 10px",
                              borderRadius:
                                999,
                              background:
                                tone.bg,
                              border: `1px solid ${tone.border}`,
                              color:
                                tone.color,
                              fontSize: 11,
                              fontWeight:
                                800,
                            }}
                          >
                            {statusLabel(
                              item.status,
                            )}
                          </div>
                        </div>

                        <div
                          style={{
                            padding:
                              "17px 18px 18px",
                          }}
                        >
                          <div
                            style={{
                              fontSize: 16,
                              fontWeight: 800,
                              color:
                                "#0f172a",
                              lineHeight:
                                1.35,
                            }}
                          >
                            {item.angle_label ||
                              "Новый ракурс"}
                          </div>

                          <div
                            style={{
                              marginTop: 6,
                              color:
                                "#64748b",
                              fontSize: 13,
                              lineHeight:
                                1.5,
                              minHeight: 20,
                            }}
                          >
                            {item.scene_label ||
                              "ИИ-фотосессия"}
                          </div>

                          {item.output_bytes ? (
                            <div
                              style={{
                                marginTop: 8,
                                color:
                                  "#94a3b8",
                                fontSize: 11,
                              }}
                            >
                              {bytes(
                                item.output_bytes,
                              )}
                            </div>
                          ) : null}

                          {item.last_error ? (
                            <div
                              style={{
                                marginTop: 12,
                                padding:
                                  "10px 12px",
                                borderRadius:
                                  10,
                                background:
                                  "#fff1f2",
                                color:
                                  "#be123c",
                                fontSize: 12,
                                lineHeight:
                                  1.45,
                              }}
                            >
                              {humanizeBusinessText(item.last_error,"Не удалось завершить этот кадр. BORIS сохранил состояние для безопасного повтора.")}
                            </div>
                          ) : null}

                          <div
                            style={{
                              display: "flex",
                              gap: 8,
                              flexWrap:
                                "wrap",
                              marginTop: 16,
                            }}
                          >
                            {item.output_url &&
                            item.output_exists ? (
                              <>
                                <a
                                  className="boris-photo-action"
                                  href={
                                    item.output_url
                                  }
                                  target="_blank"
                                  rel="noreferrer"
                                  style={{
                                    flex: 1,
                                    textAlign:
                                      "center",
                                    minWidth:
                                      110,
                                    padding:
                                      "10px 12px",
                                    borderRadius:
                                      10,
                                    background:
                                      "#2563eb",
                                    color:
                                      "#fff",
                                    textDecoration:
                                      "none",
                                    fontWeight:
                                      800,
                                    fontSize:
                                      13,
                                  }}
                                >
                                  Открыть
                                </a>

                                <a
                                  className="boris-photo-action"
                                  href={
                                    item.output_url
                                  }
                                  download
                                  style={{
                                    flex: 1,
                                    textAlign:
                                      "center",
                                    minWidth:
                                      110,
                                    padding:
                                      "10px 12px",
                                    borderRadius:
                                      10,
                                    background:
                                      "#fff",
                                    color:
                                      "#334155",
                                    border:
                                      "1px solid #dbe4ef",
                                    textDecoration:
                                      "none",
                                    fontWeight:
                                      800,
                                    fontSize:
                                      13,
                                  }}
                                >
                                  Скачать
                                </a>
                              </>
                            ) : null}

                            {(item.status ===
                              "failed" ||
                              item.status ===
                                "rejected") && (
                              <button
                                onClick={() =>
                                  retry(
                                    item.index,
                                  )
                                }
                                disabled={
                                  retrying ===
                                  item.index
                                }
                                style={{
                                  width:
                                    "100%",
                                  padding:
                                    "10px 12px",
                                  borderRadius:
                                    10,
                                  background:
                                    "#fff",
                                  color:
                                    "#b91c1c",
                                  border:
                                    "1px solid #fecaca",
                                  cursor:
                                    "pointer",
                                  fontWeight:
                                    800,
                                  fontSize:
                                    13,
                                }}
                              >
                                {retrying ===
                                item.index
                                  ? "Повторяем..."
                                  : "Перегенерировать кадр"}
                              </button>
                            )}
                          </div>
                        </div>
                      </article>
                    );
                  },
                )}
              </section>

              {scenarios.length === 0 ? (
                <section
                  style={{
                    background: "#fff",
                    border:
                      "1px solid #e5ebf3",
                    borderRadius: 22,
                    padding: 40,
                    textAlign: "center",
                    color: "#64748b",
                  }}
                >
                  <div
                    style={{
                      fontSize: 18,
                      fontWeight: 800,
                      color: "#334155",
                    }}
                  >
                    Пока нет готовых кадров
                  </div>

                  <div
                    style={{
                      marginTop: 8,
                      fontSize: 13,
                    }}
                  >
                    Как только БОРИС
                    начнёт генерацию,
                    изображения появятся
                    здесь автоматически.
                  </div>
                </section>
              ) : null}
            </>
          ) : loading ? (
            <section
              className="boris-gallery-grid"
              style={{
                display: "grid",
                gridTemplateColumns:
                  "repeat(auto-fill, minmax(320px, 1fr))",
                gap: 18,
              }}
            >
              {Array.from({
                length: 6,
              }).map((_, index) => (
                <SkeletonCard
                  key={index}
                />
              ))}
            </section>
          ) : null}
        </div>
      </main>
    </>
  );
}
