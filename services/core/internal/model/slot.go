package model

import (
	"time"

	"github.com/google/uuid"
)

// Статус слота расписания.
type TimeSlotStatus string

const (
	TimeSlotStatusPlanned   TimeSlotStatus = "planned"
	TimeSlotStatusBooked    TimeSlotStatus = "booked"
	TimeSlotStatusCancelled TimeSlotStatus = "cancelled"
)

// time_slots
type TimeSlot struct {
	ID uuid.UUID `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`

	ScheduleID *uuid.UUID `gorm:"type:uuid;index"`
	ProviderID uuid.UUID  `gorm:"type:uuid;not null;index"`
	ServiceID  *uuid.UUID `gorm:"type:uuid;index"`

	StartsAt time.Time `gorm:"type:timestamp with time zone;not null;index"`
	EndsAt   time.Time `gorm:"type:timestamp with time zone;not null"`

	Status TimeSlotStatus `gorm:"type:varchar(32);not null;default:'planned';index"`

	CreatedAt time.Time `gorm:"not null;default:now()"`
	UpdatedAt time.Time `gorm:"not null;default:now()"`

	// Навигационные поля (опционально, но удобно для Preload).
	Schedule *Schedule `gorm:"foreignKey:ScheduleID;constraint:OnUpdate:CASCADE,OnDelete:SET NULL"`
	Provider *Provider `gorm:"foreignKey:ProviderID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
	Service  *Service  `gorm:"foreignKey:ServiceID;constraint:OnUpdate:CASCADE,OnDelete:SET NULL"`
}
