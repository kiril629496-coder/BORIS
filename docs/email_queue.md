# Очередь писем BORIS AI

Как устроена отправка почты, что делать при сбое и куда смотреть при разборе.
Актуально на 02.08.2026, после этапов A и B.

## 1. Архитектура

Единая цепочка: вызывающий код → очередь → транспорт → SMTP Яндекс 360.

| Компонент | Файл | Назначение |
|---|---|---|
| EmailService | `app/services/email_service.py` | сборка письма и отправка по SMTP. Единственное место в проекте с `smtplib` |
| Очередь | `app/services/email_queue.py` | постановка, захват, повторы, статистика |
| Обёртка совместимости | `app/services/mailer.py` | старый интерфейс `send_mail` / `send_verification_code`, внутри зовёт EmailService |
| Воркер | `email_queue_runner.py` | добивает письма, ждущие повтора. Запускается из cron раз в минуту |

Собственные подключения `smtplib` в других файлах создавать нельзя. `app/api/support.py` и
`app/api/sitebuild.py` переведены на EmailService на этапе A.

### Таблицы

`email_queue` — само письмо и его состояние: получатели, тема, текст и HTML, отправитель,
`reply_to`, заголовки, `status`, `attempts`, `max_attempts`, `next_attempt_at`, `last_error`,
`provider_message_id`, `sent_at`, `idempotency_key`, `source`.

`email_delivery_events` — журнал: `queued`, `sent`, `retrying`, `failed`, `duplicate`.
Одно письмо — несколько событий, порядок по `id`.

Уникальность: частичный индекс `ux_email_queue_idem` по `idempotency_key`, где ключ не пуст.

## 2. Жизненный цикл письма

```
queued → processing → sent
queued → processing → retrying → sent
queued → processing → dead
duplicate → возврат существующего id без новой отправки
```

`processing` — это статус `sending` в базе; в `queue_stats()` он отдаётся под обоими именами.

`enqueue_email(..., send_now=True)` кладёт письмо и **сразу** пробует отправить в том же
запросе. Поэтому код подтверждения не ждёт следующего тика cron. Если попытка не удалась,
письмо остаётся в очереди и его добивает воркер.

## 3. Идемпотентность

Ключ передаётся вызывающим кодом: `enqueue_email(..., idempotency_key="verify-42-1785...")`.
Хранится в колонке `idempotency_key`, защищён уникальным частичным индексом.

При повторном вызове с тем же ключом письмо **не создаётся**. Вызывающий код получает:

```python
{"id": 5, "status": "sent", "duplicate": True}
```

то есть `id` уже существующего письма и признак `duplicate`. В журнал пишется событие
`duplicate`, счётчик виден в `queue_stats()["duplicates"]`.

Без ключа защиты нет — два вызова создадут два письма. Для критичных писем
(подтверждение почты, восстановление пароля) ключ обязателен.

## 4. Ошибки: постоянные и временные

Причина отказа приходит из EmailService в виде `ИмяИсключения` либо `ИмяИсключения:КодSMTP`
(например `SMTPDataError:550`).

**Permanent — сразу в `dead`, повторов нет:**

- `SMTPRecipientsRefused` — адрес не принят
- `SMTPNotSupportedError` — сервер не умеет нужного расширения (например кириллица в адресе без SMTPUTF8)
- `SMTPAuthenticationError` — не приняты учётные данные
- `SMTPSenderRefused` — не принят отправитель
- `UnicodeEncodeError`
- любой SMTP-код на `5xx`

**Temporary — уходит в retry:**

- `SMTPServerDisconnected`, `SMTPConnectError`
- таймауты соединения и чтения
- `SMTPDataError` с кодом `4xx`
- сетевые ошибки

Правило простое: постоянная ошибка не станет лучше от повтора, поэтому письмо помечается
`dead` уже на первой попытке. Временная — повторяется до `max_attempts` (по умолчанию 5),
после чего тоже уходит в `dead`, но с пометкой «исчерпаны попытки».

Различить причину можно по `last_error` и по событию `failed` в журнале: там записано
`постоянная ошибка: ...` либо `исчерпаны попытки: ...`.

## 5. Backoff

```
1 / 2 / 5 / 15 / 30 минут
```

Дальше интервал не растёт, потолок 30 минут. Время следующей попытки лежит в
`next_attempt_at`; воркер берёт только те письма, у которых это время уже наступило.

## 6. Cron

Точная строка задания:

```
* * * * * cd /root/BORIS/backend && venv/bin/python3 email_queue_runner.py >> /root/BORIS/backend/email_queue.log 2>&1
```

- лог: `/root/BORIS/backend/email_queue.log`
- heartbeat: `/root/BORIS/backend/.email_worker_heartbeat`, перезаписывается при каждом запуске

Воркер пишет в лог только когда что-то произошло — пустые проходы не логируются, иначе файл
распухнет. Поэтому «в логе тихо» не значит «воркер умер»: живость проверяется heartbeat.

**Как проверить, что воркер жив:**

```
ls -la --time-style=+%H:%M:%S /root/BORIS/backend/.email_worker_heartbeat
```

либо через метрики — `worker_alive` истинно, если последний запуск был меньше 5 минут назад.

**Как не создать дубликат записи в crontab** — добавлять только через проверку:

```
crontab -l | grep -q email_queue_runner && echo "УЖЕ ЕСТЬ" || (crontab -l; echo "<строка>") | crontab -
```

Контроль: `crontab -l | grep -c email_queue_runner` должен вернуть `1`.

## 7. Метрики `queue_stats()`

```python
from app.services import email_queue as q
q.queue_stats()
```

| Ключ | Что означает | Порог для Health Monitor |
|---|---|---|
| `queued` | ждут отправки или повтора | > 100 — тревога |
| `processing` | взяты воркером в работу | > 20 длительно — подозрение на зависание |
| `sending` | то же самое, исходное имя статуса в базе | — |
| `retrying` | в очереди и уже с неудачной попыткой | > 20 — проблемы с транспортом |
| `sent` | успешно отправлено, всего | — |
| `failed` | зарезервировано, в норме 0 | > 0 — разобрать |
| `dead` | отправка прекращена | любой рост — уведомление |
| `duplicates` | сработала защита идемпотентности | резкий рост — двойные вызовы в коде |
| `avg_send_time` | среднее время от постановки до отправки, секунды | > 60 — деградация |
| `stuck` | висят в `queued` или `sending` дольше 30 минут | > 0 — тревога |
| `last_sent_at` | время последней успешной отправки | старше суток при непустой очереди — тревога |
| `last_error` | последняя записанная ошибка | для показа в админке |
| `worker_last_run` | время последнего запуска воркера | — |
| `worker_age_min` | сколько минут назад это было | > 5 — воркер не запускается |
| `worker_alive` | запуск моложе 5 минут | `false` — тревога |

## 8. Ручные операции

Все команды выполняются в `/root/BORIS/backend`.

**Посмотреть очередь и метрики:**

```
venv/bin/python3 -c "
from dotenv import load_dotenv; load_dotenv('/root/BORIS/backend/.env')
from app.services import email_queue as q
import json; print(json.dumps(q.queue_stats(), ensure_ascii=False, indent=1))"
```

**Проверить dead-письма:**

```
venv/bin/python3 -c "
from dotenv import load_dotenv; load_dotenv('/root/BORIS/backend/.env')
from app.db.session import SessionLocal
from sqlalchemy import text
db = SessionLocal()
for r in db.execute(text(\"SELECT id, left(subject,40), attempts, last_error FROM email_queue WHERE status='dead' ORDER BY id\")).fetchall(): print(r)
db.close()"
```

**Безопасно повторить письмо** — вернуть его в очередь, обнулив счётчик:

```
venv/bin/python3 -c "
from dotenv import load_dotenv; load_dotenv('/root/BORIS/backend/.env')
from app.db.session import SessionLocal
from sqlalchemy import text
db = SessionLocal()
db.execute(text(\"UPDATE email_queue SET status='queued', attempts=0, next_attempt_at=now(), last_error=NULL WHERE id=:i\"), {'i': 42})
db.commit(); db.close(); print('письмо 42 возвращено в очередь')"
```

Повторять письмо, упавшее с постоянной ошибкой, бессмысленно — сначала исправьте причину
(адрес, учётные данные), иначе оно снова ляжет в `dead` на первой же попытке.

**Запустить воркер вручную:**

```
venv/bin/python3 email_queue_runner.py
```

**Проверить heartbeat:**

```
ls -la --time-style=+%H:%M:%S /root/BORIS/backend/.email_worker_heartbeat
```

**Диагностировать зависшее письмо** — порядок такой:

1. `queue_stats()` — смотрим `stuck` и `worker_alive`.
2. Если `worker_alive` ложно — воркер не запускается, проверяем `crontab -l | grep -c email_queue_runner` и хвост лога.
3. Если воркер жив, а письмо висит — смотрим его строку: `status`, `attempts`, `next_attempt_at`, `last_error`.
4. `status='sending'` дольше нескольких минут означает, что процесс упал между захватом и записью результата. Вернуть в очередь вручную (см. выше).
5. `queued` с будущим `next_attempt_at` — это норма, письмо ждёт своей паузы.

## 9. Безопасность и эксплуатационные правила

- Пароли SMTP, тела писем и коды подтверждения **в лог не пишутся**. В логе только идентификатор письма, класс ошибки и SMTP-код.
- В `.env` любое значение с пробелом — **только в кавычках**: `EMAIL_FROM_NAME="BORIS AI"`. Файл читает не только python, но и bash (`backup_db.sh`, cron автопостинга), и незакавыченный пробел ломает их с `command not found`.
- После каждой правки `.env` обязательно `bash -n /root/BORIS/backend/.env`.
- Тесты никогда не удаляют боевые строки. Всё созданное QA помечается `source='qa_auto'`, и очистка идёт строго по этой метке.
- В тестах транспорт подменяется заглушкой, наружу письма не уходят. Живая отправка — только по явному флагу `--real`.
- Не запускать миграцию `001_email_verification.sql` — внутри есть data-migration, подтверждающая почту всем ожидающим пользователям.

## 10. Восстановление

**Бэкапы базы:** `/root/BORIS/backups/`, ночной cron `backup_db.sh`, хранится 7 копий.
Бэкап перед миграцией очереди — `boris_db_20260802_142826.sql.gz`.

**Миграция 002** — `migrations/002_email_queue.sql`, обёртка `migrations/run_002.py`:

```
venv/bin/python3 migrations/run_002.py test    # сухой прогон, ROLLBACK, база не меняется
venv/bin/python3 migrations/run_002.py apply   # бэкап и COMMIT
```

Миграция идемпотентна: повторный прогон ничего не портит и не трогает данные.

**Откат cron** — бэкапы лежат в `/root/BORIS/backups/emailA/crontab.bak_<метка>`:

```
crontab /root/BORIS/backups/emailA/crontab.bak_<метка>
```

Либо убрать одну строку: `crontab -l | grep -v email_queue_runner | crontab -`.
Письма при этом не теряются — они остаются в очереди и уйдут после возврата воркера.

**Восстановление файлов** — рядом с каждым изменённым файлом лежит бэкап с меткой времени:
`*.before_emailA_<ts>`, `*.before_b3_<ts>`, `*.before_importos`, `*.before_stats2`,
плюс копии в `/root/BORIS/backups/emailA/`. После возврата файла проверять
`venv/bin/python3 -c "from app.services import email_queue"` и только потом рестартовать бэкенд.
