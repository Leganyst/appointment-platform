package calendar

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/Leganyst/appointment-platform/internal/calendar"
)

func mustTime(t *testing.T, year int, month time.Month, day, hour, min int) time.Time {
	t.Helper()
	return time.Date(year, month, day, hour, min, 0, 0, time.UTC)
}

func equalTimeRange(a, b TimeRange) bool {
	return a.Start.Equal(b.Start) && a.End.Equal(b.End)
}

func equalTimeRangeSlices(a, b []TimeRange) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if !equalTimeRange(a[i], b[i]) {
			return false
		}
	}
	return true
}

//
// 3.1. Тесты для NormalizeTimeRange
//

func TestNormalizeTimeRange_SwappedBounds(t *testing.T) {
	loc := time.UTC
	start := mustTime(t, 2025, 1, 1, 12, 0)
	end := mustTime(t, 2025, 1, 1, 10, 0)

	tr, err := NormalizeTimeRange(start, end, loc, 0)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}

	if tr.Start.Equal(start) || tr.End.Equal(end) {
		t.Fatalf("expected bounds to be swapped, got %v", tr)
	}
	if !tr.Start.Equal(end) || !tr.End.Equal(start) {
		t.Fatalf("expected Start=%v End=%v, got %v", end, start, tr)
	}
}

func TestNormalizeTimeRange_MaxDuration(t *testing.T) {
	loc := time.UTC
	start := mustTime(t, 2025, 1, 1, 10, 0)
	end := mustTime(t, 2025, 1, 1, 15, 0)
	maxDuration := 2 * time.Hour

	tr, err := NormalizeTimeRange(start, end, loc, maxDuration)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}

	dur := tr.End.Sub(tr.Start)
	if dur != maxDuration {
		t.Fatalf("expected duration %v, got %v", maxDuration, dur)
	}
}

func TestNormalizeTimeRange_InvalidZero(t *testing.T) {
	_, err := NormalizeTimeRange(time.Time{}, time.Time{}, time.UTC, 0)
	if err == nil {
		t.Fatalf("expected error for zero times, got nil")
	}
}

//
// 3.2. Тесты для SplitToTimeSlots
//

func TestSplitToTimeSlots_Basic(t *testing.T) {
	start := mustTime(t, 2025, 1, 1, 10, 0)
	end := mustTime(t, 2025, 1, 1, 12, 0)
	tr := TimeRange{Start: start, End: end}

	slots, err := SplitToTimeSlots(tr, 30*time.Minute, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(slots) != 4 {
		t.Fatalf("expected 4 slots, got %d", len(slots))
	}

	expected := []TimeRange{
		{Start: mustTime(t, 2025, 1, 1, 10, 0), End: mustTime(t, 2025, 1, 1, 10, 30)},
		{Start: mustTime(t, 2025, 1, 1, 10, 30), End: mustTime(t, 2025, 1, 1, 11, 0)},
		{Start: mustTime(t, 2025, 1, 1, 11, 0), End: mustTime(t, 2025, 1, 1, 11, 30)},
		{Start: mustTime(t, 2025, 1, 1, 11, 30), End: mustTime(t, 2025, 1, 1, 12, 0)},
	}

	if !equalTimeRangeSlices(slots, expected) {
		t.Fatalf("expected %+v, got %+v", expected, slots)
	}
}

func TestSplitToTimeSlots_TailDropped(t *testing.T) {
	start := mustTime(t, 2025, 1, 1, 10, 0)
	end := mustTime(t, 2025, 1, 1, 11, 10)
	tr := TimeRange{Start: start, End: end}

	slots, err := SplitToTimeSlots(tr, 30*time.Minute, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(slots) != 2 {
		t.Fatalf("expected 2 slots, got %d", len(slots))
	}
}

func TestSplitToTimeSlots_AlignMinutes(t *testing.T) {
	start := mustTime(t, 2025, 1, 1, 10, 10)
	end := mustTime(t, 2025, 1, 1, 11, 40)
	tr := TimeRange{Start: start, End: end}

	slots, err := SplitToTimeSlots(tr, 30*time.Minute, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(slots) == 0 {
		t.Fatalf("expected slots, got 0")
	}
	if !slots[0].Start.Equal(mustTime(t, 2025, 1, 1, 10, 30)) {
		t.Fatalf("expected first slot to start at 10:30, got %v", slots[0].Start)
	}
}

func TestSplitToTimeSlots_InvalidDuration(t *testing.T) {
	tr := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 10, 0),
		End:   mustTime(t, 2025, 1, 1, 11, 0),
	}

	_, err := SplitToTimeSlots(tr, 0, 0)
	if err == nil {
		t.Fatalf("expected error for zero slot duration, got nil")
	}
}

//
// 3.3. Тесты для HasOverlap
//

func TestHasOverlap_NoOverlap(t *testing.T) {
	newRange := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 10, 0),
		End:   mustTime(t, 2025, 1, 1, 11, 0),
	}
	existing := []TimeRange{
		{Start: mustTime(t, 2025, 1, 1, 11, 0), End: mustTime(t, 2025, 1, 1, 12, 0)},
	}

	has, conflicts := HasOverlap(newRange, existing, false)
	if has {
		t.Fatalf("expected no overlap, got conflicts: %+v", conflicts)
	}
}

func TestHasOverlap_TouchInclusive(t *testing.T) {
	newRange := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 10, 0),
		End:   mustTime(t, 2025, 1, 1, 11, 0),
	}
	existing := []TimeRange{
		{Start: mustTime(t, 2025, 1, 1, 11, 0), End: mustTime(t, 2025, 1, 1, 12, 0)},
	}

	has, _ := HasOverlap(newRange, existing, true)
	if !has {
		t.Fatalf("expected overlap in inclusive mode")
	}
}

func TestHasOverlap_OverlapFound(t *testing.T) {
	newRange := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 10, 30),
		End:   mustTime(t, 2025, 1, 1, 11, 30),
	}
	existing := []TimeRange{
		{Start: mustTime(t, 2025, 1, 1, 9, 0), End: mustTime(t, 2025, 1, 1, 10, 0)},
		{Start: mustTime(t, 2025, 1, 1, 11, 0), End: mustTime(t, 2025, 1, 1, 12, 0)},
	}

	has, conflicts := HasOverlap(newRange, existing, false)
	if !has {
		t.Fatalf("expected overlap, got none")
	}
	if len(conflicts) != 1 {
		t.Fatalf("expected 1 conflict, got %d", len(conflicts))
	}
}

//
// 3.4. Тесты для ExpandRecurringRule
//

func TestExpandRecurringRule_DailyCount(t *testing.T) {
	start := mustTime(t, 2025, 1, 1, 10, 0)
	window := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 0, 0),
		End:   mustTime(t, 2025, 1, 10, 0, 0),
	}
	count := 3

	rule := RecurringRule{
		Freq:      FreqDaily,
		Interval:  1,
		StartTime: start,
		Duration:  time.Hour,
		Count:     &count,
	}

	events, err := ExpandRecurringRule(rule, window)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(events) != 3 {
		t.Fatalf("expected 3 events, got %d", len(events))
	}

	expectedStarts := []time.Time{
		mustTime(t, 2025, 1, 1, 10, 0),
		mustTime(t, 2025, 1, 2, 10, 0),
		mustTime(t, 2025, 1, 3, 10, 0),
	}

	for i, ev := range events {
		if !ev.Start.Equal(expectedStarts[i]) {
			t.Fatalf("event %d: expected start %v, got %v", i, expectedStarts[i], ev.Start)
		}
	}
}

func TestExpandRecurringRule_InvalidDuration(t *testing.T) {
	rule := RecurringRule{
		Freq:      FreqDaily,
		Interval:  1,
		StartTime: mustTime(t, 2025, 1, 1, 10, 0),
		Duration:  0,
	}
	window := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 0, 0),
		End:   mustTime(t, 2025, 1, 10, 0, 0),
	}

	_, err := ExpandRecurringRule(rule, window)
	if err == nil {
		t.Fatalf("expected error for zero duration, got nil")
	}
}

//
// 3.5. Тесты для FormatSlotForUser
//

func TestFormatSlotForUser_Basic(t *testing.T) {
	tr := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 10, 0),
		End:   mustTime(t, 2025, 1, 1, 11, 0),
	}

	str := FormatSlotForUser(tr, time.UTC, false, "")
	// Ожидаем что-то типа "Среда, 01.01.2025, 10:00–11:00"
	if str == "" {
		t.Fatalf("expected non-empty string")
	}
	if !containsAll(str, []string{"2025", "10:00", "11:00"}) {
		t.Fatalf("unexpected format: %q", str)
	}
}

func TestFormatSlotForUser_WithID(t *testing.T) {
	tr := TimeRange{
		Start: mustTime(t, 2025, 1, 1, 10, 0),
		End:   mustTime(t, 2025, 1, 1, 11, 0),
	}

	str := FormatSlotForUser(tr, time.UTC, true, "slot-123")
	if str == "" || !containsAll(str, []string{"ID", "slot-123"}) {
		t.Fatalf("expected string with ID, got %q", str)
	}
}

func containsAll(s string, parts []string) bool {
	for _, p := range parts {
		if !contains(s, p) {
			return false
		}
	}
	return true
}

func contains(s, sub string) bool {
	return len(sub) == 0 || (len(sub) > 0 && reflect.ValueOf(s).String() != "" && (func() bool {
		return len(s) >= len(sub) && (func() bool {
			for i := 0; i+len(sub) <= len(s); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
			return false
		})()
	})())
}

// Можно было бы использовать strings.Contains, но это учебный пример.
// На практике просто импортируй "strings" и используй strings.Contains.

//
// 3.6. Тесты для ValidateTelegramUser
//

type mockUserStore struct {
	user *calendar.User
	err  error
}

func (m *mockUserStore) FindByTelegramID(ctx context.Context, telegramID int64) (*calendar.User, error) {
	return m.user, m.err
}

func TestValidateTelegramUser_Success(t *testing.T) {
	store := &mockUserStore{
		user: &calendar.User{
			ID:         1,
			TelegramID: 123,
			Role:       calendar.UserRoleClient,
			Status:     calendar.UserStatusActive,
		},
	}

	result, err := calendar.ValidateTelegramUser(context.Background(), store, 123)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result == nil || result.ID != 1 || result.Role != calendar.UserRoleClient {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestValidateTelegramUser_InvalidID(t *testing.T) {
	store := &mockUserStore{}
	_, err := calendar.ValidateTelegramUser(context.Background(), store, 0)
	if err != calendar.ErrInvalidTelegramID {
		t.Fatalf("expected ErrInvalidTelegramID, got %v", err)
	}
}

func TestValidateTelegramUser_UserNotFound(t *testing.T) {
	store := &mockUserStore{
		user: nil,
		err:  nil,
	}
	_, err := calendar.ValidateTelegramUser(context.Background(), store, 123)
	if err != calendar.ErrUserNotFound {
		t.Fatalf("expected ErrUserNotFound, got %v", err)
	}
}

func TestValidateTelegramUser_UserInactive(t *testing.T) {
	store := &mockUserStore{
		user: &calendar.User{
			ID:         1,
			TelegramID: 123,
			Role:       calendar.UserRoleClient,
			Status:     calendar.UserStatusInactive,
		},
	}
	_, err := calendar.ValidateTelegramUser(context.Background(), store, 123)
	if err != calendar.ErrUserInactive {
		t.Fatalf("expected ErrUserInactive, got %v", err)
	}
}

//
// 3.7. Тесты для Paginate / SlicePage
//

func TestPaginate_Basic(t *testing.T) {
	items := []int{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
	page := calendar.Paginate(items, 1, 5)

	if len(page.Items) != 5 {
		t.Fatalf("expected 5 items on page 1, got %d", len(page.Items))
	}
	if page.HasPrev {
		t.Fatalf("expected HasPrev=false on first page")
	}
	if !page.HasNext {
		t.Fatalf("expected HasNext=true on first page")
	}
	if page.Total != len(items) {
		t.Fatalf("expected Total=%d, got %d", len(items), page.Total)
	}
}

func TestPaginate_LastPage(t *testing.T) {
	items := []int{1, 2, 3, 4, 5, 6}
	page := calendar.Paginate(items, 2, 4)

	if len(page.Items) != 2 {
		t.Fatalf("expected 2 items on last page, got %d", len(page.Items))
	}
	if !page.HasPrev {
		t.Fatalf("expected HasPrev=true on last page")
	}
	if page.HasNext {
		t.Fatalf("expected HasNext=false on last page")
	}
}

func TestPaginate_Empty(t *testing.T) {
	var items []int
	page := calendar.Paginate(items, 1, 10)

	if len(page.Items) != 0 {
		t.Fatalf("expected 0 items, got %d", len(page.Items))
	}
	if page.HasNext || page.HasPrev {
		t.Fatalf("expected no prev/next for empty list")
	}
}

func TestSlicePage_Alias(t *testing.T) {
	items := []int{1, 2, 3}
	p1 := calendar.Paginate(items, 1, 2)
	p2 := calendar.SlicePage(items, 1, 2)

	if !reflect.DeepEqual(p1, p2) {
		t.Fatalf("expected SlicePage to behave like Paginate")
	}
}
