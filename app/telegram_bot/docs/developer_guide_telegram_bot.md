# Техническое руководство разработчика (Telegram‑бот)

Документ предназначен для разработчиков и сопровождающих компонент `app/telegram_bot`: структура кода, ключевые зависимости, точки расширения, интеграции и типовые практики разработки.

## Стек и зависимости

- Python (asyncio).
- `aiogram` 3.x — обработка апдейтов Telegram, роутеры, FSM.
- `grpcio` (async `grpc.aio`) — вызовы core‑сервисов по gRPC.
- `SQLAlchemy` + `psycopg2-binary` — заготовка для локального Postgres (в текущем запуске не подключена).

Зависимости фиксируются в `requirements.txt`.

Примечание по gRPC: сгенерированные файлы `src/telegram_bot/generated/*_pb2_grpc.py` содержат проверку совместимости версии `grpcio` (поле `GRPC_GENERATED_VERSION`). При несовпадении версий возможны ошибки при импорте. В этом случае нужно либо обновить `grpcio`, либо заново сгенерировать stubs под используемую версию.

## Архитектура модуля

### Точка входа и DI‑контекст

- `src/telegram_bot/main.py`:
  - создаёт `Settings` (`src/telegram_bot/config.py`);
  - создаёт `GrpcClients` (`src/telegram_bot/services/grpc_clients.py`);
  - создаёт `Bot`/`Dispatcher` (`src/telegram_bot/bot.py`);
  - складывает зависимости в `dispatcher.workflow_data`:
    - `workflow_data["settings"]`
    - `workflow_data["grpc_clients"]`
  - запускает long polling: `dispatcher.start_polling(bot)`.

Обработчики получают зависимости так:
- `settings = message.bot.dispatcher.workflow_data.get("settings")`
- `clients = message.bot.dispatcher.workflow_data.get("grpc_clients")`

### Слои и папки

- `handlers/*` — presentation слой: aiogram Router, обработка сообщений/коллбеков, переходы FSM.
- `states.py` — модель состояний FSM.
- `keyboards.py` — построение reply/inline клавиатур, форматирование `callback_data`.
- `services/*` — application/integration слой: gRPC‑вызовы и преобразование protobuf → DTO.
- `dto.py` — dataclass‑модели, используемые в UI‑коде.
- `generated/*` — сгенерированные protobuf/gRPC stubs.
- `utils/*` — утилиты: correlation id, работа со временем.
- `db/*`, `models/*` — заготовка для локальной БД (не используется в `main.py` по умолчанию).

## Интеграции и API

### gRPC клиенты

`src/telegram_bot/services/grpc_clients.py` реализует:
- кэширование каналов по endpoint;
- выбор `insecure_channel` или `secure_channel` (TLS) по настройкам;
- создание stubs:
  - `identity_stub()`
  - `calendar_stub()`
- закрытие каналов в `GrpcClients.close()`.

Для сквозной трассировки используется metadata `x-corr-id`:
- генерация: `src/telegram_bot/utils/corr.py`
- упаковка: `build_metadata()` в `grpc_clients.py`

### Контракты

Исходные `.proto` лежат в `api/proto/*`:
- `identity.proto` — регистрация/роль/контакты/поиск провайдера по телефону.
- `calendar.proto` + `common.proto` — каталог услуг, слоты, бронирования, расписания.

Фактически используемые методы со стороны бота сосредоточены в:
- `src/telegram_bot/services/identity.py`
- `src/telegram_bot/services/calendar.py`

## FSM и сценарии

Состояния описаны в `src/telegram_bot/states.py`:
- `ClientStates` — welcome → каталог → слоты → подтверждение → результат → мои записи и т.д.
- `ProviderStates` — состояния для роли провайдера (часть сценариев может быть экспериментальной).

Рекомендуемая практика:
- хранить в FSM только контекст диалога и кэши для UI (ID выбранных сущностей, текущая страница, краткие DTO);
- доменные данные не “дублировать” в бот‑хранилище — источником истины остаются core‑сервисы.

## Расширение функциональности

### Добавление нового сценария

1. Добавьте состояния в `src/telegram_bot/states.py` (если нужен диалог).
2. Добавьте кнопки/клавиатуры в `src/telegram_bot/keyboards.py`:
   - придерживайтесь единого паттерна `prefix:action:...` для `callback_data`.
3. Реализуйте обработчики в новом или существующем модуле `src/telegram_bot/handlers/*.py`.
4. Подключите router в `src/telegram_bot/handlers/__init__.py`.
5. Вынесите сетевые/доменные операции в `src/telegram_bot/services/*` (не делать прямые protobuf‑маппинги в handlers).
6. Все gRPC вызовы оборачивайте в обработку `grpc.aio.AioRpcError` и отдавайте пользователю сообщение через `user_friendly_error()` (`src/telegram_bot/services/errors.py`).

### Работа с ошибками и UX

- Для ошибок core используйте `user_friendly_error(exc)` (grpc status → текст).
- Для неполных данных в FSM возвращайте пользователя к `/start` или предшествующему шагу (в проекте часто используется текст “повторите /start”).

### Время и таймзоны

`src/telegram_bot/utils/time.py` трактует protobuf `Timestamp` как UTC и проставляет `tzinfo=timezone.utc`. При добавлении новых сценариев:
- передавайте `datetime` в gRPC только tz-aware (или полагайтесь на приведение к UTC в `to_timestamp()`).

## Локальная БД (статус)

В проекте есть `src/telegram_bot/db/*` и `src/telegram_bot/models/*`, но по умолчанию `main.py` не создаёт engine/session_factory и не прокидывает его в `workflow_data`. Если планируется использовать локальную БД (кэш/аудит/доп. настройки):
- нужно инициализировать engine через `make_engine(Settings.database_url)` и `make_session_factory(engine)`;
- затем передать `session_factory` в `dispatcher.workflow_data`.

## Известные особенности текущей кодовой базы

- `src/telegram_bot/handlers/provider_flow.py` присутствует, но по умолчанию не подключён в `src/telegram_bot/handlers/__init__.py`. Перед включением проверьте согласованность callback‑данных и сигнатур клавиатур из `src/telegram_bot/keyboards.py`.
