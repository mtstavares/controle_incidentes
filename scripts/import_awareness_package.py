import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from app import create_app, db
from app.models import ConscientizacaoCampanha
from app.services.awareness_image_service import MIME_BY_EXTENSION, _validate_content, get_awareness_upload_folder
from app.services.timezone_service import utc_now
from config import DevelopmentConfig


PACKAGE_VERSION = 1


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def parse_manifest(package):
    try:
        manifest = json.loads(package.read("manifest.json").decode("utf-8"))
    except KeyError as exc:
        raise ValueError("Pacote sem manifest.json.") from exc

    if manifest.get("version") != PACKAGE_VERSION or manifest.get("type") != "awareness_campaigns":
        raise ValueError("Manifesto de conscientizações incompatível.")
    if not isinstance(manifest.get("items"), list):
        raise ValueError("Manifesto sem lista de campanhas.")
    return manifest


def parse_publication_date(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Data de publicação inválida: {value}") from exc


def validate_item(package, item):
    title = str(item.get("title") or "").strip()
    if not title:
        raise ValueError("Campanha sem título.")

    filename = Path(str(item.get("image_file") or "")).name
    if not filename or filename != item.get("image_file"):
        raise ValueError(f"Nome de imagem inválido para campanha: {title}")

    extension = Path(filename).suffix.lower()
    if extension not in MIME_BY_EXTENSION:
        raise ValueError(f"Extensão de imagem não permitida: {filename}")

    publication_date = parse_publication_date(item.get("publication_date"))
    image_member = f"images/{filename}"
    try:
        image_bytes = package.read(image_member)
    except KeyError as exc:
        raise ValueError(f"Imagem ausente no pacote: {filename}") from exc

    if not image_bytes:
        raise ValueError(f"Imagem vazia no pacote: {filename}")
    if sha256_bytes(image_bytes) != item.get("image_sha256"):
        raise ValueError(f"Hash divergente para imagem: {filename}")
    if len(image_bytes) != int(item.get("image_size") or 0):
        raise ValueError(f"Tamanho divergente para imagem: {filename}")

    _validate_content(image_bytes, extension)
    return {
        "title": title,
        "publication_date": publication_date,
        "image_file": filename,
        "image_mime_type": item.get("image_mime_type") or MIME_BY_EXTENSION[extension],
        "image_size": len(image_bytes),
        "image_bytes": image_bytes,
    }


def remove_old_images_after_commit(upload_folder, filenames):
    for filename in filenames:
        if not filename:
            continue
        candidate = (upload_folder / filename).resolve()
        if upload_folder.resolve() not in candidate.parents:
            continue
        candidate.unlink(missing_ok=True)


def cleanup_written_files(upload_folder, filenames):
    for filename in filenames:
        try:
            candidate = (upload_folder / filename).resolve()
            if upload_folder.resolve() in candidate.parents:
                candidate.unlink(missing_ok=True)
        except OSError:
            pass


def import_package(package_path, dry_run=False):
    upload_folder = get_awareness_upload_folder()
    imported = 0
    updated = 0
    skipped = 0
    written_files = []
    old_files_to_delete = []

    try:
        with zipfile.ZipFile(package_path) as package:
            manifest = parse_manifest(package)
            for raw_item in manifest["items"]:
                item = validate_item(package, raw_item)
                existing = ConscientizacaoCampanha.query.filter_by(
                    titulo=item["title"],
                    data_publicacao=item["publication_date"],
                ).first()

                if existing and existing.imagem_arquivo == item["image_file"]:
                    skipped += 1
                    continue

                if not dry_run:
                    destination = upload_folder / item["image_file"]
                    destination.write_bytes(item["image_bytes"])
                    written_files.append(item["image_file"])

                if existing:
                    updated += 1
                    if not dry_run:
                        old_files_to_delete.append(existing.imagem_arquivo)
                        existing.imagem_arquivo = item["image_file"]
                        existing.imagem_mime_type = item["image_mime_type"]
                        existing.imagem_tamanho = item["image_size"]
                        existing.updated_at = utc_now()
                    continue

                imported += 1
                if not dry_run:
                    db.session.add(
                        ConscientizacaoCampanha(
                            titulo=item["title"],
                            imagem_arquivo=item["image_file"],
                            imagem_mime_type=item["image_mime_type"],
                            imagem_tamanho=item["image_size"],
                            data_publicacao=item["publication_date"],
                            created_at=utc_now(),
                            updated_at=utc_now(),
                        )
                    )

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
            remove_old_images_after_commit(upload_folder, old_files_to_delete)
    except Exception:
        db.session.rollback()
        cleanup_written_files(upload_folder, written_files)
        raise

    return imported, updated, skipped


def main():
    parser = argparse.ArgumentParser(description="Importa pacote de imagens de conscientização.")
    parser.add_argument("package", help="Caminho do pacote .awareness.zip")
    parser.add_argument("--dry-run", action="store_true", help="Valida sem gravar no banco nem copiar imagens.")
    args = parser.parse_args()

    package_path = Path(args.package)
    if not package_path.exists() or package_path.suffix.lower() != ".zip":
        raise SystemExit("Pacote inválido ou não encontrado.")

    app = create_app(DevelopmentConfig)
    with app.app_context():
        imported, updated, skipped = import_package(package_path, dry_run=args.dry_run)
        print(f"importadas={imported}")
        print(f"atualizadas={updated}")
        print(f"ignoradas_sem_mudanca={skipped}")


if __name__ == "__main__":
    main()
