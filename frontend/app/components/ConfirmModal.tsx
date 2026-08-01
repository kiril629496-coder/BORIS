'use client';
import React from 'react';

type ModalKind = "confirm" | "danger" | "info";

interface ConfirmModalProps {
  open: boolean;
  title: string;
  message?: string;
  kind?: ModalKind;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void;
  onCancel: () => void;
  children?: React.ReactNode; // для форм внутри модалки (сумма/период оплаты)
}

export const ConfirmModal: React.FC<ConfirmModalProps> = ({
  open, title, message, kind = "confirm",
  confirmText = "Подтвердить", cancelText = "Отмена",
  onConfirm, onCancel, children,
}) => {
  if (!open) return null;
  const accent = kind === "danger" ? "#F04438" : "#2F6FED";
  return (
    <div onClick={(e) => { e.stopPropagation(); }} style={{
      position: "fixed", inset: 0, background: "rgba(16,24,40,0.45)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9999,
      backdropFilter: "blur(2px)",
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: "#FFFFFF", borderRadius: "16px", padding: "28px",
        width: "440px", maxWidth: "90vw", boxShadow: "0 20px 60px rgba(16,24,40,0.25)",
        border: "1px solid #E3E7F0",
      }}>
        <div style={{ fontSize: "19px", fontWeight: "bold", color: "#101828", marginBottom: message ? "10px" : "18px" }}>
          {title}
        </div>
        {message && (
          <div style={{ fontSize: "15px", color: "#475467", lineHeight: "1.5", marginBottom: "18px" }}>
            {message}
          </div>
        )}
        {children && <div style={{ marginBottom: "20px" }}>{children}</div>}
        <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
          <button onClick={(e) => { e.stopPropagation(); onCancel(); }} className="boris-btn-hover" style={{
            background: "#FFFFFF", color: "#475467", border: "1px solid #D0D5DD",
            borderRadius: "8px", padding: "10px 18px", cursor: "pointer", fontSize: "15px", fontWeight: "500",
          }}>{cancelText}</button>
          <button onClick={(e) => { e.stopPropagation(); onConfirm(); }} className="boris-btn-hover" style={{
            background: accent, color: "#FFFFFF", border: "none",
            borderRadius: "8px", padding: "10px 18px", cursor: "pointer", fontSize: "15px", fontWeight: "bold",
          }}>{confirmText}</button>
        </div>
      </div>
    </div>
  );
};
