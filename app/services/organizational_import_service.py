from dataclasses import dataclass, field
from datetime import datetime
import re
import time
import unicodedata

from sqlalchemy import text

from app import db
from app.models import Incidente, OrganizationalCommand, OrganizationalUnit, Unidades
from app.services.timezone_service import local_now


REPLACEMENT_CHAR = chr(0xFFFD)
MOJIBAKE_MARKERS = (
    "\u00c3",
    "\u00c2",
    "\u00e2\u20ac",
    "\u00e2\u20ac\u0153",
    "\u00e2\u20ac\u009d",
    "\u00e2\u20ac\u2122",
    "\u00ef\u00bf\u00bd",
    REPLACEMENT_CHAR,
)
INVALID_TEXT_MARKERS = (REPLACEMENT_CHAR, "\u00ef\u00bf\u00bd")
BRANCH_PREFIX_RE = re.compile(r"^[\s│├└─]+")
SPACES_RE = re.compile(r"\s+")


@dataclass
class OrganizationalImportResult:
    started_at: datetime
    finished_at: datetime | None = None
    removed_commands: int = 0
    removed_units: int = 0
    removed_legacy_units: int = 0
    imported_commands: int = 0
    imported_units: int = 0
    invalid_lines: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    rolled_back: bool = False

    @property
    def success(self):
        return not self.rolled_back and not self.errors and not self.invalid_lines

    def as_dict(self):
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "removed_commands": self.removed_commands,
            "removed_units": self.removed_units,
            "removed_legacy_units": self.removed_legacy_units,
            "imported_commands": self.imported_commands,
            "imported_units": self.imported_units,
            "invalid_lines": self.invalid_lines,
            "errors": self.errors,
            "elapsed_seconds": round(self.elapsed_seconds, 4),
            "success": self.success,
            "rolled_back": self.rolled_back,
        }


def _try_repair_mojibake(value):
    text_value = value or ""
    if not any(marker in text_value for marker in MOJIBAKE_MARKERS):
        return text_value
    try:
        repaired = text_value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return text_value
    return repaired


def normalize_text(value):
    text_value = _try_repair_mojibake(value)
    text_value = unicodedata.normalize("NFC", text_value)
    text_value = text_value.replace("°", "º").replace("˚", "º")
    text_value = text_value.replace("–", "-").replace("—", "-")
    text_value = SPACES_RE.sub(" ", text_value).strip()
    return text_value


def normalize_command_name(value):
    command = normalize_text(value)
    command = re.sub(r"\s*-\s*SEDE\s*$", "", command, flags=re.IGNORECASE)
    return command


def normalize_unit_name(value):
    return " ".join(normalize_text(value).casefold().split())


def _is_unit_line(line):
    stripped = line.lstrip()
    return stripped.startswith(("├", "└", "│", "─"))


def _extract_unit_name(line):
    return normalize_text(BRANCH_PREFIX_RE.sub("", line).strip())


def _validate_name(kind, name, line_number):
    if not name:
        return f"Linha {line_number}: {kind} vazio."
    if any(marker in name for marker in INVALID_TEXT_MARKERS):
        return f"Linha {line_number}: {kind} possui caractere inválido."
    if len(name) > 100:
        return f"Linha {line_number}: {kind} excede 100 caracteres."
    if not re.fullmatch(r"[\w\sº/.\-]+", name, flags=re.UNICODE):
        return f"Linha {line_number}: {kind} possui caractere não permitido."
    return None


def parse_organizational_structure_text(content):
    groups = []
    current_group = None
    invalid_lines = []

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = normalize_text(raw_line)
        if not line:
            continue

        if _is_unit_line(line):
            if current_group is None:
                invalid_lines.append({"line": line_number, "value": line, "error": "Unidade sem CPA/Grande Comando."})
                continue
            unit_name = _extract_unit_name(line)
            error = _validate_name("Unidade", unit_name, line_number)
            if error:
                invalid_lines.append({"line": line_number, "value": unit_name, "error": error})
                continue
            current_group["units"].append(unit_name)
            continue

        command_name = normalize_command_name(line)
        error = _validate_name("CPA/Grande Comando", command_name, line_number)
        if error:
            invalid_lines.append({"line": line_number, "value": command_name, "error": error})
            current_group = None
            continue
        current_group = {"name": command_name, "units": []}
        groups.append(current_group)

    seen_commands = set()
    for group in groups:
        command_key = group["name"].casefold()
        if command_key in seen_commands:
            invalid_lines.append({"line": None, "value": group["name"], "error": "CPA/Grande Comando duplicado."})
        seen_commands.add(command_key)

        seen_units = set()
        for unit in group["units"]:
            unit_key = normalize_unit_name(unit)
            if unit_key in seen_units:
                invalid_lines.append({"line": None, "value": f"{group['name']} -> {unit}", "error": "Unidade duplicada no mesmo CPA."})
            seen_units.add(unit_key)

        if not group["units"]:
            invalid_lines.append({"line": None, "value": group["name"], "error": "CPA/Grande Comando sem unidades."})

    return groups, invalid_lines


def parse_organizational_structure_file(path):
    with open(path, "r", encoding="utf-8-sig") as file:
        return parse_organizational_structure_text(file.read())


def _reset_identity(table_names):
    dialect = db.engine.dialect.name
    if dialect == "sqlite":
        has_sequence = db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'")
        ).first()
        if not has_sequence:
            return
        for table_name in table_names:
            db.session.execute(
                text("DELETE FROM sqlite_sequence WHERE name = :table_name"),
                {"table_name": table_name},
            )
    elif dialect == "postgresql":
        for table_name in table_names:
            db.session.execute(text(f"ALTER SEQUENCE {table_name}_id_seq RESTART WITH 1"))


def rebuild_organizational_structure_from_groups(groups, *, invalid_lines=None, logger=None):
    started_at = local_now()
    result = OrganizationalImportResult(started_at=started_at, invalid_lines=list(invalid_lines or []))
    perf_started = time.perf_counter()

    if result.invalid_lines:
        result.errors.append("Importação cancelada por linhas inválidas.")
        result.finished_at = local_now()
        result.elapsed_seconds = time.perf_counter() - perf_started
        result.rolled_back = True
        if logger:
            logger.error("organizational_import_invalid", extra=result.as_dict())
        return result

    try:
        with db.session.begin_nested():
            result.removed_units = OrganizationalUnit.query.count()
            result.removed_commands = OrganizationalCommand.query.count()
            result.removed_legacy_units = Unidades.query.count()

            Incidente.query.update({Incidente.command_id: None, Incidente.unit_id: None}, synchronize_session=False)
            OrganizationalUnit.query.delete(synchronize_session=False)
            OrganizationalCommand.query.delete(synchronize_session=False)
            Unidades.query.delete(synchronize_session=False)
            db.session.flush()
            db.session.expunge_all()
            _reset_identity(["organizational_units", "organizational_commands", "unidades"])

            for command_index, group in enumerate(groups, start=1):
                command = OrganizationalCommand(
                    name=group["name"],
                    active=True,
                    sort_order=command_index,
                )
                db.session.add(command)
                db.session.flush()
                result.imported_commands += 1

                for unit_index, unit_name in enumerate(group["units"]):
                    normalized_name = normalize_unit_name(unit_name)
                    unit = OrganizationalUnit(
                        command_id=command.id,
                        name=unit_name,
                        normalized_name=normalized_name,
                        active=True,
                        sort_order=0 if unit_name == "SEDE" else unit_index + 1,
                    )
                    db.session.add(unit)
                    db.session.add(Unidades(cpa=command.name, btl=unit_name))
                    result.imported_units += 1

            db.session.flush()
            validation_errors = validate_organizational_structure(groups)
            if validation_errors:
                result.errors.extend(validation_errors)
                raise ValueError("Validação final da estrutura organizacional falhou.")

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        result.rolled_back = True
        result.errors.append(str(exc))
        if logger:
            logger.exception("organizational_import_rollback", extra=result.as_dict())
    finally:
        result.finished_at = local_now()
        result.elapsed_seconds = time.perf_counter() - perf_started

    if logger and result.success:
        logger.info("organizational_import_success", extra=result.as_dict())
    return result


def rebuild_organizational_structure_from_file(path, *, logger=None):
    groups, invalid_lines = parse_organizational_structure_file(path)
    return rebuild_organizational_structure_from_groups(groups, invalid_lines=invalid_lines, logger=logger)


def validate_organizational_structure(groups):
    errors = []
    commands = OrganizationalCommand.query.order_by(OrganizationalCommand.sort_order.asc()).all()
    if len(commands) != len(groups):
        errors.append(f"Quantidade de CPAs divergente: esperado {len(groups)}, encontrado {len(commands)}.")

    command_names = [command.name for command in commands]
    if len(command_names) != len(set(name.casefold() for name in command_names)):
        errors.append("CPAs/Grandes Comandos duplicados encontrados.")

    for group in groups:
        command = OrganizationalCommand.query.filter_by(name=group["name"], active=True).first()
        if not command:
            errors.append(f"CPA/Grande Comando ausente: {group['name']}.")
            continue

        db_units = OrganizationalUnit.query.filter_by(command_id=command.id, active=True).order_by(
            OrganizationalUnit.sort_order.asc(),
            OrganizationalUnit.id.asc(),
        ).all()
        expected_units = group["units"]
        db_unit_names = [unit.name for unit in db_units]
        if db_unit_names != expected_units:
            errors.append(f"Unidades divergentes para {group['name']}.")

        normalized_names = [unit.normalized_name for unit in db_units]
        if len(normalized_names) != len(set(normalized_names)):
            errors.append(f"Unidades duplicadas para {group['name']}.")

    return errors
