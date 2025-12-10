package model

import (
	"time"

	"github.com/google/uuid"
)

// users
type User struct {
	ID uuid.UUID `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`

	TelegramID   int64  `gorm:"not null;uniqueIndex"`
	DisplayName  string `gorm:"type:varchar(255)"`
	ContactPhone string `gorm:"type:varchar(32)"`

	Note string `gorm:"type:text"`

	CreatedAt time.Time `gorm:"not null;default:now()"`
	UpdatedAt time.Time `gorm:"not null;default:now()"`

	// Навигационные поля (опционально)
	Client   *Client   `gorm:"foreignKey:UserID"`
	Provider *Provider `gorm:"foreignKey:UserID"`
	// Roles []Role `gorm:"many2many:user_roles"` — если захотим
}

// clients
type Client struct {
	ID uuid.UUID `gorm:"type:uuid;default:gen_random_uuid();primaryKey"`

	UserID uuid.UUID `gorm:"type:uuid;not null;index"`

	Comment string `gorm:"type:text"`

	CreatedAt time.Time `gorm:"not null;default:now()"`
	UpdatedAt time.Time `gorm:"not null;default:now()"`

	User *User `gorm:"foreignKey:UserID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
}
