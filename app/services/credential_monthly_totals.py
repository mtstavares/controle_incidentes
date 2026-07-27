from app import db
from app.models import CredencialColetaMensal


MIN_REFERENCE_YEAR = 2000
MAX_REFERENCE_YEAR = 2100

HISTORICAL_MONTHLY_TOTALS = {
    2024: {
        2: 508,
        3: 854,
        4: 350,
        5: 453,
        6: 1900,
        7: 911,
        8: 586,
        9: 561,
        10: 485,
        11: 1965,
        12: 1251,
    },
    2025: {
        1: 2307,
        2: 1577,
        3: 2947,
        4: 227,
        5: 2528,
        6: 415,
        7: 557,
        8: 950,
        9: 473,
        10: 693,
        11: 826,
        12: 485,
    },
    2026: {
        1: 717,
        2: 309,
        3: 579,
        4: 1863,
        5: 2188,
        6: 1580,
    },
}


class MonthlyCredentialTotalValidationError(ValueError):
    pass


def _strict_int(value, field_name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise MonthlyCredentialTotalValidationError(f"{field_name} deve ser um número inteiro.")
    return value


def validate_reference_year(value):
    year = _strict_int(value, "Ano")
    if year < MIN_REFERENCE_YEAR or year > MAX_REFERENCE_YEAR:
        raise MonthlyCredentialTotalValidationError("Ano está fora do intervalo permitido.")
    return year


def validate_reference_month(value):
    month = _strict_int(value, "Mês")
    if month < 1 or month > 12:
        raise MonthlyCredentialTotalValidationError("Mês deve estar entre 1 e 12.")
    return month


def validate_monthly_total(value):
    total = _strict_int(value, "Quantidade")
    if total < 0:
        raise MonthlyCredentialTotalValidationError("Quantidade deve ser maior ou igual a zero.")
    return total


def upsert_monthly_total(year, month, total, *, commit=True):
    year = validate_reference_year(year)
    month = validate_reference_month(month)
    total = validate_monthly_total(total)

    record = CredencialColetaMensal.query.filter_by(ano_referencia=year, mes_referencia=month).one_or_none()
    if record is None:
        record = CredencialColetaMensal(
            ano_referencia=year,
            mes_referencia=month,
            quantidade_localizada=total,
        )
        db.session.add(record)
    else:
        record.quantidade_localizada = total

    if commit:
        db.session.commit()
    return record


def seed_historical_monthly_totals(*, commit=True):
    stats = {"created": 0, "updated": 0, "unchanged": 0}
    for year, months in HISTORICAL_MONTHLY_TOTALS.items():
        for month, total in months.items():
            existing = CredencialColetaMensal.query.filter_by(
                ano_referencia=year,
                mes_referencia=month,
            ).one_or_none()
            if existing is None:
                stats["created"] += 1
            elif existing.quantidade_localizada != total:
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
            upsert_monthly_total(year, month, total, commit=False)

    if commit:
        db.session.commit()
    return stats
