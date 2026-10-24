"""Saving and printing keep the scan's own text layer.

The exported file used to be a picture of each page and nothing else — no
fonts, no text, no annotations.  That costs more than searchability: handed a
PDF with no text in it at all, a reader is entitled to conclude there is text
to be recovered, and older Acrobat duly runs its own OCR over the pages and
then prints the "suspect word" highlighting it produces, all over the opinion.

So the pages are cropped by tightening their boxes — lossless, and the text
comes with them — and a redacted case.law scan's black bars are painted out
*on the page* rather than on a picture of it, with the running head lettered
back on as real text.

These exercise the real pdfium code against a stand-in for a case.law scan: a
page picture, an invisible OCR layer over it, and black bars where West's
headnotes were.
"""

import io
import os
import tempfile
import unittest
from types import SimpleNamespace

try:
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import letter
    _READY = True
except ImportError:  # pragma: no cover - exercised on a bare checkout
    _READY = False

try:  # the saved-margin tests need only pdfium (and the app's tkinter)
    import pypdfium2 as pdfium
    import courtlistener_gui as g
    _HAVE_PDFIUM = True
except ImportError:  # pragma: no cover - exercised on a bare checkout
    _HAVE_PDFIUM = False


LINES = [
    "FRIGALIMENT IMPORTING CO. v. B. N. S. INTERNATIONAL SALES CORP.",
    "",
    "   The issue is, what is chicken?  Plaintiff says chicken means a",
    "young chicken, suitable for broiling and frying.  Defendant says",
    "chicken means any bird of that genus that meets contract",
    "specifications on weight and quality, including what it calls stewing",
    "chicken and plaintiff pejoratively terms fowl.",
]


def _serif(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _page_picture(page: int, bars: bool) -> "Image.Image":
    img = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(img)
    font = _serif(30)
    y = 200
    if bars:
        for _ in range(3):                       # West headnotes, blacked out
            d.rectangle([200, y, 1500, y + 70], fill=(0, 0, 0))
            y += 90
        d.rectangle([560, 110, 1140, 150], fill=(0, 0, 0))     # running head
    for line in LINES:
        d.text((200, y), line, font=font, fill=(20, 20, 20))
        y += 44
    d.text((820, 2080), str(115 + page), font=font, fill=(20, 20, 20))
    return img


def _redacted_scan(pages: int = 2, bars: bool = True) -> bytes:
    """A stand-in for a static.case.law scan."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    for p in range(pages):
        c.drawImage(ImageReader(_page_picture(p, bars)), 0, 0,
                    width=612, height=792)
        t = c.beginText()
        t.setTextRenderMode(3)                   # the invisible OCR layer
        t.setFont("Times-Roman", 11)
        y = 792 - 160
        for line in LINES:
            t.setTextOrigin(72, y)
            t.textLine(line)
            y -= 16
        c.drawText(t)
        c.showPage()
    c.save()
    return buf.getvalue()


def _text_of(path: str, page: int = 0) -> str:
    doc = pdfium.PdfDocument(path)
    tp = doc[page].get_textpage()
    try:
        return tp.get_text_range()
    finally:
        tp.close()


def _dark_pixels(path: str, page: int = 0, below: int = 40) -> int:
    doc = pdfium.PdfDocument(path)
    img = doc[page].render(scale=2).to_pil().convert("L")
    return sum(1 for v in img.convert('L').tobytes() if v < below)


@unittest.skipUnless(_READY, "needs pypdfium2, Pillow and reportlab")
class LosslessOutputTests(unittest.TestCase):
    """_paint_out_pdf_redactions and the crop, on a scan with an OCR layer."""

    @classmethod
    def setUpClass(cls):
        cls.data = _redacted_scan()
        cls.frac = [(0.10, 0.04, 0.90, 0.96)] * 2

    def setUp(self):
        fh = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        fh.close()
        self.path = fh.name
        self.addCleanup(lambda: os.path.exists(self.path)
                        and os.unlink(self.path))

    def _plan(self, header: str = ""):
        """What the pane's detector would report, computed the same way."""
        plan = []
        doc = pdfium.PdfDocument(io.BytesIO(self.data))
        for i in range(len(doc)):
            page = doc[i]
            img = page.render(scale=110 / 72).to_pil().convert("RGB")
            tp = page.get_textpage()
            text = tp.get_text_range()
            tp.close()
            boxes = g._whiten_pdf_redactions(img)
            W, H = img.size
            head = g._header_redaction_box(boxes, img.size) if header else None
            plan.append({
                "boxes": tuple((l / W, t / H, r / W, b / H)
                               for l, t, r, b in boxes),
                "head": (head[0] / W, head[1] / H, head[2] / W, head[3] / H)
                        if head else None,
                "drop": g._page_fully_redacted(text, boxes, img.size),
            })
        return plan

    def test_the_fixture_really_is_a_scan_with_an_ocr_layer(self):
        doc = pdfium.PdfDocument(io.BytesIO(self.data))
        self.assertTrue(g._page_has_scan_background(doc[0]))
        tp = doc[0].get_textpage()
        self.assertGreater(tp.count_chars(), 100)
        tp.close()

    def test_a_full_page_picture_is_recognised_as_a_scan(self):
        # The object-bounds accessor has been called two things by pypdfium2;
        # asking for the wrong one used to leave this test never firing.
        doc = pdfium.PdfDocument(io.BytesIO(self.data))
        for obj in doc[0].get_objects():
            bounds = g._pdf_object_bounds(obj)
            if bounds is not None:
                self.assertEqual(len(bounds), 4)
                break
        else:
            self.fail("no page object reported its bounds")

    def test_painting_out_the_bars_keeps_the_text(self):
        out, _kept = g._paint_out_pdf_redactions(self.data, self._plan())
        with open(self.path, "wb") as fh:
            fh.write(out)
        self.assertIn("pejoratively terms fowl", _text_of(self.path))

    def test_and_actually_removes_them(self):
        before = _dark_pixels_bytes(self.data)
        out, _kept = g._paint_out_pdf_redactions(self.data, self._plan())
        with open(self.path, "wb") as fh:
            fh.write(out)
        self.assertLess(_dark_pixels(self.path), before / 4)

    def test_the_running_head_is_lettered_back_on_as_real_text(self):
        out, _kept = g._paint_out_pdf_redactions(
            self.data, self._plan("190 F. Supp. 116"),
            header_text="190 F. Supp. 116")
        with open(self.path, "wb") as fh:
            fh.write(out)
        self.assertIn("190 F. Supp. 116", _text_of(self.path))

    def test_a_page_the_redaction_emptied_is_dropped(self):
        plan = self._plan()
        plan[0]["drop"] = True
        out, kept = g._paint_out_pdf_redactions(
            self.data, plan, [(0, 0, 1, 1), (0.1, 0.1, 0.9, 0.9)])
        doc = pdfium.PdfDocument(io.BytesIO(out))
        self.assertEqual(len(doc), 1)
        self.assertEqual(kept, [(0.1, 0.1, 0.9, 0.9)])

    def test_a_scan_is_cropped_to_the_viewers_ink_not_its_ocr_layer(self):
        # The OCR glyphs sit wherever the recognizer put them, which is not
        # the printed page's margins; cropping to them throws the scan away.
        out = g._crop_pdf_to_content(self.data, frac_boxes=self.frac)
        doc = pdfium.PdfDocument(io.BytesIO(out))
        width, height = doc[0].get_size()
        self.assertGreater(width, 400)
        self.assertGreater(height, 600)

    def test_a_page_with_no_ink_box_is_left_at_full_size(self):
        out = g._crop_pdf_to_content(self.data, frac_boxes=[None, None])
        doc = pdfium.PdfDocument(io.BytesIO(out))
        self.assertEqual(tuple(round(v) for v in doc[0].get_size()), (612, 792))

    def test_the_crop_keeps_every_word(self):
        out = g._crop_pdf_to_content(self.data, frac_boxes=self.frac)
        with open(self.path, "wb") as fh:
            fh.write(out)
        self.assertIn("INTERNATIONAL SALES CORP.", _text_of(self.path))

    def test_the_written_file_is_never_a_bare_picture(self):
        # What sends older Acrobat off to OCR the page itself.
        out, _kept = g._paint_out_pdf_redactions(
            self.data, self._plan("190 F. Supp. 116"),
            header_text="190 F. Supp. 116")
        with open(self.path, "wb") as fh:
            fh.write(g._crop_pdf_to_content(out, frac_boxes=self.frac))
        doc = pdfium.PdfDocument(self.path)
        tp = doc[0].get_textpage()
        try:
            self.assertGreater(tp.count_chars(), 100)
        finally:
            tp.close()


def _born_digital(slug: bool = True, rule: bool = False) -> bytes:
    """A born-digital letter page, written by hand (no reportlab needed): a
    visible block of text, the invisible printer's slug GovInfo's U.S.
    Reports carry above the running head (text render mode 3), and (with
    *rule*) a line drawn well below the text."""
    return _pdf_of([_page_ops(LINES, slug=slug, rule=rule)])


def _page_ops(lines: list, slug: bool = False, rule: bool = False,
              bands: bool = False) -> bytes:
    """One page's content: *lines* of text from near the top, and what the
    flags add.  *bands* lays white boxes the width of the page across its
    head and foot, as the Court's own PDFs do."""
    ops = ["BT /F1 11 Tf 14 TL 150 600 Td"]
    ops += [f"({line}) Tj T*" for line in lines]
    ops.append("ET")
    if slug:
        ops.append("BT 3 Tr /F1 8 Tf 150 770 Td "
                   "(553US2 Unit: $U42 [11-26-12 13:39:59] PAGES PGT: OPIN) "
                   "Tj ET")
    if rule:
        ops.append("3 w 150 300 m 400 300 l S")
    if bands:
        ops.append("q 1 1 1 rg 0 0 612 50 re f 0 742 612 50 re f Q")
    return "\n".join(ops).encode("latin-1")


def _pdf_of(streams: list) -> bytes:
    """A letter-size PDF with a page for each content stream."""
    kids = " ".join(f"{4 + 2 * k} 0 R" for k in range(len(streams)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (kids.encode(), len(streams)),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
    ]
    for k, stream in enumerate(streams):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
            % (5 + 2 * k))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream"
                       % (len(stream), stream))
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)


def _box(data: bytes) -> tuple:
    doc = pdfium.PdfDocument(io.BytesIO(data))
    return tuple(doc[0].get_mediabox())


@unittest.skipUnless(_HAVE_PDFIUM, "needs pypdfium2 and tkinter")
class SavedMarginTests(unittest.TestCase):
    """A born-digital page saved is framed as the viewer shows it."""

    def test_invisible_text_is_no_part_of_the_page(self):
        # Counting the slug left a band of blank page atop every page saved.
        l, b, r, t = _box(g._crop_pdf_to_content(_born_digital()))
        self.assertLess(t, 640)

    def test_a_drawn_figure_is_not_cropped_away(self):
        l, b, r, t = _box(g._crop_pdf_to_content(_born_digital(rule=True)))
        self.assertLessEqual(b, 300)

    def test_the_viewers_margin_carries_over(self):
        # The U.S. Reports are shown on a margin three times the default:
        # 54 pixels beside a type block drawn 792 wide.
        data = _born_digital(slug=False)
        doc = pdfium.PdfDocument(io.BytesIO(data))
        cl, cb, cr, ct = g._page_content_box_pts(doc[0])
        ratio = 54 / 792
        l, b, r, t = _box(g._crop_pdf_to_content(data, margin_ratio=ratio))
        margin = (cr - cl) * ratio
        self.assertAlmostEqual(cl - l, margin, places=1)
        self.assertAlmostEqual(t - ct, margin, places=1)
        # Without it, the old close crop.
        l, b, r, t = _box(g._crop_pdf_to_content(data))
        self.assertAlmostEqual(cl - l, 7.0, places=1)

    def test_the_share_is_the_viewers_at_its_fit_width(self):
        # Fixed in pixels, a margin is a smaller share of a zoomed-in page;
        # the file saved is framed alike at any zoom.
        pane = SimpleNamespace(_margin=54, _base_w=900, _target_w=1800)
        self.assertAlmostEqual(g._PdfPane._margin_ratio(pane), 54 / 792)


@unittest.skipUnless(_HAVE_PDFIUM, "needs pypdfium2 and tkinter")
class CourtPdfFramingTests(unittest.TestCase):
    """The Court's own PDFs — its slip opinions in the U.S. Reports' form,
    preliminary prints and bound volumes — are saved framed as the viewer
    shows them, not on the page as published."""

    OPINION = LINES * 3      # enough type for the viewer to measure

    def _viewer_boxes(self, data: bytes) -> list:
        """The boxes the viewer frames each page in, an opinion's pages
        sharing one as they do on screen."""
        pane = SimpleNamespace(_meta=g._PdfPane.measure_pdf(data))
        g._PdfPane._apply_uniform_crop(pane)
        return [m[2] for m in pane._meta]

    def _sizes(self, data: bytes) -> list:
        doc = pdfium.PdfDocument(io.BytesIO(data))
        return [tuple(round(v) for v in doc[i].get_size())
                for i in range(len(doc))]

    def test_a_drawing_that_puts_down_no_ink_is_no_part_of_the_page(self):
        # The white boxes across the head and foot left every page whole.
        data = _pdf_of([_page_ops(self.OPINION, bands=True)])
        out = g._crop_pdf_to_content(data,
                                     frac_boxes=self._viewer_boxes(data))
        l, b, r, t = _box(out)
        self.assertGreater(b, 250)
        self.assertLess(t, 640)

    def test_a_drawing_the_viewer_sees_still_counts(self):
        data = _pdf_of([_page_ops(self.OPINION, rule=True, bands=True)])
        out = g._crop_pdf_to_content(data,
                                     frac_boxes=self._viewer_boxes(data))
        l, b, r, t = _box(out)
        self.assertLessEqual(b, 298)

    def test_the_pages_of_an_opinion_are_saved_one_size(self):
        # Cropped page by page, the short last page came out a short sheet,
        # which a printer fitting it to the paper set in larger type.
        data = _pdf_of([_page_ops(self.OPINION, bands=True),
                        _page_ops(LINES * 2, bands=True)])
        out = g._crop_pdf_to_content(data,
                                     frac_boxes=self._viewer_boxes(data))
        first, last = self._sizes(out)
        self.assertEqual(first, last)
        self.assertLess(first[0], 500)

    def test_a_page_too_bare_to_measure_is_framed_with_the_rest(self):
        # The viewer shows such a page whole, unsure of so little type; its
        # own box alone made a scrap of a page.
        data = _pdf_of([_page_ops(self.OPINION), _page_ops(self.OPINION),
                        _page_ops(["It is so ordered."])])
        boxes = self._viewer_boxes(data)
        self.assertEqual(boxes[2], (0.0, 0.0, 1.0, 1.0))
        sizes = self._sizes(g._crop_pdf_to_content(data, frac_boxes=boxes))
        self.assertEqual(sizes[2], sizes[0])

    def test_the_margin_is_the_viewers(self):
        data = _pdf_of([_page_ops(self.OPINION, bands=True)])
        out = g._crop_pdf_to_content(data, frac_boxes=self._viewer_boxes(data),
                                     margin_ratio=54 / 792)
        doc = pdfium.PdfDocument(io.BytesIO(data))
        cl, cb, cr, ct = g._page_content_box_pts(
            doc[0], visible=g._frac_box_to_points(
                self._viewer_boxes(data)[0], (0, 0, 612, 792)))
        l, b, r, t = _box(out)
        self.assertAlmostEqual(cl - l, (cr - cl) * 54 / 792, places=1)


def _dark_pixels_bytes(data: bytes, page: int = 0, below: int = 40) -> int:
    doc = pdfium.PdfDocument(io.BytesIO(data))
    img = doc[page].render(scale=2).to_pil().convert("L")
    return sum(1 for v in img.convert('L').tobytes() if v < below)


def _bordered_picture(paper=(255, 255, 255), ink=(20, 20, 20),
                      shadow: bool = True) -> "Image.Image":
    """A page scanned from a bound volume: its print, and — *shadow* — the
    uneven black of what lay around it, as on Palsgraf v. Long Island R.R.
    Co., 248 N.Y. 339: a strip down the binding side, blots at two corners,
    a ragged band along the foot."""
    img = Image.new("RGB", (850, 1100), paper)
    d = ImageDraw.Draw(img)
    for k, line in enumerate(LINES * 3):
        d.text((170, 200 + 30 * k), line, font=_serif(22), fill=ink)
    if shadow:
        d.polygon([(0, 0), (60, 0), (35, 400), (70, 700), (25, 1100),
                   (0, 1100)], fill=(10, 10, 10))
        d.ellipse((780, -40, 900, 90), fill=(15, 15, 15))
        d.polygon([(0, 1060), (300, 1040), (520, 1075), (850, 1050),
                   (850, 1100), (0, 1100)], fill=(25, 25, 25))
        # The gray fading off the shadow.
        d.line([(62, 0), (38, 400), (73, 700), (28, 1100)],
               fill=(150, 150, 150), width=4)
    return img


def _scan_of(picture: "Image.Image", in_form: bool = False) -> bytes:
    """A one-page scan of *picture* with an invisible OCR line, written by
    pdfium itself; *in_form* sets the picture alone in a form object, as
    GovInfo's scans are."""
    import pypdfium2.raw as raw

    def picture_on(doc, page) -> None:
        image = pdfium.PdfImage.new(doc)
        bitmap = pdfium.PdfBitmap.from_pil(picture)
        image.set_bitmap(bitmap)
        bitmap.close()
        image.set_matrix(pdfium.PdfMatrix(612, 0, 0, 792, 0, 0))
        page.insert_obj(image)
        page.gen_content()

    doc = pdfium.PdfDocument.new()
    page = doc.new_page(612, 792)
    if in_form:
        source = pdfium.PdfDocument.new()
        picture_on(source, source.new_page(612, 792))
        xobject = raw.FPDF_NewXObjectFromPage(doc.raw, source.raw, 0)
        form = raw.FPDF_NewFormObjectFromXObject(xobject)
        raw.FPDFPage_InsertObject(page.raw, form)
        raw.FPDF_CloseXObject(xobject)
    else:
        picture_on(doc, page)
    text, _size = g._pdf_new_text(doc, " ".join(LINES[2:4]), size=11)
    raw.FPDFTextObj_SetTextRenderMode(text, 3)      # the OCR layer
    raw.FPDFPageObj_Transform(text, 1, 0, 0, 1, 122, 640)
    raw.FPDFPage_InsertObject(page.raw, text)
    page.gen_content()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _stand_in_pane(data: bytes):
    """A PDF pane's state as exporting reads it, without its window."""
    pane = object.__new__(g._PdfPane)
    pane._pdf_bytes = data
    pane._doc = pdfium.PdfDocument(io.BytesIO(data))
    pane._meta = g._PdfPane._measure(pane._doc)
    pane._margin, pane._base_w = 12, 700
    return pane


def _edge_darkness(data: bytes, page: int = 0) -> float:
    """The share of dark pixels in a band along the page's edges."""
    img = pdfium.PdfDocument(io.BytesIO(data))[page].render(
        scale=1).to_pil().convert("L")
    W, H = img.size
    band = [img.crop(b) for b in ((0, 0, W, H // 20), (0, H - H // 20, W, H),
                                  (0, 0, W // 20, H), (W - W // 20, 0, W, H))]
    dark = sum(sum(1 for v in b.tobytes() if v < 100) for b in band)
    return dark / sum(b.width * b.height for b in band)


@unittest.skipUnless(_HAVE_PDFIUM, "needs pypdfium2 and tkinter")
class ScanBorderTests(unittest.TestCase):
    """A scan's black border is painted out when it is saved — not the
    print it framed, and not its text layer."""

    def test_the_border_is_the_dark_joined_to_the_edge(self):
        gray = _bordered_picture().convert("L")
        mask = g._scan_border_mask(gray)
        self.assertIsNotNone(mask)
        # The shadow down the binding, the corner, the foot, and the gray
        # fading off them…
        for xy in ((5, 300), (40, 395), (840, 10), (400, 1090), (61, 2)):
            with self.subTest(xy=xy):
                self.assertEqual(mask.getpixel(xy), 255)
        # …but none of the print.
        print_box = gray.crop((150, 180, 800, 900))
        inked = [i for i, v in enumerate(print_box.tobytes()) if v < 100]
        self.assertTrue(inked)
        held = mask.crop((150, 180, 800, 900)).tobytes()
        self.assertFalse(any(held[i] for i in inked))

    def test_a_clean_page_has_none(self):
        self.assertIsNone(g._scan_border_mask(
            _bordered_picture(shadow=False).convert("L")))

    def test_a_shadow_over_the_print_is_kept_to_the_edges(self):
        # A shadow so deep it runs into the lines is cut back to a band.
        img = _bordered_picture()
        ImageDraw.Draw(img).rectangle((0, 400, 600, 430), fill=(10, 10, 10))
        mask = g._scan_border_mask(img.convert("L"))
        self.assertEqual(mask.getpixel((20, 415)), 255)
        self.assertEqual(mask.getpixel((400, 415)), 0)

    def test_its_boxes_cover_it(self):
        from PIL import Image as _Image
        mask = _Image.new("L", (10, 4), 0)
        for x in range(0, 3):
            for y in range(4):
                mask.putpixel((x, y), 255)
        mask.putpixel((9, 0), 255)
        self.assertEqual(sorted(g._mask_boxes(mask)),
                         [(0.0, 0.0, 0.3, 1.0), (0.9, 0.0, 1.0, 0.25)])

    def test_saved_the_border_is_gone_and_the_text_kept(self):
        # As dark, saved, as the page scanned clean — its print and no more.
        clean = _dark_pixels_bytes(_scan_of(_bordered_picture(shadow=False)),
                                   below=100)
        data = _scan_of(_bordered_picture())
        self.assertGreater(_edge_darkness(data), 0.05)
        self.assertGreater(_dark_pixels_bytes(data, below=100), 1.5 * clean)
        fh = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        fh.close()
        self.addCleanup(os.unlink, fh.name)

        def saved(**kw) -> bytes:
            _stand_in_pane(data).export_cropped_pdf(fh.name, **kw)
            with open(fh.name, "rb") as saved_file:
                return saved_file.read()

        out = saved(clean_borders=True)
        self.assertLess(abs(_dark_pixels_bytes(out, below=100) - clean),
                        0.05 * clean)
        tp = pdfium.PdfDocument(io.BytesIO(out))[0].get_textpage()
        try:
            self.assertIn("what is chicken", tp.get_text_range())
        finally:
            tp.close()
        # Asked not to, it leaves the border be.
        self.assertGreater(_dark_pixels_bytes(saved(), below=100),
                           1.5 * clean)

    def test_a_typeset_page_is_not_looked_at(self):
        doc = pdfium.PdfDocument(io.BytesIO(_born_digital()))
        self.assertFalse(g._page_is_scan(doc[0]))
        doc = pdfium.PdfDocument(io.BytesIO(_scan_of(_bordered_picture())))
        self.assertTrue(g._page_is_scan(doc[0]))


@unittest.skipUnless(_HAVE_PDFIUM, "needs pypdfium2 and tkinter")
class BlackAndWhiteTests(unittest.TestCase):
    """A volume of the Statutes at Large saved in black and white: its
    yellowed paper white, its print black, its OCR text kept."""

    YELLOWED, GRAY_INK = (226, 202, 179), (70, 62, 55)

    def test_the_paper_goes_white_and_the_print_black(self):
        picture = _bordered_picture(self.YELLOWED, self.GRAY_INK,
                                    shadow=False)
        out = g._ink_on_white(picture)
        self.assertEqual(out.mode, "L")
        self.assertEqual(out.getpixel((600, 1000)), 255)       # the paper
        darkest = min(out.crop((150, 180, 800, 900)).tobytes())
        self.assertEqual(darkest, 0)                           # the print

    def test_saved_so_the_text_layer_stays(self):
        picture = _bordered_picture(self.YELLOWED, self.GRAY_INK,
                                    shadow=False)
        for in_form in (False, True):
            with self.subTest(in_form=in_form):
                data = _scan_of(picture, in_form=in_form)
                out = g._pdf_ink_on_white(data)
                doc = pdfium.PdfDocument(io.BytesIO(out))
                tp = doc[0].get_textpage()
                try:
                    self.assertIn("what is chicken", tp.get_text_range())
                finally:
                    tp.close()
                img = doc[0].render(scale=1).to_pil().convert("RGB")
                self.assertEqual(img.getpixel((450, 760)), (255, 255, 255))
                # The print where it stood.
                before = pdfium.PdfDocument(io.BytesIO(data))[0].render(
                    scale=1).to_pil().convert("L")
                after = img.convert("L")
                dark_before = {i for i, v in enumerate(before.tobytes())
                               if v < 90}
                dark_after = {i for i, v in enumerate(after.tobytes())
                              if v < 90}
                self.assertGreater(
                    len(dark_before & dark_after), 0.9 * len(dark_before))

    def _save(self, url: str, answer):
        """A PDF window's Download PDF for *url*, the black-and-white
        question answered *answer*: (whether it was asked, the options the
        file was written with — None when nothing was)."""
        from unittest import mock

        win = object.__new__(g._PdfWindow)
        win._bytes, win._url, win._title = b"%PDF-1.7", url, "79 Stat. 437"
        win._pane, win._app = object(), None
        win._dialog_parent = lambda: None
        win._say = lambda text: None
        written = []
        asked = mock.Mock(return_value=answer)
        with mock.patch.object(g.messagebox, "askyesnocancel", asked), \
                mock.patch.object(g.filedialog, "asksaveasfilename",
                                  return_value="out.pdf"), \
                mock.patch.object(g, "_case_law_print_citation",
                                  return_value=""), \
                mock.patch.object(g, "_write_output_pdf",
                                  lambda *a, **kw: written.append(kw)):
            win._download()
        return asked.called, (written[0] if written else None)

    def test_saving_a_volume_asks(self):
        stat = "https://www.govinfo.gov/link/statute/79/437"
        asked, options = self._save(stat, True)
        self.assertTrue(asked)
        self.assertTrue(options["black_and_white"])
        self.assertTrue(options["clean_borders"])
        asked, options = self._save(stat, False)
        self.assertFalse(options["black_and_white"])
        # Cancelled, nothing is saved.
        self.assertEqual(self._save(stat, None), (True, None))

    def test_nothing_else_is_asked_and_the_u_s_reports_are_left_be(self):
        asked, options = self._save(
            "https://static.case.law/ny/248/case-pdfs/0339-01.pdf", True)
        self.assertFalse(asked)
        self.assertEqual((options["black_and_white"],
                          options["clean_borders"]), (False, True))
        asked, options = self._save(
            "https://www.govinfo.gov/content/pkg/USREPORTS-410/pdf/"
            "USREPORTS-410-113.pdf", True)
        self.assertFalse(options["clean_borders"])

    def test_only_the_statutes_at_large_are_offered_it(self):
        for url, offered in (
                ("https://www.govinfo.gov/link/statute/79/437", True),
                ("https://www.govinfo.gov/content/pkg/STATUTE-79/pdf/"
                 "STATUTE-79-Pg437.pdf", True),
                ("https://www.govinfo.gov/link/fr/88/1234", False),
                ("https://static.case.law/ny/248/case-pdfs/0339-01.pdf",
                 False)):
            with self.subTest(url=url):
                self.assertEqual(g._is_statutes_at_large_pdf(url), offered)


if __name__ == "__main__":
    unittest.main()
