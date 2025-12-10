package model

import "github.com/google/uuid"

// roles
type Role struct {
	ID   int64  `gorm:"primaryKey;autoIncrement"`
	Code string `gorm:"type:varchar(32);not null;uniqueIndex"`
	Name string `gorm:"type:varchar(255)"`

	// Users []User `gorm:"many2many:user_roles"` — можно добавить, но не обязательно
}

// user_roles — связывает пользователей и роли (комбинированный PK)
type UserRole struct {
	RoleID int64     `gorm:"primaryKey;index"`
	UserID uuid.UUID `gorm:"type:uuid;primaryKey;index"`

	// Навигационные поля (по желанию)
	Role *Role `gorm:"foreignKey:RoleID;constraint:OnUpdate:CASCADE,OnDelete:RESTRICT"`
	User *User `gorm:"foreignKey:UserID;constraint:OnUpdate:CASCADE,OnDelete:CASCADE"`
}
