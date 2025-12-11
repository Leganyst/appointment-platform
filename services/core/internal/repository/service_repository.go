package repository

import (
	"context"

	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type ServiceRepository interface {
	GetByID(ctx context.Context, id string) (*model.Service, error)
}

type GormServiceRepository struct {
	db *gorm.DB
}

func NewGormServiceRepository(db *gorm.DB) *GormServiceRepository {
	return &GormServiceRepository{db: db}
}

func (r *GormServiceRepository) GetByID(ctx context.Context, id string) (*model.Service, error) {
	var s model.Service
	if err := r.db.WithContext(ctx).First(&s, "id = ?", id).Error; err != nil {
		return nil, err
	}
	return &s, nil
}
