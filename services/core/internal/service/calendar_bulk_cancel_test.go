package service

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"google.golang.org/protobuf/types/known/timestamppb"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"

	calendarpb "github.com/Leganyst/appointment-platform/internal/api/calendar/v1"
	"github.com/Leganyst/appointment-platform/internal/model"
)

func TestCalendarService_BulkCancelProviderSlots_ReturnsRecipientsAndCancels(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}

	// Minimal schema for the query/update logic (sqlite-friendly).
	schema := []string{
		`CREATE TABLE users (
			id TEXT PRIMARY KEY,
			telegram_id INTEGER NOT NULL,
			display_name TEXT,
			contact_phone TEXT,
			note TEXT,
			created_at DATETIME,
			updated_at DATETIME
		);`,
		`CREATE TABLE clients (
			id TEXT PRIMARY KEY,
			user_id TEXT NOT NULL,
			comment TEXT,
			created_at DATETIME,
			updated_at DATETIME
		);`,
		`CREATE TABLE providers (
			id TEXT PRIMARY KEY,
			user_id TEXT NOT NULL,
			display_name TEXT NOT NULL,
			description TEXT,
			created_at DATETIME,
			updated_at DATETIME
		);`,
		`CREATE TABLE time_slots (
			id TEXT PRIMARY KEY,
			schedule_id TEXT,
			provider_id TEXT NOT NULL,
			service_id TEXT,
			starts_at DATETIME NOT NULL,
			ends_at DATETIME NOT NULL,
			status TEXT NOT NULL,
			created_at DATETIME,
			updated_at DATETIME
		);`,
		`CREATE TABLE bookings (
			id TEXT PRIMARY KEY,
			client_id TEXT NOT NULL,
			slot_id TEXT NOT NULL UNIQUE,
			created_at DATETIME,
			updated_at DATETIME,
			status TEXT NOT NULL,
			cancelled_at DATETIME,
			comment TEXT
		);`,
	}
	for _, stmt := range schema {
		if err := db.Exec(stmt).Error; err != nil {
			t.Fatalf("create schema: %v", err)
		}
	}

	providerUserID := uuid.New()
	providerID := uuid.New()
	clientUserID := uuid.New()
	clientID := uuid.New()
	serviceID := uuid.New()

	clientTelegramID := int64(777001)

	now := time.Now().UTC().Truncate(time.Second)
	windowStart := now.Add(-1 * time.Hour)
	windowEnd := now.Add(2 * time.Hour)

	slotInWindowBookedID := uuid.New()
	slotInWindowPlannedID := uuid.New()
	slotOutOfWindowID := uuid.New()
	bookingID := uuid.New()

	// Seed users/clients/providers.
	if err := db.Create(&model.User{ID: providerUserID, TelegramID: 111, DisplayName: "prov"}).Error; err != nil {
		t.Fatalf("seed provider user: %v", err)
	}
	if err := db.Create(&model.Provider{ID: providerID, UserID: providerUserID, DisplayName: "prov"}).Error; err != nil {
		t.Fatalf("seed provider: %v", err)
	}
	if err := db.Create(&model.User{ID: clientUserID, TelegramID: clientTelegramID, DisplayName: "cli"}).Error; err != nil {
		t.Fatalf("seed client user: %v", err)
	}
	if err := db.Create(&model.Client{ID: clientID, UserID: clientUserID}).Error; err != nil {
		t.Fatalf("seed client: %v", err)
	}

	// Slots.
	if err := db.Create(&model.TimeSlot{
		ID:         slotInWindowBookedID,
		ProviderID: providerID,
		ServiceID:  &serviceID,
		StartsAt:   now,
		EndsAt:     now.Add(30 * time.Minute),
		Status:     model.TimeSlotStatusBooked,
	}).Error; err != nil {
		t.Fatalf("seed booked slot: %v", err)
	}
	if err := db.Create(&model.TimeSlot{
		ID:         slotInWindowPlannedID,
		ProviderID: providerID,
		StartsAt:   now.Add(40 * time.Minute),
		EndsAt:     now.Add(70 * time.Minute),
		Status:     model.TimeSlotStatusPlanned,
	}).Error; err != nil {
		t.Fatalf("seed planned slot: %v", err)
	}
	if err := db.Create(&model.TimeSlot{
		ID:         slotOutOfWindowID,
		ProviderID: providerID,
		StartsAt:   now.Add(4 * time.Hour),
		EndsAt:     now.Add(5 * time.Hour),
		Status:     model.TimeSlotStatusPlanned,
	}).Error; err != nil {
		t.Fatalf("seed out-of-window slot: %v", err)
	}

	// Booking on booked slot.
	if err := db.Create(&model.Booking{
		ID:       bookingID,
		ClientID: clientID,
		SlotID:   slotInWindowBookedID,
		Status:   model.BookingStatusConfirmed,
	}).Error; err != nil {
		t.Fatalf("seed booking: %v", err)
	}

	svc := &CalendarService{db: db}
	reason := "provider cancelled window"

	resp, err := svc.BulkCancelProviderSlots(context.Background(), &calendarpb.BulkCancelProviderSlotsRequest{
		ProviderId: providerID.String(),
		Start:      timestamppb.New(windowStart),
		End:        timestamppb.New(windowEnd),
		Reason:     reason,
	})
	if err != nil {
		t.Fatalf("BulkCancelProviderSlots: %v", err)
	}

	if resp.GetCancelledSlots() != 2 {
		t.Fatalf("cancelled_slots = %d, want 2", resp.GetCancelledSlots())
	}
	if resp.GetCancelledBookings() != 1 {
		t.Fatalf("cancelled_bookings = %d, want 1", resp.GetCancelledBookings())
	}
	if len(resp.GetAffectedBookings()) != 1 {
		t.Fatalf("affected_bookings len = %d, want 1", len(resp.GetAffectedBookings()))
	}
	ab := resp.GetAffectedBookings()[0]
	if ab.GetBookingId() != bookingID.String() {
		t.Fatalf("affected booking_id = %s, want %s", ab.GetBookingId(), bookingID.String())
	}
	if ab.GetClientId() != clientID.String() {
		t.Fatalf("affected client_id = %s, want %s", ab.GetClientId(), clientID.String())
	}
	if ab.GetClientUserId() != clientUserID.String() {
		t.Fatalf("affected client_user_id = %s, want %s", ab.GetClientUserId(), clientUserID.String())
	}
	if ab.GetClientTelegramId() != clientTelegramID {
		t.Fatalf("affected client_telegram_id = %d, want %d", ab.GetClientTelegramId(), clientTelegramID)
	}
	if ab.GetProviderId() != providerID.String() {
		t.Fatalf("affected provider_id = %s, want %s", ab.GetProviderId(), providerID.String())
	}
	if ab.GetServiceId() != serviceID.String() {
		t.Fatalf("affected service_id = %s, want %s", ab.GetServiceId(), serviceID.String())
	}

	// Verify DB updates.
	var s1, s2, s3 model.TimeSlot
	if err := db.First(&s1, "id = ?", slotInWindowBookedID.String()).Error; err != nil {
		t.Fatalf("load slot1: %v", err)
	}
	if err := db.First(&s2, "id = ?", slotInWindowPlannedID.String()).Error; err != nil {
		t.Fatalf("load slot2: %v", err)
	}
	if err := db.First(&s3, "id = ?", slotOutOfWindowID.String()).Error; err != nil {
		t.Fatalf("load slot3: %v", err)
	}
	if s1.Status != model.TimeSlotStatusCancelled || s2.Status != model.TimeSlotStatusCancelled {
		t.Fatalf("in-window slots not cancelled: %s / %s", s1.Status, s2.Status)
	}
	if s3.Status != model.TimeSlotStatusPlanned {
		t.Fatalf("out-of-window slot changed: %s", s3.Status)
	}

	var b model.Booking
	if err := db.First(&b, "id = ?", bookingID.String()).Error; err != nil {
		t.Fatalf("load booking: %v", err)
	}
	if b.Status != model.BookingStatusCancelled {
		t.Fatalf("booking status = %s, want cancelled", b.Status)
	}
	if b.CancelledAt == nil {
		t.Fatalf("booking cancelled_at is nil")
	}
	if b.Comment != reason {
		t.Fatalf("booking comment = %q, want %q", b.Comment, reason)
	}
}
