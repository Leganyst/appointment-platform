package service

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"sort"
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
	calendarutils "github.com/Leganyst/appointment-platform/internal/utils"
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

	logger *log.Logger
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
		logger:       log.Default(),
	}
}

func (s *CalendarService) logErr(method string, err error, fields ...any) {
	if s == nil || s.logger == nil || err == nil {
		return
	}
	if len(fields) > 0 {
		s.logger.Printf("[ERROR] %s: %v | %v", method, err, fields)
		return
	}
	s.logger.Printf("[ERROR] %s: %v", method, err)
}

func (s *CalendarService) logInfo(method string, fields ...any) {
	if s == nil || s.logger == nil {
		return
	}
	if len(fields) > 0 {
		s.logger.Printf("[INFO] %s | %v", method, fields)
		return
	}
	s.logger.Printf("[INFO] %s", method)
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

	// Генерируем (материализуем) слоты из расписаний провайдера в окне,
	// чтобы далее отдавать их через существующий репозиторий с пагинацией.
	if s.db != nil && s.scheduleRepo != nil {
		providerUUID, err := uuid.Parse(req.GetProviderId())
		if err != nil {
			s.logErr("ListFreeSlots", err, "provider_id", req.GetProviderId())
			return nil, status.Error(codes.InvalidArgument, "invalid provider_id")
		}
		var serviceUUID *uuid.UUID
		if req.GetServiceId() != "" {
			sid, err := uuid.Parse(req.GetServiceId())
			if err != nil {
				s.logErr("ListFreeSlots", err, "service_id", req.GetServiceId())
				return nil, status.Error(codes.InvalidArgument, "invalid service_id")
			}
			serviceUUID = &sid
		}

		schedules, err := s.scheduleRepo.ListByProvider(ctx, req.GetProviderId())
		if err != nil {
			s.logErr("ListFreeSlots", err, "stage", "list schedules")
			return nil, status.Errorf(codes.Internal, "list schedules: %v", err)
		}
		if err := s.materializeSlotsFromSchedules(ctx, providerUUID, serviceUUID, from.UTC(), to.UTC(), schedules); err != nil {
			s.logErr("ListFreeSlots", err, "stage", "materialize")
			return nil, status.Errorf(codes.Internal, "materialize schedule slots: %v", err)
		}
	}

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
		s.logErr("ListFreeSlots", err, "stage", "list slots")
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

type slotKey struct {
	ServiceID string
	StartNS   int64
	EndNS     int64
}

func makeSlotKey(serviceID *uuid.UUID, start, end time.Time) slotKey {
	s := ""
	if serviceID != nil {
		s = serviceID.String()
	}
	return slotKey{ServiceID: s, StartNS: start.UnixNano(), EndNS: end.UnixNano()}
}

func (s *CalendarService) materializeSlotsFromSchedules(
	ctx context.Context,
	providerID uuid.UUID,
	serviceID *uuid.UUID,
	fromUTC, toUTC time.Time,
	schedules []model.Schedule,
) error {
	if len(schedules) == 0 {
		return nil
	}
	if !toUTC.After(fromUTC) {
		return nil
	}

	// Expand all schedules to occurrences inside the window.
	occBySchedule := make(map[uuid.UUID][]calendarutils.TimeRange, len(schedules))
	for i := range schedules {
		sched := schedules[i]
		occ, err := s.expandScheduleModelInWindowUTC(&sched, fromUTC, toUTC)
		if err != nil {
			return fmt.Errorf("expand schedule %s: %w", sched.ID.String(), err)
		}
		if len(occ) == 0 {
			continue
		}
		occBySchedule[sched.ID] = occ
	}
	if len(occBySchedule) == 0 {
		return nil
	}

	return s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		// Fetch existing slots in window once.
		var existing []model.TimeSlot
		q := tx.Model(&model.TimeSlot{}).
			Where("provider_id = ?", providerID).
			Where("starts_at >= ? AND ends_at <= ?", fromUTC, toUTC)
		if serviceID != nil {
			q = q.Where("service_id = ?", *serviceID)
		} else {
			q = q.Where("service_id IS NULL")
		}
		if err := q.Find(&existing).Error; err != nil {
			return err
		}

		existingByKey := make(map[slotKey]model.TimeSlot, len(existing))
		for _, sl := range existing {
			sid := sl.ServiceID
			k := makeSlotKey(sid, sl.StartsAt.UTC(), sl.EndsAt.UTC())
			existingByKey[k] = sl
		}

		var toCreate []model.TimeSlot
		for schedID, occurrences := range occBySchedule {
			sid := schedID
			for _, occ := range occurrences {
				start := occ.Start.UTC()
				end := occ.End.UTC()
				k := makeSlotKey(serviceID, start, end)
				if _, ok := existingByKey[k]; ok {
					continue
				}
				toCreate = append(toCreate, model.TimeSlot{
					ScheduleID: &sid,
					ProviderID: providerID,
					ServiceID:  serviceID,
					StartsAt:   start,
					EndsAt:     end,
					Status:     model.TimeSlotStatusPlanned,
				})
			}
		}

		if len(toCreate) == 0 {
			return nil
		}

		// Make deterministic insert order to reduce chances of diff on retries.
		sort.Slice(toCreate, func(i, j int) bool {
			if toCreate[i].StartsAt.Equal(toCreate[j].StartsAt) {
				return toCreate[i].EndsAt.Before(toCreate[j].EndsAt)
			}
			return toCreate[i].StartsAt.Before(toCreate[j].StartsAt)
		})

		return tx.Create(&toCreate).Error
	})
}

func (s *CalendarService) expandScheduleModelInWindowUTC(sched *model.Schedule, fromUTC, toUTC time.Time) ([]calendarutils.TimeRange, error) {
	if sched == nil {
		return []calendarutils.TimeRange{}, nil
	}
	rulePB := decodeScheduleRule(sched.Rules)
	if rulePB == nil {
		return []calendarutils.TimeRange{}, nil
	}

	loc := time.UTC
	if sched.TimeZone != "" {
		if l, err := time.LoadLocation(sched.TimeZone); err == nil {
			loc = l
		}
	}

	window := calendarutils.TimeRange{Start: fromUTC.In(loc), End: toUTC.In(loc)}
	if !window.End.After(window.Start) {
		return []calendarutils.TimeRange{}, nil
	}

	// Respect schedule date bounds (start_date/end_date).
	if sched.StartDate != nil {
		sd := time.Time(*sched.StartDate).In(loc)
		sd = time.Date(sd.Year(), sd.Month(), sd.Day(), 0, 0, 0, 0, loc)
		if window.Start.Before(sd) {
			window.Start = sd
		}
	}
	if sched.EndDate != nil {
		ed := time.Time(*sched.EndDate).In(loc)
		ed = time.Date(ed.Year(), ed.Month(), ed.Day(), 23, 59, 59, 0, loc)
		if window.End.After(ed) {
			window.End = ed
		}
	}
	if !window.End.After(window.Start) {
		return []calendarutils.TimeRange{}, nil
	}

	startsAt := rulePB.GetStartsAt()
	if startsAt == nil {
		return nil, fmt.Errorf("rule.starts_at is required")
	}
	st := startsAt.AsTime().In(loc)
	// Базовая дата старта: start_date расписания или начало окна.
	baseDate := window.Start
	if sched.StartDate != nil {
		bd := time.Time(*sched.StartDate).In(loc)
		baseDate = time.Date(bd.Year(), bd.Month(), bd.Day(), 0, 0, 0, 0, loc)
	}
	startTime := time.Date(baseDate.Year(), baseDate.Month(), baseDate.Day(), st.Hour(), st.Minute(), st.Second(), 0, loc)

	// Map frequency
	freq := calendarutils.FreqDaily
	switch rulePB.GetFrequency() {
	case commonpb.RecurrenceFrequency_RECURRENCE_FREQUENCY_WEEKLY:
		freq = calendarutils.FreqWeekly
	case commonpb.RecurrenceFrequency_RECURRENCE_FREQUENCY_DAILY:
		freq = calendarutils.FreqDaily
	}

	// Map weekdays 1-7 (Mon-Sun) -> time.Weekday
	var weekdays []time.Weekday
	for _, d := range rulePB.GetWeekdays() {
		if d < 1 || d > 7 {
			continue
		}
		if d == 7 {
			weekdays = append(weekdays, time.Sunday)
		} else {
			weekdays = append(weekdays, time.Weekday(d))
		}
	}

	var until *time.Time
	if rulePB.GetUntil() != nil {
		u := rulePB.GetUntil().AsTime().In(loc)
		until = &u
	} else if sched.EndDate != nil {
		ed := time.Time(*sched.EndDate).In(loc)
		u := time.Date(ed.Year(), ed.Month(), ed.Day(), 23, 59, 59, 0, loc)
		until = &u
	}

	var count *int
	if rulePB.GetCount() > 0 {
		c := int(rulePB.GetCount())
		count = &c
	}

	exceptions := map[time.Time]struct{}{}
	for _, ts := range rulePB.GetExceptions() {
		if ts == nil {
			continue
		}
		d := ts.AsTime().In(loc)
		exceptions[time.Date(d.Year(), d.Month(), d.Day(), 0, 0, 0, 0, time.UTC)] = struct{}{}
	}
	if len(exceptions) == 0 {
		exceptions = nil
	}

	rule := calendarutils.RecurringRule{
		Freq:       freq,
		Interval:   int(rulePB.GetInterval()),
		Weekdays:   weekdays,
		StartTime:  startTime,
		Duration:   time.Duration(rulePB.GetDurationMin()) * time.Minute,
		Until:      until,
		Count:      count,
		Exceptions: exceptions,
	}

	intervals, err := calendarutils.ExpandRecurringRule(rule, window)
	if err != nil {
		return nil, err
	}

	// Convert to UTC for storage / comparison.
	for i := range intervals {
		intervals[i].Start = intervals[i].Start.UTC()
		intervals[i].End = intervals[i].End.UTC()
	}
	return intervals, nil
}

// GetAvailableSlots — alias метода из ТЗ.
func (s *CalendarService) GetAvailableSlots(
	ctx context.Context,
	req *calendarpb.ListFreeSlotsRequest,
) (*calendarpb.ListFreeSlotsResponse, error) {
	return s.ListFreeSlots(ctx, req)
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
		s.logErr("CreateBooking", err, "client_id", req.GetClientId())
		return nil, status.Error(codes.InvalidArgument, "invalid client_id")
	}
	// Роль пользователя не ограничивает возможность записываться.
	// Здесь достаточно того, что client_id существует.
	if s.db != nil {
		var c model.Client
		if err := s.db.WithContext(ctx).First(&c, "id = ?", clientID).Error; err != nil {
			s.logErr("CreateBooking", err, "stage", "find client")
			return nil, status.Errorf(codes.NotFound, "client not found: %v", err)
		}
	}

	var resp *calendarpb.CreateBookingResponse
	err = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var slot model.TimeSlot
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&slot, "id = ?", req.GetSlotId()).Error; err != nil {
			s.logErr("CreateBooking", err, "stage", "find slot")
			return status.Errorf(codes.NotFound, "slot not found: %v", err)
		}
		if slot.Status != model.TimeSlotStatusPlanned {
			return status.Error(codes.FailedPrecondition, "slot is not free")
		}

		// Проверка конфликтов по времени (ТЗ 3.5.1–3.5.2):
		// - у клиента не должно быть пересекающихся подтверждённых бронирований;
		// - у провайдера не должно быть пересекающихся подтверждённых бронирований.
		newRange := calendarutils.TimeRange{Start: slot.StartsAt.UTC(), End: slot.EndsAt.UTC()}

		clientRanges, err := s.listClientConfirmedBookingRangesTx(ctx, tx, clientID, slot.ID)
		if err != nil {
			s.logErr("CreateBooking", err, "stage", "list client ranges")
			return status.Errorf(codes.Internal, "list client booking ranges: %v", err)
		}
		if has, _ := calendarutils.HasOverlap(newRange, clientRanges, false); has {
			return status.Error(codes.FailedPrecondition, "client has conflicting booking")
		}

		providerRanges, err := s.listProviderConfirmedBookingRangesTx(ctx, tx, slot.ProviderID, slot.ID)
		if err != nil {
			s.logErr("CreateBooking", err, "stage", "list provider ranges")
			return status.Errorf(codes.Internal, "list provider booking ranges: %v", err)
		}
		if has, _ := calendarutils.HasOverlap(newRange, providerRanges, false); has {
			return status.Error(codes.FailedPrecondition, "provider has conflicting booking")
		}

		booking := &model.Booking{
			ClientID: clientID,
			SlotID:   slot.ID,
			Status:   model.BookingStatusConfirmed,
			Comment:  req.GetComment(),
		}

		if err := tx.Create(booking).Error; err != nil {
			s.logErr("CreateBooking", err, "stage", "create booking")
			return status.Errorf(codes.Internal, "create booking: %v", err)
		}

		if err := tx.Model(&model.TimeSlot{}).
			Where("id = ?", slot.ID).
			Update("status", model.TimeSlotStatusBooked).Error; err != nil {
			s.logErr("CreateBooking", err, "stage", "mark slot booked")
			return status.Errorf(codes.Internal, "mark slot booked: %v", err)
		}

		resp = &calendarpb.CreateBookingResponse{Booking: s.mapBooking(ctx, booking)}
		return nil
	})
	if err != nil {
		return nil, err
	}

	if resp != nil && resp.Booking != nil {
		s.logInfo("CreateBooking", "booking_id", resp.Booking.GetId(), "slot_id", resp.Booking.GetSlotId(), "client_id", resp.Booking.GetClientId())
	}

	return resp, nil
}

// BookSlot — alias метода из ТЗ.
func (s *CalendarService) BookSlot(
	ctx context.Context,
	req *calendarpb.CreateBookingRequest,
) (*calendarpb.CreateBookingResponse, error) {
	return s.CreateBooking(ctx, req)
}

func (s *CalendarService) CheckAvailability(
	ctx context.Context,
	req *calendarpb.CheckAvailabilityRequest,
) (*calendarpb.CheckAvailabilityResponse, error) {
	if req.GetSlotId() == "" || req.GetClientId() == "" {
		return nil, status.Error(codes.InvalidArgument, "slot_id and client_id are required")
	}

	clientID, err := uuid.Parse(req.GetClientId())
	if err != nil {
		s.logErr("CheckAvailability", err, "client_id", req.GetClientId())
		return nil, status.Error(codes.InvalidArgument, "invalid client_id")
	}
	// Роль пользователя не ограничивает возможность записываться.
	if s.db != nil {
		var c model.Client
		if err := s.db.WithContext(ctx).First(&c, "id = ?", clientID).Error; err != nil {
			return nil, status.Errorf(codes.NotFound, "client not found: %v", err)
		}
	}

	slot, err := s.slotRepo.GetByID(ctx, req.GetSlotId())
	if err != nil {
		return &calendarpb.CheckAvailabilityResponse{Available: false, Reason: "slot not found"}, nil
	}
	if slot.Status != model.TimeSlotStatusPlanned {
		return &calendarpb.CheckAvailabilityResponse{Available: false, Reason: "slot is not free"}, nil
	}

	if s.db != nil {
		newRange := calendarutils.TimeRange{Start: slot.StartsAt.UTC(), End: slot.EndsAt.UTC()}
		// Клиентские конфликты (confirmed).
		clientRanges, err := s.listClientConfirmedBookingRangesTx(ctx, s.db.WithContext(ctx), clientID, slot.ID)
		if err != nil {
			s.logErr("CheckAvailability", err, "stage", "client ranges")
			return nil, status.Errorf(codes.Internal, "check conflicts: %v", err)
		}
		if has, _ := calendarutils.HasOverlap(newRange, clientRanges, false); has {
			return &calendarpb.CheckAvailabilityResponse{Available: false, Reason: "client has conflicting booking"}, nil
		}
		// Провайдерские конфликты (confirmed).
		providerRanges, err := s.listProviderConfirmedBookingRangesTx(ctx, s.db.WithContext(ctx), slot.ProviderID, slot.ID)
		if err != nil {
			s.logErr("CheckAvailability", err, "stage", "provider ranges")
			return nil, status.Errorf(codes.Internal, "check conflicts: %v", err)
		}
		if has, _ := calendarutils.HasOverlap(newRange, providerRanges, false); has {
			return &calendarpb.CheckAvailabilityResponse{Available: false, Reason: "provider has conflicting booking"}, nil
		}
	}

	return &calendarpb.CheckAvailabilityResponse{Available: true}, nil
}

func (s *CalendarService) ExpandSchedule(
	ctx context.Context,
	req *calendarpb.ExpandScheduleRequest,
) (*calendarpb.ExpandScheduleResponse, error) {
	if req.GetScheduleId() == "" {
		return nil, status.Error(codes.InvalidArgument, "schedule_id is required")
	}
	if req.GetWindowStart() == nil || req.GetWindowEnd() == nil {
		return nil, status.Error(codes.InvalidArgument, "window_start and window_end are required")
	}

	sched, err := s.scheduleRepo.GetByID(ctx, req.GetScheduleId())
	if err != nil {
		s.logErr("ExpandSchedule", err, "stage", "get schedule", "schedule_id", req.GetScheduleId())
		return nil, status.Errorf(codes.NotFound, "schedule not found: %v", err)
	}

	// Расширение делаем для владельца расписания (provider).
	if err := s.ensureProviderRole(ctx, sched.ProviderID.String()); err != nil {
		s.logErr("ExpandSchedule", err, "stage", "ensure provider", "provider_id", sched.ProviderID.String())
		return nil, err
	}

	fromUTC := req.GetWindowStart().AsTime().UTC()
	toUTC := req.GetWindowEnd().AsTime().UTC()
	if !toUTC.After(fromUTC) {
		return nil, status.Error(codes.InvalidArgument, "window_end must be after window_start")
	}

	intervals, err := s.expandScheduleModelInWindowUTC(sched, fromUTC, toUTC)
	if err != nil {
		s.logErr("ExpandSchedule", err, "stage", "expand rule")
		return nil, status.Errorf(codes.InvalidArgument, "expand rule: %v", err)
	}

	resp := &calendarpb.ExpandScheduleResponse{Intervals: make([]*commonpb.TimeRange, 0, len(intervals))}
	for _, it := range intervals {
		resp.Intervals = append(resp.Intervals, &commonpb.TimeRange{
			Start: timestamppb.New(it.Start),
			End:   timestamppb.New(it.End),
		})
	}

	return resp, nil
}

func (s *CalendarService) ValidateSlot(
	ctx context.Context,
	req *calendarpb.ValidateSlotRequest,
) (*calendarpb.ValidateSlotResponse, error) {
	if req.GetSlotId() == "" {
		return nil, status.Error(codes.InvalidArgument, "slot_id is required")
	}
	slot, err := s.slotRepo.GetByID(ctx, req.GetSlotId())
	if err != nil {
		return &calendarpb.ValidateSlotResponse{Valid: false, Reason: "slot not found"}, nil
	}

	valid, reason := validateSlotModel(slot, req.GetProviderId(), req.GetServiceId())
	if !valid {
		return &calendarpb.ValidateSlotResponse{Valid: false, Reason: reason, Slot: mapSlot(slot)}, nil
	}
	return &calendarpb.ValidateSlotResponse{Valid: true, Slot: mapSlot(slot)}, nil
}

// GetNearestFreeSlot — быстрый предикат: ближайший свободный слот от указанного времени.
func (s *CalendarService) GetNearestFreeSlot(
	ctx context.Context,
	req *calendarpb.GetNearestFreeSlotRequest,
) (*calendarpb.GetNearestFreeSlotResponse, error) {
	from := time.Now().UTC()
	if ts := req.GetFrom(); ts != nil {
		from = ts.AsTime()
	}
	until := from.Add(30 * 24 * time.Hour)
	if ts := req.GetUntil(); ts != nil {
		until = ts.AsTime()
	}
	if !until.After(from) {
		return nil, status.Error(codes.InvalidArgument, "until must be after from")
	}

	slots, _, err := s.slotRepo.ListFreeSlots(ctx, req.GetProviderId(), req.GetServiceId(), from, until, 1, 0)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list slots: %v", err)
	}
	resp := &calendarpb.GetNearestFreeSlotResponse{}
	if len(slots) > 0 {
		resp.Slot = mapSlot(&slots[0])
	}
	return resp, nil
}

// GetNextProviderSlot — следующий свободный слот конкретного провайдера.
func (s *CalendarService) GetNextProviderSlot(
	ctx context.Context,
	req *calendarpb.GetNextProviderSlotRequest,
) (*calendarpb.GetNextProviderSlotResponse, error) {
	if req.GetProviderId() == "" {
		return nil, status.Error(codes.InvalidArgument, "provider_id is required")
	}
	from := time.Now().UTC()
	if ts := req.GetFrom(); ts != nil {
		from = ts.AsTime()
	}
	until := from.Add(30 * 24 * time.Hour)
	if ts := req.GetUntil(); ts != nil {
		until = ts.AsTime()
	}
	if !until.After(from) {
		return nil, status.Error(codes.InvalidArgument, "until must be after from")
	}

	slots, _, err := s.slotRepo.ListFreeSlots(ctx, req.GetProviderId(), req.GetServiceId(), from, until, 1, 0)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list slots: %v", err)
	}
	resp := &calendarpb.GetNextProviderSlotResponse{}
	if len(slots) > 0 {
		resp.Slot = mapSlot(&slots[0])
	}
	return resp, nil
}

// FindFreeSlots — небольшая выборка свободных слотов в окне без пагинации.
func (s *CalendarService) FindFreeSlots(
	ctx context.Context,
	req *calendarpb.FindFreeSlotsRequest,
) (*calendarpb.FindFreeSlotsResponse, error) {
	if req.GetStart() == nil || req.GetEnd() == nil {
		return nil, status.Error(codes.InvalidArgument, "start and end are required")
	}
	start := req.GetStart().AsTime()
	end := req.GetEnd().AsTime()
	if !end.After(start) {
		return nil, status.Error(codes.InvalidArgument, "end must be after start")
	}
	limit := int(req.GetLimit())
	if limit <= 0 {
		limit = 5
	}

	slots, _, err := s.slotRepo.ListFreeSlots(ctx, req.GetProviderId(), req.GetServiceId(), start, end, limit, 0)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "list slots: %v", err)
	}
	resp := &calendarpb.FindFreeSlotsResponse{Slots: make([]*commonpb.Slot, 0, len(slots))}
	for i := range slots {
		resp.Slots = append(resp.Slots, mapSlot(&slots[i]))
	}
	return resp, nil
}

func validateSlotModel(slot *model.TimeSlot, expectedProviderID, expectedServiceID string) (bool, string) {
	if slot == nil {
		return false, "slot not found"
	}
	if !slot.EndsAt.After(slot.StartsAt) {
		return false, "invalid slot time range"
	}
	if slot.Status != model.TimeSlotStatusPlanned {
		return false, "slot is not free"
	}
	if expectedProviderID != "" && slot.ProviderID.String() != expectedProviderID {
		return false, "slot provider mismatch"
	}
	if expectedServiceID != "" {
		actual := ""
		if slot.ServiceID != nil {
			actual = slot.ServiceID.String()
		}
		if actual != expectedServiceID {
			return false, "slot service mismatch"
		}
	}
	return true, ""
}

type dbTimeRange struct {
	StartsAt time.Time `gorm:"column:starts_at"`
	EndsAt   time.Time `gorm:"column:ends_at"`
}

func (s *CalendarService) listClientConfirmedBookingRangesTx(
	ctx context.Context,
	tx *gorm.DB,
	clientID uuid.UUID,
	excludeSlotID uuid.UUID,
) ([]calendarutils.TimeRange, error) {
	if tx == nil {
		return []calendarutils.TimeRange{}, nil
	}
	var rows []dbTimeRange
	err := tx.WithContext(ctx).
		Table("bookings").
		Select("time_slots.starts_at AS starts_at, time_slots.ends_at AS ends_at").
		Joins("JOIN time_slots ON time_slots.id = bookings.slot_id").
		Where("bookings.client_id = ?", clientID).
		Where("bookings.status = ?", model.BookingStatusConfirmed).
		Where("time_slots.id <> ?", excludeSlotID).
		Order("time_slots.starts_at ASC").
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	res := make([]calendarutils.TimeRange, 0, len(rows))
	for _, r := range rows {
		res = append(res, calendarutils.TimeRange{Start: r.StartsAt.UTC(), End: r.EndsAt.UTC()})
	}
	return res, nil
}

func (s *CalendarService) listProviderConfirmedBookingRangesTx(
	ctx context.Context,
	tx *gorm.DB,
	providerID uuid.UUID,
	excludeSlotID uuid.UUID,
) ([]calendarutils.TimeRange, error) {
	if tx == nil {
		return []calendarutils.TimeRange{}, nil
	}
	var rows []dbTimeRange
	err := tx.WithContext(ctx).
		Table("bookings").
		Select("time_slots.starts_at AS starts_at, time_slots.ends_at AS ends_at").
		Joins("JOIN time_slots ON time_slots.id = bookings.slot_id").
		Where("time_slots.provider_id = ?", providerID).
		Where("bookings.status = ?", model.BookingStatusConfirmed).
		Where("time_slots.id <> ?", excludeSlotID).
		Order("time_slots.starts_at ASC").
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	res := make([]calendarutils.TimeRange, 0, len(rows))
	for _, r := range rows {
		res = append(res, calendarutils.TimeRange{Start: r.StartsAt.UTC(), End: r.EndsAt.UTC()})
	}
	return res, nil
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
		s.logErr("ListBookings", err, "stage", "list bookings", "client_id", req.GetClientId())
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
		s.logErr("ListProviderSchedules", err, "stage", "list schedules", "provider_id", req.GetProviderId())
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
		s.logErr("CreateProviderSchedule", err, "stage", "encode rule")
		return nil, status.Errorf(codes.InvalidArgument, "invalid rule: %v", err)
	}
	sched.Rules = ruleJSON

	if err := s.scheduleRepo.Create(ctx, &sched); err != nil {
		s.logErr("CreateProviderSchedule", err, "stage", "create schedule")
		return nil, status.Errorf(codes.Internal, "create schedule: %v", err)
	}

	s.logInfo("CreateProviderSchedule", "schedule_id", sched.ID.String(), "provider_id", sched.ProviderID.String())

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
		s.logErr("UpdateProviderSchedule", err, "stage", "get schedule", "schedule_id", req.GetScheduleId())
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
		s.logErr("UpdateProviderSchedule", err, "stage", "encode rule")
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
		s.logErr("UpdateProviderSchedule", err, "stage", "update schedule")
		return nil, status.Errorf(codes.Internal, "update schedule: %v", err)
	}

	s.logInfo("UpdateProviderSchedule", "schedule_id", sched.ID.String(), "provider_id", sched.ProviderID.String())

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
		s.logErr("DeleteProviderSchedule", err, "stage", "get schedule", "schedule_id", req.GetScheduleId())
		return nil, status.Errorf(codes.NotFound, "schedule not found: %v", err)
	}
	if err := s.ensureProviderRole(ctx, sched.ProviderID.String()); err != nil {
		return nil, err
	}
	if err := s.scheduleRepo.Delete(ctx, req.GetScheduleId()); err != nil {
		s.logErr("DeleteProviderSchedule", err, "stage", "delete schedule")
		return nil, status.Errorf(codes.Internal, "delete schedule: %v", err)
	}

	s.logInfo("DeleteProviderSchedule", "schedule_id", req.GetScheduleId(), "provider_id", sched.ProviderID.String())
	return &calendarpb.DeleteProviderScheduleResponse{}, nil
}

// BulkCancelProviderSlots — массовая отмена слотов провайдера в интервале.
// Отменяет и связанные бронирования (если есть) и возвращает список затронутых записей,
// чтобы внешний слой мог уведомить клиентов.
func (s *CalendarService) BulkCancelProviderSlots(
	ctx context.Context,
	req *calendarpb.BulkCancelProviderSlotsRequest,
) (*calendarpb.BulkCancelProviderSlotsResponse, error) {
	if req.GetProviderId() == "" {
		return nil, status.Error(codes.InvalidArgument, "provider_id is required")
	}
	if req.GetStart() == nil || req.GetEnd() == nil {
		return nil, status.Error(codes.InvalidArgument, "start and end are required")
	}
	start := req.GetStart().AsTime()
	end := req.GetEnd().AsTime()
	if !end.After(start) {
		return nil, status.Error(codes.InvalidArgument, "end must be after start")
	}
	if s.db == nil {
		return nil, status.Error(codes.FailedPrecondition, "db is not configured")
	}

	if err := s.ensureProviderRole(ctx, req.GetProviderId()); err != nil {
		return nil, err
	}

	type affectedBookingRow struct {
		BookingID        string    `gorm:"column:booking_id"`
		SlotID           string    `gorm:"column:slot_id"`
		ClientID         string    `gorm:"column:client_id"`
		ClientUserID     string    `gorm:"column:client_user_id"`
		ClientTelegramID int64     `gorm:"column:client_telegram_id"`
		ProviderID       string    `gorm:"column:provider_id"`
		ServiceID        *string   `gorm:"column:service_id"`
		StartsAt         time.Time `gorm:"column:starts_at"`
		EndsAt           time.Time `gorm:"column:ends_at"`
	}

	resp := &calendarpb.BulkCancelProviderSlotsResponse{
		AffectedBookings: []*calendarpb.AffectedBooking{},
	}

	err := s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		// 1) Собрать активные бронирования в окне (для уведомлений) до обновлений.
		var rows []affectedBookingRow
		q := tx.Table("bookings").
			Select(
				"bookings.id AS booking_id, bookings.slot_id AS slot_id, bookings.client_id AS client_id, "+
					"clients.user_id AS client_user_id, users.telegram_id AS client_telegram_id, "+
					"time_slots.provider_id AS provider_id, time_slots.service_id AS service_id, "+
					"time_slots.starts_at AS starts_at, time_slots.ends_at AS ends_at",
			).
			Joins("JOIN time_slots ON time_slots.id = bookings.slot_id").
			Joins("JOIN clients ON clients.id = bookings.client_id").
			Joins("JOIN users ON users.id = clients.user_id").
			Where("time_slots.provider_id = ?", req.GetProviderId()).
			Where("time_slots.starts_at >= ? AND time_slots.ends_at <= ?", start, end).
			Where("time_slots.status <> ?", model.TimeSlotStatusCancelled).
			Where("bookings.status <> ?", model.BookingStatusCancelled)

		if err := q.Scan(&rows).Error; err != nil {
			s.logErr("BulkCancelProviderSlots", err, "stage", "list affected bookings")
			return status.Errorf(codes.Internal, "list affected bookings: %v", err)
		}

		// 2) Отменить бронирования (если есть).
		var cancelledBookings int64
		if len(rows) > 0 {
			bookingIDs := make([]string, 0, len(rows))
			for i := range rows {
				bookingIDs = append(bookingIDs, rows[i].BookingID)
			}
			now := time.Now().UTC()
			update := map[string]any{
				"status":       model.BookingStatusCancelled,
				"cancelled_at": now,
			}
			if req.GetReason() != "" {
				update["comment"] = req.GetReason()
			}
			res := tx.Model(&model.Booking{}).
				Where("id IN ?", bookingIDs).
				Where("status <> ?", model.BookingStatusCancelled).
				Updates(update)
			if res.Error != nil {
				s.logErr("BulkCancelProviderSlots", res.Error, "stage", "cancel bookings")
				return status.Errorf(codes.Internal, "cancel bookings: %v", res.Error)
			}
			cancelledBookings = res.RowsAffected

			resp.AffectedBookings = make([]*calendarpb.AffectedBooking, 0, len(rows))
			for i := range rows {
				serviceID := ""
				if rows[i].ServiceID != nil {
					serviceID = *rows[i].ServiceID
				}
				resp.AffectedBookings = append(resp.AffectedBookings, &calendarpb.AffectedBooking{
					BookingId:        rows[i].BookingID,
					SlotId:           rows[i].SlotID,
					ClientId:         rows[i].ClientID,
					ClientUserId:     rows[i].ClientUserID,
					ClientTelegramId: rows[i].ClientTelegramID,
					ProviderId:       rows[i].ProviderID,
					ServiceId:        serviceID,
					StartsAt:         timestamppb.New(rows[i].StartsAt),
					EndsAt:           timestamppb.New(rows[i].EndsAt),
				})
			}
		}
		resp.CancelledBookings = int32(cancelledBookings)

		// 3) Отменить слоты провайдера в окне.
		res := tx.Model(&model.TimeSlot{}).
			Where("provider_id = ?", req.GetProviderId()).
			Where("starts_at >= ? AND ends_at <= ?", start, end).
			Where("status <> ?", model.TimeSlotStatusCancelled).
			Update("status", model.TimeSlotStatusCancelled)
		if res.Error != nil {
			s.logErr("BulkCancelProviderSlots", res.Error, "stage", "cancel slots")
			return status.Errorf(codes.Internal, "cancel slots: %v", res.Error)
		}
		resp.CancelledSlots = int32(res.RowsAffected)

		return nil
	})
	if err != nil {
		return nil, err
	}

	s.logInfo("BulkCancelProviderSlots", "provider_id", req.GetProviderId(), "start", start, "end", end, "cancelled_slots", resp.GetCancelledSlots(), "cancelled_bookings", resp.GetCancelledBookings())

	return resp, nil
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
			s.logErr("CreateSlot", err, "service_id", req.GetServiceId())
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
		s.logErr("CreateSlot", err, "stage", "create slot")
		return nil, status.Errorf(codes.Internal, "create slot: %v", err)
	}

	s.logInfo("CreateSlot", "slot_id", slot.ID.String(), "provider_id", req.GetProviderId(), "service_id", req.GetServiceId())

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
		s.logErr("UpdateSlot", err, "stage", "get slot", "slot_id", req.GetSlotId())
		return nil, status.Errorf(codes.NotFound, "slot not found: %v", err)
	}
	if err := s.ensureProviderRole(ctx, slot.ProviderID.String()); err != nil {
		return nil, err
	}

	if req.GetServiceId() != "" {
		id, err := uuid.Parse(req.GetServiceId())
		if err != nil {
			s.logErr("UpdateSlot", err, "service_id", req.GetServiceId())
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
		s.logErr("UpdateSlot", err, "stage", "update slot")
		return nil, status.Errorf(codes.Internal, "update slot: %v", err)
	}

	s.logInfo("UpdateSlot", "slot_id", slot.ID.String(), "provider_id", slot.ProviderID.String(), "status", slot.Status)

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
		s.logErr("DeleteSlot", err, "stage", "get slot", "slot_id", req.GetSlotId())
		return nil, status.Errorf(codes.NotFound, "slot not found: %v", err)
	}
	if err := s.ensureProviderRole(ctx, slot.ProviderID.String()); err != nil {
		return nil, err
	}

	if err := s.slotRepo.Delete(ctx, req.GetSlotId()); err != nil {
		s.logErr("DeleteSlot", err, "stage", "delete slot")
		return nil, status.Errorf(codes.Internal, "delete slot: %v", err)
	}

	s.logInfo("DeleteSlot", "slot_id", req.GetSlotId(), "provider_id", slot.ProviderID.String())
	return &calendarpb.DeleteSlotResponse{}, nil
}

func (s *CalendarService) ListServices(
	ctx context.Context,
	req *calendarpb.ListServicesRequest,
) (*calendarpb.ListServicesResponse, error) {
	if s.serviceRepo == nil {
		return nil, status.Error(codes.Internal, "service repository is not configured")
	}

	onlyActive := true
	if req != nil {
		onlyActive = req.GetOnlyActive()
	}

	page := int32(1)
	size := int32(50)
	if req != nil {
		if req.GetPage() > 0 {
			page = req.GetPage()
		}
		if req.GetPageSize() > 0 {
			size = req.GetPageSize()
		}
	}
	offset := (int(page) - 1) * int(size)

	items, total, err := s.serviceRepo.List(ctx, onlyActive, int(size), offset)
	if err != nil {
		s.logErr("ListServices", err, "stage", "list services")
		return nil, status.Errorf(codes.Internal, "list services: %v", err)
	}

	resp := &calendarpb.ListServicesResponse{Services: make([]*commonpb.Service, 0, len(items)), TotalCount: int32(total)}
	for i := range items {
		resp.Services = append(resp.Services, mapService(&items[i]))
	}
	return resp, nil
}

func (s *CalendarService) ListProviderServices(
	ctx context.Context,
	req *calendarpb.ListProviderServicesRequest,
) (*calendarpb.ListProviderServicesResponse, error) {
	if req.GetProviderId() == "" {
		return nil, status.Error(codes.InvalidArgument, "provider_id is required")
	}
	if s.providerRepo == nil || s.serviceRepo == nil {
		return nil, status.Error(codes.Internal, "repositories are not configured")
	}

	provider, err := s.providerRepo.GetByID(ctx, req.GetProviderId())
	if err != nil {
		s.logErr("ListProviderServices", err, "stage", "get provider", "provider_id", req.GetProviderId())
		return nil, status.Errorf(codes.NotFound, "provider not found: %v", err)
	}

	providerID, err := uuid.Parse(req.GetProviderId())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid provider_id")
	}
	services, err := s.serviceRepo.ListByProvider(ctx, providerID)
	if err != nil {
		s.logErr("ListProviderServices", err, "stage", "list provider services", "provider_id", req.GetProviderId())
		return nil, status.Errorf(codes.Internal, "list provider services: %v", err)
	}

	resp := &calendarpb.ListProviderServicesResponse{
		Provider: &commonpb.Provider{Id: provider.ID.String(), DisplayName: provider.DisplayName, Description: provider.Description},
		Services: make([]*commonpb.Service, 0, len(services)),
	}
	for i := range services {
		resp.Services = append(resp.Services, mapService(&services[i]))
	}
	return resp, nil
}

func mapService(sv *model.Service) *commonpb.Service {
	if sv == nil {
		return nil
	}
	var d int32
	if sv.DefaultDurationMin != nil {
		d = int32(*sv.DefaultDurationMin)
	}
	return &commonpb.Service{
		Id:                 sv.ID.String(),
		Name:               sv.Name,
		Description:        sv.Description,
		DefaultDurationMin: d,
		IsActive:           sv.IsActive,
	}
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
	Exceptions  []time.Time                  `json:"exceptions,omitempty"`
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
	if len(rule.GetExceptions()) > 0 {
		ex := make([]time.Time, 0, len(rule.GetExceptions()))
		for _, ts := range rule.GetExceptions() {
			if ts == nil {
				continue
			}
			// Храним только дату (time at 00:00 UTC) для стабильного сравнения.
			t := ts.AsTime().UTC()
			ex = append(ex, time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC))
		}
		dto.Exceptions = ex
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

	var exceptions []*timestamppb.Timestamp
	if len(dto.Exceptions) > 0 {
		exceptions = make([]*timestamppb.Timestamp, 0, len(dto.Exceptions))
		for _, d := range dto.Exceptions {
			d = d.UTC()
			exceptions = append(exceptions, timestamppb.New(time.Date(d.Year(), d.Month(), d.Day(), 0, 0, 0, 0, time.UTC)))
		}
	}

	return &commonpb.ScheduleRule{
		Frequency:   dto.Frequency,
		Interval:    dto.Interval,
		Weekdays:    dto.Weekdays,
		StartsAt:    startsAt,
		DurationMin: dto.DurationMin,
		Until:       until,
		Count:       dto.Count,
		Exceptions:  exceptions,
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
