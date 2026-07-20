import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.migrate_legacy_database import run_migration


LEGACY_SCHEMA = """
CREATE TABLE user (
    id INTEGER PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL UNIQUE,
    profile VARCHAR(50) NOT NULL,
    is_temp_password BOOLEAN NOT NULL,
    password VARCHAR(256) NOT NULL
);
CREATE TABLE status_incidente (
    id INTEGER PRIMARY KEY,
    status VARCHAR(50) NOT NULL,
    desc_status TEXT
);
CREATE TABLE tipo_incidente (
    id INTEGER PRIMARY KEY,
    tipo_incidente VARCHAR(100) NOT NULL,
    desc_incidente TEXT
);
CREATE TABLE unidades (
    id INTEGER PRIMARY KEY,
    cpa VARCHAR(100) NOT NULL,
    btl VARCHAR(100) NOT NULL
);
CREATE TABLE incidente (
    id INTEGER PRIMARY KEY,
    incident_type VARCHAR(100) NOT NULL,
    report_number VARCHAR(50) NOT NULL,
    ticket_number VARCHAR(50),
    cpa VARCHAR(100) NOT NULL,
    btl VARCHAR(100) NOT NULL,
    cia VARCHAR(100),
    description TEXT NOT NULL,
    start_date DATETIME NOT NULL,
    end_date DATETIME,
    status_incident VARCHAR(50) NOT NULL,
    user_id INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES user(id)
);
CREATE TABLE incidente_obs (
    id INTEGER PRIMARY KEY,
    texto_observacao TEXT NOT NULL,
    data_observacao DATETIME NOT NULL,
    usuario_id INTEGER NOT NULL,
    incidente_id INTEGER NOT NULL,
    FOREIGN KEY(usuario_id) REFERENCES user(id),
    FOREIGN KEY(incidente_id) REFERENCES incidente(id)
);
"""


CURRENT_SCHEMA = """
CREATE TABLE user (
    id INTEGER PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(150) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    profile VARCHAR(50) NOT NULL,
    is_temp_password BOOLEAN NOT NULL DEFAULT 1,
    must_change_password BOOLEAN NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    deleted_by_id INTEGER,
    password VARCHAR(256) NOT NULL,
    created_at DATETIME,
    updated_at DATETIME,
    deleted_at DATETIME,
    FOREIGN KEY(deleted_by_id) REFERENCES user(id)
);
CREATE TABLE status_incidente (
    id INTEGER PRIMARY KEY,
    status VARCHAR(50) NOT NULL,
    desc_status TEXT
);
CREATE TABLE tipo_incidente (
    id INTEGER PRIMARY KEY,
    tipo_incidente VARCHAR(100) NOT NULL,
    desc_incidente TEXT
);
CREATE TABLE unidades (
    id INTEGER PRIMARY KEY,
    cpa VARCHAR(100) NOT NULL,
    btl VARCHAR(100) NOT NULL
);
CREATE TABLE organizational_commands (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT 1,
    sort_order INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    deleted_at DATETIME
);
CREATE TABLE organizational_units (
    id INTEGER PRIMARY KEY,
    command_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    normalized_name VARCHAR(100) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT 1,
    sort_order INTEGER,
    created_at DATETIME,
    updated_at DATETIME,
    deleted_at DATETIME,
    UNIQUE(command_id, normalized_name),
    FOREIGN KEY(command_id) REFERENCES organizational_commands(id)
);
CREATE TABLE incidente (
    id INTEGER PRIMARY KEY,
    message_number VARCHAR(100),
    incident_type VARCHAR(100) NOT NULL,
    report_number VARCHAR(50) NOT NULL,
    ticket_number VARCHAR(50),
    cpa VARCHAR(100) NOT NULL,
    btl VARCHAR(100) NOT NULL,
    cia VARCHAR(100),
    description TEXT NOT NULL,
    start_date DATETIME NOT NULL,
    end_date DATETIME,
    status_incident VARCHAR(50) NOT NULL,
    command_id INTEGER,
    unit_id INTEGER,
    user_id INTEGER NOT NULL,
    description_plain_text TEXT,
    created_at DATETIME,
    updated_at DATETIME,
    deleted_at DATETIME,
    FOREIGN KEY(command_id) REFERENCES organizational_commands(id),
    FOREIGN KEY(unit_id) REFERENCES organizational_units(id),
    FOREIGN KEY(user_id) REFERENCES user(id)
);
CREATE TABLE incidente_obs (
    id INTEGER PRIMARY KEY,
    texto_observacao TEXT NOT NULL,
    data_observacao DATETIME NOT NULL,
    usuario_id INTEGER NOT NULL,
    incidente_id INTEGER NOT NULL,
    created_at DATETIME,
    updated_at DATETIME,
    deleted_at DATETIME,
    FOREIGN KEY(usuario_id) REFERENCES user(id),
    FOREIGN KEY(incidente_id) REFERENCES incidente(id)
);
"""


class LegacyMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.legacy_db = self.root / "legacy.db"
        self.current_db = self.root / "current.db"
        self._create_legacy_db()
        self._create_current_db()

    def tearDown(self):
        self.tmp.cleanup()

    def _create_legacy_db(self):
        with closing(sqlite3.connect(self.legacy_db)) as con:
            con.executescript(LEGACY_SCHEMA)
            con.execute(
                "INSERT INTO user VALUES (1, '142467', 'SGT LEGADO', 'legado@test.local', 'Admin', 0, 'hash')"
            )
            con.execute("INSERT INTO status_incidente VALUES (1, 'Em Análise', NULL)")
            con.execute("INSERT INTO tipo_incidente VALUES (1, 'Tentativa de intrusão', NULL)")
            con.execute("INSERT INTO unidades VALUES (1, 'CPA/M1', 'CPA/M-1 - SEDE')")
            con.execute("INSERT INTO unidades VALUES (2, 'CPA/M1', '7ºBPM/M')")
            con.execute(
                """
                INSERT INTO incidente
                VALUES (1, 'Tentativa de intrusão', '001/150/26', 'RDS1', 'CPA/M1', '7ºBPM/M',
                        '1 CIA', '<p>Descrição legada</p>', '2026-07-15 13:20:10',
                        NULL, 'Em Análise', 1)
                """
            )
            con.execute(
                "INSERT INTO incidente_obs VALUES (1, 'Observação legada', '2026-07-15 14:00:00', 1, 1)"
            )
            con.commit()

    def _create_current_db(self):
        with closing(sqlite3.connect(self.current_db)) as con:
            con.execute("PRAGMA foreign_keys = ON")
            con.executescript(CURRENT_SCHEMA)
            con.commit()

    def _count(self, table):
        with closing(sqlite3.connect(self.current_db)) as con:
            return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def test_dry_run_rolls_back_all_changes(self):
        report = run_migration(self.legacy_db, self.current_db, dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertEqual(self._count("incidente"), 0)
        self.assertEqual(self._count("organizational_commands"), 0)

    def test_migration_is_idempotent_and_normalizes_relationships(self):
        first = run_migration(self.legacy_db, self.current_db)
        second = run_migration(self.legacy_db, self.current_db)

        self.assertEqual(first.integrity_check, [("ok",)])
        self.assertEqual(second.integrity_check, [("ok",)])
        self.assertEqual(self._count("incidente"), 1)
        self.assertEqual(self._count("incidente_obs"), 1)

        with closing(sqlite3.connect(self.current_db)) as con:
            con.row_factory = sqlite3.Row
            incident = con.execute("SELECT cpa, btl, command_id, unit_id FROM incidente").fetchone()
            command = con.execute("SELECT name FROM organizational_commands WHERE id = ?", (incident["command_id"],)).fetchone()
            unit = con.execute("SELECT name FROM organizational_units WHERE id = ?", (incident["unit_id"],)).fetchone()
            sede = con.execute("SELECT COUNT(*) FROM organizational_units WHERE name = 'SEDE'").fetchone()[0]
            self.assertEqual(incident["cpa"], "CPA/M-1")
            self.assertEqual(incident["btl"], "7º BPM/M")
            self.assertEqual(command["name"], "CPA/M-1")
            self.assertEqual(unit["name"], "7º BPM/M")
            self.assertEqual(sede, 1)

    def test_invalid_current_database_fails_before_writes(self):
        invalid = self.root / "invalid.db"
        with closing(sqlite3.connect(invalid)) as con:
            con.execute("CREATE TABLE user (id INTEGER PRIMARY KEY)")
            con.commit()
        with self.assertRaises(ValueError):
            run_migration(self.legacy_db, invalid)

    def test_gitignore_protects_database_and_backup_patterns(self):
        gitignore = Path(".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.db", gitignore)
        self.assertIn("*.sqlite3", gitignore)
        self.assertIn("backups/", gitignore)
        self.assertIn("instance/", gitignore)
        self.assertIn(".env.*", gitignore)


if __name__ == "__main__":
    unittest.main()
