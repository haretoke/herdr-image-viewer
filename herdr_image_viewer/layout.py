"""Pure layout of the viewer pane, in terminal cells.

Row 0 is the title line. A tall pane (by pixel aspect) puts the thumbnail grid
below the main image, a wide pane puts it in columns on the right.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

THUMB_TARGET_PX = (96, 60)
GAP_COLS = 1
GRID_ROWS_BELOW = 2


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
    thumb_cols, thumb_rows = thumb_size(cell_w, cell_h)
    columns = (cols + GAP_COLS) // (thumb_cols + GAP_COLS)
    grid_top = rows - GRID_ROWS_BELOW * thumb_rows
    cells = tuple(
        Rect(col=c * (thumb_cols + GAP_COLS), row=grid_top + r * thumb_rows, cols=thumb_cols, rows=thumb_rows)
        for r in range(GRID_ROWS_BELOW)
        for c in range(columns)
    )
    main = Rect(col=0, row=1, cols=cols, rows=grid_top - 2)  # title above, separator below
    return Layout(main=main, grid=Grid("below", columns, GRID_ROWS_BELOW, cells))
