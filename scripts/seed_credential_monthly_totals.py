import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.credential_monthly_totals import seed_historical_monthly_totals
from config import DevelopmentConfig


load_dotenv()


def main():
    app = create_app(DevelopmentConfig)
    with app.app_context():
        result = seed_historical_monthly_totals()
        print(
            "Seed de totais mensais de credenciais concluido: "
            f"{result['created']} criados, {result['updated']} atualizados, "
            f"{result['unchanged']} sem alteracao."
        )


if __name__ == "__main__":
    main()
