package main

import (
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"

	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	calendarpb "github.com/Leganyst/appointment-platform/internal/api/calendar/v1"
	identitypb "github.com/Leganyst/appointment-platform/internal/api/identity/v1"
	"github.com/Leganyst/appointment-platform/internal/config"
	"github.com/Leganyst/appointment-platform/internal/db"
	"github.com/Leganyst/appointment-platform/internal/model"
	"github.com/Leganyst/appointment-platform/internal/repository"
	"github.com/Leganyst/appointment-platform/internal/service"
)

func main() {
	// 1. Загружаем конфиг БД из env.
	dbCfg, err := config.LoadDBConfig()
	if err != nil {
		log.Fatalf("load db config: %v", err)
	}

	// 2. Подключаемся к БД через GORM.
	gormDB, err := db.NewGormDB(dbCfg)
	if err != nil {
		log.Fatalf("init db: %v", err)
	}

	// 3. Миграции моделей.
	if err := model.AutoMigrate(gormDB); err != nil {
		log.Fatalf("auto migrate: %v", err)
	}

	sqlDB, err := gormDB.DB()
	if err != nil {
		log.Fatalf("sql DB: %v", err)
	}
	defer sqlDB.Close()

	// 4. Репозитории (реализации на GORM).
	slotRepo := repository.NewGormSlotRepository(gormDB)
	bookingRepo := repository.NewGormBookingRepository(gormDB)
	scheduleRepo := repository.NewGormScheduleRepository(gormDB)
	userRepo := repository.NewGormUserRepository(gormDB)
	clientRepo := repository.NewGormClientRepository(gormDB)
	providerRepo := repository.NewGormProviderRepository(gormDB)
	serviceRepo := repository.NewGormServiceRepository(gormDB)

	// 5. gRPC-сервис календаря.
	calendarSvc := service.NewCalendarService(gormDB, slotRepo, bookingRepo, scheduleRepo, providerRepo, serviceRepo, userRepo)
	identitySvc := service.NewIdentityService(userRepo, clientRepo, providerRepo)

	// 6. Настраиваем gRPC-сервер.
	grpcServer := grpc.NewServer()
	calendarpb.RegisterCalendarServiceServer(grpcServer, calendarSvc)
	identitypb.RegisterIdentityServiceServer(grpcServer, identitySvc)
	reflection.Register(grpcServer)

	addr := ":50051" // можно вынести в env, например CORE_GRPC_ADDR
	lis, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("listen %s: %v", addr, err)
	}

	log.Printf("core gRPC server listening on %s", addr)

	// 7. Запускаем сервер в горутине.
	go func() {
		if err := grpcServer.Serve(lis); err != nil {
			log.Fatalf("grpc serve: %v", err)
		}
	}()

	// 8. Грейсфул-шатдаун по сигналу.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	log.Println("shutting down gRPC server...")
	grpcServer.GracefulStop()
}
