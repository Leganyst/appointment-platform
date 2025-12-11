package repository

import (
	"context"

	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type ScheduleRepository interface {
	// ListByProvider возвращает расписания провайдера.
	ListByProvider(ctx context.Context, providerID string) ([]model.Schedule, error)
	GetByID(ctx context.Context, id string) (*model.Schedule, error)
	Create(ctx context.Context, s *model.Schedule) error
	Update(ctx context.Context, s *model.Schedule) error
	Delete(ctx context.Context, id string) error
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

func (r *GormScheduleRepository) GetByID(ctx context.Context, id string) (*model.Schedule, error) {
	var s model.Schedule
	if err := r.db.WithContext(ctx).First(&s, "id = ?", id).Error; err != nil {
		return nil, err
	}
	return &s, nil
}

func (r *GormScheduleRepository) Create(ctx context.Context, s *model.Schedule) error {
	return r.db.WithContext(ctx).Create(s).Error
}

func (r *GormScheduleRepository) Update(ctx context.Context, s *model.Schedule) error {
	return r.db.WithContext(ctx).Model(&model.Schedule{}).Where("id = ?", s.ID).Updates(s).Error
}

func (r *GormScheduleRepository) Delete(ctx context.Context, id string) error {
	return r.db.WithContext(ctx).Delete(&model.Schedule{}, "id = ?", id).Error
}
