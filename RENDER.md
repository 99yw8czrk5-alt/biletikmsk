# Render Free Deploy

Это самый простой бесплатный вариант без твоего компьютера. Render будет запускать бота как web service, а Telegram будет отправлять сообщения через webhook.

Важное ограничение бесплатного Render: сервис может засыпать после простоя. Первый ответ после сна может прийти медленно, зато бот не зависит от твоего Mac.

## 1. Залить проект на GitHub

Проект уже загружен в GitHub.

Не загружай `.env`: он уже добавлен в `.gitignore`, потому что там секреты.

## 2. Создать сервис в Render

1. Открой Render.
2. Нажми **New +**.
3. Выбери **Blueprint**.
4. Подключи GitHub-репозиторий с этим проектом.
5. Render прочитает `render.yaml`.

## 3. Заполнить переменные в Render

В Render нужно указать:

```env
TRAVELPAYOUTS_TOKEN=твой Travelpayouts token
TELEGRAM_BOT_TOKEN=твой Telegram bot token
TELEGRAM_CHAT_ID=твой Telegram chat id
WEBHOOK_SECRET=любая длинная случайная строка
```

Пример `WEBHOOK_SECRET`:

```text
bilet-msk-bkk-2026-long-secret
```

Остальные настройки уже есть в `render.yaml`: `MOW`, `BKK`, `MAX_PRICE=60000`, багаж `allow_unknown`.

## 4. Дождаться URL Render

После деплоя Render покажет адрес сервиса, например:

```text
https://bilet-msk-bkk-bot.onrender.com
```

Открой этот адрес в браузере. Если всё запустилось, увидишь:

```text
Flight Watch is running
```

## 5. Подключить Telegram webhook

Webhook-адрес для твоего Render URL можно проверить локально:

```bash
PYTHONPATH=src python3 -m flight_watch.monitor --print-webhook-url https://RENDER_URL
```

В браузере открой ссылку такого вида:

```text
https://api.telegram.org/botTELEGRAM_BOT_TOKEN/setWebhook?url=https://RENDER_URL/telegram/WEBHOOK_SECRET
```

Замени:

- `TELEGRAM_BOT_TOKEN` на токен бота.
- `RENDER_URL` на адрес Render без последнего `/`.
- `WEBHOOK_SECRET` на ту же строку, которую указал в Render.

Telegram должен ответить примерно так:

```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

## 6. Проверить бота

Напиши `@bilet_msk_bot`:

```text
цена
```

Сначала бот отправит:

```text
Ищу билеты Москва -> Бангкок...
```

Потом пришлёт варианты билетов.

## Если бот не отвечает

Проверь в Render:

1. Сервис запущен без ошибок.
2. Все переменные окружения заполнены.
3. `WEBHOOK_SECRET` в Render и в ссылке `setWebhook` одинаковый.
4. В логах Render нет ошибки `Missing required environment variable`.
