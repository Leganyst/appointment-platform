package model

import (
	"time"

	"github.com/google/uuid"
)

// Provider — представитель услуг (консультант, мастер и т.п.).
// Привязан к базе пользователей через UserID.
type Provider struct {
	ID uuid.UUID `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`

	// Внешний ключ на таблицу пользователей.
	UserID uuid.UUID `gorm:"type:uuid;not null;index"`

	// Имя/отображаемое название в интерфейсе.
	DisplayName string `gorm:"type:varchar(255);not null"`

	// Краткое описание, специализация и т.п.
	Description string `gorm:"type:text"`

	CreatedAt time.Time `gorm:"not null;default:now()"`
	UpdatedAt time.Time `gorm:"not null;default:now()"`

	// Навигационные поля для GORM (опционально, но удобно для Preload).
	User *User `gorm:"foreignKey:UserID;constraint:OnUpdate:CASCADE,OnDelete:RESTRICT"`

	Services []Service `gorm:"many2many:provider_services;constraint:OnUpdate:CASCADE,OnDelete:RESTRICT"`

	Schedules []Schedule `gorm:"foreignKey:ProviderID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
	Slots     []TimeSlot `gorm:"foreignKey:ProviderID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
}
