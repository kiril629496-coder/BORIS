// Next.js page for Public Offer (Оферта)
// Save to: frontend/app/oferta/page.tsx

'use client';

import React from 'react';
import { Mascot } from '../components/Mascot';
import { ArrowLeft, BookOpen, Clock, FileText } from 'lucide-react';

export const OfertaPage: React.FC = () => {
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
              Юридический документ
            </span>
            <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-[#14161A]">
              Публичная оферта
            </h1>
            <p className="text-xs text-[#5B6472] flex items-center gap-1.5">
              <Clock size={12} />
              Действует до замены новой редакцией
            </p>
          </div>
          <FileText size={48} className="text-[#2F6FED] shrink-0 opacity-80 hidden sm:block" />
        </div>

        {/* Text Container with Elegant Typography */}
        <div className="bg-white border border-[#EAF4FF] rounded-[24px] p-6 sm:p-10 shadow-sm space-y-6 text-sm leading-relaxed text-slate-700">
          <p className="font-semibold text-slate-900 border-b border-[#EAF4FF] pb-4">
            Публичная оферта на оказание услуг платформы «БОРИС»
            <br />
            <span className="text-xs font-normal text-slate-500">Редакция от 8 июля 2026 г. Действует до замены новой редакцией.</span>
          </p>

          <div className="space-y-6">
            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">1. Общие положения</h2>
              <p>
                1.1. Настоящий документ является публичной офертой (предложением) ИП Остапенко Кирилл Олегович, ИНН 911002277804, ОГРНИП 322784700115639, юридический адрес: 297406, Республика Крым, г. Евпатория, ул. Интернациональная, д. 117, кв. 16 (далее — «Исполнитель») в адрес любого физического или юридического лица (далее — «Заказчик») заключить договор на изложенных ниже условиях.
              </p>
              <p>
                1.2. Акцептом настоящей оферты (полным и безоговорочным принятием её условий) является факт регистрации Заказчика в личном кабинете на сайте boris-ai.pro и/или факт оплаты услуг.
              </p>
              <p>
                1.3. Договор считается заключённым в простой письменной форме с момента акцепта оферты в соответствии со ст. 428 и 438 Гражданского кодекса РФ.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">2. Предмет договора</h2>
              <p>
                2.1. Исполнитель предоставляет Заказчику доступ к SaaS-платформе «БОРИС» — программному обеспечению для автоматизации размещения объявлений на досках объявлений, генерации рекламных текстов и изображений с использованием технологий искусственного интеллекта, а также иных сопутствующих функций, описанных на сайте boris-ai.pro (далее — «Сервис»).
              </p>
              <p>
                2.2. Перечень и объём функций Сервиса, включённых в тариф, актуальны на дату оплаты и указаны в разделе «Тариф» на сайте.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">3. Тарифы и порядок оплаты</h2>
              <p>
                3.1. Действующий тариф «Автопилот»: 7 000 (семь тысяч) рублей за 30 (тридцать) календарных дней доступа.
              </p>
              <p>
                3.2. Заказчику предоставляется бесплатный тестовый период — 4 (четыре) календарных дня с момента регистрации, с ограниченными лимитами расходуемых ресурсов согласно описанию на сайте.
              </p>
              <p>
                3.3. По истечении тестового периода доступ к полному функционалу предоставляется после оплаты тарифа. Оплата производится через платёжный сервис Робокасса банковской картой или иным доступным способом.
              </p>
              <p>
                3.4. Дополнительные ресурсы сверх лимита тарифа (баннеры, шаблоны, стоковые изображения) могут быть докуплены Заказчиком отдельно, разовым платежом, по ценам, действующим на момент покупки.
              </p>
              <p>
                3.5. Не включены в стоимость тарифа и оплачиваются отдельно: баннеры повышенных форматов (Расширенный/Максимальный), услуга «Виртуальный менеджер по продажам», ведение социальных сетей, создание сайтов, настройка платного продвижения — согласно перечню на сайте.
              </p>
              <p>
                3.6. Исполнитель вправе изменять стоимость тарифа с уведомлением Заказчика не менее чем за 7 дней до начала нового расчётного периода. Уже оплаченный период предоставляется по цене на момент оплаты.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">4. Права и обязанности сторон</h2>
              <p>
                4.1. Исполнитель обязуется предоставить доступ к Сервису в течение оплаченного периода и обеспечивать его работоспособность, за исключением случаев плановых технических работ (с предварительным уведомлением, где это возможно) и обстоятельств непреодолимой силы.
              </p>
              <p>
                4.2. Заказчик обязуется предоставлять достоверную информацию о своём бизнесе, не использовать Сервис для размещения запрещённого законодательством РФ контента и самостоятельно нести ответственность за содержание публикуемых через Сервис объявлений.
              </p>
              <p>
                4.3. Заказчик самостоятельно несёт ответственность за соблюдение правил площадок объявлений, на которых происходит публикация, включая требования к тарифам размещения (Сервис работает с аккаунтами уровня «Расширенный» или «Максимальный» на крупнейшей доске объявлений в РФ).
              </p>
              <p>
                4.4. Исполнитель не несёт ответственности за решения площадок объявлений о блокировке, отклонении или ограничении аккаунтов Заказчика.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">5. Ответственность сторон</h2>
              <p>
                5.1. Исполнитель не несёт ответственности за косвенные убытки Заказчика, включая упущенную выгоду, возникшие в связи с использованием или невозможностью использования Сервиса.
              </p>
              <p>
                5.2. Совокупная ответственность Исполнителя перед Заказчиком по любым основаниям ограничивается суммой, уплаченной Заказчиком за последний оплаченный расчётный период.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">6. Порядок возврата средств</h2>
              <p>
                6.1. При отказе Заказчика от услуг до начала использования оплаченного периода возврат средств производится в соответствии с законодательством РФ о защите прав потребителей в течение 10 рабочих дней с момента получения соответствующего обращения.
              </p>
              <p>
                6.2. Обращение по вопросу возврата направляется на контактный адрес, указанный в разделе 8.
              </p>
            </section>

            <section className="space-y-2">
              <h2 className="font-extrabold text-slate-900 text-base">7. Срок действия и расторжение</h2>
              <p>
                7.1. Договор действует в течение оплаченного расчётного периода и автоматически прекращается по его истечении, если Заказчик не производит очередную оплату.
              </p>
              <p>
                7.2. Заказчик вправе отказаться от услуг в любой момент, уведомив Исполнителя через личный кабинет или контактные данные ниже.
              </p>
            </section>

            <section className="space-y-4 pt-4 border-t border-[#EAF4FF]">
              <h2 className="font-extrabold text-slate-900 text-base">8. Реквизиты и контакты Исполнителя</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs font-mono bg-[#F5FAFF] p-4 rounded-xl border border-[#EAF4FF]">
                <div>
                  <p className="font-bold text-slate-900">Исполнитель:</p>
                  <p className="mt-1">ИП Остапенко Кирилл Олегович</p>
                  <p>ИНН: 911002277804</p>
                  <p>ОГРНИП: 322784700115639</p>
                </div>
                <div>
                  <p className="font-bold text-slate-900">Контакты:</p>
                  <p className="mt-1">Email: ostapenko-kirill-86@yandex.ru</p>
                  <p>Тел: +7 981 967-37-87</p>
                </div>
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

export default OfertaPage;
