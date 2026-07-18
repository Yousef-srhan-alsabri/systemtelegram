from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.models import TelegramAccount, TelegramGroup

bp = Blueprint("groups", __name__, url_prefix="/groups")


@bp.get("/<int:account_id>")
@login_required
def index(account_id):
    account = TelegramAccount.query.filter_by(id=account_id, owner_id=current_user.id).first_or_404()
    q = request.args.get("q", "").strip()
    query = TelegramGroup.query.filter_by(account_id=account.id)
    if q:
        query = query.filter(TelegramGroup.title.ilike(f"%{q}%"))
    groups = query.order_by(TelegramGroup.title).all()
    return render_template("groups.html", account=account, groups=groups, q=q)
