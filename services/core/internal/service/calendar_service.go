package service

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"
	"gorm.io/datatypes"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	calendarpb "github.com/Leganyst/appointment-platform/internal/api/calendar/v1"
	commonpb "github.com/Leganyst/appointment-platform/internal/api/common/v1"
	"github.com/Leganyst/appointment-platform/internal/model"
	"github.com/Leganyst/appointment-platform/internal/repository"
)

type CalendarService struct {
	calendarpb.UnimplementedCalendarServiceServer

	db           *gorm.DB
	slotRepo     repository.SlotRepository
	bookingRepo  repository.BookingRepository
	scheduleRepo repository.ScheduleRepository
	providerRepo repository.ProviderRepository
	serviceRepo  repository.ServiceRepository
	userRepo     repository.UserRepository
}

func NewCalendarService(
	db *gorm.DB,
	slotRepo repository.SlotRepository,
	bookingRepo repository.BookingRepository,
	scheduleRepo repository.ScheduleRepository,
	providerRepo repository.ProviderRepository,
	serviceRepo repository.ServiceRepository,
	userRepo repository.UserRepository,
) *CalendarService {
	return &CalendarService{
		db:           db,
		slotRepo:     slotRepo,
		bookingRepo:  bookingRepo,
		scheduleRepo: scheduleRepo,
		providerRepo: providerRepo,
		serviceRepo:  serviceRepo,
		userRepo:     userRepo,
	}
}

// ListFreeSlots — реализация RPC из сгенерённого интерфейса.
func (s *CalendarService) ListFreeSlots(
	ctx context.Context,
	req *calendarpb.ListFreeSlotsRequest,
) (*calendarpb.ListFreeSlotsResponse, error) {
	if req.GetProviderId() == "" {
		return nil, status.Error(codes.InvalidArgument, "provider_id is required")
	}

	from := req.GetStart().AsTime()
	to := req.GetEnd().AsTime()
	if !to.After(from) {
		return nil, status.Error(codes.InvalidArgument, "end must be after start")
	}

	page := req.GetPage()
	if page <= 0 {
		page = 1
	}
	size := req.GetPageSize()
	if size <= 0 {
		size = 20
	}
	offset := (int(page) - 1) * int(size)

	slots, total, err := s.slotRepo.ListFreeSlots(
		ctx,
		req.GetProviderId(),
		req.GetServiceId(),
		from,
		to,
		int(size),
		offset,
	)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list slots: %v", err)
	}

	resp := &calendarpb.ListFreeSlotsResponse{
		Slots:      make([]*commonpb.Slot, 0, len(slots)),
		TotalCount: int32(total),
	}

	for _, slot := range slots {
		resp.Slots = append(resp.Slots, &commonpb.Slot{
			Id:         slot.ID.String(),
			ProviderId: slot.ProviderID.String(),
			ServiceId:  slot.ServiceID.String(),
			StartsAt:   timestamppb.New(slot.StartsAt),
			EndsAt:     timestamppb.New(slot.EndsAt),
			Status:     mapSlotStatus(slot.Status),
		})
	}

	return resp, nil
}

// CreateBooking — создание бронирования на слот.
func (s *CalendarService) CreateBooking(
	ctx context.Context,
	req *calendarpb.CreateBookingRequest,
) (*calendarpb.CreateBookingResponse, error) {
	if req.GetSlotId() == "" || req.GetClientId() == "" {
		return nil, status.Error(codes.InvalidArgument, "slot_id and client_id are required")
	}

	clientID, err := uuid.Parse(req.GetClientId())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid client_id")
	}

	if err := s.ensureClientRole(ctx, clientID); err != nil {
		return nil, err
	}

	var resp *calendarpb.CreateBookingResponse
	err = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var slot model.TimeSlot
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&slot, "id = ?", req.GetSlotId()).Error; err != nil {
			return status.Errorf(codes.NotFound, "slot not found: %v", err)
		}
		if slot.Status != model.TimeSlotStatusPlanned {
			return status.Error(codes.FailedPrecondition, "slot is not free")
		}

		booking := &model.Booking{
			ClientID: clientID,
			SlotID:   slot.ID,
			Status:   model.BookingStatusConfirmed,
			Comment:  req.GetComment(),
		}

		if err := tx.Create(booking).Error; err != nil {
			return status.Errorf(codes.Internal, "create booking: %v", err)
		}

		if err := tx.Model(&model.TimeSlot{}).
			Where("id = ?", slot.ID).
			Update("status", model.TimeSlotStatusBooked).Error; err != nil {
			return status.Errorf(codes.Internal, "mark slot booked: %v", err)
		}

		resp = &calendarpb.CreateBookingResponse{Booking: s.mapBooking(ctx, booking)}
		return nil
	})
	if err != nil {
		return nil, err
	}

	return resp, nil
}

// GetBooking — получить бронирование.
func (s *CalendarService) GetBooking(
	ctx context.Context,
	req *calendarpb.GetBookingRequest,
) (*calendarpb.GetBookingResponse, error) {
	if req.GetBookingId() == "" {
		return nil, status.Error(codes.InvalidArgument, "booking_id is required")
	}

	booking, err := s.bookingRepo.GetByID(ctx, req.GetBookingId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "booking not found: %v", err)
	}

	return &calendarpb.GetBookingResponse{Booking: s.mapBooking(ctx, booking)}, nil
}

// CancelBooking — отменить бронирование и освободить слот.
func (s *CalendarService) CancelBooking(
	ctx context.Context,
	req *calendarpb.CancelBookingRequest,
) (*calendarpb.CancelBookingResponse, error) {
	if req.GetBookingId() == "" {
		return nil, status.Error(codes.InvalidArgument, "booking_id is required")
	}

	booking, err := s.bookingRepo.GetByID(ctx, req.GetBookingId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "booking not found: %v", err)
	}

	if booking.Status == model.BookingStatusCancelled {
		return &calendarpb.CancelBookingResponse{Booking: mapBooking(booking)}, nil
	}

	var resp *calendarpb.CancelBookingResponse
	err = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		now := time.Now().UTC()
		if err := tx.Model(&model.Booking{}).
			Where("id = ?", req.GetBookingId()).
			Updates(map[string]any{"status": model.BookingStatusCancelled, "cancelled_at": now}).Error; err != nil {
			return status.Errorf(codes.Internal, "cancel booking: %v", err)
		}

		if err := tx.Model(&model.TimeSlot{}).
			Where("id = ?", booking.SlotID).
			Update("status", model.TimeSlotStatusPlanned).Error; err != nil {
			return status.Errorf(codes.Internal, "free slot: %v", err)
		}

		booking.Status = model.BookingStatusCancelled
		booking.CancelledAt = &now
		resp = &calendarpb.CancelBookingResponse{Booking: s.mapBooking(ctx, booking)}
		return nil
	})
	if err != nil {
		return nil, err
	}

	return resp, nil
}

// ListBookings — список бронирований клиента.
func (s *CalendarService) ListBookings(
	ctx context.Context,
	req *calendarpb.ListBookingsRequest,
) (*calendarpb.ListBookingsResponse, error) {
	if req.GetClientId() == "" {
		return nil, status.Error(codes.InvalidArgument, "client_id is required")
	}

	page := req.GetPage()
	if page <= 0 {
		page = 1
	}
	size := req.GetPageSize()
	if size <= 0 {
		size = 20
	}
	offset := (int(page) - 1) * int(size)

	from := req.GetFrom().AsTime()
	to := req.GetTo().AsTime()
	if !to.IsZero() && !from.IsZero() && !to.After(from) {
		return nil, status.Error(codes.InvalidArgument, "to must be after from")
	}

	bookings, total, err := s.bookingRepo.ListByClientAndRange(ctx, req.GetClientId(), from, to, int(size), offset)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list bookings: %v", err)
	}

	resp := &calendarpb.ListBookingsResponse{
		Bookings:   make([]*commonpb.Booking, 0, len(bookings)),
		TotalCount: int32(total),
	}

	for i := range bookings {
		resp.Bookings = append(resp.Bookings, s.mapBooking(ctx, &bookings[i]))
	}

	return resp, nil
}

// ListProviderSchedules — вернуть расписания провайдера.
func (s *CalendarService) ListProviderSchedules(
	ctx context.Context,
	req *calendarpb.ListProviderSchedulesRequest,
) (*calendarpb.ListProviderSchedulesResponse, error) {
	if req.GetProviderId() == "" {
		return nil, status.Error(codes.InvalidArgument, "provider_id is required")
	}

	schedules, err := s.scheduleRepo.ListByProvider(ctx, req.GetProviderId())
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list schedules: %v", err)
	}

	resp := &calendarpb.ListProviderSchedulesResponse{
		Schedules: make([]*commonpb.ProviderSchedule, 0, len(schedules)),
	}
	for i := range schedules {
		resp.Schedules = append(resp.Schedules, mapProviderSchedule(&schedules[i]))
	}

	return resp, nil
}

// CreateProviderSchedule — создать расписание провайдера.
func (s *CalendarService) CreateProviderSchedule(
	ctx context.Context,
	req *calendarpb.CreateProviderScheduleRequest,
) (*calendarpb.CreateProviderScheduleResponse, error) {
	if req.GetProviderId() == "" {
		return nil, status.Error(codes.InvalidArgument, "provider_id is required")
	}
	if err := s.ensureProviderRole(ctx, req.GetProviderId()); err != nil {
		return nil, err
	}

	// rule/timezone/dates берём из ProviderSchedule
	ps := req.GetSchedule()
	if ps == nil {
		return nil, status.Error(codes.InvalidArgument, "schedule is required")
	}

	sched := model.Schedule{}
	if id, err := uuid.Parse(req.GetProviderId()); err == nil {
		sched.ProviderID = id
	}
	sched.TimeZone = ps.GetTimeZone()
	sched.StartDate = protoDateToDate(ps.GetStartDate())
	sched.EndDate = protoDateToDate(ps.GetEndDate())
	ruleJSON, err := encodeScheduleRule(ps.GetRule())
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "invalid rule: %v", err)
	}
	sched.Rules = ruleJSON

	if err := s.scheduleRepo.Create(ctx, &sched); err != nil {
		return nil, status.Errorf(codes.Internal, "create schedule: %v", err)
	}

	return &calendarpb.CreateProviderScheduleResponse{Schedule: mapProviderSchedule(&sched)}, nil
}

// UpdateProviderSchedule — обновить расписание.
func (s *CalendarService) UpdateProviderSchedule(
	ctx context.Context,
	req *calendarpb.UpdateProviderScheduleRequest,
) (*calendarpb.UpdateProviderScheduleResponse, error) {
	if req.GetScheduleId() == "" {
		return nil, status.Error(codes.InvalidArgument, "schedule_id is required")
	}

	ps := req.GetSchedule()
	if ps == nil {
		return nil, status.Error(codes.InvalidArgument, "schedule is required")
	}

	// обновляем timezone / rule при необходимости
	schedID, err := uuid.Parse(req.GetScheduleId())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid schedule_id")
	}

	existing, err := s.scheduleRepo.GetByID(ctx, req.GetScheduleId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "schedule not found: %v", err)
	}

	targetProviderID := existing.ProviderID.String()
	if ps.GetProviderId() != "" {
		targetProviderID = ps.GetProviderId()
	}

	if err := s.ensureProviderRole(ctx, targetProviderID); err != nil {
		return nil, err
	}

	// запрещаем менять владельца расписания
	if ps.GetProviderId() != "" && ps.GetProviderId() != existing.ProviderID.String() {
		return nil, status.Error(codes.PermissionDenied, "schedule owner cannot be changed")
	}

	ruleJSON, err := encodeScheduleRule(ps.GetRule())
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "invalid rule: %v", err)
	}

	sched := model.Schedule{
		ID:        schedID,
		TimeZone:  ps.GetTimeZone(),
		StartDate: protoDateToDate(ps.GetStartDate()),
		EndDate:   protoDateToDate(ps.GetEndDate()),
		Rules:     ruleJSON,
	}

	if ps.GetProviderId() != "" {
		if pid, err := uuid.Parse(ps.GetProviderId()); err == nil {
			sched.ProviderID = pid
		}
	}

	if err := s.scheduleRepo.Update(ctx, &sched); err != nil {
		return nil, status.Errorf(codes.Internal, "update schedule: %v", err)
	}

	return &calendarpb.UpdateProviderScheduleResponse{Schedule: mapProviderSchedule(&sched)}, nil
}

// DeleteProviderSchedule — удалить расписание.
func (s *CalendarService) DeleteProviderSchedule(
	ctx context.Context,
	req *calendarpb.DeleteProviderScheduleRequest,
) (*calendarpb.DeleteProviderScheduleResponse, error) {
	if req.GetScheduleId() == "" {
		return nil, status.Error(codes.InvalidArgument, "schedule_id is required")
	}
	sched, err := s.scheduleRepo.GetByID(ctx, req.GetScheduleId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "schedule not found: %v", err)
	}
	if err := s.ensureProviderRole(ctx, sched.ProviderID.String()); err != nil {
		return nil, err
	}
	if err := s.scheduleRepo.Delete(ctx, req.GetScheduleId()); err != nil {
		return nil, status.Errorf(codes.Internal, "delete schedule: %v", err)
	}
	return &calendarpb.DeleteProviderScheduleResponse{}, nil
}

// CreateSlot — добавить слот провайдера.
func (s *CalendarService) CreateSlot(
	ctx context.Context,
	req *calendarpb.CreateSlotRequest,
) (*calendarpb.CreateSlotResponse, error) {
	if req.GetProviderId() == "" {
		return nil, status.Error(codes.InvalidArgument, "provider_id is required")
	}
	if err := s.ensureProviderRole(ctx, req.GetProviderId()); err != nil {
		return nil, err
	}

	providerID, err := uuid.Parse(req.GetProviderId())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid provider_id")
	}

	var serviceID *uuid.UUID
	if req.GetServiceId() != "" {
		id, err := uuid.Parse(req.GetServiceId())
		if err != nil {
			return nil, status.Error(codes.InvalidArgument, "invalid service_id")
		}
		serviceID = &id
	}

	if req.GetRange() == nil || req.GetRange().GetStart() == nil || req.GetRange().GetEnd() == nil {
		return nil, status.Error(codes.InvalidArgument, "range is required")
	}
	start := req.GetRange().GetStart().AsTime()
	end := req.GetRange().GetEnd().AsTime()
	if !end.After(start) {
		return nil, status.Error(codes.InvalidArgument, "end must be after start")
	}

	slot := model.TimeSlot{
		ProviderID: providerID,
		ServiceID:  serviceID,
		StartsAt:   start,
		EndsAt:     end,
		Status:     model.TimeSlotStatusPlanned,
	}

	if err := s.slotRepo.Create(ctx, &slot); err != nil {
		return nil, status.Errorf(codes.Internal, "create slot: %v", err)
	}

	return &calendarpb.CreateSlotResponse{Slot: mapSlot(&slot)}, nil
}

// UpdateSlot — обновить слот.
func (s *CalendarService) UpdateSlot(
	ctx context.Context,
	req *calendarpb.UpdateSlotRequest,
) (*calendarpb.UpdateSlotResponse, error) {
	if req.GetSlotId() == "" {
		return nil, status.Error(codes.InvalidArgument, "slot_id is required")
	}

	slot, err := s.slotRepo.GetByID(ctx, req.GetSlotId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "slot not found: %v", err)
	}
	if err := s.ensureProviderRole(ctx, slot.ProviderID.String()); err != nil {
		return nil, err
	}

	if req.GetServiceId() != "" {
		id, err := uuid.Parse(req.GetServiceId())
		if err != nil {
			return nil, status.Error(codes.InvalidArgument, "invalid service_id")
		}
		slot.ServiceID = &id
	}

	if req.GetRange() != nil && req.GetRange().GetStart() != nil && req.GetRange().GetEnd() != nil {
		start := req.GetRange().GetStart().AsTime()
		end := req.GetRange().GetEnd().AsTime()
		if !end.After(start) {
			return nil, status.Error(codes.InvalidArgument, "end must be after start")
		}
		slot.StartsAt = start
		slot.EndsAt = end
	}

	if req.GetStatus() != commonpb.SlotStatus_SLOT_STATUS_UNSPECIFIED {
		slot.Status = mapSlotStatusBack(req.GetStatus())
	}

	if err := s.slotRepo.Update(ctx, slot); err != nil {
		return nil, status.Errorf(codes.Internal, "update slot: %v", err)
	}

	return &calendarpb.UpdateSlotResponse{Slot: mapSlot(slot)}, nil
}

// DeleteSlot — удалить слот.
func (s *CalendarService) DeleteSlot(
	ctx context.Context,
	req *calendarpb.DeleteSlotRequest,
) (*calendarpb.DeleteSlotResponse, error) {
	if req.GetSlotId() == "" {
		return nil, status.Error(codes.InvalidArgument, "slot_id is required")
	}
	slot, err := s.slotRepo.GetByID(ctx, req.GetSlotId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "slot not found: %v", err)
	}
	if err := s.ensureProviderRole(ctx, slot.ProviderID.String()); err != nil {
		return nil, err
	}

	if err := s.slotRepo.Delete(ctx, req.GetSlotId()); err != nil {
		return nil, status.Errorf(codes.Internal, "delete slot: %v", err)
	}
	return &calendarpb.DeleteSlotResponse{}, nil
}

func mapSlotStatus(s model.TimeSlotStatus) commonpb.SlotStatus {
	switch s {
	case model.TimeSlotStatusPlanned:
		return commonpb.SlotStatus_SLOT_STATUS_FREE
	case model.TimeSlotStatusBooked:
		return commonpb.SlotStatus_SLOT_STATUS_BOOKED
	case model.TimeSlotStatusCancelled:
		return commonpb.SlotStatus_SLOT_STATUS_CANCELLED
	default:
		return commonpb.SlotStatus_SLOT_STATUS_UNSPECIFIED
	}
}

func mapSlotStatusBack(s commonpb.SlotStatus) model.TimeSlotStatus {
	switch s {
	case commonpb.SlotStatus_SLOT_STATUS_FREE:
		return model.TimeSlotStatusPlanned
	case commonpb.SlotStatus_SLOT_STATUS_BOOKED:
		return model.TimeSlotStatusBooked
	case commonpb.SlotStatus_SLOT_STATUS_CANCELLED:
		return model.TimeSlotStatusCancelled
	default:
		return model.TimeSlotStatusPlanned
	}
}

func (s *CalendarService) mapBooking(ctx context.Context, b *model.Booking) *commonpb.Booking {
	if b == nil {
		return nil
	}

	var providerID, providerName, serviceID, serviceName string

	// Извлекаем слот (может быть предзагружен)
	slot := b.Slot
	if slot == nil {
		if fetched, err := s.slotRepo.GetByID(ctx, b.SlotID.String()); err == nil {
			slot = fetched
		}
	}
	if slot != nil {
		providerID = slot.ProviderID.String()
		if slot.ServiceID != nil {
			serviceID = slot.ServiceID.String()
		}
	}

	// Обогащаем именами провайдера/услуги, если есть ID.
	if providerID != "" {
		if p, err := s.providerRepo.GetByID(ctx, providerID); err == nil {
			providerName = p.DisplayName
		}
	}
	if serviceID != "" {
		if sv, err := s.serviceRepo.GetByID(ctx, serviceID); err == nil {
			serviceName = sv.Name
		}
	}

	var cancelledAt *timestamppb.Timestamp
	if b.CancelledAt != nil {
		cancelledAt = timestamppb.New(*b.CancelledAt)
	}

	return &commonpb.Booking{
		Id:           b.ID.String(),
		ClientId:     b.ClientID.String(),
		SlotId:       b.SlotID.String(),
		ProviderId:   providerID,
		ProviderName: providerName,
		ServiceId:    serviceID,
		ServiceName:  serviceName,
		Status:       mapBookingStatus(b.Status),
		CreatedAt:    timestamppb.New(b.CreatedAt),
		CancelledAt:  cancelledAt,
		Comment:      b.Comment,
	}
}

func mapSlot(slot *model.TimeSlot) *commonpb.Slot {
	if slot == nil {
		return nil
	}

	serviceID := ""
	if slot.ServiceID != nil {
		serviceID = slot.ServiceID.String()
	}

	return &commonpb.Slot{
		Id:         slot.ID.String(),
		ProviderId: slot.ProviderID.String(),
		ServiceId:  serviceID,
		StartsAt:   timestamppb.New(slot.StartsAt),
		EndsAt:     timestamppb.New(slot.EndsAt),
		Status:     mapSlotStatus(slot.Status),
	}
}

func mapBookingStatus(s model.BookingStatus) commonpb.BookingStatus {
	switch s {
	case model.BookingStatusPending:
		return commonpb.BookingStatus_BOOKING_STATUS_PENDING
	case model.BookingStatusConfirmed:
		return commonpb.BookingStatus_BOOKING_STATUS_CONFIRMED
	case model.BookingStatusCancelled:
		return commonpb.BookingStatus_BOOKING_STATUS_CANCELLED
	default:
		return commonpb.BookingStatus_BOOKING_STATUS_UNSPECIFIED
	}
}

func mapBooking(b *model.Booking) *commonpb.Booking {
	if b == nil {
		return nil
	}

	var cancelledAt *timestamppb.Timestamp
	if b.CancelledAt != nil {
		cancelledAt = timestamppb.New(*b.CancelledAt)
	}

	return &commonpb.Booking{
		Id:          b.ID.String(),
		ClientId:    b.ClientID.String(),
		SlotId:      b.SlotID.String(),
		Status:      mapBookingStatus(b.Status),
		CreatedAt:   timestamppb.New(b.CreatedAt),
		CancelledAt: cancelledAt,
		Comment:     b.Comment,
	}
}

func mapProviderSchedule(s *model.Schedule) *commonpb.ProviderSchedule {
	if s == nil {
		return nil
	}

	return &commonpb.ProviderSchedule{
		Id:         s.ID.String(),
		ProviderId: s.ProviderID.String(),
		TimeZone:   s.TimeZone,
		StartDate:  dateToProto(s.StartDate),
		EndDate:    dateToProto(s.EndDate),
		Rule:       decodeScheduleRule(s.Rules),
	}
}

// scheduleRuleDTO — внутренняя форма правила для хранения в JSON.
type scheduleRuleDTO struct {
	Frequency   commonpb.RecurrenceFrequency `json:"frequency"`
	Interval    int32                        `json:"interval"`
	Weekdays    []int32                      `json:"weekdays,omitempty"`
	StartsAt    *time.Time                   `json:"starts_at,omitempty"`
	DurationMin int32                        `json:"duration_min,omitempty"`
	Until       *time.Time                   `json:"until,omitempty"`
	Count       int32                        `json:"count,omitempty"`
}

func encodeScheduleRule(rule *commonpb.ScheduleRule) (datatypes.JSON, error) {
	if rule == nil {
		return nil, nil
	}

	dto := scheduleRuleDTO{
		Frequency:   rule.GetFrequency(),
		Interval:    rule.GetInterval(),
		Weekdays:    rule.GetWeekdays(),
		DurationMin: rule.GetDurationMin(),
		Count:       rule.GetCount(),
	}

	if rule.GetStartsAt() != nil {
		t := rule.GetStartsAt().AsTime()
		dto.StartsAt = &t
	}
	if rule.GetUntil() != nil {
		t := rule.GetUntil().AsTime()
		dto.Until = &t
	}

	data, err := json.Marshal(dto)
	if err != nil {
		return nil, err
	}
	return datatypes.JSON(data), nil
}

func decodeScheduleRule(raw datatypes.JSON) *commonpb.ScheduleRule {
	if len(raw) == 0 {
		return nil
	}
	var dto scheduleRuleDTO
	if err := json.Unmarshal(raw, &dto); err != nil {
		return nil
	}

	var startsAt, until *timestamppb.Timestamp
	if dto.StartsAt != nil {
		startsAt = timestamppb.New(*dto.StartsAt)
	}
	if dto.Until != nil {
		until = timestamppb.New(*dto.Until)
	}

	return &commonpb.ScheduleRule{
		Frequency:   dto.Frequency,
		Interval:    dto.Interval,
		Weekdays:    dto.Weekdays,
		StartsAt:    startsAt,
		DurationMin: dto.DurationMin,
		Until:       until,
		Count:       dto.Count,
	}
}

func protoDateToDate(ts *timestamppb.Timestamp) *datatypes.Date {
	if ts == nil {
		return nil
	}
	t := ts.AsTime().In(time.UTC)
	dateOnly := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
	d := datatypes.Date(dateOnly)
	return &d
}

func dateToProto(d *datatypes.Date) *timestamppb.Timestamp {
	if d == nil {
		return nil
	}
	t := time.Time(*d)
	dateOnly := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
	return timestamppb.New(dateOnly)
}

func (s *CalendarService) ensureClientRole(ctx context.Context, clientID uuid.UUID) error {
	if s.userRepo == nil || s.db == nil {
		return nil
	}
	// clientID относится к таблице clients, нужна привязка к user_id для проверки роли
	var client model.Client
	if err := s.db.WithContext(ctx).First(&client, "id = ?", clientID).Error; err != nil {
		return status.Errorf(codes.NotFound, "client not found: %v", err)
	}
	role, err := s.userRepo.GetRole(ctx, client.UserID)
	if err != nil {
		return status.Errorf(codes.PermissionDenied, "cannot verify role: %v", err)
	}
	if role != "client" {
		return status.Error(codes.PermissionDenied, "only clients can book slots")
	}
	return nil
}

func (s *CalendarService) ensureProviderRole(ctx context.Context, providerID string) error {
	if s.providerRepo == nil || s.userRepo == nil {
		return nil
	}
	provider, err := s.providerRepo.GetByID(ctx, providerID)
	if err != nil {
		return status.Errorf(codes.NotFound, "provider not found: %v", err)
	}
	role, err := s.userRepo.GetRole(ctx, provider.UserID)
	if err != nil {
		return status.Errorf(codes.PermissionDenied, "cannot verify provider role: %v", err)
	}
	if role != "provider" {
		return status.Error(codes.PermissionDenied, "only providers can manage schedules and slots")
	}
	return nil
}
