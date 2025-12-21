package repository

import (
	"context"
	"strings"

	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
	"github.com/google/uuid"
)

type UserRepository interface {
	FindByTelegramID(ctx context.Context, telegramID int64) (*model.User, error)
	FindByPhone(ctx context.Context, phone string) (*model.User, error)
	FindByUsername(ctx context.Context, username string) (*model.User, error)
	ClearRoles(ctx context.Context, userID uuid.UUID) error
	ResetAccount(ctx context.Context, telegramID int64) (*model.User, error)
	UpsertUser(ctx context.Context, telegramID int64, displayName, username, contactPhone string) (*model.User, error)
	UpdateContacts(ctx context.Context, telegramID int64, displayName, username, contactPhone string) (*model.User, error)
	SetRole(ctx context.Context, userID uuid.UUID, roleCode string) error
	GetRole(ctx context.Context, userID uuid.UUID) (string, error)
}

type GormUserRepository struct {
	db *gorm.DB
}

func NewGormUserRepository(db *gorm.DB) *GormUserRepository {
	return &GormUserRepository{db: db}
}

func (r *GormUserRepository) FindByTelegramID(ctx context.Context, telegramID int64) (*model.User, error) {
	var u model.User
	if err := r.db.WithContext(ctx).Where("telegram_id = ?", telegramID).First(&u).Error; err != nil {
		return nil, err
	}
	return &u, nil
}

func normalizePhone(phone string) string {
	phone = strings.TrimSpace(phone)
	if phone == "" {
		return ""
	}
	// Keep only digits; ignore formatting characters.
	b := make([]byte, 0, len(phone))
	for i := 0; i < len(phone); i++ {
		c := phone[i]
		if c >= '0' && c <= '9' {
			b = append(b, c)
		}
	}
	digits := string(b)
	// RU-friendly: 8XXXXXXXXXX -> 7XXXXXXXXXX, 10 digits -> prefix 7
	if len(digits) == 11 && strings.HasPrefix(digits, "8") {
		digits = "7" + digits[1:]
	} else if len(digits) == 10 {
		digits = "7" + digits
	}
	return digits
}

func (r *GormUserRepository) FindByPhone(ctx context.Context, phone string) (*model.User, error) {
	n := normalizePhone(phone)
	if n == "" {
		return nil, gorm.ErrRecordNotFound
	}

	var u model.User
	// Try normalized first, then raw (in case old data is not normalized).
	q := r.db.WithContext(ctx).Model(&model.User{}).
		Where("contact_phone = ?", n)
	if strings.TrimSpace(phone) != n {
		q = q.Or("contact_phone = ?", strings.TrimSpace(phone))
	}
	if err := q.First(&u).Error; err != nil {
		return nil, err
	}
	return &u, nil
}

func normalizeUsername(username string) string {
	u := strings.TrimSpace(username)
	u = strings.TrimPrefix(u, "@")
	u = strings.ToLower(u)
	return u
}

func (r *GormUserRepository) FindByUsername(ctx context.Context, username string) (*model.User, error) {
	uq := normalizeUsername(username)
	if uq == "" {
		return nil, gorm.ErrRecordNotFound
	}
	var u model.User
	// Username stored in Note (see mapUser). Make lookup case-insensitive.
	if err := r.db.WithContext(ctx).Model(&model.User{}).Where("LOWER(note) = ?", uq).First(&u).Error; err != nil {
		return nil, err
	}
	return &u, nil
}

func (r *GormUserRepository) ClearRoles(ctx context.Context, userID uuid.UUID) error {
	if userID == uuid.Nil {
		return gorm.ErrRecordNotFound
	}
	return r.db.WithContext(ctx).Where("user_id = ?", userID).Delete(&model.UserRole{}).Error
}

func (r *GormUserRepository) ResetAccount(ctx context.Context, telegramID int64) (*model.User, error) {
	if telegramID <= 0 {
		return nil, gorm.ErrRecordNotFound
	}
	updates := map[string]any{
		"display_name":  "",
		"contact_phone": "",
		"note":          "",
	}
	if err := r.db.WithContext(ctx).Model(&model.User{}).Where("telegram_id = ?", telegramID).Updates(updates).Error; err != nil {
		return nil, err
	}
	return r.FindByTelegramID(ctx, telegramID)
}

func (r *GormUserRepository) UpsertUser(ctx context.Context, telegramID int64, displayName, username, contactPhone string) (*model.User, error) {
	contactPhone = normalizePhone(contactPhone)
	username = normalizeUsername(username)
	var u model.User
	tx := r.db.WithContext(ctx).Where("telegram_id = ?", telegramID).First(&u)
	if tx.Error != nil {
		if tx.Error == gorm.ErrRecordNotFound {
			u.TelegramID = telegramID
			u.DisplayName = displayName
			u.ContactPhone = contactPhone
			// username не хранится отдельно в модели — можем сохранить в Note или расширить модель
			u.Note = username
			if err := r.db.WithContext(ctx).Create(&u).Error; err != nil {
				return nil, err
			}
			return &u, nil
		}
		return nil, tx.Error
	}
	// update existing
	updates := map[string]any{
		"display_name":  displayName,
		"contact_phone": contactPhone,
		"note":          username,
	}
	if err := r.db.WithContext(ctx).Model(&model.User{}).Where("telegram_id = ?", telegramID).Updates(updates).Error; err != nil {
		return nil, err
	}
	u.DisplayName = displayName
	u.ContactPhone = contactPhone
	u.Note = username
	return &u, nil
}

func (r *GormUserRepository) UpdateContacts(ctx context.Context, telegramID int64, displayName, username, contactPhone string) (*model.User, error) {
	updates := map[string]any{}
	if displayName != "" {
		updates["display_name"] = displayName
	}
	if contactPhone != "" {
		updates["contact_phone"] = normalizePhone(contactPhone)
	}
	if username != "" {
		updates["note"] = normalizeUsername(username)
	}
	if len(updates) == 0 {
		// nothing to update; just return current user
		return r.FindByTelegramID(ctx, telegramID)
	}
	if err := r.db.WithContext(ctx).Model(&model.User{}).Where("telegram_id = ?", telegramID).Updates(updates).Error; err != nil {
		return nil, err
	}
	return r.FindByTelegramID(ctx, telegramID)
}

func (r *GormUserRepository) SetRole(ctx context.Context, userID uuid.UUID, roleCode string) error {
	// ensure role exists
	var role model.Role
	if err := r.db.WithContext(ctx).Where("code = ?", roleCode).First(&role).Error; err != nil {
		if err == gorm.ErrRecordNotFound {
			role.Code = roleCode
			role.Name = roleCode
			if err := r.db.WithContext(ctx).Create(&role).Error; err != nil {
				return err
			}
		} else {
			return err
		}
	}

	// remove previous roles and set new one (single role policy)
	if err := r.db.WithContext(ctx).Where("user_id = ?", userID).Delete(&model.UserRole{}).Error; err != nil {
		return err
	}

	ur := model.UserRole{RoleID: role.ID, UserID: userID}
	return r.db.WithContext(ctx).Create(&ur).Error
}

func (r *GormUserRepository) GetRole(ctx context.Context, userID uuid.UUID) (string, error) {
	var ur model.UserRole
	if err := r.db.WithContext(ctx).Where("user_id = ?", userID).First(&ur).Error; err != nil {
		return "", err
	}
	var role model.Role
	if err := r.db.WithContext(ctx).First(&role, "id = ?", ur.RoleID).Error; err != nil {
		return "", err
	}
	return role.Code, nil
}
