"""Carga administrativa da base de credenciais comprometidas.

Uso:
    python scripts/populate_compromised_credentials.py --file "C:\\caminho\\Credenciais.xlsx"

O script descarta a coluna SENHA antes de transformar os dados e nao imprime
CPF completo, URL, observacoes, mensagem de bloqueio ou conteudo sensivel.
"""

from __future__ import annotations

import argparse
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
from app.services.credential_service import import_credential_spreadsheet
from config import DevelopmentConfig


def parse_args():
    parser = argparse.ArgumentParser(description="Popula credenciais comprometidas a partir de planilha Excel.")
    parser.add_argument("--file", required=True, help="Caminho absoluto ou relativo da planilha .xlsx/.xls.")
    parser.add_argument("--user", default="system", help="Usuario responsavel pela carga para auditoria.")
    return parser.parse_args()


def main():
    args = parse_args()
    spreadsheet = Path(args.file).expanduser().resolve()
    if not spreadsheet.exists() or not spreadsheet.is_file():
        raise SystemExit("Planilha nao encontrada.")

    app = create_app(DevelopmentConfig)
    with app.app_context():
        db.create_all()
        actor = User.query.filter_by(username=args.user, is_active=True).first()

        with spreadsheet.open("rb") as file_obj:
            storage = FileStorage(stream=file_obj, filename=spreadsheet.name)
            summary = import_credential_spreadsheet(storage, user_id=actor.id if actor else None)

        registrar_auditoria(
            acao=AuditAction.IMPORTAR_CREDENCIAIS,
            modulo="Credenciais comprometidas",
            entidade="CredencialComprometida",
            descricao="Carga administrativa de credenciais comprometidas concluida.",
            alteracoes={
                "total_linhas": {"anterior": None, "novo": summary.total_rows},
                "importadas": {"anterior": None, "novo": summary.imported},
                "atualizadas": {"anterior": None, "novo": summary.updated},
                "rejeitadas": {"anterior": None, "novo": summary.rejected},
                "coluna_senha_ignorada": {"anterior": None, "novo": summary.ignored_password_column},
                "erros": {"anterior": None, "novo": summary.errors[:50]},
            },
            usuario=actor,
            commit=False,
            raise_on_error=True,
        )
        db.session.commit()

        print("Carga concluida.")
        print(f"Linhas processadas: {summary.total_rows}")
        print(f"Importadas: {summary.imported}")
        print(f"Atualizadas: {summary.updated}")
        print(f"Rejeitadas: {summary.rejected}")
        print(f"Coluna SENHA ignorada: {'sim' if summary.ignored_password_column else 'nao'}")
        if summary.errors:
            print("Primeiros erros de validacao, sem dados sensiveis:")
            for item in summary.errors[:20]:
                print(f"- linha {item['linha']}: {item['motivo']}")


if __name__ == "__main__":
    main()
