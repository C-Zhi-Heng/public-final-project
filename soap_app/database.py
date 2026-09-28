"""Local encounter storage: SQLCipher full-database encryption with sqlcipher3-wheels."""

import os
from datetime import datetime, timedelta

from sqlcipher3 import dbapi2 as sqlite3

from soap_app import auth
from soap_app.config import (
    AUDIT_LOG_PAGE_SIZE,
    DATA_DIR,
    DB_PATH,
    KEY_PATH,
    LOCKOUT_DURATION_MINUTES,
    MASTER_DUMMY_PASSWORD,
    MASTER_USERNAME,
    MAX_LOGIN_ATTEMPTS,
)

# Re-export sqlite3.IntegrityError for use in app.py / app_multipatient.py
IntegrityError = sqlite3.IntegrityError


# Dummy data for fresh database seeding
DUMMY_PATIENTS = [
    {
        "first_name": "Alice",
        "last_name": "Tan",
        "dob": "1985-03-14",
        "gender": "Female",
        "nric": "S8503140A",
        "address": "12 Orchard Rd, Singapore 238888",
        "blood_type": "A+",
        "phone": "91234567",
        "email": "alice.tan@example.com",
    },
    {
        "first_name": "Benjamin",
        "last_name": "Lee",
        "dob": "1978-11-02",
        "gender": "Male",
        "nric": "S7811020B",
        "address": "45 Bukit Timah Rd, Singapore 588089",
        "blood_type": "O-",
        "phone": "92345678",
        "email": "benjamin.lee@example.com",
    },
    {
        "first_name": "Chloe",
        "last_name": "Ng",
        "dob": "1993-07-21",
        "gender": "Female",
        "nric": "S9307210C",
        "address": "8 Tampines Ave 5, Singapore 529456",
        "blood_type": "B+",
        "phone": "93456789",
        "email": "chloe.ng@example.com",
    },
    {
        "first_name": "David",
        "last_name": "Kumar",
        "dob": "1966-01-30",
        "gender": "Male",
        "nric": "S6601300D",
        "address": "23 Serangoon Rd, Singapore 218123",
        "blood_type": "AB+",
        "phone": "94567890",
        "email": "david.kumar@example.com",
    },
    {
        "first_name": "Emily",
        "last_name": "Wong",
        "dob": "2001-09-09",
        "gender": "Female",
        "nric": "S0109090E",
        "address": "5 Jurong West St 41, Singapore 649414",
        "blood_type": "O+",
        "phone": "95678901",
        "email": "emily.wong@example.com",
    },
]

DUMMY_DOCTORS = [
    {
        "first_name": "Sarah",
        "last_name": "Lim",
        "dob": "1975-05-12",
        "gender": "Female",
        "nric": "S7505120F",
        "address": "1 Hospital Dr, Singapore 169608",
        "phone": "96789012",
        "email": "sarah.lim@clinic.com",
        "username": "drlim",
        "password": "DrLim123!",
    },
    {
        "first_name": "Marcus",
        "last_name": "Chua",
        "dob": "1968-08-25",
        "gender": "Male",
        "nric": "S6808250G",
        "address": "3 Outram Rd, Singapore 169045",
        "phone": "97890123",
        "email": "marcus.chua@clinic.com",
        "username": "drchua",
        "password": "DrChua123!",
    },
    {
        "first_name": "Priya",
        "last_name": "Rao",
        "dob": "1982-12-03",
        "gender": "Female",
        "nric": "S8212030H",
        "address": "7 Novena Ave, Singapore 307506",
        "phone": "98901234",
        "email": "priya.rao@clinic.com",
        "username": "drrao",
        "password": "DrRao123!",
    },
]


class AccountLockedError(Exception):
    """Raised by authenticate() when too many failed attempts have temporarily locked the account."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Account locked, try again in {retry_after_seconds} seconds")


def _get_sqlcipher_key() -> str:
    """Loads the SQLCipher raw-hex encryption key, generating one on first run (MVP-level key storage)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        # Generate a random 32-byte key and store as hex
        KEY_PATH.write_text(os.urandom(32).hex())
    return KEY_PATH.read_text().strip()


def _connect():
    """Opens a SQLCipher-encrypted database connection and sets the encryption key via PRAGMA."""
    conn = sqlite3.connect(str(DB_PATH))
    key_hex = _get_sqlcipher_key()
    conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
    return conn


def _seed_dummy_data(conn) -> None:
    """Inserts 5 dummy patients, 3 dummy doctor profiles, and 3 dummy doctor login accounts on fresh DB."""
    # Insert dummy patients
    for patient_data in DUMMY_PATIENTS:
        full_name = f"{patient_data['first_name'].strip()} {patient_data['last_name'].strip()}".strip()
        cursor = conn.execute(
            """
            INSERT INTO patients (
                name, created_at, first_name, last_name, dob, gender, nric, address, blood_type, phone, email
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                full_name,
                datetime.now().isoformat(timespec="seconds"),
                patient_data["first_name"].strip(),
                patient_data["last_name"].strip(),
                patient_data["dob"],
                patient_data["gender"],
                patient_data["nric"].strip() if patient_data["nric"] else None,
                patient_data["address"].strip() if patient_data["address"] else None,
                patient_data["blood_type"],
                patient_data["phone"].strip() if patient_data["phone"] else None,
                patient_data["email"].strip() if patient_data["email"] else None,
            ),
        )
        patient_id = cursor.lastrowid
        patient_code = f"P{patient_id:04d}"
        conn.execute("UPDATE patients SET patient_code = ? WHERE id = ?", (patient_code, patient_id))

    # Insert dummy doctors (profiles + login accounts)
    for doctor_data in DUMMY_DOCTORS:
        # Insert doctor profile
        cursor = conn.execute(
            """
            INSERT INTO doctor_profiles (
                doctor_code, first_name, last_name, dob, gender, nric, address, phone, email, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                doctor_data["first_name"].strip(),
                doctor_data["last_name"].strip(),
                doctor_data["dob"],
                doctor_data["gender"],
                doctor_data["nric"].strip() if doctor_data["nric"] else None,
                doctor_data["address"].strip() if doctor_data["address"] else None,
                doctor_data["phone"].strip() if doctor_data["phone"] else None,
                doctor_data["email"].strip() if doctor_data["email"] else None,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        doctor_id = cursor.lastrowid
        doctor_code = f"D{doctor_id:04d}"
        conn.execute("UPDATE doctor_profiles SET doctor_code = ? WHERE id = ?", (doctor_code, doctor_id))

        # Insert corresponding login account, linked back to the profile via user_id
        username = doctor_data["username"].strip()
        password = doctor_data["password"]
        password_hash, salt = auth.hash_password(password)
        user_cursor = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?, ?, ?, 'doctor', ?)",
            (username, password_hash, salt, datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(
            "UPDATE doctor_profiles SET user_id = ? WHERE id = ?", (user_cursor.lastrowid, doctor_id)
        )


def init_db() -> None:
    """Creates the encounters/patients tables if missing, and migrates older DBs to add patient_id."""
    is_fresh_db = not DB_PATH.exists()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS encounters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                patient_name TEXT NOT NULL,
                transcript TEXT NOT NULL,
                xray_analysis TEXT,
                soap_note TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # older DBs were created before patient_id existed; add it in place rather than requiring a fresh DB
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(encounters)")}
        if "patient_id" not in existing_columns:
            conn.execute("ALTER TABLE encounters ADD COLUMN patient_id INTEGER")

        # older DBs were created before full patient demographics existed; add the columns in place.
        # No per-field encryption is needed here (unlike the old Fernet version) since SQLCipher
        # encrypts the entire database file at rest, so PII columns are now plain TEXT.
        patient_columns = {row[1] for row in conn.execute("PRAGMA table_info(patients)")}
        patient_new_columns = {
            "patient_code": "TEXT",
            "first_name": "TEXT",
            "last_name": "TEXT",
            "dob": "TEXT",
            "gender": "TEXT",
            "nric": "TEXT",
            "address": "TEXT",
            "blood_type": "TEXT",
            "phone": "TEXT",
            "email": "TEXT",
        }
        for column_name, column_type in patient_new_columns.items():
            if column_name not in patient_columns:
                conn.execute(f"ALTER TABLE patients ADD COLUMN {column_name} {column_type}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS doctor_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_code TEXT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                dob TEXT,
                gender TEXT,
                nric TEXT,
                address TEXT,
                phone TEXT,
                email TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        # older DBs were created before the profile<->login link existed; add it in place.
        doctor_profile_columns = {row[1] for row in conn.execute("PRAGMA table_info(doctor_profiles)")}
        if "user_id" not in doctor_profile_columns:
            conn.execute("ALTER TABLE doctor_profiles ADD COLUMN user_id INTEGER REFERENCES users(id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_code TEXT,
                patient_id INTEGER NOT NULL,
                doctor_id INTEGER,
                appointment_date TEXT NOT NULL,
                appointment_time TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'Scheduled',
                created_at TEXT NOT NULL,
                FOREIGN KEY (patient_id) REFERENCES patients (id),
                FOREIGN KEY (doctor_id) REFERENCES doctor_profiles (id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash BLOB NOT NULL,
                salt BLOB NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('master', 'doctor')),
                created_at TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT
            )
            """
        )
        # seeds one master login on first run; MASTER_DUMMY_PASSWORD is a known weak-default limitation (see report)
        if conn.execute("SELECT 1 FROM users WHERE role = 'master'").fetchone() is None:
            password_hash, salt = auth.hash_password(MASTER_DUMMY_PASSWORD)
            conn.execute(
                "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?, ?, ?, 'master', ?)",
                (MASTER_USERNAME, password_hash, salt, datetime.now().isoformat(timespec="seconds")),
            )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details TEXT
            )
            """
        )

        # Seed dummy data on fresh database only
        if is_fresh_db:
            _seed_dummy_data(conn)


def save_encounter(
    patient_name: str, transcript: str, soap_note: str, xray_analysis: str | None = None
) -> None:
    """Inserts a new encounter row; encryption is handled by SQLCipher at the database level."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO encounters (created_at, patient_name, transcript, xray_analysis, soap_note) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                patient_name,
                transcript,
                xray_analysis,
                soap_note,
            ),
        )


def get_all_encounters() -> list[dict]:
    """Reads every legacy (non patient-linked) encounter, most recent first. Decryption is handled by SQLCipher."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, patient_name, transcript, xray_analysis, soap_note "
            "FROM encounters WHERE patient_id IS NULL ORDER BY id DESC"
        ).fetchall()

    encounters = []
    for row_id, created_at, patient_name, transcript, xray_analysis, soap_note in rows:
        encounters.append(
            {
                "id": row_id,
                "created_at": created_at,
                "patient_name": patient_name,
                "transcript": transcript,
                "xray_analysis": xray_analysis,
                "soap_note": soap_note,
            }
        )
    return encounters


def delete_encounter(encounter_id: int) -> None:
    """Permanently removes a single encounter row by id."""
    with _connect() as conn:
        conn.execute("DELETE FROM encounters WHERE id = ?", (encounter_id,))


# ---- Multi-patient (sidebar page) support: one row per patient, encounters linked via patient_id ----


def create_patient(name: str) -> int:
    """Inserts a new patient and returns its id, used as the sidebar page's stable identifier."""
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO patients (name, created_at) VALUES (?, ?)",
            (name, datetime.now().isoformat(timespec="seconds")),
        )
        return cursor.lastrowid


def get_all_patients() -> list[dict]:
    """Reads every patient, oldest first (stable sidebar ordering). Decryption is handled by SQLCipher."""
    with _connect() as conn:
        rows = conn.execute("SELECT id, name, created_at FROM patients ORDER BY id ASC").fetchall()
    return [
        {"id": row_id, "name": name, "created_at": created_at}
        for row_id, name, created_at in rows
    ]


def _blank_if_none(value: str | None) -> str:
    """Small helper for optional columns that may be NULL on older/partial records."""
    return value if value else ""


# ---- Full patient demographics (Create Patient ID page) ----


def create_patient_full(
    first_name: str,
    last_name: str,
    dob: str,
    gender: str,
    nric: str,
    address: str,
    blood_type: str,
    phone: str,
    email: str,
) -> int:
    """Creates a patient with full demographics; reuses the existing `patients`/`name` column for
    backward compatibility with the SOAP-note workflow (render_patient_page etc. key off `name`),
    while also storing the structured fields for the Patient ID page. Returns the new patient id,
    and stamps a sequential patient_code (e.g. P0001) once the id is known. Encryption of the whole
    database file is handled by SQLCipher, so these fields are stored as plain TEXT."""
    full_name = f"{first_name.strip()} {last_name.strip()}".strip()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO patients (
                name, created_at, first_name, last_name, dob, gender, nric, address, blood_type, phone, email
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                full_name,
                datetime.now().isoformat(timespec="seconds"),
                first_name.strip(),
                last_name.strip(),
                dob,
                gender,
                nric.strip() if nric else None,
                address.strip() if address else None,
                blood_type,
                phone.strip() if phone else None,
                email.strip() if email else None,
            ),
        )
        patient_id = cursor.lastrowid
        patient_code = f"P{patient_id:04d}"
        conn.execute("UPDATE patients SET patient_code = ? WHERE id = ?", (patient_code, patient_id))
        return patient_id


def get_all_patients_full() -> list[dict]:
    """Like get_all_patients(), but includes the full demographic record for each patient. Decryption
    of the whole database file is handled by SQLCipher."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, created_at, patient_code, first_name, last_name, dob, gender,
                   nric, address, blood_type, phone, email
            FROM patients ORDER BY id ASC
            """
        ).fetchall()

    patients = []
    for (row_id, name, created_at, patient_code, first_name, last_name, dob, gender,
         nric, address, blood_type, phone, email) in rows:
        patients.append(
            {
                "id": row_id,
                "name": name,
                "created_at": created_at,
                "patient_code": patient_code or f"P{row_id:04d}",
                "first_name": _blank_if_none(first_name),
                "last_name": _blank_if_none(last_name),
                "dob": dob or "",
                "gender": gender or "",
                "nric": _blank_if_none(nric),
                "address": _blank_if_none(address),
                "blood_type": blood_type or "",
                "phone": _blank_if_none(phone),
                "email": _blank_if_none(email),
            }
        )
    return patients


def get_patient(patient_id: int) -> dict | None:
    """Returns one patient's full decrypted demographic record, or None if not found."""
    for patient in get_all_patients_full():
        if patient["id"] == patient_id:
            return patient
    return None


def update_patient_full(
    patient_id: int,
    first_name: str,
    last_name: str,
    dob: str,
    gender: str,
    nric: str,
    address: str,
    blood_type: str,
    phone: str,
    email: str,
) -> None:
    """Updates a patient's demographic record in place. Also refreshes the legacy `name` column
    (used elsewhere, e.g. encounter pages) so it stays in sync with first/last name."""
    full_name = f"{first_name.strip()} {last_name.strip()}".strip()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE patients SET
                name = ?, first_name = ?, last_name = ?, dob = ?, gender = ?,
                nric = ?, address = ?, blood_type = ?, phone = ?, email = ?
            WHERE id = ?
            """,
            (
                full_name,
                first_name.strip(),
                last_name.strip(),
                dob,
                gender,
                nric.strip() if nric else None,
                address.strip() if address else None,
                blood_type,
                phone.strip() if phone else None,
                email.strip() if email else None,
                patient_id,
            ),
        )


# ---- Doctor profiles (Create Doctor ID page) — HR-style demographic record, separate from the
# `users` login accounts managed on the Administration > Manage Doctors page ----


def create_doctor_profile(
    first_name: str,
    last_name: str,
    dob: str,
    gender: str,
    nric: str,
    address: str,
    phone: str,
    email: str,
    username: str,
    password: str,
) -> int:
    """Creates a doctor profile together with its login account (one transaction, so a duplicate
    username or weak password leaves no orphan profile row). Raises ValueError on bad username/
    password, sqlite3.IntegrityError on a duplicate username. Returns the new doctor profile id."""
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    password_hash, salt = auth.hash_password(password)
    with _connect() as conn:
        user_cursor = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?, ?, ?, 'doctor', ?)",
            (username, password_hash, salt, datetime.now().isoformat(timespec="seconds")),
        )
        user_id = user_cursor.lastrowid

        cursor = conn.execute(
            """
            INSERT INTO doctor_profiles (
                doctor_code, first_name, last_name, dob, gender, nric, address, phone, email, created_at, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                first_name.strip(),
                last_name.strip(),
                dob,
                gender,
                nric.strip() if nric else None,
                address.strip() if address else None,
                phone.strip() if phone else None,
                email.strip() if email else None,
                datetime.now().isoformat(timespec="seconds"),
                user_id,
            ),
        )
        doctor_id = cursor.lastrowid
        doctor_code = f"D{doctor_id:04d}"
        conn.execute("UPDATE doctor_profiles SET doctor_code = ? WHERE id = ?", (doctor_code, doctor_id))
        return doctor_id


def get_all_doctor_profiles() -> list[dict]:
    """Reads every doctor profile, oldest first, along with its linked login's username (if any).
    Decryption of the whole database file is handled by SQLCipher."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT dp.id, dp.doctor_code, dp.first_name, dp.last_name, dp.dob, dp.gender, dp.nric,
                   dp.address, dp.phone, dp.email, dp.created_at, dp.user_id, u.username
            FROM doctor_profiles dp
            LEFT JOIN users u ON u.id = dp.user_id
            ORDER BY dp.id ASC
            """
        ).fetchall()

    doctors = []
    for (row_id, doctor_code, first_name, last_name, dob, gender, nric, address, phone, email,
         created_at, user_id, username) in rows:
        doctors.append(
            {
                "id": row_id,
                "doctor_code": doctor_code or f"D{row_id:04d}",
                "first_name": _blank_if_none(first_name),
                "last_name": _blank_if_none(last_name),
                "name": f"{_blank_if_none(first_name)} {_blank_if_none(last_name)}".strip(),
                "dob": dob or "",
                "gender": gender or "",
                "nric": _blank_if_none(nric),
                "address": _blank_if_none(address),
                "phone": _blank_if_none(phone),
                "email": _blank_if_none(email),
                "created_at": created_at,
                "user_id": user_id,
                "username": username,
            }
        )
    return doctors


def delete_doctor_profile(doctor_id: int) -> None:
    """Removes a doctor profile record. Appointments referencing it keep their doctor_id (historical record)."""
    with _connect() as conn:
        conn.execute("DELETE FROM doctor_profiles WHERE id = ?", (doctor_id,))


def delete_doctor(doctor_id: int) -> None:
    """Removes a doctor profile and its linked login account together, in one transaction."""
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM doctor_profiles WHERE id = ?", (doctor_id,)).fetchone()
        if row and row[0] is not None:
            conn.execute("DELETE FROM users WHERE id = ?", (row[0],))
        conn.execute("DELETE FROM doctor_profiles WHERE id = ?", (doctor_id,))


def get_doctor_profile(doctor_id: int) -> dict | None:
    """Returns one doctor's full decrypted profile record, or None if not found."""
    for doctor in get_all_doctor_profiles():
        if doctor["id"] == doctor_id:
            return doctor
    return None


def update_doctor_profile(
    doctor_id: int,
    first_name: str,
    last_name: str,
    dob: str,
    gender: str,
    nric: str,
    address: str,
    phone: str,
    email: str,
) -> None:
    """Updates a doctor profile record in place."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE doctor_profiles SET
                first_name = ?, last_name = ?, dob = ?, gender = ?,
                nric = ?, address = ?, phone = ?, email = ?
            WHERE id = ?
            """,
            (
                first_name.strip(),
                last_name.strip(),
                dob,
                gender,
                nric.strip() if nric else None,
                address.strip() if address else None,
                phone.strip() if phone else None,
                email.strip() if email else None,
                doctor_id,
            ),
        )


# ---- Appointments ----


def create_appointment(
    patient_id: int,
    doctor_id: int | None,
    appointment_date: str,
    appointment_time: str,
    reason: str = "",
) -> int:
    """Creates an appointment linked to a patient (and optionally a doctor profile). Returns the new id."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO appointments (
                appointment_code, patient_id, doctor_id, appointment_date, appointment_time,
                reason, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Scheduled', ?)
            """,
            (
                None,
                patient_id,
                doctor_id,
                appointment_date,
                appointment_time,
                reason,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        appointment_id = cursor.lastrowid
        appointment_code = f"A{appointment_id:05d}"
        conn.execute(
            "UPDATE appointments SET appointment_code = ? WHERE id = ?", (appointment_code, appointment_id)
        )
        return appointment_id


def get_all_appointments() -> list[dict]:
    """Returns every appointment, soonest first, with patient/doctor names resolved for display.
    Decryption of the whole database file is handled by SQLCipher."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.appointment_code, a.patient_id, p.name, a.doctor_id,
                   d.first_name, d.last_name, a.appointment_date, a.appointment_time,
                   a.reason, a.status, a.created_at
            FROM appointments a
            LEFT JOIN patients p ON p.id = a.patient_id
            LEFT JOIN doctor_profiles d ON d.id = a.doctor_id
            ORDER BY a.appointment_date ASC, a.appointment_time ASC
            """
        ).fetchall()

    appointments = []
    for (row_id, code, patient_id, patient_name, doctor_id, doc_first, doc_last,
         appt_date, appt_time, reason, status, created_at) in rows:
        doctor_name = ""
        if doc_first or doc_last:
            doctor_name = f"{_blank_if_none(doc_first)} {_blank_if_none(doc_last)}".strip()
        appointments.append(
            {
                "id": row_id,
                "appointment_code": code or f"A{row_id:05d}",
                "patient_id": patient_id,
                "patient_name": _blank_if_none(patient_name) or "(deleted patient)",
                "doctor_id": doctor_id,
                "doctor_name": doctor_name or "(unassigned)",
                "appointment_date": appt_date,
                "appointment_time": appt_time,
                "reason": reason or "",
                "status": status,
                "created_at": created_at,
            }
        )
    return appointments


def get_appointments_for_patient(patient_id: int) -> list[dict]:
    """Returns just one patient's appointments, soonest first."""
    return [a for a in get_all_appointments() if a["patient_id"] == patient_id]


def update_appointment_status(appointment_id: int, status: str) -> None:
    """Updates an appointment's status (e.g. Scheduled / Completed / Cancelled)."""
    with _connect() as conn:
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))


def get_appointment(appointment_id: int) -> dict | None:
    """Returns one appointment (with resolved patient/doctor names), or None if not found."""
    for appointment in get_all_appointments():
        if appointment["id"] == appointment_id:
            return appointment
    return None


def update_appointment(
    appointment_id: int,
    patient_id: int,
    doctor_id: int | None,
    appointment_date: str,
    appointment_time: str,
    reason: str,
    status: str,
) -> None:
    """Updates an appointment's full details (patient, doctor, date/time, reason, status) in place."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE appointments SET
                patient_id = ?, doctor_id = ?, appointment_date = ?, appointment_time = ?,
                reason = ?, status = ?
            WHERE id = ?
            """,
            (patient_id, doctor_id, appointment_date, appointment_time, reason, status, appointment_id),
        )


def delete_appointment(appointment_id: int) -> None:
    """Permanently removes one appointment."""
    with _connect() as conn:
        conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))


def rename_patient(patient_id: int, new_name: str) -> None:
    """Updates a patient's name; the sidebar page title picks this up on the next rerun."""
    with _connect() as conn:
        conn.execute("UPDATE patients SET name = ? WHERE id = ?", (new_name, patient_id))


def delete_patient(patient_id: int) -> None:
    """Removes a patient and all of their encounters (cascaded manually, no FK enforcement in sqlite3 by default)."""
    with _connect() as conn:
        conn.execute("DELETE FROM encounters WHERE patient_id = ?", (patient_id,))
        conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))


def save_patient_encounter(
    patient_id: int, transcript: str, soap_note: str, xray_analysis: str | None = None
) -> None:
    """Same as save_encounter, but linked to a patient_id instead of storing a name per row."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO encounters (created_at, patient_name, patient_id, transcript, xray_analysis, soap_note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                "",  # legacy column, unused for patient-linked encounters (kept only for schema compatibility)
                patient_id,
                transcript,
                xray_analysis,
                soap_note,
            ),
        )


def get_patient_encounters(patient_id: int) -> list[dict]:
    """Reads a single patient's encounters, most recent first. Decryption is handled by SQLCipher."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, created_at, transcript, xray_analysis, soap_note FROM encounters "
            "WHERE patient_id = ? ORDER BY id DESC",
            (patient_id,),
        ).fetchall()

    encounters = []
    for row_id, created_at, transcript, xray_analysis, soap_note in rows:
        encounters.append(
            {
                "id": row_id,
                "created_at": created_at,
                "transcript": transcript,
                "xray_analysis": xray_analysis,
                "soap_note": soap_note,
            }
        )
    return encounters


# ---- Login accounts (master + doctors), used only by app_multipatient.py ----


def create_user(username: str, password: str, role: str) -> int:
    """Creates a login account; raises ValueError on bad input, sqlite3.IntegrityError on a duplicate username."""
    username = username.strip()
    if not username:
        raise ValueError("Username cannot be empty.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    password_hash, salt = auth.hash_password(password)
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, salt, role, datetime.now().isoformat(timespec="seconds")),
        )
        return cursor.lastrowid


def authenticate(username: str, password: str) -> dict | None:
    """Returns {id, username, role} on success, None on wrong username/password, or raises AccountLockedError."""
    username = username.strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, salt, role, failed_attempts, locked_until "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return None
        user_id, db_username, password_hash, salt, role, failed_attempts, locked_until = row

        # locked accounts are rejected before even checking the password, so brute-forcing can't continue
        if locked_until and datetime.fromisoformat(locked_until) > datetime.now():
            remaining = datetime.fromisoformat(locked_until) - datetime.now()
            raise AccountLockedError(int(remaining.total_seconds()))

        if not auth.verify_password(password, salt, password_hash):
            failed_attempts += 1
            new_locked_until = None
            if failed_attempts >= MAX_LOGIN_ATTEMPTS:
                new_locked_until = (
                    datetime.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                ).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                (failed_attempts, new_locked_until, user_id),
            )
            return None

        conn.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (user_id,))
        return {"id": user_id, "username": db_username, "role": role}


def get_all_doctors() -> list[dict]:
    """Returns doctor accounts only (not master), oldest first for a stable list order."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, username, created_at FROM users WHERE role = 'doctor' ORDER BY created_at ASC"
        ).fetchall()
    return [{"id": row_id, "username": username, "created_at": created_at} for row_id, username, created_at in rows]


def delete_user(user_id: int) -> None:
    """Removes a login account; patients/encounters are untouched since they aren't owned by doctors."""
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def set_password(user_id: int, new_password: str) -> None:
    """Re-hashes with a fresh salt and clears any existing lockout, so a reset also undoes a brute-force lock."""
    if len(new_password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    password_hash, salt = auth.hash_password(new_password)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (password_hash, salt, user_id),
        )


# ---- Audit log: metadata only (who/what/when), never clinical text, so it needs no encryption ----


def log_action(username: str, action: str, target: str | None = None, details: str | None = None) -> None:
    """Records one audit entry; called at every login/logout/patient/doctor checkpoint in app_multipatient.py."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, username, action, target, details) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), username, action, target, details),
        )


def get_recent_audit_log(limit: int = AUDIT_LOG_PAGE_SIZE) -> list[dict]:
    """Returns the most recent audit entries, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT timestamp, username, action, target, details FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"timestamp": ts, "username": username, "action": action, "target": target, "details": details}
        for ts, username, action, target, details in rows
    ]
