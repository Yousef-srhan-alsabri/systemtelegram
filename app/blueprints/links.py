from flask import Blueprint, flash, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ManagedLink
from app.services.links import extract_links

bp = Blueprint("links", __name__, url_prefix="/links")


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        items = extract_links(request.form.get("text", ""))
        added = 0
        for item in items:
            existing = ManagedLink.query.filter_by(url_hash=item.url_hash).first()
            if not existing:
                db.session.add(ManagedLink(owner_id=current_user.id, url=item.url, url_hash=item.url_hash, link_type=item.link_type))
                added += 1
        db.session.commit()
        flash(f"تمت إضافة {added} روابط جديدة", "success")
    links = ManagedLink.query.filter_by(owner_id=current_user.id).order_by(ManagedLink.id.desc()).all()
    grouped = {kind: [x for x in links if x.link_type == kind] for kind in ("telegram", "whatsapp", "other")}
    return render_template("links.html", grouped=grouped)
