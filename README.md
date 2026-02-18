# ProxyBot (Telegram)

Telegram-бот для продажи персональных SOCKS5-прокси с тарифами и сроком действия.

## Что уже сделано

- Тарифы:
  - `1 ссылка (1 устройство)` — `10₽ / мес`
  - `5 ссылок (5 устройств)` — `25₽ / мес`
  - `15 ссылок (15 устройств)` — `50₽ / мес`
- После подтверждения оплаты бот выдаёт SOCKS5 в формате Telegram:
  - `socks5://login:password@SERVER_IP:PORT`
  - отдельно показывает `host / port / login / pass`
- Ссылки привязаны к Telegram-профилю пользователя.
- Срок действия каждой покупки — `30 дней`.
- Истёкшие подписки автоматически деактивируются, пользователь получает уведомление.

## docker-compose (бот + PostgreSQL + генерация SOCKS)

1. Создайте `.env`:

```bash
cp .env.example .env
```

2. Заполните минимум:

- `BOT_TOKEN` — токен бота
- `SERVER_IP` — публичный IP вашего сервера
- `ADMIN_TG_IDS` — ваш Telegram ID (или список через запятую)

3. Запустите:

```bash
docker compose up -d --build
```

Что поднимется:

- `postgres` — основная БД бота (PostgreSQL).
- `socks-farm` — сервис, который генерирует пул SOCKS5 (`port/login/password`) и запускает сами SOCKS5-прокси.
- `bot` — Telegram-бот, который берёт прокси из этого пула и выдаёт пользователям.

Важно:
- В `docker-compose` для `socks-farm` используется `network_mode: host` (Linux VDS) — это заметно ускоряет старт больших диапазонов (например, 1000 портов).
- Если часть портов уже занята, `socks-farm` их автоматически пропускает и помечает как `active: false` в `proxy_pool.json`.
- Бот использует только прокси с `active: true`, поэтому занятые порты автоматически отсеиваются.

## Переменные окружения

- `BOT_TOKEN` — токен Telegram-бота
- `ADMIN_TG_IDS` — список Telegram ID админов через запятую, например `123,456`
- `DATABASE_URL` — DSN PostgreSQL (если указан, бот работает с Postgres)
- `DATABASE_PATH` — путь к SQLite БД (fallback, когда `DATABASE_URL` пустой)
- `POSTGRES_DB` — имя БД контейнера Postgres
- `POSTGRES_USER` — пользователь Postgres
- `POSTGRES_PASSWORD` — пароль Postgres
- `PROXY_PUBLIC_HOST` — хост/IP, который бот вставляет в ссылки
- `PROXY_POOL_FILE` — путь к JSON-пулу прокси
- `EXPIRATION_CHECK_INTERVAL` — интервал проверки истечения (сек.)
- `SERVER_IP` — публичный IP сервера (используется в `docker-compose`)
- `SOCKS_BIND_HOST` — интерфейс bind SOCKS-сервиса
- `SOCKS_PORT_RANGE` — диапазон портов SOCKS, например `30000-30199`
- `SOCKS_POOL_FILE` — путь к файлу пула для socks-сервиса

## Команды бота

- `/start` — главное меню
- `/plans` — показать тарифы
- `/buy` — выбрать тариф
- `/my_links` — активные ссылки
- `/status` — активные подписки и остаток времени
- `/admin` — админ-панель (только для `ADMIN_TG_IDS`)

## Миграция SQLite -> PostgreSQL

Если у вас уже есть рабочая SQLite БД (`data/bot.db`), используйте скрипт:

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path data/bot.db \
  --postgres-url postgresql://proxybot:proxybot@localhost:5432/proxybot
```

По умолчанию скрипт очищает целевые таблицы Postgres перед копированием (`TRUNCATE ... CASCADE`) и переносит данные с сохранением `id`.

Через Docker (рекомендуется на сервере):

```bash
docker compose run --rm bot python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path /data/bot.db \
  --postgres-url postgresql://proxybot:proxybot@postgres:5432/proxybot
```

## Локальный запуск без Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Для локального запуска тоже нужен `PROXY_POOL_FILE` с валидным пулом прокси.
