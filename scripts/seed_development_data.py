import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.seeds.organizational_units import seed_development_organizational_units
from app.services.credential_monthly_totals import seed_historical_monthly_totals
from config import DevelopmentConfig


load_dotenv()


def main():
    app = create_app(DevelopmentConfig)
    with app.app_context():
        result = seed_development_organizational_units()
        credential_totals_result = seed_historical_monthly_totals()
        print(
            "Seed de unidades organizacionais concluída: "
            f"{result['created']} criadas, {result['existing']} já existentes."
        )

        print(
            "Seed de totais mensais de credenciais concluido: "
            f"{credential_totals_result['created']} criados, "
            f"{credential_totals_result['updated']} atualizados, "
            f"{credential_totals_result['unchanged']} sem alteracao."
        )


if __name__ == "__main__":
    main()
