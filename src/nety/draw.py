"""Window to draw a signature with the mouse and save it as a transparent PNG."""

from PIL import Image, ImageDraw

from .config import ConfigError

WIDTH, HEIGHT = 500, 250
STROKE = 3
# Strokes are drawn at SCALE× then downsampled: Pillow lines are not anti-aliased.
SCALE = 4
MARGIN = 4


def render_png(strokes, size=(WIDTH, HEIGHT), width=STROKE):
    """Strokes (lists of (x, y) points) on a transparent image, cropped to the ink.

    Returns None when there is no ink.
    """
    big = Image.new("RGBA", (size[0] * SCALE, size[1] * SCALE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(big)
    w = width * SCALE
    for stroke in strokes:
        points = [(x * SCALE, y * SCALE) for x, y in stroke]
        if len(points) > 1:
            pen.line(points, fill="black", width=w, joint="curve")
        # Round caps and joints, and a dot for a single click.
        for x, y in points:
            pen.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill="black")
    image = big.resize(size, Image.Resampling.LANCZOS)
    box = image.getbbox()
    if not box:
        return None
    left, top, right, bottom = box
    return image.crop((max(left - MARGIN, 0), max(top - MARGIN, 0),
                       min(right + MARGIN, size[0]), min(bottom + MARGIN, size[1])))


def draw_signature(path):
    """Open the drawing window; save to `path` and return True, or False if cancelled."""
    try:
        import tkinter as tk
    except ImportError:
        raise ConfigError("drawing needs tkinter: install it for this Python "
                          "(e.g. python3-tkinter) or use a uv-managed Python")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise ConfigError(f"cannot open a window: {e}")

    strokes = []
    saved = False
    root.title("nety – signature")
    root.resizable(False, False)
    canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg="white", cursor="pencil",
                       highlightthickness=1, highlightbackground="grey")
    canvas.pack(padx=8, pady=8)
    tk.Label(root, text="Draw with the mouse. Enter saves, Esc cancels.").pack()

    def start(event):
        strokes.append([(event.x, event.y)])
        canvas.create_oval(event.x - STROKE / 2, event.y - STROKE / 2,
                           event.x + STROKE / 2, event.y + STROKE / 2,
                           fill="black", outline="")

    def move(event):
        x0, y0 = strokes[-1][-1]
        strokes[-1].append((event.x, event.y))
        canvas.create_line(x0, y0, event.x, event.y, width=STROKE, capstyle=tk.ROUND,
                           fill="black")

    def clear(_event=None):
        strokes.clear()
        canvas.delete("all")

    def save(_event=None):
        nonlocal saved
        image = render_png(strokes)
        if image is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG")
        saved = True
        root.destroy()

    canvas.bind("<ButtonPress-1>", start)
    canvas.bind("<B1-Motion>", move)
    buttons = tk.Frame(root)
    buttons.pack(pady=8)
    tk.Button(buttons, text="Clear", command=clear).pack(side=tk.LEFT, padx=4)
    tk.Button(buttons, text="Cancel", command=root.destroy).pack(side=tk.LEFT, padx=4)
    tk.Button(buttons, text="Save", command=save, default=tk.ACTIVE).pack(side=tk.LEFT, padx=4)
    root.bind("<Return>", save)
    root.bind("<Escape>", lambda _event: root.destroy())
    root.mainloop()
    return saved
