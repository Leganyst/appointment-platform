package repository

import (
	"context"

	"github.com/google/uuid"
	"gorm.io/gorm"

	"github.com/Leganyst/appointment-platform/internal/model"
)

type ClientRepository interface {
	GetByID(ctx context.Context, id string) (*model.Client, error)
	GetByUserID(ctx context.Context, userID uuid.UUID) (*model.Client, error)
	EnsureByUserID(ctx context.Context, userID uuid.UUID) (*model.Client, error)
}

type GormClientRepository struct {
	db *gorm.DB
}

func NewGormClientRepository(db *gorm.DB) *GormClientRepository {
	return &GormClientRepository{db: db}
}

func (r *GormClientRepository) GetByID(ctx context.Context, id string) (*model.Client, error) {
	var c model.Client
	if err := r.db.WithContext(ctx).First(&c, "id = ?", id).Error; err != nil {
		return nil, err
	}
	return &c, nil
}

func (r *GormClientRepository) GetByUserID(ctx context.Context, userID uuid.UUID) (*model.Client, error) {
	var c model.Client
	if err := r.db.WithContext(ctx).First(&c, "user_id = ?", userID).Error; err != nil {
		return nil, err
	}
	return &c, nil
}

func (r *GormClientRepository) EnsureByUserID(ctx context.Context, userID uuid.UUID) (*model.Client, error) {
	if userID == uuid.Nil {
		return nil, gorm.ErrRecordNotFound
	}
	var c model.Client
	tx := r.db.WithContext(ctx).First(&c, "user_id = ?", userID)
	if tx.Error == nil {
		return &c, nil
	}
	if tx.Error != gorm.ErrRecordNotFound {
		return nil, tx.Error
	}

	c = model.Client{UserID: userID}
	if err := r.db.WithContext(ctx).Create(&c).Error; err != nil {
		return nil, err
	}
	return &c, nil
}
