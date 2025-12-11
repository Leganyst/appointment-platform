package repository

import (
	"context"

	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type ScheduleRepository interface {
	// ListByProvider возвращает расписания провайдера.
	ListByProvider(ctx context.Context, providerID string) ([]model.Schedule, error)
}

type GormScheduleRepository struct {
	db *gorm.DB
}

func NewGormScheduleRepository(db *gorm.DB) *GormScheduleRepository {
	return &GormScheduleRepository{db: db}
}

func (r *GormScheduleRepository) ListByProvider(ctx context.Context, providerID string) ([]model.Schedule, error) {
	var schedules []model.Schedule
	err := r.db.WithContext(ctx).
		Where("provider_id = ?", providerID).
		Order("created_at DESC").
		Find(&schedules).Error
	if err != nil {
		return nil, err
	}
	return schedules, nil
}
