package model

import (
	"time"

	"github.com/google/uuid"
)

// Тип события аудита.
type EventType string

const (
	EventTypeBookingCreated   EventType = "booking_created"
	EventTypeBookingCancelled EventType = "booking_cancelled"
	EventTypeBookingUpdated   EventType = "booking_updated"
	EventTypeUserValidated    EventType = "user_validated"
)

// events — события аудита
type Event struct {
	ID uuid.UUID `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`

	EventType EventType `gorm:"type:varchar(64);not null;index"`

	CreatedAt time.Time `gorm:"not null;default:now();index"`

	UserID    *uuid.UUID `gorm:"type:uuid;index"`
	BookingID *uuid.UUID `gorm:"type:uuid;index"`

	Details string `gorm:"type:text"`

	// Навигационные поля
	User    *User    `gorm:"foreignKey:UserID;constraint:OnUpdate:CASCADE,OnDelete:SET NULL"`
	Booking *Booking `gorm:"foreignKey:BookingID;constraint:OnUpdate:CASCADE,OnDelete:SET NULL"`
}
