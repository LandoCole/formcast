"""A generated corpus of client-information forms for the extraction tests, and the template a person would write for them.

Every extraction test reads the same form, so its geometry is fixed here as module constants (Letter pages,
points, origin top-left, unrotated page space) and shared by the generator, the hand-written template and the
tests that want to know where an answer sits.

    page 0   "Client Information Form", then Investor legal name, Taxpayer ID, Date of birth or formation, Country,
             Investor type (four check boxes), Mailing address (two lines), Beneficial owners (four rows of name
             and percent), and a paragraph naming the bank and account the funds come from
    page 1   a page of agreement text ending with "Signature"

``make_form`` draws a blank or filled copy in two kinds. A *fillable* copy holds one named widget per answer
(the wire paragraph is page text in both kinds). A *flattened* copy has no widgets: the same answers are drawn
as page text at the widget positions and the check boxes are drawn squares with an "X" inside when on.
``shift`` moves page 0 down, ``rotate`` sets ``/Rotate``, ``extra_page`` appends a blank page and ``painted``
also draws a fillable copy's values as page text under its widgets (as a viewer's appearance stream would).
``edition`` draws another edition of the form: ``"revised"`` adds a "Phone:" row under the legal name and moves
everything below it down :data:`REVISED_GAP`; ``"swapped"`` prints the Taxpayer ID and date-of-birth rows in each
other's place. Both still pass as the layout (most of its lines are where the template has them), which makes them
the hard case for box reads.
The files are not byte-identical between runs (PyMuPDF writes a random file id), but their content is.
"""

from __future__ import annotations

import datetime as dt
import functools
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

from formcast.data.mapping import Transform
from formcast.extract.layout import learn_layout
from formcast.extract.model import (
    AnchorRead,
    BoxRead,
    ChoiceMark,
    ChoiceRead,
    DocumentResult,
    ExtractionTemplate,
    Layout,
    PhraseRead,
    Reader,
    WidgetRead,
)
from formcast.pagemap.build import build_form_map
from formcast.template.model import FormInfo, form_info_for

Rect = tuple[float, float, float, float]

PAGE_SIZE = (612.0, 792.0)
FONT_SIZE = 10.0
SHIFT = 14.0  # the shift the corpus folder's shifted copy uses
ROTATE = 90  # the /Rotate the corpus folder's rotated copy uses

# ------------------------------------------------------------------- geometry

TITLE = "Client Information Form"
TITLE_ORIGIN = (72.0, 72.0)
HEADER_ORIGIN = (72.0, 96.0)  # where ``make_form(header=...)`` draws its line, between the title and the first row

#: Printed labels: the baseline origin of each (page 0, x, baseline y), keyed by the label's default wording.
LABEL_ORIGINS: dict[str, tuple[float, float]] = {
    "Investor legal name:": (72.0, 120.0),
    "Taxpayer ID:": (72.0, 150.0),
    "Date of birth or formation:": (72.0, 180.0),
    "Country:": (72.0, 210.0),
    "Investor type:": (72.0, 240.0),
    "Mailing address": (72.0, 280.0),
    "Beneficial owners (25% or more)": (72.0, 354.0),
}

#: Where each answer sits (the widget's rectangle; a flattened copy draws the text inside it).
FIELD_RECTS: dict[str, Rect] = {
    "investor_name": (240.0, 106.0, 540.0, 126.0),
    "tax_id": (240.0, 136.0, 400.0, 156.0),
    "dob": (240.0, 166.0, 400.0, 186.0),
    "country": (240.0, 196.0, 400.0, 216.0),
    "address_1": (72.0, 288.0, 540.0, 308.0),
    "address_2": (72.0, 314.0, 540.0, 334.0),
}
OWNER_ROWS = 4
for _i in range(1, OWNER_ROWS + 1):
    _top = 380.0 + (_i - 1) * 30.0
    FIELD_RECTS[f"owner_{_i}_name"] = (72.0, _top, 330.0, _top + 20.0)
    FIELD_RECTS[f"owner_{_i}_pct"] = (350.0, _top, 430.0, _top + 20.0)

#: The four investor-type check boxes: value -> (label, square). The label is drawn 6 points right of the square.
TYPE_BOXES: dict[str, tuple[str, Rect]] = {
    "individual": ("Individual", (240.0, 230.0, 252.0, 242.0)),
    "joint": ("Joint", (320.0, 230.0, 332.0, 242.0)),
    "trust": ("Trust", (380.0, 230.0, 392.0, 242.0)),
    "llc": ("LLC", (440.0, 230.0, 452.0, 242.0)),
}

WIRE_RECT: Rect = (72.0, 560.0, 540.0, 620.0)
ABA = "114000093"

#: The "revised" edition: a "Phone:" row under the legal name, and everything below that row moved down this far.
REVISED_GAP = 30.0
REVISED_BELOW = 126.0  # what starts below this y (the legal name row's bottom) moves
PHONE_LABEL = ("Phone:", (72.0, 150.0))
PHONE_RECT: Rect = (240.0, 136.0, 400.0, 156.0)
PHONE = "608 555 0142"
#: The "swapped" edition: the Taxpayer ID and date-of-birth rows (label and answer) in each other's place.
_SWAPPED = {"Taxpayer ID:": 30.0, "tax_id": 30.0, "Date of birth or formation:": -30.0, "dob": -30.0}
EDITIONS = ("original", "revised", "swapped")

SIGNATURE_ORIGIN = (72.0, 740.0)
SIGNATURE_LINE = ((130.0, 742.0), (340.0, 742.0))

# ------------------------------------------------------------------ the people

INVESTOR_TYPES = ("individual", "joint", "trust", "llc")
_COUNTRIES = ("United States", "United States", "USA", "U.S.", "Canada")
_FIRST = ("Avery", "Blake", "Casey", "Devon", "Emerson", "Finley", "Harper", "Jordan", "Kendall", "Logan", "Morgan",
          "Parker", "Quinn", "Riley", "Sawyer", "Taylor")  # fmt: skip
_LAST = ("Alvarez", "Bennett", "Carver", "Dalton", "Ellison", "Foster", "Granger", "Holloway", "Ingram", "Jensen",
         "Keller", "Lambert", "Monroe", "Nolan", "Okafor", "Prescott")  # fmt: skip
_STREETS = ("Maple Street", "Oak Avenue", "Cedar Lane", "Birch Road", "Willow Drive", "Elm Court", "Summit Way")
_CITIES = (("Madison", "WI", "53703"), ("Austin", "TX", "78701"), ("Denver", "CO", "80202"),
           ("Portland", "OR", "97205"), ("Boston", "MA", "02108"), ("Raleigh", "NC", "27601"))  # fmt: skip
_BANKS = ("First Harbor Bank", "Lakeshore Trust Bank", "Pinecrest Savings Bank", "Union Meridian Bank",
          "Westfield National Bank", "Cobalt Credit Bank")  # fmt: skip


@dataclass
class Client:
    legal_name: str
    tax_id: str
    dob: str  # MM/DD/YYYY
    investor_type: str
    country: str  # as written on the form
    street: str
    city_state_zip: str
    owners: list[tuple[str, str]] = field(default_factory=list)  # (name, percent) such as ("Dana Keller", "40%")
    bank: str = ""
    account: str = ""  # digits only


def clients(n: int, seed: int = 0) -> list[Client]:
    """``n`` made-up clients, the same ones for the same ``seed``. Investor types cycle through all four."""
    rng = random.Random(seed)

    def person() -> str:
        return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"

    out: list[Client] = []
    for i in range(n):
        kind = INVESTOR_TYPES[i % len(INVESTOR_TYPES)]
        last = rng.choice(_LAST)
        if kind == "individual":
            name = f"{rng.choice(_FIRST)} {last}"
        elif kind == "joint":
            name = f"{rng.choice(_FIRST)} and {rng.choice(_FIRST)} {last}"
        elif kind == "trust":
            name = f"The {last} Family Trust"
        else:
            name = f"{last} Holdings LLC"
        # an EIN for entities, an SSN for people
        if kind in ("trust", "llc"):
            tax_id = f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}"
        else:
            tax_id = f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"
        dob = f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/{rng.randint(1948, 2001)}"
        city, state, zip_ = rng.choice(_CITIES)
        shares = [rng.choice((10, 20, 25, 30, 40)) for _ in range(rng.randint(1, OWNER_ROWS))]
        while sum(shares) > 100:
            shares.pop()
        owners = [(person(), f"{s}%") for s in shares]
        out.append(
            Client(
                legal_name=name,
                tax_id=tax_id,
                dob=dob,
                investor_type=kind,
                country=rng.choice(_COUNTRIES),
                street=f"{rng.randint(10, 9999)} {rng.choice(_STREETS)}",
                city_state_zip=f"{city}, {state} {zip_}",
                owners=owners,
                bank=rng.choice(_BANKS),
                account="".join(str(rng.randint(0, 9)) for _ in range(rng.randint(8, 12))),
            )
        )
    return out


# ------------------------------------------------------------------- the truth


def truth(client: Client) -> dict[str, str]:
    """What each path should read for ``client``; owner rows the client lacks, and answers left blank, are not in it."""
    dob = ""
    if client.dob:
        m, d, y = client.dob.split("/")
        dob = dt.date(int(y), int(m), int(d)).isoformat()
    out = {
        "investor.legal_name": client.legal_name,
        "investor.tax_id": client.tax_id,
        "investor.dob": dob,
        "investor.type": client.investor_type,
        "investor.country": _COUNTRY_CODES.get(client.country, ""),
        "address.street": client.street,
        "address.city_state_zip": client.city_state_zip,
        "wire.bank": client.bank,
        "wire.account": client.account,
    }
    for i, (name, percent) in enumerate(client.owners):
        out[f"owners[{i}].name"] = name
        out[f"owners[{i}].percent"] = percent
    return {path: value for path, value in out.items() if value}


def assert_no_wrong_found(result: DocumentResult, client: Client | None) -> None:
    """No field ``result`` found differs from ``truth(client)``; with no client (another form), none is found at all."""
    want = truth(client) if client is not None else {}
    for f in result.fields:
        if f.status == "found":
            assert client is not None, (result.file, f)
            assert f.value == want.get(f.path), (result.file, f)


# ------------------------------------------------------------------- the forms


def _answers(client: Client | None) -> dict[str, str]:
    """The text of each answer field, keyed by widget name (empty for the blank form)."""
    values = dict.fromkeys(FIELD_RECTS, "")
    if client is None:
        return values
    values.update(
        investor_name=client.legal_name,
        tax_id=client.tax_id,
        dob=client.dob,
        country=client.country,
        address_1=client.street,
        address_2=client.city_state_zip,
    )
    for i, (name, percent) in enumerate(client.owners, start=1):
        values[f"owner_{i}_name"] = name
        values[f"owner_{i}_pct"] = percent
    return values


def _shifted(rect: Rect, dy: float) -> pymupdf.Rect:
    return pymupdf.Rect(rect[0], rect[1] + dy, rect[2], rect[3] + dy)


def _text_widget(page: pymupdf.Page, name: str, rect: pymupdf.Rect, value: str) -> None:
    w = pymupdf.Widget()
    w.field_name = name
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = rect
    w.field_value = value
    w.text_fontsize = FONT_SIZE
    w.text_font = "Helv"
    page.add_widget(w)


def _check_widget(page: pymupdf.Page, name: str, rect: pymupdf.Rect, on: bool) -> None:
    w = pymupdf.Widget()
    w.field_name = name
    w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    w.rect = rect
    w.field_value = on
    w.border_color = (0, 0, 0)
    w.border_width = 0.75
    page.add_widget(w)


def _answer_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str) -> None:
    """Draw an answer as page text inside its field rectangle (what a flattened copy, or a painted one, has)."""
    if text:
        page.insert_text((rect.x0 + 4, rect.y1 - 6), text, fontsize=FONT_SIZE, fontname="helv")


def _wire_text(client: Client | None) -> str:
    bank = client.bank if client else "____________"
    account = client.account if client else "____________"
    return (
        f"Funds will be sent from {bank}, ABA {ABA}, account {account}, in the name of the investor named above."
    )


def _edition_dy(edition: str, y: float, key: str = "") -> float:
    """How far ``edition`` moves the thing at ``y`` (a label's baseline or a rectangle's top) named ``key``."""
    if edition == "revised":
        new_row = key in (PHONE_LABEL[0], "phone")  # the inserted row is where the moved rows were
        return REVISED_GAP if y > REVISED_BELOW and not new_row else 0.0
    if edition == "swapped":
        return _SWAPPED.get(key, 0.0)
    return 0.0


def _draw_page_zero(
    page: pymupdf.Page,
    client: Client | None,
    kind: str,
    shift: float,
    painted: bool,
    labels: dict[str, str],
    header: str | None = None,
    checked: tuple[str, ...] | None = None,
    edition: str = "original",
) -> None:
    def word(text: str) -> str:
        return labels.get(text, text)

    def dy(y: float, key: str = "") -> float:
        return shift + _edition_dy(edition, y, key)

    page.insert_text((TITLE_ORIGIN[0], TITLE_ORIGIN[1] + shift), word(TITLE), fontsize=16, fontname="hebo")
    if header:
        page.insert_text((HEADER_ORIGIN[0], HEADER_ORIGIN[1] + shift), header, fontsize=FONT_SIZE, fontname="helv")
    origins = dict(LABEL_ORIGINS)
    if edition == "revised":
        origins[PHONE_LABEL[0]] = PHONE_LABEL[1]
    for text, (x, y) in origins.items():
        bold = text.startswith(("Mailing", "Beneficial"))
        page.insert_text((x, y + dy(y, text)), word(text), fontsize=FONT_SIZE, fontname="hebo" if bold else "helv")
    # the owners table's column heads
    page.insert_text((72, 376 + dy(376)), word("Name"), fontsize=8, fontname="helv")
    page.insert_text((350, 376 + dy(376)), word("Percent"), fontsize=8, fontname="helv")

    values = _answers(client)
    rects = dict(FIELD_RECTS)
    if edition == "revised":
        values["phone"] = PHONE if client is not None else ""
        rects["phone"] = PHONE_RECT
    # a line under each single-line answer, in both kinds, so a flattened copy still looks like a form
    for name, rect in rects.items():
        r = _shifted(rect, dy(rect[1], name))
        page.draw_line((r.x0, r.y1), (r.x1, r.y1), width=0.5)
        if kind == "flattened" or painted:
            _answer_text(page, r, values[name])
        if kind == "fillable":
            _text_widget(page, name, r, values[name])

    for value, (label, square) in TYPE_BOXES.items():
        r = _shifted(square, dy(square[1]))
        on = value in checked if checked is not None else client is not None and client.investor_type == value
        page.insert_text((r.x1 + 6, r.y1 - 2), word(label), fontsize=FONT_SIZE, fontname="helv")
        if kind == "fillable":
            _check_widget(page, f"type_{value}", r, on)
            if painted and on:
                page.insert_text((r.x0 + 2.5, r.y1 - 2.5), "X", fontsize=FONT_SIZE, fontname="hebo")
        else:
            page.draw_rect(r, width=0.75)
            if on:
                page.insert_text((r.x0 + 2.5, r.y1 - 2.5), "X", fontsize=FONT_SIZE, fontname="hebo")

    wire = _shifted(WIRE_RECT, dy(WIRE_RECT[1]))
    if page.insert_textbox(wire, _wire_text(client), fontsize=FONT_SIZE, fontname="helv") < 0:
        raise AssertionError("the wire paragraph does not fit its box")


_SENTENCES = (
    "The investor confirms that the information given on this form is complete and correct.",
    "The administrator may rely on this information until it receives written notice of a change.",
    "Each beneficial owner named above has agreed to be identified for the purposes of anti-money-laundering review.",
    "Funds are accepted only from an account held in the name of the investor.",
    "A transfer from any other account will be returned without interest.",
    "The investor will provide further documents when the administrator reasonably asks for them.",
    "No interest in the fund is transferable without the written consent of the manager.",
)


def _agreement_text() -> str:
    """Forty sentences of agreement text, the same on every copy."""
    return " ".join(_SENTENCES[i % len(_SENTENCES)] for i in range(40))


def _draw_page_one(page: pymupdf.Page) -> None:
    rect = pymupdf.Rect(72, 72, 540, 700)
    if page.insert_textbox(rect, _agreement_text(), fontsize=FONT_SIZE, fontname="helv", align=0) < 0:
        raise AssertionError("the agreement text does not fit its page")
    page.insert_text(SIGNATURE_ORIGIN, "Signature", fontsize=FONT_SIZE, fontname="helv")
    page.draw_line(*SIGNATURE_LINE, width=0.5)


def make_form(
    client: Client | None,
    *,
    kind: Literal["fillable", "flattened"],
    shift: float = 0.0,
    rotate: int = 0,
    extra_page: bool = False,
    painted: bool = False,
    labels: dict[str, str] | None = None,
    header: str | None = None,
    checked: tuple[str, ...] | None = None,
    edition: Literal["original", "revised", "swapped"] = "original",
) -> bytes:
    """A two-page client-information form as PDF bytes; ``client=None`` is the blank form.

    ``labels`` replaces printed wordings (``{"Taxpayer ID:": "TIN:"}``) for a copy that words a label differently.
    ``header`` draws one more line of text under the title (``"Taxpayer ID:"``, so that label is on the page twice).
    ``checked`` names the investor-type boxes that are on (``("individual", "joint")``) in place of the client's type.
    ``edition`` draws the ``"revised"`` or ``"swapped"`` edition the module describes; a filled revised copy's phone
    is :data:`PHONE` (in a widget ``phone`` on a fillable copy).
    """
    if kind not in ("fillable", "flattened"):
        raise ValueError(f"kind must be 'fillable' or 'flattened', not {kind!r}")
    if edition not in EDITIONS:
        raise ValueError(f"edition must be one of {EDITIONS}, not {edition!r}")
    doc = pymupdf.open()
    first = doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    _draw_page_zero(first, client, kind, shift, painted, labels or {}, header, checked, edition)
    _draw_page_one(doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1]))
    if rotate:
        for page in doc:
            page.set_rotation(rotate)  # after drawing: the widgets keep their unrotated rectangles
    if extra_page:
        doc.new_page(width=PAGE_SIZE[0], height=PAGE_SIZE[1])
    return doc.tobytes(deflate=True)


def other_form() -> bytes:
    """A form of a different layout: the repo's W-9."""
    return (Path(__file__).resolve().parents[2] / "demo" / "data" / "forms" / "fw9.pdf").read_bytes()


# --------------------------------------------------------------------- template

_COUNTRY_CODES = {"United States": "US", "USA": "US", "U.S.": "US", "Canada": "CA"}


def _schema() -> dict:
    def text(title: str, description: str, **extra: object) -> dict:
        return {"type": "string", "title": title, "description": description, **extra}

    return {
        "type": "object",
        "properties": {
            "investor": {
                "type": "object",
                "title": "Investor",
                "properties": {
                    "legal_name": text("Investor legal name", "The investor's full legal name as registered."),
                    "tax_id": text(
                        "Taxpayer ID",
                        "The investor's EIN (12-3456789) or SSN (123-45-6789).",
                        pattern=r"^\d{2}-\d{7}$|^\d{3}-\d{2}-\d{4}$",
                    ),
                    "dob": text(
                        "Date of birth or formation",
                        "The investor's date of birth, or the date the entity was formed.",
                        format="date",
                    ),
                    "type": text(
                        "Investor type",
                        "Whether the investor is an individual, a joint account, a trust or an LLC.",
                        enum=list(INVESTOR_TYPES),
                    ),
                    "country": text(
                        "Country",
                        "The investor's country, as a two-letter code.",
                        enum=sorted(set(_COUNTRY_CODES.values())),
                    ),
                },
                "required": ["tax_id"],
            },
            "address": {
                "type": "object",
                "title": "Mailing address",
                "properties": {
                    "street": text("Street", "The street address line of the mailing address."),
                    "city_state_zip": text("City, state, ZIP", "The city, state and ZIP line of the mailing address."),
                },
            },
            "owners": {
                "type": "array",
                "title": "Beneficial owners",
                "description": "People who own 25% or more of the investor.",
                "maxItems": OWNER_ROWS,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": text("Owner name", "The beneficial owner's name."),
                        "percent": text("Owner percent", "The share the owner holds, as written (such as 40%)."),
                    },
                },
            },
            "wire": {
                "type": "object",
                "title": "Wire instructions",
                "properties": {
                    "bank": text("Sending bank", "The bank the funds will be sent from."),
                    "account": text("Sending account", "The account number the funds will be sent from, digits only."),
                },
            },
        },
        "required": ["investor"],  # so a document with no investor answers at all reports its tax ID missing
    }


def _readers() -> dict[str, Reader]:
    def box(name: str, page: int = 0) -> BoxRead:
        return BoxRead(page=page, rect=FIELD_RECTS[name])

    readers: dict[str, Reader] = {
        "investor.legal_name": Reader(
            strategies=[
                WidgetRead(names=["investor_name"]),
                box("investor_name"),
                AnchorRead(labels=["Investor legal name", "Legal name"], page=0, max_width=300),
            ]
        ),
        "investor.tax_id": Reader(
            strategies=[
                WidgetRead(names=["tax_id"]),
                box("tax_id"),
                AnchorRead(labels=["Taxpayer ID", "TIN"], page=0, max_width=300),  # "TIN:" ends far from the answer
            ]
        ),
        "investor.dob": Reader(
            strategies=[
                WidgetRead(names=["dob"]),
                box("dob"),
                AnchorRead(labels=["Date of birth or formation", "Date of birth"], page=0, max_width=200),
            ],
            clean=[Transform(op="date", args={"formats": ["%m/%d/%Y"]})],
        ),
        "investor.type": Reader(
            strategies=[
                ChoiceRead(options={v: ChoiceMark(widget=f"type_{v}") for v in INVESTOR_TYPES}),
                ChoiceRead(options={v: ChoiceMark(page=0, rect=TYPE_BOXES[v][1]) for v in INVESTOR_TYPES}),
            ]
        ),
        "investor.country": Reader(
            strategies=[
                WidgetRead(names=["country"]),
                box("country"),
                AnchorRead(labels=["Country"], page=0, max_width=200),
            ],
            clean=[Transform(op="map", args={"values": _COUNTRY_CODES})],
        ),
        "address.street": Reader(
            strategies=[
                WidgetRead(names=["address_1"]),
                box("address_1"),
                AnchorRead(labels=["Mailing address"], page=0, side="below", max_width=468),
            ]
        ),
        # the second line has no label of its own, so it is read only by position
        "address.city_state_zip": Reader(strategies=[WidgetRead(names=["address_2"]), box("address_2")]),
        "wire.bank": Reader(strategies=[PhraseRead(phrases=["Funds will be sent from"], page=0, stop="comma")]),
        "wire.account": Reader(strategies=[PhraseRead(phrases=["account"], page=0, stop="digits")]),
    }
    for i in range(OWNER_ROWS):
        readers[f"owners[{i}].name"] = Reader(strategies=[WidgetRead(names=[f"owner_{i + 1}_name"]), box(f"owner_{i + 1}_name")])
        readers[f"owners[{i}].percent"] = Reader(strategies=[WidgetRead(names=[f"owner_{i + 1}_pct"]), box(f"owner_{i + 1}_pct")])
    return readers


@functools.cache
def _form_info() -> FormInfo:
    """The form info of the blank fillable form, built once (building a form map opens and reads the PDF)."""
    return form_info_for(build_form_map(make_form(None, kind="fillable"), detect=False))


#: The clients whose filled copies the hand template's layout is learned from (not the corpus folder's clients).
LAYOUT_SEED = 99


@functools.cache
def _layout() -> Layout:
    """The layout learned from six varied filled copies: both kinds, plain, rotated and with an extra page."""
    people = clients(6, seed=LAYOUT_SEED)
    samples = [
        make_form(people[0], kind="fillable"),
        make_form(people[1], kind="flattened"),
        make_form(people[2], kind="fillable", rotate=ROTATE),
        make_form(people[3], kind="flattened", extra_page=True),
        make_form(people[4], kind="fillable", painted=True),
        make_form(people[5], kind="flattened"),
    ]
    return learn_layout(samples)


def hand_template() -> ExtractionTemplate:
    """The template a person would write for this form.

    Its ``layout`` is learned from filled copies of other clients (:data:`LAYOUT_SEED`); its ``form`` is the blank
    fillable form's. Both are made once and copied, so a test may change its template freely.
    """
    return ExtractionTemplate(
        template_id="client_information",
        name="Client information",
        schema=_schema(),
        form=_form_info().model_copy(deep=True),
        layout=_layout().model_copy(deep=True),
        readers=_readers(),
    )
