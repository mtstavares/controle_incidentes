from flask import current_app, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import func

from app import db
from app.blueprints.dashboard import dashboard_bp
from app.models import CredencialComprometida
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.timezone_service import local_now


MONTHS_PT_BR = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}

MIN_DASHBOARD_YEAR = 2000
MAX_DASHBOARD_YEAR = 2100
ALLOWED_DASHBOARD_PARAMS = {"year", "month"}


def _available_credential_years():
    year_expr = func.strftime("%Y", CredencialComprometida.data_coleta)
    rows = (
        db.session.query(year_expr.label("year"))
        .filter(CredencialComprometida.data_coleta.isnot(None))
        .filter(year_expr.isnot(None))
        .group_by(year_expr)
        .order_by(year_expr.desc())
        .all()
    )
    years = []
    for row in rows:
        try:
            year = int(row.year)
        except (TypeError, ValueError):
            continue
        if MIN_DASHBOARD_YEAR <= year <= MAX_DASHBOARD_YEAR:
            years.append(year)
    return years


def _validate_dashboard_filters(args):
    unexpected = set(args.keys()) - ALLOWED_DASHBOARD_PARAMS
    if unexpected:
        raise ValueError("Parâmetros de filtro inválidos.")

    years = _available_credential_years()
    default_year = years[0] if years else local_now().year

    raw_year = args.get("year", str(default_year)).strip()
    if not raw_year.isdigit():
        raise ValueError("Ano informado é inválido.")
    year = int(raw_year)
    if year < MIN_DASHBOARD_YEAR or year > MAX_DASHBOARD_YEAR:
        raise ValueError("Ano informado está fora do intervalo permitido.")

    raw_month = args.get("month", "all").strip().lower()
    if raw_month in {"", "all", "todos"}:
        month = None
    elif raw_month.isdigit() and 1 <= int(raw_month) <= 12:
        month = int(raw_month)
    else:
        raise ValueError("Mês informado é inválido.")

    return year, month, years


def _count_credentials_by_month(year, month=None):
    month_expr = func.strftime("%m", CredencialComprometida.data_coleta)
    year_expr = func.strftime("%Y", CredencialComprometida.data_coleta)
    query = (
        db.session.query(month_expr.label("month"), func.count(CredencialComprometida.id).label("total"))
        .filter(CredencialComprometida.data_coleta.isnot(None))
        .filter(year_expr == str(year))
    )
    if month:
        query = query.filter(month_expr == f"{month:02d}")

    rows = query.group_by(month_expr).order_by(month_expr.asc()).all()
    totals = {int(row.month): int(row.total) for row in rows if row.month}
    months = [month] if month else list(range(1, 13))

    return [
        {
            "month": item,
            "monthName": MONTHS_PT_BR[item],
            "year": year,
            "total": totals.get(item, 0),
        }
        for item in months
    ]


def _invalid_collection_date_count():
    year_expr = func.strftime("%Y", CredencialComprometida.data_coleta)
    return (
        db.session.query(func.count(CredencialComprometida.id))
        .filter(
            db.or_(
                CredencialComprometida.data_coleta.is_(None),
                year_expr.is_(None),
            )
        )
        .scalar()
        or 0
    )


@dashboard_bp.route("/dashboard-credenciais", methods=["GET"])
@login_required
def dashboard_credenciais():
    years = _available_credential_years()
    selected_year = years[0] if years else local_now().year
    registrar_auditoria(
        acao=AuditAction.VISUALIZAR,
        modulo="Dashboard de credenciais",
        entidade="CredencialComprometida",
        descricao="Acessou o dashboard de credenciais comprometidas.",
        alteracoes={"data_coleta": {"anterior": None, "novo": f"ano={selected_year}; mes=todos"}},
    )
    return render_template(
        "dashboard/credenciais.html",
        title="Dashboard de credenciais comprometidas",
        years=years,
        selected_year=selected_year,
        months=MONTHS_PT_BR,
    )


@dashboard_bp.route("/api/dashboard/credenciais", methods=["GET"])
@login_required
def api_dashboard_credenciais():
    try:
        year, month, years = _validate_dashboard_filters(request.args)
        items = _count_credentials_by_month(year, month)
        invalid_dates = _invalid_collection_date_count()
        if invalid_dates:
            current_app.logger.warning(
                "Dashboard de credenciais ignorou %s registro(s) sem data de coleta válida.",
                invalid_dates,
            )
        registrar_auditoria(
            acao=AuditAction.VISUALIZAR,
            modulo="Dashboard de credenciais",
            entidade="CredencialComprometida",
            descricao="Consultou agregação mensal de credenciais comprometidas.",
            alteracoes={
                "data_coleta": {
                    "anterior": None,
                    "novo": f"ano={year}; mes={month or 'todos'}; datas_invalidas={invalid_dates}",
                }
            },
        )
        return jsonify({
            "data": items,
            "error": None,
            "meta": {
                "year": year,
                "month": month or "all",
                "years": years,
                "invalidCollectionDates": invalid_dates,
            },
        })
    except ValueError as exc:
        registrar_auditoria(
            acao=AuditAction.VISUALIZAR,
            modulo="Dashboard de credenciais",
            entidade="CredencialComprometida",
            descricao="Consulta recusada por filtros inválidos no dashboard de credenciais.",
            alteracoes={"data_coleta": {"anterior": None, "novo": "filtros inválidos"}},
            resultado="NEGADO",
        )
        return jsonify({"data": [], "error": {"message": str(exc)}, "meta": {}}), 400
    except Exception:
        current_app.logger.exception("Falha ao consultar dashboard de credenciais.")
        registrar_auditoria(
            acao=AuditAction.VISUALIZAR,
            modulo="Dashboard de credenciais",
            entidade="CredencialComprometida",
            descricao="Falha na consulta do dashboard de credenciais.",
            alteracoes=None,
            resultado="ERRO",
        )
        return jsonify({
            "data": [],
            "error": {"message": "Não foi possível carregar o dashboard de credenciais."},
            "meta": {},
        }), 500
