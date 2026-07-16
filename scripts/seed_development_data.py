import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
