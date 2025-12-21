# 4 Разработка программного продукта — сервис `core`

Документ описывает фактическую реализацию gRPC‑сервиса `core` (платформа записи) по состоянию текущей кодовой базы в каталоге `services/core`.

Назначение `core`: «ядро» домена записи — расписания провайдеров, слоты, бронирования, каталог услуг и базовые методы идентификации пользователя (Telegram) для внешних клиентов (например, Telegram‑бота). Сервис не содержит UI; он предоставляет API и хранит состояние в PostgreSQL.

Источники в репозитории:
- Общее описание сервиса: `README.md`
- Архитектурные решения: `ARCHITECTURE.md`
- gRPC контракты: `internal/api/**`
- Реализация gRPC: `internal/service/*`
- Доступ к данным: `internal/repository/*`
- Модели данных (GORM): `internal/model/*`

---

## 4.1 Компонентный состав

### 4.1.1 Сервис как компонент платформы

`core` — отдельный сервис (микросервис на уровне платформы), запускаемый как stateless‑процесс, состояние хранится в PostgreSQL:

`gRPC client (бот/внешний сервис)` → `gRPC server (core)` → `GORM репозитории` → `PostgreSQL`.

Основные публичные интерфейсы:
- `CalendarService` — календарный домен: расписания, слоты, бронирования, каталог услуг.
- `IdentityService` — домен идентификации: регистрация/контакты/роль/профиль/поиск провайдера по телефону.

### 4.1.2 Структура кода (модули/пакеты)

**Точка входа / bootstrap**
- `cmd/main.go`
  - Загрузка конфигурации БД из env (`internal/config`).
  - Подключение к Postgres через GORM (`internal/db`).
  - Автомиграции моделей (`internal/model/AutoMigrate`).
  - Инициализация репозиториев (`internal/repository/*`).
  - Регистрация gRPC сервисов (`internal/service/*`) и включение reflection.
  - Graceful shutdown по SIGINT/SIGTERM.

**API слой (контракты)**
- `internal/api/common/v1/common.proto`
  - Общие DTO: `TimeRange`, `Slot`, `Booking`, `Provider`, `Service`, `ProviderSchedule`, `ScheduleRule` и enum‑ы статусов/частот.
- `internal/api/calendar/v1/calendar.proto`
  - Контракт `CalendarService` и сообщения запросов/ответов.
  - Присутствуют алиасы под требования ТЗ: `GetAvailableSlots` (alias `ListFreeSlots`), `BookSlot` (alias `CreateBooking`).
- `internal/api/identity/v1/identity.proto`
  - Контракт `IdentityService` и сообщения запросов/ответов.

**Сервисный слой (application/service)**
- `internal/service/calendar_service.go`
  - Реализация `CalendarService`.
  - Оркестрация транзакций, конкурентный доступ (row lock на слоте при бронировании), валидация параметров, маппинг моделей в protobuf.
  - Материализация слотов из правил расписаний «по запросу» (при выдаче свободных слотов).
- `internal/service/identity_service.go`
  - Реализация `IdentityService`.
  - Upsert пользователя по Telegram ID, обновление контактов, назначение роли, получение профиля, поиск провайдера по телефону.
- Тесты сервисного слоя:
  - `internal/service/calendar_bulk_cancel_test.go` — проверка массовой отмены слотов/бронирований.
  - `internal/service/slot_validation_test.go` — проверка валидации слота.

**Слой доступа к данным (repositories)**
- `internal/repository/*`
  - Интерфейсы репозиториев и реализация на GORM (например, `GormUserRepository`, `GormSlotRepository`).
  - Репозитории инкапсулируют SQL/GORM детали и предоставляют сервисному слою целевые методы.

**Модели хранения (persistence model)**
- `internal/model/*`
  - GORM‑модели и настройки схемы (типы, индексы, ограничения).
  - `internal/model/migrate.go` — список сущностей для `AutoMigrate`.

**Утилиты / чистая логика**
- `internal/utils/calendar_utils.go`
  - Чистая логика времени/интервалов: нормализация интервала, разбиение на слоты, проверка пересечений, развёртывание правил повторения.
  - Используется сервисом календаря для: развёртывания расписаний (`ExpandRecurringRule`) и проверки конфликтов (`HasOverlap`).
  - Тесты: `internal/utils/calendar_utils_test.go`.
- `internal/calendar/*`
  - Общие вспомогательные функции/модели (например, пагинация `Paginate`, валидация пользователя `ValidateTelegramUser`).
  - В текущей реализации бизнес‑потоки `IdentityService` и `CalendarService` опираются на репозитории/модели и утилиты `internal/utils` (а не на `internal/calendar`).

**Конфигурация и доступ к БД**
- `internal/config/db.go`
  - `DBConfig` и загрузка параметров из переменных окружения (`DB_HOST`, `DB_PORT`, …).
- `internal/db/db.go`
  - Построение DSN, настройка GORM (`NowFunc` → UTC), настройка пула соединений.

**Контейнеризация**
- `Dockerfile`
  - Multi‑stage сборка Go бинарника (`CGO_ENABLED=0`) и запуск в `distroless` образе.

### 4.1.3 Компоненты и взаимодействия (связи между модулями)

**Связи уровней (слоёв):**
- `cmd/main.go` создаёт зависимости (DB, репозитории) и внедряет их в сервисы.
- `internal/service/*` использует:
  - `internal/repository/*` для чтения/записи данных;
  - `internal/utils` для вычислений, не зависящих от инфраструктуры (правила повторений, пересечения интервалов).
- `internal/repository/*` использует:
  - `internal/model/*` (структуры и имена таблиц/полей) и `gorm.io/gorm`.
- `internal/model/*` описывает схему хранения (таблицы/индексы/ограничения).

### 4.1.4 Модель данных (таблицы и связи)

Схема создаётся/обновляется автоматически при старте (`internal/model/AutoMigrate`), ключевые таблицы:

**Пользователи и роли**
- `users` (`internal/model/user.go`)
  - `telegram_id` — уникальный идентификатор пользователя (unique index).
  - `display_name`, `contact_phone`.
  - `note` — используется для хранения `username` из Telegram (заполняется при регистрации/обновлении профиля).
- `roles`, `user_roles` (`internal/model/role.go`)
  - Политика «одна роль на пользователя» реализована процедурно в `GormUserRepository.SetRole`: удаление старых записей `user_roles` и вставка новой.

**Акторы календаря**
- `clients` (`internal/model/user.go`)
  - «Клиент» привязан к `users` (FK `clients.user_id`).
  - Создаётся автоматически при регистрации (`IdentityService.RegisterUser`) и при назначении роли (`IdentityService.SetRole`).
- `providers` (`internal/model/provider.go`)
  - «Провайдер» привязан к `users` (FK `providers.user_id`).
  - Создаётся автоматически при назначении роли `provider` (`IdentityService.SetRole`) и при поиске по телефону (`IdentityService.FindProviderByPhone`) — как механизм согласования данных.

**Каталог услуг**
- `services`, `provider_services` (`internal/model/service.go`)
  - `services.is_active` проиндексировано и используется фильтром в `ListServices`.
  - Связь провайдер ↔ услуги: many‑to‑many через `provider_services`.

**Расписания, слоты, бронирования**
- `schedules` (`internal/model/schedule.go`)
  - Хранит правило расписания в JSONB поле `rules` (кодирование/декодирование rule реализовано в `internal/service/calendar_service.go`: `encodeScheduleRule` / `decodeScheduleRule`).
  - `start_date`/`end_date` — границы действия расписания на уровне дат.
  - `time_zone` — TZ, используемая при развёртывании.
- `time_slots` (`internal/model/slot.go`)
  - Материализованные слоты: `starts_at`, `ends_at`, `status`.
  - `schedule_id` — ссылка на исходное правило (опционально), чтобы понимать происхождение слота.
  - Статусы: `planned` (свободен), `booked` (занят), `cancelled` (отменён).
- `bookings` (`internal/model/booking.go`)
  - `slot_id` имеет уникальный индекс (гарантия «не более одного бронирования на слот»).
  - Статусы: `pending` / `confirmed` / `cancelled` (в текущей реализации создаётся сразу `confirmed`).
  - При отмене выставляется `cancelled_at`.

**Аудит/события**
- `events` (`internal/model/event.go`)
  - Модель заведена (типы `booking_created`, `booking_cancelled`, …), однако в текущих сервисных методах запись событий не используется (т.е. таблица создаётся, но не наполняется бизнес‑логикой).

### 4.1.5 Интерфейсы API для межмодульного/межсервисного взаимодействия

`core` предоставляет gRPC‑API. Внешние клиенты (бот/веб‑панель/другие сервисы) обращаются к методам RPC; внутренние модули взаимодействуют через Go‑интерфейсы репозиториев.

**gRPC интерфейсы (внешние):**
- `identity.v1.IdentityService` (`internal/api/identity/v1/identity.proto`)
  - `RegisterUser`, `UpdateContacts`, `SetRole`, `GetProfile`, `FindProviderByPhone`.
- `calendar.v1.CalendarService` (`internal/api/calendar/v1/calendar.proto`)
  - Слоты: `ListFreeSlots`, `GetAvailableSlots`, `GetNearestFreeSlot`, `GetNextProviderSlot`, `FindFreeSlots`, `ValidateSlot`.
  - Бронирования: `CreateBooking`, `BookSlot`, `GetBooking`, `CancelBooking`, `ListBookings`, `CheckAvailability`.
  - Расписания: `ListProviderSchedules`, `CreateProviderSchedule`, `UpdateProviderSchedule`, `DeleteProviderSchedule`, `ExpandSchedule`.
  - Массовые операции: `BulkCancelProviderSlots`.
  - Каталог: `ListServices`, `ListProviderServices`.

**Интерфейсы репозиториев (внутренние):**
- `repository.UserRepository`, `ClientRepository`, `ProviderRepository`, `ScheduleRepository`, `SlotRepository`, `BookingRepository`, `ServiceRepository` (`internal/repository/*`).

---

## 4.2 Алгоритмы и процессы обработки

Ниже приведены последовательности действий, которые реализованы в методах gRPC сервисов (с указанием ключевых проверок/транзакций и используемых сущностей).

### 4.2.1 Регистрация пользователя (IdentityService.RegisterUser)

Файл: `internal/service/identity_service.go`, метод `RegisterUser`.

**Вход:** `telegram_id`, `display_name`, `username`, `contact_phone`.

**Последовательность:**
1. Валидация: `telegram_id > 0`, иначе `codes.InvalidArgument`.
2. Upsert пользователя по `telegram_id`:
   - если записи нет — создаётся строка в `users`;
   - если есть — обновляются `display_name`, `contact_phone`, `note` (в `note` сохраняется `username`);
   - реализация: `repository.UserRepository.UpsertUser` (нормализация телефона выполняется в репозитории).
3. Гарантия наличия клиента:
   - `ClientRepository.EnsureByUserID` создаёт запись в `clients`, если её нет.
4. Чтение роли пользователя:
   - `UserRepository.GetRole` (ошибка «роль отсутствует» игнорируется, роль может быть пустой).
5. Подтягивание `client_id`/`provider_id` (если существуют записи `clients`/`providers`) для удобства дальнейших вызовов `CalendarService`.
6. Возврат `identity.v1.User` с заполнением полей: `id`, `telegram_id`, `display_name`, `username` (из `note`), `contact_phone`, `role_code`, `client_id`, `provider_id`.

### 4.2.2 Обновление контактов (IdentityService.UpdateContacts)

Файл: `internal/service/identity_service.go`, метод `UpdateContacts`.

**Особенности:**
- Частичное обновление: обновляются только непустые поля (`display_name`, `username`, `contact_phone`).
- Телефон нормализуется до «только цифры» на уровне репозитория пользователя (функция `normalizePhone`).

### 4.2.3 Назначение роли (IdentityService.SetRole)

Файл: `internal/service/identity_service.go`, метод `SetRole`.

**Последовательность:**
1. Валидация: `telegram_id > 0`, `role_code != ""`.
2. Поиск пользователя по Telegram ID (`UserRepository.FindByTelegramID`); если нет — `codes.NotFound`.
3. Назначение роли:
   - `UserRepository.SetRole`:
     - гарантирует наличие записи `roles` для `role_code` (создаёт при отсутствии);
     - удаляет старые записи `user_roles` для пользователя;
     - вставляет новую запись `user_roles` (policy «1 роль на пользователя»).
4. Гарантия наличия клиента (`clients`) — создаётся всегда.
5. Если `role_code == "provider"` — гарантируется наличие `providers` записи (`ProviderRepository.EnsureByUserID`), где `display_name` берётся из `users.display_name` (или fallback на `users.note`).
6. Возврат профиля как в `RegisterUser`.

### 4.2.4 Поиск провайдера по телефону (IdentityService.FindProviderByPhone)

Файл: `internal/service/identity_service.go`, метод `FindProviderByPhone`.

**Последовательность:**
1. Валидация: телефон непустой.
2. Нормализация телефона (цифры) и поиск пользователя по `users.contact_phone` (`UserRepository.FindByPhone`).
3. Проверка роли пользователя через `GetRole`; если роль отсутствует или не равна `"provider"` — ответ `codes.NotFound` (скрытие деталей).
4. Согласование данных:
   - `ProviderRepository.EnsureByUserID` (на случай исторических данных, когда роль есть, а провайдер ещё не создан).
   - `ClientRepository.EnsureByUserID` (чтобы провайдер также мог быть клиентом).
5. Возврат `identity.v1.User` с заполненным `provider_id`.

### 4.2.5 Выдача свободных слотов (CalendarService.ListFreeSlots / GetAvailableSlots)

Файл: `internal/service/calendar_service.go`, метод `ListFreeSlots`.

**Цель:** вернуть материализованные слоты (`time_slots`) со статусом `planned` в окне времени, с пагинацией.

**Последовательность:**
1. Валидация: `provider_id` обязателен; `end > start`; корректность UUID для `provider_id`/`service_id`.
2. Параметры пагинации: `page` (>=1), `page_size` (default 20), `offset = (page-1)*page_size`.
3. Материализация слотов из правил расписаний (если доступны `db` и `scheduleRepo`):
   1) Загрузка расписаний провайдера: `ScheduleRepository.ListByProvider(provider_id)` → список `schedules`.
   2) Для каждого расписания:
      - `expandScheduleModelInWindowUTC`:
        - декодирует JSONB правило `schedules.rules` → `common.v1.ScheduleRule`;
        - учитывает `schedules.time_zone`;
        - учитывает `schedules.start_date`/`end_date` и ограничения правила (`until`, `count`, `exceptions`);
        - разворачивает правило через `internal/utils.ExpandRecurringRule`;
        - возвращает интервалы в UTC.
   3) Транзакция:
      - одним запросом выбирает существующие `time_slots` в окне и строит in‑memory map ключей `(service_id, starts_at, ends_at)` для дедупликации;
      - создаёт отсутствующие слоты пачкой (`tx.Create(&toCreate)`), вставки сортируются детерминированно для снижения «дрейфа» при повторах.
4. Чтение свободных слотов через репозиторий:
   - `SlotRepository.ListFreeSlots(provider_id, service_id, start, end, limit, offset)`:
     - фильтры: провайдер, окно `starts_at >= start AND ends_at <= end`, статус `planned`, опционально `service_id`;
     - сортировка по `starts_at ASC`;
     - возвращает `slots` и `total_count` (через `COUNT(*)`).
5. Маппинг в protobuf (`common.v1.Slot`) и возврат.

**Замечание по сервису/услуге:** при материализации используется либо конкретный `service_id` (если он задан в запросе), либо `NULL`‑service слоты (если `service_id` не задан). При выдаче слотов фильтр по `service_id` применяется только когда `service_id` передан в `ListFreeSlots`; если `service_id` не задан, репозиторий возвращает все свободные слоты провайдера в окне независимо от `service_id` (включая `NULL` и не‑`NULL`).

### 4.2.6 Развёртывание расписания (CalendarService.ExpandSchedule)

Файл: `internal/service/calendar_service.go`, метод `ExpandSchedule`.

**Вход:** `schedule_id`, `window_start`, `window_end`.

**Последовательность:**
1. Валидация параметров.
2. Загрузка расписания: `ScheduleRepository.GetByID`.
3. Проверка, что расписание принадлежит провайдеру, у которого роль `"provider"`:
   - `ensureProviderRole(provider_id)`:
     - `ProviderRepository.GetByID(provider_id)` → `providers.user_id`;
     - `UserRepository.GetRole(user_id)` и сравнение с `"provider"`;
     - при несоответствии возвращается `codes.PermissionDenied`.
4. `expandScheduleModelInWindowUTC` → список интервалов.
5. Возврат интервалов как `common.v1.TimeRange[]`.

### 4.2.7 Создание бронирования (CalendarService.CreateBooking / BookSlot)

Файл: `internal/service/calendar_service.go`, метод `CreateBooking`.

**Цель:** атомарно «занять» слот и создать запись о бронировании.

**Последовательность:**
1. Валидация: `slot_id`, `client_id` обязательны; `client_id` должен быть UUID.
2. Проверка существования `clients` записи по `client_id` (роль пользователя не ограничивает право бронировать; важно наличие `client_id`).
3. Транзакция:
   1) Получение слота `time_slots` с блокировкой строки:
      - `SELECT ... FOR UPDATE` через `tx.Clauses(clause.Locking{Strength: "UPDATE"})`.
   2) Проверка статуса слота: только `planned` допускается к бронированию; иначе `codes.FailedPrecondition`.
   3) Проверка конфликтов по времени (требование предотвращения пересечений):
      - `newRange = [slot.starts_at, slot.ends_at)`.
      - Получение интервалов подтверждённых бронирований клиента:
        - `listClientConfirmedBookingRangesTx`: `bookings JOIN time_slots` по `slot_id`, фильтр `bookings.client_id = ? AND bookings.status = confirmed`, исключая текущий `slot_id`.
      - Проверка пересечений: `internal/utils.HasOverlap(newRange, clientRanges, inclusive=false)`.
      - Аналогично для провайдера (по `time_slots.provider_id`): `listProviderConfirmedBookingRangesTx`.
      - При конфликте — `codes.FailedPrecondition`.
   4) Создание `bookings` записи со статусом `confirmed` и комментарием `comment`.
   5) Обновление статуса слота `time_slots.status` на `booked`.
4. Возврат `common.v1.Booking` с обогащением:
   - `mapBooking` подгружает `provider_name` и `service_name` через репозитории (если доступны `provider_id`/`service_id`).

**Конкурентная безопасность:**
- Row‑lock на слоте + уникальный индекс `bookings.slot_id` обеспечивают защиту от «двойного бронирования» одного слота.

### 4.2.8 Проверка доступности слота (CalendarService.CheckAvailability)

Файл: `internal/service/calendar_service.go`, метод `CheckAvailability`.

**Особенности:**
- Возвращает `available=false` и текстовую `reason`, не выбрасывая ошибку для некоторых кейсов (например, «slot not found»), чтобы упростить UX клиента.
- Проверяет те же виды конфликтов по времени (клиент/провайдер) и свободность слота, но без блокировок и без создания данных.

### 4.2.9 Отмена бронирования (CalendarService.CancelBooking)

Файл: `internal/service/calendar_service.go`, метод `CancelBooking`.

**Последовательность:**
1. Валидация: `booking_id`.
2. Загрузка бронирования с предзагрузкой слота: `BookingRepository.GetByID` (`Preload("Slot")`).
3. Если уже отменено — возврат текущего состояния.
4. Транзакция:
   - Обновление `bookings.status = cancelled` и `bookings.cancelled_at = now`.
   - Обновление `time_slots.status = planned` для слота бронирования (слот снова становится доступным).
5. Возврат `Booking`.

**Примечание:** поле `reason` из `CancelBookingRequest` в текущей реализации не сохраняется (в отличие от массовой отмены, где причина записывается в `bookings.comment`).

### 4.2.10 Список бронирований (CalendarService.ListBookings)

Файл: `internal/service/calendar_service.go`, метод `ListBookings`.

**Особенности текущей реализации:**
- Фильтрация выполняется по `bookings.created_at` (а не по времени слота), диапазон `[from, to]` задаётся через `created_at >= from AND created_at <= to`.
- Пагинация через `page/page_size`.
- `BookingRepository.ListByClientAndRange` предзагружает `Slot` (`Preload("Slot")`) и сортирует по `created_at DESC`.

### 4.2.11 CRUD расписаний провайдера (CalendarService.*ProviderSchedule*)

Файл: `internal/service/calendar_service.go`, методы:
- `ListProviderSchedules`
- `CreateProviderSchedule`
- `UpdateProviderSchedule`
- `DeleteProviderSchedule`

**Общие правила:**
- Для операций изменения требуется, чтобы владелец расписания (provider) имел роль `"provider"` (`ensureProviderRole`).
- В `UpdateProviderSchedule` запрещено менять владельца расписания: если в запросе указан `provider_id` и он отличается от существующего, возвращается `codes.PermissionDenied`.
- Правило `ScheduleRule` сериализуется в JSON (`encodeScheduleRule`) и хранится в `schedules.rules` как JSONB.

**Материализация слотов от расписаний:**
- Создание/обновление расписания не создаёт слоты напрямую.
- Слоты создаются лениво при `ListFreeSlots` через `materializeSlotsFromSchedules`.

### 4.2.12 Массовая отмена слотов провайдера (CalendarService.BulkCancelProviderSlots)

Файл: `internal/service/calendar_service.go`, метод `BulkCancelProviderSlots`.

**Цель:** отменить слоты в окне, отменить связанные бронирования и вернуть данные для уведомления клиентов (например, через бота).

**Последовательность:**
1. Валидация: `provider_id`, `start`, `end`, `end > start`, наличие `db`.
2. Проверка роли провайдера (`ensureProviderRole`).
3. Транзакция:
   1) До обновлений собираются затронутые бронирования:
      - `bookings JOIN time_slots JOIN clients JOIN users`,
      - фильтры: провайдер, окно времени слота, `time_slots.status <> cancelled`, `bookings.status <> cancelled`.
      - Из выборки собираются: `booking_id`, `slot_id`, `client_id`, `clients.user_id`, `users.telegram_id`, `service_id`, `starts_at`, `ends_at`.
   2) Отмена бронирований пачкой:
      - `bookings.status = cancelled`, `cancelled_at = now`;
      - если задана `reason`, она записывается в `bookings.comment`.
   3) Отмена слотов пачкой:
      - `time_slots.status = cancelled` в окне.
4. Возврат статистики и массива `AffectedBooking[]` для последующих уведомлений.

### 4.2.13 CRUD слотов (CalendarService.CreateSlot/UpdateSlot/DeleteSlot)

Файл: `internal/service/calendar_service.go`.

**CreateSlot**
- Проверка `provider_id` и роли провайдера (`ensureProviderRole`).
- Валидация временного диапазона.
- Создание `time_slots` со статусом `planned`.

**UpdateSlot**
- Загрузка слота по ID, проверка роли по `slot.provider_id`.
- Обновление `service_id` (если задан), `range` (если задан), `status` (если задан).

**DeleteSlot**
- Загрузка слота по ID, проверка роли по `slot.provider_id`.
- Удаление записи `time_slots`.

### 4.2.14 Каталог услуг (CalendarService.ListServices / ListProviderServices)

Файл: `internal/service/calendar_service.go`.

**ListServices**
- Пагинация `page/page_size`, сортировка по имени.
- Фильтр `only_active` → `services.is_active = true` (по умолчанию true).

**ListProviderServices**
- Загрузка провайдера по ID.
- Загрузка услуг провайдера через join‑таблицу `provider_services`.
- Возврат профиля провайдера и списка услуг.

### 4.2.15 Методы оптимизации производительности (реализовано и где применяется)

**Реализовано в текущей кодовой базе:**
- Ленивое создание слотов:
  - слоты не генерируются «на годы вперёд», а материализуются в окне запроса (`ListFreeSlots`), что ограничивает объём вставок и позволяет масштабировать по запросам.
- Пагинация и `COUNT(*)`:
  - выдачи `ListFreeSlots`, `ListBookings`, `ListServices` используют ограничение `LIMIT/OFFSET` и отдельный подсчёт общего количества.
- Индексы на частые фильтры:
  - `users.telegram_id` (unique),
  - `time_slots.provider_id`, `time_slots.starts_at`, `time_slots.status`,
  - `services.is_active`,
  - `bookings.client_id`, `bookings.status`, `bookings.slot_id` (unique),
  - используются фильтрами в запросах репозиториев и сервисов.
- Транзакции и блокировки для критических секций:
  - `CreateBooking` блокирует строку слота `FOR UPDATE`, предотвращая гонки.
  - Массовые операции `BulkCancelProviderSlots` выполняются в транзакции, чтобы состояние слотов и бронирований было согласованным.
- Минимизация числа запросов в массовой отмене:
  - сбор «получателей уведомлений» делается одним join‑запросом перед update‑операциями.
- Настройка пула соединений:
  - `DB_MAX_OPEN_CONNS`, `DB_MAX_IDLE_CONNS`, `DB_CONN_MAX_LIFETIME_MIN` уменьшают накладные расходы на коннекты и стабилизируют нагрузку на БД.

**Ограничения текущей реализации (важно учитывать при нагрузке):**
- Материализация делает выборку всех слотов в окне и дедупликацию в памяти; при больших окнах это может быть тяжело (и требует уникального ограничения в БД для полной идемпотентности при нескольких репликах).

---

## 4.3 Интерфейс пользователя (UI/UX)

### 4.3.1 Фактическое состояние UI в `core`

Сервис `core` не содержит пользовательского интерфейса: нет web‑страниц, мобильных экранов или Telegram‑диалогов. `core` предоставляет **gRPC API**, которое потребляется внешними клиентами (например, Telegram‑ботом).

Поэтому UI/UX в контексте `core` описывается как:
- «UX для клиента API» (структура методов, коды ошибок, предсказуемость поведения);
- пользовательские сценарии, которые реализуются внешним интерфейсом (бот/панель), но опираются на конкретные RPC‑методы `core`.

### 4.3.2 Пользовательские интерфейсы (точки взаимодействия)

**1) Пользователь (клиент) в Telegram‑боте**
- Идентификатор пользователя: `telegram_id` (передаётся в `IdentityService`).
- Основные действия: регистрация/обновление контактов, выбор услуги/провайдера, просмотр свободных слотов, создание/отмена брони, просмотр списка броней.

**2) Провайдер (мастер/консультант)**
- С точки зрения `core` провайдер — это пользователь с ролью `"provider"` + сущность `providers`.
- Провайдерские действия (через внешний UI): управление расписанием и слотами, массовая отмена слотов для уведомления клиентов.

**3) Оператор/администратор (если будет реализовано внешним клиентом)**
- На уровне данных есть модель ролей (`roles/user_roles`), но отдельные административные RPC‑методы в `core` не выделены (кроме `SetRole` в `IdentityService`).

### 4.3.3 Макеты экранов (концептуальные, привязанные к API)

Ниже приведены макеты, которые напрямую отображаются на существующие RPC‑возможности `core`. Эти макеты описывают возможный UI внешнего клиента (например, Telegram‑бота) и соответствие вызовам API.

**Экран A — «Старт / регистрация»**
- Текст: «Добро пожаловать. Подтвердите имя и телефон».
- Поля/ввод:
  - Имя (`display_name`) — текст.
  - Username (`username`) — подтягивается из Telegram (если есть).
  - Телефон (`contact_phone`) — ввод/кнопка «Отправить контакт».
- Действия:
  - «Продолжить» → `IdentityService.RegisterUser`.
  - «Обновить контакты» → `IdentityService.UpdateContacts`.

**Экран B — «Главное меню клиента»**
- Кнопки:
  - «Записаться» → сценарий выбора услуги/провайдера/слота.
  - «Мои записи» → `CalendarService.ListBookings` (по `client_id` из профиля).
  - «Отменить запись» → выбор из списка и `CalendarService.CancelBooking`.
  - «Профиль» → `IdentityService.GetProfile`.

**Экран C — «Каталог услуг»**
- Список услуг (карточки): `name`, `description`, `duration`, статус активности (в UI скрывается, если `only_active=true`).
- Навигация: пагинация «Далее/Назад» (page/page_size).
- API:
  - `CalendarService.ListServices(only_active=true, page, page_size)`
  - опционально после выбора: `CalendarService.ListProviderServices` (если нужна привязка к провайдеру).

**Экран D — «Свободные слоты»**
- Фильтры:
  - Провайдер (provider_id) — выбран ранее или задан заранее.
  - Услуга (service_id) — выбранная в каталоге.
  - Окно дат (start/end).
- Список слотов (строки):
  - Дата/время начала‑конца, возможно форматирование во временной зоне клиента (внешний UI).
  - Идентификатор слота (скрытый или по кнопке «Подробнее»).
- API:
  - `CalendarService.ListFreeSlots(provider_id, service_id, start, end, page, page_size)`
  - «быстрые подсказки»: `GetNearestFreeSlot` / `FindFreeSlots`.

**Экран E — «Подтверждение записи»**
- Показ выбранного слота + предупреждения (если есть).
- Действия:
  - «Проверить доступность» → `CalendarService.CheckAvailability(client_id, slot_id)`.
  - «Записаться» → `CalendarService.CreateBooking(client_id, slot_id, comment)`.

**Экран F — «Мои записи»**
- Список бронирований:
  - `provider_name`, `service_name`, время слота, статус.
- Действия:
  - «Отменить» на записи → `CalendarService.CancelBooking(booking_id, reason)`.
  - «Подробнее» → `CalendarService.GetBooking(booking_id)`.

**Экран G — «Панель провайдера: расписание»**
- Список правил расписаний (периоды/частота/дни недели/исключения).
- Действия:
  - «Добавить расписание» → `CreateProviderSchedule`.
  - «Изменить» → `UpdateProviderSchedule`.
  - «Удалить» → `DeleteProviderSchedule`.
  - «Проверить развёртку» → `ExpandSchedule(schedule_id, window_start, window_end)` (предпросмотр интервалов).

**Экран H — «Панель провайдера: слоты»**
- CRUD слотов (точечные исключения/добавления):
  - «Добавить слот» → `CreateSlot(provider_id, service_id, range)`.
  - «Изменить слот» → `UpdateSlot(slot_id, …)`.
  - «Удалить слот» → `DeleteSlot(slot_id)`.
  - «Отменить окно» → `BulkCancelProviderSlots(provider_id, start, end, reason)` и последующие уведомления клиентам на стороне внешнего UI по `AffectedBooking.client_telegram_id`.

### 4.3.4 Навигационные схемы (сценарии)

**Сценарий «Клиент записывается»**
1. `GetProfile`/`RegisterUser` → получение `client_id`.
2. `ListServices` → выбор услуги.
3. `ListFreeSlots` (или `GetNearestFreeSlot`) → выбор слота.
4. `CheckAvailability` (опционально) → проверка.
5. `CreateBooking` → подтверждение записи.

**Сценарий «Клиент отменяет запись»**
1. `ListBookings(client_id, from/to)` → список записей.
2. `CancelBooking(booking_id)` → отмена и освобождение слота.

**Сценарий «Провайдер отменяет окно слотов»**
1. `BulkCancelProviderSlots(provider_id, start, end, reason)` → отмена слотов и бронирований.
2. Внешний слой уведомляет клиентов по `AffectedBooking.client_telegram_id`.

---

## 4.4 Безопасность и надежность

Раздел фиксирует текущее состояние (что реально реализовано) и то, что обычно требуется для промышленной эксплуатации. Для итогового отчёта по проекту рекомендуется явно разделять «реализовано» и «рекомендуется».

### 4.4.1 Защита данных (фактически реализовано)

**Передача данных**
- gRPC сервер поднимается на `:50051` без TLS на уровне приложения (`cmd/main.go` использует `grpc.NewServer()` без creds).
- gRPC reflection включён (`reflection.Register`), что упрощает отладку, но в production обычно требует ограничения по сети/доступу.

**Хранение данных**
- Основное хранилище: PostgreSQL.
- Доступ к БД настраивается через переменные окружения (`internal/config/db.go`).
- Специальные механизмы шифрования на уровне приложения (шифрование полей, токенизация телефонов и т.п.) не реализованы.
- Используется `gen_random_uuid()` для UUID в моделях, поэтому в Postgres требуется расширение `pgcrypto` (указано в `README.md`).

### 4.4.2 Аутентификация и авторизация (фактически реализовано)

**Идентификация пользователя**
- Ключевой внешний идентификатор: `telegram_id` (таблица `users`, unique index).
- `IdentityService` предоставляет операции регистрации/профиля по `telegram_id`.

**Роли**
- Роли реализованы в БД (`roles`, `user_roles`) и управляются через `IdentityService.SetRole`.
- Применение ролей в бизнес‑логике `CalendarService` ограничено проверкой для провайдерских операций:
  - `ensureProviderRole` проверяет, что у владельца `provider_id` роль `"provider"`.

**Важно (ограничение текущей реализации):**
- В `core` нет механизма аутентификации вызова (нет JWT/mTLS/подписей запросов) и нет привязки «кто вызывает метод» к конкретному `telegram_id`/провайдеру.
- Следовательно, ответственность за аутентификацию/авторизацию клиента должна лежать на внешнем слое (например, Telegram‑боте/шлюзе), который:
  - проверяет пользователя в Telegram,
  - подставляет корректные `client_id`/`provider_id`,
  - ограничивает доступ к «провайдерским» методам.

### 4.4.3 Надёжность и целостность данных (фактически реализовано)

**Транзакционность и согласованность**
- `CreateBooking` выполняется в транзакции и блокирует строку слота `FOR UPDATE`, затем создаёт бронирование и обновляет статус слота.
- `BulkCancelProviderSlots` выполняется в транзакции: сначала собирает затронутые бронирования, затем массово отменяет бронирования и слоты.
- `CancelBooking` выполняется в транзакции: отмена бронирования + освобождение слота.

**Ограничения на уровне схемы**
- `bookings.slot_id` — unique index: не допускает более одного бронирования на один слот.
- Индексы на основных фильтрах (`provider_id`, `starts_at`, статусы), что снижает риск деградации на чтении при росте данных.

**Управление жизненным циклом сервиса**
- Graceful shutdown (`cmd/main.go`): `grpcServer.GracefulStop()` по сигналам SIGINT/SIGTERM.
- Пул соединений к БД настраивается из env (max open/idle/lifetime).

**Миграции**
- Используется `AutoMigrate` при запуске (простота разработки и развёртывания).
- Риск: при одновременном старте нескольких реплик миграции могут конкурировать; для production чаще используют управляемые миграции (отдельный job/миграционный инструмент).

### 4.4.4 Политики безопасности (рекомендуется для production, если требуется итоговым отчётом)

**Шифрование данных**
- Включить TLS для gRPC (или mTLS между сервисами) либо вынести TLS‑терминацию в gateway/ingress.
- Для Postgres включить SSL (`DB_SSLMODE=require/verify-full`) и управлять сертификатами.
- При необходимости защиты PII: шифрование/токенизация телефона (`contact_phone`) и ограничение доступа к нему.

**Логины/пароли и 2FA**
- В текущем `core` нет логинов/паролей: идентификатор — Telegram, а безопасность входа обеспечивается Telegram‑аккаунтом и внешним клиентом.
- Для административных интерфейсов (если появятся) рекомендуется отдельная аутентификация (например, OIDC) и 2FA на стороне админ‑панели/IdP.

**Авторизация (RBAC/ABAC)**
- Ввести проверку «кто вызывает метод»:
  - через JWT (gateway добавляет `sub`, `role`, `telegram_id`) и server‑side interceptor;
  - либо через mTLS (identity в сертификате).
- Расширить проверку прав на уровне методов:
  - провайдер может изменять только свои расписания/слоты;
  - клиент может отменять только свои бронирования;
  - администратор имеет расширенные операции.

**Резервное копирование и восстановление**
- Регулярные бэкапы PostgreSQL (snapshot + WAL/point‑in‑time recovery при необходимости).
- Регулярные тестовые восстановления (не только наличие бэкапа, но и проверка работоспособности restore).
- Мониторинг БД (латентность, количество соединений, заполнение диска).

**Наблюдаемость**
- Структурированные логи, метрики (latency/error rate по RPC, pool connections), трассировка (OpenTelemetry).
- Ограничение gRPC reflection на production окружениях (по сети или по аутентификации).
