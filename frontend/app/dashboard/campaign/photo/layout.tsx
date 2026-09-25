import Link from "next/link";

export default function CampaignPhotoLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <div
        style={{
          maxWidth: 1180,
          margin: "18px auto 0",
          padding: "0 22px",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 16,
            flexWrap: "wrap",
            padding: "14px 16px",
            border: "1px solid #dfe7f5",
            borderRadius: 14,
            background: "#f6f9ff",
          }}
        >
          <div>
            <div
              style={{
                fontWeight: 800,
                color: "#1d2b46",
              }}
            >
              AI Фотостудия BORIS
            </div>

            <div
              style={{
                marginTop: 3,
                color: "#69758a",
                fontSize: 13,
              }}
            >
              Несколько реальных фото → новые
              ракурсы → галерея объявлений.
              Сейчас платные генераторы отключены.
            </div>
          </div>

          <Link
            href="/dashboard/photo-factory"
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "10px 14px",
              borderRadius: 10,
              textDecoration: "none",
              background: "#2463eb",
              color: "#fff",
              fontSize: 14,
              fontWeight: 750,
              whiteSpace: "nowrap",
            }}
          >
            Открыть AI Фотостудию
          </Link>
        </div>
      </div>

      {children}
    </>
  );
}
