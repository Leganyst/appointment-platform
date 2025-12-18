package repository

import (
	"context"
	"time"

	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type SlotRepository interface {
	// Свободные слоты провайдера по интервалу и услуге.
	ListFreeSlots(ctx context.Context, providerID, serviceID string, from, to time.Time, limit, offset int) ([]model.TimeSlot, int64, error)
	// Все слоты провайдера по интервалу (любые статусы).
	ListByProviderRange(ctx context.Context, providerID string, from, to time.Time, limit, offset int) ([]model.TimeSlot, int64, error)
	// Найти слот по ID.
	GetByID(ctx context.Context, id string) (*model.TimeSlot, error)
	// Обновить статус слота.
	UpdateStatus(ctx context.Context, id string, status model.TimeSlotStatus) error
	// Создать слот.
	Create(ctx context.Context, slot *model.TimeSlot) error
	// Обновить слот.
	Update(ctx context.Context, slot *model.TimeSlot) error
	// Удалить слот.
	Delete(ctx context.Context, id string) error
}

type GormSlotRepository struct {
	db *gorm.DB
}

func NewGormSlotRepository(db *gorm.DB) *GormSlotRepository {
	return &GormSlotRepository{db: db}
}

func (r *GormSlotRepository) ListFreeSlots(
	ctx context.Context,
	providerID, serviceID string,
	from, to time.Time,
	limit, offset int,
) ([]model.TimeSlot, int64, error) {
	var slots []model.TimeSlot
	q := r.db.WithContext(ctx).
		Model(&model.TimeSlot{}).
		Where("provider_id = ?", providerID).
		Where("starts_at >= ? AND ends_at <= ?", from, to).
		Where("status = ?", model.TimeSlotStatusPlanned)

	if serviceID != "" {
		q = q.Where("service_id = ?", serviceID)
	}

	var total int64
	if err := q.Count(&total).Error; err != nil {
		return nil, 0, err
	}

	if limit > 0 {
		q = q.Limit(limit).Offset(offset)
	}

	if err := q.Order("starts_at ASC").Find(&slots).Error; err != nil {
		return nil, 0, err
	}

	return slots, total, nil
}

func (r *GormSlotRepository) ListByProviderRange(
	ctx context.Context,
	providerID string,
	from, to time.Time,
	limit, offset int,
) ([]model.TimeSlot, int64, error) {
	var slots []model.TimeSlot
	q := r.db.WithContext(ctx).
		Model(&model.TimeSlot{}).
		Where("provider_id = ?", providerID).
		Where("starts_at >= ? AND ends_at <= ?", from, to)

	var total int64
	if err := q.Count(&total).Error; err != nil {
		return nil, 0, err
	}

	if limit > 0 {
		q = q.Limit(limit).Offset(offset)
	}

	if err := q.Order("starts_at ASC").Find(&slots).Error; err != nil {
		return nil, 0, err
	}

	return slots, total, nil
}

func (r *GormSlotRepository) GetByID(ctx context.Context, id string) (*model.TimeSlot, error) {
	var slot model.TimeSlot
	if err := r.db.WithContext(ctx).First(&slot, "id = ?", id).Error; err != nil {
		return nil, err
	}
	return &slot, nil
}

func (r *GormSlotRepository) UpdateStatus(ctx context.Context, id string, status model.TimeSlotStatus) error {
	return r.db.WithContext(ctx).
		Model(&model.TimeSlot{}).
		Where("id = ?", id).
		Update("status", status).
		Error
}

func (r *GormSlotRepository) Create(ctx context.Context, slot *model.TimeSlot) error {
	return r.db.WithContext(ctx).Create(slot).Error
}

func (r *GormSlotRepository) Update(ctx context.Context, slot *model.TimeSlot) error {
	return r.db.WithContext(ctx).Model(&model.TimeSlot{}).Where("id = ?", slot.ID).Updates(slot).Error
}

func (r *GormSlotRepository) Delete(ctx context.Context, id string) error {
	return r.db.WithContext(ctx).Delete(&model.TimeSlot{}, "id = ?", id).Error
}
