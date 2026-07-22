import argparse
import hashlib
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
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
from app.services.awareness_image_service import MIME_BY_EXTENSION, get_awareness_upload_folder
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

IMAGE_MIME = MIME_BY_EXTENSION


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


def slide_id_to_index(zip_file):
    presentation = read_xml(zip_file, "ppt/presentation.xml")
    mapping = {}
    for index, slide in enumerate(presentation.findall(".//p:sldId", PPT_NS), start=1):
        mapping[slide.attrib["id"]] = index
    return mapping


def section_specs(zip_file):
    presentation = read_xml(zip_file, "ppt/presentation.xml")
    for section in presentation.findall(".//p14:section", PPT_NS):
        section_name = section.attrib.get("name", "")
        slide_ids = [item.attrib["id"] for item in section.findall(".//p14:sldId", PPT_NS)]
        if slide_ids:
            title, publication_date = parse_section_name(section_name)
            yield {
                "section_name": section_name,
                "slide_id": slide_ids[0],
                "title": title,
                "publication_date": publication_date,
            }


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


def render_slides_with_powerpoint(pptx_path, slide_indices, output_dir):
    if os.name != "nt":
        raise RuntimeError("Renderização via PowerPoint está disponível apenas no Windows.")

    script = """
param(
    [string]$PptxPath,
    [string]$OutputDir,
    [string]$SlideIndexes
)
$ErrorActionPreference = "Stop"
$powerPoint = $null
$presentation = $null
try {
    $powerPoint = New-Object -ComObject PowerPoint.Application
    $presentation = $powerPoint.Presentations.Open($PptxPath, $true, $false, $false)
    foreach ($indexText in $SlideIndexes.Split(",")) {
        if ([string]::IsNullOrWhiteSpace($indexText)) { continue }
        $index = [int]$indexText
        $destination = Join-Path $OutputDir ("slide_{0:D3}.png" -f $index)
        $presentation.Slides.Item($index).Export($destination, "PNG", 1920, 1080)
    }
}
finally {
    if ($presentation -ne $null) { $presentation.Close() }
    if ($powerPoint -ne $null) {
        $powerPoint.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", encoding="utf-8", delete=False) as script_file:
        script_file.write(script)
        script_path = script_file.name
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
                "-PptxPath",
                str(pptx_path),
                "-OutputDir",
                str(output_dir),
                "-SlideIndexes",
                ",".join(str(index) for index in slide_indices),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)


def _find_required_command(candidates, package_hint):
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    names = ", ".join(candidates)
    raise RuntimeError(f"Comando obrigatório não encontrado ({names}). Instale {package_hint}.")


def _converted_pdf_path(output_dir, pptx_path):
    preferred = output_dir / f"{pptx_path.stem}.pdf"
    if preferred.exists():
        return preferred
    pdf_files = sorted(output_dir.glob("*.pdf"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not pdf_files:
        raise RuntimeError("LibreOffice não gerou o PDF intermediário do PPTX.")
    return pdf_files[0]


def render_slides_with_libreoffice(pptx_path, slide_indices, output_dir):
    soffice = _find_required_command(["soffice", "libreoffice"], "LibreOffice")
    pdftoppm = _find_required_command(["pdftoppm"], "poppler-utils")

    with tempfile.TemporaryDirectory(prefix="awareness-pdf-") as pdf_temp_dir:
        pdf_dir = Path(pdf_temp_dir)
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_dir),
                str(pptx_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        pdf_path = _converted_pdf_path(pdf_dir, pptx_path)

        for slide_index in slide_indices:
            prefix = output_dir / f"slide_{slide_index:03}"
            subprocess.run(
                [
                    pdftoppm,
                    "-f",
                    str(slide_index),
                    "-l",
                    str(slide_index),
                    "-png",
                    "-r",
                    "144",
                    str(pdf_path),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            generated = output_dir / f"slide_{slide_index:03}-1.png"
            final_path = output_dir / f"slide_{slide_index:03}.png"
            if not generated.exists():
                raise RuntimeError(f"Poppler não gerou a imagem do slide {slide_index}.")
            generated.replace(final_path)


def render_slides(pptx_path, slide_indices, output_dir):
    if os.name == "nt":
        render_slides_with_powerpoint(pptx_path, slide_indices, output_dir)
    else:
        render_slides_with_libreoffice(pptx_path, slide_indices, output_dir)


def iter_rendered_slide_campaigns(pptx_path):
    with zipfile.ZipFile(pptx_path) as zip_file:
        index_by_slide_id = slide_id_to_index(zip_file)
        specs = list(section_specs(zip_file))

    slide_indices = []
    for spec in specs:
        slide_index = index_by_slide_id.get(spec["slide_id"])
        if not slide_index:
            raise ValueError(f"Slide não encontrado para a seção: {spec['section_name']}")
        spec["slide_index"] = slide_index
        slide_indices.append(slide_index)

    with tempfile.TemporaryDirectory(prefix="awareness-slides-") as temp_dir:
        output_dir = Path(temp_dir)
        render_slides(pptx_path, slide_indices, output_dir)
        for spec in specs:
            image_path = output_dir / f"slide_{spec['slide_index']:03}.png"
            if not image_path.exists() or image_path.stat().st_size <= 0:
                raise ValueError(f"Falha ao renderizar slide da seção: {spec['section_name']}")
            yield {
                "title": spec["title"],
                "publication_date": spec["publication_date"],
                "image_bytes": image_path.read_bytes(),
                "extension": ".png",
                "mime_type": "image/png",
                "source": str(pptx_path),
            }


def iter_embedded_image_campaigns(pptx_path):
    with zipfile.ZipFile(pptx_path) as zip_file:
        slide_paths = slide_id_to_path(zip_file)
        for spec in section_specs(zip_file):
            slide_path = slide_paths.get(spec["slide_id"])
            if not slide_path:
                raise ValueError(f"Slide não encontrado para a seção: {spec['section_name']}")
            image_path = choose_largest_image(zip_file, slide_image_targets(zip_file, slide_path))
            image_bytes = zip_file.read(image_path)
            extension = Path(image_path).suffix.lower()
            yield {
                "title": spec["title"],
                "publication_date": spec["publication_date"],
                "image_bytes": image_bytes,
                "extension": extension,
                "mime_type": IMAGE_MIME[extension],
                "source": str(pptx_path),
            }


def stored_image_name(title, publication_date, image_bytes, extension):
    digest = hashlib.sha256(image_bytes).hexdigest()[:12]
    return f"{publication_date.isoformat()}-{safe_filename(title)}-{digest}{extension}"


def iter_campaigns(pptx_path, *, extract_embedded_images=False):
    if extract_embedded_images:
        yield from iter_embedded_image_campaigns(pptx_path)
    else:
        yield from iter_rendered_slide_campaigns(pptx_path)


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


def import_campaigns(paths, dry_run=False, replace_images=False, extract_embedded_images=False):
    upload_folder = get_awareness_upload_folder()
    imported = 0
    updated = 0
    skipped = 0
    errors = []
    written_files = []
    old_files_to_delete = []

    try:
        for path in paths:
            pptx_path = Path(path)
            if not pptx_path.exists() or pptx_path.suffix.lower() != ".pptx":
                errors.append(f"Arquivo inválido: {pptx_path}")
                continue

            for item in iter_campaigns(pptx_path, extract_embedded_images=extract_embedded_images):
                existing = ConscientizacaoCampanha.query.filter_by(
                    titulo=item["title"],
                    data_publicacao=item["publication_date"],
                ).first()
                if existing and not replace_images:
                    skipped += 1
                    continue

                stored_filename = stored_image_name(
                    item["title"],
                    item["publication_date"],
                    item["image_bytes"],
                    item["extension"],
                )
                destination = upload_folder / stored_filename

                if existing:
                    updated += 1
                    if not dry_run:
                        if existing.imagem_arquivo != stored_filename:
                            destination.write_bytes(item["image_bytes"])
                            written_files.append(stored_filename)
                            old_files_to_delete.append(existing.imagem_arquivo)
                        existing.imagem_arquivo = stored_filename
                        existing.imagem_mime_type = item["mime_type"]
                        existing.imagem_tamanho = len(item["image_bytes"])
                        existing.updated_at = utc_now()
                    continue

                imported += 1
                if not dry_run:
                    destination.write_bytes(item["image_bytes"])
                    written_files.append(stored_filename)
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

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
            remove_old_images_after_commit(upload_folder, old_files_to_delete)
    except Exception:
        db.session.rollback()
        cleanup_written_files(upload_folder, written_files)
        raise

    return imported, updated, skipped, errors


def main():
    parser = argparse.ArgumentParser(description="Importa conscientizações de arquivos PPTX.")
    parser.add_argument("pptx", nargs="+", help="Caminho dos arquivos .pptx")
    parser.add_argument("--dry-run", action="store_true", help="Valida sem gravar no banco nem copiar imagens.")
    parser.add_argument(
        "--replace-images",
        action="store_true",
        help="Substitui a imagem de campanhas já cadastradas com mesmo título e data.",
    )
    parser.add_argument(
        "--extract-embedded-images",
        action="store_true",
        help="Modo legado: extrai a maior imagem embutida no slide em vez de renderizar o slide completo.",
    )
    args = parser.parse_args()

    app = create_app(DevelopmentConfig)
    with app.app_context():
        imported, updated, skipped, errors = import_campaigns(
            args.pptx,
            dry_run=args.dry_run,
            replace_images=args.replace_images,
            extract_embedded_images=args.extract_embedded_images,
        )
        print(f"importadas={imported}")
        print(f"atualizadas={updated}")
        print(f"ignoradas_por_duplicidade={skipped}")
        print(f"erros={len(errors)}")
        for error in errors:
            print(f"- {error}")
        if errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
