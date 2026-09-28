"""
in parent folder (DocAssist) create a .venv using requirements.txt with: python -m venv .venv
so dir would be DocAssist/.venv and DocAssist/soap_app
activate venv in DocAssist dir
Run with: python -m streamlit run soap_app/app.py
"""

import re
import sqlite3
import time
from datetime import date, datetime
from functools import partial
from pathlib import Path
import tempfile

import streamlit as st

from soap_app.config import RECORDING_PATH, SESSION_TIMEOUT_MINUTES
from soap_app.database import (
    AccountLockedError,
    IntegrityError,
    authenticate,
    create_appointment,
    create_doctor_profile,
    create_patient_full,
    delete_appointment,
    delete_doctor,
    delete_encounter,
    delete_patient,
    get_all_appointments,
    get_all_doctor_profiles,
    get_all_patients_full,
    get_patient_encounters,
    get_recent_audit_log,
    init_db,
    log_action,
    save_patient_encounter,
    set_password,
    update_appointment,
    update_appointment_status,
    update_doctor_profile,
    update_patient_full,
)
from soap_app.imaging import analyze_xray
from soap_app.ollama_utils import ensure_ollama_running
from soap_app.soap_note import generate_soap_note
from soap_app.transcription import Recorder, transcribe_audio

st.set_page_config(page_title="SOAP Note Assistant (multi-patient)", layout="centered")

# init_db() must run before the login gate below, since authenticate() needs the users table to exist.
init_db()

GENDER_OPTIONS = ["Male", "Female", "Other"]
BLOOD_TYPE_OPTIONS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "Unknown"]
APPOINTMENT_STATUSES = ["Scheduled", "Completed", "Cancelled"]

HELP_TOPIC_ICONS = {
    "Getting Started": "🚀",
    "Managing Patients": "👤",
    "Managing Doctors": "🩺",
    "Appointments": "📅",
    "Recording an Encounter": "🎙️",
    "Administration": "⚙️",
    "FAQ & Troubleshooting": "🛟",
}

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None
if "login_message" not in st.session_state:
    st.session_state.login_message = None


def render_login_page() -> None:
    """Login form. Always rendered via st.navigation (even for one page): calling st.stop() before
    st.navigation() left Streamlit's client-side sidebar stuck showing the previous session's pages
    after logout, unclickable, until a full browser refresh."""
    st.title("SOAP Note Assistant — Login")
    if st.session_state.login_message:
        st.info(st.session_state.login_message)
        st.session_state.login_message = None
    with st.form("login_form"):
        login_username = st.text_input("Username")
        login_password = st.text_input("Password", type="password")
        login_submitted = st.form_submit_button("Log in")
    if login_submitted:
        attempted_username = login_username.strip()
        try:
            logged_in_user = authenticate(login_username, login_password)
        except AccountLockedError as e:
            minutes, seconds = divmod(e.retry_after_seconds, 60)
            log_action(attempted_username, "login_locked", details=f"retry_after={e.retry_after_seconds}s")
            st.error(f"Too many failed attempts. Try again in {minutes}m {seconds}s.")
        else:
            if logged_in_user is None:
                log_action(attempted_username, "login_failed")
                st.error("Invalid username or password.")
            else:
                log_action(logged_in_user["username"], "login_success")
                st.session_state.auth_user = logged_in_user
                st.session_state.last_activity = time.time()
                st.rerun()


if st.session_state.auth_user is None:
    login_page = st.Page(render_login_page, title="Login", url_path="login")
    st.navigation([login_page], position="hidden").run()
    st.stop()

auth_user = st.session_state.auth_user

# Fallback check on this rerun (e.g. right after page load); the ticking fragment below is what
# actually catches an idle timeout without needing the user to click anything first.
_now = time.time()
if _now - st.session_state.get("last_activity", _now) > SESSION_TIMEOUT_MINUTES * 60:
    log_action(auth_user["username"], "session_timeout")
    st.session_state.auth_user = None
    st.session_state.login_message = "Session expired due to inactivity. Please log in again."
    st.rerun()
st.session_state.last_activity = _now


@st.fragment(run_every="5s")
def _render_session_countdown() -> None:
    """Reruns on its own every 5s (independent of clicks) so idle logout is proactive, with a live countdown."""
    remaining = SESSION_TIMEOUT_MINUTES * 60 - (time.time() - st.session_state.get("last_activity", time.time()))
    if remaining <= 0:
        log_action(st.session_state.auth_user["username"], "session_timeout")
        st.session_state.auth_user = None
        st.session_state.login_message = "Session expired due to inactivity. Please log in again."
        st.rerun()
    else:
        minutes, seconds = divmod(int(remaining), 60)
        st.sidebar.caption(f"Session expires in {minutes}m {seconds}s of inactivity")


# Ollama is only started once someone is actually logged in, so the login screen itself loads instantly.
if "ollama_ready" not in st.session_state:
    with st.spinner("Starting Ollama..."):
        st.session_state.ollama_ready = ensure_ollama_running()
if not st.session_state.ollama_ready:
    st.error("Could not reach Ollama. Install it from ollama.com and make sure it's on your PATH, then reload.")
    st.stop()

# One shared recorder + per-patient dict so switching sidebar pages doesn't mix up different patients' data.
if "recorder" not in st.session_state:
    st.session_state.recorder = Recorder()
if "is_recording" not in st.session_state:
    st.session_state.is_recording = False
if "patient_state" not in st.session_state:
    st.session_state.patient_state = {}  # patient_id -> {"transcript": ..., "xray_analysis": ..., "soap_note": ...}


def _state_for(patient_id: int) -> dict:
    """Returns this patient's own transcript/X-ray/SOAP-note slot, creating it on first visit."""
    return st.session_state.patient_state.setdefault(
        patient_id, {"transcript": "", "xray_analysis": "", "soap_note": "", "saved": False}
    )


def _total_encounters(patients: list[dict]) -> int:
    """Sums saved encounters across all patients, via the existing per-patient accessor rather than
    a raw DB query — keeps this file storage-backend-agnostic."""
    return sum(len(get_patient_encounters(p["id"])) for p in patients)


# Recognized SOAP section headers, in canonical display order. Matches "Subjective", "S:", "**Subjective:**",
# etc. at the start of a line — tolerant of whatever light formatting phi3:mini tends to produce.
_SOAP_SECTIONS = [
    ("subjective", "Subjective", r"s(?:ubjective)?"),
    ("objective", "Objective", r"o(?:bjective)?"),
    ("assessment", "Assessment", r"a(?:ssessment)?"),
    ("plan", "Plan", r"p(?:lan)?"),
]


def _split_soap_sections(note_text: str) -> dict[str, str] | None:
    """Splits a generated SOAP note into its four sections for tabbed display.

    Purely a string-formatting step on the output of generate_soap_note() — does not call the
    model or change what gets saved to the database. Returns None if the text doesn't look like
    labeled SOAP sections, so callers can fall back to showing it as plain text.
    """
    pattern = re.compile(
        r"^\s*\**(" + "|".join(regex for _, _, regex in _SOAP_SECTIONS) + r")\**\s*:?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(pattern.finditer(note_text))
    if len(matches) < 2:  # too little structure to bother with tabs
        return None

    found: dict[str, str] = {}
    for i, match in enumerate(matches):
        key = match.group(1).strip().lower()[0]  # first letter: s/o/a/p
        section_id = next(sid for sid, _, _ in _SOAP_SECTIONS if sid[0] == key)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(note_text)
        found[section_id] = note_text[start:end].strip()

    return found if found else None


# ---------------------------------------------------------------------------
# Dashboard — visible to everyone; patients are shared across doctors, so
# nothing on this page is scoped to the logged-in user.
# ---------------------------------------------------------------------------


def render_dashboard_page() -> None:
    st.title("📊 Dashboard")
    st.write(f"Welcome, **{st.session_state.auth_user['username']}**.")

    patients = get_all_patients_full()
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Patients", len(patients))
    col2.metric("Total Encounters", _total_encounters(patients))
    col3.metric("Role", st.session_state.auth_user["role"].title())

    st.divider()
    st.caption("Open an existing patient from the sidebar, or add a new one below.")
    if st.button("➕ Add New Patient"):
        st.switch_page(st.session_state.nav_pages["new_patient"])


# ---------------------------------------------------------------------------
# New Patient — full demographics form, ported from app.py's Create Patient ID page.
# ---------------------------------------------------------------------------


def render_new_patient_form() -> None:
    st.title("➕ New Patient")
    with st.form("new_patient_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        first_name = c1.text_input("First Name")
        last_name = c2.text_input("Last Name")
        dob = c1.date_input(
            "Date of Birth", value=date(1990, 1, 1), min_value=date(1900, 1, 1), max_value=date.today()
        )
        gender = c2.selectbox("Gender", GENDER_OPTIONS)
        nric = c1.text_input("NRIC")
        blood_type = c2.selectbox("Blood Type", BLOOD_TYPE_OPTIONS)
        phone = c1.text_input("Phone")
        email = c2.text_input("Email")
        address = st.text_area("Address")
        submitted = st.form_submit_button("Create Patient")

    if submitted:
        if not first_name.strip() or not last_name.strip():
            st.error("First Name and Last Name are required.")
        else:
            patient_id = create_patient_full(
                first_name, last_name, dob.isoformat(), gender, nric, address, blood_type, phone, email
            )
            log_action(
                st.session_state.auth_user["username"], "create_patient", target=str(patient_id),
                details=f"{first_name} {last_name}",
            )
            st.success(f"Patient created — Patient ID P{patient_id:04d}. They'll appear in the sidebar on next load.")


# ---------------------------------------------------------------------------
# Per-patient page — demographics management + the audio/X-ray/SOAP workflow.
# ---------------------------------------------------------------------------


def render_patient_page(patient_id: int, patient: dict) -> None:
    """Renders one patient's sidebar page: demographics, the encounter workflow, and past encounters.
    Every logged-in user (master or doctor) sees the same patients here — there is no assignment
    or per-doctor filtering."""
    state = _state_for(patient_id)
    st.title(f"{patient['patient_code']} — {patient['name']}")

    # ---- Demographics, rename via first/last name, delete ----
    with st.expander("Manage patient"):
        with st.form(f"edit_patient_form_{patient_id}"):
            c1, c2 = st.columns(2)
            first_name = c1.text_input("First Name", value=patient["first_name"])
            last_name = c2.text_input("Last Name", value=patient["last_name"])
            dob_value = date.fromisoformat(patient["dob"]) if patient["dob"] else date(1990, 1, 1)
            dob = c1.date_input(
                "Date of Birth", value=dob_value, min_value=date(1900, 1, 1), max_value=date.today(),
                key=f"dob_{patient_id}",
            )
            gender = c2.selectbox(
                "Gender", GENDER_OPTIONS,
                index=GENDER_OPTIONS.index(patient["gender"]) if patient["gender"] in GENDER_OPTIONS else 0,
                key=f"gender_{patient_id}",
            )
            nric = c1.text_input("NRIC", value=patient["nric"])
            blood_type = c2.selectbox(
                "Blood Type", BLOOD_TYPE_OPTIONS,
                index=BLOOD_TYPE_OPTIONS.index(patient["blood_type"]) if patient["blood_type"] in BLOOD_TYPE_OPTIONS else 0,
                key=f"blood_type_{patient_id}",
            )
            phone = c1.text_input("Phone", value=patient["phone"])
            email = c2.text_input("Email", value=patient["email"])
            address = st.text_area("Address", value=patient["address"])
            save_submitted = st.form_submit_button("Save Changes")

        if save_submitted:
            if not first_name.strip() or not last_name.strip():
                st.error("First Name and Last Name are required.")
            else:
                update_patient_full(
                    patient_id, first_name, last_name, dob.isoformat(), gender, nric, address, blood_type, phone, email,
                )
                log_action(
                    st.session_state.auth_user["username"], "update_patient", target=str(patient_id),
                    details=f"{first_name} {last_name}",
                )
                st.success("Patient updated.")
                st.rerun()

        st.divider()
        st.write("Deleting removes this patient and all of their saved encounters.")
        confirm = st.checkbox("I understand, delete this patient", key=f"confirm_delete_{patient_id}")
        if st.button("Delete patient", key=f"delete_{patient_id}", disabled=not confirm):
            delete_patient(patient_id)
            log_action(
                st.session_state.auth_user["username"], "delete_patient", target=str(patient_id),
                details=patient["name"],
            )
            st.session_state.patient_state.pop(patient_id, None)
            st.rerun()

    c1, c2, c3 = st.columns(3)
    c1.write(f"**DOB:** {patient['dob'] or '—'}")
    c1.write(f"**Gender:** {patient['gender'] or '—'}")
    c2.write(f"**Blood Type:** {patient['blood_type'] or '—'}")
    c2.write(f"**Phone:** {patient['phone'] or '—'}")
    c3.write(f"**Email:** {patient['email'] or '—'}")
    c3.write(f"**NRIC:** {patient['nric'] or '—'}")

    st.divider()

    # ---- Appointments for this patient only — no patient selector needed, since we're already
    # scoped to one patient. The "Doctor" field is optional scheduling metadata; every doctor can
    # see and manage every patient's appointments here (patients remain shared across doctors). ----
    st.subheader("📅 Appointments")
    doctors = get_all_doctor_profiles()
    patient_appointments = [a for a in get_all_appointments() if a["patient_id"] == patient_id]

    with st.expander("➕ New Appointment", expanded=not patient_appointments):
        with st.form(f"new_appointment_form_{patient_id}", clear_on_submit=True):
            doctor_choice = st.selectbox(
                "Doctor", options=[None] + doctors,
                format_func=lambda d: "— Unassigned —" if d is None else f"{d['doctor_code']} — Dr. {d['name']}",
                key=f"new_appt_doctor_{patient_id}",
            )
            c1, c2 = st.columns(2)
            appt_date = c1.date_input("Date", value=date.today(), key=f"new_appt_date_{patient_id}")
            appt_time = c2.time_input("Time", key=f"new_appt_time_{patient_id}")
            reason = st.text_input("Reason / Notes", key=f"new_appt_reason_{patient_id}")
            submitted = st.form_submit_button("Schedule Appointment")

        if submitted:
            appt_id = create_appointment(
                patient_id,
                doctor_choice["id"] if doctor_choice else None,
                appt_date.isoformat(),
                appt_time.strftime("%H:%M"),
                reason,
            )
            log_action(
                st.session_state.auth_user["username"], "create_appointment", target=str(appt_id),
                details=patient["name"],
            )
            st.success(f"Appointment A{appt_id:05d} scheduled.")
            st.rerun()

    if not patient_appointments:
        st.caption("No appointments scheduled yet.")
    else:
        for a in patient_appointments:
            with st.expander(
                f"{a['appointment_code']} — {a['appointment_date']} {a['appointment_time']} "
                f"with {a['doctor_name']} ({a['status']})"
            ):
                edit_mode = st.toggle("Edit", key=f"edit_toggle_a_{a['id']}")

                if edit_mode:
                    current_doctor = next((d for d in doctors if d["id"] == a["doctor_id"]), None)
                    with st.form(f"edit_appointment_form_{a['id']}"):
                        doctor_options = [None] + doctors
                        doctor_choice = st.selectbox(
                            "Doctor", options=doctor_options,
                            index=doctor_options.index(current_doctor) if current_doctor in doctor_options else 0,
                            format_func=lambda d: "— Unassigned —" if d is None else f"{d['doctor_code']} — Dr. {d['name']}",
                            key=f"edit_doctor_{a['id']}",
                        )
                        c1, c2 = st.columns(2)
                        appt_date = c1.date_input(
                            "Date", value=date.fromisoformat(a["appointment_date"]), key=f"edit_date_{a['id']}"
                        )
                        appt_time = c2.time_input(
                            "Time", value=datetime.strptime(a["appointment_time"], "%H:%M").time(),
                            key=f"edit_time_{a['id']}",
                        )
                        reason = st.text_input("Reason / Notes", value=a["reason"], key=f"edit_reason_{a['id']}")
                        status = st.selectbox(
                            "Status", APPOINTMENT_STATUSES,
                            index=APPOINTMENT_STATUSES.index(a["status"]) if a["status"] in APPOINTMENT_STATUSES else 0,
                            key=f"edit_status_{a['id']}",
                        )
                        save_submitted = st.form_submit_button("Save Changes")

                    if save_submitted:
                        update_appointment(
                            a["id"], patient_id, doctor_choice["id"] if doctor_choice else None,
                            appt_date.isoformat(), appt_time.strftime("%H:%M"), reason, status,
                        )
                        log_action(
                            st.session_state.auth_user["username"], "update_appointment",
                            target=str(a["id"]), details=status,
                        )
                        st.success("Appointment updated.")
                        st.rerun()

                    if st.button("Delete Appointment", key=f"del_appt_{a['id']}"):
                        delete_appointment(a["id"])
                        log_action(st.session_state.auth_user["username"], "delete_appointment", target=str(a["id"]))
                        st.rerun()
                    continue

                st.write(f"**Reason:** {a['reason'] or '—'}")
                sc1, sc2 = st.columns([2, 1])
                new_status = sc1.selectbox(
                    "Status", APPOINTMENT_STATUSES,
                    index=APPOINTMENT_STATUSES.index(a["status"]) if a["status"] in APPOINTMENT_STATUSES else 0,
                    key=f"status_{a['id']}",
                )
                if sc1.button("Update Status", key=f"update_status_{a['id']}"):
                    update_appointment_status(a["id"], new_status)
                    log_action(
                        st.session_state.auth_user["username"], "update_appointment",
                        target=str(a["id"]), details=new_status,
                    )
                    st.rerun()
                if sc2.button("Delete", key=f"del_appt_view_{a['id']}"):
                    delete_appointment(a["id"])
                    log_action(st.session_state.auth_user["username"], "delete_appointment", target=str(a["id"]))
                    st.rerun()

    st.divider()

    # ---- Workflow steps, as tabs ----
    audio_tab, xray_tab, soap_tab = st.tabs(["🎙️ Audio", "🩻 X-ray (optional)", "📋 SOAP Note"])

    # ---- 1. Consultation audio ----
    with audio_tab:
        audio_source = st.radio(
            "Audio source", ["Record live", "Upload a file"], horizontal=True, key=f"audio_source_{patient_id}"
        )

        if audio_source == "Record live":
            col1, col2 = st.columns(2)
            if col1.button("Start Recording", disabled=st.session_state.is_recording, key=f"start_{patient_id}"):
                st.session_state.recorder.start()
                st.session_state.is_recording = True
            if col2.button("Stop Recording", disabled=not st.session_state.is_recording, key=f"stop_{patient_id}"):
                RECORDING_PATH.parent.mkdir(parents=True, exist_ok=True)
                st.session_state.recorder.stop(str(RECORDING_PATH))
                st.session_state.is_recording = False
                with st.status("Transcribing...", expanded=True) as status:
                    state["transcript"] = transcribe_audio(str(RECORDING_PATH))
                    status.update(label="Transcription complete", state="complete")
            if st.session_state.is_recording:
                st.info("Recording... click Stop when finished.")
        else:
            uploaded_audio = st.file_uploader(
                "Upload consultation audio", type=["wav", "mp3", "m4a"], key=f"audio_upload_{patient_id}"
            )
            if uploaded_audio is not None and st.button("Transcribe Uploaded Audio", key=f"transcribe_{patient_id}"):
                suffix = Path(uploaded_audio.name).suffix
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(uploaded_audio.read())
                    tmp_path = tmp.name
                with st.status("Transcribing...", expanded=True) as status:
                    state["transcript"] = transcribe_audio(tmp_path)
                    status.update(label="Transcription complete", state="complete")

        if state["transcript"]:
            with st.container(border=True):
                st.caption("Transcript")
                st.text_area(
                    "Transcript", state["transcript"], height=150, label_visibility="collapsed",
                    key=f"transcript_display_{patient_id}",
                )

    # ---- 2. Optional X-ray ----
    with xray_tab:
        uploaded_image = st.file_uploader(
            "Upload X-ray image", type=["png", "jpg", "jpeg"], key=f"xray_{patient_id}"
        )
        if uploaded_image is not None and st.button("Analyze X-ray", key=f"analyze_{patient_id}"):
            suffix = Path(uploaded_image.name).suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(uploaded_image.read())
                tmp_path = tmp.name
            with st.status("Analyzing X-ray with MedGemma...", expanded=True) as status:
                state["xray_analysis"] = analyze_xray(tmp_path)
                status.update(label="X-ray analysis complete", state="complete")

        if state["xray_analysis"]:
            with st.container(border=True):
                st.caption("X-ray Analysis")
                st.text_area(
                    "X-ray Analysis", state["xray_analysis"], height=150, label_visibility="collapsed",
                    key=f"xray_display_{patient_id}",
                )

    # ---- 3. SOAP note generation ----
    with soap_tab:
        if st.button("Generate SOAP Note", disabled=not state["transcript"], key=f"generate_{patient_id}"):
            with st.status("Generating SOAP note with phi3:mini...", expanded=True) as status:
                state["soap_note"] = generate_soap_note(state["transcript"], state["xray_analysis"] or None)
                state["saved"] = False
                status.update(label="SOAP note generated", state="complete")

        if state["soap_note"]:
            st.text_area(
                "SOAP Note", state["soap_note"], height=250, label_visibility="collapsed",
                key=f"soap_display_{patient_id}",
            )

            st.markdown(":green[● Saved]" if state["saved"] else ":orange[● Draft — not yet saved]")
            if st.button("Save Encounter", key=f"save_encounter_{patient_id}"):
                save_patient_encounter(
                    patient_id=patient_id,
                    transcript=state["transcript"],
                    soap_note=state["soap_note"],
                    xray_analysis=state["xray_analysis"] or None,
                )
                log_action(st.session_state.auth_user["username"], "save_encounter", target=str(patient_id))
                state["saved"] = True
                st.success("Encounter saved.")

    # ---- 4. Past encounters for this patient ----
    st.header("Past Encounters")
    encounters = get_patient_encounters(patient_id)
    if not encounters:
        st.write("No saved encounters yet.")
    else:
        for encounter in encounters:
            header_col, delete_col = st.columns([5, 1])
            with header_col.expander(encounter["created_at"]):
                st.write("**Transcript**")
                st.write(encounter["transcript"])
                if encounter["xray_analysis"]:
                    st.write("**X-ray Analysis**")
                    st.write(encounter["xray_analysis"])
                st.write("**SOAP Note**")
                st.write(encounter["soap_note"])
            if delete_col.button("Delete", key=f"delete_encounter_{encounter['id']}"):
                delete_encounter(encounter["id"])
                log_action(st.session_state.auth_user["username"], "delete_encounter", target=str(encounter["id"]))
                st.rerun()



# ---------------------------------------------------------------------------
# Doctors — HR-style demographic records, separate from login accounts.
# Master-only: doctors must not see this list (rule: "doctors cannot see
# doctor list"). This page is also simply omitted from a doctor's nav below,
# so a doctor user never even sees a link to it.
# ---------------------------------------------------------------------------


def render_doctors_page() -> None:
    if st.session_state.auth_user["role"] != "master":
        st.error("Not authorized.")  # defense in depth; this page is never linked to for non-master users
        return

    st.title("🩺 Doctors")
    list_tab, new_doctor_tab = st.tabs(["Doctor List", "➕ New Doctor"])

    with list_tab:
        doctors = get_all_doctor_profiles()
        if not doctors:
            st.info("No doctor records yet. Add one from the **➕ New Doctor** tab.")
        else:
            for d in doctors:
                with st.expander(f"{d['doctor_code']} — Dr. {d['name']}"):
                    edit_mode = st.toggle("Edit", key=f"edit_toggle_d_{d['id']}")

                    if edit_mode:
                        with st.form(f"edit_doctor_form_{d['id']}"):
                            c1, c2 = st.columns(2)
                            first_name = c1.text_input("First Name", value=d["first_name"])
                            last_name = c2.text_input("Last Name", value=d["last_name"])
                            dob_value = date.fromisoformat(d["dob"]) if d["dob"] else date(1980, 1, 1)
                            dob = c1.date_input(
                                "Date of Birth", value=dob_value, min_value=date(1900, 1, 1), max_value=date.today(),
                            )
                            gender = c2.selectbox(
                                "Gender", GENDER_OPTIONS,
                                index=GENDER_OPTIONS.index(d["gender"]) if d["gender"] in GENDER_OPTIONS else 0,
                            )
                            nric = c1.text_input("NRIC", value=d["nric"])
                            phone = c2.text_input("Phone", value=d["phone"])
                            email = st.text_input("Email", value=d["email"])
                            address = st.text_area("Address", value=d["address"])
                            save_submitted = st.form_submit_button("Save Changes")

                        if save_submitted:
                            if not first_name.strip() or not last_name.strip():
                                st.error("First Name and Last Name are required.")
                            else:
                                update_doctor_profile(
                                    d["id"], first_name, last_name, dob.isoformat(), gender,
                                    nric, address, phone, email,
                                )
                                log_action(
                                    st.session_state.auth_user["username"], "update_doctor_profile",
                                    target=str(d["id"]), details=f"{first_name} {last_name}",
                                )
                                st.success("Doctor updated.")
                                st.rerun()
                        continue

                    c1, c2 = st.columns(2)
                    c1.write(f"**DOB:** {d['dob'] or '—'}")
                    c1.write(f"**Gender:** {d['gender'] or '—'}")
                    c1.write(f"**NRIC:** {d['nric'] or '—'}")
                    c2.write(f"**Phone:** {d['phone'] or '—'}")
                    c2.write(f"**Email:** {d['email'] or '—'}")
                    st.write(f"**Address:** {d['address'] or '—'}")

                    st.divider()
                    if d["user_id"] is None:
                        # legacy/unlinked profile from before profiles and logins were synced
                        st.caption("⚠️ No login account linked to this profile.")
                    else:
                        st.write(f"**Login username:** {d['username']}")
                        with st.form(f"change_pw_form_{d['id']}"):
                            new_pw = st.text_input("New password", type="password", key=f"new_pw_{d['id']}")
                            confirm_pw = st.text_input(
                                "Confirm new password", type="password", key=f"confirm_pw_{d['id']}"
                            )
                            pw_submitted = st.form_submit_button("Change Password")

                        if pw_submitted:
                            if new_pw != confirm_pw:
                                st.error("Passwords do not match.")
                            else:
                                try:
                                    set_password(d["user_id"], new_pw)
                                except ValueError as e:
                                    st.error(str(e))
                                else:
                                    log_action(
                                        st.session_state.auth_user["username"], "reset_password",
                                        target=d["username"],
                                    )
                                    st.success("Password updated.")

                    st.divider()
                    confirm = st.checkbox(
                        "I understand, delete this doctor's profile and login", key=f"confirm_del_d_{d['id']}"
                    )
                    if st.button("Delete Doctor", key=f"del_d_{d['id']}", disabled=not confirm):
                        delete_doctor(d["id"])
                        log_action(
                            st.session_state.auth_user["username"], "delete_doctor",
                            target=str(d["id"]), details=d["name"],
                        )
                        st.rerun()

    with new_doctor_tab:
        st.caption("Creates the doctor's demographic record and login account together.")
        with st.form("new_doctor_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            first_name = c1.text_input("First Name")
            last_name = c2.text_input("Last Name")
            dob = c1.date_input(
                "Date of Birth", value=date(1980, 1, 1), min_value=date(1900, 1, 1), max_value=date.today()
            )
            gender = c2.selectbox("Gender", GENDER_OPTIONS)
            nric = c1.text_input("NRIC")
            phone = c2.text_input("Phone")
            email = st.text_input("Email")
            address = st.text_area("Address")
            st.divider()
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Create Doctor")

        if submitted:
            if not first_name.strip() or not last_name.strip():
                st.error("First Name and Last Name are required.")
            elif password != confirm_password:
                st.error("Passwords do not match.")
            else:
                try:
                    doctor_id = create_doctor_profile(
                        first_name, last_name, dob.isoformat(), gender, nric, address, phone, email,
                        username, password,
                    )
                except ValueError as e:
                    st.error(str(e))
                except IntegrityError:
                    st.error("That username is already taken.")
                else:
                    log_action(
                        st.session_state.auth_user["username"], "create_doctor_profile", target=str(doctor_id),
                        details=f"{first_name} {last_name} ({username.strip()})",
                    )
                    st.success(f"Doctor created — Doctor ID D{doctor_id:04d}.")


def render_audit_log_page() -> None:
    """Master-only: read-only view of recent login/patient/doctor/appointment actions (metadata only,
    no clinical text)."""
    if st.session_state.auth_user["role"] != "master":
        st.error("Not authorized.")  # defense in depth; this page is never linked to for non-master users
        return

    st.title("📋 Audit Log")
    st.caption("Most recent actions, newest first. Records who did what and when — never transcript/SOAP text.")
    entries = get_recent_audit_log()
    if not entries:
        st.write("No audit entries yet.")
    else:
        st.dataframe(entries, use_container_width=True, hide_index=True)


def render_help_page(topic: str | None) -> None:
    st.markdown("## ❓ Help")
    st.caption("How to use this app. Pick a topic from the left, or read top to bottom.")

    topic = topic or "Getting Started"

    if topic == "Getting Started":
        st.markdown("### 🚀 Getting Started")
        st.markdown(
            "**Logging in.** Use the account your clinic administrator gave you. The first time this app "
            "is set up, a default **master** account is seeded — ask your administrator for those credentials "
            "if you don't have your own login yet."
        )
        st.markdown(
            "**The two roles.**\n"
            "- **Master** — full access, including Administration (creating doctor logins, resetting "
            "passwords, and viewing the audit log).\n"
            "- **Doctor** — everything except Administration: Dashboard, Patients, Doctors, and Appointments."
        )
        st.markdown(
            "**The Dashboard** shows your total patients, total encounters, and your role, plus quick-start "
            "shortcuts to add a patient or schedule an appointment."
        )
        st.markdown(
            "**Sessions time out** after a period of inactivity — you'll see a live countdown at the bottom "
            "of the left panel (\"Session expires in...\"). Save your work before it runs out; there's no "
            "way to recover an unsaved SOAP note draft after a timeout."
        )

    elif topic == "Managing Patients":
        st.markdown("### 👤 Managing Patients")
        st.markdown(
            "**Adding a patient.** Go to **Patients > New Patient**, fill in First Name and Last Name "
            "(required) plus whatever demographics you have — DOB, Gender, NRIC, Address, Blood Type, "
            "Phone, Email are all optional. A Patient ID (e.g. P0001) is generated automatically."
        )
        st.markdown(
            "**Viewing / editing.** Open a patient from the sidebar — expand **Manage patient** to see or "
            "change any field, then **Save Changes**."
        )
        st.markdown(
            "**Opening a patient's clinical record.** Selecting a patient from the sidebar takes you to "
            "their encounter workflow — this is where you record consultations and generate SOAP notes "
            "(see *Recording an Encounter*)."
        )
        st.markdown(
            "**Deleting a patient.** Tick **I understand, delete this patient** before the Delete patient "
            "button becomes clickable. This also permanently deletes all of that patient's saved encounters "
            "— there's no undo."
        )

    elif topic == "Managing Doctors":
        st.markdown("### 🩺 Managing Doctors")
        st.markdown(
            "A doctor record is a single profile that bundles both the HR-style demographic details "
            "(name, DOB, gender, NRIC, address, phone, email) and the login (username + password) that "
            "lets that doctor sign in — the two are created, changed, and deleted together."
        )
        st.markdown(
            "**Creating a doctor.** **Doctors > ➕ New Doctor**, fill in the demographic fields plus a "
            "username and password (minimum 6 characters), submit. A Doctor ID (e.g. D0001) is generated "
            "automatically and the login is active immediately."
        )
        st.markdown(
            "**Editing.** Expand a doctor in **Doctor List**, toggle **Edit** to update demographic fields, "
            "or use the **Change Password** form further down the same expander to reset their login "
            "password."
        )
        st.markdown(
            "**Deleting.** Tick the confirm box and click **Delete Doctor** to remove both the profile and "
            "the login together — there's no way to remove just one side. Deleting a doctor that's linked "
            "to an existing appointment doesn't delete the appointment — it just shows as \"(unassigned)\"."
        )

    elif topic == "Appointments":
        st.markdown("### 📅 Appointments")
        st.markdown(
            "Open a patient from the sidebar and go to their **📅 Appointments** section. Open **➕ New "
            "Appointment**, pick a doctor (optional — leave as \"— Unassigned —\" if you don't know yet), "
            "set the date, time, and a short reason, then **Schedule Appointment**."
        )
        st.markdown(
            "**Changing status.** Each appointment has a quick **Status** dropdown (Scheduled / Completed / "
            "Cancelled) with an **Update Status** button — the fastest way to mark a visit done."
        )
        st.markdown(
            "**Full edit.** Toggle **Edit** on an appointment to change the doctor, date, time, reason, or "
            "status all at once."
        )
        st.markdown("Appointments are scoped to the patient whose page you're on — there's no cross-patient list.")

    elif topic == "Recording an Encounter":
        st.markdown("### 🎙️ Recording an Encounter (SOAP Notes)")
        st.markdown("This is the core clinical workflow, found on each patient's page. It has three tabs plus a history:")
        st.markdown(
            "**1. 🎙️ Audio.** Either record live (**Start Recording** → speak → **Stop Recording**) "
            "or upload an existing audio file (.wav/.mp3/.m4a). Either way, this app transcribes it to text "
            "automatically using a local speech-to-text model — no audio ever leaves your machine."
        )
        st.markdown(
            "**2. 🩻 X-ray (optional).** Upload an X-ray image and click **Analyze X-ray** to get a structured "
            "FINDINGS / IMPRESSION report from a local vision model. Skip this step entirely if there's no "
            "imaging for this visit."
        )
        st.markdown(
            "**3. 📋 SOAP Note.** Once there's a transcript (X-ray optional), click **Generate SOAP "
            "Note**. It combines the transcript and any X-ray analysis into a SUBJECTIVE / OBJECTIVE / "
            "ASSESSMENT / PLAN note. Review it carefully — always check it against what was actually said "
            "before relying on it."
        )
        st.markdown(
            "**4. Save Encounter.** Nothing is stored until you click this — a generated note that isn't "
            "saved is lost if you navigate away. Once saved, it appears under **Past Encounters** below, "
            "which you can expand to review or delete at any time."
        )
        st.markdown(
            "**Tip:** you can work on different patients' encounters in parallel — each patient's "
            "in-progress transcript/X-ray/SOAP draft is kept separate until you save or navigate away."
        )

    elif topic == "Administration":
        st.markdown("### ⚙️ Administration")
        st.caption("This section is only visible to the master account.")
        st.markdown(
            "**Doctors.** Create a doctor's profile and login together (username + password, minimum 6 "
            "characters) under **➕ New Doctor**, reset an existing doctor's password from their entry in "
            "**Doctor List**, or delete a doctor entirely (which removes both their profile and login) — "
            "see *Managing Doctors* for details."
        )
        st.markdown(
            "**Audit Log.** A running record of who did what and when — logins, logouts, and every create/"
            "edit/delete action across patients, doctors, appointments, and encounters. It records "
            "*metadata only*: usernames, actions, and timestamps — never clinical text like transcripts or "
            "SOAP notes."
        )

    else:  # FAQ & Troubleshooting
        st.markdown("### 🛟 FAQ & Troubleshooting")
        st.markdown("**\"Could not reach Ollama\" on login.** This app needs a local Ollama server "
                    "running with the required models pulled. Install Ollama from ollama.com, then run "
                    "`ollama pull phi3:mini` and `ollama pull medgemma:latest`, and reload the page.")
        st.markdown("**\"Generate SOAP Note\" button is disabled.** You need a transcript first — record "
                    "or upload consultation audio in the Audio tab before generating a note.")
        st.markdown("**I got locked out after failed login attempts.** After 5 failed attempts, an "
                    "account locks temporarily. Wait for the countdown shown on the login error, or ask "
                    "a master user to reset your password (which also clears the lockout).")
        st.markdown("**I forgot my password.** You can't reset your own password from the login screen — "
                    "ask a master user to reset it from your entry under **Doctors > Doctor List**.")
        st.markdown("**My session ended while I was working.** Sessions expire after a period of "
                    "inactivity for security. Unsaved transcripts/SOAP drafts aren't recoverable after "
                    "that — save encounters as you go rather than leaving them until the end.")
        st.markdown("**Is my patient data safe?** All sensitive fields (names, NRIC, address, phone, "
                    "email, transcripts, SOAP notes) are encrypted at rest in the local database, and "
                    "passwords are hashed, never stored in plain text.")


# ---- Sidebar: who's logged in + log out (pinned above everything else) ----
st.sidebar.write(f"Logged in as **{auth_user['username']}** ({auth_user['role']})")
_render_session_countdown()
if st.sidebar.button("Log out"):
    log_action(auth_user["username"], "logout")
    st.session_state.auth_user = None
    st.rerun()
st.sidebar.divider()

# ---------------------------------------------------------------------------
# Build the nav: Overview + Patients sections for everyone; Administration
# section (Doctors — HR profiles + login accounts, and Audit Log) for master
# only. A doctor never sees the Administration section at all. All patients below are
# the same shared list for every user — no per-doctor assignment or filtering.
# ---------------------------------------------------------------------------
patients = get_all_patients_full()
patient_pages = [
    st.Page(
        partial(render_patient_page, patient_id=p["id"], patient=p),
        title=p["name"] or p["patient_code"],
        url_path=f"patient-{p['id']}",
    )
    for p in patients
]

dashboard_page = st.Page(render_dashboard_page, title="Dashboard", url_path="dashboard", default=True)
new_patient_page = st.Page(render_new_patient_form, title="➕ New Patient", url_path="new-patient")

st.session_state.nav_pages = {"new_patient": new_patient_page}

nav_sections = {
    "Overview": [dashboard_page],
    "Patients": patient_pages + [new_patient_page],
}

if auth_user["role"] == "master":
    nav_sections["Administration"] = [
        st.Page(render_doctors_page, title="🩺 Doctors", url_path="doctors"),
        st.Page(render_audit_log_page, title="📋 Audit Log", url_path="audit-log"),
    ]

nav_sections["Help"] = [
    st.Page(
        partial(render_help_page, topic=topic),
        title=f"{icon} {topic}",
        url_path=f"help-{topic.lower().replace(' ', '-').replace('&', 'and')}",
    )
    for topic, icon in HELP_TOPIC_ICONS.items()
]

st.navigation(nav_sections).run()
