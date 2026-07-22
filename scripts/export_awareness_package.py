import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from app import create_app
from app.models import ConscientizacaoCampanha
from app.services.awareness_image_service import get_awareness_upload_folder
from config import DevelopmentConfig


PACKAGE_VERSION = 1


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(campaigns, upload_folder):
    items = []
    for campaign in campaigns:
        filename = Path(campaign.imagem_arquivo or "").name
        if not filename or filename != campaign.imagem_arquivo:
            raise ValueError(f"Campanha {campaign.id} possui nome de imagem inválido.")

        image_path = upload_folder / filename
        if not image_path.exists() or not image_path.is_file():
            raise FileNotFoundError(f"Imagem não encontrada para campanha {campaign.id}: {filename}")

        items.append(
            {
                "id": campaign.id,
                "title": campaign.titulo,
                "publication_date": campaign.data_publicacao.isoformat(),
                "image_file": filename,
                "image_mime_type": campaign.imagem_mime_type,
                "image_size": image_path.stat().st_size,
                "image_sha256": file_sha256(image_path),
            }
        )

    return {"version": PACKAGE_VERSION, "type": "awareness_campaigns", "items": items}


def export_package(destination):
    app = create_app(DevelopmentConfig)
    with app.app_context():
        upload_folder = get_awareness_upload_folder()
        campaigns = (
            ConscientizacaoCampanha.query.order_by(
                ConscientizacaoCampanha.data_publicacao.desc(),
                ConscientizacaoCampanha.id.desc(),
            )
            .all()
        )
        manifest = build_manifest(campaigns, upload_folder)

        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as package:
            package.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for item in manifest["items"]:
                package.write(upload_folder / item["image_file"], f"images/{item['image_file']}")

    return len(manifest["items"]), destination


def main():
    parser = argparse.ArgumentParser(description="Exporta campanhas de conscientização para pacote de imagens.")
    parser.add_argument(
        "--output",
        default="runtime_exports/conscientizacoes_renderizadas.awareness.zip",
        help="Caminho do pacote .awareness.zip a ser gerado.",
    )
    args = parser.parse_args()

    total, destination = export_package(args.output)
    print(f"campanhas_exportadas={total}")
    print(f"pacote={destination}")


if __name__ == "__main__":
    main()
