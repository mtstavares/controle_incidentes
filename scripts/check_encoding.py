"""Fail when project text files contain invalid UTF-8, BOM, or mojibake."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "instance",
    "logs",
    ".pytest_cache",
    ".cache",
    "build",
    "dist",
    "coverage",
    "node_modules",
}
TEXT_SUFFIXES = {
    ".py",
    ".html",
    ".css",
    ".js",
    ".json",
    ".yml",
    ".yaml",
    ".md",
    ".txt",
    ".ini",
    ".cfg",
    ".toml",
    ".sql",
    ".mako",
    ".dockerignore",
    ".gitignore",
}
MOJIBAKE_MARKERS = (chr(0xFFFD),) + tuple(
    bytes(value, "ascii").decode("unicode_escape")
    for value in (
        "\\u00c3\\u0083",
        "\\u00c3\\u00a7",
        "\\u00c3\\u00a3",
        "\\u00c3\\u00b5",
        "\\u00c3\\u00a1",
        "\\u00c3\\u00a9",
        "\\u00c3\\u00aa",
        "\\u00c3\\u00ad",
        "\\u00c3\\u00b3",
        "\\u00c3\\u00ba",
        "\\u00c2\\u00ba",
        "\\u00c2\\u00aa",
        "\\u00e2\\u20ac\\u2122",
        "\\u00e2\\u20ac\\u0153",
        "\\u00e2\\u20ac",
        "Descri" + "?" + "\\u00e3o",
        "Transfer" + "?" + "ncia",
        "Relat\\u00c3",
        "Usu\\u00c3",
        "N\\u00c3",
        "Batalh\\u00c3",
        "Confidencialidade\\ufffd",
    )
)


def is_text_candidate(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts):
        return False
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {".gitignore", ".dockerignore", "Dockerfile"}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    failures: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or not is_text_candidate(path):
            continue

        data = path.read_bytes()
        rel = path.relative_to(ROOT)
        if data.startswith(b"\xef\xbb\xbf"):
            failures.append(f"{rel}: UTF-8 BOM encontrado")
            data = data[3:]

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            failures.append(f"{rel}: UTF-8 inválido em byte {exc.start}")
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            for marker in MOJIBAKE_MARKERS:
                if marker in line:
                    failures.append(f"{rel}:{line_no}: marcador suspeito {marker!r}")
                    break

    if failures:
        print("Falha na validação de encoding:")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("Validação de encoding concluída sem achados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
