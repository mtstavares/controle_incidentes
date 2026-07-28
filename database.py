from app import create_app, db


def create_database_schema():
    app = create_app()
    with app.app_context():
        db.create_all()
        app.logger.info("Banco de dados e tabelas validados com sucesso.")


if __name__ == "__main__":
    create_database_schema()
