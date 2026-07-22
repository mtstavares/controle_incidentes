import os
import sys

from dotenv import load_dotenv

load_dotenv()

os.makedirs("instance", exist_ok=True)
os.makedirs("logs", exist_ok=True)

from app import create_app, db
from app.models import StatusIncidente, TipoIncidente, Unidades, User
from app.seeds.organizational_units import seed_development_organizational_units
from app.services.user_service import gerar_hash_senha
from config import DevelopmentConfig


app = create_app(DevelopmentConfig)


def bootstrap_local_database():
    with app.app_context():
        if os.path.exists("./instance/divciber.db"):
            print("Banco de dados existente encontrado.")

        db.create_all()

        if not User.query.filter_by(username="system").first():
            db.session.add(User(
                username="system",
                name="system",
                email="system@local",
                profile="Admin",
                is_temp_password=False,
                must_change_password=False,
                password=gerar_hash_senha("system"),
            ))

        if not User.query.filter_by(username="admin").first():
            db.session.add(User(
                username="admin",
                name="Administrador Local",
                email="admin@local",
                profile="Admin",
                is_temp_password=False,
                must_change_password=False,
                password=gerar_hash_senha("admin123"),
            ))

        for status, desc in [
            ("Em Análise", "Incidente em análise"),
            ("Em Mitigação", "Incidente aguardando mitigação"),
            ("Encerrado", "Incidente encerrado"),
            ("Falso positivo", "Incidente classificado como falso positivo"),
        ]:
            if not StatusIncidente.query.filter_by(status=status).first():
                db.session.add(StatusIncidente(status=status, desc_status=desc))

        for tipo, desc in [
            ("Phishing", "Tentativa de fraude por mensagem ou link"),
            ("Malware", "Código malicioso ou suspeita de infecção"),
            ("Acesso indevido", "Acesso não autorizado ou tentativa"),
            ("Outro", "Incidente não classificado nas opções anteriores"),
        ]:
            if not TipoIncidente.query.filter_by(tipo_incidente=tipo).first():
                db.session.add(TipoIncidente(tipo_incidente=tipo, desc_incidente=desc))

        for cpa, btl in [
            ("CPA Local", "BTL Local"),
            ("Diretoria Local", "Unidade Local"),
        ]:
            if not Unidades.query.filter_by(cpa=cpa, btl=btl).first():
                db.session.add(Unidades(cpa=cpa, btl=btl))

        seed_result = seed_development_organizational_units(commit=False)
        db.session.commit()
        print(
            "Seed de unidades organizacionais concluída: "
            f"{seed_result['created']} criadas, {seed_result['existing']} já existentes."
        )
        print("Banco de dados e tabelas criados com sucesso.")
        print("Script de criação do banco de dados concluído.")


if len(sys.argv) > 1 and sys.argv[1] == "db":
    bootstrap_local_database()


if __name__ == "__main__":
    bootstrap_local_database()
    app.run(port=5005, host="0.0.0.0")
