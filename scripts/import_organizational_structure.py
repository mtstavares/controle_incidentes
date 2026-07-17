import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.services.organizational_import_service import rebuild_organizational_structure_from_file
from config import DevelopmentConfig


def main():
    parser = argparse.ArgumentParser(description="Reconstrói CPAs/Grandes Comandos e Batalhões/Unidades a partir de TXT oficial.")
    parser.add_argument("source", type=Path, help="Caminho do arquivo TXT oficial.")
    args = parser.parse_args()

    app = create_app(DevelopmentConfig)
    with app.app_context():
        result = rebuild_organizational_structure_from_file(args.source, logger=app.logger)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, default=str))
        return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
