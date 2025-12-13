package service

import (
	"context"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	identitypb "github.com/Leganyst/appointment-platform/internal/api/identity/v1"
	"github.com/Leganyst/appointment-platform/internal/model"
	"github.com/Leganyst/appointment-platform/internal/repository"
)

// IdentityService реализует регистрацию и управление профилем по Telegram ID.
type IdentityService struct {
	identitypb.UnimplementedIdentityServiceServer

	userRepo     repository.UserRepository
	clientRepo   repository.ClientRepository
	providerRepo repository.ProviderRepository
}

func NewIdentityService(userRepo repository.UserRepository, clientRepo repository.ClientRepository, providerRepo repository.ProviderRepository) *IdentityService {
	return &IdentityService{userRepo: userRepo, clientRepo: clientRepo, providerRepo: providerRepo}
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

	// Любой пользователь (любая роль) может быть "клиентом" для механизма записи,
	// поэтому гарантируем наличие записи в таблице clients.
	if s.clientRepo != nil {
		if _, err := s.clientRepo.EnsureByUserID(ctx, u.ID); err != nil {
			return nil, status.Errorf(codes.Internal, "ensure client: %v", err)
		}
	}

	roleCode, _ := s.userRepo.GetRole(ctx, u.ID) // роль может отсутствовать; игнорируем ошибку
	clientID, providerID := s.lookupActorIDs(ctx, u)

	return &identitypb.RegisterUserResponse{User: mapUser(u, roleCode, clientID, providerID)}, nil
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
	clientID, providerID := s.lookupActorIDs(ctx, u)

	return &identitypb.UpdateContactsResponse{User: mapUser(u, roleCode, clientID, providerID)}, nil
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

	// Вариант A: автосоздание сущностей календарного ядра при смене роли.
	// Клиентская сущность нужна всегда, т.к. записываться может любой пользователь.
	if s.clientRepo != nil {
		if _, err := s.clientRepo.EnsureByUserID(ctx, u.ID); err != nil {
			return nil, status.Errorf(codes.Internal, "ensure client: %v", err)
		}
	}
	roleCode := strings.TrimSpace(req.GetRoleCode())
	switch roleCode {
	case "provider":
		if s.providerRepo != nil {
			dn := strings.TrimSpace(u.DisplayName)
			if dn == "" {
				dn = strings.TrimSpace(u.Note)
			}
			if _, err := s.providerRepo.EnsureByUserID(ctx, u.ID, dn); err != nil {
				return nil, status.Errorf(codes.Internal, "ensure provider: %v", err)
			}
		}
	}

	clientID, providerID := s.lookupActorIDs(ctx, u)
	return &identitypb.SetRoleResponse{User: mapUser(u, roleCode, clientID, providerID)}, nil
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
	clientID, providerID := s.lookupActorIDs(ctx, u)

	return &identitypb.GetProfileResponse{User: mapUser(u, roleCode, clientID, providerID)}, nil
}

// FindProviderByPhone ищет провайдера по номеру телефона (визитке).
func (s *IdentityService) FindProviderByPhone(ctx context.Context, req *identitypb.FindProviderByPhoneRequest) (*identitypb.FindProviderByPhoneResponse, error) {
	phone := strings.TrimSpace(req.GetPhone())
	if phone == "" {
		return nil, status.Error(codes.InvalidArgument, "phone is required")
	}
	if s.userRepo == nil {
		return nil, status.Error(codes.Internal, "user repo is not configured")
	}

	u, err := s.userRepo.FindByPhone(ctx, phone)
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "provider not found: %v", err)
	}

	roleCode, err := s.userRepo.GetRole(ctx, u.ID)
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "provider not found: %v", err)
	}
	if roleCode != "provider" {
		return nil, status.Error(codes.NotFound, "provider not found")
	}

	// Гарантируем наличие provider записи (на случай данных до варианта A).
	if s.providerRepo != nil {
		dn := strings.TrimSpace(u.DisplayName)
		if dn == "" {
			dn = strings.TrimSpace(u.Note)
		}
		if _, err := s.providerRepo.EnsureByUserID(ctx, u.ID, dn); err != nil {
			return nil, status.Errorf(codes.Internal, "ensure provider: %v", err)
		}
	}
	// Клиентская сущность тоже полезна (например, чтобы провайдер мог сам записываться).
	if s.clientRepo != nil {
		if _, err := s.clientRepo.EnsureByUserID(ctx, u.ID); err != nil {
			return nil, status.Errorf(codes.Internal, "ensure client: %v", err)
		}
	}

	clientID, providerID := s.lookupActorIDs(ctx, u)
	if providerID == "" {
		return nil, status.Error(codes.NotFound, "provider not found")
	}

	return &identitypb.FindProviderByPhoneResponse{User: mapUser(u, roleCode, clientID, providerID)}, nil
}

func (s *IdentityService) lookupActorIDs(ctx context.Context, u *model.User) (clientID string, providerID string) {
	if u == nil {
		return "", ""
	}
	if s.clientRepo != nil {
		if c, err := s.clientRepo.GetByUserID(ctx, u.ID); err == nil && c != nil {
			clientID = c.ID.String()
		}
	}
	if s.providerRepo != nil {
		if p, err := s.providerRepo.GetByUserID(ctx, u.ID); err == nil && p != nil {
			providerID = p.ID.String()
		}
	}
	return clientID, providerID
}

func mapUser(u *model.User, roleCode, clientID, providerID string) *identitypb.User {
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
		ClientId:     clientID,
		ProviderId:   providerID,
	}
}
