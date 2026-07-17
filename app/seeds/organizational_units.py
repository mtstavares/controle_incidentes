import json
from pathlib import Path

from app import db
from app.models import OrganizationalCommand, OrganizationalUnit, Unidades


DATA_FILE = Path(__file__).resolve().parent / "data" / "organizational_units.dev.json"


def load_development_organizational_units(data_file=DATA_FILE):
    with Path(data_file).open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return payload.get("organizationalUnits", [])


def normalize_unit_name(value):
    return " ".join((value or "").strip().casefold().split())


def _unit_sort_order(name, index):
    if name == "SEDE":
        return 0
    return index + 1


def seed_development_organizational_units(*, commit=True):
    """Insert or update development CPA/BTL rows without creating duplicates."""
    created = 0
    existing = 0

    for command_index, group in enumerate(load_development_organizational_units(), start=1):
        cpa = (group.get("cpa") or "").strip()
        battalions = group.get("battalions") or []

        if not cpa:
            continue

        command = OrganizationalCommand.query.filter_by(name=cpa).first()
        if command:
            existing += 1
            command.active = True
            command.sort_order = command_index
        else:
            command = OrganizationalCommand(name=cpa, active=True, sort_order=command_index)
            db.session.add(command)
            db.session.flush()
            created += 1

        for unit_index, battalion in enumerate(battalions):
            btl = (battalion or "").strip()
            if not btl:
                continue

            normalized_name = normalize_unit_name(btl)
            unit = OrganizationalUnit.query.filter_by(
                command_id=command.id,
                normalized_name=normalized_name,
            ).first()
            if unit:
                unit.name = btl
                unit.active = True
                unit.sort_order = _unit_sort_order(btl, unit_index)
                existing += 1
            else:
                db.session.add(OrganizationalUnit(
                    command_id=command.id,
                    name=btl,
                    normalized_name=normalized_name,
                    active=True,
                    sort_order=_unit_sort_order(btl, unit_index),
                ))
                created += 1

            legacy = Unidades.query.filter_by(cpa=cpa, btl=btl).first()
            if not legacy:
                db.session.add(Unidades(cpa=cpa, btl=btl))

    if commit:
        db.session.commit()

    return {"created": created, "existing": existing}
