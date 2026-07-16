from dotenv import load_dotenv

from app import create_app
from app.seeds.organizational_units import seed_development_organizational_units


load_dotenv()


def main():
    app = create_app()
    with app.app_context():
        result = seed_development_organizational_units()
        print(
            "Seed de unidades organizacionais concluída: "
            f"{result['created']} criadas, {result['existing']} já existentes."
        )


if __name__ == "__main__":
    main()
