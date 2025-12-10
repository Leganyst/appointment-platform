package db

import (
	"fmt"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"

	"github.com/Leganyst/appointment-platform/internal/config"
)

func NewGormDB(cfg *config.DBConfig) (*gorm.DB, error) {
	dsn := fmt.Sprintf(
		"host=%s user=%s password=%s dbname=%s port=%d sslmode=%s TimeZone=%s",
		cfg.Host,
		cfg.User,
		cfg.Password,
		cfg.Name,
		cfg.Port,
		cfg.SSLMode,
		cfg.TimeZone,
	)

	gormCfg := &gorm.Config{
		Logger: gormlogger.Default.LogMode(gormlogger.Warn),
		NowFunc: func() time.Time {
			// всегда в UTC, дальше уже сами конвертим в нужные таймзоны
			return time.Now().UTC()
		},
	}

	db, err := gorm.Open(postgres.Open(dsn), gormCfg)
	if err != nil {
		return nil, fmt.Errorf("gorm open: %w", err)
	}

	sqlDB, err := db.DB()
	if err != nil {
		return nil, fmt.Errorf("db.DB(): %w", err)
	}

	if cfg.MaxOpenConns > 0 {
		sqlDB.SetMaxOpenConns(cfg.MaxOpenConns)
	}
	if cfg.MaxIdleConns > 0 {
		sqlDB.SetMaxIdleConns(cfg.MaxIdleConns)
	}
	if cfg.ConnMaxLifeTime > 0 {
		sqlDB.SetConnMaxLifetime(time.Duration(cfg.ConnMaxLifeTime) * time.Minute)
	}

	return db, nil
}
