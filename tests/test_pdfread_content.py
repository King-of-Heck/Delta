import pytest

from delta.pdfread.content import IDENT, apply, base_matrix, interpret, mul
from delta.pdfread.pdffonts import PdfFont
from delta.pdfread.types import PageData

F = PdfFont("ABCDEF+Arial", widths={65: 500.0, 66: 500.0, 32: 250.0})
LETTER = (0.0, 0.0, 612.0, 792.0)


def run(ops, fonts=None, xobjects=None, box=LETTER, rotation=0):
    box_w, box_h = box[2] - box[0], box[3] - box[1]
    page = PageData(*((box_h, box_w) if rotation in (90, 270) else (box_w, box_h)))
    interpret(ops, fonts if fonts is not None else {"/F1": F}, xobjects or {}, base_matrix(box, rotation), page)
    return page


def text_ops(*show, at=(72.0, 700.0), size=10.0):
    return [([], b"BT"), (["/F1", size], b"Tf"), (list(at), b"Td"), *show, ([], b"ET")]


def test_matrices_compose_left_to_right():
    shift, scale = (1, 0, 0, 1, 10, 20), (2, 0, 0, 2, 0, 0)
    assert apply(mul(shift, scale), 1, 1) == (22, 42)
    assert mul(IDENT, shift) == shift


def test_base_matrix_flips_y_and_honours_the_box_origin_and_rotation():
    assert apply(base_matrix(LETTER, 0), 72, 700) == (72, 92)
    assert apply(base_matrix((10, 20, 622, 812), 0), 82, 720) == (72, 92)
    assert apply(base_matrix(LETTER, 90), 0, 0) == (0, 0)          # bottom-left becomes top-left
    assert apply(base_matrix(LETTER, 90), 612, 0) == (0, 612)
    assert apply(base_matrix(LETTER, 180), 612, 0) == (0, 0)
    assert apply(base_matrix(LETTER, 270), 612, 792) == (0, 0)


def test_tj_gives_a_run_with_start_end_baseline_and_size():
    p = run(text_ops(([b"AB"], b"Tj")))
    (r,) = p.runs
    assert (r.text, r.font, r.size) == ("AB", "ABCDEF+Arial", 10.0)
    assert (r.x0, r.x1, r.y) == pytest.approx((72.0, 82.0, 92.0))
    assert (p.chars, p.bad_chars) == (2, 0)


def test_tj_array_moves_by_its_numbers_and_word_spacing_applies_to_spaces():
    p = run(text_ops(([2.0], b"Tw"), ([[b"A", -1000.0, b"A B"]], b"TJ")))
    a, b = p.runs
    assert (a.x0, a.x1) == pytest.approx((72.0, 77.0))
    assert b.x0 == pytest.approx(87.0)                       # 77 + 1000/1000 * 10
    assert b.x1 == pytest.approx(87.0 + 5 + 2.5 + 2 + 5)     # A, space + Tw, B


def test_td_tstar_quote_and_tm_position_lines():
    ops = [([], b"BT"), (["/F1", 10.0], b"Tf"), ([12.0], b"TL"), ([72.0, 700.0], b"Td"), ([b"A"], b"Tj"),
           ([], b"T*"), ([b"A"], b"Tj"), ([b"B"], b"'"), ([1.0, 0.0, 0.0, 1.0, 100.0, 500.0], b"Tm"), ([b"A"], b"Tj"),
           ([], b"ET")]
    ys = [(r.x0, r.y) for r in run(ops).runs]
    assert ys == pytest.approx([(72, 92), (72, 104), (72, 116), (100, 292)])


def test_cm_scales_position_and_size_and_q_restores():
    ops = [([], b"q"), ([2.0, 0.0, 0.0, 2.0, 0.0, 0.0], b"cm"), *text_ops(([b"A"], b"Tj"), at=(36.0, 350.0)),
           ([], b"Q"), *text_ops(([b"A"], b"Tj"))]
    big, normal = run(ops).runs
    assert (big.x0, big.y, big.size, big.x1) == pytest.approx((72.0, 92.0, 20.0, 82.0))
    assert normal.size == 10.0


def test_rotated_text_is_dropped_and_nul_text_is_empty():
    rot = [([], b"BT"), (["/F1", 10.0], b"Tf"), ([0.0, 1.0, -1.0, 0.0, 100.0, 100.0], b"Tm"), ([b"A"], b"Tj"), ([], b"ET")]
    p = run(rot)
    assert p.runs == [] and p.chars == 0             # dropped text is never counted either
    nul = PdfFont("N", to_unicode={65: "\x00"})
    assert run(text_ops(([b"A"], b"Tj")), fonts={"/F1": nul}).runs == []


def test_a_malformed_text_operand_is_ignored_and_the_good_runs_around_it_read_correctly():
    ops = [([], b"BT"), (["/F1", 10.0], b"Tf"), ([72.0, 700.0], b"Td"), ([b"A"], b"Tj"),
           (["/F1"], b"Tf"),               # malformed: Tf with one operand
           ([5.0], b"Tj"),                 # malformed: Tj with a number, not bytes
           ([b"B"], b"Tj"), ([], b"ET")]
    a, b = run(ops).runs
    assert (a.text, a.x0, a.x1) == ("A", 72.0, 77.0)
    assert (b.text, b.x0, b.x1) == ("B", 77.0, 82.0)


def test_an_unknown_font_name_still_reads_as_cp1252():
    p = run(text_ops(([b"Hi"], b"Tj")), fonts={})
    assert p.runs[0].text == "Hi" and p.runs[0].font == "Unknown"
    assert (p.chars, p.bad_chars) == (2, 2)          # counted as unreadable: its font was lost


def test_a_page_shown_entirely_through_an_unknown_font_is_unreadable():
    from delta.pdfread.extract import unreadable
    p = run(text_ops(([b"Hi"], b"Tj")), fonts={})
    assert unreadable(p)


def test_a_stroked_rectangle_gives_four_segments_and_a_line_gives_one():
    ops = [([72.0, 642.0, 200.0, -40.0], b"re"), ([], b"S"),
           ([172.0, 642.0], b"m"), ([172.0, 602.0], b"l"), ([], b"S")]
    segs = run(ops).segs
    assert len(segs) == 5
    assert sorted((s.x0, s.y0, s.x1, s.y1) for s in segs if s.horizontal) == [(72, 150, 272, 150), (72, 190, 272, 190)]
    assert (172, 150, 172, 190) in [(s.x0, s.y0, s.x1, s.y1) for s in segs]


def test_a_thin_filled_rectangle_is_a_segment_and_a_fat_one_is_shading():
    ops = [([72.0, 572.0, 200.0, -0.5], b"re"), ([], b"f"),          # a border drawn as a filled sliver
           ([72.0, 500.0, 200.0, -40.0], b"re"), ([], b"f"),         # cell shading
           ([300.0, 700.0], b"m"), ([300.6, 700.0], b"l"), ([300.6, 600.0], b"l"), ([300.0, 600.0], b"l"),
           ([], b"h"), ([], b"f")]                                   # a sliver drawn as a closed path
    segs = run(ops).segs
    assert [(s.x0, s.y0, s.x1, s.y1) for s in segs] == pytest.approx([(72, 220.25, 272, 220.25), (300.3, 92, 300.3, 192)])


def test_a_clip_path_is_not_a_border_and_images_and_forms_are_counted():
    ops = [([72.0, 642.0, 200.0, -40.0], b"re"), ([], b"W"), ([], b"n"),
           (["/Im1"], b"Do"), (["/Fm1"], b"Do"), ([{}], b"INLINE IMAGE")]
    p = run(ops, xobjects={"/Im1": "image", "/Fm1": "form"})
    assert p.segs == [] and p.images == 2 and p.forms == 1


def test_a_run_ending_in_spaces_has_its_ink_end_before_them():
    p = run(text_ops(([b"AB  "], b"Tj"), ([b"   "], b"Tj")))
    a, blank = p.runs
    assert a.x1 == pytest.approx(87.0)                       # the pen moves past the two spaces ...
    assert a.ink_x1 == pytest.approx(82.0)                   # ... the ink stops after the B
    assert blank.ink_x1 == pytest.approx(blank.x0)          # nothing inked: the ink ends where it starts


def _reference_runs(ops, fonts, base):
    """The text runs as the interpreter first worked them out, one mul() per glyph: the yardstick
    the written-out arithmetic must match to the last bit."""
    from delta.pdfread.content import _State
    from delta.pdfread.types import TextRun
    st, stack, tm, runs, unknown = _State(), [], IDENT, [], PdfFont("Unknown")
    for operands, op in ops:
        if op == b"q":
            stack.append(st.copy())
        elif op == b"Q":
            if stack:
                st = stack.pop()
        elif op == b"cm":
            st.ctm = mul(tuple(operands), st.ctm)
        elif op == b"Tf":
            st.font_name, st.size = operands[0], float(operands[1])
        elif op == b"Tc":
            st.tc = float(operands[0])
        elif op == b"Tw":
            st.tw = float(operands[0])
        elif op == b"Tz":
            st.th = float(operands[0]) / 100.0
        elif op == b"Ts":
            st.rise = float(operands[0])
        elif op == b"Tm":
            tm = tuple(operands)
        elif op == b"TJ":
            for item in operands[0]:
                if not isinstance(item, bytes):
                    tm = mul((1.0, 0.0, 0.0, 1.0, -float(item) / 1000.0 * st.size * st.th, 0.0), tm)
                    continue
                font = fonts.get(st.font_name, unknown)
                full = mul(st.ctm, base)
                m = mul(tm, full)
                x_start, y_start = apply(m, 0.0, st.rise)
                pieces, ink_tm = [], tm
                for code, text, w in font.decode(item):
                    pieces.append(text)
                    tx = (w / 1000.0 * st.size + st.tc + (st.tw if font.nbytes == 1 and code == 32 else 0.0)) * st.th
                    tm = mul((1.0, 0.0, 0.0, 1.0, tx, 0.0), tm)
                    if text.strip():
                        ink_tm = tm
                text = "".join(pieces).replace("\x00", "")
                upright = m[0] > 0 and m[3] < 0 and abs(m[1]) < 0.01 * abs(m[0]) and abs(m[2]) < 0.01 * abs(m[3])
                if text and upright:
                    runs.append(TextRun(text, x_start, apply(mul(tm, full), 0.0, st.rise)[0], y_start,
                                        st.size * abs(m[3]), font.name, apply(mul(ink_tm, full), 0.0, st.rise)[0]))
    return runs


def test_text_positions_match_the_one_matrix_per_glyph_arithmetic_to_the_last_bit():
    import random
    rng = random.Random(11)
    fonts = {"/F1": F, "/F2": PdfFont("XYZ+Times", widths={65: 722.0, 32: 250.0, 0: 0.0}, default_width=333.3)}
    vals = [0.0, -0.0, 1.0, -1.0, 0.1, 7.3, -12.25, 1e-9, 1000.0, 0.333]
    strings = [b"AB", b"A B", b"  ", b" A", b"A ", b"\x00", b"\x00A\x00", b"BBB  A", b"", b"Z"]
    memo = {}
    for _ in range(300):
        ops = [([], b"BT")]
        for _ in range(rng.randint(1, 12)):
            k = rng.randint(0, 7)
            if k == 0:
                ops.append(([rng.choice(["/F1", "/F2", "/F9"]), rng.choice([9.96, 12.0, -3.0, 0.0])], b"Tf"))
            elif k == 1:
                ops.append(([rng.choice(vals)], rng.choice([b"Tc", b"Tw", b"Ts"])))
            elif k == 2:
                ops.append(([rng.choice([100.0, 85.5, -50.0, 0.0])], b"Tz"))
            elif k == 3:
                ops.append(([rng.choice(vals + [1.0] * 6) for _ in range(6)], b"Tm"))
            elif k == 4:
                ops += [([], b"q"), ([rng.choice(vals + [1.0] * 6) for _ in range(6)], b"cm")]
            elif k == 5:
                ops.append(([], b"Q"))
            else:
                ops.append(([[rng.choice(strings + [rng.choice(vals), -250])
                              for _ in range(rng.randint(1, 5))]], b"TJ"))
        base = base_matrix(LETTER, rng.choice([0, 90, 180, 270]))
        page = PageData(612, 792)
        expected = _reference_runs(ops, fonts, base)
        interpret(ops, fonts, {}, base, page, memo)
        assert [repr(r) for r in page.runs] == [repr(r) for r in expected]
