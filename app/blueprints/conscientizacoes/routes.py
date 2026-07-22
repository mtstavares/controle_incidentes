import os
import re

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.blueprints.conscientizacoes import conscientizacoes_bp
from app.models import ConscientizacaoCampanha
from app.services.audit_service import AuditAction, registrar_auditoria
from app.services.awareness_image_service import (
    AwarenessImageValidationError,
    delete_awareness_image,
    resolve_awareness_image_path,
    save_awareness_image,
)
from app.services.timezone_service import local_now, parse_iso_date


WRITE_PROFILES = {"Admin", "User"}
VIEW_PROFILES = {"Admin", "User", "Viewer"}
TITLE_MAX_LENGTH = 150


def _current_profile():
    return getattr(current_user, "profile", None)


def _require_view_permission():
    if _current_profile() not in VIEW_PROFILES:
        registrar_auditoria(
            acao=AuditAction.ACESSO_NEGADO,
            modulo="Conscientizações",
            entidade="ConscientizacaoCampanha",
            descricao="Tentativa de acesso sem permissão às conscientizações.",
            resultado="NEGADO",
        )
        abort(403)


def _require_write_permission():
    if _current_profile() not in WRITE_PROFILES:
        registrar_auditoria(
            acao=AuditAction.ACESSO_NEGADO,
            modulo="Conscientizações",
            entidade="ConscientizacaoCampanha",
            descricao="Tentativa de alteração de campanha sem permissão.",
            resultado="NEGADO",
        )
        abort(403)


def _normalize_title(value):
    title = re.sub(r"\s+", " ", (value or "").strip())
    if not title:
        raise ValueError("Informe o título da campanha.")
    if len(title) > TITLE_MAX_LENGTH:
        raise ValueError(f"O título deve ter no máximo {TITLE_MAX_LENGTH} caracteres.")
    return title


def _parse_publication_date(value):
    try:
        parsed = parse_iso_date(value)
    except (TypeError, ValueError):
        raise ValueError("Informe uma data de publicação válida.") from None
    if not parsed:
        raise ValueError("Informe a data de publicação.")
    return parsed


def _audit_campaign(action, campaign, description, changes=None, result="SUCESSO", commit=True):
    return registrar_auditoria(
        acao=action,
        modulo="Conscientizações",
        entidade="ConscientizacaoCampanha",
        entidade_id=campaign.id if campaign and campaign.id else None,
        descricao=description,
        alteracoes=changes,
        resultado=result,
        commit=commit,
    )


@conscientizacoes_bp.route("/conscientizacoes", methods=["GET"])
@login_required
def listar_conscientizacoes():
    _require_view_permission()
    campanhas = (
        ConscientizacaoCampanha.query
        .order_by(
            ConscientizacaoCampanha.data_publicacao.desc(),
            ConscientizacaoCampanha.id.desc(),
        )
        .all()
    )
    return render_template(
        "conscientizacoes/listar.html",
        title="Conscientizações",
        campanhas=campanhas,
        can_manage=_current_profile() in WRITE_PROFILES,
        today=local_now().date().isoformat(),
    )


@conscientizacoes_bp.route("/conscientizacoes", methods=["POST"])
@login_required
def criar_conscientizacao():
    _require_write_permission()
    saved_image = None
    try:
        titulo = _normalize_title(request.form.get("titulo"))
        data_publicacao = _parse_publication_date(request.form.get("data_publicacao"))
        saved_image = save_awareness_image(request.files.get("imagem"))

        campanha = ConscientizacaoCampanha(
            titulo=titulo,
            imagem_arquivo=saved_image["stored_filename"],
            imagem_mime_type=saved_image["mime_type"],
            imagem_tamanho=saved_image["size"],
            data_publicacao=data_publicacao,
            created_by_id=current_user.id,
        )
        db.session.add(campanha)
        db.session.flush()
        _audit_campaign(
            AuditAction.CRIAR,
            campanha,
            f"Campanha de conscientização criada: {campanha.titulo}.",
            changes={
                "titulo": {"anterior": None, "novo": campanha.titulo},
                "data_publicacao": {"anterior": None, "novo": campanha.data_publicacao.isoformat()},
                "created_by_id": {"anterior": None, "novo": campanha.created_by_id},
            },
            commit=False,
        )
        db.session.commit()
        flash("Campanha cadastrada com sucesso.", "success")
    except (ValueError, AwarenessImageValidationError) as exc:
        db.session.rollback()
        if saved_image:
            delete_awareness_image(saved_image["stored_filename"])
        flash(str(exc), "danger")
    except SQLAlchemyError:
        db.session.rollback()
        if saved_image:
            delete_awareness_image(saved_image["stored_filename"])
        current_app.logger.exception("Falha ao cadastrar campanha de conscientização.")
        flash("Não foi possível cadastrar a campanha.", "danger")
    return redirect(url_for("conscientizacoes.listar_conscientizacoes"))


@conscientizacoes_bp.route("/conscientizacoes/<int:campaign_id>/editar", methods=["POST"])
@login_required
def editar_conscientizacao(campaign_id):
    _require_write_permission()
    campanha = ConscientizacaoCampanha.query.get_or_404(campaign_id)
    old_image = campanha.imagem_arquivo
    saved_image = None
    try:
        titulo = _normalize_title(request.form.get("titulo"))
        data_publicacao = _parse_publication_date(request.form.get("data_publicacao"))
        changes = {}

        if campanha.titulo != titulo:
            changes["titulo"] = {"anterior": campanha.titulo, "novo": titulo}
            campanha.titulo = titulo
        if campanha.data_publicacao != data_publicacao:
            changes["data_publicacao"] = {
                "anterior": campanha.data_publicacao.isoformat(),
                "novo": data_publicacao.isoformat(),
            }
            campanha.data_publicacao = data_publicacao

        image_file = request.files.get("imagem")
        if image_file and image_file.filename:
            saved_image = save_awareness_image(image_file)
            changes["imagem_arquivo"] = {"anterior": old_image, "novo": saved_image["stored_filename"]}
            changes["imagem_mime_type"] = {"anterior": campanha.imagem_mime_type, "novo": saved_image["mime_type"]}
            changes["imagem_tamanho"] = {"anterior": campanha.imagem_tamanho, "novo": saved_image["size"]}
            campanha.imagem_arquivo = saved_image["stored_filename"]
            campanha.imagem_mime_type = saved_image["mime_type"]
            campanha.imagem_tamanho = saved_image["size"]

        if changes:
            _audit_campaign(
                AuditAction.EDITAR,
                campanha,
                f"Campanha de conscientização editada: {campanha.titulo}.",
                changes=changes,
                commit=False,
            )
        db.session.commit()
        if saved_image:
            delete_awareness_image(old_image)
        flash("Campanha atualizada com sucesso.", "success")
    except (ValueError, AwarenessImageValidationError) as exc:
        db.session.rollback()
        if saved_image:
            delete_awareness_image(saved_image["stored_filename"])
        flash(str(exc), "danger")
    except SQLAlchemyError:
        db.session.rollback()
        if saved_image:
            delete_awareness_image(saved_image["stored_filename"])
        current_app.logger.exception("Falha ao editar campanha de conscientização.")
        flash("Não foi possível atualizar a campanha.", "danger")
    return redirect(url_for("conscientizacoes.listar_conscientizacoes"))


@conscientizacoes_bp.route("/conscientizacoes/<int:campaign_id>/excluir", methods=["POST"])
@login_required
def excluir_conscientizacao(campaign_id):
    _require_write_permission()
    campanha = ConscientizacaoCampanha.query.get_or_404(campaign_id)
    stored_image = campanha.imagem_arquivo
    try:
        _audit_campaign(
            AuditAction.EXCLUIR,
            campanha,
            f"Campanha de conscientização excluída: {campanha.titulo}.",
            changes={
                "titulo": {"anterior": campanha.titulo, "novo": None},
                "imagem_arquivo": {"anterior": campanha.imagem_arquivo, "novo": None},
                "data_publicacao": {"anterior": campanha.data_publicacao.isoformat(), "novo": None},
            },
            commit=False,
        )
        db.session.delete(campanha)
        db.session.flush()
        delete_awareness_image(stored_image, raise_on_error=True)
        db.session.commit()
        flash("Campanha excluída com sucesso.", "success")
    except AwarenessImageValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Falha ao excluir campanha de conscientização.")
        flash("Não foi possível excluir a campanha.", "danger")
    return redirect(url_for("conscientizacoes.listar_conscientizacoes"))


@conscientizacoes_bp.route("/conscientizacoes/<int:campaign_id>/imagem", methods=["GET"])
@login_required
def imagem_conscientizacao(campaign_id):
    _require_view_permission()
    campanha = ConscientizacaoCampanha.query.get_or_404(campaign_id)
    try:
        path = resolve_awareness_image_path(campanha.imagem_arquivo)
    except AwarenessImageValidationError:
        abort(404)
    response = send_file(
        path,
        mimetype=campanha.imagem_mime_type,
        as_attachment=False,
        download_name=f"campanha-{campanha.id}{os.path.splitext(campanha.imagem_arquivo)[1]}",
        max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Disposition"] = "inline"
    return response
