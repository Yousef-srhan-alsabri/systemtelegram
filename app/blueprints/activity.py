
from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.models import AuditLog

bp = Blueprint("activity", __name__, url_prefix="/activity")


@bp.get("")
@login_required
def index():
    logs = AuditLog.query.filter_by(owner_id=current_user.id).order_by(AuditLog.id.desc()).limit(200).all()
    return render_template("activity.html", logs=logs)
