
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.services.audit import log_action
from app.services.settings import SETTING_SPECS, all_settings, set_setting

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        for key in SETTING_SPECS:
            spec = SETTING_SPECS[key]
            if spec["type"] == "bool":
                raw = "true" if request.form.get(key) in {"1", "true", "on", "yes"} else "false"
            else:
                if key not in request.form:
                    continue
                raw = request.form.get(key, "").strip()
            if spec["type"] == "int":
                try:
                    val = int(raw)
                except ValueError:
                    flash(f"القيمة غير صحيحة: {spec['label']}", "danger")
                    return redirect(url_for("settings.index"))
                if val < 0:
                    flash(f"القيمة لا يمكن أن تكون سالبة: {spec['label']}", "danger")
                    return redirect(url_for("settings.index"))
                raw = str(val)
            set_setting(current_user.id, key, raw, current_user.id)
        log_action("settings.updated", "settings", details="تم تحديث الإعدادات التشغيلية")
        db.session.commit()
        flash("تم حفظ الإعدادات", "success")
        return redirect(url_for("settings.index"))

    rows = all_settings(current_user.id)
    groups = {
        "telegram": [r for r in rows if r["group"] == "telegram"],
        "search": [r for r in rows if r["group"] == "search"],
        "join": [r for r in rows if r["group"] == "join"],
    }
    return render_template("settings.html", groups=groups)
