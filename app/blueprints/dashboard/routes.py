from flask import current_app, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import func

from app import db
from app.blueprints.dashboard import dashboard_bp
from app.models import CredencialColetaMensal, CredencialComprometida
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
ALLOWED_DASHBOARD_PARAMS = {"year", "month", "start_month", "end_month"}


def _available_credential_years():
    year_expr = func.strftime("%Y", CredencialComprometida.data_coleta)
    credential_rows = (
        db.session.query(year_expr.label("year"))
        .filter(CredencialComprometida.data_coleta.isnot(None))
        .filter(year_expr.isnot(None))
        .group_by(year_expr)
        .all()
    )
    monthly_rows = (
        db.session.query(CredencialColetaMensal.ano_referencia.label("year"))
        .filter(CredencialColetaMensal.deleted_at.is_(None))
        .group_by(CredencialColetaMensal.ano_referencia)
        .all()
    )
    years = set()
    for row in [*credential_rows, *monthly_rows]:
        try:
            year = int(row.year)
        except (TypeError, ValueError):
            continue
        if MIN_DASHBOARD_YEAR <= year <= MAX_DASHBOARD_YEAR:
            years.add(year)
    return sorted(years, reverse=True)


def _parse_month_filter(value, field_label):
    raw_value = (value or "").strip().lower()
    if raw_value in {"", "all", "todos"}:
        return None
    if raw_value.isdigit() and 1 <= int(raw_value) <= 12:
        return int(raw_value)
    raise ValueError(f"{field_label} informado é inválido.")


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

    # Mantém compatibilidade com o filtro antigo `month` e habilita o novo intervalo.
    month = _parse_month_filter(args.get("month", "all"), "Mês")
    start_month = _parse_month_filter(args.get("start_month", ""), "Mês inicial")
    end_month = _parse_month_filter(args.get("end_month", ""), "Mês final")

    if month is not None:
        start_month = month
        end_month = month
    if start_month is not None and end_month is not None and start_month > end_month:
        raise ValueError("Mês inicial não pode ser posterior ao mês final.")

    return year, start_month, end_month, years


def _count_positive_credentials_by_month(year, start_month=None, end_month=None):
    month_expr = func.strftime("%m", CredencialComprometida.data_coleta)
    year_expr = func.strftime("%Y", CredencialComprometida.data_coleta)
    query = (
        db.session.query(month_expr.label("month"), func.count(CredencialComprometida.id).label("total"))
        .filter(CredencialComprometida.data_coleta.isnot(None))
        .filter(year_expr == str(year))
        .filter(
            db.or_(
                CredencialComprometida.permitiu_acesso.is_(True),
                CredencialComprometida.acesso_ad.is_(True),
                CredencialComprometida.acesso_ms.is_(True),
            )
        )
    )
    if start_month is not None:
        query = query.filter(month_expr >= f"{start_month:02d}")
    if end_month is not None:
        query = query.filter(month_expr <= f"{end_month:02d}")

    rows = query.group_by(month_expr).order_by(month_expr.asc()).all()
    return {int(row.month): int(row.total) for row in rows if row.month}


def _monthly_collected_totals(year, start_month=None, end_month=None):
    query = CredencialColetaMensal.query.filter_by(ano_referencia=year).filter(
        CredencialColetaMensal.deleted_at.is_(None)
    )
    if start_month is not None:
        query = query.filter(CredencialColetaMensal.mes_referencia >= start_month)
    if end_month is not None:
        query = query.filter(CredencialColetaMensal.mes_referencia <= end_month)
    return {row.mes_referencia: int(row.quantidade_localizada) for row in query.all()}


def _months_for_dashboard(collected_totals, positive_totals, start_month=None, end_month=None):
    if start_month is not None or end_month is not None:
        first_month = start_month or 1
        last_month = end_month or 12
        return list(range(first_month, last_month + 1))
    return sorted(set(collected_totals.keys()) | set(positive_totals.keys())) or list(range(1, 13))


def _count_credentials_by_month(year, start_month=None, end_month=None):
    positive_totals = _count_positive_credentials_by_month(year, start_month, end_month)
    collected_totals = _monthly_collected_totals(year, start_month, end_month)
    months = _months_for_dashboard(collected_totals, positive_totals, start_month, end_month)

    items = []
    for item in months:
        has_monthly_total = item in collected_totals
        collected = int(collected_totals.get(item, 0))
        positive = int(positive_totals.get(item, 0))
        without_access = max(collected - positive, 0)
        inconsistent = has_monthly_total and positive > collected
        if not has_monthly_total and positive > 0:
            current_app.logger.warning(
                "Dashboard de credenciais encontrou positivos sem total consolidado cadastrado: ano=%s mes=%s positivos=%s.",
                year,
                item,
                positive,
            )
        if inconsistent:
            current_app.logger.error(
                "Inconsistência no dashboard de credenciais: ano=%s mes=%s total_consolidado=%s positivos=%s.",
                year,
                item,
                collected,
                positive,
            )
        access_rate = round((positive / collected) * 100, 2) if collected > 0 else 0
        items.append(
            {
                "month": item,
                "monthName": MONTHS_PT_BR[item],
                "year": year,
                "total": positive,
                "credenciais_localizadas": collected,
                "credenciais_com_acesso": positive,
                "credenciais_sem_acesso": without_access,
                "taxa_acesso_positivo": access_rate,
                "contabilizacao_cadastrada": has_monthly_total,
                "inconsistente": inconsistent,
            }
        )
    return items


def _dashboard_summary(items):
    located = sum(item["credenciais_localizadas"] for item in items)
    positive = sum(item["credenciais_com_acesso"] for item in items)
    without_access = sum(item["credenciais_sem_acesso"] for item in items)
    access_rate = round((positive / located) * 100, 2) if located > 0 else 0
    return {
        "credenciais_localizadas": located,
        "credenciais_com_acesso": positive,
        "credenciais_sem_acesso": without_access,
        "taxa_acesso_positivo": access_rate,
        "competencias_sem_contabilizacao": [
            {"year": item["year"], "month": item["month"]}
            for item in items
            if not item["contabilizacao_cadastrada"] and item["credenciais_com_acesso"] > 0
        ],
        "competencias_inconsistentes": [
            {"year": item["year"], "month": item["month"]}
            for item in items
            if item["inconsistente"]
        ],
    }


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
        year, start_month, end_month, years = _validate_dashboard_filters(request.args)
        items = _count_credentials_by_month(year, start_month, end_month)
        summary = _dashboard_summary(items)
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
                    "novo": (
                        f"ano={year}; mes_inicial={start_month or 'auto'}; "
                        f"mes_final={end_month or 'auto'}; datas_invalidas={invalid_dates}"
                    ),
                }
            },
        )
        return jsonify(
            {
                "data": items,
                "summary": summary,
                "error": None,
                "meta": {
                    "year": year,
                    "month": start_month if start_month == end_month and start_month is not None else "all",
                    "startMonth": start_month,
                    "endMonth": end_month,
                    "years": years,
                    "invalidCollectionDates": invalid_dates,
                },
            }
        )
    except ValueError as exc:
        registrar_auditoria(
            acao=AuditAction.VISUALIZAR,
            modulo="Dashboard de credenciais",
            entidade="CredencialComprometida",
            descricao="Consulta recusada por filtros inválidos no dashboard de credenciais.",
            alteracoes={"data_coleta": {"anterior": None, "novo": "filtros inválidos"}},
            resultado="NEGADO",
        )
        return jsonify({"data": [], "summary": {}, "error": {"message": str(exc)}, "meta": {}}), 400
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
        return (
            jsonify(
                {
                    "data": [],
                    "summary": {},
                    "error": {"message": "Não foi possível carregar o dashboard de credenciais."},
                    "meta": {},
                }
            ),
            500,
        )
