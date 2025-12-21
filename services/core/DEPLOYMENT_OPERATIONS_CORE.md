# Инструкция по развертыванию и эксплуатации — сервис `core`

Документ предназначен для DevOps/сопровождения и описывает: требования, конфигурацию, варианты развёртывания, эксплуатационные процедуры, мониторинг и типовые проблемы.

## 1. Назначение сервиса и интерфейсы

`core` — gRPC‑сервис “ядра” платформы записи. Порт gRPC по умолчанию: `50051` (адрес захардкожен в `cmd/main.go` как `:50051`). gRPC reflection включён.

Внешние интеграции работают через:
- `identity.v1.IdentityService`
- `calendar.v1.CalendarService`

## 2. Требования к окружению

### 2.1 Runtime

- Linux (контейнерный запуск рекомендуем).
- Go версии из `go.mod` (`go 1.25.0`) — требуется только для сборки/запуска вне Docker.

### 2.2 PostgreSQL

Требования:
- доступная PostgreSQL БД (для production желательно HA/managed).
- расширение `pgcrypto` (для `gen_random_uuid()`):

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

## 3. Конфигурация (переменные окружения)

`core` читает настройки БД из env (dotenv не подхватывается автоматически).

| Переменная | Значение по умолчанию | Назначение |
|---|---:|---|
| `DB_HOST` | `postgres` | хост Postgres |
| `DB_PORT` | `5432` | порт |
| `DB_USER` | `booking` | пользователь |
| `DB_PASSWORD` | `booking` | пароль |
| `DB_NAME` | `booking_db` | база данных |
| `DB_SSLMODE` | `disable` | SSL режим |
| `DB_TIMEZONE` | `Europe/Moscow` | `TimeZone` в DSN |
| `DB_MAX_OPEN_CONNS` | `10` | max open connections |
| `DB_MAX_IDLE_CONNS` | `5` | max idle connections |
| `DB_CONN_MAX_LIFETIME_MIN` | `30` | max lifetime (мин) |

Примечания:
- `DB_TIMEZONE` влияет на DSN параметр `TimeZone`; внутренняя бизнес‑логика времени хранит события в UTC и учитывает TZ расписаний при развёртывании правил.
- Для production рекомендуется включить SSL для Postgres (`DB_SSLMODE=require`/`verify-full`) и настроить сертификаты.

## 4. Развертывание

### 4.1 Локальный запуск (для разработки)

1) Поднять Postgres, создать `pgcrypto`.
2) Экспортировать переменные окружения:

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_USER=booking
export DB_PASSWORD=booking
export DB_NAME=booking_db
export DB_SSLMODE=disable
export DB_TIMEZONE=Europe/Moscow
```

3) Запустить:

```bash
go run ./cmd
```

Важно:
- При старте выполняется `AutoMigrate` (создание/обновление таблиц).

### 4.2 Docker (рекомендуемый базовый вариант)

Сборка образа:

```bash
docker build -t appointment-core:local .
```

Запуск (пример, Postgres доступен снаружи):

```bash
docker run --rm -p 50051:50051 \
  -e DB_HOST=host.docker.internal -e DB_PORT=5432 \
  -e DB_USER=booking -e DB_PASSWORD=booking -e DB_NAME=booking_db \
  -e DB_SSLMODE=disable -e DB_TIMEZONE=Europe/Moscow \
  appointment-core:local
```

Примечания по образу:
- сборка multi‑stage; runtime — distroless nonroot;
- порт `50051` пробрасывается как gRPC.

### 4.3 Kubernetes (рекомендации)

Базовые рекомендации (в репозитории манифестов нет, поэтому ниже — эксплуатационные требования):
- Deployment с несколькими репликами (service stateless) при общей БД.
- Service типа ClusterIP + ingress/gateway (если нужен внешний доступ).
- Секреты БД хранить в Secret‑хранилище.
- Ограничить доступ к порту gRPC сетевыми политиками.

Health checks:
- В текущей реализации нет gRPC health сервиса, поэтому:
  - simplest: readiness как TCP‑проверка + проверка доступности БД на уровне sidecar/gateway;
  - recommended: добавить gRPC health checking в код и использовать стандартные probes.

## 5. Эксплуатация

### 5.1 Логи

Сервис пишет логи в stdout/stderr (подходит для контейнерного сбора логов). В `CalendarService` есть простые `logInfo/logErr` сообщения по ключевым операциям.

Рекомендации:
- централизованный сбор логов (ELK/Loki/Cloud Logging);
- не логировать чувствительные данные (телефон) либо маскировать на уровне лог‑пайплайна.

### 5.2 Диагностика gRPC (reflection)

Reflection включён, можно проверять доступность:

```bash
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext localhost:50051 list calendar.v1.CalendarService
grpcurl -plaintext localhost:50051 list identity.v1.IdentityService
```

Для production рекомендуется отключить reflection или ограничить доступ к нему сетевыми политиками/шлюзом.

### 5.3 Масштабирование и нагрузка

Сервис можно масштабировать горизонтально, но учитывать:
- БД — основной узел состояния и потенциальный SPOF.
- Материализация слотов выполняется при запросе `ListFreeSlots` в окне и читает/пишет `time_slots`; большие окна времени могут быть тяжёлыми.
- При нескольких репликах возможны дубли слотов при параллельной материализации без уникального ограничения на `time_slots` (рекомендуется проработать индексацию/идемпотентность под production).

### 5.4 Управление схемой (миграции)

Сейчас миграции выполняются автоматически на старте (`AutoMigrate`).

Риски:
- увеличение времени старта;
- потенциальные гонки при одновременном старте нескольких реплик.

Рекомендации:
- вынести миграции в отдельный управляемый этап (job/step) и контролировать порядок деплоя.

## 6. Безопасность (кратко для эксплуатации)

Сервис не реализует встроенную аутентификацию клиента; безопасность должна обеспечиваться внешним слоем (gateway/бот) или дорабатываться в `core`.

Минимальные меры для production:
- TLS на внешнем периметре (и желательно mTLS между сервисами).
- Ограничение доступа к порту gRPC по сети (VPC/mesh/network policies).
- SSL/TLS к Postgres и секреты из Secret‑хранилища.
- Отключить/ограничить reflection.

 

## 7. Резервное копирование и восстановление (операционный чек‑лист)

Так как критическое состояние хранится в PostgreSQL, DR относится к БД.

Рекомендуемый чек‑лист:
- Настроить регулярные base backups/snapshots.
- При требованиях по минимальному RPO — включить WAL‑архивацию (PITR).
- Настроить шифрование бэкапов “at rest” и контроль доступа к ним.
- Регулярно выполнять тестовые восстановления на стенд и проверять базовые RPC‑сценарии.
- Зафиксировать целевые RPO/RTO и иметь runbook восстановления/переключения.

## 8. Типовые проблемы и решения

### 8.1 Ошибка `function gen_random_uuid() does not exist`

Причина: не установлен `pgcrypto`.

Решение:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

### 8.2 Ошибки доступа к БД / таймауты

Проверить:
- корректность `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`;
- сетевую доступность (security groups/network policies);
- лимиты пула (`DB_MAX_OPEN_CONNS`, `DB_MAX_IDLE_CONNS`) и нагрузку на Postgres.

### 8.3 “provider not found” / “only providers can manage…”

Причины:
- неверный `provider_id` в запросах;
- у пользователя нет роли `provider`.

Решение:
- назначить роль `provider` через `IdentityService.SetRole` (из доверенного внешнего слоя);
- убедиться, что внешний клиент не подставляет чужие ID и соблюдает авторизацию.
