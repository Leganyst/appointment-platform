# core

`core` — gRPC‑сервис “ядра” платформы записи: расписания провайдеров, слоты, бронирования, каталог услуг и базовые методы идентификации пользователя (Telegram) для внешних клиентов (например, бота).

## Возможности

- **CalendarService**: расписания, развёртывание повторений, выдача свободных слотов, бронирование/отмена, массовая отмена слотов провайдера, CRUD слотов, каталог услуг.
- **IdentityService**: регистрация/обновление контактов по Telegram ID, назначение роли, профиль, поиск провайдера по телефону.

gRPC reflection включён (удобно для `grpcurl`).

## Документация

- Архитектурные решения по `core`: `ARCHITECTURE.md`

## Требования

- Go версии из `go.mod` (сейчас `go 1.25.0`).
- PostgreSQL (боевой драйвер — `gorm.io/driver/postgres`).
  - Используется `gen_random_uuid()` в `gorm`‑моделях, обычно нужен `pgcrypto`: `CREATE EXTENSION IF NOT EXISTS pgcrypto;`.

## Конфигурация

Сервис читает конфиг БД из переменных окружения (dotenv не подхватывается автоматически).

| Переменная | По умолчанию | Описание |
|---|---:|---|
| `DB_HOST` | `postgres` | хост Postgres |
| `DB_PORT` | `5432` | порт Postgres |
| `DB_USER` | `booking` | пользователь |
| `DB_PASSWORD` | `booking` | пароль |
| `DB_NAME` | `booking_db` | база данных |
| `DB_SSLMODE` | `disable` | sslmode для pgx |
| `DB_TIMEZONE` | `Europe/Moscow` | параметр `TimeZone` в DSN |
| `DB_MAX_OPEN_CONNS` | `10` | лимит открытых соединений |
| `DB_MAX_IDLE_CONNS` | `5` | лимит idle соединений |
| `DB_CONN_MAX_LIFETIME_MIN` | `30` | max lifetime (минуты) |

gRPC адрес сейчас захардкожен в `cmd/main.go` как `:50051`.

## Запуск локально

1) Поднимите Postgres и создайте расширение:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

2) Экспортируйте переменные окружения (пример):

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_USER=booking
export DB_PASSWORD=booking
export DB_NAME=booking_db
export DB_SSLMODE=disable
export DB_TIMEZONE=Europe/Moscow
```

3) Запустите сервис:

```bash
go run ./cmd
```

При старте выполняется `AutoMigrate` (реализация: `internal/model/migrate.go`).

## Запуск в Docker

Сборка:

```bash
docker build -t appointment-core:local .
```

Запуск контейнера (пример, при доступном Postgres снаружи):

```bash
docker run --rm -p 50051:50051 \
  -e DB_HOST=host.docker.internal -e DB_PORT=5432 \
  -e DB_USER=booking -e DB_PASSWORD=booking -e DB_NAME=booking_db \
  appointment-core:local
```

## gRPC API

Proto‑контракты лежат в:

- `internal/api/calendar/v1/calendar.proto`
- `internal/api/identity/v1/identity.proto`
- `internal/api/common/v1/common.proto`

Быстрая проверка через `grpcurl` (reflection включён):

```bash
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext localhost:50051 list calendar.v1.CalendarService
grpcurl -plaintext localhost:50051 list identity.v1.IdentityService
```

## Данные и роли (как реализовано сейчас)

- Пользователь идентифицируется по `telegram_id` (таблица `users`).
- `IdentityService` хранит `username` в `users.note` (реализация: `internal/repository/user_repository.go` и `internal/service/identity_service.go`).
- Политика ролей — “одна роль на пользователя” (таблицы `roles` + `user_roles`).
- При `RegisterUser` сервис гарантирует наличие записи в `clients` (т.к. записываться может любой пользователь).
- При назначении роли `provider` создаётся запись в `providers` (если её нет).

## Структура проекта

- `cmd/main.go` — bootstrap: конфиг, подключение к БД, `AutoMigrate`, gRPC‑сервер.
- `internal/api/**` — `.proto` + сгенерированные `*.pb.go`.
- `internal/config` — загрузка env‑конфига.
- `internal/db` — инициализация GORM/DSN.
- `internal/model` — GORM‑модели и миграция.
- `internal/repository` — репозитории (GORM‑реализации).
- `internal/service` — реализации gRPC сервисов (`CalendarService`, `IdentityService`).
- `internal/utils`, `internal/calendar` — утилиты календаря/валидации (чистая логика + тесты).

## Тестирование

```bash
go test ./...
```

## Генерация gRPC (при необходимости)

Сгенерированные файлы уже лежат рядом с `.proto`. Если нужно регенерировать:

```bash
protoc \
  -I . \
  --go_out=. --go_opt=paths=source_relative \
  --go-grpc_out=. --go-grpc_opt=paths=source_relative \
  internal/api/common/v1/common.proto \
  internal/api/identity/v1/identity.proto \
  internal/api/calendar/v1/calendar.proto
```
