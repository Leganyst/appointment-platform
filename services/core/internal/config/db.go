package config

import (
	"fmt"
	"os"
	"strconv"
)

type DBConfig struct {
	Host            string
	Port            int
	User            string
	Password        string
	Name            string
	SSLMode         string
	TimeZone        string
	MaxOpenConns    int
	MaxIdleConns    int
	ConnMaxLifeTime int // минут
}

func LoadDBConfig() (*DBConfig, error) {
	cfg := &DBConfig{
		Host:            getEnv("DB_HOST", "postgres"),
		User:            getEnv("DB_USER", "booking"),
		Password:        getEnv("DB_PASSWORD", "booking"),
		Name:            getEnv("DB_NAME", "booking_db"),
		SSLMode:         getEnv("DB_SSLMODE", "disable"),
		TimeZone:        getEnv("DB_TIMEZONE", "Europe/Moscow"),
		Port:            getEnvInt("DB_PORT", 5432),
		MaxOpenConns:    getEnvInt("DB_MAX_OPEN_CONNS", 10),
		MaxIdleConns:    getEnvInt("DB_MAX_IDLE_CONNS", 5),
		ConnMaxLifeTime: getEnvInt("DB_CONN_MAX_LIFETIME_MIN", 30),
	}

	// минимальная валидация
	if cfg.Host == "" || cfg.User == "" || cfg.Name == "" {
		return nil, fmt.Errorf("invalid DB config: host/user/name must not be empty")
	}

	return cfg, nil
}

func getEnv(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func getEnvInt(key string, def int) int {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		i, err := strconv.Atoi(v)
		if err == nil {
			return i
		}
	}
	return def
}
