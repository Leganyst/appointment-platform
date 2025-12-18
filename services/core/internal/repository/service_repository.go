package repository

import (
	"context"

	"github.com/google/uuid"
	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type ServiceRepository interface {
	GetByID(ctx context.Context, id string) (*model.Service, error)
	Create(ctx context.Context, service *model.Service) error
	List(ctx context.Context, onlyActive bool, limit, offset int) ([]model.Service, int64, error)
	ListByProvider(ctx context.Context, providerID uuid.UUID) ([]model.Service, error)
	ListByIDs(ctx context.Context, ids []uuid.UUID) ([]model.Service, error)
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

func (r *GormServiceRepository) Create(ctx context.Context, service *model.Service) error {
	return r.db.WithContext(ctx).Create(service).Error
}

func (r *GormServiceRepository) List(ctx context.Context, onlyActive bool, limit, offset int) ([]model.Service, int64, error) {
	q := r.db.WithContext(ctx).Model(&model.Service{})
	if onlyActive {
		q = q.Where("is_active = ?", true)
	}

	var total int64
	if err := q.Count(&total).Error; err != nil {
		return nil, 0, err
	}

	if limit <= 0 {
		limit = 50
	}
	if offset < 0 {
		offset = 0
	}

	var services []model.Service
	if err := q.Order("name ASC").Limit(limit).Offset(offset).Find(&services).Error; err != nil {
		return nil, 0, err
	}
	return services, total, nil
}

func (r *GormServiceRepository) ListByProvider(ctx context.Context, providerID uuid.UUID) ([]model.Service, error) {
	var services []model.Service
	err := r.db.WithContext(ctx).
		Table("services").
		Select("services.*").
		Joins("JOIN provider_services ON provider_services.service_id = services.id").
		Where("provider_services.provider_id = ?", providerID).
		Order("services.name ASC").
		Scan(&services).Error
	if err != nil {
		return nil, err
	}
	return services, nil
}

func (r *GormServiceRepository) ListByIDs(ctx context.Context, ids []uuid.UUID) ([]model.Service, error) {
	if len(ids) == 0 {
		return []model.Service{}, nil
	}
	var services []model.Service
	err := r.db.WithContext(ctx).
		Where("id IN ?", ids).
		Find(&services).Error
	if err != nil {
		return nil, err
	}
	return services, nil
}
