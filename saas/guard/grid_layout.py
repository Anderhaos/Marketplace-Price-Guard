"""Pure layout spans for seller cards; no database or API access."""


def _balanced_grid_spans(item_count):
    """Twelve-column rows of 2–4 equal cards, without a stranded last card.

    Six cards become three per row, eight cards four per row; a partial
    final row expands to the complete container width. CSS handles tablet
    (two columns) and phone (one column) independently.
    """
    if item_count <= 0:
        return []
    rows = (item_count + 3) // 4
    base, extra = divmod(item_count, rows)
    spans_by_count = {1: 12, 2: 6, 3: 4, 4: 3}
    spans = []
    for row in range(rows):
        columns = base + (1 if row < extra else 0)
        spans.extend([spans_by_count[columns]] * columns)
    return spans
