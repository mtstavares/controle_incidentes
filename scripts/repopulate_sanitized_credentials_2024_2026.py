"""Repovoa credenciais positivas sanitizadas de 2024 a 2026.

Uso:
    python scripts/repopulate_sanitized_credentials_2024_2026.py --file "C:\\caminho\\Credenciais 2024-25-26 - Sem senha.xlsx"

O script:
- cria backup do SQLite antes da carga real;
- remove somente registros individuais com data de coleta entre 2024 e 2026;
- reaplica os totais mensais consolidados por upsert;
- importa somente linhas com acesso positivo confirmado;
- nao imprime CPF, e-mail, nome, URL ou senha.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func
from werkzeug.datastructures import FileStorage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from app import create_app, db
from app.models import CredencialComprometida, User
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.credential_monthly_totals import seed_historical_monthly_totals
from app.services.credential_service import import_positive_credential_spreadsheet
from config import DevelopmentConfig


ALLOWED_YEARS = {2024, 2025, 2026}


def parse_args():
    parser = argparse.ArgumentParser(description="Repovoa credenciais positivas sanitizadas de 2024 a 2026.")
    parser.add_argument("--file", required=True, help="Caminho absoluto ou relativo da planilha .xlsx/.xls.")
    parser.add_argument("--user", default="system", help="Usuario responsavel pela carga para auditoria.")
    parser.add_argument("--dry-run", action="store_true", help="Valida e simula a carga sem confirmar no banco.")
    parser.add_argument("--no-backup", action="store_true", help="Nao cria backup do banco SQLite antes da carga.")
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
    backup_path = backup_dir / f"{database_path.stem}-pre-repopulate-credentials-2024-2026{database_path.suffix}.backup"
    shutil.copy2(database_path, backup_path)
    return backup_path


def _delete_existing_years():
    year_expr = func.strftime("%Y", CredencialComprometida.data_coleta)
    return (
        CredencialComprometida.query.filter(year_expr.in_([str(year) for year in sorted(ALLOWED_YEARS)]))
        .delete(synchronize_session=False)
    )


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
            seed_historical_monthly_totals(commit=False)
            removed = _delete_existing_years()
            with spreadsheet.open("rb") as file_obj:
                storage = FileStorage(stream=file_obj, filename=spreadsheet.name)
                summary = import_positive_credential_spreadsheet(
                    storage,
                    user_id=actor.id if actor else None,
                    allowed_years=ALLOWED_YEARS,
                )

            registrar_auditoria(
                acao=AuditAction.IMPORTAR_CREDENCIAIS,
                modulo="Credenciais comprometidas",
                entidade="CredencialComprometida",
                descricao="Recarga administrativa de credenciais positivas sanitizadas de 2024 a 2026 concluida.",
                alteracoes={
                    "anos": {"anterior": None, "novo": sorted(ALLOWED_YEARS)},
                    "removidas_antes_da_carga": {"anterior": None, "novo": removed},
                    "total_linhas": {"anterior": None, "novo": summary.total_rows},
                    "importadas": {"anterior": None, "novo": summary.imported},
                    "atualizadas": {"anterior": None, "novo": summary.updated},
                    "duplicidades_ignoradas": {"anterior": None, "novo": summary.duplicates_ignored},
                    "rejeitadas": {"anterior": None, "novo": summary.rejected},
                    "coluna_senha_ignorada": {"anterior": None, "novo": summary.ignored_password_column},
                    "positivas_por_competencia": {"anterior": None, "novo": summary.positive_by_competence},
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

        print("Recarga 2024-2026 concluida." if not args.dry_run else "Dry-run 2024-2026 concluido.")
        print(f"Arquivo processado: {spreadsheet.name}")
        print("Anos processados: 2024, 2025, 2026")
        print(f"Backup criado: {backup_path.name if backup_path else 'nao aplicavel'}")
        print(f"Registros removidos antes da carga: {removed}")
        print(f"Total de linhas lidas: {summary.total_rows}")
        print(f"Total de registros validos: {summary.imported + summary.updated + summary.duplicates_ignored}")
        print(f"Total de registros inseridos: {summary.imported}")
        print(f"Total de registros atualizados: {summary.updated}")
        print(f"Total de duplicidades ignoradas: {summary.duplicates_ignored}")
        print(f"Total de registros rejeitados: {summary.rejected}")
        print("Quantidade positiva por competencia:")
        for competence in sorted(summary.positive_by_competence):
            print(f"- {competence}: {summary.positive_by_competence[competence]}")
        if summary.errors:
            print("Registros rejeitados, sem dados sensiveis:")
            for item in summary.errors[:50]:
                print(f"- linha {item['linha']}: {item['campo']} - {item['motivo']}")


if __name__ == "__main__":
    main()
