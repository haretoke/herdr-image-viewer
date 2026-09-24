"""Pure layout of the viewer pane, in terminal cells.

Row 0 is the title line. A tall pane (by pixel aspect) puts the thumbnail grid
below the main image, a wide pane puts it in columns on the right.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

THUMB_TARGET_PX = (96, 60)
GAP_COLS = 1
GRID_ROWS_BELOW = 2
GRID_COLUMNS_RIGHT = 2
MIN_THUMBS = 3
MIN_MAIN_COLS = 16
MIN_MAIN_ROWS = 4


@dataclass(frozen=True)
class Rect:
    col: int
    row: int
    cols: int
    rows: int


@dataclass(frozen=True)
class Grid:
    orientation: str
    columns: int
    rows: int
    cells: Tuple[Rect, ...]


@dataclass(frozen=True)
class Layout:
    main: Rect
    grid: Optional[Grid]


def thumb_size(cell_w, cell_h):
    """Thumbnail size in cells, close to THUMB_TARGET_PX."""
    return (
        max(1, round(THUMB_TARGET_PX[0] / cell_w)),
        max(1, round(THUMB_TARGET_PX[1] / cell_h)),
    )


def compute(cols, rows, cell_w, cell_h, count):
    """The layout for a pane, or None while its size or cell size is unknown."""
    if not all(isinstance(value, int) and value > 0 for value in (cols, rows, cell_w, cell_h)):
        return None
    thumb_cols, thumb_rows = thumb_size(cell_w, cell_h)
    if rows * cell_h >= cols * cell_w:
        result = below(cols, rows, thumb_cols, thumb_rows)
    else:
        result = right(cols, rows, thumb_cols, thumb_rows)
    too_small = (
        len(result.grid.cells) < MIN_THUMBS
        or result.main.cols < MIN_MAIN_COLS
        or result.main.rows < MIN_MAIN_ROWS
    )
    if count == 0 or too_small:
        # The whole pane below the title shows the image, or the placeholder.
        return Layout(main=Rect(col=0, row=1, cols=cols, rows=rows - 1), grid=None)
    return result


def below(cols, rows, thumb_cols, thumb_rows):
    """Row-major grid under the main image."""
    columns = (cols + GAP_COLS) // (thumb_cols + GAP_COLS)
    grid_top = rows - GRID_ROWS_BELOW * thumb_rows
    cells = tuple(
        Rect(col=c * (thumb_cols + GAP_COLS), row=grid_top + r * thumb_rows, cols=thumb_cols, rows=thumb_rows)
        for r in range(GRID_ROWS_BELOW)
        for c in range(columns)
    )
    main = Rect(col=0, row=1, cols=cols, rows=grid_top - 2)  # title above, separator below
    return Layout(main=main, grid=Grid("below", columns, GRID_ROWS_BELOW, cells))


def right(cols, rows, thumb_cols, thumb_rows):
    """Column-major grid right of the main image, following the history order."""
    per_column = (rows - 1) // thumb_rows
    width = GRID_COLUMNS_RIGHT * thumb_cols + (GRID_COLUMNS_RIGHT - 1) * GAP_COLS
    left = cols - width
    cells = tuple(
        Rect(col=left + c * (thumb_cols + GAP_COLS), row=1 + r * thumb_rows, cols=thumb_cols, rows=thumb_rows)
        for c in range(GRID_COLUMNS_RIGHT)
        for r in range(per_column)
    )
    main = Rect(col=0, row=1, cols=left - 1, rows=rows - 1)  # separator column before the grid
    return Layout(main=main, grid=Grid("right", GRID_COLUMNS_RIGHT, per_column, cells))
