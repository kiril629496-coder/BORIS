"use client";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";
import { apiFetch } from "@/app/lib/api";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

type Scenario = {
  index: number;
  angle_label: string;
  scene_label: string;
  status: string;
  output_url?: string | null;
  identity_score?: number | null;
  quality_score?: number | null;
};

type Stats = {
  queued?: number;
  processing?: number;
  complete?: number;
  rejected?: number;
  failed?: number;
};

type Job = {
  id: string;
  status: string;
  scenarios: Scenario[];
  stats: Stats;
};

export default function PhotoFactoryPage() {
  const [name, setName] = useState("");
  const [objectType, setObjectType] = useState("car");
  const [count, setCount] = useState(30);

  const [files, setFiles] = useState<File[]>([]);

  const [instruction, setInstruction] =
    useState("");

  const [busy, setBusy] = useState(false);

  const [message, setMessage] =
    useState("");

  const [job, setJob] =
    useState<Job | null>(null);

  const objectId = useMemo(() => {
    return (name || "object")
      .trim()
      .toLowerCase()
      .replace(
        /[^a-zа-яё0-9_-]+/gi,
        "_"
      )
      .replace(/^_+|_+$/g, "")
      .slice(0, 80);
  }, [name]);

  async function refreshJob(
    jobId: string
  ) {
    const response = await apiFetch(
      `/api/photo-factory/jobs/${encodeURIComponent(jobId)}`,
      {
        cache: "no-store",
      }
    );

    if (!response.ok) {
      return;
    }

    const data = await response.json();

    setJob(data);
  }

  useEffect(() => {
    if (!job?.id) {
      return;
    }

    if (
      job.status === "complete"
    ) {
      return;
    }

    const timer = window.setInterval(
      () => {
        refreshJob(job.id);
      },
      3000
    );

    return () =>
      window.clearInterval(timer);
  }, [job?.id, job?.status]);

  async function start() {
    if (!files.length) {
      setMessage(
        "Добавьте исходные фотографии."
      );

      return;
    }

    setBusy(true);
    setJob(null);

    try {
      setMessage(
        "Загружаю исходные фотографии…"
      );

      const form = new FormData();

      files.forEach((file) => {
        form.append(
          "files",
          file
        );
      });

      const uploadResponse =
        await apiFetch(
          `/api/photo-factory/objects/${encodeURIComponent(objectId)}/references`,
          {
            method: "POST",
            body: form,
          }
        );

      const uploadData =
        await uploadResponse.json();

      if (!uploadResponse.ok) {
        throw new Error(
          uploadData?.detail ||
            "Ошибка загрузки"
        );
      }

      setMessage(
        "Создаю задания для GPU…"
      );

      const jobResponse =
        await apiFetch(
          "/api/photo-factory/jobs",
          {
            method: "POST",
            headers: {
              "Content-Type":
                "application/json",
            },
            body: JSON.stringify({
              object_id: objectId,
              object_type:
                objectType,
              count,
              preserve_identity:
                true,
              custom_instruction:
                instruction.trim() ||
                null,
            }),
          }
        );

      const jobData =
        await jobResponse.json();

      if (!jobResponse.ok) {
        throw new Error(
          jobData?.detail ||
            "Ошибка создания задания"
        );
      }

      setJob(jobData);

      setMessage(
        `Фотосессия создана: ${jobData.scenarios.length} кадров поставлены в очередь.`
      );
    } catch (error: any) {
      setMessage(
        error?.message ||
          "Ошибка ИИ-фотостудии"
      );
    } finally {
      setBusy(false);
    }
  }

  const completed =
    job?.stats?.complete || 0;

  const total =
    job?.scenarios?.length || 0;

  return (
    <main
      style={{
        minHeight: "100vh",
        background: "#f6f8fb",
        padding: 28,
      }}
    >
      <div
        style={{
          maxWidth: 1240,
          margin: "0 auto",
        }}
      >
        <div
          style={{
            marginBottom: 24,
          }}
        >
          <div
            style={{
              color: "#64748b",
              fontSize: 14,
              marginBottom: 7,
            }}
          >
            ЕЦО → Объявления
          </div>

          <h1
            style={{
              margin: 0,
              color: "#0f172a",
              fontSize: 30,
            }}
          >
            ИИ-фотостудия
          </h1>

          <p
            style={{
              color: "#64748b",
              lineHeight: 1.6,
              maxWidth: 760,
            }}
          >
            Несколько реальных
            фотографий → до 30 новых
            ракурсов одного и того же
            объекта.
          </p>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "repeat(auto-fit,minmax(280px,1fr))",
            gap: 20,
          }}
        >
          <section
            style={{
              background: "#fff",
              border:
                "1px solid #e2e8f0",
              borderRadius: 16,
              padding: 22,
            }}
          >
            <h2
              style={{
                marginTop: 0,
              }}
            >
              Исходный объект
            </h2>

            <input
              value={name}
              onChange={(e) =>
                setName(
                  e.target.value
                )
              }
              placeholder="Toyota Camry"
              style={{
                boxSizing:
                  "border-box",
                width: "100%",
                padding: 11,
                border:
                  "1px solid #cbd5e1",
                borderRadius: 10,
                marginBottom: 12,
              }}
            />

            <select
              value={objectType}
              onChange={(e) =>
                setObjectType(
                  e.target.value
                )
              }
              style={{
                width: "100%",
                padding: 11,
                border:
                  "1px solid #cbd5e1",
                borderRadius: 10,
                background: "#fff",
                marginBottom: 12,
              }}
            >
              <option value="car">
                Автомобиль
              </option>

              <option value="furniture">
                Мебель
              </option>

              <option value="building">
                Бытовка
              </option>

              <option value="product">
                Товар
              </option>

              <option value="other">
                Другое
              </option>
            </select>

            <div
              style={{
                border:
                  "1px dashed #94a3b8",
                borderRadius: 12,
                padding: 18,
                background: "#f8fafc",
              }}
            >
              <input
                type="file"
                multiple
                accept="image/jpeg,image/png,image/webp"
                style={{
                  width: "100%",
                  maxWidth: "100%",
                  boxSizing: "border-box",
                }}
                onChange={(e) =>
                  setFiles(
                    Array.from(
                      e.target.files ||
                        []
                    ).slice(
                      0,
                      10
                    )
                  )
                }
              />

              <div
                style={{
                  color: "#64748b",
                  fontSize: 13,
                  marginTop: 9,
                }}
              >
                Лучше 3–8 фото с разных
                сторон.
              </div>

              {!!files.length && (
                <strong
                  style={{
                    display: "block",
                    marginTop: 8,
                  }}
                >
                  Выбрано:{" "}
                  {files.length}
                </strong>
              )}
            </div>

            <textarea
              value={instruction}
              onChange={(e) =>
                setInstruction(
                  e.target.value
                )
              }
              rows={4}
              placeholder="Пожелания к окружению, свету и стилю..."
              style={{
                boxSizing:
                  "border-box",
                width: "100%",
                padding: 11,
                border:
                  "1px solid #cbd5e1",
                borderRadius: 10,
                marginTop: 12,
              }}
            />
          </section>

          <section
            style={{
              background: "#fff",
              border:
                "1px solid #e2e8f0",
              borderRadius: 16,
              padding: 22,
            }}
          >
            <h2
              style={{
                marginTop: 0,
              }}
            >
              Новая фотосессия
            </h2>

            <div
              style={{
                display: "grid",
                gridTemplateColumns:
                  "repeat(4,1fr)",
                gap: 8,
                marginBottom: 18,
              }}
            >
              {[5, 10, 20, 30].map(
                (value) => (
                  <button
                    key={value}
                    onClick={() =>
                      setCount(value)
                    }
                    style={{
                      padding: 10,
                      borderRadius: 9,
                      border:
                        count === value
                          ? "1px solid #2563eb"
                          : "1px solid #cbd5e1",
                      background:
                        count === value
                          ? "#eff6ff"
                          : "#fff",
                      color:
                        count === value
                          ? "#1d4ed8"
                          : "#334155",
                      fontWeight: 800,
                    }}
                  >
                    {value}
                  </button>
                )
              )}
            </div>

            <div
              style={{
                background: "#f8fafc",
                padding: 14,
                borderRadius: 12,
                marginBottom: 18,
              }}
            >
              <strong>
                Сохранение объекта 1:1
              </strong>

              <div
                style={{
                  marginTop: 5,
                  color: "#64748b",
                  fontSize: 13,
                  lineHeight: 1.5,
                }}
              >
                Все кадры относятся к
                одному исходному объекту.
              </div>
            </div>

            <button
              onClick={start}
              disabled={
                busy ||
                !files.length
              }
              style={{
                width: "100%",
                padding:
                  "14px 16px",
                border: 0,
                borderRadius: 11,
                background:
                  busy ||
                  !files.length
                    ? "#94a3b8"
                    : "#2563eb",
                color: "#fff",
                fontWeight: 800,
                fontSize: 15,
              }}
            >
              {busy
                ? "BORIS готовит очередь…"
                : `Создать ${count} фото`}
            </button>

            {!!message && (
              <div
                style={{
                  marginTop: 12,
                  color: "#475569",
                  fontSize: 13,
                }}
              >
                {message}
              </div>
            )}

            {job && (
              <div
                style={{
                  marginTop: 20,
                }}
              >
                <strong>
                  Готово{" "}
                  {completed}/{total}
                </strong>

                <div
                  style={{
                    height: 8,
                    background:
                      "#e2e8f0",
                    borderRadius: 99,
                    overflow: "hidden",
                    marginTop: 8,
                  }}
                >
                  <div
                    style={{
                      height: "100%",
                      width:
                        total > 0
                          ? `${
                              (completed /
                                total) *
                              100
                            }%`
                          : "0%",
                      background:
                        "#2563eb",
                    }}
                  />
                </div>
              </div>
            )}
          </section>
        </div>

        {job && (
          <section
            style={{
              marginTop: 20,
              background: "#fff",
              border:
                "1px solid #e2e8f0",
              borderRadius: 16,
              padding: 22,
            }}
          >
            <h2
              style={{
                marginTop: 0,
              }}
            >
              Результаты
            </h2>

            <div
              style={{
                display: "grid",
                gridTemplateColumns:
                  "repeat(auto-fill,minmax(230px,1fr))",
                gap: 14,
              }}
            >
              {job.scenarios.map(
                (item) => (
                  <article
                    key={item.index}
                    style={{
                      border:
                        "1px solid #e2e8f0",
                      borderRadius: 12,
                      overflow: "hidden",
                      background: "#fff",
                    }}
                  >
                    {item.output_url ? (
                      <img
                        src={
                          item.output_url
                        }
                        alt={
                          item.angle_label
                        }
                        style={{
                          width: "100%",
                          aspectRatio:
                            "4 / 3",
                          objectFit:
                            "cover",
                          display:
                            "block",
                        }}
                      />
                    ) : (
                      <div
                        style={{
                          aspectRatio:
                            "4 / 3",
                          background:
                            "#f8fafc",
                          display:
                            "flex",
                          alignItems:
                            "center",
                          justifyContent:
                            "center",
                          color:
                            "#94a3b8",
                          fontSize: 13,
                        }}
                      >
                        {
                          item.status
                        }
                      </div>
                    )}

                    <div
                      style={{
                        padding: 12,
                      }}
                    >
                      <strong>
                        #{item.index}{" "}
                        {
                          item.angle_label
                        }
                      </strong>

                      <div
                        style={{
                          color:
                            "#64748b",
                          fontSize: 12,
                          marginTop: 4,
                        }}
                      >
                        {
                          item.scene_label
                        }
                      </div>

                      <div
                        style={{
                          marginTop: 8,
                          color:
                            item.status ===
                            "complete"
                              ? "#15803d"
                              : "#2563eb",
                          fontSize: 12,
                          fontWeight: 700,
                        }}
                      >
                        {humanizeBusinessText(item.status, "В работе")}
                      </div>
                    </div>
                  </article>
                )
              )}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
