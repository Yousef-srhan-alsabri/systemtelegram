from flask import Blueprint, jsonify
from flask_login import current_user, login_required
from app.models import TelegramAccount
from app.background import get_qr_state

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/accounts/status")
@login_required
def statuses():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id).all()
    return jsonify({"accounts": [{"id": a.id, "status": a.status, "reason": a.status_reason} for a in accounts]})


@bp.get("/qr/<token>")
@login_required
def qr_status(token):
    return jsonify(get_qr_state(token))
