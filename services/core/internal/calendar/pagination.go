package calendar

// Page описывает одну страницу элементов.
type Page[T any] struct {
	Items    []T // элементы на текущей странице
	Page     int // номер страницы (с 1)
	PageSize int // количество элементов на странице
	HasNext  bool
	HasPrev  bool
	Total    int // общее количество элементов
}

// Paginate возвращает срез items для указанной страницы и метаданные.
// page нумеруется с 1. При некорректных значениях используются дефолты.
func Paginate[T any](items []T, page, pageSize int) Page[T] {
	const defaultPageSize = 10

	total := len(items)

	if pageSize <= 0 {
		pageSize = defaultPageSize
	}
	if page <= 0 {
		page = 1
	}

	start := (page - 1) * pageSize
	if start > total {
		start = total
	}

	end := start + pageSize
	if end > total {
		end = total
	}

	pageItems := items[start:end]

	hasPrev := page > 1
	hasNext := end < total

	return Page[T]{
		Items:    pageItems,
		Page:     page,
		PageSize: pageSize,
		HasNext:  hasNext,
		HasPrev:  hasPrev,
		Total:    total,
	}
}

// SlicePage — синоним Paginate, оставлен для совместимости со спецификацией.
func SlicePage[T any](items []T, page, pageSize int) Page[T] {
	return Paginate(items, page, pageSize)
}
