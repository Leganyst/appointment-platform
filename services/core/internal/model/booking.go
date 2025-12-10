package model

import (
	"time"

	"github.com/google/uuid"
)

type BookingStatus string

const (
	BookingStatusPending   BookingStatus = "pending"
	BookingStatusConfirmed BookingStatus = "confirmed"
	BookingStatusCancelled BookingStatus = "cancelled"
)

// bookings
type Booking struct {
	ID          uuid.UUID     `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`
	ClientID    uuid.UUID     `gorm:"type:uuid;not null;index"`
	SlotID      uuid.UUID     `gorm:"type:uuid;not null;uniqueIndex"`
	CreatedAt   time.Time     `gorm:"not null;default:now()"`
	UpdatedAt   time.Time     `gorm:"not null;default:now()"`
	Status      BookingStatus `gorm:"type:varchar(32);not null;index"`
	CancelledAt *time.Time    `gorm:"type:timestamp with time zone"`
	Comment     string        `gorm:"type:text"`

	Client *Client   `gorm:"foreignKey:ClientID;constraint:OnUpdate:CASCADE,OnDelete:RESTRICT"`
	Slot   *TimeSlot `gorm:"foreignKey:SlotID;constraint:OnUpdate:CASCADE,OnDelete:RESTRICT"`
}
