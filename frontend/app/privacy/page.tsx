// Next.js page for Privacy Policy (Политика конфиденциальности)
// Save to: frontend/app/privacy/page.tsx

'use client';

import React from 'react';
import { Mascot } from '../components/Mascot';
import { ArrowLeft, Clock, ShieldCheck, ShieldAlert } from 'lucide-react';

export const PrivacyPage: React.FC = () => {
  return (
    <div className="font-sans antialiased min-h-screen text-[#14161A] bg-[#FFFFFF] flex flex-col">
      {/* HEADER */}
      <header className="sticky top-0 z-50 bg-white/95 backdrop-blur-md border-b border-[#E5E7EB] h-[72px] flex items-center px-4 sm:px-6 md:px-10">
        <div className="max-w-7xl mx-auto w-full flex items-center justify-between">
          <div className="flex items-center gap-[12px] cursor-pointer">
            <Mascot size={32} interactive={true} />
            <span className="font-extrabold text-[22px] tracking-[-0.5px] text-[#14161A] font-display">
              БОРИС
            </span>
          </div>

          <a 
            href="/"
            className="inline-flex items-center gap-2 bg-[#F5FAFF] border border-[#EAF4FF] text-[#2F6FED] hover:bg-[#EAF4FF] px-4 py-2.5 rounded-[8px] text-[14px] font-semibold transition-colors"
          >
            <ArrowLeft size={16} />
            Назад на главную
          </a>
        </div>
      </header>

      {/* CONTENT BODY */}
      <main className="flex-1 max-w-4xl mx-auto px-4 sm:px-6 py-12 md:py-16 space-y-8">
        {/* Banner Card */}
        <div className="bg-[#F5FAFF] border border-[#EAF4FF] p-6 sm:p-8 rounded-[24px] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="space-y-1">
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#EAF4FF] text-[#2F6FED] text-[10px] font-extrabold uppercase tracking-wider">
              Конфиденциальность
            </span>
            <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-[#14161A]">
              Политика конфиденциальности
            </h1>
            <p className="text-xs text-[#5B6472] flex items-center gap-1.5">
              <Clock size={12} />
              Редакция от 8 июля 2026 г.
            </p>
          </div>
          <ShieldCheck size={48} className="text-[#2F6FED] shrink-0 opacity-80 hidden sm:block" />
        </div>

        {/* Text Container with Elegant Typography */}
        <div className="bg-white border border-[#EAF4FF] rounded-[24px] p-6 sm:p-10 shadow-sm space-y-6 text-sm leading-relaxed text-slate-700">
          <p className="font-semibold text-slate-900 border-b border-[#EAF4FF] pb-4">
            Политика конфиденциальности платформы «БОРИС»
            <br />
            <span className="text-xs font-normal text-slate-500">Редакция от 8 июля 2026 г. Действует до замены новой редакцией.</span>
          </p>

          <div className="space-y-6">
            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">1. Общие положения</h2>
              <p>
                1.1. Настоящая Политика конфиденциальности (далее — «Политика») определяет порядок обработки персональных данных пользователей сайта boris-ai.pro и SaaS-платформы «БОРИС» (далее — «Сервис») оператором ИП Остапенко Кирилл Олегович, ИНН 911002277804 (далее — «Оператор»).
              </p>
              <p>
                1.2. Политика разработана в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных данных».
              </p>
              <p>
                1.3. Используя Сервис и/или отправляя данные через формы на сайте, пользователь подтверждает согласие с условиями настоящей Политики.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">2. Какие данные собираются</h2>
              <p>
                2.1. При регистрации: адрес электронной почты, пароль (хранится в зашифрованном виде), название компании (по желанию пользователя).
              </p>
              <p>
                2.2. В процессе использования Сервиса: информация о бизнесе, товарах и услугах пользователя, предоставленная в диалоге с ИИ-помощником при настройке аккаунта; учётные данные для доступа к аккаунту пользователя на площадке размещения объявлений (логин и телефон, необходимые для работы функций публикации от имени пользователя); переписка с клиентами пользователя в рамках функции «Виртуальный менеджер по продажам» (в объёме, необходимом для генерации ответов).
              </p>
              <p>
                2.3. Технические данные: IP-адрес, данные о браузере и устройстве, файлы cookie — в объёме, необходимом для работы и безопасности Сервиса.
              </p>
              <p>
                2.4. Платёжные данные при оплате тарифа обрабатываются платёжным оператором (Робокасса) напрямую; Оператор не получает и не хранит номера банковских карт.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">3. Цели обработки данных</h2>
              <p>
                3.1. Предоставление доступа к функциям Сервиса и их корректная работа.
              </p>
              <p>
                3.2. Автоматизированная генерация рекламных текстов, изображений и ответов клиентам на основе данных о бизнесе пользователя.
              </p>
              <p>
                3.3. Информирование пользователя о статусе задач, окончании тестового периода, необходимости оплаты.
              </p>
              <p>
                3.4. Улучшение качества работы Сервиса, включая обучение внутренних алгоритмов на основе агрегированной статистики эффективности (без передачи третьим лицам).
              </p>
              <p>
                3.5. Исполнение требований законодательства РФ.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">4. Передача данных третьим лицам</h2>
              <p>
                4.1. Для генерации текстов, изображений и ответов Сервис использует технологии искусственного интеллекта сторонних поставщиков в объёме, необходимом для работы функций платформы.
              </p>
              <p>
                4.2. Оператор не передаёт персональные данные пользователей (email, пароли, платёжные данные) третьим лицам, за исключением случаев, прямо предусмотренных законодательством РФ, либо технических посредников, необходимых для работы Сервиса (хостинг-провайдер, платёжный оператор).
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">5. Хранение и защита данных</h2>
              <p>
                5.1. Данные хранятся на серверах, расположенных на территории Российской Федерации.
              </p>
              <p>
                5.2. Пароли хранятся в необратимо зашифрованном виде (хеширование), доступ к исходным паролям у Оператора отсутствует.
              </p>
              <p>
                5.3. Учётные данные доступа к аккаунтам на площадках объявлений хранятся в изолированном виде по каждому пользователю отдельно и используются исключительно для выполнения функций Сервиса по поручению пользователя.
              </p>
              <p>
                5.4. Оператор принимает организационные и технические меры для защиты данных от несанкционированного доступа, изменения, раскрытия или уничтожения.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">6. Сроки хранения</h2>
              <p>
                6.1. Данные хранятся в течение всего срока действия учётной записи пользователя и в течение 3 лет после её удаления — в объёме, необходимом для соблюдения требований бухгалтерского и налогового законодательства.
              </p>
              <p>
                6.2. Пользователь вправе запросить удаление своих данных ранее указанного срока в порядке, предусмотренном разделом 7, за исключением данных, хранение которых обязательно в силу закона.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">7. Права пользователя</h2>
              <p>Пользователь вправе:</p>
              <ul className="list-disc pl-5 space-y-1 text-xs">
                <li>запросить информацию о том, какие его данные обрабатываются;</li>
                <li>потребовать исправления неточных данных;</li>
                <li>отозвать согласие на обработку данных и потребовать их удаления;</li>
                <li>обратиться с жалобой в Роскомнадзор при нарушении его прав.</li>
              </ul>
              <p className="pt-2">
                Для реализации этих прав необходимо направить запрос на контактный email, указанный в разделе 9.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">8. Файлы cookie</h2>
              <p>
                8.1. Сайт может использовать файлы cookie для обеспечения работы формы регистрации, сохранения сессии пользователя и сбора обезличенной статистики посещений.
              </p>
              <p>
                8.2. Пользователь может ограничить использование cookie в настройках своего браузера; это может повлиять на корректную работу отдельных функций сайта.
              </p>
            </section>

            <section className="space-y-4 pt-4 border-t border-[#EAF4FF]">
              <h2 className="font-extrabold text-slate-900 text-base">9. Контакты Оператора</h2>
              <div className="bg-[#F5FAFF] p-4 rounded-xl border border-[#EAF4FF] text-xs font-mono">
                <p className="font-bold text-slate-900">ИП Остапенко Кирилл Олегович</p>
                <p className="mt-1">Контактный email по вопросам обработки персональных данных: ostapenko-kirill-86@yandex.ru</p>
                <p>Телефон техподдержки: +7 981 967-37-87</p>
              </div>
            </section>
          </div>
        </div>

        {/* Back Link bottom */}
        <div className="flex justify-center pt-4">
          <a 
            href="/"
            className="inline-flex items-center gap-2 bg-[#2F6FED] hover:bg-[#2058D0] text-white px-6 py-3 rounded-[8px] text-[14px] font-semibold transition-colors shadow-sm"
          >
            <ArrowLeft size={16} />
            Назад на главную страницу
          </a>
        </div>
      </main>

      {/* FOOTER */}
      <footer className="bg-slate-50 text-[#5B6472] py-8 px-4 border-t border-[#E5E7EB] text-center text-xs">
        <div className="max-w-7xl mx-auto space-y-2">
          <p>© 2026 БОРИС. Все права защищены.</p>
          <p className="text-[10px] opacity-75">SaaS-платформа ИИ-автоматизации и автоответчиков</p>
        </div>
      </footer>
    </div>
  );
};

export default PrivacyPage;
