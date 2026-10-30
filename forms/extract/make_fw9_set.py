"""Write the W-9 extraction test set: 20 copies of the real IRS Form W-9 (forms/dev/fw9.pdf), each filled with
different made-up vendor data, and the answer key for them.

    python forms/extract/make_fw9_set.py        # from the repo root; rewrites forms/extract/fw9/

Every copy is the same form and edition; what varies is the data and how the copy was saved:

* *fillable* copies keep their form fields, with the answers in them (what a person typing into the PDF sends);
* *flattened* copies had their fields baked into the page (what "print to PDF" sends): no widgets, the answers are
  page text where the fields were.

The signature and its date have no form field on the W-9, so every copy has them drawn as page text, as a viewer's
signature tool would. The records cover every tax classification on line 3a, the LLC letter, "Other" with its
description, the foreign-partners box, exemption codes, SSN and EIN, a single-member LLC (owner on line 1, the LLC on
line 2), a PO box, an apartment, long names and an empty requester box. All names, numbers and addresses are invented.

The answer key (``answers.json``, and ``answers.csv`` flat) uses forms/schemas/fw9.json's property names where the
schema has one, plus ``fatca_code``, ``requester`` and ``account_numbers`` for the form's other answers. A field the
copy leaves blank is absent from its record. ``certified_on`` is the signature date (ISO); the form shows MM/DD/YYYY.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / "forms" / "dev" / "fw9.pdf"
OUT = Path(__file__).resolve().parent / "fw9"

#: line 3a's check boxes, in form order (widgets c1_1[0..6])
CLASSIFICATIONS = ["individual_sole_proprietor", "c_corporation", "s_corporation", "partnership", "trust_estate",
                   "llc", "other"]
LLC_LETTER = {"c_corporation": "C", "s_corporation": "S", "partnership": "P"}

NORTHBRIDGE = "Northbridge Capital Partners LP\n100 Federal St, 30th Fl\nBoston, MA 02110"
ORCHARD = "Orchard Lane Fund II, L.P.\n2 Embarcadero Ctr\nSan Francisco, CA 94111"

RECORDS: list[dict] = [
    {"legal_name": "Priya Raman", "tax_classification": "individual_sole_proprietor",
     "address": {"street": "19 Cedar Lane", "city": "Menomonie", "state": "WI", "postal_code": "54751"},
     "ssn": "512-48-3907", "requester": NORTHBRIDGE, "certified_on": "2026-01-14"},
    {"legal_name": "Marcus T. Ellison", "business_name": "Ellison Woodworks",
     "tax_classification": "individual_sole_proprietor",
     "address": {"street": "4417 N Halsted St", "city": "Chicago", "state": "IL", "postal_code": "60613"},
     "ssn": "338-61-0254", "requester": NORTHBRIDGE, "certified_on": "2026-01-20"},
    {"legal_name": "Harborview Logistics Inc.", "tax_classification": "c_corporation", "exempt_payee_code": "5",
     "address": {"street": "800 Terminal Way", "city": "Tacoma", "state": "WA", "postal_code": "98421"},
     "ein": "91-2384756", "requester": NORTHBRIDGE, "certified_on": "2026-02-03"},
    {"legal_name": "Bluestem Dental Group, P.C.", "tax_classification": "s_corporation",
     "address": {"street": "1210 W 10th Ave, Suite 300", "city": "Denver", "state": "CO", "postal_code": "80204"},
     "ein": "84-1927365", "requester": ORCHARD, "certified_on": "2026-02-11"},
    {"legal_name": "Calder & Voss Partners", "tax_classification": "partnership", "has_foreign_partners": True,
     "address": {"street": "55 Water St, 12th Floor", "city": "New York", "state": "NY", "postal_code": "10041"},
     "ein": "13-4728105", "requester": NORTHBRIDGE, "certified_on": "2026-02-17"},
    {"legal_name": "The Okafor Family Trust", "business_name": "Adaeze Okafor, Trustee",
     "tax_classification": "trust_estate",
     "address": {"street": "2290 Peachtree Rd NW", "city": "Atlanta", "state": "GA", "postal_code": "30309"},
     "ein": "58-6603192", "requester": ORCHARD, "certified_on": "2026-02-24"},
    {"legal_name": "Ridgeline Ventures LLC", "tax_classification": "llc", "llc_tax_classification": "partnership",
     "address": {"street": "301 Congress Ave, Ste 1500", "city": "Austin", "state": "TX", "postal_code": "78701"},
     "ein": "74-3159826", "requester": NORTHBRIDGE, "certified_on": "2026-03-02"},
    {"legal_name": "Sable Point Holdings LLC", "tax_classification": "llc",
     "llc_tax_classification": "c_corporation", "fatca_code": "A",
     "address": {"street": "1 Biscayne Tower, 2 S Biscayne Blvd", "city": "Miami", "state": "FL",
                 "postal_code": "33131"},
     "ein": "65-1048372", "requester": ORCHARD, "certified_on": "2026-03-05"},
    {"legal_name": "Kestrel Analytics LLC", "tax_classification": "llc", "llc_tax_classification": "s_corporation",
     "address": {"street": "77 Mass Ave", "city": "Cambridge", "state": "MA", "postal_code": "02139"},
     "ein": "04-3871629", "requester": NORTHBRIDGE, "certified_on": "2026-03-09"},
    {"legal_name": "Friends of the Riverside Library", "tax_classification": "other",
     "other_classification": "Unincorporated nonprofit association", "exempt_payee_code": "1",
     "address": {"street": "PO Box 1187", "city": "Riverside", "state": "CA", "postal_code": "92502"},
     "ein": "33-0952184", "requester": ORCHARD, "certified_on": "2026-03-12"},
    {"legal_name": "Elena Sofia Martínez-Reyes", "tax_classification": "individual_sole_proprietor",
     "address": {"street": "3315 Fremont Ave N, Apt 4B", "city": "Seattle", "state": "WA", "postal_code": "98103"},
     "ssn": "601-27-8843", "requester": NORTHBRIDGE, "account_numbers": "NB-204417", "certified_on": "2026-03-18"},
    {"legal_name": "Meridian Tool & Die Corporation", "tax_classification": "c_corporation",
     "exempt_payee_code": "5", "fatca_code": "B",
     "address": {"street": "4600 Industrial Pkwy", "city": "Cleveland", "state": "OH", "postal_code": "44135"},
     "ein": "34-0281947", "requester": NORTHBRIDGE, "account_numbers": "NB-118230, NB-118231",
     "certified_on": "2026-03-23"},
    {"legal_name": "Jonathan O'Neill-Whitaker", "tax_classification": "individual_sole_proprietor",
     "address": {"street": "18 Beacon Hill Ct", "city": "Portland", "state": "ME", "postal_code": "04101"},
     "ssn": "004-52-7719", "certified_on": "2026-03-30"},
    {"legal_name": "Tidewater Clinical Associates", "tax_classification": "partnership",
     "address": {"street": "5800 Wesleyan Dr", "city": "Virginia Beach", "state": "VA", "postal_code": "23455"},
     "ein": "54-1736290", "requester": ORCHARD, "certified_on": "2026-04-06"},
    # a single-member LLC is disregarded: its owner's name on line 1, the LLC on line 2, the owner's classification
    {"legal_name": "Grace Li", "business_name": "Pinecone Studio LLC",
     "tax_classification": "individual_sole_proprietor",
     "address": {"street": "940 Pearl St", "city": "Boulder", "state": "CO", "postal_code": "80302"},
     "ssn": "521-90-4468", "requester": NORTHBRIDGE, "certified_on": "2026-04-13"},
    {"legal_name": "Estate of Harold J. Brennan", "business_name": "Margaret Brennan, Executor",
     "tax_classification": "trust_estate",
     "address": {"street": "212 Elm St", "city": "Hartford", "state": "CT", "postal_code": "06103"},
     "ein": "06-6129384", "requester": NORTHBRIDGE, "certified_on": "2026-04-20"},
    {"legal_name": "Lumen Pediatrics, Inc.", "tax_classification": "s_corporation",
     "address": {"street": "1600 Lakeshore Dr, Suite 210", "city": "Madison", "state": "WI", "postal_code": "53715"},
     "ein": "39-2047718", "requester": ORCHARD, "certified_on": "2026-04-27"},
    {"legal_name": "Darnell Washington", "tax_classification": "individual_sole_proprietor",
     "address": {"street": "PO Box 4402", "city": "Jackson", "state": "MS", "postal_code": "39296"},
     "ssn": "427-35-6190", "requester": NORTHBRIDGE, "certified_on": "2026-05-04"},
    {"legal_name": "Atlas Peak Investors LLC", "tax_classification": "llc",
     "llc_tax_classification": "partnership", "has_foreign_partners": True,
     "address": {"street": "1455 Pennsylvania Ave NW, Suite 400", "city": "Washington", "state": "DC",
                 "postal_code": "20004"},
     "ein": "52-2918463", "requester": ORCHARD, "account_numbers": "OL2-00931", "certified_on": "2026-05-11"},
    {"legal_name": "Consolidated Midwestern Agricultural Equipment Manufacturing Company",
     "tax_classification": "c_corporation", "exempt_payee_code": "5",
     "address": {"street": "12 Grain Exchange Plaza", "city": "Des Moines", "state": "IA", "postal_code": "50309"},
     "ein": "42-0871365", "requester": NORTHBRIDGE, "certified_on": "2026-05-18"},
]

#: the copies saved flattened (1-based); the rest stay fillable
FLATTENED = {4, 8, 11, 15, 17, 20}

#: where the signature and its date go on page 0 (no widgets there): the baseline just above the "Signature of
#: U.S. person" / "Date" line
SIGNATURE_AT = pymupdf.Point(122, 596)
DATE_AT = pymupdf.Point(408, 596)


def signer(record: dict) -> str:
    """Who signs: the person named, or for an entity a made-up officer derived from its name."""
    if record["tax_classification"] == "individual_sole_proprietor":
        return record["legal_name"]
    if "business_name" in record and "," in record["business_name"]:
        return record["business_name"].split(",")[0]
    first = ["Alex Morgan", "Jordan Patel", "Casey Nguyen", "Riley Okonkwo", "Taylor Brooks"]
    return first[sum(map(ord, record["legal_name"])) % len(first)]


def values(record: dict) -> dict[str, str | bool]:
    """The record as the W-9's widget values, by the short widget name (f1_01[0], c1_1[3]...)."""
    a = record["address"]
    out: dict[str, str | bool] = {
        "f1_01[0]": record["legal_name"],
        "f1_02[0]": record.get("business_name", ""),
        "f1_03[0]": LLC_LETTER.get(record.get("llc_tax_classification", ""), ""),
        "f1_04[0]": record.get("other_classification", ""),
        "f1_05[0]": record.get("exempt_payee_code", ""),
        "f1_06[0]": record.get("fatca_code", ""),
        "f1_07[0]": a["street"],
        "f1_08[0]": f"{a['city']}, {a['state']} {a['postal_code']}",
        "f1_09[0]": record.get("requester", ""),
        "f1_10[0]": record.get("account_numbers", ""),
        "c1_2[0]": bool(record.get("has_foreign_partners")),
    }
    for i, name in enumerate(CLASSIFICATIONS):
        out[f"c1_1[{i}]"] = record["tax_classification"] == name
    ssn = record.get("ssn", "").replace("-", "")
    ein = record.get("ein", "").replace("-", "")
    out |= {"f1_11[0]": ssn[:3], "f1_12[0]": ssn[3:5], "f1_13[0]": ssn[5:]}
    out |= {"f1_14[0]": ein[:2], "f1_15[0]": ein[2:]}
    return out


def fill(record: dict, *, flatten: bool) -> bytes:
    doc = pymupdf.open(FORM)
    # the IRS form also carries an XFA copy of its fields; drop it so every viewer shows the AcroForm answers
    acro = doc.xref_get_key(doc.pdf_catalog(), "AcroForm")
    if acro[0] == "xref":
        doc.xref_set_key(int(acro[1].split()[0]), "XFA", "null")
    want = values(record)
    page = doc[0]
    for w in page.widgets():
        short = w.field_name.split(".")[-1]
        if short not in want:
            continue
        v = want[short]
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            w.field_value = w.on_state() if v else "Off"
        else:
            w.field_value = v
        w.update()
    m, d, y = record["certified_on"][5:7], record["certified_on"][8:10], record["certified_on"][:4]
    page.insert_text(SIGNATURE_AT, signer(record), fontname="tiit", fontsize=13, color=(0.05, 0.1, 0.35))
    page.insert_text(DATE_AT, f"{m}/{d}/{y}", fontname="helv", fontsize=10)
    if flatten:
        doc.bake()
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def flat_row(entry: dict) -> dict:
    row = {k: v for k, v in entry.items() if k != "address"}
    for k, v in entry["address"].items():
        row[f"address.{k}"] = v
    if "requester" in row:
        row["requester"] = row["requester"].replace("\n", " / ")
    return row


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("w9_*.pdf"):
        old.unlink()
    key = []
    for n, record in enumerate(RECORDS, start=1):
        kind = "flattened" if n in FLATTENED else "fillable"
        name = f"w9_{n:02d}.pdf"
        (OUT / name).write_bytes(fill(record, flatten=kind == "flattened"))
        key.append({"file": name, "kind": kind, **record})
    (OUT / "answers.json").write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n")
    rows = [flat_row(e) for e in key]
    columns = list(dict.fromkeys(c for r in rows for c in r))
    with (OUT / "answers.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(key)} copies to {OUT}")


if __name__ == "__main__":
    main()
