"""Physical layout geometry for the Halcyon Corne rev2 -- column stagger
and thumb cluster positions. Ported from halcyon-corne's Corne Palette
Editor artifact tool (same board, same visual layout, different renderer).

Coordinates are computed in an abstract unit grid; hud_window.py scales
them to actual pixels.
"""
from dataclasses import dataclass

LEFT_COLS_VISUAL = [0, 1, 2, 3, 4, 5]
RIGHT_COLS_VISUAL = [5, 4, 3, 2, 1, 0]
# Column stagger offset (in row-height units), keyed by matrix column.
# Same for both halves -- column 0 is each half's own outer/pinky edge.
COL_OFFSET = {0: 0.44, 1: 0.36, 2: 0.15, 3: 0.0, 4: 0.11, 5: 0.29}

LEFT_ROWS = [0, 1, 2]
RIGHT_ROWS = [5, 6, 7]

# Thumb cluster: (row, col, rotation_degrees), in the order they read
# visually left-to-right on each half.
LEFT_THUMB = [(3, 3, -12), (3, 4, -4), (3, 5, 6)]
RIGHT_THUMB = [(8, 5, -6), (8, 4, 4), (8, 3, 12)]

HALF_GAP_UNITS = 2.0  # gap between the two halves, in row-height units


@dataclass(frozen=True)
class KeyPos:
    row: int
    col: int
    x: float  # column position, in row-height units
    y: float  # row position, in row-height units
    rotation: float  # degrees, 0 for main keys


def compute_positions():
    """Returns a list of KeyPos covering all 42 real keys, in abstract
    row-height units. (0, 0) is the top-left of the left half's bounding
    box; x increases rightward across both halves including the gap."""
    positions = []

    def half(cols_visual, rows, x_start):
        for visual_i, matrix_col in enumerate(cols_visual):
            x = x_start + visual_i
            y_off = COL_OFFSET[matrix_col]
            for row_i, matrix_row in enumerate(rows):
                positions.append(KeyPos(matrix_row, matrix_col, x, row_i + y_off, 0.0))

    half(LEFT_COLS_VISUAL, LEFT_ROWS, x_start=0.0)
    right_x_start = len(LEFT_COLS_VISUAL) + HALF_GAP_UNITS
    half(RIGHT_COLS_VISUAL, RIGHT_ROWS, x_start=right_x_start)

    # Thumb clusters sit below the main block, offset inward toward the gap.
    thumb_y = len(LEFT_ROWS) + 0.6
    for i, (row, col, rot) in enumerate(LEFT_THUMB):
        x = (len(LEFT_COLS_VISUAL) - len(LEFT_THUMB)) + i
        positions.append(KeyPos(row, col, x, thumb_y, rot))
    for i, (row, col, rot) in enumerate(RIGHT_THUMB):
        x = right_x_start + i
        positions.append(KeyPos(row, col, x, thumb_y, rot))

    return positions


def bounding_size():
    """(width, height) in row-height units, for sizing the HUD window."""
    positions = compute_positions()
    max_x = max(p.x for p in positions) + 1.0
    max_y = max(p.y for p in positions) + 1.0
    return max_x, max_y
