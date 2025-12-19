# 🏥 Doctor Appointment Management System

A web-based Doctor Appointment Management System built using **Flask** and **MongoDB**.  
The system allows patients to book appointments, doctors to manage availability and appointments, and admins to manage users — with **automatic email reminders**.

---

## 🚀 Features

### 👤 Patient
- Register and login
- View doctors and their availability
- Book, reschedule, and cancel appointments
- Receive **email reminders** (24h, 2h, 1h, 30min before appointment)
- Message doctors directly

### 🩺 Doctor
- Manage profile
- Set availability slots
- Accept, reject, reschedule appointments
- Receive appointment notifications
- Message patients

### 🛠 Admin
- Create, edit, and delete users (patients & doctors)
- View all appointments and system activity

---

## ⏰ Appointment Reminders
- Automated reminders using **APScheduler**
- Email notifications sent via **Gmail SMTP**
- Reminder intervals:
  - 24 hours
  - 2 hours
  - 1 hour
  - 30 minutes before appointment

---

## 🧰 Technologies Used

- **Backend:** Flask (Python)
- **Database:** MongoDB
- **Frontend:** HTML, CSS, Bootstrap
- **Authentication:** Flask Sessions & Werkzeug
- **Email Service:** Gmail SMTP
- **Task Scheduling:** APScheduler
- **Version Control:** Git & GitHub

---

## ⚙️ Installation & Setup

### 1. Clone the repository
```bash
git clone https://github.com/craigkibet071-sudo/doctor-appointment-system.git
cd doctor-appointment-system
