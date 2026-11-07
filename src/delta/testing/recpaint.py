"""A Painter that records its calls as plain tuples (coordinates rounded to 2 decimals), so the
draw logic is tested exactly without fpdf2 or real fonts."""
from __future__ import annotations


def _r(v):
    return round(v, 2)


class RecordingPainter:
    def __init__(self):
        self.ops: list[tuple] = []
        self.pages = 0

    def page(self, w, h):
        self.pages += 1
        self.ops.append(("page", _r(w), _r(h)))

    def text(self, x, baseline, text, face, size, color, fake_bold=False, fake_italic=False, width=None, role=None):
        self.ops.append(("text", _r(x), _r(baseline), text, face.path, _r(size), color, fake_bold, fake_italic))

    def rule(self, x1, x2, y, thickness, color, dotted=False):
        self.ops.append(("rule", _r(x1), _r(x2), _r(y), _r(thickness), color, dotted))

    def line(self, x1, y1, x2, y2, width, color):
        self.ops.append(("line", _r(x1), _r(y1), _r(x2), _r(y2), _r(width), color))

    def box(self, x, y, w, h, color, role=None):
        self.ops.append(("box", _r(x), _r(y), _r(w), _r(h), color))

    def of(self, kind: str) -> list[tuple]:
        return [o for o in self.ops if o[0] == kind]

    def page_ops(self, n: int) -> list[tuple]:
        """The operations drawn on page n (1-based), excluding the page op itself."""
        starts = [i for i, o in enumerate(self.ops) if o[0] == "page"]
        if n > len(starts):
            return []
        end = starts[n] if n < len(starts) else len(self.ops)
        return self.ops[starts[n - 1] + 1:end]
