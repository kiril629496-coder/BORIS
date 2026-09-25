"use client";
import { Button } from "@/app/ui";
import { humanizeBusinessText } from "@/app/lib/humanizeBusinessText";



import { crmApi } from "@/app/lib/crmApi";

import {
  useEffect,
  useState,
} from "react";

import {
  useRouter,
} from "next/navigation";


type Provider =
  | "amocrm"
  | "bitrix24";


type Status = {
  provider: Provider;

  mode: string;

  status: string;

  external_domain?: string | null;

  external_account_id?: string | null;

  external_writes_enabled: boolean;

  mapped_stages: number;

  last_sync_at?: string | null;

  last_error?: string | null;
};


type RemoteStage = {
  id: string;
  name: string;
};


type RemotePipeline = {
  id: string;
  name: string;
  stages: RemoteStage[];
};


type LocalMap = {
  id: number;
  name: string;
  position: number;

  external_pipeline_id?: string | null;
  external_stage_id?: string | null;

  external_pipeline_name?: string | null;
  external_stage_name?: string | null;
};


async function api<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  // BORIS_CRM_INTEGRATIONS_SHARED_AUTH_V1
  return crmApi<T>(url, options);
}


export default function CRMIntegrationsPage() {

  const router = useRouter();


  const [
    statuses,
    setStatuses,
  ] = useState<Status[]>([]);


  const [
    selected,
    setSelected,
  ] = useState<Provider>(
    "amocrm"
  );


  const [
    form,
    setForm,
  ] = useState({
    client_id: "",
    client_secret: "",
    external_domain: "",
    redirect_uri: "",
    mode: "bidirectional",
  });


  const [
    remote,
    setRemote,
  ] = useState<RemotePipeline[]>([]);


  const [
    mapping,
    setMapping,
  ] = useState<LocalMap[]>([]);


  const [
    error,
    setError,
  ] = useState("");


  const [
    busy,
    setBusy,
  ] = useState(false);

  const [statusLoading, setStatusLoading] = useState(true);


  async function loadStatus() {

    setStatusLoading(true);
    try {

      const rows =
        await api<Status[]>(
          "/api/crm/integration/status"
        );

      setStatuses(rows);

    } catch (e) {

      setError(
        e instanceof Error
          ? e.message
          : "Ошибка статуса"
      );

    } finally {
      setStatusLoading(false);
    }
  }


  useEffect(() => {

    loadStatus();

  }, []);


  const status =
    statuses.find(
      x =>
        x.provider === selected
    );


  function callbackUrl(
    provider: Provider,
  ) {

    return (
      `https://boris-ai.pro/api/crm/integration/`
      + (
        provider === "amocrm"
          ? "amocrm/oauth/callback"
          : "bitrix24/oauth/callback"
      )
    );
  }


  async function saveConfiguration() {

    setBusy(true);
    setError("");

    try {

      await api(
        "/api/crm/integration/configure",
        {
          method: "POST",

          headers: {
            "Content-Type":
              "application/json",
          },

          body: JSON.stringify({
            provider:
              selected,

            client_id:
              form.client_id,

            client_secret:
              form.client_secret,

            external_domain:
              form.external_domain,

            redirect_uri:
              form.redirect_uri
              || callbackUrl(
                selected
              ),

            mode:
              form.mode,
          }),
        }
      );

      await loadStatus();

    } catch (e) {

      setError(
        e instanceof Error
          ? e.message
          : "Ошибка сохранения"
      );

    } finally {

      setBusy(false);

    }
  }


  async function connect() {

    setBusy(true);
    setError("");

    try {

      const result =
        await api<{
          url: string;
        }>(
          `/api/crm/integration/${selected}/oauth/start`,
          {
            method: "POST",
          }
        );


      const popup =
        window.open(
          result.url,
          "boris_crm_oauth",
          "width=850,height=720,scrollbars=yes,resizable=yes"
        );


      if (!popup) {
        throw new Error(
          "Браузер заблокировал окно авторизации"
        );
      }


      const timer =
        window.setInterval(
          async () => {

            if (popup.closed) {

              window.clearInterval(
                timer
              );

              await loadStatus();

            }

          },
          1000
        );


    } catch (e) {

      setError(
        e instanceof Error
          ? e.message
          : "Ошибка авторизации"
      );

    } finally {

      setBusy(false);

    }
  }


  async function loadMapping() {

    setBusy(true);
    setError("");

    try {

      const [
        remoteResult,
        localResult,
      ] = await Promise.all([

        api<{
          pipelines:
            RemotePipeline[];
        }>(
          `/api/crm/integration/${selected}/pipelines`
        ),

        api<LocalMap[]>(
          `/api/crm/integration/${selected}/mapping`
        ),

      ]);


      setRemote(
        remoteResult.pipelines
      );

      setMapping(
        localResult
      );


    } catch (e) {

      setError(
        e instanceof Error
          ? e.message
          : "Ошибка mapping"
      );

    } finally {

      setBusy(false);

    }
  }


  async function setStageMap(
    localStage: LocalMap,
    value: string,
  ) {

    if (!value) {
      return;
    }


    const [
      pipelineId,
      stageId,
    ] = value.split("::");


    const pipeline =
      remote.find(
        x =>
          x.id === pipelineId
      );


    const remoteStage =
      pipeline?.stages.find(
        x =>
          x.id === stageId
      );


    await api(
      `/api/crm/integration/${selected}/mapping`,
      {
        method: "POST",

        headers: {
          "Content-Type":
            "application/json",
        },

        body: JSON.stringify({
          boris_stage_id:
            localStage.id,

          external_pipeline_id:
            pipelineId,

          external_stage_id:
            stageId,

          external_pipeline_name:
            pipeline?.name || "",

          external_stage_name:
            remoteStage?.name || "",
        }),
      }
    );


    await loadMapping();
    await loadStatus();
  }


  async function toggleWrites(
    enabled: boolean,
  ) {

    setBusy(true);
    setError("");

    try {

      await api(
        `/api/crm/integration/${selected}/writes`,
        {
          method: "POST",

          headers: {
            "Content-Type":
              "application/json",
          },

          body: JSON.stringify({
            enabled,
          }),
        }
      );


      await loadStatus();

    } catch (e) {

      setError(
        e instanceof Error
          ? e.message
          : "Ошибка переключения"
      );

    } finally {

      setBusy(false);

    }
  }


  async function pushAll() {

    if (
      !window.confirm(
        "Поставить все текущие контакты и сделки BORIS в очередь синхронизации?"
      )
    ) {
      return;
    }


    setBusy(true);

    try {

      const result =
        await api<{
          queued: number;
        }>(
          `/api/crm/integration/${selected}/enqueue-all`,
          {
            method: "POST",
          }
        );


      alert(
        `В очередь поставлено: ${result.queued}`
      );


    } catch (e) {

      setError(
        e instanceof Error
          ? e.message
          : "Ошибка синхронизации"
      );

    } finally {

      setBusy(false);

    }
  }


  async function importRemote() {

    if (
      !window.confirm(
        "Импортировать до 100 внешних сделок в BORIS?"
      )
    ) {
      return;
    }


    setBusy(true);

    try {

      const result =
        await api<any>(
          `/api/crm/integration/${selected}/import?limit=100`,
          {
            method: "POST",
          }
        );


      alert(
        JSON.stringify(
          result,
          null,
          2
        )
      );


    } catch (e) {

      setError(
        e instanceof Error
          ? e.message
          : "Ошибка импорта"
      );

    } finally {

      setBusy(false);

    }
  }


  return (
    <div
      className="boris-product-route boris-integrations-page"
      style={{
        minHeight:
          "100vh",

        background:
          "var(--boris-background)",

        padding:
          24,
      }}
    >

      <div
        style={{
          maxWidth:
            1250,

          margin:
            "0 auto",
        }}
      >

        <Button
          onClick={
            () =>
              router.push(
                "/dashboard/crm"
              )
          }

          style={
            ghost
          }
        >
          ← Продажи
        </Button>


        <div
          style={{
            marginTop:
              16,
          }}
        >

          <div style={{ color:"var(--boris-blue)", fontWeight:650, fontSize:13 }}>
            Подключение системы продаж
          </div>


          <h1
            style={{
              margin:
                "5px 0 5px",

              fontSize:
                30,
            }}
          >
            Интеграции продаж
          </h1>


          <div
            style={{
              color:
                "var(--boris-text-2)",
            }}
          >
            BORIS может быть основной системой продаж
            или работать двусторонне
            с существующей системой клиента.
          </div>

        </div>


        {error ? (

          <div
            style={{
              marginTop:
                14,

              background:
                "#FEF3F2",

              color:
                "var(--b-danger-text)",

              padding:
                12,

              borderRadius:
                10,
            }}
          >
            {error}
          </div>

        ) : null}


        <div
          style={{
            display:
              "flex",

            gap:
              8,

            marginTop:
              20,
          }}
        >

          {(
            [
              [
                "amocrm",
                "amoCRM",
              ],

              [
                "bitrix24",
                "Битрикс24",
              ],

            ] as const
          ).map(
            ([
              key,
              name,
            ]) => (

              <Button
                key={
                  key
                }

                onClick={
                  () => {
                    setSelected(
                      key
                    );

                    setRemote(
                      []
                    );

                    setMapping(
                      []
                    );
                  }
                }

                style={{
                  ...ghost,

                  background: selected === key ? "#2563eb" : "#FFFFFF",
                  color: selected === key ? "#FFFFFF" : "#334155",
                  borderColor: selected === key ? "#2563eb" : "#cbd5e1",
                  fontWeight: 650,
                }}
              >
                {name}
              </Button>

            )
          )}

        </div>


        <div
          className="boris-integrations-grid"
          style={{
            display:
              "grid",

            gridTemplateColumns:
              "440px minmax(0,1fr)",

            gap:
              16,

            marginTop:
              14,
          }}
        >

          <div
            style={
              card
            }
          >

            <h3
              style={{
                marginTop:
                  0,
              }}
            >
              Подключение
            </h3>


            <Field
              label={
                "Идентификатор клиента (Client ID)"
              }

              value={
                form.client_id
              }

              onChange={
                value =>
                  setForm({
                    ...form,
                    client_id:
                      value,
                  })
              }
            />


            <Field
              label={
                "Секретный ключ (Client Secret)"
              }

              type={
                "password"
              }

              value={
                form.client_secret
              }

              onChange={
                value =>
                  setForm({
                    ...form,
                    client_secret:
                      value,
                  })
              }
            />


            {selected ===
            "bitrix24" ? (

              <Field
                label={
                  "Портал"
                }

                placeholder={
                  "company.bitrix24.ru"
                }

                value={
                  form.external_domain
                }

                onChange={
                  value =>
                    setForm({
                      ...form,
                      external_domain:
                        value,
                    })
                }
              />

            ) : null}


            <Field
              label={
                "Адрес возврата (Redirect URI)"
              }

              value={
                form.redirect_uri
                || callbackUrl(
                  selected
                )
              }

              onChange={
                value =>
                  setForm({
                    ...form,
                    redirect_uri:
                      value,
                  })
              }
            />


            <div
              style={{
                marginTop:
                  12,
              }}
            >

              <div
                style={
                  label
                }
              >
                РЕЖИМ
              </div>


              <select
                value={
                  form.mode
                }

                onChange={
                  e =>
                    setForm({
                      ...form,
                      mode:
                        e.target.value,
                    })
                }

                style={
                  input
                }
              >
                <option
                  value=
                    "boris_master"
                >
                  BORIS — основная система продаж
                </option>

                <option
                  value=
                    "bidirectional"
                >
                  Двусторонняя синхронизация
                </option>

                <option
                  value=
                    "external_master"
                >
                  Внешняя система продаж — основная
                </option>
              </select>

            </div>


            <Button
              disabled={
                busy
              }

              onClick={
                saveConfiguration
              }

              style={{
                ...primary,
                width:
                  "100%",
                marginTop:
                  14,
              }}
            >
              Сохранить настройки
            </Button>


            <Button
              disabled={
                busy
              }

              onClick={
                connect
              }

              style={{
                ...ghost,
                width:
                  "100%",
                marginTop:
                  8,
              }}
            >
              Подключить через авторизацию
            </Button>


            <div
              style={{
                marginTop:
                  18,

                paddingTop:
                  14,

                borderTop:
                  "1px solid var(--boris-border-soft)",

                fontSize:
                  13,
              }}
            >

              <div>
                Статус:{" "}

                <b>
                  {statusLoading
                    ? "проверяем…"
                    : status?.status || "не настроено"}
                </b>
              </div>


              <div
                style={{
                  marginTop:
                    5,
                }}
              >
                Сопоставлено этапов:{" "}

                <b>
                  {statusLoading ? "—" : status?.mapped_stages ?? 0}
                </b>
              </div>


              <div
                style={{
                  marginTop:
                    5,
                }}
              >
                Запись наружу:{" "}

                <b>
                  {statusLoading
                    ? "—"
                    : status?.external_writes_enabled ? "включена" : "выключена"}
                </b>
              </div>


              {status?.last_error ? (

                <div
                  style={{
                    marginTop:
                      8,

                    color:
                      "var(--b-danger-text)",

                    fontSize:
                      12,
                  }}
                >
                  {humanizeBusinessText(status.last_error,"Интеграция временно недоступна. BORIS повторно проверит подключение.")}
                </div>

              ) : null}

            </div>

          </div>


          <div
            style={
              card
            }
          >

            <div
              style={{
                display:
                  "flex",

                justifyContent:
                  "space-between",

                gap:
                  10,
              }}
            >

              <div>

                <h3
                  style={{
                    margin:
                      0,
                  }}
                >
                  Воронка и этапы
                </h3>

                <div
                  style={{
                    color:
                      "var(--boris-text-2)",

                    fontSize:
                      12,

                    marginTop:
                      4,
                  }}
                >
                  Каждый этап BORIS
                  должен соответствовать
                  внешнему этапу.
                </div>

              </div>


              <Button
                onClick={
                  loadMapping
                }

                disabled={
                  busy
                  || status?.status
                    !== "active"
                }

                style={
                  ghost
                }
              >
                Загрузить этапы
              </Button>

            </div>


            {mapping.length
              ? (

                <div
                  style={{
                    marginTop:
                      15,
                  }}
                >

                  {mapping.map(
                    local => (

                      <div
                        className="boris-integrations-map-row"
                        key={
                          local.id
                        }

                        style={{
                          display:
                            "grid",

                          gridTemplateColumns:
                            "220px 1fr",

                          gap:
                            12,

                          padding:
                            "10px 0",

                          borderBottom:
                            "1px solid var(--boris-border-soft)",

                          alignItems:
                            "center",
                        }}
                      >

                        <div>

                          <div
                            style={{
                              fontWeight:
                                700,

                              fontSize:
                                13,
                            }}
                          >
                            {local.name}
                          </div>

                          {local.external_stage_name
                            ? (

                              <div
                                style={{
                                  color:
                                    "#12B76A",

                                  fontSize:
                                    11,

                                  marginTop:
                                    3,
                                }}
                              >
                                →{" "}
                                {local.external_pipeline_name}
                                {" / "}
                                {local.external_stage_name}
                              </div>

                            )
                            : null}

                        </div>


                        <select
                          value={
                            local.external_stage_id
                              ? (
                                  `${local.external_pipeline_id}`
                                  + "::"
                                  + `${local.external_stage_id}`
                                )
                              : ""
                          }

                          onChange={
                            e =>
                              setStageMap(
                                local,
                                e.target.value
                              )
                          }

                          style={
                            input
                          }
                        >

                          <option value="">
                            Выберите внешний этап
                          </option>


                          {remote.map(
                            pipeline => (

                              <optgroup
                                key={
                                  pipeline.id
                                }

                                label={
                                  pipeline.name
                                }
                              >

                                {pipeline.stages.map(
                                  stage => (

                                    <option
                                      key={
                                        pipeline.id
                                        + ":"
                                        + stage.id
                                      }

                                      value={
                                        pipeline.id
                                        + "::"
                                        + stage.id
                                      }
                                    >
                                      {stage.name}
                                    </option>

                                  )
                                )}

                              </optgroup>

                            )
                          )}

                        </select>

                      </div>

                    )
                  )}

                </div>

              )
              : (

                <div
                  style={{
                    color:
                      "var(--boris-text-3)",

                    marginTop:
                      30,

                    textAlign:
                      "center",
                  }}
                >
                  После авторизации нажмите
                  «Загрузить этапы».
                </div>

              )}


            <div
              style={{
                display:
                  "flex",

                flexWrap:
                  "wrap",

                gap:
                  8,

                marginTop:
                  18,

                paddingTop:
                  16,

                borderTop:
                  "1px solid var(--boris-border-soft)",
              }}
            >

              <Button
                disabled={
                  !status
                  || status.status
                    !== "active"
                  || !status.mapped_stages
                }

                onClick={
                  () =>
                    toggleWrites(
                      !status
                        ?.external_writes_enabled
                    )
                }

                style={
                  status
                    ?.external_writes_enabled
                    ? danger
                    : primary
                }
              >
                {status
                  ?.external_writes_enabled
                  ? "Выключить внешнюю запись"
                  : "Включить синхронизацию"}
              </Button>


              <Button
                disabled={
                  !status
                    ?.external_writes_enabled
                }

                onClick={
                  pushAll
                }

                style={
                  ghost
                }
              >
                Отправить текущие данные продаж
              </Button>


              <Button
                disabled={
                  status?.status
                    !== "active"
                }

                onClick={
                  importRemote
                }

                style={
                  ghost
                }
              >
                Импортировать из системы продаж
              </Button>

            </div>

          </div>

        </div>

      </div>

    </div>
  );
}


function Field({
  label: labelText,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (
    value: string
  ) => void;
  placeholder?: string;
  type?: string;
}) {

  return (
    <div
      style={{
        marginTop:
          12,
      }}
    >

      <div
        style={
          label
        }
      >
        {labelText.toUpperCase()}
      </div>

      <input
        type={
          type
        }

        value={
          value
        }

        placeholder={
          placeholder
        }

        onChange={
          e =>
            onChange(
              e.target.value
            )
        }

        style={
          input
        }
      />

    </div>
  );
}


const card: React.CSSProperties = {
  background:
    "#FFFFFF",

  border:
    "1px solid var(--boris-border)",

  borderRadius:
    14,

  padding:
    18,
};


const input: React.CSSProperties = {
  width:
    "100%",

  height:
    40,

  border:
    "1px solid var(--boris-border)",

  borderRadius:
    9,

  padding:
    "0 10px",

  marginTop:
    5,

  background:
    "#FFFFFF",
};


const label: React.CSSProperties = {
  fontSize:
    11,

  fontWeight:
    700,

  color:
    "var(--boris-text-3)",
};


const ghost: React.CSSProperties = {
  border: "1px solid #cbd5e1",
  background: "#FFFFFF",
  color: "#1e293b",
  borderRadius: 11,
  padding: "10px 14px",
  cursor: "pointer",
  fontWeight: 600,
  boxShadow: "0 3px 10px rgba(15,23,42,.04)",
};


const primary: React.CSSProperties = {
  border: "1px solid #2563eb",
  background: "#2563eb",
  color: "#FFFFFF",
  borderRadius: 11,
  padding: "10px 14px",
  cursor: "pointer",
  fontWeight: 650,
  boxShadow: "0 6px 16px rgba(37,99,235,.16)",
};


const danger: React.CSSProperties = {
  border:
    0,

  background:
    "var(--b-danger-text)",

  color:
    "#FFFFFF",

  borderRadius:
    9,

  padding:
    "9px 12px",

  cursor:
    "pointer",

  fontWeight:
    700,
};
