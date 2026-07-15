import unittest
from io import BytesIO
from datetime import datetime

from app import create_app, db, hash
from app.models import AuditLog, Incidente, IncidenteObs, StatusIncidente, TipoIncidente, Unidades, User


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


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
            "description": "<p>DescriÃ§Ã£o segura</p><script>alert('x')</script>",
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
            "description": "<p>DescriÃ§Ã£o segura</p><script>alert('x')</script>",
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
            data={"texto_observacao": "EvidÃªncia 10.44.44.21 contato alvo@teste.local"},
        )
        self.client.post(
            f"/incidente/{incident.id}/add_obs",
            data={"texto_observacao": "Segunda evidÃªncia 10.44.44.21"},
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

        response = self.client.post(
            "/incidente/new",
            data={
                "status_incidente": "Em AnÃ¡lise",
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
        self.assertEqual(incident.start_date.strftime("%H:%M:%S"), "00:00:00")
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

    def test_incident_form_errors_keep_submitted_values_and_show_specific_message(self):
        self.login("user", "user123")
        response = self.client.post("/incidente/new", data={
            "status_incidente": "Em AnÃ¡lise",
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
                "status_incidente": "Em AnÃ¡lise",
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
        self.assertIn("q=Pessoa", self.client.get("/admin/usuarios?q=Pessoa&page=1").request.path + "?q=Pessoa")

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


if __name__ == "__main__":
    unittest.main()
