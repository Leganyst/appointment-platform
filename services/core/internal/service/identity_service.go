package service

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	identitypb "github.com/Leganyst/appointment-platform/internal/api/identity/v1"
	"github.com/Leganyst/appointment-platform/internal/model"
	"github.com/Leganyst/appointment-platform/internal/repository"
)

// IdentityService реализует регистрацию и управление профилем по Telegram ID.
type IdentityService struct {
	identitypb.UnimplementedIdentityServiceServer

	userRepo repository.UserRepository
}

func NewIdentityService(userRepo repository.UserRepository) *IdentityService {
	return &IdentityService{userRepo: userRepo}
}

// RegisterUser создаёт пользователя по Telegram ID или возвращает существующего, обновляя контактные данные.
func (s *IdentityService) RegisterUser(ctx context.Context, req *identitypb.RegisterUserRequest) (*identitypb.RegisterUserResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}

	u, err := s.userRepo.UpsertUser(ctx, req.GetTelegramId(), req.GetDisplayName(), req.GetUsername(), req.GetContactPhone())
	if err != nil {
		return nil, status.Errorf(codes.Internal, "register user: %v", err)
	}

	roleCode, _ := s.userRepo.GetRole(ctx, u.ID) // роль может отсутствовать; игнорируем ошибку

	return &identitypb.RegisterUserResponse{User: mapUser(u, roleCode)}, nil
}

// UpdateContacts обновляет отображаемое имя, username и телефон.
func (s *IdentityService) UpdateContacts(ctx context.Context, req *identitypb.UpdateContactsRequest) (*identitypb.UpdateContactsResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}

	u, err := s.userRepo.UpdateContacts(ctx, req.GetTelegramId(), req.GetDisplayName(), req.GetUsername(), req.GetContactPhone())
	if err != nil {
		return nil, status.Errorf(codes.Internal, "update contacts: %v", err)
	}
	roleCode, _ := s.userRepo.GetRole(ctx, u.ID)

	return &identitypb.UpdateContactsResponse{User: mapUser(u, roleCode)}, nil
}

// SetRole назначает роль пользователю по Telegram ID.
func (s *IdentityService) SetRole(ctx context.Context, req *identitypb.SetRoleRequest) (*identitypb.SetRoleResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}
	if req.GetRoleCode() == "" {
		return nil, status.Error(codes.InvalidArgument, "role_code is required")
	}

	u, err := s.userRepo.FindByTelegramID(ctx, req.GetTelegramId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "user not found: %v", err)
	}

	if err := s.userRepo.SetRole(ctx, u.ID, req.GetRoleCode()); err != nil {
		return nil, status.Errorf(codes.Internal, "set role: %v", err)
	}

	return &identitypb.SetRoleResponse{User: mapUser(u, req.GetRoleCode())}, nil
}

// GetProfile возвращает профиль пользователя по Telegram ID.
func (s *IdentityService) GetProfile(ctx context.Context, req *identitypb.GetProfileRequest) (*identitypb.GetProfileResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}

	u, err := s.userRepo.FindByTelegramID(ctx, req.GetTelegramId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "user not found: %v", err)
	}
	roleCode, _ := s.userRepo.GetRole(ctx, u.ID)

	return &identitypb.GetProfileResponse{User: mapUser(u, roleCode)}, nil
}

func mapUser(u *model.User, roleCode string) *identitypb.User {
	if u == nil {
		return nil
	}
	return &identitypb.User{
		Id:           u.ID.String(),
		TelegramId:   u.TelegramID,
		DisplayName:  u.DisplayName,
		Username:     u.Note, // username сохраняем в поле Note
		ContactPhone: u.ContactPhone,
		RoleCode:     roleCode,
	}
}
