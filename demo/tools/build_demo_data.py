"""Build ``demo/data/``: the demo forms, their schemas, fictional records and stored templates.

    python demo/tools/build_demo_data.py                       # rewrite demo/data
    python demo/tools/build_demo_data.py --forms fw9 wadoc_auth

Per form (dev split only; the default set is ``fw9``, ``wadoc_auth``, ``menomonie_app``):

- ``forms/<id>.pdf`` copied from ``forms/dev/``
- ``schemas/<id>.json`` copied from ``forms/schemas/``
- ``records/<id>.csv`` and a table in ``records.sqlite``: five fictional records (example.com
  emails, 555-01xx phones, 000-prefixed SSNs, "Example" streets)
- ``templates/<id>.json``: the agent's template from the archived defer-placement run
  (``eval/results/templates.tar.gz``), rebound to the copied PDF so it fills with the
  fingerprint check on

The output is deterministic, so a rerun changes nothing unless an input changed. To use your own
forms, mirror this layout (see ``demo/server/records.py`` for the record columns).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import tarfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))  # run this checkout's formcast, not an installed copy

from formcast import Template, flatten_schema, rebind  # noqa: E402

ARCHIVE = ROOT / "eval" / "results" / "templates.tar.gz"
ARCHIVE_MEMBER = "eval/results/templates/2026-10-21_defer-placement/anthropic_eval/{form}_as_published.json"
DEFAULT_OUT = ROOT / "demo" / "data"


def _addr(street: str, city: str, state: str, zip_: str) -> dict:
    return {"street": street, "city": city, "state": state, "postal_code": zip_}


def _org(name: str, address: dict, phone: str) -> dict:
    return {"name": name, "address": address, "phone": phone}


def _job(employer: str, start: str, end: str, title: str, **more: Any) -> dict:
    return {"employer": employer, "start_date": start, "end_date": end, "position_title": title, **more}


FW9 = [
    {"vendor_id": "V-00101", "legal_name": "Juniper Lane Ceramics LLC", "business_name": "Juniper Lane Studio",
     "tax_classification": "llc", "llc_tax_classification": "s_corporation", "has_foreign_partners": False,
     "address": _addr("418 Example Avenue", "Springfield", "OR", "97477"), "ein": "00-0000101",
     "remit_email": "ap@example.com", "certified_on": "2026-03-14"},
    {"vendor_id": "V-00102", "legal_name": "Maria Example Lindqvist", "tax_classification": "individual_sole_proprietor",
     "has_foreign_partners": False, "address": _addr("22 Example Street", "Eau Claire", "WI", "54701"),
     "ssn": "000-12-3402", "remit_email": "maria@example.com", "certified_on": "2026-02-02"},
    {"vendor_id": "V-00103", "legal_name": "Northgate Print Works, Inc.", "tax_classification": "c_corporation",
     "exempt_payee_code": "5", "has_foreign_partners": False,
     "address": _addr("9 Example Plaza, Suite 4", "Duluth", "MN", "55802"), "ein": "00-0000103",
     "remit_email": "billing@example.com", "certified_on": "2026-01-19"},
    {"vendor_id": "V-00104", "legal_name": "Ashby & Okafor Partners", "business_name": "A&O Surveying",
     "tax_classification": "partnership", "has_foreign_partners": True,
     "address": _addr("1500 Example Boulevard", "Tacoma", "WA", "98402"), "ein": "00-0000104",
     "remit_email": "office@example.com", "certified_on": "2026-04-08"},
    {"vendor_id": "V-00105", "legal_name": "Harbor Light Trust", "tax_classification": "other",
     "other_classification": "Charitable remainder trust", "has_foreign_partners": False,
     "address": _addr("75 Example Court", "Portland", "ME", "04101"), "ein": "00-0000105"},
]

WADOC = [
    {"individual": {"first_name": "Daniel", "middle_name": "R", "last_name": "Example", "date_of_birth": "1984-06-12",
                    "doc_number": "000101"},
     "releasing_organization": _org("Example County Health Clinic", _addr("300 Example Road", "Olympia", "WA", "98501"),
                                    "(360) 555-0101"),
     "recipient_organization": _org("Example Reentry Services", _addr("12 Example Way", "Tacoma", "WA", "98402"),
                                    "(253) 555-0102"),
     "information_requested": "Treatment summary and attendance record", "purpose": "Release planning",
     "signed_on": "2026-05-04", "witness": {"name": "Pat Sample", "signed_on": "2026-05-04"},
     "expires_on": "2027-05-04"},
    {"individual": {"first_name": "Aisha", "last_name": "Example", "date_of_birth": "1991-11-30", "doc_number": "000202"},
     "releasing_organization": _org("Example Valley Hospital", _addr("88 Example Drive", "Yakima", "WA", "98901"),
                                    "(509) 555-0103"),
     "recipient_organization": _org("Example Community Corrections", _addr("5 Example Lane", "Spokane", "WA", "99201"),
                                    "(509) 555-0104"),
     "information_requested": "Medical records, last 12 months", "purpose": "Continuity of care",
     "signed_on": "2026-06-18", "expires_on": "2027-06-18"},
    {"individual": {"first_name": "Luis", "middle_name": "M", "last_name": "Example", "doc_number": "000303"},
     "releasing_organization": _org("Example Learning Center", _addr("41 Example Street", "Bellingham", "WA", "98225"),
                                    "(360) 555-0105"),
     "recipient_organization": _org("Example Education Office", _addr("2 Example Circle", "Tumwater", "WA", "98501"),
                                    "(360) 555-0106"),
     "information_requested": "Transcripts and program certificates", "signed_on": "2026-07-01"},
    {"individual": {"first_name": "Grace", "last_name": "Example", "date_of_birth": "1979-02-27", "doc_number": "000404"},
     "releasing_organization": _org("Example Employment Agency", _addr("700 Example Avenue", "Vancouver", "WA", "98660"),
                                    "(360) 555-0107"),
     "recipient_organization": _org("Example Workforce Program", _addr("19 Example Place", "Everett", "WA", "98201"),
                                    "(425) 555-0108"),
     "purpose": "Job placement", "signed_on": "2026-03-22", "expires_on": "2026-09-22"},
    {"individual": {"first_name": "Tomas", "last_name": "Example", "date_of_birth": "1996-08-05", "doc_number": "000505"},
     "releasing_organization": _org("Example Counseling Group", _addr("63 Example Road", "Kent", "WA", "98032"),
                                    "(253) 555-0109"),
     "recipient_organization": _org("Example Housing Authority", _addr("8 Example Street", "Renton", "WA", "98057"),
                                    "(425) 555-0110"),
     "information_requested": "Attendance confirmation", "purpose": "Housing application",
     "signed_on": "2026-09-09", "witness": {"name": "Jo Sample"}, "expires_on": "2027-09-09"},
]

MENOMONIE = [
    {"application_id": "APP-0101", "application_date": "2026-04-02", "position_applied_for": "Library Assistant",
     "applicant": {"first_name": "Nora", "middle_name": "J", "last_name": "Example",
                   "address": _addr("14 Example Street", "Menomonie", "WI", "54751"), "phone": "(715) 555-0101",
                   "email": "nora@example.com", "us_born_citizen": True},
     "previous_application": {"applied_before": False},
     "education": {"school_name": "Example State University", "school_city": "Eau Claire", "school_state": "WI",
                   "credential": "B.A. English"},
     "certifications": ["CPR", "First Aid"], "drivers_license": {"valid": True, "state": "WI"},
     "commercial_drivers_license": {"valid": False},
     "employment_history": [_job("Example Books", "2021-06-01", "2025-12-31", "Clerk", supervisor="Sam Sample",
                                 final_salary=17.5, final_salary_period="hour", reason_for_leaving="Seasonal")],
     "may_contact_current_employer": True, "accepts_temporary_employment": True,
     "criminal_history": {"has_record": False}, "available_from": "2026-05-01", "signed_on": "2026-04-02"},
    {"application_id": "APP-0102", "application_date": "2026-04-09", "position_applied_for": "Street Maintenance Worker",
     "applicant": {"first_name": "Marcus", "last_name": "Example",
                   "address": _addr("320 Example Avenue", "Menomonie", "WI", "54751"), "phone": "(715) 555-0102",
                   "email": "marcus@example.com", "us_born_citizen": True},
     "previous_application": {"applied_before": True, "position": "Parks Laborer", "applied_on": "2024-03-15"},
     "education": {"school_name": "Example Technical College", "school_city": "Rice Lake", "school_state": "WI",
                   "credential": "Diploma, Diesel Mechanics"},
     "certifications": ["Flagger", "Chainsaw safety"], "drivers_license": {"valid": True, "state": "WI"},
     "commercial_drivers_license": {"valid": True, "state": "WI"},
     "employment_history": [
         _job("Example Hauling", "2019-04-01", "2026-03-15", "Driver", final_salary=26, final_salary_period="hour",
              duties="Dump truck, snow plow routes"),
         _job("Example Farm Supply", "2016-05-01", "2019-03-30", "Yard hand", final_salary=14.25,
              final_salary_period="hour")],
     "may_contact_current_employer": False, "accepts_temporary_employment": False,
     "criminal_history": {"has_record": False}, "available_from": "2026-04-20", "signed_on": "2026-04-09"},
    {"application_id": "APP-0103", "application_date": "2026-05-12", "position_applied_for": "Recreation Leader",
     "applicant": {"first_name": "Priya", "middle_name": "S", "last_name": "Example",
                   "address": _addr("77 Example Court", "Colfax", "WI", "54730"), "phone": "(715) 555-0103",
                   "email": "priya@example.com", "us_born_citizen": False},
     "education": {"school_name": "Example High School", "school_city": "Colfax", "school_state": "WI",
                   "credential": "Diploma"},
     "certifications": ["Lifeguard"], "drivers_license": {"valid": True, "state": "WI"},
     "may_contact_current_employer": True, "accepts_temporary_employment": True,
     "criminal_history": {"has_record": False}, "available_from": "2026-06-01", "signed_on": "2026-05-12"},
    {"application_id": "APP-0104", "application_date": "2026-05-20", "position_applied_for": "Water Utility Operator",
     "applicant": {"first_name": "Henry", "last_name": "Example",
                   "address": _addr("5 Example Road", "Elk Mound", "WI", "54739"), "phone": "(715) 555-0104",
                   "email": "henry@example.com", "us_born_citizen": True},
     "previous_application": {"applied_before": False},
     "education": {"school_name": "Example Technical College", "school_city": "Eau Claire", "school_state": "WI",
                   "credential": "Associate degree, Water Quality"},
     "certifications": [], "drivers_license": {"valid": True, "state": "WI"},
     "commercial_drivers_license": {"valid": False},
     "employment_history": [_job("Example Water District", "2020-01-06", "2026-05-01", "Operator in training",
                                 final_salary=58000, final_salary_period="year", reason_for_leaving="Relocation")],
     "may_contact_current_employer": True, "accepts_temporary_employment": True,
     "criminal_history": {"has_record": True, "county": "Dunn", "state": "WI", "date": "2012-07-14",
                          "court": "Example County Circuit Court", "offense": "Disorderly conduct",
                          "disposition": "Fine paid"},
     "available_from": "2026-06-15", "signed_on": "2026-05-20"},
    {"application_id": "APP-0105", "application_date": "2026-06-03", "position_applied_for": "Administrative Clerk",
     "applicant": {"first_name": "Elena", "middle_name": "T", "last_name": "Example",
                   "address": _addr("201 Example Lane", "Menomonie", "WI", "54751"), "phone": "(715) 555-0105",
                   "email": "elena@example.com", "us_born_citizen": True},
     "education": {"school_name": "Example State University", "school_city": "Stout", "school_state": "WI",
                   "credential": "B.S. Business Administration"},
     "certifications": ["Notary public"], "drivers_license": {"valid": False},
     "employment_history": [
         _job("Example Insurance Agency", "2022-09-01", "2026-05-30", "Office coordinator", final_salary=19.75,
              final_salary_period="hour", duties="Scheduling, filing, front desk"),
         _job("Example Cafe", "2019-06-01", "2022-08-15", "Shift lead")],
     "may_contact_current_employer": False, "accepts_temporary_employment": True,
     "criminal_history": {"has_record": False}, "available_from": "2026-07-01", "signed_on": "2026-06-03"},
]

# form id -> (records, how to name a record in lists)
DEMO: dict[str, tuple[list[dict], Callable[[dict], str]]] = {
    "fw9": (FW9, lambda r: r["legal_name"]),
    "wadoc_auth": (WADOC, lambda r: f"{r['individual']['first_name']} {r['individual']['last_name']}, "
                                    f"{r['releasing_organization']['name']}"),
    "menomonie_app": (MENOMONIE, lambda r: f"{r['applicant']['first_name']} {r['applicant']['last_name']}, "
                                           f"{r['position_applied_for']}"),
}  # fmt: skip


def _columns(schema: dict) -> list[tuple[str, str]]:
    """(dotted column, schema type) for every value a row can hold: scalars and whole arrays."""
    return [(p.path, p.type) for p in flatten_schema(schema) if "[]" not in p.path and p.type != "object"]


def _cells(record: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        path = prefix + key
        if isinstance(value, dict):
            out.update(_cells(value, path + "."))
        else:
            out[path] = value
    return out


def _csv_cell(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)


def _sql_cell(value: Any) -> Any:
    if isinstance(value, list):
        return json.dumps(value)
    return int(value) if isinstance(value, bool) else value


_SQL_TYPE = {"boolean": "INTEGER", "integer": "INTEGER", "number": "REAL"}


def build(forms: list[str], out: Path) -> None:
    manifest = {f["id"]: f for f in json.loads((ROOT / "forms" / "manifest.json").read_text())["forms"]}
    for form in forms:
        if form not in DEMO:
            raise SystemExit(f"no demo records for {form!r} (have {', '.join(DEMO)})")
        if manifest.get(form, {}).get("split") != "dev":
            raise SystemExit(f"{form!r} is not a dev-split form")
    for sub in ("forms", "schemas", "records", "templates"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    db_path = out / "records.sqlite"
    db_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as db, tarfile.open(ARCHIVE) as archive:
        for form in forms:
            pdf = out / "forms" / f"{form}.pdf"
            shutil.copyfile(ROOT / "forms" / "dev" / f"{form}.pdf", pdf)
            schema_text = (ROOT / "forms" / "schemas" / f"{form}.json").read_text(encoding="utf-8")
            (out / "schemas" / f"{form}.json").write_text(schema_text, encoding="utf-8")
            schema = json.loads(schema_text)

            records, title = DEMO[form]
            columns = _columns(schema)
            names = [name for name, _ in columns]
            rows = []
            for n, record in enumerate(records, start=1):
                cells = _cells(record)
                if extra := set(cells) - set(names):
                    raise SystemExit(f"{form} record {n} has values outside the schema: {sorted(extra)}")
                rows.append({"id": f"{form}-{n:02d}", "title": title(record), **cells})
            with (out / "records" / f"{form}.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, lineterminator="\n")
                writer.writerow(["id", "title", *names])
                for row in rows:
                    writer.writerow([_csv_cell(row[c]) if c in row else "" for c in ["id", "title", *names]])
            defs = ", ".join(f'"{n}" {_SQL_TYPE.get(t, "TEXT")}' for n, t in columns)
            db.execute(f'CREATE TABLE "{form}" (id TEXT PRIMARY KEY, title TEXT, {defs})')
            marks = ", ".join("?" * (len(names) + 2))
            db.executemany(
                f'INSERT INTO "{form}" VALUES ({marks})',
                [[row["id"], row["title"], *(_sql_cell(row[c]) if c in row else None for c in names)] for row in rows],
            )

            member = archive.extractfile(ARCHIVE_MEMBER.format(form=form))
            assert member is not None
            template = rebind(Template.from_json(member.read().decode("utf-8")), pdf.read_bytes())
            (out / "templates" / f"{form}.json").write_text(template.to_json() + "\n", encoding="utf-8")
        db.commit()
    print(f"wrote {len(forms)} forms to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--forms", nargs="+", default=list(DEMO), help="dev form ids (default: all three)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    build(args.forms, args.out)


if __name__ == "__main__":
    main()
