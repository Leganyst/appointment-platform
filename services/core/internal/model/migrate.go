package model

import "gorm.io/gorm"

// AutoMigrate выполняет миграцию всех сущностей календарного ядра.
func AutoMigrate(db *gorm.DB) error {
	return db.AutoMigrate(
		&User{},
		&Role{},
		&UserRole{},
		&Client{},
		&Provider{},
		&Service{},
		&ProviderService{},
		&Schedule{},
		&TimeSlot{},
		&Booking{},
		&Event{},
	)
}
