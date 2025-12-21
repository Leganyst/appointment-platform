package service

import (
	"context"
	"errors"
	"log"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"gorm.io/gorm"

	commonpb "github.com/Leganyst/appointment-platform/internal/api/common/v1"
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
	logger       *log.Logger
}

func NewIdentityService(userRepo repository.UserRepository, clientRepo repository.ClientRepository, providerRepo repository.ProviderRepository) *IdentityService {
	return &IdentityService{userRepo: userRepo, clientRepo: clientRepo, providerRepo: providerRepo, logger: log.Default()}
}

func (s *IdentityService) logInfo(method string, fields ...any) {
	if s == nil || s.logger == nil {
		return
	}
	if len(fields) > 0 {
		s.logger.Printf("[IDENTITY][INFO] %s | %v", method, fields)
		return
	}
	s.logger.Printf("[IDENTITY][INFO] %s", method)
}

func (s *IdentityService) logErr(method string, err error, fields ...any) {
	if s == nil || s.logger == nil || err == nil {
		return
	}
	if len(fields) > 0 {
		s.logger.Printf("[IDENTITY][ERROR] %s: %v | %v", method, err, fields)
		return
	}
	s.logger.Printf("[IDENTITY][ERROR] %s: %v", method, err)
}

// RegisterUser создаёт пользователя по Telegram ID или возвращает существующего, обновляя контактные данные.
func (s *IdentityService) RegisterUser(ctx context.Context, req *identitypb.RegisterUserRequest) (*identitypb.RegisterUserResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}

	s.logInfo("RegisterUser", "telegram_id", req.GetTelegramId(), "display_name", req.GetDisplayName(), "username", req.GetUsername(), "contact_phone", req.GetContactPhone())

	u, err := s.userRepo.UpsertUser(ctx, req.GetTelegramId(), req.GetDisplayName(), req.GetUsername(), req.GetContactPhone())
	if err != nil {
		s.logErr("RegisterUser", err, "stage", "upsert user", "telegram_id", req.GetTelegramId())
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

	resp := &identitypb.RegisterUserResponse{User: mapUser(u, roleCode, clientID, providerID)}
	s.logInfo("RegisterUser", "telegram_id", req.GetTelegramId(), "role", roleCode, "client_id", clientID, "provider_id", providerID)
	return resp, nil
}

// UpdateContacts обновляет отображаемое имя, username и телефон.
func (s *IdentityService) UpdateContacts(ctx context.Context, req *identitypb.UpdateContactsRequest) (*identitypb.UpdateContactsResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}

	s.logInfo("UpdateContacts", "telegram_id", req.GetTelegramId(), "display_name", req.GetDisplayName(), "username", req.GetUsername(), "contact_phone", req.GetContactPhone())

	u, err := s.userRepo.UpdateContacts(ctx, req.GetTelegramId(), req.GetDisplayName(), req.GetUsername(), req.GetContactPhone())
	if err != nil {
		s.logErr("UpdateContacts", err, "stage", "update contacts", "telegram_id", req.GetTelegramId())
		return nil, status.Errorf(codes.Internal, "update contacts: %v", err)
	}
	roleCode, _ := s.userRepo.GetRole(ctx, u.ID)
	clientID, providerID := s.lookupActorIDs(ctx, u)

	resp := &identitypb.UpdateContactsResponse{User: mapUser(u, roleCode, clientID, providerID)}
	s.logInfo("UpdateContacts", "telegram_id", req.GetTelegramId(), "role", roleCode, "client_id", clientID, "provider_id", providerID)
	return resp, nil
}

// SetRole назначает роль пользователю по Telegram ID.
func (s *IdentityService) SetRole(ctx context.Context, req *identitypb.SetRoleRequest) (*identitypb.SetRoleResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}
	if req.GetRoleCode() == "" {
		return nil, status.Error(codes.InvalidArgument, "role_code is required")
	}

	s.logInfo("SetRole", "telegram_id", req.GetTelegramId(), "role", req.GetRoleCode())

	u, err := s.userRepo.FindByTelegramID(ctx, req.GetTelegramId())
	if err != nil {
		s.logErr("SetRole", err, "stage", "find user", "telegram_id", req.GetTelegramId())
		return nil, status.Errorf(codes.NotFound, "user not found: %v", err)
	}

	if err := s.userRepo.SetRole(ctx, u.ID, req.GetRoleCode()); err != nil {
		s.logErr("SetRole", err, "stage", "set role", "user_id", u.ID.String(), "role", req.GetRoleCode())
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
				s.logErr("SetRole", err, "stage", "ensure provider", "user_id", u.ID.String())
				return nil, status.Errorf(codes.Internal, "ensure provider: %v", err)
			}
			clientID, providerID := s.lookupActorIDs(ctx, u)
			s.logInfo("SetRole", "stage", "provider ensured", "user_id", u.ID.String(), "client_id", clientID, "provider_id", providerID)
		}
	}

	clientID, providerID := s.lookupActorIDs(ctx, u)
	resp := &identitypb.SetRoleResponse{User: mapUser(u, roleCode, clientID, providerID)}
	s.logInfo("SetRole", "telegram_id", req.GetTelegramId(), "role", roleCode, "client_id", clientID, "provider_id", providerID)
	return resp, nil
}

// GetProfile возвращает профиль пользователя по Telegram ID.
func (s *IdentityService) GetProfile(ctx context.Context, req *identitypb.GetProfileRequest) (*identitypb.GetProfileResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}

	s.logInfo("GetProfile", "telegram_id", req.GetTelegramId())

	u, err := s.userRepo.FindByTelegramID(ctx, req.GetTelegramId())
	if err != nil {
		s.logErr("GetProfile", err, "stage", "find user", "telegram_id", req.GetTelegramId())
		return nil, status.Errorf(codes.NotFound, "user not found: %v", err)
	}
	roleCode, _ := s.userRepo.GetRole(ctx, u.ID)
	clientID, providerID := s.lookupActorIDs(ctx, u)

	resp := &identitypb.GetProfileResponse{User: mapUser(u, roleCode, clientID, providerID)}
	s.logInfo("GetProfile", "telegram_id", req.GetTelegramId(), "role", roleCode, "client_id", clientID, "provider_id", providerID)
	return resp, nil
}

// FindProviderByPhone ищет провайдера по номеру телефона (визитке).
func (s *IdentityService) FindProviderByPhone(ctx context.Context, req *identitypb.FindProviderByPhoneRequest) (*identitypb.FindProviderByPhoneResponse, error) {
	contact := strings.TrimSpace(req.GetPhone())
	if contact == "" {
		return nil, status.Error(codes.InvalidArgument, "phone is required")
	}
	if s.userRepo == nil {
		return nil, status.Error(codes.Internal, "user repo is not configured")
	}
	s.logInfo("FindProviderByPhone", "phone", contact)

	// Treat non-phone input as username (allows searching by @username provided by provider).
	hasDigit := false
	for i := 0; i < len(contact); i++ {
		c := contact[i]
		if c >= '0' && c <= '9' {
			hasDigit = true
			break
		}
	}
	var u *model.User
	var err error
	if hasDigit {
		u, err = s.userRepo.FindByPhone(ctx, contact)
	} else {
		u, err = s.userRepo.FindByUsername(ctx, contact)
	}
	if err != nil {
		s.logErr("FindProviderByPhone", err, "stage", "find user", "phone", contact)
		return nil, status.Errorf(codes.NotFound, "provider not found: %v", err)
	}

	roleCode, err := s.userRepo.GetRole(ctx, u.ID)
	if err != nil {
		s.logErr("FindProviderByPhone", err, "stage", "get role", "user_id", u.ID.String())
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
			s.logErr("FindProviderByPhone", err, "stage", "ensure provider", "user_id", u.ID.String())
			return nil, status.Errorf(codes.Internal, "ensure provider: %v", err)
		}
	}
	// Клиентская сущность тоже полезна (например, чтобы провайдер мог сам записываться).
	if s.clientRepo != nil {
		if _, err := s.clientRepo.EnsureByUserID(ctx, u.ID); err != nil {
			s.logErr("FindProviderByPhone", err, "stage", "ensure client", "user_id", u.ID.String())
			return nil, status.Errorf(codes.Internal, "ensure client: %v", err)
		}
	}

	clientID, providerID := s.lookupActorIDs(ctx, u)
	if providerID == "" {
		return nil, status.Error(codes.NotFound, "provider not found")
	}

	resp := &identitypb.FindProviderByPhoneResponse{User: mapUser(u, roleCode, clientID, providerID)}
	s.logInfo("FindProviderByPhone", "phone", contact, "user_id", u.ID.String(), "client_id", clientID, "provider_id", providerID)
	return resp, nil
}

// GetUserContext возвращает расширенный контекст пользователя по Telegram ID:
// user (как в GetProfile) + опционально профиль провайдера.
func (s *IdentityService) GetUserContext(ctx context.Context, req *identitypb.GetUserContextRequest) (*identitypb.GetUserContextResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}
	if s.userRepo == nil {
		return nil, status.Error(codes.Internal, "user repo is not configured")
	}

	includeProvider := req.GetIncludeProviderProfile()
	s.logInfo("GetUserContext", "telegram_id", req.GetTelegramId(), "include_provider_profile", includeProvider)

	u, err := s.userRepo.FindByTelegramID(ctx, req.GetTelegramId())
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, status.Errorf(codes.NotFound, "user not found: %v", err)
		}
		s.logErr("GetUserContext", err, "stage", "find user", "telegram_id", req.GetTelegramId())
		return nil, status.Errorf(codes.Internal, "find user: %v", err)
	}

	roleCode, _ := s.userRepo.GetRole(ctx, u.ID)
	clientID, providerID := s.lookupActorIDs(ctx, u)

	isProvider := roleCode == "provider" || providerID != ""
	var providerProfile *commonpb.Provider
	if includeProvider && isProvider && s.providerRepo != nil {
		// Ensure provider exists for provider users, so we can return a stable profile.
		if roleCode == "provider" {
			dn := strings.TrimSpace(u.DisplayName)
			if dn == "" {
				dn = strings.TrimSpace(u.Note)
			}
			if _, err := s.providerRepo.EnsureByUserID(ctx, u.ID, dn); err != nil {
				s.logErr("GetUserContext", err, "stage", "ensure provider", "user_id", u.ID.String())
				return nil, status.Errorf(codes.Internal, "ensure provider: %v", err)
			}
		}

		p, err := s.providerRepo.GetByUserID(ctx, u.ID)
		if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
			s.logErr("GetUserContext", err, "stage", "get provider", "user_id", u.ID.String())
			return nil, status.Errorf(codes.Internal, "get provider: %v", err)
		}
		if err == nil && p != nil {
			providerID = p.ID.String()
			providerProfile = &commonpb.Provider{
				Id:          p.ID.String(),
				DisplayName: p.DisplayName,
				Description: p.Description,
			}
		}
	}

	resp := &identitypb.GetUserContextResponse{
		User:     mapUser(u, roleCode, clientID, providerID),
		Provider: providerProfile,
	}
	s.logInfo("GetUserContext", "telegram_id", req.GetTelegramId(), "role", roleCode, "client_id", clientID, "provider_id", providerID, "include_provider_profile", includeProvider)
	return resp, nil
}

// ResetAccount очищает роль и контактные данные пользователя.
// Календарные сущности (client/provider) не удаляются, чтобы не ломать существующие записи.
func (s *IdentityService) ResetAccount(ctx context.Context, req *identitypb.GetProfileRequest) (*identitypb.RegisterUserResponse, error) {
	if req.GetTelegramId() <= 0 {
		return nil, status.Error(codes.InvalidArgument, "telegram_id is required")
	}
	if s.userRepo == nil {
		return nil, status.Error(codes.Internal, "user repo is not configured")
	}

	s.logInfo("ResetAccount", "telegram_id", req.GetTelegramId())

	u, err := s.userRepo.FindByTelegramID(ctx, req.GetTelegramId())
	if err != nil {
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			s.logErr("ResetAccount", err, "stage", "find user", "telegram_id", req.GetTelegramId())
			return nil, status.Errorf(codes.Internal, "find user: %v", err)
		}
		// If user doesn't exist yet, create an empty record so subsequent /start works consistently.
		u, err = s.userRepo.UpsertUser(ctx, req.GetTelegramId(), "", "", "")
		if err != nil {
			s.logErr("ResetAccount", err, "stage", "upsert user", "telegram_id", req.GetTelegramId())
			return nil, status.Errorf(codes.Internal, "reset account: %v", err)
		}
	}

	if err := s.userRepo.ClearRoles(ctx, u.ID); err != nil {
		s.logErr("ResetAccount", err, "stage", "clear roles", "user_id", u.ID.String())
		return nil, status.Errorf(codes.Internal, "clear roles: %v", err)
	}

	u, err = s.userRepo.ResetAccount(ctx, req.GetTelegramId())
	if err != nil {
		s.logErr("ResetAccount", err, "stage", "reset contacts", "telegram_id", req.GetTelegramId())
		return nil, status.Errorf(codes.Internal, "reset contacts: %v", err)
	}

	roleCode, _ := s.userRepo.GetRole(ctx, u.ID)
	clientID, providerID := s.lookupActorIDs(ctx, u)

	resp := &identitypb.RegisterUserResponse{User: mapUser(u, roleCode, clientID, providerID)}
	s.logInfo("ResetAccount", "telegram_id", req.GetTelegramId(), "role", roleCode, "client_id", clientID, "provider_id", providerID)
	return resp, nil
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
