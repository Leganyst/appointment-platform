package calendar

import (
	"context"
	"errors"
)

// Ошибки валидации Telegram-пользователя.
var (
	ErrInvalidTelegramID = errors.New("invalid telegram id")
	ErrUserNotFound      = errors.New("user not found")
	ErrUserInactive      = errors.New("user is inactive")
)

// Статус пользователя в системе.
type UserStatus string

const (
	UserStatusActive   UserStatus = "active"
	UserStatusInactive UserStatus = "inactive"
	UserStatusBlocked  UserStatus = "blocked"
)

// Роль пользователя в системе.
type UserRole string

const (
	UserRoleClient   UserRole = "client"
	UserRoleProvider UserRole = "provider"
	UserRoleAdmin    UserRole = "admin"
	UserRoleUnknown  UserRole = "unknown"
)

// Доменная модель пользователя.
type User struct {
	ID         int64
	TelegramID int64
	Role       UserRole
	Status     UserStatus
}

// Результат успешной валидации.
type ValidatedUser struct {
	ID         int64
	TelegramID int64
	Role       UserRole
	Status     UserStatus
}

// Источник данных о пользователях.
// В реале это может быть обёртка над БД, в тестах — мок.
type UserStore interface {
	FindByTelegramID(ctx context.Context, telegramID int64) (*User, error)
}

// ValidateTelegramUser:
//   - проверяет корректность идентификатора;
//   - вытаскивает пользователя из хранилища;
//   - проверяет статус (активен / нет);
//   - возвращает нормализованный результат или ошибку.
func ValidateTelegramUser(
	ctx context.Context,
	store UserStore,
	telegramID int64,
) (*ValidatedUser, error) {
	if telegramID <= 0 {
		return nil, ErrInvalidTelegramID
	}

	u, err := store.FindByTelegramID(ctx, telegramID)
	if err != nil {
		return nil, err
	}
	if u == nil {
		return nil, ErrUserNotFound
	}

	if u.Status == UserStatusInactive || u.Status == UserStatusBlocked {
		return nil, ErrUserInactive
	}

	return &ValidatedUser{
		ID:         u.ID,
		TelegramID: u.TelegramID,
		Role:       u.Role,
		Status:     u.Status,
	}, nil
}
