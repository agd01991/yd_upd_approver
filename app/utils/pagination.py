def page_items[T](items: list[T], page: int, per_page: int = 10) -> list[T]:
    start = max(page, 0) * per_page
    return items[start : start + per_page]
