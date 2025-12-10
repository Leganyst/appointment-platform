package repository

import (
	"context"
	"time"

	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type BookingRepository interface {
	// Создать новое бронирование.
	Create(ctx context.Context, booking *model.Booking) error
	// Получить бронирование по ID.
	GetByID(ctx context.Context, id string) (*model.Booking, error)
	// Обновить статус бронирования (например, при отмене).
	UpdateStatus(ctx context.Context, id string, status model.BookingStatus, cancelledAt *time.Time) error
	// Список бронирований клиента за период с пагинацией.
	ListByClientAndRange(
		ctx context.Context,
		clientID string,
		from, to time.Time,
		limit, offset int,
	) ([]model.Booking, int64, error)
}

// Реализация на GORM.
type GormBookingRepository struct {
	db *gorm.DB
}

func NewGormBookingRepository(db *gorm.DB) *GormBookingRepository {
	return &GormBookingRepository{db: db}
}

func (r *GormBookingRepository) Create(ctx context.Context, booking *model.Booking) error {
	return r.db.WithContext(ctx).Create(booking).Error
}

func (r *GormBookingRepository) GetByID(ctx context.Context, id string) (*model.Booking, error) {
	var b model.Booking
	if err := r.db.WithContext(ctx).First(&b, "id = ?", id).Error; err != nil {
		return nil, err
	}
	return &b, nil
}

func (r *GormBookingRepository) UpdateStatus(
	ctx context.Context,
	id string,
	status model.BookingStatus,
	cancelledAt *time.Time,
) error {
	update := map[string]any{
		"status": status,
	}
	if cancelledAt != nil {
		update["cancelled_at"] = *cancelledAt
	}
	return r.db.WithContext(ctx).
		Model(&model.Booking{}).
		Where("id = ?", id).
		Updates(update).
		Error
}

func (r *GormBookingRepository) ListByClientAndRange(
	ctx context.Context,
	clientID string,
	from, to time.Time,
	limit, offset int,
) ([]model.Booking, int64, error) {
	var (
		bookings []model.Booking
		total    int64
	)

	q := r.db.WithContext(ctx).
		Model(&model.Booking{}).
		Where("client_id = ?", clientID).
		Where("created_at >= ? AND created_at <= ?", from, to)

	if err := q.Count(&total).Error; err != nil {
		return nil, 0, err
	}

	if limit > 0 {
		q = q.Limit(limit).Offset(offset)
	}

	if err := q.Order("created_at DESC").Find(&bookings).Error; err != nil {
		return nil, 0, err
	}

	return bookings, total, nil
}
