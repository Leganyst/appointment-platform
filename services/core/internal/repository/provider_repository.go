package repository

import (
	"context"

	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type ProviderRepository interface {
	GetByID(ctx context.Context, id string) (*model.Provider, error)
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
