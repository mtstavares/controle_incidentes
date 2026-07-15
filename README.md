# 🛡️ DivCiber — Gestão de Incidentes de Segurança

Aplicação web desenvolvida para registrar, consultar, acompanhar e analisar incidentes de segurança tratados pela DivCiber.

## 🎯 Objetivo

O projeto foi criado para centralizar informações que antes ficavam espalhadas em planilhas, documentos e controles separados.

Com o sistema, usuários autorizados conseguem acompanhar o histórico dos incidentes, responsáveis, observações, anexos, status e indicadores em um único ambiente.

## 🚀 Principais funcionalidades

* Autenticação de usuários;
* Perfis de acesso: `Admin`, `User` e `Viewer`;
* Cadastro, edição, consulta e exclusão de incidentes;
* Pesquisa dinâmica em incidentes e observações;
* Filtros por status e ordenação;
* Inclusão de observações;
* Upload de PDF, imagens, Word e Excel;
* Gestão de usuários;
* Dashboards de incidentes;
* Logs de auditoria;
* Interface responsiva para computador, tablet e celular.

## 👥 Perfis de acesso

### Admin

Possui acesso completo ao sistema, incluindo gestão de usuários e logs de auditoria.

### User

Pode registrar, consultar e editar incidentes conforme as permissões definidas.

### Viewer

Possui acesso somente para consulta de incidentes e dashboards.

## 🧰 Tecnologias

### Backend

* Python;
* Flask;
* Flask-Login;
* SQLAlchemy;
* Flask-Migrate;
* Werkzeug;
* Pandas.

### Frontend

* HTML5;
* CSS3;
* JavaScript;
* Jinja2;
* Plotly;
* React, quando necessário para componentes mais interativos.

### Banco de dados

* SQLite para desenvolvimento local;
* PostgreSQL recomendado para produção.

## 📁 Estrutura resumida

```text
DivCiber/
├── app/
│   ├── blueprints/
│   ├── templates/
│   ├── static/
│   ├── services/
│   ├── models.py
│   └── __init__.py
├── migrations/
├── tests/
├── instance/
├── run.py
├── requirements.txt
└── README.md
```

## 🔐 Segurança

O projeto adota controles como:

* autenticação e autorização no backend;
* hashing seguro de senhas;
* proteção CSRF;
* validação de entradas;
* sanitização de conteúdo HTML;
* armazenamento privado de anexos;
* auditoria de ações;
* tratamento seguro de erros;
* restrição de acesso por perfil.

## 📌 Status

Projeto em desenvolvimento contínuo, com foco em segurança, rastreabilidade, usabilidade e centralização dos incidentes da DivCiber.

## ⚠️ Uso interno
