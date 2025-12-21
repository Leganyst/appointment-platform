# Разработка Telegram‑бота (компоненты, процессы, UI, безопасность)

Раздел описывает **фактическую реализацию** Telegram‑бота в репозитории `app/telegram_bot`: состав компонентов, интерфейсы взаимодействия, алгоритмы обработки, пользовательский интерфейс и меры безопасности/надёжности.

## Компонентный состав

### Общая структура

Компонент реализован как Python‑приложение с асинхронной обработкой событий Telegram (long polling) и обращением к core‑сервисам по gRPC.

- Точка входа и запуск:
  - `src/telegram_bot/main.py` — инициализация настроек, логирования, gRPC‑клиентов, запуск `Dispatcher.start_polling`.
  - `src/telegram_bot/bot.py` — фабрики `create_bot()` и `create_dispatcher()` (FSM‑хранилище: `MemoryStorage`).
- Конфигурация:
  - `src/telegram_bot/config.py` — класс `Settings`, чтение переменных окружения: токен, gRPC endpoints, TLS, deadline.
- Представление (Telegram UI):
  - `src/telegram_bot/handlers/*` — обработчики сообщений/коллбеков (aiogram Router).
  - `src/telegram_bot/states.py` — состояния FSM (`ClientStates`, `ProviderStates`).
  - `src/telegram_bot/keyboards.py` — reply/inline‑клавиатуры и шаблоны callback_data.
- Бизнес‑логика (use cases) / интеграции:
  - `src/telegram_bot/services/identity.py` — вызовы `IdentityService` и маппинг protobuf → DTO.
  - `src/telegram_bot/services/calendar.py` — вызовы `CalendarService` и маппинг protobuf → DTO.
  - `src/telegram_bot/services/grpc_clients.py` — кэширование gRPC‑каналов, создание stubs, TLS/insecure режим, `x-corr-id` metadata.
  - `src/telegram_bot/services/errors.py` — преобразование gRPC ошибок в пользовательские сообщения.
- DTO и утилиты:
  - `src/telegram_bot/dto.py` — dataclass‑модели (`IdentityUser`, `ServiceDTO`, `ProviderDTO`, `SlotDTO`, `BookingDTO`).
  - `src/telegram_bot/utils/corr.py` — генерация correlation id.
  - `src/telegram_bot/utils/time.py` — конвертация `google.protobuf.Timestamp` ↔ `datetime` (используется в `services/calendar.py`).
- Контракты API (protobuf) и сгенерированный код:
  - `api/proto/*.proto` — источники контрактов `IdentityService` и `CalendarService`.
  - `src/telegram_bot/generated/*` — сгенерированные `*_pb2.py` и `*_pb2_grpc.py`.

Зависимости (фиксированные версии): `requirements.txt` (ключевые: `aiogram`, `grpcio`, `SQLAlchemy`, `psycopg2-binary`).

### Назначение модулей и взаимодействие

**Передача контекста между модулями** реализована через `dispatcher.workflow_data`:
- `Settings` записывается в `workflow_data["settings"]` в `src/telegram_bot/main.py`.
- `GrpcClients` записывается в `workflow_data["grpc_clients"]` в `src/telegram_bot/main.py`.
Далее обработчики получают их через `message.bot.dispatcher.workflow_data`.

**Маршрутизация обработчиков**:
- `src/telegram_bot/handlers/__init__.py` создаёт общий `router` и подключает под‑роутеры: `start`, `role`, `client_flow`.

Примечание по фактическому состоянию:
- модуль `src/telegram_bot/handlers/provider_flow.py` присутствует, но **не подключён** в `handlers/__init__.py`, и содержит несогласованности с API клавиатур (например, вызов `provider_schedule_keyboard(...)`). В текущем виде работа представителя услуг через команды меню требует доработки/подключения.

### Интерфейсы API для межмодульного взаимодействия

#### Внутренние интерфейсы (внутри бота)

- `handlers/*` → `services/*`: обработчики вызывают функции use‑case уровня (`register_user`, `list_services`, `find_free_slots`, …).
- `services/*` → `generated/*`: функции сервисов используют сгенерированные stubs и protobuf‑типы.
- `handlers/*` ↔ `FSMContext`: хранение диалогового контекста (выбранная услуга/провайдер/slot_id, роль, client_id/provider_id и т.д.).

#### Внешние интерфейсы (bot ↔ core)

**IdentityService** (`api/proto/identity.proto`), методы, используемые ботом:
- `RegisterUser` — регистрация/получение пользователя по `telegram_id` (`src/telegram_bot/handlers/start.py`).
- `SetRole` — установка `role_code` (`src/telegram_bot/handlers/role.py`).
- `UpdateContacts` — сохранение контактов (`src/telegram_bot/handlers/role.py`).
- `FindProviderByPhone` — поиск провайдера по телефону (`src/telegram_bot/handlers/client_flow.py`).

**CalendarService** (`api/proto/calendar.proto`), методы, используемые ботом:
- каталог: `ListServices`, `ListProviders`, `ListProviderServices`;
- слоты: `FindFreeSlots`, `ListProviderSlots` (используется для построения карты слотов в «Мои записи»);
- бронирование: `CheckAvailability`, `CreateBooking`, `ListBookings`, `GetBooking`, `CancelBooking`;
- (для провайдера в заготовке): `ListProviderBookings`, `ConfirmBooking`, `CreateSlot`, `UpdateSlot`, `DeleteSlot`.

Для трассировки запросов в каждом gRPC вызове прокидывается metadata `x-corr-id` (`src/telegram_bot/services/grpc_clients.py`).

## Алгоритмы и процессы обработки

Ниже приведены последовательности действий для ключевых функций, как они реализованы в текущем коде.

### Старт и регистрация пользователя

Файл: `src/telegram_bot/handlers/start.py`

1. Пользователь отправляет `/start`.
2. Бот формирует `corr_id` и вызывает `IdentityService.RegisterUser(telegram_id, display_name, username)`.
3. Ответ (`user`) сохраняется в FSM: `client_id`, `provider_id`, `role_code`, контакты.
4. В зависимости от `role_code` выбирается следующее состояние FSM и клавиатура:
   - `provider` → `ProviderStates.main_menu` + `provider_main_menu_keyboard()`;
   - иначе → `ClientStates.welcome` + `start_keyboard()`.
5. При ошибках gRPC возвращается сообщение «Не удалось связаться с Identity сервисом».

### Выбор роли и заполнение профиля

Файл: `src/telegram_bot/handlers/role.py`

1. Пользователь нажимает «Выбрать роль / Настроить профиль» (`callback_data="role:start"`).
2. Бот показывает выбор роли: `client` или `provider`.
3. Ветка `client`:
   - запрос контакта → подтверждение → `IdentityService.SetRole` → `IdentityService.UpdateContacts` → переход в `ClientStates.main_menu`.
4. Ветка `provider`:
   - последовательный сбор `name`, `description`, `contact` (в FSM как `provider_setup`) → подтверждение;
   - вызовы `IdentityService.SetRole` и `IdentityService.UpdateContacts`;
   - затем `CalendarService.UpdateProviderProfile(provider_id, display_name, description)`;
   - переход в `ProviderStates.main_menu`.

### Клиент: поиск услуги → выбор провайдера → слоты → бронирование

Файл: `src/telegram_bot/handlers/client_flow.py`

1. В главном меню (reply‑кнопка) пользователь выбирает «Поиск услуг».
2. Бот вызывает `CalendarService.ListServices(page=1, page_size=10)`, сохраняет результат в `service_cache` и показывает inline‑список услуг с пагинацией.
3. Пользователь выбирает услугу → бот вызывает `CalendarService.ListProviders(service_id, page=1, page_size=10)`, сохраняет `provider_cache`, показывает список провайдеров.
4. Пользователь выбирает провайдера → бот вызывает `CalendarService.FindFreeSlots(provider_id, service_id, window=DEFAULT_SLOTS_WINDOW_DAYS, limit=10)`.
5. Пользователь выбирает слот → бот переводит в `ClientStates.booking_confirm` и показывает подтверждение.
6. Подтверждение:
   - `CalendarService.CheckAvailability(client_id, slot_id)`;
   - если доступно → `CalendarService.CreateBooking(client_id, slot_id)`;
   - результат показывается в `ClientStates.booking_result`.
7. Отмена на шаге подтверждения возвращает пользователя к выбору слотов (повторный `FindFreeSlots`).

Текущая обработка случая «слот недоступен»:
- пользователь получает текст с причиной и остаётся без автоматически подгруженных альтернатив (альтернативы подгружаются в иных ветках, например если у провайдера слотов нет — предлагается выбрать другого провайдера).

### Клиент: «Мои записи», детали и отмена

Файл: `src/telegram_bot/handlers/client_flow.py`

1. Пользователь выбирает «Мои записи» (reply‑кнопка).
2. Бот вызывает `CalendarService.ListBookings(client_id)` и строит карту слотов для отображения дат/времени:
   - группирует записи по `provider_id`;
   - запрашивает `CalendarService.ListProviderSlots(... include_bookings=true ...)` крупными страницами (`page_size=500`) в окне времени (≈ −180…+365 дней);
   - сопоставляет `booking.slot_id` → `slot.starts_at`.
3. Пользователь может открыть детали (`GetBooking`) и отменить активную запись (`CancelBooking(reason="client_request")`).

### Поиск провайдера по телефону

Файл: `src/telegram_bot/handlers/client_flow.py`

1. Пользователь выбирает «Найти провайдера по телефону».
2. Бот вызывает `IdentityService.FindProviderByPhone(phone)`.
3. Затем подгружает услуги провайдера: `CalendarService.ListProviderServices(provider_id)`.
4. Пользователь выбирает услугу → бот показывает свободные слоты через `FindFreeSlots`.

### Методы оптимизации производительности (фактически применяемые)

- Асинхронные gRPC вызовы (`grpc.aio`) и обработка Telegram событий в одном event loop.
- Кэширование gRPC каналов по endpoint (`src/telegram_bot/services/grpc_clients.py`).
- Пагинация каталогов (`SERVICE_PAGE_SIZE=10`, `PROVIDER_PAGE_SIZE=10`) и ограничение количества кнопок в inline‑клавиатурах (`services[:20]`, `slots[:15]`, `bookings[:20]`).
- Локальный кэш объектов в FSM (`service_cache`, `provider_cache`, `slot_cache`) для отображения без повторных вызовов в пределах диалога.

## Интерфейс пользователя (Telegram UI)

UI реализован средствами Telegram: текстовые сообщения + reply‑клавиатуры (постоянное меню) и inline‑клавиатуры (контекстные действия).

### Основные экраны/сценарии

- Стартовый экран (`/start`):
  - приветствие и предложение: «Выбрать роль / Настроить профиль» или «Главное меню» (`src/telegram_bot/keyboards.py` → `start_keyboard()`).
- Главное меню клиента (reply):
  - «Поиск услуг», «Найти провайдера по телефону», «Мои записи», «Профиль», «Помощь» (`main_menu_keyboard()`).
- Каталог услуг (inline):
  - список услуг + стрелки пагинации + «В главное меню» (`service_search_keyboard()`).
- Список провайдеров по услуге (inline):
  - список провайдеров + пагинация (`provider_keyboard()`).
- Список слотов (inline):
  - кнопки с датой/временем слота и действие «Назад к выбору представителя» (`slots_keyboard()`).
- Подтверждение брони (inline):
  - «Подтвердить» / «Отменить» (`booking_confirm_keyboard()`).
- Мои записи (inline):
  - список записей, кнопка «Отменить» для активных, переход в детали (`my_bookings_keyboard()` / `booking_details_keyboard()`).

### Навигация и UX‑принципы, реализованные в коде

- Разделение на FSM‑состояния для последовательных сценариев (см. `src/telegram_bot/states.py`).
- Встроенная навигация «в меню» (`callback_data="menu:main"`) из большинства экранов.
- Человекочитаемые сообщения об ошибках core‑сервисов по кодам gRPC (`src/telegram_bot/services/errors.py`).
- Ограничение длины описаний (`_truncate`) для удобства чтения в чате.

## Безопасность и надёжность

### Защита данных и каналы связи

- **Telegram Bot API**: передача данных между клиентом Telegram и ботом происходит через инфраструктуру Telegram (HTTPS/TLS на стороне Telegram).
- **gRPC бот ↔ core**:
  - по умолчанию используется `insecure_channel`;
  - поддерживается включение TLS через `GRPC_TLS=true` и загрузка корневого сертификата `GRPC_ROOT_CERT` (`src/telegram_bot/services/grpc_clients.py`).
- Секреты и конфигурация:
  - токен бота `BOT_TOKEN` и адреса сервисов задаются через переменные окружения (`src/telegram_bot/config.py`).

### Аутентификация и авторизация (фактическая модель)

- Аутентификация пользователя в системе осуществляется по **Telegram ID** (передаётся в `IdentityService.RegisterUser`).
- Пароли/логины и 2FA в бот‑части **не используются**; роль (`role_code`) задаётся через `IdentityService.SetRole`.
- Ограничение действий по ролям на стороне бота носит характер UX‑ветвления (какое меню показать); доменные ограничения должны обеспечиваться core‑сервисами.

### Надёжность, резервирование и восстановление

- Таймауты вызовов core‑сервисов: `GRPC_DEADLINE_SEC` (используется в обработчиках при каждом вызове).
- Обработка типовых отказов gRPC: `UNAVAILABLE`, `DEADLINE_EXCEEDED`, `INVALID_ARGUMENT`, `NOT_FOUND`, `FAILED_PRECONDITION` с понятными сообщениями (`src/telegram_bot/services/errors.py`).
- FSM‑состояние хранится в памяти (`MemoryStorage`), поэтому:
  - при перезапуске процесса бот теряет контекст текущих диалогов;
  - доменные данные (слоты/записи) не теряются, т.к. хранятся в core‑части.
- Бэкапы:
  - собственное хранилище критичных данных у бота отсутствует (бот зависит от core);
  - при использовании локальной БД (модули `src/telegram_bot/db/*`, `src/telegram_bot/models/*`) резервное копирование относится к инфраструктуре PostgreSQL, но этот путь в текущем запуске не подключён (в `main.py` `session_factory` не инициализируется).
