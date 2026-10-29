"""Contact sheets of review candidates, ordered by moment (used by the Claude Code skill)."""
import os

from PIL import Image, ImageDraw, ImageFont


def _font(size):
    for f in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheets(shoot, photos, per_sheet=9, cols=3, cell=560):
    """Write 3x3 sheets labelled '#id mMoment'; frames of one moment stay together."""
    out_dir = os.path.join(shoot.store.work, "sheets")
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, old))
    font = _font(34)
    sheets = []
    for s in range(0, len(photos), per_sheet):
        batch = photos[s:s + per_sheet]
        rows = (len(batch) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell, rows * cell), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for k, p in enumerate(batch):
            with Image.open(shoot.store.thumb_path(p["fp"])) as t:
                t = t.copy()
            t.thumbnail((cell - 12, cell - 12))
            x0, y0 = (k % cols) * cell, (k // cols) * cell
            sheet.paste(t, (x0 + (cell - t.width) // 2, y0 + (cell - t.height) // 2))
            label = f"#{p['id']}  m{p['moment']}"
            box = draw.textbbox((x0 + 10, y0 + 8), label, font=font)
            draw.rectangle([box[0] - 6, box[1] - 4, box[2] + 6, box[3] + 4], fill=(0, 0, 0))
            draw.text((x0 + 10, y0 + 8), label, fill=(255, 220, 0), font=font)
        path = os.path.join(out_dir, f"sheet_{len(sheets) + 1:02d}.jpg")
        sheet.save(path, "JPEG", quality=82)
        sheets.append({"path": path, "ids": [p["id"] for p in batch]})
    return sheets
