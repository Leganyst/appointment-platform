# syntax=docker/dockerfile:1.7

############################
# 1. builder-образ
############################
FROM golang:1.25 AS builder

# Рекомендуемая структура от Docker/Go-гайда: отдельный WORKDIR и копирование go.mod сначала 
WORKDIR /app

# Сначала только модули — максимум кэширования
COPY go.mod go.sum ./

# Включаем BuildKit-кэш для модулей
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download

# Теперь копируем остальной исходный код
COPY . .

# Собираем статический бинарь с кэшем компиляции
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -o /bin/core ./cmd

############################
# 2. финальный runtime-образ
############################
# Distroless static + nonroot: минимальный и безопасный рантайм 
FROM gcr.io/distroless/static-debian13:nonroot

# Порт gRPC сервиса
EXPOSE 50051

# Копируем бинарь из builder-стейджа
COPY --from=builder /bin/core /core

# nonroot уже заложен в базовый образ, так что дополнительно USER не нужен 

# В distroless нужен JSON-формат ENTRYPOINT/CMD (нет /bin/sh) 
ENTRYPOINT ["/core"]
