"""Copy a matplotlib figure to the Windows clipboard as an image, so it can be
pasted directly into PowerPoint/Word/etc. without a screenshot."""
from __future__ import annotations

import io

from PIL import Image


def figure_to_image(figure, dpi: int = 150) -> Image.Image:
    buf = io.BytesIO()
    figure.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    buf.seek(0)
    image = Image.open(buf).convert("RGB")
    buf.close()
    return image


def copy_image_to_windows_clipboard(image: Image.Image) -> None:
    import win32clipboard

    output = io.BytesIO()
    image.save(output, "BMP")
    data = output.getvalue()[14:]  # strip the 14-byte BMP file header — clipboard wants a raw DIB
    output.close()

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()


def copy_figure_to_clipboard(figure, dpi: int = 150) -> None:
    """Render the figure to PNG, convert it, and place it on the Windows clipboard
    as an image (CF_DIB) so a plain Ctrl+V in PowerPoint/Word pastes the picture."""
    copy_image_to_windows_clipboard(figure_to_image(figure, dpi=dpi))
