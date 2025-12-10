package model

import (
	"time"

	"github.com/google/uuid"
)

// services
type Service struct {
	ID uuid.UUID `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`

	Name        string `gorm:"type:varchar(255);not null"`
	Description string `gorm:"type:text"`

	// В минутах, может быть nil, если услуга не фиксирована по времени.
	DefaultDurationMin *int64 `gorm:"type:bigint"`

	IsActive bool `gorm:"not null;default:true;index"`

	CreatedAt time.Time `gorm:"not null;default:now()"`
	UpdatedAt time.Time `gorm:"not null;default:now()"`

	// Навигация many2many
	Providers []Provider `gorm:"many2many:provider_services;constraint:OnUpdate:CASCADE,OnDelete:RESTRICT"`
}

// provider_services — кастомная join-таблица многие-ко-многим.
type ProviderService struct {
	ProviderID uuid.UUID `gorm:"type:uuid;primaryKey"`
	ServiceID  uuid.UUID `gorm:"type:uuid;primaryKey"`

	CreatedAt time.Time `gorm:"not null;default:now()"`
	UpdatedAt time.Time `gorm:"not null;default:now()"`

	Provider *Provider `gorm:"foreignKey:ProviderID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
	Service  *Service  `gorm:"foreignKey:ServiceID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
}
