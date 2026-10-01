"""Kitty graphics escape sequences for the viewer's own pane.

Herdr 0.9.2 removed its pane.graphics API; pane programs now write standard
Kitty graphics, which Herdr renders.
"""

import base64

CHUNK_BYTES = 4096  # base64 per escape sequence, the Kitty protocol's limit


def transmit(image_id, z_index, col, row, data):
    """Move the cursor to the cell (0-based) and place the PNG data there.
    The image replaces any earlier one with the same id; Kitty sends no reply
    (q=2) and the cursor stays where it is (C=1). Data longer than one chunk
    goes in several escapes, m=1 on all but the last."""
    payload = base64.standard_b64encode(data).decode("ascii")
    chunks = [payload[start:start + CHUNK_BYTES] for start in range(0, len(payload), CHUNK_BYTES)] or [""]
    keys = f"a=T,f=100,i={image_id},z={z_index},C=1,q=2"
    if len(chunks) == 1:
        return f"\x1b[{row + 1};{col + 1}H\x1b_G{keys};{payload}\x1b\\"
    escapes = [f"\x1b_G{keys},m=1;{chunks[0]}\x1b\\"]
    escapes += [f"\x1b_Gm=1;{chunk}\x1b\\" for chunk in chunks[1:-1]]
    escapes.append(f"\x1b_Gm=0;{chunks[-1]}\x1b\\")
    return f"\x1b[{row + 1};{col + 1}H" + "".join(escapes)
