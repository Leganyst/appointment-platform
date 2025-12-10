package main

import (
	"log"

	"github.com/Leganyst/appointment-platform/internal/config"
	"github.com/Leganyst/appointment-platform/internal/db"
	"github.com/Leganyst/appointment-platform/internal/model"
)

func main() {
	dbCfg, err := config.LoadDBConfig()
	if err != nil {
		log.Fatalf("load db config: %v", err)
	}

	gormDB, err := db.NewGormDB(dbCfg)
	if err != nil {
		log.Fatalf("init db: %v", err)
	}

	if err := model.AutoMigrate(gormDB); err != nil {
		log.Fatalf("auto migrate: %v", err)
	}

	sqlDB, err := gormDB.DB()
	if err != nil {
		log.Fatalf("sql DB: %v", err)
	}
	defer sqlDB.Close()

	// дальше — инициализация сервисов ядра календаря, gRPC/HTTP и т.п.
}
