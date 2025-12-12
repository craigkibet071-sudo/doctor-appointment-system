# app_fixed.py — cleaned and fixed version
from flask import Flask, render_template, request, redirect, url_for, session, abort, flash, jsonify
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import re
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta


# ------------ Configuration & app init ------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "replace_this_in_prod")
app.config["UPLOAD_FOLDER"] = "static/uploads"
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4 MB max upload

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------ Email settings (set in env before use) ------------
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "craigkibet072@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

def send_email(to, subject, message):
    if not to:
        return
    try:
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = to
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        logger.exception("Email sending failed: %s", e)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ------------ Database connection (safe) ------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client["healthcare_db"]
    logger.info("Connected to MongoDB")
except Exception as e:
    logger.exception("Could not connect to MongoDB at %s. Error: %s", MONGO_URI, e)
    class BrokenDB:
        def __getattr__(self, name):
            raise RuntimeError("Database not available - check MongoDB connection")
    db = BrokenDB()

# ------------ Auth decorators ------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def role_required(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                return "Access denied", 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ----------------- BASIC PAGES -----------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Email required", "danger")
            return redirect(url_for("register"))
        if db.users.find_one({"email": email}):
            flash("Email already exists!", "danger")
            return redirect(url_for("register"))
        user = {
            "name": request.form.get("name", "").strip(),
            "email": email,
            "password": generate_password_hash(request.form.get("password", "")),
            "role": "patient",
            "photo": "/static/images/patient_default.jpeg",
            "phone": request.form.get("phone", "")
        }
        db.users.insert_one(user)
        flash("Registered successfully. Please login.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.users.find_one({"email": email})
        if user and check_password_hash(user.get("password", ""), password):
            session["user_id"] = str(user["_id"]) if isinstance(user.get("_id"), ObjectId) else str(user.get("_id"))
            session["role"] = user["role"]
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            if user["role"] == "doctor":
                return redirect(url_for("doctor_dashboard"))
            return redirect(url_for("patient_dashboard"))
        flash("Invalid email or password", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# ----------------- PATIENT -----------------
@app.route("/patient/profile", methods=["GET", "POST"])
@login_required
@role_required("patient")
def patient_profile():
    try:
        user = db.users.find_one({"_id": ObjectId(session["user_id"])})
    except Exception:
        flash("Invalid session or DB error", "danger")
        return redirect(url_for("logout"))
    if not user:
        flash("User not found", "danger")
        return redirect(url_for("logout"))
    if request.method == "POST":
        name = request.form.get("name", user.get("name"))
        phone = request.form.get("phone", user.get("phone", ""))
        update = {"name": name, "phone": phone}
        file = request.files.get("photo")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("File type not allowed", "danger")
                return redirect(url_for("patient_profile"))
            filename = secure_filename(f"{session['user_id']}_{int(datetime.utcnow().timestamp())}_{file.filename}")
            dest = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(dest)
            update["photo"] = f"/{app.config['UPLOAD_FOLDER']}/{filename}"
        db.users.update_one({"_id": ObjectId(session["user_id"])}, {"$set": update})
        flash("Profile updated", "success")
        user = db.users.find_one({"_id": ObjectId(session["user_id"])})
    return render_template("patient_profile.html", user=user)

@app.route("/doctors")
@login_required
@role_required("patient")
def doctors_list():
    doctors = list(db.users.find({"role": "doctor"}))
    return render_template("doctors_list.html", doctors=doctors)

@app.route("/book", methods=["GET", "POST"])
@login_required
@role_required("patient")
def book():
    doctors = list(db.users.find({"role": "doctor"}))
    if request.method == "POST":
        # allow using doctor id if provided
        doctor_id = request.form.get("doctor_id", "").strip()
        typed_name = request.form.get("doctor_name", "").strip()
        date = request.form.get("date", "").strip()
        time = request.form.get("time", "").strip()
        if not date or not time:
            flash("Please choose date and time", "warning")
            return redirect(url_for("book"))
        doctor_obj = None
        if doctor_id:
            try:
                doctor_obj = db.users.find_one({"_id": ObjectId(doctor_id), "role": "doctor"})
            except Exception:
                doctor_obj = None
        if not doctor_obj and typed_name:
            pattern = re.compile(re.escape(typed_name), re.IGNORECASE)
            doctor_obj = db.users.find_one({"role": "doctor", "name": {"$regex": pattern}})
        if not doctor_obj:
            flash("Selected doctor not found. Please choose from list.", "danger")
            return redirect(url_for("book"))
        patient = db.users.find_one({"_id": ObjectId(session["user_id"])})
        if not patient:
            flash("Patient record missing", "danger")
            return redirect(url_for("logout"))
        appt = {
            "patient_id": str(session["user_id"]),
            "patient_name": patient.get("name", ""),
            "patient_email": patient.get("email", ""),
            "patient_phone": patient.get("phone", ""),
            "patient_photo": patient.get("photo", "/static/images/patient_default.jpeg"),
            "doctor_id": str(doctor_obj["_id"]),
            "doctor_name": doctor_obj.get("name", ""),
            "doctor_email": doctor_obj.get("email", ""),
            "doctor_photo": doctor_obj.get("photo", "/static/images/doctor_default.jpg"),
            "date": date,
            "time": time,
            "status": "Pending",
            "created_at": datetime.utcnow()
        }
        db.appointments.insert_one(appt)
        try:
            send_email(appt["patient_email"], "Appointment Requested",
                       f"Your appointment with {appt['doctor_name']} on {date} at {time} is pending approval.")
        except Exception:
            pass
        return render_template("appointment_success.html", doctor_name=appt["doctor_name"], date=appt["date"], time=appt["time"], patient_name=appt["patient_name"]) 
    return render_template("book_appointment.html", doctors=doctors)

@app.route("/book/<doctor_id>", methods=["GET", "POST"])
@login_required
@role_required("patient")
def book_appointment(doctor_id):
    try:
        doctor_obj = db.users.find_one({"_id": ObjectId(doctor_id), "role": "doctor"})
    except Exception:
        doctor_obj = None
    if not doctor_obj:
        flash("Doctor not found", "danger")
        return redirect(url_for("book"))
    patient = db.users.find_one({"_id": ObjectId(session["user_id"])})
    if not patient:
        flash("Patient record not found", "danger")
        return redirect(url_for("logout"))
    if request.method == "POST":
        date = request.form.get("date", "").strip()
        time = request.form.get("time", "").strip()
        if not date or not time:
            flash("Please choose date and time", "warning")
            return redirect(url_for("book_appointment", doctor_id=doctor_id))
        appt = {
            "patient_id": str(session["user_id"]),
            "patient_name": patient.get("name", ""),
            "patient_email": patient.get("email", ""),
            "patient_phone": patient.get("phone", ""),
            "patient_photo": patient.get("photo", "/static/images/patient_default.jpeg"),
            "doctor_id": str(doctor_obj["_id"]),
            "doctor_name": doctor_obj.get("name", ""),
            "doctor_email": doctor_obj.get("email", ""),
            "doctor_photo": doctor_obj.get("photo", "/static/images/doctor_default.jpg"),
            "date": date,
            "time": time,
            "status": "Pending",
            "created_at": datetime.utcnow()
        }
        db.appointments.insert_one(appt)
        flash("Appointment requested", "success")
        return redirect(url_for("patient_dashboard"))
    return render_template("book_appointment.html", doctor=doctor_obj)

# ----------------- PATIENT DASHBOARD -----------------
@app.route("/patient/dashboard")
@login_required
@role_required("patient")
def patient_dashboard():
    patient_id = session.get("user_id")
    patient = db.users.find_one({"_id": ObjectId(patient_id)})
    
    # Fetch upcoming appointments for this patient
    appointments = list(db.appointments.find({"patient_id": str(patient_id)}).sort("date", 1))
    
    # Fetch all doctors to show in Book Appointment section
    doctors = list(db.users.find({"role": "doctor"}))
    
    return render_template(
        "patient_dashboard.html",
        patient=patient,
        appointments=appointments,
        doctors=doctors
    )

@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
@role_required("patient")
def edit_profile():
    try:
        patient = db.users.find_one({"_id": ObjectId(session["user_id"])})
    except Exception:
        flash("Invalid session or DB error", "danger")
        return redirect(url_for("logout"))
    if not patient:
        flash("Patient record not found.", "danger")
        return redirect(url_for("logout"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        update_data = {}
        if name: update_data["name"] = name
        if email: update_data["email"] = email
        if phone: update_data["phone"] = phone
        if password: update_data["password"] = generate_password_hash(password)
        if "photo" in request.files:
            photo = request.files["photo"]
            if photo and photo.filename:
                if not allowed_file(photo.filename):
                    flash("File type not allowed", "danger")
                    return redirect(url_for("edit_profile"))
                filename = secure_filename(f"{session['user_id']}_{int(datetime.utcnow().timestamp())}_{photo.filename}")
                upload_folder = os.path.join(app.root_path, "static", "uploads")
                os.makedirs(upload_folder, exist_ok=True)
                filepath = os.path.join(upload_folder, filename)
                photo.save(filepath)
                update_data["photo"] = f"/static/uploads/{filename}"
        if update_data:
            db.users.update_one({"_id": ObjectId(session["user_id"])}, {"$set": update_data})
            flash("Profile updated successfully!", "success")
        else:
            flash("No changes made.", "info")
        return redirect(url_for("patient_dashboard"))
    return render_template("edit_profile.html", patient=patient)

@app.route("/patient/appointments")
@login_required
@role_required("patient")
def view_appointments():
    appointments = list(db.appointments.find({"patient_id": str(session["user_id"]) }).sort([("date", 1), ("time", 1)]))
    return render_template("patient_appointments.html", appointments=appointments)

# ----------------- ADMIN -----------------
@app.route("/admin/create_user", methods=["GET", "POST"])
@login_required
@role_required("admin")
def create_user():
    if request.method == "POST":
        data = {"name": request.form.get("name", "").strip(), "email": request.form.get("email", "").strip().lower(), "password": generate_password_hash(request.form.get("password", "")), "role": request.form.get("role", "patient")}
        if data["role"] == "doctor":
            data["specialty"] = request.form.get("specialty", "")
            data["department"] = request.form.get("department", "")
            data["phone"] = request.form.get("phone", "")
            file = request.files.get("photo")
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"doc_{int(datetime.utcnow().timestamp())}_{file.filename}")
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                data["photo"] = f"/{app.config['UPLOAD_FOLDER']}/{filename}"
            else:
                data["photo"] = "/static/images/doctor_default.jpg"
        db.users.insert_one(data)
        flash("User created", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("create_user.html")

@app.route("/admin/edit/<user_id>", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_user(user_id):
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        abort(404)
    if request.method == "POST":
        update = {"name": request.form.get("name", user.get("name")), "role": request.form.get("role", user.get("role"))}
        if update["role"] == "doctor":
            update["specialty"] = request.form.get("specialty", user.get("specialty", ""))
            update["department"] = request.form.get("department", user.get("department", ""))
            update["phone"] = request.form.get("phone", user.get("phone", ""))
            file = request.files.get("photo")
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"doc_{int(datetime.utcnow().timestamp())}_{file.filename}")
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                update["photo"] = f"/{app.config['UPLOAD_FOLDER']}/{filename}"
        db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})
        flash("User updated", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("edit_user.html", user=user)

@app.route("/admin/delete/<user_id>")
@login_required
@role_required("admin")
def delete_user(user_id):
    db.users.delete_one({"_id": ObjectId(user_id)})
    flash("User deleted", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/dashboard")
@login_required
@role_required("admin")
def admin_dashboard():
    users = list(db.users.find())
    appointments = list(db.appointments.find().sort("created_at", -1))
    return render_template("admin_dashboard.html", users=users, appointments=appointments)

@app.route("/doctor/dashboard")
@login_required
@role_required("doctor")
def doctor_dashboard():
    doctor_id = session.get("user_id")

    # Fetch doctor record
    doctor = db.users.find_one({"_id": ObjectId(doctor_id), "role": "doctor"})
    if not doctor:
        flash("Doctor account not found.", "danger")
        return redirect(url_for("logout"))

    # Fetch appointments (sorted)
    appointments = list(
        db.appointments.find({"doctor_id": str(doctor_id)})
        .sort([("date", 1), ("time", 1)])
    )

    # Fetch availability
    availability = list(
        db.availability.find({"doctor_id": str(doctor_id)})
        .sort([("date", 1), ("start_time", 1)])
    )

    return render_template(
        "doctor_dashboard.html",
        doctor=doctor,
        appointments=appointments,
        availability=availability
    )

@app.route("/doctor/edit_profile", methods=["GET", "POST"])
@login_required
@role_required("doctor")
def doctor_edit_profile():
    doctor_id = session["user_id"]
    doctor = db.users.find_one({"_id": ObjectId(doctor_id)})
    if not doctor:
        flash("Doctor not found", "danger")
        return redirect(url_for("logout"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        specialty = request.form.get("specialty", "").strip()
        password = request.form.get("password", "").strip()
        update_data = {}
        if name: update_data["name"] = name
        if email: update_data["email"] = email
        if phone: update_data["phone"] = phone
        if specialty: update_data["specialty"] = specialty
        if password: update_data["password"] = generate_password_hash(password)
        if "photo" in request.files:
            photo = request.files["photo"]
            if photo and photo.filename:
                if not allowed_file(photo.filename):
                    flash("File type not allowed", "danger")
                    return redirect(url_for("doctor_edit_profile"))
                filename = secure_filename(f"{doctor_id}_{int(datetime.utcnow().timestamp())}_{photo.filename}")
                upload_path = os.path.join(app.root_path, "static", "uploads")
                os.makedirs(upload_path, exist_ok=True)
                photo.save(os.path.join(upload_path, filename))
                update_data["photo"] = f"/static/uploads/{filename}"
        if update_data:
            db.users.update_one({"_id": ObjectId(doctor_id)}, {"$set": update_data})
            flash("Profile updated successfully!", "success")
        else:
            flash("No changes made.", "info")
        return redirect(url_for("doctor_dashboard"))
    return render_template("doctor_edit_profile.html", doctor=doctor)

@app.route("/doctor/calendar")
@login_required
@role_required("doctor")
def doctor_calendar():
    doctor_id = session["user_id"]
    doctor = db.users.find_one({"_id": ObjectId(doctor_id)})
    appointments = list(db.appointments.find({"doctor_id": doctor_id}).sort([("date", 1), ("time", 1)]))
    availability = list(db.availability.find({"doctor_id": doctor_id}).sort("date", 1))
    return render_template("doctor_calendar.html", doctor=doctor, appointments=appointments, availability=availability)

@app.route("/doctor/availability/add", methods=["POST"])
@login_required
@role_required("doctor")
def doctor_add_availability():
    doctor_id = str(session["user_id"])
    date = request.form.get("date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    notes = request.form.get("notes", "").strip()

    if not date or not start_time or not end_time:
        flash("All fields are required.", "danger")
        return redirect(url_for("doctor_dashboard"))

    # Validate datetime
    try:
        st = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
        et = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M")
        if et <= st:
            flash("End time must be after start time.", "danger")
            return redirect(url_for("doctor_dashboard"))
    except ValueError:
        flash("Invalid date/time format.", "danger")
        return redirect(url_for("doctor_dashboard"))

    # Check overlapping slots
    conflict = db.availability.find_one({
        "doctor_id": doctor_id,
        "date": date,
        "$or": [
            {"start_time": {"$lt": end_time, "$gte": start_time}},
            {"end_time": {"$lte": end_time, "$gt": start_time}}
        ]
    })
    if conflict:
        flash("This slot overlaps with an existing availability.", "danger")
        return redirect(url_for("doctor_dashboard"))

    # Insert into MongoDB
    db.availability.insert_one({
        "doctor_id": doctor_id,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "notes": notes,
        "created_at": datetime.utcnow()
    })

    flash("Availability added successfully.", "success")
    return redirect(url_for("doctor_dashboard"))

# ----------------- DOCTOR AVAILABILITY JSON -----------------
@app.route("/doctor/availability/json", methods=["GET"])
@login_required
def doctor_availability_json():
    doctor_id = request.args.get("doctor_id")
    doctor_name = request.args.get("doctor_name")

    doctor = None
    if doctor_id:
        try:
            doctor = db.users.find_one({"_id": ObjectId(doctor_id), "role": "doctor"})
        except Exception:
            doctor = None
    elif doctor_name:
        # case-insensitive search by name
        doctor = db.users.find_one({"name": {"$regex": f"^{re.escape(doctor_name)}$", "$options": "i"}, "role": "doctor"})
    elif session.get("role") == "doctor":
        doctor = db.users.find_one({"_id": ObjectId(session.get("user_id")), "role": "doctor"})

    if not doctor:
        return jsonify([])

    slots = list(db.availability.find({"doctor_id": str(doctor["_id"])}))
    events = []
    for s in slots:
        events.append({
            "id": str(s["_id"]),
            "title": "Available",
            "start": f"{s['date']}T{s['start_time']}",
            "end": f"{s['date']}T{s['end_time']}",
            "color": "#28a745",
            "extendedProps": {
                "slot_id": str(s["_id"]),
                "date": s.get("date"),
                "start_time": s.get("start_time"),
                "end_time": s.get("end_time"),
                "notes": s.get("notes", ""),
                "doctor_id": str(s.get("doctor_id"))
            }
        })
    return jsonify(events)


# ----------------- PATIENT VIEW DOCTOR AVAILABILITY -----------------
@app.route("/patient/doctor/<doctor_id>/availability")
@login_required
@role_required("patient")
def patient_view_doctor_availability(doctor_id):
    doctor = db.users.find_one({"_id": ObjectId(doctor_id), "role": "doctor"})
    if not doctor:
        flash("Doctor not found.", "danger")
        return redirect(url_for("patient_dashboard"))

    # Fetch available slots
    availability = list(db.availability.find({"doctor_id": str(doctor_id)}))
    return render_template("patient_doctor_availability.html", doctor=doctor, availability=availability)
# ----------------- PATIENT BOOK APPOINTMENT -----------------
@app.route("/patient/book_appointment/<doctor_id>", methods=["GET", "POST"])
@login_required
@role_required("patient")
def patient_book_appointment(doctor_id):
    doctor = db.users.find_one({"_id": ObjectId(doctor_id), "role": "doctor"})
    if not doctor:
        flash("Doctor not found.", "danger")
        return redirect(url_for("patient_dashboard"))

    # Get all available slots for this doctor
    availability = list(db.availability.find({"doctor_id": str(doctor["_id"])}))

    if request.method == "POST":
        slot_id = request.form.get("slot_id")
        patient_id = session.get("user_id")
        message = request.form.get("message", "").strip()

        if not slot_id:
            flash("Please select a slot.", "danger")
            return redirect(url_for("patient_book_appointment", doctor_id=doctor_id))

        slot = db.availability.find_one({"_id": ObjectId(slot_id)})
        if not slot:
            flash("Invalid slot selected.", "danger")
            return redirect(url_for("patient_book_appointment", doctor_id=doctor_id))

        # check for conflicts
        conflict = db.appointments.find_one({
            "doctor_id": str(doctor["_id"]),
            "date": slot["date"],
            "time": slot["start_time"],
            "status": {"$in": ["Pending", "Accepted"]}
        })
        if conflict:
            flash("Slot already taken", "danger")
            return redirect(url_for("patient_book_appointment", doctor_id=doctor_id))

        # Create appointment
        appt = {
            "patient_id": str(patient_id),
            "patient_name": db.users.find_one({"_id": ObjectId(patient_id)}).get("name"),
            "doctor_id": str(doctor["_id"]),
            "doctor_name": doctor.get("name"),
            "date": slot["date"],
            "time": slot["start_time"],
            "status": "Pending",
            "created_at": datetime.utcnow(),
            "patient_message": message
        }
        db.appointments.insert_one(appt)
        db.availability.delete_one({"_id": ObjectId(slot_id)})

        flash("Appointment requested successfully", "success")
        return redirect(url_for("patient_dashboard"))

    return render_template("patient_doctor_availability.html", doctor=doctor, availability=availability)


@app.route("/doctor/availability/delete/<slot_id>", methods=["POST"])
@login_required
@role_required("doctor")
def delete_availability(slot_id):
    doctor_id = session.get("user_id")

    slot = db.availability.find_one({"_id": ObjectId(slot_id), "doctor_id": doctor_id})
    if not slot:
        flash("Slot not found or not authorized", "danger")
        return redirect(url_for("doctor_dashboard"))

    db.availability.delete_one({"_id": ObjectId(slot_id)})
    flash("Availability slot deleted successfully", "success")
    return redirect(url_for("doctor_dashboard"))

@app.route("/doctor/notifications")
@login_required
@role_required("doctor")
def doctor_notifications():
    doctor_id = session["user_id"]
    unread_messages = list(db.messages.find({"receiver_id": doctor_id, "read": False}).sort("timestamp", 1))
    pending_appointments = list(db.appointments.find({"doctor_id": doctor_id, "status": "Pending"}).sort([("date", 1), ("time", 1)]))
    return render_template("doctor_notifications.html", unread_messages=unread_messages, pending_appointments=pending_appointments)

@app.route("/update_appointment_status/<appointment_id>", methods=["POST"])
@login_required
@role_required("doctor")
def update_appointment_status(appointment_id):
    status = request.form.get("status", "")
    allowed = ["Pending", "Accepted", "Rejected", "Completed", "Rescheduled", "Cancelled"]
    if status not in allowed:
        flash("Invalid status.", "danger")
        return redirect(url_for("doctor_dashboard"))
    try:
        db.appointments.update_one({"_id": ObjectId(appointment_id)}, {"$set": {"status": status}})
        flash(f"Appointment updated to {status}", "success")
    except Exception:
        flash("Could not update appointment (invalid id?)", "danger")
    return redirect(url_for("doctor_dashboard"))

@app.route("/doctor/appointment/accept/<appointment_id>", methods=["POST"])
@login_required
@role_required("doctor")
def accept_appointment(appointment_id):
    try:
        db.appointments.update_one({"_id": ObjectId(appointment_id)}, {"$set": {"status": "Accepted"}})
        appt = db.appointments.find_one({"_id": ObjectId(appointment_id)})
        if appt and appt.get("patient_email"):
            send_email(appt["patient_email"], "Appointment Accepted", f"Your appointment with Dr. {appt.get('doctor_name')} on {appt.get('date')} at {appt.get('time')} was accepted.")
        flash("Appointment accepted", "success")
    except Exception:
        flash("Error accepting appointment", "danger")
    return redirect(url_for("doctor_dashboard"))

@app.route("/doctor/appointment/reject/<appointment_id>", methods=["POST"])
@login_required
@role_required("doctor")
def reject_appointment(appointment_id):
    try:
        db.appointments.update_one({"_id": ObjectId(appointment_id)}, {"$set": {"status": "Rejected"}})
        appt = db.appointments.find_one({"_id": ObjectId(appointment_id)})
        if appt and appt.get("patient_email"):
            send_email(appt["patient_email"], "Appointment Rejected", f"Your appointment with Dr. {appt.get('doctor_name')} on {appt.get('date')} at {appt.get('time')} was rejected.")
        flash("Appointment rejected", "info")
    except Exception:
        flash("Error rejecting appointment", "danger")
    return redirect(url_for("doctor_dashboard"))

@app.route("/doctor/appointment/reschedule/<appointment_id>", methods=["GET", "POST"])
@login_required
@role_required("doctor")
def doctor_reschedule_appointment(appointment_id):
    try:
        appt = db.appointments.find_one({"_id": ObjectId(appointment_id)})
    except Exception:
        appt = None
    if request.method == "POST":
        new_date = request.form.get("date", "").strip()
        new_time = request.form.get("time", "").strip()
        if not new_date or not new_time:
            flash("Date and time required", "warning")
            return redirect(url_for("doctor_dashboard"))
        db.appointments.update_one({"_id": ObjectId(appointment_id)}, {"$set": {"date": new_date, "time": new_time, "status": "Rescheduled"}})
        if appt and appt.get("patient_email"):
            send_email(appt["patient_email"], "Appointment Rescheduled", f"Your appointment with Dr. {appt.get('doctor_name')} has been rescheduled to {new_date} at {new_time}.")
        flash("Appointment rescheduled", "warning")
        return redirect(url_for("doctor_dashboard"))
    return render_template("doctor_reschedule.html", appt=appt)

@app.route("/doctor/messages")
@login_required
@role_required("doctor")
def doctor_messages():
    doctor_id = session["user_id"]
    doctor = db.users.find_one({"_id": ObjectId(doctor_id)})
    if not doctor:
        flash("Doctor not found", "danger")
        return redirect(url_for("logout"))
    msgs = list(db.messages.find({"$or": [{"sender_id": doctor_id}, {"receiver_id": doctor_id}]}).sort("timestamp", 1))
    patients = list(db.users.find({"role": "patient"}))
    return render_template("doctor_messages.html", doctor=doctor, messages=msgs, patients=patients)

@app.route("/doctor/send_message", methods=["POST"])
@login_required
@role_required("doctor")
def send_message():
    sender_id = session["user_id"]
    receiver_id = request.form.get("receiver_id")
    content = request.form.get("content", "").strip()
    if not receiver_id or not content:
        flash("Please select a patient and write a message.", "warning")
        return redirect(url_for("doctor_messages"))
    doctor = db.users.find_one({"_id": ObjectId(sender_id)})
    patient = db.users.find_one({"_id": ObjectId(receiver_id)})
    if not doctor or not patient:
        flash("Invalid sender or receiver.", "danger")
        return redirect(url_for("doctor_messages"))
    message = {"sender_id": sender_id, "sender_name": doctor.get("name", "Doctor"), "receiver_id": receiver_id, "receiver_name": patient.get("name", "Patient"), "sender_role": "doctor", "receiver_role": "patient", "content": content, "timestamp": datetime.utcnow(), "read": False}
    db.messages.insert_one(message)
    flash("Message sent successfully!", "success")
    return redirect(url_for("doctor_messages"))

@app.route("/patient/messages")
@login_required
@role_required("patient")
def patient_messages():
    patient_id = session["user_id"]
    patient = db.users.find_one({"_id": ObjectId(patient_id)})
    if not patient:
        flash("Patient not found", "danger")
        return redirect(url_for("logout"))
    msgs = list(db.messages.find({"$or": [{"sender_id": patient_id}, {"receiver_id": patient_id}]}).sort("timestamp", 1))
    doctors = list(db.users.find({"role": "doctor"}))
    return render_template("patient_messages.html", patient=patient, messages=msgs, doctors=doctors)

@app.route("/patient/send_message", methods=["POST"])
@login_required
@role_required("patient")
def send_patient_message():
    sender_id = session["user_id"]
    receiver_id = request.form.get("receiver_id")
    content = request.form.get("content", "").strip()
    if not receiver_id or not content:
        flash("Please select a doctor and write a message.", "warning")
        return redirect(url_for("patient_messages"))
    patient = db.users.find_one({"_id": ObjectId(sender_id)})
    doctor = db.users.find_one({"_id": ObjectId(receiver_id)})
    if not patient or not doctor:
        flash("Invalid sender or receiver.", "danger")
        return redirect(url_for("patient_messages"))
    message = {"sender_id": sender_id, "sender_name": patient.get("name", "Patient"), "receiver_id": receiver_id, "receiver_name": doctor.get("name", "Doctor"), "sender_role": "patient", "receiver_role": "doctor", "content": content, "timestamp": datetime.utcnow(), "read": False}
    db.messages.insert_one(message)
    flash("Message sent successfully!", "success")
    return redirect(url_for("patient_messages"))

@app.route("/patient/cancel/<appointment_id>")
@login_required
@role_required("patient")
def cancel_appointment(appointment_id):
    appt = db.appointments.find_one({"_id": ObjectId(appointment_id)})
    if not appt:
        abort(404)
    db.appointments.update_one({"_id": ObjectId(appointment_id)}, {"$set": {"status": "Cancelled"}})
    if appt.get("patient_email"):
        send_email(appt["patient_email"], "Appointment Cancelled", f"Your appointment with Dr. {appt.get('doctor_name')} on {appt.get('date')} at {appt.get('time')} has been cancelled.")
    flash("Appointment cancelled", "info")
    return redirect(url_for("patient_dashboard"))

@app.route("/patient/reschedule/<appointment_id>", methods=["POST"])
@login_required
@role_required("patient")
def patient_reschedule_appointment(appointment_id):
    new_date = request.form.get("date")
    new_time = request.form.get("time")
    if not new_date or not new_time:
        flash("Date and time required", "warning")
        return redirect(url_for("patient_dashboard"))
    appt = db.appointments.find_one({"_id": ObjectId(appointment_id)})
    if not appt:
        abort(404)
    db.appointments.update_one({"_id": ObjectId(appointment_id)}, {"$set": {"date": new_date, "time": new_time, "status": "Pending"}})
    if appt.get("patient_email"):
        send_email(appt["patient_email"], "Appointment Rescheduled", f"Your appointment with Dr. {appt.get('doctor_name')} has been rescheduled to {new_date} at {new_time}.")
    flash("Appointment rescheduled", "success")
    return redirect(url_for("patient_dashboard"))

# ----------------- REMINDERS -----------------
def send_appointment_reminders():
    now = datetime.now()   # Kenya local time
    print(f"[Reminder Check] Now: {now}")

    # Reminder windows
    time_windows = {
        "2h": now + timedelta(hours=2),
        "1h": now + timedelta(hours=1),
        "30m": now + timedelta(minutes=30),
        "24h": now + timedelta(hours=24)
    }

    appointments = list(db.appointments.find({"status": "Accepted"}))

    for appt in appointments:
        try:
            # Convert appointment datetime
            appt_time = datetime.strptime(
                f"{appt['date']} {appt['time']}", "%Y-%m-%d %H:%M"
            )
            print(f"Checking appointment: {appt_time}")

            # Fetch patient email from DB
            patient = db.users.find_one({"_id": ObjectId(appt["patient_id"])})
            if not patient:
                print("Patient not found, skipping.")
                continue

            patient_email = patient.get("email")
            if not patient_email:
                print("No patient email found, skipping.")
                continue

            # Track reminders to avoid duplicates
            if "reminders_sent" not in appt:
                appt["reminders_sent"] = []

            # Check each reminder window
            for key, target_time in time_windows.items():
                diff = abs((appt_time - target_time).total_seconds())

                # Use a 2-minute window instead of 30 seconds
                if diff < 120 and key not in appt["reminders_sent"]:

                    if key == "2h":
                        subject = "Appointment Reminder (In 2 Hours)"
                        body = (
                            f"Hi, this is a reminder that you have an appointment with "
                            f"Dr. {appt['doctor_name']} in 2 hours at {appt['time']}."
                        )

                    elif key == "1h":
                        subject = "Appointment Reminder (In 1 Hour)"
                        body = (
                            f"Hi, your appointment with Dr. {appt['doctor_name']} "
                            f"is in 1 hour at {appt['time']}."
                        )

                    elif key == "30m":
                        subject = "Appointment Reminder (In 30 Minutes)"
                        body = (
                            f"Reminder: Your appointment with Dr. {appt['doctor_name']} "
                            f"is in 30 minutes at {appt['time']}."
                        )

                    elif key == "24h":
                        subject = "Appointment Tomorrow"
                        body = (
                            f"Reminder: You have an appointment tomorrow with Dr. "
                            f"{appt['doctor_name']} at {appt['time']}."
                        )

                    print(f"Sending [{key}] reminder email to {patient_email}")
                    send_email(patient_email, subject, body)

                    # Mark reminder as sent
                    db.appointments.update_one(
                        {"_id": appt["_id"]},
                        {"$push": {"reminders_sent": key}}
                    )

        except Exception as e:
            logger.error(f"Error processing reminders: {e}")
            print(f"Error: {e}")

# ---------------- SCHEDULER ----------------
scheduler = BackgroundScheduler()

scheduler.add_job(
    func=send_appointment_reminders,
    trigger="interval",
    minutes=1,   # check every minute
    id="appointment_reminder_job",
    replace_existing=True
)

scheduler.start()
print("Appointment Reminder Scheduler Started...")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
