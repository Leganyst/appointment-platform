package service

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"

	calendarpb "github.com/Leganyst/appointment-platform/internal/api/calendar/v1"
	"github.com/Leganyst/appointment-platform/internal/model"
	"github.com/Leganyst/appointment-platform/internal/repository"
)

type CalendarService struct {
	calendarpb.UnimplementedCalendarServiceServer

	slotRepo    repository.SlotRepository
	bookingRepo repository.BookingRepository
}

func NewCalendarService(
	slotRepo repository.SlotRepository,
	bookingRepo repository.BookingRepository,
) *CalendarService {
	return &CalendarService{
		slotRepo:    slotRepo,
		bookingRepo: bookingRepo,
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
		Slots:      make([]*calendarpb.Slot, 0, len(slots)),
		TotalCount: int32(total),
	}

	for _, slot := range slots {
		resp.Slots = append(resp.Slots, &calendarpb.Slot{
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

func mapSlotStatus(s model.TimeSlotStatus) calendarpb.SlotStatus {
	switch s {
	case model.TimeSlotStatusPlanned:
		return calendarpb.SlotStatus_SLOT_STATUS_FREE
	case model.TimeSlotStatusBooked:
		return calendarpb.SlotStatus_SLOT_STATUS_BOOKED
	case model.TimeSlotStatusCancelled:
		return calendarpb.SlotStatus_SLOT_STATUS_CANCELLED
	default:
		return calendarpb.SlotStatus_SLOT_STATUS_UNSPECIFIED
	}
}
