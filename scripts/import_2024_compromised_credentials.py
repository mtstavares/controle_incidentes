"""Importa credenciais positivas sanitizadas de 2024.

Uso:
    python scripts/import_2024_compromised_credentials.py --file "C:\\caminho\\Credenciais 2024.xlsx"

O script nao versiona nem copia a planilha para o projeto. A coluna SENHA, se
existir, e descartada pelo servico antes das transformacoes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.datastructures import FileStorage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from app import create_app, db
from app.models import User
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.credential_monthly_totals import upsert_monthly_total
from app.services.credential_service import import_positive_2024_credential_spreadsheet
from config import DevelopmentConfig


MONTHLY_TOTALS_2024 = {
    2: 508,
    3: 854,
    4: 350,
    5: 453,
    6: 1900,
    7: 911,
    8: 586,
    9: 561,
    10: 485,
    11: 1965,
    12: 1251,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Importa credenciais positivas de 2024.")
    parser.add_argument("--file", required=True, help="Caminho absoluto ou relativo da planilha .xlsx/.xls.")
    parser.add_argument("--user", default="system", help="Usuario responsavel pela carga para auditoria.")
    parser.add_argument("--no-backup", action="store_true", help="Nao cria backup do banco SQLite antes da carga.")
    parser.add_argument("--dry-run", action="store_true", help="Valida e simula a carga sem confirmar no banco.")
    return parser.parse_args()


def _database_file_from_uri(uri):
    prefix = "sqlite:///"
    if not uri or not uri.startswith(prefix):
        return None
    return Path(uri[len(prefix):]).resolve()


def _backup_sqlite_database(app):
    database_path = _database_file_from_uri(app.config.get("SQLALCHEMY_DATABASE_URI"))
    if not database_path or not database_path.exists():
        return None
    backup_dir = Path(app.instance_path) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{database_path.stem}-pre-import-2024{database_path.suffix}.backup"
    shutil.copy2(database_path, backup_path)
    return backup_path


def _upsert_2024_totals():
    for month, total in MONTHLY_TOTALS_2024.items():
        upsert_monthly_total(2024, month, total, commit=False)


def main():
    args = parse_args()
    spreadsheet = Path(args.file).expanduser().resolve()
    if not spreadsheet.exists() or not spreadsheet.is_file():
        raise SystemExit("Planilha nao encontrada.")
    if spreadsheet.suffix.lower() not in {".xlsx", ".xls"}:
        raise SystemExit("Arquivo invalido. Use .xlsx ou .xls.")

    app = create_app(DevelopmentConfig)
    with app.app_context():
        db.create_all()
        actor = User.query.filter_by(username=args.user, is_active=True).first()
        backup_path = None if args.no_backup or args.dry_run else _backup_sqlite_database(app)

        try:
            _upsert_2024_totals()
            with spreadsheet.open("rb") as file_obj:
                storage = FileStorage(stream=file_obj, filename=spreadsheet.name)
                summary = import_positive_2024_credential_spreadsheet(storage, user_id=actor.id if actor else None)

            registrar_auditoria(
                acao=AuditAction.IMPORTAR_CREDENCIAIS,
                modulo="Credenciais comprometidas",
                entidade="CredencialComprometida",
                descricao="Carga administrativa de credenciais positivas de 2024 concluida.",
                alteracoes={
                    "ano": {"anterior": None, "novo": 2024},
                    "total_linhas": {"anterior": None, "novo": summary.total_rows},
                    "importadas": {"anterior": None, "novo": summary.imported},
                    "atualizadas": {"anterior": None, "novo": summary.updated},
                    "duplicidades_ignoradas": {"anterior": None, "novo": summary.duplicates_ignored},
                    "rejeitadas": {"anterior": None, "novo": summary.rejected},
                    "coluna_senha_ignorada": {"anterior": None, "novo": summary.ignored_password_column},
                    "positivas_por_mes": {"anterior": None, "novo": summary.positive_by_month},
                    "totais_consolidados": {"anterior": None, "novo": MONTHLY_TOTALS_2024},
                    "erros": {"anterior": None, "novo": summary.errors[:50]},
                },
                usuario=actor,
                commit=False,
                raise_on_error=True,
            )

            if args.dry_run:
                db.session.rollback()
            else:
                db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        print("Importacao 2024 concluida." if not args.dry_run else "Dry-run 2024 concluido.")
        print(f"Arquivo processado: {spreadsheet.name}")
        print("Ano processado: 2024")
        print(f"Backup criado: {backup_path.name if backup_path else 'nao aplicavel'}")
        print(f"Total de linhas lidas: {summary.total_rows}")
        print(f"Total de registros validos: {summary.imported + summary.updated + summary.duplicates_ignored}")
        print(f"Total de registros inseridos: {summary.imported}")
        print(f"Total de registros atualizados: {summary.updated}")
        print(f"Total de duplicidades ignoradas: {summary.duplicates_ignored}")
        print(f"Total de registros rejeitados: {summary.rejected}")
        print("Quantidade positiva por mes:")
        for month in sorted(summary.positive_by_month):
            print(f"- {month:02d}/2024: {summary.positive_by_month[month]}")
        print("Total consolidado por mes:")
        for month, total in MONTHLY_TOTALS_2024.items():
            print(f"- {month:02d}/2024: {total}")
        if summary.errors:
            print("Registros rejeitados, sem dados sensiveis:")
            for item in summary.errors[:50]:
                print(f"- linha {item['linha']}: {item['campo']} - {item['motivo']}")


if __name__ == "__main__":
    main()
