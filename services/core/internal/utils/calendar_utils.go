package calendar

import (
	"context"
	"errors"
	"fmt"
	"time"
)

var (
	ErrInvalidTimeRange  = errors.New("invalid time range")
	ErrSlotDuration      = errors.New("slot duration must be positive")
	ErrInvalidTelegramID = errors.New("invalid telegram ID")
	ErrUserNotFound      = errors.New("user not found")
	ErrUserInactive      = errors.New("user is inactive")
)

// TimeRange представляет временной интервал [Start, End).
type TimeRange struct {
	Start time.Time
	End   time.Time
}

// NewTimeRange создаёт интервал и делает простую валидацию.
func NewTimeRange(start, end time.Time) (TimeRange, error) {
	if start.IsZero() || end.IsZero() {
		return TimeRange{}, ErrInvalidTimeRange
	}
	return TimeRange{Start: start, End: end}, nil
}

// NormalizeTimeRange нормализует интервал:
//   - меняет местами границы, если они перепутаны;
//   - переводит в заданный часовой пояс loc;
//   - при превышении maxDuration обрезает интервал до start+maxDuration.
// Если maxDuration <= 0, ограничение по длительности не применяется.
func NormalizeTimeRange(
	start, end time.Time,
	loc *time.Location,
	maxDuration time.Duration,
) (TimeRange, error) {
	if start.IsZero() || end.IsZero() {
		return TimeRange{}, ErrInvalidTimeRange
	}

	// Перестановка границ при необходимости.
	if end.Before(start) {
		start, end = end, start
	}

	if loc != nil {
		start = start.In(loc)
		end = end.In(loc)
	}

	if maxDuration > 0 {
		if end.Sub(start) > maxDuration {
			end = start.Add(maxDuration)
		}
	}

	if !end.After(start) {
		return TimeRange{}, ErrInvalidTimeRange
	}

	return TimeRange{Start: start, End: end}, nil
}

// SplitToTimeSlots разбивает интервал на слоты фиксированной длительности.
// alignMinutes > 0 — выравнивание начала по ближайшей отметке, кратной alignMinutes.
// "Хвост" меньшей длительности, чем slotDuration, отбрасывается.
func SplitToTimeSlots(
	tr TimeRange,
	slotDuration time.Duration,
	alignMinutes int,
) ([]TimeRange, error) {
	if slotDuration <= 0 {
		return nil, ErrSlotDuration
	}
	if !tr.End.After(tr.Start) {
		return []TimeRange{}, nil
	}

	start := tr.Start

	// Выравнивание по шагу в минутах, если задан.
	if alignMinutes > 0 {
		min := start.Minute()
		rem := min % alignMinutes
		if rem != 0 {
			delta := alignMinutes - rem
			start = time.Date(
				start.Year(),
				start.Month(),
				start.Day(),
				start.Hour(),
				min+delta,
				0, 0,
				start.Location(),
			)
			if !start.Before(tr.End) {
				return []TimeRange{}, nil
			}
		}
	}

	var slots []TimeRange
	for cur := start; cur.Add(slotDuration).Add(-time.Nanosecond).Before(tr.End); cur = cur.Add(slotDuration) {
		slotEnd := cur.Add(slotDuration)
		if !slotEnd.After(tr.End) {
			slots = append(slots, TimeRange{Start: cur, End: slotEnd})
		} else {
			break
		}
	}

	return slots, nil
}

// HasOverlap проверяет, пересекается ли newRange с existing.
// inclusive = true — касание концами считается пересечением.
func HasOverlap(
	newRange TimeRange,
	existing []TimeRange,
	inclusive bool,
) (bool, []TimeRange) {
	var conflicts []TimeRange

	for _, tr := range existing {
		if rangesOverlap(newRange, tr, inclusive) {
			conflicts = append(conflicts, tr)
		}
	}

	return len(conflicts) > 0, conflicts
}

func rangesOverlap(a, b TimeRange, inclusive bool) bool {
	if inclusive {
		// [a.Start, a.End] и [b.Start, b.End] пересекаются,
		// если a.Start <= b.End && b.Start <= a.End
		return !a.Start.After(b.End) && !b.Start.After(a.End)
	}

	// Полуоткрытые интервалы [Start, End)
	// пересекаются, если a.Start < b.End && b.Start < a.End
	return a.Start.Before(b.End) && b.Start.Before(a.End)
}

// ===== Recurring rules =====

type RecurrenceFrequency int

const (
	FreqDaily RecurrenceFrequency = iota
	FreqWeekly
)

type RecurringRule struct {
	Freq      RecurrenceFrequency
	Interval  int              // шаг: каждые Interval дней/недель (>=1)
	Weekdays  []time.Weekday   // для FreqWeekly
	StartTime time.Time        // начальное начало слота
	Duration  time.Duration    // длительность слота
	Until     *time.Time       // опционально: дата/время окончания
	Count     *int             // опционально: максимальное количество повторений
	// Исключения по датам (используем дату без времени).
	Exceptions map[time.Time]struct{}
}

// ExpandRecurringRule разворачивает правило повторений в набор интервалов
// внутри окна window. Интервалы, полностью лежащие вне window, отбрасываются.
func ExpandRecurringRule(rule RecurringRule, window TimeRange) ([]TimeRange, error) {
	if rule.Duration <= 0 {
		return nil, errors.New("recurring rule: duration must be positive")
	}
	if rule.Interval <= 0 {
		rule.Interval = 1
	}
	if rule.StartTime.IsZero() {
		return nil, errors.New("recurring rule: StartTime is required")
	}
	if !window.End.After(window.Start) {
		return []TimeRange{}, nil
	}

	var result []TimeRange
	countGenerated := 0

	cur := rule.StartTime

	for {
		// Ограничение по Until
		if rule.Until != nil && cur.After(*rule.Until) {
			break
		}
		// Ограничение по Count
		if rule.Count != nil && countGenerated >= *rule.Count {
			break
		}
		occStart := cur
		occEnd := cur.Add(rule.Duration)

		// Для weekly учитываем только нужные дни недели.
		if rule.Freq == FreqWeekly && len(rule.Weekdays) > 0 {
			if !containsWeekday(rule.Weekdays, occStart.Weekday()) {
				cur = nextOccurrence(rule, cur)
				continue
			}
		}

		// Проверка исключений по дате.
		if isException(rule, occStart) {
			cur = nextOccurrence(rule, cur)
			continue
		}

		occRange := TimeRange{Start: occStart, End: occEnd}

		// Если интервал пересекается с окном — включаем.
		if rangesOverlap(occRange, window, false) {
			result = append(result, occRange)
			countGenerated++
		} else if occEnd.After(window.End) && occStart.After(window.End) {
			// Дальнейшие повторения точно будут дальше окна.
			break
		}

		cur = nextOccurrence(rule, cur)
	}

	return result, nil
}

func nextOccurrence(rule RecurringRule, cur time.Time) time.Time {
	switch rule.Freq {
	case FreqDaily:
		return cur.AddDate(0, 0, rule.Interval)
	case FreqWeekly:
		return cur.AddDate(0, 0, 7*rule.Interval)
	default:
		return cur.AddDate(0, 0, rule.Interval)
	}
}

func containsWeekday(list []time.Weekday, w time.Weekday) bool {
	for _, d := range list {
		if d == w {
			return true
		}
	}
	return false
}

func isException(rule RecurringRule, t time.Time) bool {
	if rule.Exceptions == nil {
		return false
	}
	day := dateOnly(t)
	_, ok := rule.Exceptions[day]
	return ok
}

func dateOnly(t time.Time) time.Time {
	year, month, day := t.Date()
	return time.Date(year, month, day, 0, 0, 0, 0, t.Location())
}

// ===== Форматирование слота для пользователя =====

var ruWeekdays = map[time.Weekday]string{
	time.Monday:    "Понедельник",
	time.Tuesday:   "Вторник",
	time.Wednesday: "Среда",
	time.Thursday:  "Четверг",
	time.Friday:    "Пятница",
	time.Saturday:  "Суббота",
	time.Sunday:    "Воскресенье",
}

// FormatSlotForUser форматирует интервал в человекочитаемую строку.
// Если loc != nil, время переводится в указанный часовой пояс.
// Если includeID = true, в конце добавляется идентификатор слота в скобках.
func FormatSlotForUser(
	tr TimeRange,
	loc *time.Location,
	includeID bool,
	slotID string,
) string {
	start := tr.Start
	end := tr.End

	if loc != nil {
		start = start.In(loc)
		end = end.In(loc)
	}

	weekday := ruWeekdays[start.Weekday()]
	// Дата в формате ДД.ММ.ГГГГ
	dateStr := start.Format("02.01.2006")
	// Время в формате ЧЧ:ММ
	startTimeStr := start.Format("15:04")
	endTimeStr := end.Format("15:04")

	base := fmt.Sprintf("%s, %s, %s–%s", weekday, dateStr, startTimeStr, endTimeStr)

	if includeID && slotID != "" {
		return fmt.Sprintf("%s (ID: %s)", base, slotID)
	}

	return base
}

// ===== Валидация Telegram-пользователя =====

type UserStatus string

const (
	UserStatusActive   UserStatus = "active"
	UserStatusInactive UserStatus = "inactive"
	UserStatusBlocked  UserStatus = "blocked"
)

type UserRole string

const (
	UserRoleClient    UserRole = "client"
	UserRoleProvider  UserRole = "provider"
	UserRoleAdmin     UserRole = "admin"
	UserRoleUnknown   UserRole = "unknown"
)

// User описывает пользователя в системе.
type User struct {
	ID         int64
	TelegramID int64
	Role       UserRole
	Status     UserStatus
}

// ValidatedUser — результат валидации.
type ValidatedUser struct {
	ID         int64
	TelegramID int64
	Role       UserRole
	Status     UserStatus
}

// UserStore описывает источник данных о пользователях.
type UserStore interface {
	FindByTelegramID(ctx context.Context, telegramID int64) (*User, error)
}

// ValidateTelegramUser выполняет базовую валидацию Telegram-пользователя:
//   - проверяет корректность идентификатора;
//   - проверяет н
