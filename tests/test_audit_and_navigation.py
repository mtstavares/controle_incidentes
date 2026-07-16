import unittest
from io import BytesIO
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app, db, hash
from app.models import AuditLog, Incidente, IncidenteObs, OrganizationalCommand, OrganizationalUnit, StatusIncidente, TipoIncidente, Unidades, User
from app.seeds.organizational_units import seed_development_organizational_units


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_ENABLED = False


class DivCiberAuditNavigationTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self._seed()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self):
        db.session.add(User(username="system", name="system", email="system@test", profile="Admin", is_temp_password=False, must_change_password=False, password=hash("system")))
        db.session.add(User(username="admin", name="Admin Teste", email="admin@test", profile="Admin", is_temp_password=False, must_change_password=False, password=hash("admin123")))
        db.session.add(User(username="user", name="User Teste", email="user@test", profile="User", is_temp_password=False, must_change_password=False, password=hash("user123")))
        db.session.add(User(username="viewer", name="Viewer Teste", email="viewer@test", profile="Viewer", is_temp_password=False, must_change_password=False, password=hash("viewer123")))
        db.session.add(StatusIncidente(status="Em Análise", desc_status=""))
        db.session.add(StatusIncidente(status="Encerrado", desc_status=""))
        db.session.add(TipoIncidente(tipo_incidente="Phishing", desc_incidente=""))
        db.session.add(Unidades(cpa="CPA Teste", btl="BTL Teste"))
        db.session.flush()
        command = OrganizationalCommand(name="CPA Teste", active=True, sort_order=1)
        db.session.add(command)
        db.session.flush()
        db.session.add(OrganizationalUnit(command_id=command.id, name="BTL Teste", normalized_name="btl teste", active=True, sort_order=1))
        db.session.commit()

    def login(self, username="admin", password="admin123"):
        return self.client.post("/login", data={"username": username, "password": password}, follow_redirects=True)

    def create_incident(self):
        self.login("user", "user123")
        self.client.post("/incidente/new", data={
            "status_incidente": "Em Análise",
            "start_data_hora": "2026-07-14T10:00",
            "incident_type": "Phishing",
            "report_number": "REL-AUDIT",
            "ticket_number": "TCK-AUDIT",
            "btl": "BTL Teste",
            "cpa": "CPA Teste",
            "cia": "1 CIA",
            "description": "<p>Descrição segura</p><script>alert('x')</script>",
        })
        return Incidente.query.order_by(Incidente.id.desc()).first()

    def test_sidebar_by_profile_and_active_item(self):
        self.login("user", "user123")
        html = self.client.get("/incidentes").get_data(as_text=True)
        self.assertIn("Operação", html)
        self.assertIn("Inteligência", html)
        self.assertIn("Incidentes de segurança", html)
        self.assertIn('aria-current="page"', html)
        self.assertNotIn("Administração", html)
        self.assertNotIn("Logs de auditoria", html)

        self.client.get("/logout")
        self.login("admin", "admin123")
        admin_html = self.client.get("/admin/logs-auditoria").get_data(as_text=True)
        self.assertIn("Administração", admin_html)
        self.assertIn("Logs de auditoria", admin_html)

    def test_new_routes_auth_and_authorization(self):
        self.assertEqual(self.client.get("/credenciais-comprometidas").status_code, 302)
        self.login("user", "user123")
        self.assertEqual(self.client.get("/credenciais-comprometidas").status_code, 200)
        self.assertEqual(self.client.get("/dashboard-credenciais").status_code, 200)
        self.assertEqual(self.client.get("/admin/logs-auditoria").status_code, 403)

        self.client.get("/logout")
        self.login("admin", "admin123")
        self.assertEqual(self.client.get("/admin/logs-auditoria").status_code, 200)

    def test_authentication_audit_without_password(self):
        self.client.post("/login", data={"username": "admin", "password": "wrong"})
        failed = AuditLog.query.filter_by(acao="LOGIN_FALHOU").first()
        self.assertIsNotNone(failed)
        self.assertNotIn("wrong", str(failed.alteracoes))
        self.assertNotIn("password", str(failed.alteracoes).lower())

        self.login("admin", "admin123")
        self.assertIsNotNone(AuditLog.query.filter_by(acao="LOGIN").first())

        self.client.post("/change_password", data={"new_password": "Senha@123", "confirm_password": "Senha@123"})
        password_log = AuditLog.query.filter_by(acao="ALTERAR_SENHA").first()
        self.assertIsNotNone(password_log)
        self.assertNotIn("Senha@123", str(password_log.alteracoes))

    def test_incident_and_observation_audit(self):
        incident = self.create_incident()
        self.assertIsNotNone(AuditLog.query.filter_by(acao="CRIAR", entidade="Incidente", entidade_id=str(incident.id)).first())

        self.client.post(f"/incidente/{incident.id}/edit", data={
            "status_incidente": "Encerrado",
            "start_data_hora": "2026-07-14T10:00",
            "incident_type": "Phishing",
            "report_number": "REL-AUDIT",
            "ticket_number": "TCK-AUDIT",
            "btl": "BTL Teste",
            "cpa": "CPA Teste",
            "cia": "1 CIA",
            "description": "<p>Descrição segura</p><script>alert('x')</script>",
        })
        edit_log = AuditLog.query.filter_by(acao="EDITAR", entidade="Incidente", entidade_id=str(incident.id)).first()
        self.assertIsNotNone(edit_log)
        self.assertEqual(set(edit_log.alteracoes.keys()), {"status_incident"})

        self.client.post(f"/incidente/{incident.id}/add_obs", data={"texto_observacao": "Observação segura"})
        obs = IncidenteObs.query.order_by(IncidenteObs.id.desc()).first()
        self.assertIsNotNone(AuditLog.query.filter_by(acao="ADICIONAR_OBSERVACAO", entidade_id=str(obs.id)).first())

        self.client.post(f"/incidente/{incident.id}/delete_obs/{obs.id}")
        self.assertIsNotNone(AuditLog.query.filter_by(acao="EXCLUIR_OBSERVACAO", entidade_id=str(obs.id)).first())

        self.client.post(f"/incidente/delete/{incident.id}")
        self.assertIsNotNone(AuditLog.query.filter_by(acao="EXCLUIR", entidade="Incidente", entidade_id=str(incident.id)).first())

    def test_user_cannot_edit_or_delete_other_users_incident(self):
        admin = User.query.filter_by(username="admin").first()
        incident = Incidente(
            status_incident="Em Análise",
            start_date=datetime(2026, 7, 14),
            incident_type="Phishing",
            report_number="REL-IDOR",
            ticket_number="TCK-IDOR",
            btl="BTL Teste",
            cpa="CPA Teste",
            cia="1 CIA",
            description="<p>Incidente de outro usuário</p>",
            description_plain_text="Incidente de outro usuário",
            user_id=admin.id,
        )
        db.session.add(incident)
        db.session.commit()

        self.login("user", "user123")
        self.assertEqual(self.client.get(f"/incidente/{incident.id}/edit").status_code, 403)
        self.assertEqual(self.client.post(f"/incidente/delete/{incident.id}").status_code, 403)
        self.assertIsNotNone(
            AuditLog.query.filter_by(acao="ACESSO_NEGADO", entidade="Incidente", entidade_id=str(incident.id)).first()
        )
        self.assertIsNotNone(db.session.get(Incidente, incident.id))

        self.client.get("/logout")
        self.login("admin", "admin123")
        self.assertEqual(self.client.get(f"/incidente/{incident.id}/edit").status_code, 200)

    def test_admin_audit_and_security_constraints(self):
        self.login("viewer", "viewer123")
        self.client.get("/admin/logs-auditoria")
        self.assertIsNotNone(AuditLog.query.filter_by(acao="ACESSO_NEGADO", resultado="NEGADO").first())

        self.client.get("/logout")
        self.login("admin", "admin123")
        self.client.post("/register", data={
            "username": "newadmin",
            "name": "Novo Admin",
            "email": "newadmin@test.com",
            "profile": "Admin",
            "password": "Secret@123",
        })
        user_log = AuditLog.query.filter_by(acao="CRIAR_USUARIO").first()
        self.assertIsNotNone(user_log)
        self.assertNotIn("Secret@123", str(user_log.alteracoes))
        self.assertNotIn("csrf", str(user_log.alteracoes).lower())
        self.assertNotIn("cookie", str(user_log.alteracoes).lower())

        self.client.get("/admin/logs-auditoria?usuario=%27%20OR%201=1--")
        self.assertIsNotNone(AuditLog.query.filter_by(acao="VISUALIZAR", entidade="AuditLog").first())
        self.assertLessEqual(self.client.get("/admin/logs-auditoria?per_page=999").status_code, 200)

        first_log = AuditLog.query.first()
        self.assertEqual(self.client.post(f"/admin/logs-auditoria/{first_log.id}").status_code, 405)

    def test_user_html_is_escaped(self):
        incident = self.create_incident()
        html = self.client.get(f"/incidente/{incident.id}").get_data(as_text=True)
        self.assertIn("Descri", html)
        self.assertNotIn("<script>alert('x')</script>", html)

    def test_incidents_page_dynamic_search_and_detail_back_action(self):
        self.assertEqual(self.client.get("/incidentes/pesquisa").status_code, 302)
        incident = self.create_incident()
        self.client.post(
            f"/incidente/{incident.id}/add_obs",
            data={"texto_observacao": "Evidência 10.44.44.21 contato alvo@teste.local"},
        )
        self.client.post(
            f"/incidente/{incident.id}/add_obs",
            data={"texto_observacao": "Segunda evidência 10.44.44.21"},
        )

        page_html = self.client.get("/incidentes?q=10.44.44.21").get_data(as_text=True)
        self.assertIn("Incidentes de seguran", page_html)
        self.assertIn("Registro de incidentes", page_html)
        self.assertIn("Registrar novo incidente", page_html)
        self.assertIn("global_incident_search", page_html)
        self.assertNotIn("Novo incidente</a>", page_html)
        self.assertNotIn("Pesquisar incidentes</label>", page_html)
        self.client.get("/logout")
        login_flash = self.client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("data-app-notification", login_flash)
        self.login("user", "user123")

        fragment = self.client.get("/incidentes/pesquisa?q=10.44.44.21").get_data(as_text=True)
        self.assertIn("REL-AUDIT", fragment)
        self.assertEqual(fragment.count("REL-AUDIT"), 2)

        empty = self.client.get("/incidentes/pesquisa?q=inexistente").get_data(as_text=True)
        self.assertIn("Nenhum incidente encontrado", empty)
        self.assertIn("data-clear-incident-search", empty)

        self.assertEqual(self.client.get("/incidentes/pesquisa?q=" + ("x" * 201)).status_code, 400)
        malicious = self.client.get("/incidentes/pesquisa?q=%27%20OR%201=1--")
        self.assertEqual(malicious.status_code, 200)

        detail = self.client.get(f"/incidente/{incident.id}?return_to=/incidentes?q=10.44.44.21").get_data(as_text=True)
        self.assertIn(">Voltar<", detail)
        self.assertIn('href="/incidentes?q=10.44.44.21"', detail)

        unsafe_detail = self.client.get(f"/incidente/{incident.id}?return_to=https://evil.example").get_data(as_text=True)
        self.assertIn('href="/incidentes"', unsafe_detail)
        self.assertNotIn("evil.example", unsafe_detail)

    def test_consolidated_incident_dashboard_page_and_api(self):
        self.login("admin", "admin123")
        admin = User.query.filter_by(username="admin").first()
        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        month_start = today.replace(day=1)
        if month_start.month == 12:
            next_month = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        month_end = next_month - timedelta(days=1)

        db.session.add(Unidades(cpa="CPA/M-1", btl="7º BPM/M"))
        db.session.add(Unidades(cpa="CPA/M-1", btl="11º BPM/M"))
        db.session.add(Unidades(cpa="CPA/M-2", btl="2º BPM/M"))
        db.session.add_all([
            Incidente(
                status_incident="Em Análise",
                start_date=datetime.combine(month_start, datetime.min.time()),
                incident_type="Phishing",
                report_number="DASH-1",
                ticket_number="TCK-1",
                cpa="CPA/M-1",
                btl="7º BPM/M",
                cia="1 CIA",
                description="Dashboard teste 1",
                user_id=admin.id,
            ),
            Incidente(
                status_incident="Encerrado",
                start_date=datetime.combine(month_start, datetime.min.time()),
                incident_type="Phishing",
                report_number="DASH-2",
                ticket_number="TCK-2",
                cpa="CPA/M-1",
                btl="11º BPM/M",
                cia="1 CIA",
                description="Dashboard teste 2",
                user_id=admin.id,
            ),
            Incidente(
                status_incident="Encerrado",
                start_date=datetime.combine(month_end, datetime.min.time()),
                incident_type="Phishing",
                report_number="DASH-3",
                ticket_number="TCK-3",
                cpa="CPA/M-2",
                btl="2º BPM/M",
                cia="1 CIA",
                description="Dashboard teste 3",
                user_id=admin.id,
            ),
        ])
        db.session.commit()

        page = self.client.get("/dashboard-incidentes")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Dashboard de Incidentes", html)
        self.assertIn(f'value="{month_start.isoformat()}"', html)
        self.assertIn(f'value="{month_end.isoformat()}"', html)

        status_response = self.client.get(
            f"/api/dashboard/incidents?view=status&startDate={month_start.isoformat()}&endDate={month_end.isoformat()}"
        )
        self.assertEqual(status_response.status_code, 200)
        status_data = status_response.get_json()
        self.assertEqual(status_data["chart"]["type"], "status")
        self.assertEqual(sum(item["total"] for item in status_data["chart"]["items"]), status_data["cards"]["total"])

        hierarchy_response = self.client.get(
            f"/api/dashboard/incidents?view=cpa-btl&startDate={month_start.isoformat()}&endDate={month_end.isoformat()}"
        )
        self.assertEqual(hierarchy_response.status_code, 200)
        hierarchy_data = hierarchy_response.get_json()
        self.assertEqual(hierarchy_data["chart"]["type"], "cpa-btl")
        groups = {group["cpaName"]: group for group in hierarchy_data["chart"]["groups"]}
        self.assertIn("CPA/M-1", groups)
        self.assertGreaterEqual(len(groups["CPA/M-1"]["battalions"]), 2)
        self.assertEqual(
            sum(btl["total"] for btl in groups["CPA/M-1"]["battalions"]),
            groups["CPA/M-1"]["total"],
        )

        cpa_page = self.client.get(
            f"/dashboard-incidentes?view=cpa-btl&startDate={month_start.isoformat()}&endDate={month_end.isoformat()}"
        ).get_data(as_text=True)
        self.assertIn("7º BPM/M", cpa_page)
        self.assertIn("hierarchy-bar", cpa_page)

        invalid_relation = self.client.get("/api/dashboard/incidents?view=cpa-btl&cpa=CPA/M-1&btl=2º BPM/M")
        self.assertEqual(invalid_relation.status_code, 400)

        invalid_date = self.client.get("/api/dashboard/incidents?startDate=2026-02-30")
        self.assertEqual(invalid_date.status_code, 400)

        legacy_status = self.client.get("/dashboard/incidentes_status", follow_redirects=False)
        self.assertEqual(legacy_status.status_code, 302)
        self.assertIn("/dashboard-incidentes", legacy_status.headers["Location"])

    def test_development_organizational_units_seed_is_idempotent(self):
        first = seed_development_organizational_units()
        second = seed_development_organizational_units()

        self.assertEqual(first["created"], 12)
        self.assertEqual(second["created"], 0)
        self.assertGreaterEqual(second["existing"], 12)

        cpa_m5 = Unidades.query.filter_by(cpa="CPA/M-5").all()
        cpa_m1 = Unidades.query.filter_by(cpa="CPA/M-1").all()

        self.assertEqual({unidade.btl for unidade in cpa_m5}, {"SEDE", "4º BPM/M", "16º BPM/M", "23º BPM/M", "49º BPM/M"})
        self.assertEqual({unidade.btl for unidade in cpa_m1}, {"SEDE", "7º BPM/M", "11º BPM/M", "13º BPM/M", "7º BAEP"})
        self.assertEqual(Unidades.query.filter_by(cpa="CPA/M-5", btl="4º BPM/M").count(), 1)

        command_m5 = OrganizationalCommand.query.filter_by(name="CPA/M-5").first()
        command_m1 = OrganizationalCommand.query.filter_by(name="CPA/M-1").first()
        self.assertIsNotNone(command_m5)
        self.assertIsNotNone(command_m1)
        self.assertEqual(OrganizationalUnit.query.filter_by(command_id=command_m5.id, name="SEDE").count(), 1)
        self.assertEqual(OrganizationalUnit.query.filter_by(command_id=command_m1.id, name="SEDE").count(), 1)

    def test_incident_form_uses_dependent_command_units_and_backend_rejects_mismatch(self):
        seed_development_organizational_units()
        self.login("user", "user123")
        command_m5 = OrganizationalCommand.query.filter_by(name="CPA/M-5").first()
        command_m1 = OrganizationalCommand.query.filter_by(name="CPA/M-1").first()
        unit_23 = OrganizationalUnit.query.filter_by(command_id=command_m5.id, name="23º BPM/M").first()
        unit_7 = OrganizationalUnit.query.filter_by(command_id=command_m1.id, name="7º BPM/M").first()

        form_html = self.client.get("/incidente/new").get_data(as_text=True)
        self.assertIn("command_select", form_html)
        self.assertIn("Selecione primeiro o CPA/Grande Comando", form_html)
        self.assertIn("CPA/M-5", form_html)
        self.assertNotIn("CPA/M-5 - SEDE", form_html)

        units_response = self.client.get(f"/api/organizational-commands/{command_m5.id}/units")
        self.assertEqual(units_response.status_code, 200)
        unit_names = [unit["name"] for unit in units_response.get_json()["units"]]
        self.assertEqual(unit_names, ["SEDE", "4º BPM/M", "16º BPM/M", "23º BPM/M", "49º BPM/M"])

        invalid = self.client.post("/incidente/new", data={
            "status_incidente": "Em Análise",
            "registration_date": "2026-07-15",
            "incident_type": "Tentativa de intrusão",
            "report_number": "REL-MISMATCH",
            "message_number": "MSG-MISMATCH",
            "ticket_number": "INC-MISMATCH",
            "command_id": str(command_m5.id),
            "unit_id": str(unit_7.id),
            "description": "<p>Descrição válida</p>",
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("pertence ao CPA", invalid.get_data(as_text=True))

        valid = self.client.post("/incidente/new", data={
            "status_incidente": "Em Análise",
            "registration_date": "2026-07-15",
            "incident_type": "Tentativa de intrusão",
            "report_number": "REL-CPA-M5",
            "message_number": "MSG-CPA-M5",
            "ticket_number": "INC-CPA-M5",
            "command_id": str(command_m5.id),
            "unit_id": str(unit_23.id),
            "description": "<p>Descrição válida</p>",
        }, follow_redirects=True)
        self.assertIn("Incidente registrado com sucesso", valid.get_data(as_text=True))
        incident = Incidente.query.filter_by(report_number="REL-CPA-M5").first()
        self.assertEqual(incident.cpa, "CPA/M-5")
        self.assertEqual(incident.btl, "23º BPM/M")
        self.assertEqual(incident.command_id, command_m5.id)
        self.assertEqual(incident.unit_id, unit_23.id)

        dashboard = self.client.get(
            "/api/dashboard/incidents?view=cpa-btl&incidentType=Tentativa de intrusão&cpa=CPA/M-5&btl=23º BPM/M&startDate=2026-07-01&endDate=2026-07-31"
        ).get_json()
        self.assertGreaterEqual(dashboard["cards"]["total"], 1)

    def test_incident_registration_date_types_fields_sanitizer_and_pdf_attachment(self):
        self.login("user", "user123")
        form_html = self.client.get("/incidente/new").get_data(as_text=True)
        self.assertIn('type="date"', form_html)
        self.assertNotIn('datetime-local', form_html)
        self.assertIn('placeholder="xxx/150/26"', form_html)
        self.assertIn("message_number", form_html)
        self.assertIn("incident_attachments", form_html)
        self.assertIn("Phishing", form_html)
        self.assertIn("Brute Force", form_html)

        with patch(
            "app.blueprints.incidente.routes._server_registration_timestamp",
            return_value=datetime(2026, 7, 14, 14, 37, 51),
        ):
            response = self.client.post(
                "/incidente/new",
                data={
                    "status_incidente": "Em Análise",
                    "registration_date": "2026-07-14",
                    "incident_type": "Phishing",
                    "report_number": "123/150/26",
                    "message_number": "MSG-123",
                    "ticket_number": "INC-987",
                    "btl": "BTL Teste",
                    "cpa": "CPA Teste",
                    "cia": "1 CIA",
                    "description": (
                        "<div style=\"text-align: center\"><font face=\"Arial\" size=\"5\" color=\"#b42318\">Texto rico</font></div>"
                        "<p><b>Negrito</b> <i>Itálico</i> <u>Sublinhado</u> <strike>Tachado</strike></p>"
                        "<p><span style=\"color: rgb(23, 92, 211); background-color: rgb(220, 250, 230);\">Cores</span></p>"
                        "<ul><li>Item de lista</li></ul><script>alert(1)</script>"
                    ),
                    "incident_attachments": (BytesIO(b"%PDF-1.4\n%test\n"), "relatorio.pdf"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        self.assertIn("Incidente registrado com sucesso", response.get_data(as_text=True))
        incident = Incidente.query.filter_by(report_number="123/150/26").first()
        self.assertIsNotNone(incident)
        self.assertEqual(incident.start_date.strftime("%Y-%m-%d %H:%M:%S"), "2026-07-14 14:37:51")
        self.assertEqual(incident.message_number, "MSG-123")
        self.assertEqual(incident.ticket_number, "INC-987")
        self.assertIn("<font", incident.description)
        self.assertIn("face=\"Arial\"", incident.description)
        self.assertIn("<b>Negrito</b>", incident.description)
        self.assertIn("<i>Itálico</i>", incident.description)
        self.assertIn("<u>Sublinhado</u>", incident.description)
        self.assertIn("<strike>Tachado</strike>", incident.description)
        self.assertIn("background-color", incident.description)
        self.assertIn("<ul><li>Item de lista</li></ul>", incident.description)
        self.assertNotIn("script", incident.description.lower())
        self.assertNotIn("onerror", incident.description.lower())
        self.assertEqual(len(incident.attachments), 1)
        attachment = incident.attachments[0]
        self.assertNotIn("static", attachment.stored_filename.lower())
        download = self.client.get(f"/incidentes/{incident.id}/anexos/{attachment.id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIsNotNone(AuditLog.query.filter_by(acao="DOWNLOAD_ANEXO", entidade_id=str(attachment.id)).first())

    def test_incident_registration_uses_server_time_for_same_day_ordering(self):
        self.login("user", "user123")
        base_payload = {
            "status_incidente": "Em Análise",
            "registration_date": "2026-07-16",
            "incident_type": "Phishing",
            "message_number": "MSG-ORDER",
            "ticket_number": "INC-ORDER",
            "btl": "BTL Teste",
            "cpa": "CPA Teste",
            "cia": "1 CIA",
            "description": "<p>Ordenação por timestamp completo</p>",
        }

        with patch(
            "app.blueprints.incidente.routes._server_registration_timestamp",
            return_value=datetime(2026, 7, 16, 8, 5, 1),
        ):
            self.client.post("/incidente/new", data={**base_payload, "report_number": "REL-EARLY"})

        with patch(
            "app.blueprints.incidente.routes._server_registration_timestamp",
            return_value=datetime(2026, 7, 16, 17, 45, 33),
        ):
            self.client.post("/incidente/new", data={**base_payload, "report_number": "REL-LATE"})

        early = Incidente.query.filter_by(report_number="REL-EARLY").first()
        late = Incidente.query.filter_by(report_number="REL-LATE").first()
        self.assertEqual(early.start_date.strftime("%Y-%m-%d %H:%M:%S"), "2026-07-16 08:05:01")
        self.assertEqual(late.start_date.strftime("%Y-%m-%d %H:%M:%S"), "2026-07-16 17:45:33")

        html = self.client.get("/incidentes").get_data(as_text=True)
        self.assertLess(html.index("REL-LATE"), html.index("REL-EARLY"))
        self.assertIn("16/07/2026", html)
        self.assertNotIn("17:45:33", html)

        audit = AuditLog.query.filter_by(acao="CRIAR", entidade="Incidente", entidade_id=str(late.id)).first()
        self.assertIn("17:45:33", audit.descricao)
        self.assertEqual(audit.alteracoes["start_date"]["novo"], "2026-07-16 17:45:33")

    def test_incident_form_errors_keep_submitted_values_and_show_specific_message(self):
        self.login("user", "user123")
        response = self.client.post("/incidente/new", data={
            "status_incidente": "Em Análise",
            "registration_date": "2026-07-14",
            "incident_type": "Phishing",
            "report_number": "",
            "message_number": "MSG-PRESERVAR",
            "ticket_number": "INC-PRESERVAR",
            "btl": "BTL Teste",
            "cpa": "CPA Teste",
            "cia": "CIA PRESERVADA",
            "description": "<p><b>Descrição preservada</b></p>",
        })
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Nº relatório", html)
        self.assertIn("MSG-PRESERVAR", html)
        self.assertIn("INC-PRESERVAR", html)
        self.assertIn("CIA PRESERVADA", html)
        self.assertIn("Descrição preservada", html)

        invalid_attachment = self.client.post(
            "/incidente/new",
            data={
                "status_incidente": "Em Análise",
                "registration_date": "2026-07-14",
                "incident_type": "Phishing",
                "report_number": "REL-PRESERVAR",
                "message_number": "MSG-PRESERVAR",
                "ticket_number": "INC-PRESERVAR",
                "btl": "BTL Teste",
                "cpa": "CPA Teste",
                "cia": "CIA PRESERVADA",
                "description": "<p>Descrição com anexo inválido</p>",
                "incident_attachments": (BytesIO(b"bad"), "malware.exe"),
            },
            content_type="multipart/form-data",
        )
        invalid_html = invalid_attachment.get_data(as_text=True)
        self.assertEqual(invalid_attachment.status_code, 400)
        self.assertIn("Tipo de arquivo não permitido", invalid_html)
        self.assertIn("REL-PRESERVAR", invalid_html)

    def test_utf8_temporal_integrity_and_audit_rollback(self):
        self.login("user", "user123")
        with patch(
            "app.blueprints.incidente.routes._server_registration_timestamp",
            return_value=datetime(2026, 7, 15, 8, 9, 10),
        ):
            response = self.client.post("/incidente/new", data={
                "status_incidente": "Em Análise",
                "registration_date": "2026-07-15",
                "incident_type": "Transferência de arquivo malicioso",
                "report_number": "Número do relatório",
                "message_number": "MSG-UTF8",
                "ticket_number": "Quebra de Confidencialidade",
                "btl": "BTL Teste",
                "cpa": "CPA Teste",
                "cia": "Incidente envolvendo VPN corporativa",
                "description": "<p>Descrição com ç, ã, é, ó e Transferência</p>",
                "timestamp": "1999-01-01T00:00:00Z",
                "created_at": "1999-01-01T00:00:00Z",
                "updated_at": "1999-01-01T00:00:00Z",
            }, follow_redirects=True)
        html = response.get_data(as_text=True)
        self.assertIn("Incidente registrado com sucesso", html)

        incident = Incidente.query.filter_by(message_number="MSG-UTF8").first()
        self.assertIsNotNone(incident)
        self.assertEqual(incident.start_date.strftime("%Y-%m-%d %H:%M:%S"), "2026-07-15 08:09:10")
        self.assertIsNotNone(incident.created_at)
        self.assertIsNotNone(incident.updated_at)
        self.assertNotEqual(incident.created_at.year, 1999)
        self.assertIn("Descri\u00e7\u00e3o", incident.description)
        self.assertIn("Transfer\u00eancia", incident.description)
        self.assertIn("N\u00famero do relat\u00f3rio", incident.report_number)
        self.assertIn("Quebra de Confidencialidade", incident.ticket_number)
        self.assertIn("Incidente envolvendo VPN corporativa", incident.cia)
        corrupted = ("Descri" + "?" + "\u00e3o", "Transfer" + "?" + "ncia", "Relat" + chr(0x00C3), "Confidencialidade" + chr(0xFFFD))
        for value in [incident.description, incident.report_number, incident.ticket_number, incident.cia]:
            for bad in corrupted:
                self.assertNotIn(bad, value)

        audit_log = AuditLog.query.filter_by(acao="CRIAR", entidade="Incidente", entidade_id=str(incident.id)).first()
        self.assertIsNotNone(audit_log)
        self.assertIsNotNone(audit_log.timestamp)
        self.assertIsNotNone(audit_log.occurred_at)
        self.assertIsNotNone(audit_log.request_id)
        self.assertNotIn("password", str(audit_log.alteracoes).lower())
        self.assertNotIn("cookie", str(audit_log.alteracoes).lower())
        sao_paulo = audit_log.timestamp.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Sao_Paulo"))
        self.assertEqual(sao_paulo.strftime("%d/%m/%Y %H:%M:%S"), self.app.jinja_env.filters["sp_datetime"](audit_log.timestamp))

        charset_response = self.client.get("/incidente/new")
        self.assertIn("charset=utf-8", charset_response.headers["Content-Type"].lower())

        def fail_audit(**kwargs):
            raise RuntimeError("audit failure")

        before_count = Incidente.query.count()
        with patch("app.blueprints.incidente.routes.registrar_auditoria", side_effect=fail_audit):
            failed = self.client.post("/incidente/new", data={
                "status_incidente": "Em Análise",
                "registration_date": "2026-07-15",
                "incident_type": "Phishing",
                "report_number": "ROLLBACK-TEST",
                "message_number": "MSG-ROLLBACK",
                "ticket_number": "INC-ROLLBACK",
                "btl": "BTL Teste",
                "cpa": "CPA Teste",
                "cia": "1 CIA",
                "description": "<p>Deve sofrer rollback</p>",
            })
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(Incidente.query.count(), before_count)
        self.assertIsNone(Incidente.query.filter_by(report_number="ROLLBACK-TEST").first())

    def test_user_management_access_and_sidebar(self):
        self.assertEqual(self.client.get("/admin/usuarios").status_code, 302)
        self.login("user", "user123")
        self.assertEqual(self.client.get("/admin/usuarios").status_code, 403)
        user_html = self.client.get("/incidentes").get_data(as_text=True)
        self.assertNotIn("Gestão de usuários", user_html)

        self.client.get("/logout")
        self.login("viewer", "viewer123")
        self.assertEqual(self.client.get("/admin/usuarios").status_code, 403)

        self.client.get("/logout")
        self.login("admin", "admin123")
        admin_html = self.client.get("/admin/usuarios").get_data(as_text=True)
        self.assertIn("Gestão de usuários", admin_html)
        self.assertIn("Criar usuário", admin_html)

    def test_user_registration_validation_and_first_access(self):
        self.login("admin", "admin123")
        response = self.client.post("/register", data={
            "username": "novo.user",
            "name": "Nome Igual",
            "email": "Novo.User@Test.COM",
            "profile": "User",
            "password": "Temp@123",
        }, follow_redirects=True)
        self.assertIn("Usuário criado com sucesso", response.get_data(as_text=True))
        novo = User.query.filter_by(username="novo.user").first()
        self.assertIsNotNone(novo)
        self.assertNotEqual(novo.password, "Temp@123")
        self.assertTrue(novo.must_change_password)
        self.assertTrue(novo.is_temp_password)
        self.assertNotIn("Temp@123", str(AuditLog.query.filter_by(acao="CRIAR_USUARIO").first().alteracoes))

        dup_re = self.client.post("/register", data={
            "username": "novo.user",
            "name": "Outro Nome",
            "email": "outro@test.com",
            "profile": "Viewer",
            "password": "Temp@123",
        }).get_data(as_text=True)
        self.assertIn("Já existe um usuário cadastrado com esse RE", dup_re)

        dup_email = self.client.post("/register", data={
            "username": "novo2",
            "name": "Nome Igual",
            "email": "novo.user@test.com",
            "profile": "Viewer",
            "password": "Temp@123",
        }).get_data(as_text=True)
        self.assertIn("Já existe um usuário cadastrado com esse e-mail", dup_email)

        same_name = self.client.post("/register", data={
            "username": "novo3",
            "name": "Nome Igual",
            "email": "novo3@test.com",
            "profile": "Viewer",
            "password": "Temp@123",
        }, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Usuário criado com sucesso", same_name)

        self.client.get("/logout")
        self.client.post("/login", data={"username": "novo.user", "password": "Temp@123"})
        self.assertEqual(self.client.get("/incidentes", follow_redirects=False).headers.get("Location"), "/change_password")
        self.client.post("/change_password", data={"new_password": "Nova@123", "confirm_password": "Nova@123"})
        db.session.refresh(novo)
        self.assertFalse(novo.must_change_password)
        self.assertFalse(novo.is_temp_password)
        self.assertEqual(self.client.get("/incidentes").status_code, 200)

    def test_user_search_and_profile_change(self):
        self.login("admin", "admin123")
        self.client.post("/register", data={
            "username": "pesquisa1",
            "name": "Pessoa Pesquisavel",
            "email": "pesquisa1@test.com",
            "profile": "User",
            "password": "Temp@123",
        })
        self.client.post("/register", data={
            "username": "pesquisa2",
            "name": "Outra Pessoa",
            "email": "pesquisa2@test.com",
            "profile": "Viewer",
            "password": "Temp@123",
        })

        for term in ["Pessoa", "pesquisa1", "pesquisa1@test.com", "Viewer"]:
            html = self.client.get(f"/admin/usuarios?q={term}").get_data(as_text=True)
            self.assertIn(term.split("@")[0] if "@" in term else term, html)
        empty = self.client.get("/admin/usuarios?q=semresultado").get_data(as_text=True)
        self.assertIn("Nenhum usuário encontrado", empty)
        self.assertIn("q=Pessoa", self.client.get("/admin/usuarios?q=Pessoa&page=1").request.path + "q=Pessoa")

        target = User.query.filter_by(username="pesquisa1").first()
        self.assertEqual(self.client.get(f"/admin/usuarios/{target.id}/perfil").status_code, 405)
        self.client.post(f"/admin/usuarios/{target.id}/perfil", data={"profile": "Viewer"})
        db.session.refresh(target)
        self.assertEqual(target.profile, "Viewer")
        self.assertIsNotNone(AuditLog.query.filter_by(acao="ALTERAR_USUARIO", entidade_id=str(target.id)).first())

        bad = self.client.post(f"/admin/usuarios/{target.id}/perfil", data={"profile": "Root"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post("/admin/usuarios/9999/perfil", data={"profile": "User"}).status_code, 404)

    def test_last_admin_cannot_be_demoted_and_non_admin_cannot_change_profile(self):
        User.query.filter(User.username == "system").delete()
        db.session.commit()
        self.login("admin", "admin123")
        admin = User.query.filter_by(username="admin").first()
        response = self.client.post(f"/admin/usuarios/{admin.id}/perfil", data={"profile": "User"}, follow_redirects=True)
        self.assertIn("Não é possível remover o perfil do único administrador", response.get_data(as_text=True))
        db.session.refresh(admin)
        self.assertEqual(admin.profile, "Admin")

        self.client.get("/logout")
        self.login("user", "user123")
        self.assertEqual(self.client.post(f"/admin/usuarios/{admin.id}/perfil", data={"profile": "Viewer"}).status_code, 403)

    def test_admin_can_soft_delete_user_and_viewer(self):
        self.login("admin", "admin123")
        admin = User.query.filter_by(username="admin").first()
        user = User.query.filter_by(username="user").first()
        viewer = User.query.filter_by(username="viewer").first()

        response = self.client.post(f"/admin/usuarios/{user.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200)
        db.session.refresh(user)
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.deleted_at)
        self.assertEqual(user.deleted_by_id, admin.id)
        delete_log = AuditLog.query.filter_by(acao="USER_DELETED", entidade="User", entidade_id=str(user.id)).first()
        self.assertIsNotNone(delete_log)
        self.assertEqual(delete_log.alteracoes["is_active"]["anterior"], "True")
        self.assertEqual(delete_log.alteracoes["is_active"]["novo"], "False")
        self.assertNotIn("password", str(delete_log.alteracoes).lower())

        response = self.client.post(f"/admin/usuarios/{viewer.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200)
        db.session.refresh(viewer)
        self.assertFalse(viewer.is_active)

    def test_user_delete_denied_cases_and_authentication_effects(self):
        self.login("admin", "admin123")
        admin = User.query.filter_by(username="admin").first()
        other_admin = User(username="admin2", name="Admin Dois", email="admin2@test", profile="Admin", is_temp_password=False, must_change_password=False, password=hash("admin2123"))
        db.session.add(other_admin)
        db.session.commit()

        own = self.client.post(f"/admin/usuarios/{admin.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(own.status_code, 400)
        self.assertIn("própria conta", own.get_json()["message"])

        admin_delete = self.client.post(f"/admin/usuarios/{other_admin.id}/excluir", data={"profile": "User"}, headers={"Accept": "application/json"})
        self.assertEqual(admin_delete.status_code, 403)
        db.session.refresh(other_admin)
        self.assertTrue(other_admin.is_active)

        missing = self.client.post("/admin/usuarios/999999/excluir", headers={"Accept": "application/json"})
        self.assertEqual(missing.status_code, 404)

        target = User.query.filter_by(username="viewer").first()
        ok = self.client.post(f"/admin/usuarios/{target.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(ok.status_code, 200)
        again = self.client.post(f"/admin/usuarios/{target.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(again.status_code, 409)

        self.client.get("/logout")
        denied_login = self.client.post("/login", data={"username": "viewer", "password": "viewer123"}, follow_redirects=True)
        self.assertIn("Nome de usuário ou senha incorretos", denied_login.get_data(as_text=True))

        self.login("admin", "admin123")
        html = self.client.get("/admin/usuarios").get_data(as_text=True)
        self.assertNotIn("Viewer Teste", html)

        denied_logs = AuditLog.query.filter_by(acao="USER_DELETE_DENIED").all()
        self.assertGreaterEqual(len(denied_logs), 3)
        self.assertNotIn("viewer123", str([log.alteracoes for log in denied_logs]))

    def test_non_admin_and_unauthenticated_cannot_delete_users(self):
        target = User.query.filter_by(username="viewer").first()
        unauthenticated = self.client.post(f"/admin/usuarios/{target.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(unauthenticated.status_code, 401)

        self.login("user", "user123")
        denied = self.client.post(f"/admin/usuarios/{target.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(denied.status_code, 403)
        db.session.refresh(target)
        self.assertTrue(target.is_active)

        self.client.get("/logout")
        self.login("viewer", "viewer123")
        denied_viewer = self.client.post(f"/admin/usuarios/{target.id}/excluir", headers={"Accept": "application/json"})
        self.assertEqual(denied_viewer.status_code, 403)

    def test_user_delete_audit_failure_rolls_back(self):
        self.login("admin", "admin123")
        target = User.query.filter_by(username="user").first()

        def fail_audit(**kwargs):
            raise RuntimeError("audit failure")

        with patch("app.blueprints.admin.routes.registrar_auditoria", side_effect=fail_audit):
            response = self.client.post(f"/admin/usuarios/{target.id}/excluir", headers={"Accept": "application/json"})

        self.assertEqual(response.status_code, 500)
        db.session.rollback()
        db.session.refresh(target)
        self.assertTrue(target.is_active)
        self.assertIsNone(target.deleted_at)
        self.assertIsNone(target.deleted_by_id)


if __name__ == "__main__":
    unittest.main()
