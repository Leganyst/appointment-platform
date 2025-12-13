package repository

import (
	"context"

	"github.com/google/uuid"
	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type ProviderRepository interface {
	GetByID(ctx context.Context, id string) (*model.Provider, error)
	GetByUserID(ctx context.Context, userID uuid.UUID) (*model.Provider, error)
	EnsureByUserID(ctx context.Context, userID uuid.UUID, displayName string) (*model.Provider, error)
}

type GormProviderRepository struct {
	db *gorm.DB
}

func NewGormProviderRepository(db *gorm.DB) *GormProviderRepository {
	return &GormProviderRepository{db: db}
}

func (r *GormProviderRepository) GetByID(ctx context.Context, id string) (*model.Provider, error) {
	var p model.Provider
	if err := r.db.WithContext(ctx).First(&p, "id = ?", id).Error; err != nil {
		return nil, err
	}
	return &p, nil
}

func (r *GormProviderRepository) GetByUserID(ctx context.Context, userID uuid.UUID) (*model.Provider, error) {
	var p model.Provider
	if err := r.db.WithContext(ctx).First(&p, "user_id = ?", userID).Error; err != nil {
		return nil, err
	}
	return &p, nil
}

func (r *GormProviderRepository) EnsureByUserID(ctx context.Context, userID uuid.UUID, displayName string) (*model.Provider, error) {
	if userID == uuid.Nil {
		return nil, gorm.ErrRecordNotFound
	}
	var p model.Provider
	tx := r.db.WithContext(ctx).First(&p, "user_id = ?", userID)
	if tx.Error == nil {
		return &p, nil
	}
	if tx.Error != gorm.ErrRecordNotFound {
		return nil, tx.Error
	}

	if displayName == "" {
		displayName = "Provider"
	}

	p = model.Provider{UserID: userID, DisplayName: displayName}
	if err := r.db.WithContext(ctx).Create(&p).Error; err != nil {
		return nil, err
	}
	return &p, nil
}
