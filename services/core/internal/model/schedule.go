package model

import (
	"time"

	"github.com/google/uuid"
	"gorm.io/datatypes"
)

// schedules
type Schedule struct {
	ID uuid.UUID `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`

	ProviderID uuid.UUID `gorm:"type:uuid;not null;index"`

	// Чистые даты без времени — datatypes.Date
	StartDate *datatypes.Date `gorm:"type:date"`
	EndDate   *datatypes.Date `gorm:"type:date"`

	TimeZone string `gorm:"type:varchar(64);not null;default:'UTC'"`

	// Правило повторения в виде JSON (можно хранить как JSONB в Postgres).
	Rules datatypes.JSON `gorm:"type:jsonb"`

	CreatedAt time.Time `gorm:"not null;default:now()"`
	UpdatedAt time.Time `gorm:"not null;default:now()"`

	Provider *Provider `gorm:"foreignKey:ProviderID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
}
