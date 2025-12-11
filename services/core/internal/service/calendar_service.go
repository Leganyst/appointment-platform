package service

import (
	"context"
	"time"

	"github.com/google/uuid"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"

	calendarpb "github.com/Leganyst/appointment-platform/internal/api/calendar/v1"
	commonpb "github.com/Leganyst/appointment-platform/internal/api/common/v1"
	"github.com/Leganyst/appointment-platform/internal/model"
	"github.com/Leganyst/appointment-platform/internal/repository"
)

type CalendarService struct {
	calendarpb.UnimplementedCalendarServiceServer

	slotRepo     repository.SlotRepository
	bookingRepo  repository.BookingRepository
	scheduleRepo repository.ScheduleRepository
}

func NewCalendarService(
	slotRepo repository.SlotRepository,
	bookingRepo repository.BookingRepository,
	scheduleRepo repository.ScheduleRepository,
) *CalendarService {
	return &CalendarService{
		slotRepo:     slotRepo,
		bookingRepo:  bookingRepo,
		scheduleRepo: scheduleRepo,
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

	slot, err := s.slotRepo.GetByID(ctx, req.GetSlotId())
	if err != nil {
		return nil, status.Errorf(codes.NotFound, "slot not found: %v", err)
	}
	if slot.Status != model.TimeSlotStatusPlanned {
		return nil, status.Error(codes.FailedPrecondition, "slot is not free")
	}

	clientID, err := uuid.Parse(req.GetClientId())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid client_id")
	}

	booking := &model.Booking{
		ClientID: clientID,
		SlotID:   slot.ID,
		Status:   model.BookingStatusConfirmed,
		Comment:  req.GetComment(),
	}

	if err := s.bookingRepo.Create(ctx, booking); err != nil {
		return nil, status.Errorf(codes.Internal, "create booking: %v", err)
	}
	// Помечаем слот забронированным.
	_ = s.slotRepo.UpdateStatus(ctx, req.GetSlotId(), model.TimeSlotStatusBooked)

	return &calendarpb.CreateBookingResponse{Booking: mapBooking(booking)}, nil
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

	return &calendarpb.GetBookingResponse{Booking: mapBooking(booking)}, nil
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

	now := time.Now().UTC()
	if err := s.bookingRepo.UpdateStatus(ctx, req.GetBookingId(), model.BookingStatusCancelled, &now); err != nil {
		return nil, status.Errorf(codes.Internal, "cancel booking: %v", err)
	}
	// Освобождаем слот.
	_ = s.slotRepo.UpdateStatus(ctx, booking.SlotID.String(), model.TimeSlotStatusPlanned)

	// Обновляем данные в ответе.
	booking.Status = model.BookingStatusCancelled
	booking.CancelledAt = &now

	return &calendarpb.CancelBookingResponse{Booking: mapBooking(booking)}, nil
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
		resp.Bookings = append(resp.Bookings, mapBooking(&bookings[i]))
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
		StartDate:  nil,
		EndDate:    nil,
		Rule:       nil, // rules JSON не конвертируем здесь
	}
}
