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
	Update(ctx context.Context, provider *model.Provider) error
	List(ctx context.Context, serviceID *uuid.UUID, limit, offset int) ([]model.Provider, int64, error)
	SetServices(ctx context.Context, providerID uuid.UUID, serviceIDs []uuid.UUID) error
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

func (r *GormProviderRepository) Update(ctx context.Context, provider *model.Provider) error {
	return r.db.WithContext(ctx).
		Model(&model.Provider{}).
		Where("id = ?", provider.ID).
		Updates(map[string]any{
			"display_name": provider.DisplayName,
			"description":  provider.Description,
		}).Error
}

func (r *GormProviderRepository) List(ctx context.Context, serviceID *uuid.UUID, limit, offset int) ([]model.Provider, int64, error) {
	q := r.db.WithContext(ctx).Model(&model.Provider{})
	if serviceID != nil {
		q = q.Joins("JOIN provider_services ON provider_services.provider_id = providers.id").
			Where("provider_services.service_id = ?", *serviceID)
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

	var providers []model.Provider
	if err := q.Order("display_name ASC").Limit(limit).Offset(offset).Find(&providers).Error; err != nil {
		return nil, 0, err
	}

	return providers, total, nil
}

func (r *GormProviderRepository) SetServices(ctx context.Context, providerID uuid.UUID, serviceIDs []uuid.UUID) error {
	return r.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Where("provider_id = ?", providerID).Delete(&model.ProviderService{}).Error; err != nil {
			return err
		}
		if len(serviceIDs) == 0 {
			return nil
		}
		rows := make([]model.ProviderService, 0, len(serviceIDs))
		for _, sid := range serviceIDs {
			rows = append(rows, model.ProviderService{ProviderID: providerID, ServiceID: sid})
		}
		return tx.Create(&rows).Error
	})
}
