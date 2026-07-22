import argparse
import hashlib
import posixpath
import re
import sys
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

from app import create_app, db
from app.models import ConscientizacaoCampanha
from app.services.awareness_image_service import get_awareness_upload_folder
from app.services.timezone_service import utc_now
from config import DevelopmentConfig


PPT_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

MONTHS = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}

IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def normalize_text(value):
    text = unicodedata.normalize("NFC", str(value or ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_section_name(section_name):
    text = normalize_text(section_name)
    match = re.match(r"^Publicad[ao]\s+em\s+(\d{2})([A-ZÇ]{3})(\d{2})\s*-\s*(.+)$", text, re.I)
    if not match:
        raise ValueError(f"Nome de seção fora do padrão: {text}")
    day, month_name, year_suffix, title = match.groups()
    month = MONTHS.get(month_name.upper())
    if not month:
        raise ValueError(f"Mês inválido no nome da seção: {text}")
    publication_date = date(2000 + int(year_suffix), month, int(day))
    return normalize_text(title), publication_date


def safe_filename(value):
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-zA-Z0-9._-]+", "-", ascii_text).strip("-._")
    return ascii_text.lower()[:120] or "campanha"


def read_xml(zip_file, name):
    return ET.fromstring(zip_file.read(name))


def relationship_map(zip_file, rel_path):
    root = read_xml(zip_file, rel_path)
    return {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in root.findall("rel:Relationship", PPT_NS)
    }


def slide_id_to_path(zip_file):
    presentation = read_xml(zip_file, "ppt/presentation.xml")
    rels = relationship_map(zip_file, "ppt/_rels/presentation.xml.rels")
    mapping = {}
    for slide in presentation.findall(".//p:sldId", PPT_NS):
        slide_id = slide.attrib["id"]
        rel_id = slide.attrib[f"{{{PPT_NS['r']}}}id"]
        mapping[slide_id] = "ppt/" + rels[rel_id]
    return mapping


def sections(zip_file):
    presentation = read_xml(zip_file, "ppt/presentation.xml")
    for section in presentation.findall(".//p14:section", PPT_NS):
        section_name = section.attrib.get("name", "")
        slide_ids = [item.attrib["id"] for item in section.findall(".//p14:sldId", PPT_NS)]
        if slide_ids:
            yield section_name, slide_ids[0]


def slide_image_targets(zip_file, slide_path):
    slide_name = Path(slide_path).name
    rel_path = f"ppt/slides/_rels/{slide_name}.rels"
    if rel_path not in zip_file.namelist():
        return []
    root = read_xml(zip_file, rel_path)
    targets = []
    for rel in root.findall("rel:Relationship", PPT_NS):
        if not rel.attrib.get("Type", "").endswith("/image"):
            continue
        target = rel.attrib.get("Target", "")
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(slide_path), target))
        extension = Path(resolved).suffix.lower()
        if extension in IMAGE_MIME and resolved in zip_file.namelist():
            targets.append(resolved)
    return targets


def choose_largest_image(zip_file, targets):
    if not targets:
        raise ValueError("Slide sem imagem compatível.")
    return max(targets, key=lambda item: zip_file.getinfo(item).file_size)


def stored_image_name(title, publication_date, image_bytes, extension):
    digest = hashlib.sha256(image_bytes).hexdigest()[:12]
    return f"{publication_date.isoformat()}-{safe_filename(title)}-{digest}{extension}"


def iter_campaigns(pptx_path):
    with zipfile.ZipFile(pptx_path) as zip_file:
        slide_paths = slide_id_to_path(zip_file)
        for section_name, slide_id in sections(zip_file):
            title, publication_date = parse_section_name(section_name)
            slide_path = slide_paths.get(slide_id)
            if not slide_path:
                raise ValueError(f"Slide não encontrado para a seção: {section_name}")
            image_path = choose_largest_image(zip_file, slide_image_targets(zip_file, slide_path))
            image_bytes = zip_file.read(image_path)
            extension = Path(image_path).suffix.lower()
            yield {
                "title": title,
                "publication_date": publication_date,
                "image_bytes": image_bytes,
                "extension": extension,
                "mime_type": IMAGE_MIME[extension],
                "source": str(pptx_path),
            }


def import_campaigns(paths, dry_run=False):
    upload_folder = get_awareness_upload_folder()
    imported = 0
    skipped = 0
    errors = []

    for path in paths:
        pptx_path = Path(path)
        if not pptx_path.exists() or pptx_path.suffix.lower() != ".pptx":
            errors.append(f"Arquivo inválido: {pptx_path}")
            continue

        for item in iter_campaigns(pptx_path):
            existing = ConscientizacaoCampanha.query.filter_by(
                titulo=item["title"],
                data_publicacao=item["publication_date"],
            ).first()
            if existing:
                skipped += 1
                continue

            stored_filename = stored_image_name(
                item["title"],
                item["publication_date"],
                item["image_bytes"],
                item["extension"],
            )
            destination = upload_folder / stored_filename
            if not dry_run:
                destination.write_bytes(item["image_bytes"])
                campaign = ConscientizacaoCampanha(
                    titulo=item["title"],
                    imagem_arquivo=stored_filename,
                    imagem_mime_type=item["mime_type"],
                    imagem_tamanho=len(item["image_bytes"]),
                    data_publicacao=item["publication_date"],
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                db.session.add(campaign)
            imported += 1

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()
    return imported, skipped, errors


def main():
    parser = argparse.ArgumentParser(description="Importa conscientizações de arquivos PPTX.")
    parser.add_argument("pptx", nargs="+", help="Caminho dos arquivos .pptx")
    parser.add_argument("--dry-run", action="store_true", help="Valida sem gravar no banco nem copiar imagens.")
    args = parser.parse_args()

    app = create_app(DevelopmentConfig)
    with app.app_context():
        imported, skipped, errors = import_campaigns(args.pptx, dry_run=args.dry_run)
        print(f"importadas={imported}")
        print(f"ignoradas_por_duplicidade={skipped}")
        print(f"erros={len(errors)}")
        for error in errors:
            print(f"- {error}")
        if errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
