// Next.js Admin Panel for БОРИС SaaS Landing page editing
// Save to: frontend/app/admin/landing/page.tsx

'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Save, RotateCcw, Laptop, Smartphone, Trash2, Plus, Upload, CheckCircle, Palette, Undo } from 'lucide-react';

export default function BorisAdminLandingPage() {
  const router = useRouter();
  const [accessChecked, setAccessChecked] = useState(false);

  useEffect(() => {
    // Защита: только владелец (owner) может видеть эту страницу.
    // Проверка клиентская (для UX — скрыть форму от посторонних), но реальная защита
    // всё равно на бэкенде через Depends(get_current_user) + проверку роли owner.
    const role = typeof window !== 'undefined' ? typeof window !== "undefined" && localStorage.getItem('boris_user_role') : null;
    const token = typeof window !== 'undefined' ? typeof window !== "undefined" && localStorage.getItem('boris_token') : null;
    if (!token || role !== 'owner') {
      router.push('/login');
      return;
    }
    setAccessChecked(true);
  }, [router]);

  const [editorState, setEditorState] = useState<Record<string, any>>({});
  const [selectedSection, setSelectedSection] = useState<string>('hero');
  const [activeHistory, setActiveHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, 'idle' | 'success' | 'error'>>({});
  const [uploading, setUploading] = useState(false);
  const [uploadUrl, setUploadUrl] = useState<string | null>(null);

  const loadData = () => {
    setLoading(true);
    fetch('/api/landing/content')
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          setEditorState(data.content || {});
        }
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setLoading(false);
      });
  };

  useEffect(() => {
    loadData();
  }, []);

  const fetchBlockHistory = (key: string) => {
    fetch(`/api/admin/landing/history/${key}`, { headers: { 'Authorization': `Bearer ${typeof window !== "undefined" && localStorage.getItem('boris_token')}` } })
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          setActiveHistory(data.history || []);
        }
      })
      .catch(err => console.error(err));
  };

  const handleSaveBlock = (key: string) => {
    setSavingKey(key);
    setSaveStatus(prev => ({ ...prev, [key]: 'idle' }));

    fetch('/api/admin/landing/content', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${typeof window !== "undefined" && localStorage.getItem('boris_token')}` },
      body: JSON.stringify({ block_key: key, value: editorState[key] })
    })
      .then(res => res.json())
      .then(data => {
        setSavingKey(null);
        if (data.success) {
          setSaveStatus(prev => ({ ...prev, [key]: 'success' }));
          fetchBlockHistory(key);
          setTimeout(() => {
            setSaveStatus(prev => ({ ...prev, [key]: 'idle' }));
          }, 2000);
        } else {
          setSaveStatus(prev => ({ ...prev, [key]: 'error' }));
        }
      })
      .catch(err => {
        console.error(err);
        setSavingKey(null);
        setSaveStatus(prev => ({ ...prev, [key]: 'error' }));
      });
  };

  const handleRollback = (key: string) => {
    if (!confirm(`Откатить изменения для блока "${key}"?`)) return;

    fetch('/api/admin/landing/rollback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${typeof window !== "undefined" && localStorage.getItem('boris_token')}` },
      body: JSON.stringify({ block_key: key })
    })
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          alert('Изменения отменены!');
          loadData();
        } else {
          alert('Ошибка отката');
        }
      })
      .catch(err => console.error(err));
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    const reader = new FileReader();
    reader.onloadend = () => {
      fetch('/api/admin/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${typeof window !== "undefined" && localStorage.getItem('boris_token')}` },
        body: JSON.stringify({ filename: file.name, base64Data: reader.result })
      })
        .then(res => res.json())
        .then(data => {
          setUploading(false);
          if (data.success) {
            setUploadUrl(data.imageUrl);
          } else {
            alert('Загрузка не удалась');
          }
        })
        .catch(err => {
          console.error(err);
          setUploading(false);
        });
    };
    reader.readAsDataURL(file);
  };

  const updateKey = (key: string, val: any) => {
    setEditorState(prev => ({ ...prev, [key]: val }));
  };

  if (loading) {
    return <div className="p-8 text-center text-slate-400">Загрузка панели...</div>;
  }

  const sections = [
    { id: 'hero', name: 'Главный экран (Hero)' },
    { id: 'colors', name: 'Палитра цветов' },
    { id: 'pains', name: 'Боли и решения (34 шт)' },
    { id: 'pricing', name: 'Тариф «Автопилот»' },
    { id: 'trial', name: 'Бесплатный Триал' },
    { id: 'faq', name: 'FAQ' },
    { id: 'footer', name: 'Контакты и Подвал' }
  ];

  if (!accessChecked) return null;

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col font-sans">
      <nav className="bg-slate-950 px-6 py-4 border-b border-slate-800 flex justify-between items-center">
        <h1 className="font-extrabold text-lg">БОРИС | Админка контента</h1>
      </nav>

      <div className="flex-1 flex overflow-hidden">
        {/* Left Side: Navigation */}
        <div className="w-64 bg-slate-950 p-4 border-r border-slate-800 space-y-2">
          <p className="text-[10px] font-bold text-slate-500 uppercase px-2 mb-2">Блоки лендинга</p>
          {sections.map(sec => (
            <button
              key={sec.id} onClick={() => setSelectedSection(sec.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg text-xs font-bold transition-all ${
                selectedSection === sec.id ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-slate-900'
              }`}
            >
              {sec.name}
            </button>
          ))}
        </div>

        {/* Right Side: Edit Form */}
        <div className="flex-1 p-6 overflow-y-auto space-y-8 bg-slate-950/20">
          {selectedSection === 'hero' && (
            <div className="space-y-6">
              <h2 className="font-bold border-b border-slate-800 pb-3">Главный экран (Hero)</h2>
              {renderField('hero.title', 'Заголовок Hero', 'text')}
              {renderField('hero.subtitle', 'Подзаголовок', 'textarea')}
              {renderField('hero.badge', 'Хит-бейдж сверху', 'text')}
              {renderField('hero.cta', 'Текст кнопки CTA', 'text')}
            </div>
          )}

          {selectedSection === 'colors' && (
            <div className="space-y-6">
              <h2 className="font-bold border-b border-slate-800 pb-3">Фирменная цветовая палитра</h2>
              <div className="grid grid-cols-2 gap-4">
                {renderColor('color.bg', 'Цвет фона')}
                {renderColor('color.accent', 'Акцентный цвет')}
                {renderColor('color.light', 'Светлый вспомогательный тон')}
                {renderColor('color.text', 'Основной текст')}
                {renderColor('color.textMuted', 'Приглушенный текст')}
                {renderColor('color.success', 'Цвет Успеха')}
                {renderColor('color.warning', 'Цвет Внимания')}
              </div>
            </div>
          )}

          {selectedSection === 'pains' && (
            <div className="space-y-6 max-h-[500px] overflow-y-auto pr-2">
              <h2 className="font-bold border-b border-slate-800 pb-3">Боли и решения (34 пары)</h2>
              {Array.from({ length: 34 }).map((_, idx) => {
                const num = idx + 1;
                return (
                  <div key={num} className="p-4 bg-slate-950 border border-slate-800 rounded-xl space-y-4">
                    <p className="text-xs font-black text-blue-400">Пункт {num}</p>
                    {renderField(`pain.${num}.title`, `Боль ${num}`, 'text')}
                    {renderField(`pain.${num}.solution`, `Решение ${num}`, 'textarea')}
                  </div>
                );
              })}
            </div>
          )}

          {selectedSection === 'pricing' && (
            <div className="space-y-6">
              <h2 className="font-bold border-b border-slate-800 pb-3">Настройка тарифа «Автопилот»</h2>
              {renderField('pricing.title', 'Заголовок тарифов', 'text')}
              {renderField('pricing.subtitle', 'Подзаголовок', 'textarea')}
              {renderField('pricing.amount', 'Стоимость тарифа (число)', 'number')}
              {renderField('pricing.period', 'Период оплаты', 'text')}
            </div>
          )}

          {selectedSection === 'trial' && (
            <div className="space-y-6">
              <h2 className="font-bold border-b border-slate-800 pb-3">Условия бесплатного триала</h2>
              {renderField('trial.title', 'Заголовок', 'text')}
              {renderField('trial.subtitle', 'Подзаголовок', 'textarea')}
              {renderField('trial.days', 'Дней триала', 'number')}
              {renderField('trial.bannerLimit', 'Лимит баннеров', 'number')}
              {renderField('trial.templateLimit', 'Лимит шаблонов', 'number')}
              {renderField('trial.stockLimit', 'Лимит картинок стока', 'number')}
            </div>
          )}

          {selectedSection === 'faq' && (
            <div className="space-y-6">
              <h2 className="font-bold border-b border-slate-800 pb-3">Раздел FAQ</h2>
              {renderField('faq.title', 'Заголовок секции', 'text')}
              <p className="text-xs text-slate-400">Частые вопросы редактируются через соответствующий массив в базе.</p>
            </div>
          )}

          {selectedSection === 'footer' && (
            <div className="space-y-6">
              <h2 className="font-bold border-b border-slate-800 pb-3">Подвал и контакты</h2>
              {renderField('footer.contacts', 'Поддержка и контакты', 'textarea')}
              {renderField('footer.disclaimer', 'Дисклеймер (без брендов!)', 'textarea')}
              {renderField('footer.copyright', 'Копирайт', 'text')}

              {/* Uploader showcase */}
              <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl space-y-4">
                <p className="text-xs font-bold text-slate-300">Загрузка скриншотов / медиафайлов</p>
                <div className="flex gap-4 items-center">
                  <label className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs font-bold cursor-pointer">
                    Загрузить файл
                    <input type="file" accept="image/*" onChange={handleFileUpload} className="hidden" />
                  </label>
                  {uploading && <span className="text-xs text-slate-400 animate-pulse">Загрузка...</span>}
                  {uploadUrl && <span className="text-xs text-emerald-400">Ссылка: {uploadUrl}</span>}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  function renderField(key: string, label: string, type: 'text' | 'textarea' | 'number') {
    const val = editorState[key] !== undefined ? editorState[key] : '';
    const status = saveStatus[key] || 'idle';
    return (
      <div className="space-y-2">
        <div className="flex justify-between items-center">
          <label className="text-xs font-bold text-slate-400">{label}</label>
          <div className="flex gap-2">
            <button
              onClick={() => handleSaveBlock(key)}
              className={`px-3 py-1 rounded text-[10px] font-bold ${
                status === 'success' ? 'bg-emerald-600 text-white' : 'bg-blue-600 text-white'
              }`}
            >
              {status === 'success' ? 'Сохранено' : 'Сохранить'}
            </button>
            <button
              onClick={() => {
                fetchBlockHistory(key);
                if (activeHistory.length > 0 && activeHistory[0].block_key === key) {
                  setActiveHistory([]);
                }
              }}
              className="px-2 py-1 bg-slate-800 text-slate-300 rounded text-[10px] font-bold"
            >
              История
            </button>
          </div>
        </div>

        {activeHistory.length > 0 && activeHistory[0].block_key === key && (
          <div className="p-2 bg-slate-950 border border-slate-800 rounded-lg text-[10px] space-y-1.5">
            {activeHistory.map(h => (
              <div key={h.id} className="flex justify-between items-center bg-slate-900 p-1.5 rounded">
                <span className="text-slate-400">{new Date(h.changed_at).toLocaleDateString()}</span>
                <button onClick={() => handleRollback(key)} className="px-1.5 py-0.5 bg-blue-600/20 text-blue-400 rounded text-[9px] font-black">
                  Откатить
                </button>
              </div>
            ))}
          </div>
        )}

        {type === 'textarea' ? (
          <textarea
            value={val} onChange={(e) => updateKey(key, e.target.value)} rows={3}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-white focus:outline-none"
          />
        ) : (
          <input
            type={type === 'number' ? 'number' : 'text'}
            value={val} onChange={(e) => updateKey(key, type === 'number' ? Number(e.target.value) : e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs text-white focus:outline-none"
          />
        )}
      </div>
    );
  }

  function renderColor(key: string, label: string) {
    const val = editorState[key] || '#FFFFFF';
    return (
      <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl flex items-center gap-3">
        <input type="color" value={val} onChange={(e) => updateKey(key, e.target.value)} className="w-8 h-8 rounded border-0 cursor-pointer" />
        <div className="flex-1">
          <span className="text-[10px] font-bold text-slate-500 uppercase block">{label}</span>
          <input type="text" value={val} onChange={(e) => updateKey(key, e.target.value)} className="w-full bg-transparent border-0 text-xs text-white font-mono p-0 focus:outline-none focus:ring-0" />
        </div>
        <button onClick={() => handleSaveBlock(key)} className="p-1.5 bg-blue-600 rounded text-white"><Save size={12} /></button>
      </div>
    );
  }
}
