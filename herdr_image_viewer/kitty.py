"""Kitty graphics escape sequences for the viewer's own pane.

Herdr 0.9.2 removed its pane.graphics API; pane programs now write standard
Kitty graphics, which Herdr renders.
"""

import base64


def transmit(image_id, z_index, col, row, data):
    """Move the cursor to the cell (0-based) and place the PNG data there.
    The image replaces any earlier one with the same id; Kitty sends no reply
    (q=2) and the cursor stays where it is (C=1)."""
    payload = base64.standard_b64encode(data).decode("ascii")
    return (f"\x1b[{row + 1};{col + 1}H"
            f"\x1b_Ga=T,f=100,i={image_id},z={z_index},C=1,q=2;{payload}\x1b\\")
