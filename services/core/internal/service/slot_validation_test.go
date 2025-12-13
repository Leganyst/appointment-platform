package service

import (
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/Leganyst/appointment-platform/internal/model"
)

func TestValidateSlotModel_OK(t *testing.T) {
	providerID := uuid.New()
	serviceID := uuid.New()

	slot := &model.TimeSlot{
		ProviderID: providerID,
		ServiceID:  &serviceID,
		StartsAt:   time.Date(2025, 1, 1, 10, 0, 0, 0, time.UTC),
		EndsAt:     time.Date(2025, 1, 1, 11, 0, 0, 0, time.UTC),
		Status:     model.TimeSlotStatusPlanned,
	}

	ok, reason := validateSlotModel(slot, providerID.String(), serviceID.String())
	if !ok {
		t.Fatalf("expected valid, got reason=%q", reason)
	}
	if reason != "" {
		t.Fatalf("expected empty reason, got %q", reason)
	}
}

func TestValidateSlotModel_InvalidRange(t *testing.T) {
	providerID := uuid.New()

	slot := &model.TimeSlot{
		ProviderID: providerID,
		StartsAt:   time.Date(2025, 1, 1, 10, 0, 0, 0, time.UTC),
		EndsAt:     time.Date(2025, 1, 1, 10, 0, 0, 0, time.UTC),
		Status:     model.TimeSlotStatusPlanned,
	}

	ok, reason := validateSlotModel(slot, "", "")
	if ok {
		t.Fatalf("expected invalid")
	}
	if reason != "invalid slot time range" {
		t.Fatalf("expected reason %q, got %q", "invalid slot time range", reason)
	}
}

func TestValidateSlotModel_NotFree(t *testing.T) {
	slot := &model.TimeSlot{
		ProviderID: uuid.New(),
		StartsAt:   time.Date(2025, 1, 1, 10, 0, 0, 0, time.UTC),
		EndsAt:     time.Date(2025, 1, 1, 11, 0, 0, 0, time.UTC),
		Status:     model.TimeSlotStatusBooked,
	}

	ok, reason := validateSlotModel(slot, "", "")
	if ok {
		t.Fatalf("expected invalid")
	}
	if reason != "slot is not free" {
		t.Fatalf("expected reason %q, got %q", "slot is not free", reason)
	}
}

func TestValidateSlotModel_ProviderMismatch(t *testing.T) {
	slot := &model.TimeSlot{
		ProviderID: uuid.New(),
		StartsAt:   time.Date(2025, 1, 1, 10, 0, 0, 0, time.UTC),
		EndsAt:     time.Date(2025, 1, 1, 11, 0, 0, 0, time.UTC),
		Status:     model.TimeSlotStatusPlanned,
	}

	ok, reason := validateSlotModel(slot, uuid.New().String(), "")
	if ok {
		t.Fatalf("expected invalid")
	}
	if reason != "slot provider mismatch" {
		t.Fatalf("expected reason %q, got %q", "slot provider mismatch", reason)
	}
}

func TestValidateSlotModel_ServiceMismatch(t *testing.T) {
	sid := uuid.New()
	slot := &model.TimeSlot{
		ProviderID: uuid.New(),
		ServiceID:  &sid,
		StartsAt:   time.Date(2025, 1, 1, 10, 0, 0, 0, time.UTC),
		EndsAt:     time.Date(2025, 1, 1, 11, 0, 0, 0, time.UTC),
		Status:     model.TimeSlotStatusPlanned,
	}

	ok, reason := validateSlotModel(slot, "", uuid.New().String())
	if ok {
		t.Fatalf("expected invalid")
	}
	if reason != "slot service mismatch" {
		t.Fatalf("expected reason %q, got %q", "slot service mismatch", reason)
	}
}

func TestValidateSlotModel_ServiceMismatch_WhenNil(t *testing.T) {
	slot := &model.TimeSlot{
		ProviderID: uuid.New(),
		ServiceID:  nil,
		StartsAt:   time.Date(2025, 1, 1, 10, 0, 0, 0, time.UTC),
		EndsAt:     time.Date(2025, 1, 1, 11, 0, 0, 0, time.UTC),
		Status:     model.TimeSlotStatusPlanned,
	}

	ok, reason := validateSlotModel(slot, "", uuid.New().String())
	if ok {
		t.Fatalf("expected invalid")
	}
	if reason != "slot service mismatch" {
		t.Fatalf("expected reason %q, got %q", "slot service mismatch", reason)
	}
}
