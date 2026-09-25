# BORIS WORKSTREAM OWNERSHIP

Статус: канонический реестр границ параллельных задач.
Цель: разные чаты/агенты не должны независимо менять один и тот же production-контур.

## Правило

Перед любой правкой определить один workstream. Если задача затрагивает чужой workstream — не делать параллельную реализацию. Оставить только зависимость/факт и продолжить в своём контуре.

## WS-AVITO-MONEY — AI-авитолог / деньги / KPI

Единственный владелец логики:
- ставки и CPX;
- KPI, план лидов, CPL;
- дневные бюджеты и money mandates;
- budget brake / hard cap / raise guards;
- разгон новых объявлений по ставкам;
- winner/loser learning ставок;
- экономическое измерение результата money-действий;
- вечерний catch-up и midnight rollback;
- money safety QA и marketing rollout;
- PLANETA: только ставки, KPI, расход, бюджет и экономический результат;
- `app/services/marketing_signal_guard.py` и `app/services/marketing_clock.py` как money-policy примитивы.
- Не меняет заголовки/описания/фото даже как часть маркетингового эксперимента: это WS-AVITO-CONTENT.

Не владеет:
- фото, тексты, категории и XML-фид;
- фактом публикации/ремонтом feed, кроме чтения этого факта как входа;
- общим deploy/runtime, кроме проверки своей generation после изменений.

Связанные чаты должны НЕ создавать вторую реализацию money/KPI-контура. Сюда относится прежняя ветка «Продвижение объявлений при низком KPI» — она считается тем же workstream, а не независимой разработкой.

## WS-AVITO-INTEGRATION — общий Avito API / transport / throttle

Нейтральный single-writer слой между money и content.

Владеет только общими provider-примитивами:
- `app/api/avito.py`;
- `app/services/avito_account_throttle.py`;
- `daily_stats_collector.py` как общим read-only входом фактов Avito.

Не принимает бизнес-решения: не выбирает ставки, не задаёт бюджеты/KPI, не генерирует контент и не решает, что публиковать. Money и Content могут читать/вызывать этот слой, но не правят его параллельно.

## WS-AVITO-CONTENT — объявления / Feed Factory / публикация

Владеет:
- фото, баннеры, заголовки, описания;
- соответствие фото категории/названию/описанию;
- Avito XML, Feed Factory, validation;
- publication lifecycle, provider report, точечный repair;
- PLANETA campaign/feed/card content;
- `portfolio_bootstrap_runner.py` и `app/services/initial_portfolio.py` как восстановление/очередь публикационного портфеля, но не управление ставками.

Запрещено параллельно менять:
- ставки;
- дневные бюджеты;
- CPL/KPI;
- money mandates;
- алгоритмы CPX.

## WS-PRODUCTION-CORE — общая production-инфраструктура BORIS

Владеет:
- systemd/services;
- deploy/generation convergence;
- backend/frontend runtime health;
- guardian/supervisor общего назначения;
- очереди/worker инфраструктуру;
- глобальный production contract как инфраструктурный механизм.

Не меняет бизнес-политику AI-авитолога, ставки, CPL или бюджеты. Если инфраструктурная проблема блокирует WS-AVITO-MONEY, чинится только инфраструктурная первопричина.

Чаты «BORIS Production Buildout», «BORIS Production Watch», «production sprint», «Продолжение production работ» считаются одним WS-PRODUCTION-CORE и не должны создавать четыре параллельные реализации одной и той же инфраструктуры.

## WS-UI-MOBILE — интерфейс / мобильная версия

Владеет версткой, responsive/mobile, навигацией, UX запуска кампаний. Не меняет backend money-policy ради UI.

## WS-SOCIAL — VK/TG контент и автопостинг

Владеет постами, подписями, хэштегами, кнопками сообщества, очередями соцсетей. Не пересекается с Avito money-policy.

## WS-TELEPHONY — телефония

Владеет MCN/Telphin, звонками, АТС, call runtime и связанной аналитикой. Не меняет Avito money-policy.

## WS-OUTREACH — email/TG outreach

Владеет рассылками, прогревом, лимитами отправки и отчетами outreach. Не меняет Avito money-policy.

## Разрешенное пересечение

Разрешено только чтение фактов другого workstream как входных данных. Пример: WS-AVITO-MONEY может читать «объявление опубликовано», но не чинит XML; WS-AVITO-CONTENT может читать CPL, но не меняет ставку.

## При конфликте

1. Не делать вторую реализацию.
2. Сохранить уже работающий production-контур.
3. Отнести изменение к одному владельцу по таблице выше.
4. Если изменение действительно сквозное — менять минимальный общий интерфейс, а не две независимые логики.

## Текущий активный workstream этого чата

WS-AVITO-MONEY.
Текущий критический хвост: автономная money-экономика по production-аккаунтам — дневной budget brake, CPL/KPI, безопасные ставки, winner/loser learning и устранение причин, из-за которых доказанный winner не может безопасно масштабироваться.

Журнал `marketing_experiment_journal` принадлежит только WS-AVITO-MONEY и хранит bid/money evidence. Заголовки, описания, изображения, их observation lifecycle и content winner/loser классификация сюда больше не входят.

Граница этого чата: НЕ править content/feed/publication, MOP/CRM/reactivation, общий supervisor/control-plane, deploy/runtime, UI/mobile, social, telephony или outreach. Эти факты можно только читать как входные сигналы money-policy.
