"""
app.py — Sentinel Cloud Trading Web Platform
Flask application: auth, dashboard, terminal, admin panel, REST API
"""

import os
import time
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify, abort)
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

from db import (
    get_user_by_email, get_user_by_id, create_user, update_last_login,
    get_user_application, get_user_trades, get_user_trade_stats,
    get_all_applications, approve_application, reject_application,
    get_all_users, get_live_state, send_command, pg_execute, pg_query
)
from auth import (
    hash_password, verify_password, generate_user_id,
    login_required, verified_required, admin_required,
    validate_email, validate_password
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 7  # 7 days

# Rate limiter — blocks brute-force login attacks
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# ══════════════════════════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("login.html")
        user = get_user_by_email(email)
        if not user or not verify_password(password, user["password_hash"]):
            flash("Invalid email or password.", "error")
            return render_template("login.html")
        if user["account_status"] == "banned":
            flash("Your account has been suspended. Contact support.", "error")
            return render_template("login.html")
        session.permanent = True
        session["user_id"]        = user["user_id"]
        session["email"]          = user["email"]
        session["full_name"]      = user["full_name"] or user["email"]
        session["role"]           = user["role"]
        session["account_status"] = user["account_status"]
        update_last_login(user["user_id"])
        flash(f"Welcome back, {session['full_name'].split()[0]}.", "success")
        if user["role"] == "admin":
            return redirect(url_for("admin_panel"))
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email     = request.form.get("email", "").strip().lower()
        password  = request.form.get("password", "")
        confirm   = request.form.get("confirm_password", "")
        errors = []
        if not full_name: errors.append("Full name is required.")
        if not validate_email(email): errors.append("Enter a valid email address.")
        pw_ok, pw_msg = validate_password(password)
        if not pw_ok: errors.append(pw_msg)
        if password != confirm: errors.append("Passwords do not match.")
        if get_user_by_email(email): errors.append("An account with this email already exists.")
        if errors:
            for e in errors: flash(e, "error")
            return render_template("register.html", full_name=full_name, email=email)
        user_id = generate_user_id(email)
        create_user(user_id, email, hash_password(password), full_name)
        flash("Account created! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

# ══════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    user        = get_user_by_id(session["user_id"])
    application = get_user_application(session["user_id"])
    stats       = get_user_trade_stats(session["user_id"])
    # Keep session account_status in sync with DB
    if user and user["account_status"] != session.get("account_status"):
        session["account_status"] = user["account_status"]
    return render_template("dashboard.html", user=user, application=application, stats=stats)

# ══════════════════════════════════════════════════════════════
# APPLICATION (KYC) FORM
# ══════════════════════════════════════════════════════════════

@app.route("/apply", methods=["GET", "POST"])
@login_required
def apply():
    existing = get_user_application(session["user_id"])
    if existing and existing["status"] in ("pending", "under_review", "verified"):
        flash("You already have an active application.", "info")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in
             ["full_name","phone_number","address","income_source",
              "reason_for_use","mt5_login","mt5_password","mt5_server"]}
        terms = request.form.get("terms_accepted") == "on"
        if not all([f["full_name"], f["reason_for_use"], f["mt5_login"], f["mt5_server"]]):
            flash("Please fill in all required fields.", "error")
            return render_template("apply.html", **f)
        if not terms:
            flash("You must accept the Terms and Conditions.", "error")
            return render_template("apply.html", **f)
        pg_execute(
            """INSERT INTO applications
               (user_id,full_name,phone_number,address,income_source,
                reason_for_use,mt5_login,mt5_password,mt5_server,
                terms_accepted,terms_accepted_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())""",
            (session["user_id"],f["full_name"],f["phone_number"],f["address"],
             f["income_source"],f["reason_for_use"],f["mt5_login"],
             f["mt5_password"],f["mt5_server"],terms)
        )
        flash("Application submitted! We will review it within 24–48 hours.", "success")
        return redirect(url_for("dashboard"))
    return render_template("apply.html")

# ══════════════════════════════════════════════════════════════
# TRADING TERMINAL
# ══════════════════════════════════════════════════════════════

@app.route("/terminal")
@verified_required
def terminal():
    return render_template("terminal.html",
                           user_id=session["user_id"],
                           full_name=session["full_name"])

@app.route("/history")
@verified_required
def history():
    trades = get_user_trades(session["user_id"], limit=100)
    stats  = get_user_trade_stats(session["user_id"])
    return render_template("history.html", trades=trades, stats=stats)

# ══════════════════════════════════════════════════════════════
# REST API — TERMINAL
# ══════════════════════════════════════════════════════════════

@app.route("/api/terminal/state")
@verified_required
@limiter.limit("120 per minute")
def api_terminal_state():
    return jsonify(get_live_state(session["user_id"]))

@app.route("/api/terminal/command", methods=["POST"])
@verified_required
@limiter.limit("30 per minute")
def api_terminal_command():
    data    = request.get_json(silent=True) or {}
    command = data.get("command", "").upper().strip()
    VALID   = {"TOGGLE_TRADE","CLOSE_ALL","EMERGENCY_STOP","TOGGLE_CB","BYPASS","REBOOT"}
    if command not in VALID:
        return jsonify({"ok": False, "error": "Invalid command"}), 400
    return jsonify({"ok": send_command(session["user_id"], command)})

# ══════════════════════════════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════════════════════════════

@app.route("/admin")
@admin_required
def admin_panel():
    status_filter = request.args.get("status")
    applications  = get_all_applications(status_filter) or []
    users         = get_all_users() or []
    counts = {s: len([a for a in applications if a["status"]==s])
              for s in ("pending","under_review","verified","rejected")}
    counts["total_users"] = len(users)
    return render_template("admin.html", applications=applications,
                           users=users, counts=counts, status_filter=status_filter)

@app.route("/admin/applications/<application_id>/approve", methods=["POST"])
@admin_required
def admin_approve(application_id):
    if approve_application(application_id, session["user_id"]):
        flash("Application approved. User now has terminal access.", "success")
    else:
        flash("Failed to approve application.", "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/applications/<application_id>/reject", methods=["POST"])
@admin_required
def admin_reject(application_id):
    reason = request.form.get("rejection_reason", "Application did not meet requirements.")
    if reject_application(application_id, session["user_id"], reason):
        flash("Application rejected.", "info")
    else:
        flash("Failed to reject application.", "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/users/<user_id>/suspend", methods=["POST"])
@admin_required
def admin_suspend_user(user_id):
    pg_execute("UPDATE users SET account_status='suspended' WHERE user_id=%s", (user_id,))
    pg_execute("INSERT INTO audit_log (actor_id,action,target_type,target_id) VALUES (%s,'USER_SUSPENDED','user',%s)",
               (session["user_id"], user_id))
    flash(f"User {user_id} suspended.", "warning")
    return redirect(url_for("admin_panel"))

@app.route("/api/admin/live_states")
@admin_required
def api_admin_live_states():
    users = get_all_users() or []
    return jsonify({u["user_id"]: get_live_state(u["user_id"])
                    for u in users if u["account_status"] == "verified"})

# ══════════════════════════════════════════════════════════════
# CONTEXT + ERRORS
# ══════════════════════════════════════════════════════════════

@app.context_processor
def inject_globals():
    return {"app_name": "SENTINEL", "session": session}

@app.errorhandler(429)
def rate_limited(e):
    return render_template("error.html", code=429,
                           message="Too many requests. Please wait a moment."), 429
@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="Access denied."), 403
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404
@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Internal server error."), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
