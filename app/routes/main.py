from app.permissions import roles_required
from flask import request, redirect, url_for
from flask_login import current_user
from flask_login import login_required
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    abort,
    flash
)
from sqlalchemy import text
from app import db

main_bp = Blueprint("main", __name__)

def validate_money_amount(value, allow_zero=False):
    """Return a two-decimal Decimal for a valid monetary input."""
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Please enter a valid payment amount.")

    if not amount.is_finite():
        raise ValueError("NaN and Infinity are not valid payment amounts.")
    if amount < Decimal("0"):
        raise ValueError("Payment amounts cannot be negative.")
    if not allow_zero and amount == Decimal("0"):
        raise ValueError("Payment amount must be greater than zero.")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Payment amounts cannot have more than two decimal places.")

    return amount.quantize(Decimal("0.01"))

def validate_payment_date(value, admission_date):
    try:
        payment_date = datetime.strptime(
            value.strip(), "%Y-%m-%d"
        ).date()

        minimum_date = datetime.strptime(
            str(admission_date)[:10], "%Y-%m-%d"
        ).date()

    except (ValueError, AttributeError):
        raise ValueError(
            "Please enter a valid payment date."
        )

    if payment_date < minimum_date:
        raise ValueError(
            "Payment date cannot be earlier than the admission date."
        )

    return payment_date

@main_bp.before_request
def require_authentication():
    if not current_user.is_authenticated:
        return redirect(
            url_for("auth.login", next=request.full_path.rstrip("?"))
        )

@main_bp.route("/")
@login_required

def dashboard():

    # ---------------------------------
    # Role-Based Financial Permissions
    # ---------------------------------

    can_view_financials = current_user.role in (
        "administrator",
        "receptionist"
    )

    # ---------------------------------
    # Main Dashboard Statistics
    # ---------------------------------

    patient_count = db.session.execute(
        text("SELECT COUNT(*) FROM patient")
    ).scalar()

    doctor_count = db.session.execute(
        text("SELECT COUNT(*) FROM doctor")
    ).scalar()

    admission_count = db.session.execute(
        text("SELECT COUNT(*) FROM admission")
    ).scalar()

    active_admission_count = db.session.execute(
        text("""
            SELECT COUNT(*)
            FROM admission
            WHERE AdmissionStatus = 'Active'
        """)
    ).scalar()

    completed_admissions = db.session.execute(
        text("""
            SELECT COUNT(*)
            FROM admission
            WHERE AdmissionStatus = 'Discharged'
        """)
    ).scalar()

    # ---------------------------------
    # Monthly Admissions Trend
    # ---------------------------------

    monthly_admissions = db.session.execute(
        text("""
            SELECT
                DATE_FORMAT(AdmissionDate, '%Y-%m') AS Month,
                COUNT(*) AS AdmissionCount
            FROM admission
            GROUP BY DATE_FORMAT(AdmissionDate, '%Y-%m')
            ORDER BY Month ASC
        """)
    ).mappings().all()

    # ---------------------------------
    # Fill Missing Months With Zero
    # ---------------------------------

    if monthly_admissions:

        monthly_counts = {
            row["Month"]: row["AdmissionCount"]
            for row in monthly_admissions
        }

        start_month = datetime.strptime(
            monthly_admissions[0]["Month"],
            "%Y-%m"
        )

        end_month = datetime.strptime(
            monthly_admissions[-1]["Month"],
            "%Y-%m"
        )

        complete_monthly_admissions = []

        current_month = start_month

        while current_month <= end_month:

            month_label = current_month.strftime("%Y-%m")

            complete_monthly_admissions.append({
                "Month": month_label,
                "AdmissionCount": monthly_counts.get(
                    month_label,
                    0
                )
            })

            if current_month.month == 12:
                current_month = current_month.replace(
                    year=current_month.year + 1,
                    month=1
                )
            else:
                current_month = current_month.replace(
                    month=current_month.month + 1
                )

        monthly_admissions = complete_monthly_admissions

    # ---------------------------------
    # Recent Admissions
    # ---------------------------------

    recent_admissions = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                p.PatientName,
                d.DoctorName,
                dep.DepartmentName,
                a.AdmissionDate,
                a.AdmissionStatus
            FROM admission a
            JOIN patient p
                ON a.PatientID = p.PatientID
            JOIN doctor d
                ON a.DoctorID = d.DoctorID
            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID
            ORDER BY
                a.AdmissionDate DESC,
                a.AdmissionID DESC
            LIMIT 5
        """)
    ).mappings().all()

    # ---------------------------------
    # Default Financial Values
    # ---------------------------------

    total_revenue = None
    outstanding_balance = None
    total_billed = None
    collection_rate = None
    outstanding_percentage = None

    # ---------------------------------
    # Financial Statistics
    # Authorized Roles Only
    # ---------------------------------

    if can_view_financials:

        financial_summary = db.session.execute(
            text("""
                SELECT
                    COALESCE(SUM(AmountPaid), 0)
                        AS TotalRevenue,

                    COALESCE(
                        SUM(TotalAmount - AmountPaid),
                        0
                    ) AS OutstandingBalance,

                    COALESCE(SUM(TotalAmount), 0)
                        AS TotalBilled

                FROM payment
            """)
        ).mappings().first()

        total_revenue = financial_summary["TotalRevenue"]

        outstanding_balance = financial_summary[
            "OutstandingBalance"
        ]

        total_billed = financial_summary["TotalBilled"]

        # ---------------------------------
        # Financial KPI Calculations
        # ---------------------------------

        collection_rate = (
            round(
                (total_revenue / total_billed) * 100,
                2
            )
            if total_billed > 0
            else 0
        )

        outstanding_percentage = (
            round(
                (outstanding_balance / total_billed) * 100,
                2
            )
            if total_billed > 0
            else 0
        )

    # ---------------------------------
    # Render Dashboard
    # ---------------------------------

    return render_template(
        "dashboard.html",
        patient_count=patient_count,
        doctor_count=doctor_count,
        admission_count=admission_count,
        active_admission_count=active_admission_count,
        completed_admissions=completed_admissions,
        recent_admissions=recent_admissions,
        monthly_admissions=monthly_admissions,
        total_revenue=total_revenue,
        outstanding_balance=outstanding_balance,
        total_billed=total_billed,
        collection_rate=collection_rate,
        outstanding_percentage=outstanding_percentage
    )


@main_bp.route("/patients")
def patients():

    # Get search/filter values from the URL
    search = request.args.get("search", "").strip()
    gender = request.args.get("gender", "").strip()
    blood_group = request.args.get("blood_group", "").strip()

    # Base query
    query = """
        SELECT
            PatientID,
            CNIC,
            PatientName,
            Gender,
            DateOfBirth,
            PhoneNumber,
            BloodGroup
        FROM patient
        WHERE 1 = 1
    """

    params = {}

    # Search by Patient Name, CNIC or Phone Number
    if search:

        query += """
            AND (
                PatientName LIKE :search
                OR CNIC LIKE :search
                OR PhoneNumber LIKE :search
            )
        """

        params["search"] = f"%{search}%"

    # Filter by Gender
    if gender:

        query += """
            AND Gender = :gender
        """

        params["gender"] = gender

    # Filter by Blood Group
    if blood_group:

        query += """
            AND BloodGroup = :blood_group
        """

        params["blood_group"] = blood_group

    # Sort newest patients first
    query += """
        ORDER BY PatientID DESC
    """

    # Execute query
    patient_list = db.session.execute(
        text(query),
        params
    ).mappings().all()

    # Send results and current filters to HTML
    return render_template(
        "patients.html",
        patients=patient_list,
        search=search,
        gender=gender,
        blood_group=blood_group
    )

@main_bp.route("/patients/add", methods=["GET", "POST"])
def add_patient():

    if request.method == "POST":

        patient_name = request.form.get("patient_name")
        cnic = request.form.get("cnic")
        gender = request.form.get("gender")
        date_of_birth = request.form.get("date_of_birth")
        phone_number = request.form.get("phone_number")
        blood_group = request.form.get("blood_group")
        emergency_contact = request.form.get("emergency_contact")
        address = request.form.get("address")

        db.session.execute(
            text("""
                INSERT INTO patient
                (
                    CNIC,
                    PatientName,
                    Gender,
                    DateOfBirth,
                    PhoneNumber,
                    Address,
                    BloodGroup,
                    EmergencyContact
                )
                VALUES
                (
                    :cnic,
                    :patient_name,
                    :gender,
                    :date_of_birth,
                    :phone_number,
                    :address,
                    :blood_group,
                    :emergency_contact
                )
            """),
            {
                "cnic": cnic,
                "patient_name": patient_name,
                "gender": gender,
                "date_of_birth": date_of_birth or None,
                "phone_number": phone_number or None,
                "address": address or None,
                "blood_group": blood_group or None,
                "emergency_contact": emergency_contact or None
            }
        )

        db.session.commit()

        return redirect(url_for("main.patients"))

    return render_template("add_patient.html")


@main_bp.route("/patients/<int:patient_id>")
def patient_details(patient_id):

    patient = db.session.execute(
        text("""
            SELECT
                PatientID,
                CNIC,
                PatientName,
                Gender,
                DateOfBirth,
                PhoneNumber,
                Address,
                BloodGroup,
                EmergencyContact
            FROM patient
            WHERE PatientID = :patient_id
        """),
        {
            "patient_id": patient_id
        }
    ).mappings().first()

    if patient is None:
        abort(404)

    admission_history = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionType,
                a.AdmissionStatus,
                a.RoomNumber,
                d.DoctorName,
                dep.DepartmentName,
                dis.DiseaseName
            FROM admission a
            LEFT JOIN doctor d
                ON a.DoctorID = d.DoctorID
            LEFT JOIN department dep
                ON a.DepartmentID = dep.DepartmentID
            LEFT JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID
            WHERE a.PatientID = :patient_id
            ORDER BY a.AdmissionDate DESC, a.AdmissionID DESC
        """),
        {
            "patient_id": patient_id
        }
    ).mappings().all()

    return render_template(
        "patient_details.html",
        patient=patient,
        admission_history=admission_history
    )

@main_bp.route("/patients/<int:patient_id>/edit", methods=["GET", "POST"])
def edit_patient(patient_id):

    patient = db.session.execute(
        text("""
            SELECT
                PatientID,
                CNIC,
                PatientName,
                Gender,
                DateOfBirth,
                PhoneNumber,
                Address,
                BloodGroup,
                EmergencyContact
            FROM patient
            WHERE PatientID = :patient_id
        """),
        {
            "patient_id": patient_id
        }
    ).mappings().first()

    if patient is None:
        abort(404)

    if request.method == "POST":

        patient_name = request.form.get("patient_name")
        cnic = request.form.get("cnic")
        gender = request.form.get("gender")
        date_of_birth = request.form.get("date_of_birth")
        phone_number = request.form.get("phone_number")
        blood_group = request.form.get("blood_group")
        emergency_contact = request.form.get("emergency_contact")
        address = request.form.get("address")

        db.session.execute(
            text("""
                UPDATE patient

                SET
                    CNIC = :cnic,
                    PatientName = :patient_name,
                    Gender = :gender,
                    DateOfBirth = :date_of_birth,
                    PhoneNumber = :phone_number,
                    Address = :address,
                    BloodGroup = :blood_group,
                    EmergencyContact = :emergency_contact

                WHERE PatientID = :patient_id
            """),
            {
                "cnic": cnic,
                "patient_name": patient_name,
                "gender": gender,
                "date_of_birth": date_of_birth or None,
                "phone_number": phone_number or None,
                "address": address or None,
                "blood_group": blood_group or None,
                "emergency_contact": emergency_contact or None,
                "patient_id": patient_id
            }
        )

        db.session.commit()

        return redirect(
            url_for(
                "main.patient_details",
                patient_id=patient_id
            )
        )

    return render_template(
        "edit_patient.html",
        patient=patient
    )

@main_bp.route(
    "/patients/<int:patient_id>/delete",
    methods=["POST"]
)
@roles_required("administrator")
def delete_patient(patient_id):

    patient = db.session.execute(
        text("""
            SELECT
                PatientID,
                PatientName
            FROM patient
            WHERE PatientID = :patient_id
        """),
        {
            "patient_id": patient_id
        }
    ).mappings().first()

    if patient is None:
        abort(404)

    admission_count = db.session.execute(
        text("""
            SELECT COUNT(*)
            FROM admission
            WHERE PatientID = :patient_id
        """),
        {
            "patient_id": patient_id
        }
    ).scalar()

    if admission_count > 0:

        flash(
            "This patient cannot be deleted because admission "
            "records already exist.",
            "danger"
        )

        return redirect(
            url_for(
                "main.patient_details",
                patient_id=patient_id
            )
        )

    db.session.execute(
        text("""
            DELETE FROM patient
            WHERE PatientID = :patient_id
        """),
        {
            "patient_id": patient_id
        }
    )

    db.session.commit()

    flash(
        f"{patient.PatientName} was deleted successfully.",
        "success"
    )

    return redirect(
        url_for("main.patients")
    )


@main_bp.route("/departments")
def departments():

    department_list = db.session.execute(
        text("""
            SELECT
                dep.DepartmentID,
                dep.DepartmentName,
                dep.Location,
                dep.ContactExtension,
                COUNT(d.DoctorID) AS DoctorCount
            FROM department dep
            LEFT JOIN doctor d
                ON dep.DepartmentID = d.DepartmentID
            GROUP BY
                dep.DepartmentID,
                dep.DepartmentName,
                dep.Location,
                dep.ContactExtension
            ORDER BY dep.DepartmentID
        """)
    ).mappings().all()

    return render_template(
        "departments.html",
        departments=department_list
    )


@main_bp.route("/departments/add", methods=["GET", "POST"])
@roles_required("administrator")
def add_department():

    if request.method == "POST":

        department_name = request.form.get(
            "department_name", ""
        ).strip()

        location = request.form.get(
            "location", ""
        ).strip()

        contact_extension = request.form.get(
            "contact_extension", ""
        ).strip()

        # Basic validation
        if not department_name:

            flash(
                "Department name is required.",
                "danger"
            )

            return render_template(
                "add_department.html"
            )

        # Check for duplicate department name
        existing_department = db.session.execute(
            text("""
                SELECT DepartmentID
                FROM department
                WHERE DepartmentName = :department_name
            """),
            {
                "department_name": department_name
            }
        ).first()

        if existing_department:

            flash(
                "A department with this name already exists.",
                "danger"
            )

            return render_template(
                "add_department.html"
            )

        # Insert department
        db.session.execute(
            text("""
                INSERT INTO department
                (
                    DepartmentName,
                    Location,
                    ContactExtension
                )
                VALUES
                (
                    :department_name,
                    :location,
                    :contact_extension
                )
            """),
            {
                "department_name": department_name,
                "location": location or None,
                "contact_extension": contact_extension or None
            }
        )

        db.session.commit()

        flash(
            f"{department_name} department was added successfully.",
            "success"
        )

        return redirect(
            url_for("main.departments")
        )

    return render_template(
        "add_department.html"
    )

@main_bp.route("/departments/<int:department_id>")
def department_details(department_id):

    # Get department information
    department = db.session.execute(
        text("""
            SELECT
                DepartmentID,
                DepartmentName,
                Location,
                ContactExtension
            FROM department
            WHERE DepartmentID = :department_id
        """),
        {
            "department_id": department_id
        }
    ).mappings().first()

    # Department does not exist
    if department is None:
        abort(404)

    # Get doctors assigned to this department
    doctors = db.session.execute(
        text("""
            SELECT
                DoctorID,
                LicenseNo,
                DoctorName,
                Specialization,
                PhoneNumber
            FROM doctor
            WHERE DepartmentID = :department_id
            ORDER BY DoctorName
        """),
        {
            "department_id": department_id
        }
    ).mappings().all()

    return render_template(
        "department_details.html",
        department=department,
        doctors=doctors
    )


@main_bp.route(
    "/departments/<int:department_id>/edit",
    methods=["GET", "POST"]
)
@roles_required("administrator")
def edit_department(department_id):

    # Get existing department
    department = db.session.execute(
        text("""
            SELECT
                DepartmentID,
                DepartmentName,
                Location,
                ContactExtension
            FROM department
            WHERE DepartmentID = :department_id
        """),
        {
            "department_id": department_id
        }
    ).mappings().first()

    if department is None:
        abort(404)


    if request.method == "POST":

        department_name = request.form.get(
            "department_name", ""
        ).strip()

        location = request.form.get(
            "location", ""
        ).strip()

        contact_extension = request.form.get(
            "contact_extension", ""
        ).strip()


        # Department name cannot be empty
        if not department_name:

            flash(
                "Department name is required.",
                "danger"
            )

            return render_template(
                "edit_department.html",
                department=department
            )


        # Check if another department already uses this name
        duplicate = db.session.execute(
            text("""
                SELECT DepartmentID
                FROM department
                WHERE DepartmentName = :department_name
                AND DepartmentID != :department_id
            """),
            {
                "department_name": department_name,
                "department_id": department_id
            }
        ).first()


        if duplicate:

            flash(
                "Another department already uses this name.",
                "danger"
            )

            return render_template(
                "edit_department.html",
                department=department
            )


        # Update department
        db.session.execute(
            text("""
                UPDATE department

                SET
                    DepartmentName = :department_name,
                    Location = :location,
                    ContactExtension = :contact_extension

                WHERE DepartmentID = :department_id
            """),
            {
                "department_name": department_name,
                "location": location or None,
                "contact_extension": contact_extension or None,
                "department_id": department_id
            }
        )

        db.session.commit()


        flash(
            f"{department_name} was updated successfully.",
            "success"
        )


        return redirect(
            url_for(
                "main.department_details",
                department_id=department_id
            )
        )


    return render_template(
        "edit_department.html",
        department=department
    )


@main_bp.route(
    "/departments/<int:department_id>/delete",
    methods=["POST"]
)
@roles_required("administrator")
def delete_department(department_id):

    # Get department
    department = db.session.execute(
        text("""
            SELECT
                DepartmentID,
                DepartmentName
            FROM department
            WHERE DepartmentID = :department_id
        """),
        {
            "department_id": department_id
        }
    ).mappings().first()

    if department is None:
        abort(404)


    # Check assigned doctors
    doctor_count = db.session.execute(
        text("""
            SELECT COUNT(*)
            FROM doctor
            WHERE DepartmentID = :department_id
        """),
        {
            "department_id": department_id
        }
    ).scalar()


    # Check admissions
    admission_count = db.session.execute(
        text("""
            SELECT COUNT(*)
            FROM admission
            WHERE DepartmentID = :department_id
        """),
        {
            "department_id": department_id
        }
    ).scalar()


    # Block deletion if department is in use
    if doctor_count > 0 or admission_count > 0:

        flash(
            f"Cannot delete {department.DepartmentName}. "
            f"It is linked to {doctor_count} doctor(s) and "
            f"{admission_count} admission(s).",
            "danger"
        )

        return redirect(
            url_for("main.departments")
        )


    # Safe to delete
    db.session.execute(
        text("""
            DELETE FROM department
            WHERE DepartmentID = :department_id
        """),
        {
            "department_id": department_id
        }
    )

    db.session.commit()


    flash(
        f"{department.DepartmentName} was deleted successfully.",
        "success"
    )


    return redirect(
        url_for("main.departments")
    )


@main_bp.route("/doctors")
def doctors():

    # Get search/filter values from URL
    search = request.args.get("search", "").strip()
    department_id = request.args.get("department_id", "").strip()

    # Base query
    sql = """
        SELECT
            d.DoctorID,
            d.LicenseNo,
            d.DoctorName,
            d.Specialization,
            d.PhoneNumber,
            d.DepartmentID,
            dep.DepartmentName
        FROM doctor d

        LEFT JOIN department dep
            ON d.DepartmentID = dep.DepartmentID

        WHERE 1 = 1
    """

    params = {}


    # Search by doctor name, license or specialization
    if search:

        sql += """
            AND (
                d.DoctorName LIKE :search
                OR d.LicenseNo LIKE :search
                OR d.Specialization LIKE :search
            )
        """

        params["search"] = f"%{search}%"


    # Filter by department
    if department_id:

        sql += """
            AND d.DepartmentID = :department_id
        """

        params["department_id"] = department_id


    sql += """
        ORDER BY d.DoctorID
    """


    doctor_list = db.session.execute(
        text(sql),
        params
    ).mappings().all()


    # Get departments for filter dropdown
    departments = db.session.execute(
        text("""
            SELECT
                DepartmentID,
                DepartmentName
            FROM department
            ORDER BY DepartmentName
        """)
    ).mappings().all()


    return render_template(
        "doctors.html",
        doctors=doctor_list,
        departments=departments,
        search=search,
        selected_department=department_id
    )


@main_bp.route("/doctors/add", methods=["GET", "POST"])
@roles_required("administrator")
def add_doctor():

    # Get departments for dropdown
    departments = db.session.execute(
        text("""
            SELECT
                DepartmentID,
                DepartmentName
            FROM department
            ORDER BY DepartmentName
        """)
    ).mappings().all()


    if request.method == "POST":

        license_no = request.form.get(
            "license_no", ""
        ).strip()

        doctor_name = request.form.get(
            "doctor_name", ""
        ).strip()

        specialization = request.form.get(
            "specialization", ""
        ).strip()

        phone_number = request.form.get(
            "phone_number", ""
        ).strip()

        department_id = request.form.get(
            "department_id", ""
        ).strip()


        # -------------------------
        # Required field validation
        # -------------------------

        if not license_no or not doctor_name or not specialization:

            flash(
                "License number, doctor name and specialization are required.",
                "danger"
            )

            return render_template(
                "add_doctor.html",
                departments=departments
            )


        # -------------------------
        # Duplicate License Check
        # -------------------------

        existing_doctor = db.session.execute(
            text("""
                SELECT DoctorID
                FROM doctor
                WHERE LicenseNo = :license_no
            """),
            {
                "license_no": license_no
            }
        ).first()


        if existing_doctor:

            flash(
                "A doctor with this license number already exists.",
                "danger"
            )

            return render_template(
                "add_doctor.html",
                departments=departments
            )


        # -------------------------
        # Insert Doctor
        # -------------------------

        db.session.execute(
            text("""
                INSERT INTO doctor
                (
                    LicenseNo,
                    DoctorName,
                    Specialization,
                    PhoneNumber,
                    DepartmentID
                )

                VALUES
                (
                    :license_no,
                    :doctor_name,
                    :specialization,
                    :phone_number,
                    :department_id
                )
            """),
            {
                "license_no": license_no,
                "doctor_name": doctor_name,
                "specialization": specialization,
                "phone_number": phone_number or None,
                "department_id": department_id or None
            }
        )

        db.session.commit()


        flash(
            f"{doctor_name} was added successfully.",
            "success"
        )


        return redirect(
            url_for("main.doctors")
        )


    return render_template(
        "add_doctor.html",
        departments=departments
    )


@main_bp.route("/doctors/<int:doctor_id>")
def doctor_details(doctor_id):

    # -------------------------
    # Doctor Information
    # -------------------------

    doctor = db.session.execute(
        text("""
            SELECT
                d.DoctorID,
                d.LicenseNo,
                d.DoctorName,
                d.Specialization,
                d.PhoneNumber,
                d.DepartmentID,
                dep.DepartmentName,
                dep.Location AS DepartmentLocation,
                dep.ContactExtension
            FROM doctor d

            LEFT JOIN department dep
                ON d.DepartmentID = dep.DepartmentID

            WHERE d.DoctorID = :doctor_id
        """),
        {
            "doctor_id": doctor_id
        }
    ).mappings().first()


    # Doctor does not exist
    if doctor is None:
        abort(404)


    # -------------------------
    # Doctor Admission History
    # -------------------------

    admissions = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionType,
                a.AdmissionStatus,
                a.RoomNumber,

                p.PatientID,
                p.PatientName,

                dis.DiseaseName

            FROM admission a

            JOIN patient p
                ON a.PatientID = p.PatientID

            LEFT JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID

            WHERE a.DoctorID = :doctor_id

            ORDER BY
                a.AdmissionDate DESC,
                a.AdmissionID DESC
        """),
        {
            "doctor_id": doctor_id
        }
    ).mappings().all()


    return render_template(
        "doctor_details.html",
        doctor=doctor,
        admissions=admissions
    )


@main_bp.route(
    "/doctors/<int:doctor_id>/edit",
    methods=["GET", "POST"]
)
@roles_required("administrator")
def edit_doctor(doctor_id):

    # Get existing doctor
    doctor = db.session.execute(
        text("""
            SELECT
                DoctorID,
                LicenseNo,
                DoctorName,
                Specialization,
                PhoneNumber,
                DepartmentID
            FROM doctor
            WHERE DoctorID = :doctor_id
        """),
        {
            "doctor_id": doctor_id
        }
    ).mappings().first()


    if doctor is None:
        abort(404)


    # Get departments for dropdown
    departments = db.session.execute(
        text("""
            SELECT
                DepartmentID,
                DepartmentName
            FROM department
            ORDER BY DepartmentName
        """)
    ).mappings().all()


    if request.method == "POST":

        doctor_name = request.form.get(
            "doctor_name", ""
        ).strip()

        license_no = request.form.get(
            "license_no", ""
        ).strip()

        specialization = request.form.get(
            "specialization", ""
        ).strip()

        phone_number = request.form.get(
            "phone_number", ""
        ).strip()

        department_id = request.form.get(
            "department_id", ""
        ).strip()


        # Required fields
        if not doctor_name or not license_no or not specialization:

            flash(
                "Doctor name, license number and specialization are required.",
                "danger"
            )

            return render_template(
                "edit_doctor.html",
                doctor=doctor,
                departments=departments
            )


        # Check duplicate license
        duplicate = db.session.execute(
            text("""
                SELECT DoctorID
                FROM doctor
                WHERE LicenseNo = :license_no
                AND DoctorID != :doctor_id
            """),
            {
                "license_no": license_no,
                "doctor_id": doctor_id
            }
        ).first()


        if duplicate:

            flash(
                "Another doctor already uses this license number.",
                "danger"
            )

            return render_template(
                "edit_doctor.html",
                doctor=doctor,
                departments=departments
            )


        # Update doctor
        db.session.execute(
            text("""
                UPDATE doctor

                SET
                    LicenseNo = :license_no,
                    DoctorName = :doctor_name,
                    Specialization = :specialization,
                    PhoneNumber = :phone_number,
                    DepartmentID = :department_id

                WHERE DoctorID = :doctor_id
            """),
            {
                "license_no": license_no,
                "doctor_name": doctor_name,
                "specialization": specialization,
                "phone_number": phone_number or None,
                "department_id": department_id or None,
                "doctor_id": doctor_id
            }
        )

        db.session.commit()


        flash(
            f"{doctor_name} was updated successfully.",
            "success"
        )


        return redirect(
            url_for(
                "main.doctor_details",
                doctor_id=doctor_id
            )
        )


    return render_template(
        "edit_doctor.html",
        doctor=doctor,
        departments=departments
    )

@main_bp.route(
    "/doctors/<int:doctor_id>/delete",
    methods=["POST"]
)
@roles_required("administrator")
def delete_doctor(doctor_id):

    # Get doctor
    doctor = db.session.execute(
        text("""
            SELECT
                DoctorID,
                DoctorName
            FROM doctor
            WHERE DoctorID = :doctor_id
        """),
        {
            "doctor_id": doctor_id
        }
    ).mappings().first()

    if doctor is None:
        abort(404)


    # Check whether doctor has admission records
    admission_count = db.session.execute(
        text("""
            SELECT COUNT(*)
            FROM admission
            WHERE DoctorID = :doctor_id
        """),
        {
            "doctor_id": doctor_id
        }
    ).scalar()


    # Block deletion if admission history exists
    if admission_count > 0:

        flash(
            f"Cannot delete {doctor.DoctorName}. "
            f"This doctor is linked to "
            f"{admission_count} admission record(s).",
            "danger"
        )

        return redirect(
            url_for("main.doctors")
        )


    # Delete doctor
    db.session.execute(
        text("""
            DELETE FROM doctor
            WHERE DoctorID = :doctor_id
        """),
        {
            "doctor_id": doctor_id
        }
    )

    db.session.commit()


    flash(
        f"{doctor.DoctorName} was deleted successfully.",
        "success"
    )


    return redirect(
        url_for("main.doctors")
    )


@main_bp.route("/admissions")
def admissions():

    admission_list = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionType,
                a.AdmissionStatus,
                a.RoomNumber,

                p.PatientID,
                p.PatientName,

                d.DoctorID,
                d.DoctorName,

                dep.DepartmentID,
                dep.DepartmentName,

                dis.DiseaseID,
                dis.DiseaseName

            FROM admission a

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID

            ORDER BY
                a.AdmissionDate DESC,
                a.AdmissionID DESC
        """)
    ).mappings().all()


    # Dashboard statistics
    total_admissions = len(admission_list)

    active_admissions = sum(
        1
        for admission in admission_list
        if admission.AdmissionStatus == "Active"
    )

    discharged_admissions = sum(
        1
        for admission in admission_list
        if admission.AdmissionStatus == "Discharged"
    )


    return render_template(
        "admissions.html",
        admissions=admission_list,
        total_admissions=total_admissions,
        active_admissions=active_admissions,
        discharged_admissions=discharged_admissions
    )

@main_bp.route("/admissions/add", methods=["GET", "POST"])
@roles_required("administrator", "receptionist")
def add_admission():

    # -------------------------
    # Dropdown Data
    # -------------------------

    patients = db.session.execute(
        text("""
            SELECT
                PatientID,
                PatientName,
                CNIC
            FROM patient
            ORDER BY PatientName
        """)
    ).mappings().all()


    doctors = db.session.execute(
        text("""
            SELECT
                d.DoctorID,
                d.DoctorName,
                d.Specialization,
                d.DepartmentID,
                dep.DepartmentName
            FROM doctor d
            LEFT JOIN department dep
                ON d.DepartmentID = dep.DepartmentID
            ORDER BY d.DoctorName
        """)
    ).mappings().all()


    departments = db.session.execute(
        text("""
            SELECT
                DepartmentID,
                DepartmentName
            FROM department
            ORDER BY DepartmentName
        """)
    ).mappings().all()


    diseases = db.session.execute(
        text("""
            SELECT
                DiseaseID,
                DiseaseName,
                DiseaseCategory
            FROM disease
            ORDER BY DiseaseName
        """)
    ).mappings().all()


    # -------------------------
    # Handle Form Submission
    # -------------------------

    if request.method == "POST":

        patient_id = request.form.get("patient_id", "").strip()
        doctor_id = request.form.get("doctor_id", "").strip()
        department_id = request.form.get("department_id", "").strip()
        disease_id = request.form.get("disease_id", "").strip()

        admission_date = request.form.get(
            "admission_date", ""
        ).strip()

        admission_type = request.form.get(
            "admission_type", ""
        ).strip()

        room_number = request.form.get(
            "room_number", ""
        ).strip()


        # -------------------------
        # Required Validation
        # -------------------------

        if (
            not patient_id
            or not doctor_id
            or not department_id
            or not disease_id
            or not admission_date
            or not admission_type
        ):

            flash(
                "Please complete all required admission fields.",
                "danger"
            )

            return render_template(
                "add_admission.html",
                patients=patients,
                doctors=doctors,
                departments=departments,
                diseases=diseases
            )


        # -------------------------
        # Validate Doctor Department
        # -------------------------

        doctor = db.session.execute(
            text("""
                SELECT
                    DoctorID,
                    DoctorName,
                    DepartmentID
                FROM doctor
                WHERE DoctorID = :doctor_id
            """),
            {
                "doctor_id": doctor_id
            }
        ).mappings().first()


        if doctor is None:

            flash(
                "The selected doctor does not exist.",
                "danger"
            )

            return render_template(
                "add_admission.html",
                patients=patients,
                doctors=doctors,
                departments=departments,
                diseases=diseases
            )


        if str(doctor.DepartmentID) != str(department_id):

            flash(
                "The selected doctor does not belong to "
                "the selected department.",
                "danger"
            )

            return render_template(
                "add_admission.html",
                patients=patients,
                doctors=doctors,
                departments=departments,
                diseases=diseases
            )


        # -------------------------
        # Insert Admission
        # -------------------------

        db.session.execute(
            text("""
                INSERT INTO admission
                (
                    PatientID,
                    DoctorID,
                    DepartmentID,
                    DiseaseID,
                    AdmissionDate,
                    DischargeDate,
                    AdmissionType,
                    AdmissionStatus,
                    RoomNumber
                )

                VALUES
                (
                    :patient_id,
                    :doctor_id,
                    :department_id,
                    :disease_id,
                    :admission_date,
                    NULL,
                    :admission_type,
                    'Active',
                    :room_number
                )
            """),
            {
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "department_id": department_id,
                "disease_id": disease_id,
                "admission_date": admission_date,
                "admission_type": admission_type,
                "room_number": room_number or None
            }
        )

        db.session.commit()


        flash(
            "Patient admission created successfully.",
            "success"
        )


        return redirect(
            url_for("main.admissions")
        )


    return render_template(
        "add_admission.html",
        patients=patients,
        doctors=doctors,
        departments=departments,
        diseases=diseases
    )


@main_bp.route("/admissions/<int:admission_id>")

def admission_details(admission_id):

    # ---------------------------------
    # Main Admission Information
    # ---------------------------------

    admission = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionType,
                a.AdmissionStatus,
                a.RoomNumber,

                p.PatientID,
                p.PatientName,
                p.CNIC,
                p.Gender,
                p.PhoneNumber,
                p.BloodGroup,

                d.DoctorID,
                d.DoctorName,
                d.LicenseNo,
                d.Specialization,

                dep.DepartmentID,
                dep.DepartmentName,
                dep.Location AS DepartmentLocation,

                dis.DiseaseID,
                dis.DiseaseName,
                dis.DiseaseCategory,
                dis.Description AS DiseaseDescription

            FROM admission a

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID

            WHERE a.AdmissionID = :admission_id
        """),
        {"admission_id": admission_id}
    ).mappings().first()

    if admission is None:
        abort(404)

    # ---------------------------------
    # Role-Based Financial Permissions
    # ---------------------------------

    can_view_financials = current_user.role in (
        "administrator",
        "receptionist"
    )

    # ---------------------------------
    # Treatment History
    # ---------------------------------

    # Doctors receive clinical treatment details
    # without the TreatmentCost field.

    if can_view_financials:

        treatment_query = """
            SELECT
                TreatmentID,
                TreatmentDate,
                TreatmentType,
                MedicineDetails,
                TreatmentOutcome,
                TreatmentCost
            FROM treatment
            WHERE AdmissionID = :admission_id
            ORDER BY TreatmentDate DESC, TreatmentID DESC
        """

    else:

        treatment_query = """
            SELECT
                TreatmentID,
                TreatmentDate,
                TreatmentType,
                MedicineDetails,
                TreatmentOutcome
            FROM treatment
            WHERE AdmissionID = :admission_id
            ORDER BY TreatmentDate DESC, TreatmentID DESC
        """

    treatments = db.session.execute(
        text(treatment_query),
        {"admission_id": admission_id}
    ).mappings().all()

    # ---------------------------------
    # Default Financial Values
    # ---------------------------------

    # These values are safe placeholders.
    # No financial database query runs
    # for the doctor role.

    payments = []
    total_treatment_cost = 0
    total_billed = 0
    total_paid = 0
    outstanding_balance = 0

    # ---------------------------------
    # Financial Data: Authorized Roles
    # ---------------------------------

    if can_view_financials:

        payments = db.session.execute(
            text("""
                SELECT
                    PaymentID,
                    PaymentDate,
                    TotalAmount,
                    AmountPaid,
                    PaymentMethod,
                    PaymentStatus
                FROM payment
                WHERE AdmissionID = :admission_id
                ORDER BY PaymentDate DESC, PaymentID DESC
            """),
            {"admission_id": admission_id}
        ).mappings().all()

        # Total treatment costs

        total_treatment_cost = sum(
            treatment.TreatmentCost or 0
            for treatment in treatments
        )

        # Total billed amount

        total_billed = sum(
            payment.TotalAmount or 0
            for payment in payments
        )

        # Total payments collected

        total_paid = sum(
            payment.AmountPaid or 0
            for payment in payments
        )

        # Remaining balance

        outstanding_balance = (
            total_billed - total_paid
        )

    # ---------------------------------
    # Render Admission Details
    # ---------------------------------

    return render_template(
        "admission_details.html",
        admission=admission,
        treatments=treatments,
        payments=payments,
        total_treatment_cost=total_treatment_cost,
        total_billed=total_billed,
        total_paid=total_paid,
        outstanding_balance=outstanding_balance
    )


@main_bp.route(
    "/admissions/<int:admission_id>/discharge",
    methods=["GET", "POST"]
)
@roles_required("administrator", "doctor")
def discharge_admission(admission_id):

    # ---------------------------------
    # Get Admission Information
    # ---------------------------------

    admission = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionStatus,
                a.RoomNumber,
                p.PatientName,
                d.DoctorName,
                dep.DepartmentName
            FROM admission a

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            WHERE a.AdmissionID = :admission_id
        """),
        {
            "admission_id": admission_id
        }
    ).mappings().first()


    # Admission does not exist
    if admission is None:
        abort(404)


    # ---------------------------------
    # Get Outstanding Balance
    # ---------------------------------

    payment_summary = db.session.execute(
        text("""
            SELECT
                COALESCE(SUM(TotalAmount), 0) AS TotalBilled,
                COALESCE(SUM(AmountPaid), 0) AS TotalPaid
            FROM payment
            WHERE AdmissionID = :admission_id
        """),
        {
            "admission_id": admission_id
        }
    ).mappings().first()


    total_billed = payment_summary.TotalBilled
    total_paid = payment_summary.TotalPaid

    outstanding_balance = total_billed - total_paid


    # ---------------------------------
    # Already Discharged Protection
    # ---------------------------------

    if admission.AdmissionStatus == "Discharged":

        flash(
            "This patient has already been discharged.",
            "warning"
        )

        return redirect(
            url_for(
                "main.admission_details",
                admission_id=admission_id
            )
        )


    # ---------------------------------
    # Process Discharge
    # ---------------------------------

    if request.method == "POST":

        discharge_date = request.form.get(
            "discharge_date",
            ""
        ).strip()


        # Date required
        if not discharge_date:

            flash(
                "Please select a discharge date.",
                "danger"
            )

            return render_template(
                "discharge_admission.html",
                admission=admission,
                total_billed=total_billed,
                total_paid=total_paid,
                outstanding_balance=outstanding_balance
            )


        # ---------------------------------
        # Validate Date
        # ---------------------------------

        admission_date_string = str(
            admission.AdmissionDate
        )

        if discharge_date < admission_date_string:

            flash(
                "Discharge date cannot be earlier "
                "than the admission date.",
                "danger"
            )

            return render_template(
                "discharge_admission.html",
                admission=admission,
                total_billed=total_billed,
                total_paid=total_paid,
                outstanding_balance=outstanding_balance
            )


        # ---------------------------------
        # Update Admission
        # ---------------------------------

        db.session.execute(
            text("""
                UPDATE admission

                SET
                    AdmissionStatus = 'Discharged',
                    DischargeDate = :discharge_date

                WHERE AdmissionID = :admission_id
                  AND AdmissionStatus = 'Active'
            """),
            {
                "discharge_date": discharge_date,
                "admission_id": admission_id
            }
        )


        db.session.commit()


        flash(
            "Patient discharged successfully.",
            "success"
        )


        return redirect(
            url_for(
                "main.admission_details",
                admission_id=admission_id
            )
        )


    # ---------------------------------
    # Display Discharge Page
    # ---------------------------------

    return render_template(
        "discharge_admission.html",
        admission=admission,
        total_billed=total_billed,
        total_paid=total_paid,
        outstanding_balance=outstanding_balance
    )

@main_bp.route(
    "/admissions/<int:admission_id>/treatments/add",
    methods=["GET", "POST"]
)

@roles_required("administrator", "doctor")
def add_treatment(admission_id):

    # ---------------------------------
    # Get Admission
    # ---------------------------------

    admission = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionStatus,
                a.RoomNumber,

                p.PatientName,

                d.DoctorName,

                dep.DepartmentName,

                dis.DiseaseName

            FROM admission a

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID

            WHERE a.AdmissionID = :admission_id
        """),
        {
            "admission_id": admission_id
        }
    ).mappings().first()


    # Admission not found
    if admission is None:
        abort(404)


    # ---------------------------------
    # Prevent Treatment After Discharge
    # ---------------------------------

    if admission.AdmissionStatus == "Discharged":

        flash(
            "Treatment cannot be added to a discharged admission.",
            "warning"
        )

        return redirect(
            url_for(
                "main.admission_details",
                admission_id=admission_id
            )
        )


    # ---------------------------------
    # Process Form
    # ---------------------------------

    if request.method == "POST":

        treatment_date = request.form.get(
            "treatment_date", ""
        ).strip()

        treatment_type = request.form.get(
            "treatment_type", ""
        ).strip()

        medicine_details = request.form.get(
            "medicine_details", ""
        ).strip()

        treatment_outcome = request.form.get(
            "treatment_outcome", ""
        ).strip()

        treatment_cost = request.form.get(
            "treatment_cost", ""
        ).strip()


        # ---------------------------------
        # Required Fields
        # ---------------------------------

        if (
            not treatment_date
            or not treatment_type
            or not treatment_cost
        ):

            flash(
                "Please complete all required treatment fields.",
                "danger"
            )

            return render_template(
                "add_treatment.html",
                admission=admission
            )


        # ---------------------------------
        # Validate Treatment Date
        # ---------------------------------

        if treatment_date < str(admission.AdmissionDate):

            flash(
                "Treatment date cannot be earlier than "
                "the admission date.",
                "danger"
            )

            return render_template(
                "add_treatment.html",
                admission=admission
            )


        # ---------------------------------
        # Validate Treatment Cost
        # ---------------------------------

        try:

            treatment_cost_value = Decimal(
                treatment_cost
            )

            if treatment_cost_value < Decimal("0"):
                raise InvalidOperation

        except (InvalidOperation, ValueError):

            flash(
                "Treatment cost must be a valid non-negative amount.",
                "danger"
            )

            return render_template(
                "add_treatment.html",
                admission=admission
            )


        # ---------------------------------
        # Insert Treatment
        # ---------------------------------

        db.session.execute(
            text("""
                INSERT INTO treatment
                (
                    AdmissionID,
                    TreatmentDate,
                    TreatmentType,
                    MedicineDetails,
                    TreatmentOutcome,
                    TreatmentCost
                )

                VALUES
                (
                    :admission_id,
                    :treatment_date,
                    :treatment_type,
                    :medicine_details,
                    :treatment_outcome,
                    :treatment_cost
                )
            """),
            {
                "admission_id": admission_id,
                "treatment_date": treatment_date,
                "treatment_type": treatment_type,
                "medicine_details": medicine_details or None,
                "treatment_outcome": treatment_outcome or None,
                "treatment_cost": treatment_cost_value
            }
        )


        # ---------------------------------
        # Save Changes
        # ---------------------------------

        db.session.commit()


        # ---------------------------------
        # Success Message
        # ---------------------------------

        flash(
            "Treatment added successfully.",
            "success"
        )


        return redirect(
            url_for(
                "main.admission_details",
                admission_id=admission_id
            )
        )

    # ---------------------------------
    # Display Form
    # ---------------------------------

    return render_template(
        "add_treatment.html",
        admission=admission
    )


@main_bp.route(
    "/admissions/<int:admission_id>/payments/add",
    methods=["GET", "POST"]
)
@roles_required("administrator", "receptionist")
def add_payment(admission_id):

    # ---------------------------------
    # Get Admission
    # ---------------------------------

    admission = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionStatus,
                a.RoomNumber,

                p.PatientName,
                p.CNIC,

                d.DoctorName,

                dep.DepartmentName,

                dis.DiseaseName

            FROM admission a

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID

            WHERE a.AdmissionID = :admission_id
        """),
        {
            "admission_id": admission_id
        }
    ).mappings().first()


    if admission is None:
        abort(404)


    # ---------------------------------
    # Treatment Cost
    # ---------------------------------

    treatment_total = db.session.execute(
        text("""
            SELECT
                COALESCE(SUM(TreatmentCost), 0)
            FROM treatment
            WHERE AdmissionID = :admission_id
        """),
        {
            "admission_id": admission_id
        }
    ).scalar()


    # ---------------------------------
    # Existing Payment Information
    # ---------------------------------

    existing_payments = db.session.execute(
        text("""
            SELECT
                COALESCE(SUM(TotalAmount), 0) AS TotalBilled,
                COALESCE(SUM(AmountPaid), 0) AS TotalPaid
            FROM payment
            WHERE AdmissionID = :admission_id
        """),
        {
            "admission_id": admission_id
        }
    ).mappings().first()


    # ---------------------------------
    # Process Form
    # ---------------------------------

    if request.method == "POST":

        payment_date = request.form.get(
            "payment_date", ""
        ).strip()

        total_amount = request.form.get(
            "total_amount", ""
        ).strip()

        amount_paid = request.form.get(
            "amount_paid", ""
        ).strip()

        payment_method = request.form.get(
            "payment_method", ""
        ).strip()


        # Required fields
        if (
            not payment_date
            or not total_amount
            or amount_paid == ""
        ):

            flash(
                "Please complete all required payment fields.",
                "danger"
            )

            return render_template(
                "add_payment.html",
                admission=admission,
                treatment_total=treatment_total,
                existing_payments=existing_payments
            )



        # ---------------------------------
        # Validate Payment Date
        # ---------------------------------

        try:
            validated_payment_date = validate_payment_date(
                payment_date,
                admission.AdmissionDate
            )

            payment_date = validated_payment_date.isoformat()

        except ValueError as error:

            flash(str(error), "danger")

            return render_template(
                "add_payment.html",
                admission=admission,
                treatment_total=treatment_total,
                existing_payments=existing_payments
            )



        # ---------------------------------
        # Validate Amounts
        # ---------------------------------

        try:
            total_amount_value = validate_money_amount(total_amount)
            amount_paid_value = validate_money_amount(
                amount_paid, allow_zero=True
            )
            if amount_paid_value > total_amount_value:
                raise ValueError(
                    "Amount paid cannot exceed the total amount."
                )
        except ValueError as error:
            flash(str(error), "danger")
            return render_template(
                "add_payment.html",
                admission=admission,
                treatment_total=treatment_total,
                existing_payments=existing_payments
            )

        # ---------------------------------
        # Automatic Payment Status
        # ---------------------------------

        if amount_paid_value == 0:

            payment_status = "Pending"

        elif amount_paid_value < total_amount_value:

            payment_status = "Partial"

        else:

            payment_status = "Paid"


        # ---------------------------------
        # Save Bill and Initial Transaction Together
        # ---------------------------------

        if amount_paid_value > 0 and not payment_method:
            flash("Select a payment method for the amount received.", "danger")
            return render_template(
                "add_payment.html",
                admission=admission,
                treatment_total=treatment_total,
                existing_payments=existing_payments
            )

        try:
            result = db.session.execute(
                text("""
                    INSERT INTO payment (
                        AdmissionID, PaymentDate, TotalAmount,
                        AmountPaid, PaymentMethod, PaymentStatus
                    ) VALUES (
                        :admission_id, :payment_date, :total_amount,
                        :amount_paid, :payment_method, :payment_status
                    )
                """),
                {
                    "admission_id": admission_id,
                    "payment_date": payment_date,
                    "total_amount": total_amount_value,
                    "amount_paid": amount_paid_value,
                    "payment_method": payment_method or None,
                    "payment_status": payment_status
                }
            )
            new_payment_id = result.lastrowid

            # New bills start with zero historical collections.
            db.session.execute(
                text("""
                    INSERT INTO payment_opening_balance
                        (PaymentID, OpeningAmount)
                    VALUES (:payment_id, 0.00)
                """),
                {"payment_id": new_payment_id}
            )

            if amount_paid_value > 0:
                db.session.execute(
                    text("""
                        INSERT INTO payment_transaction (
                            PaymentID, TransactionDate, Amount, PaymentMethod
                        ) VALUES (
                            :payment_id, :transaction_date, :amount, :method
                        )
                    """),
                    {
                        "payment_id": new_payment_id,
                        "transaction_date": payment_date,
                        "amount": amount_paid_value,
                        "method": payment_method
                    }
                )

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


        flash(
            f"Payment record added successfully. "
            f"Status: {payment_status}.",
            "success"
        )


        return redirect(
            url_for(
                "main.admission_details",
                admission_id=admission_id
            )
        )


    return render_template(
        "add_payment.html",
        admission=admission,
        treatment_total=treatment_total,
        existing_payments=existing_payments
    )


@main_bp.route(
    "/payments/<int:payment_id>/record",
    methods=["GET", "POST"]
)
@roles_required("administrator", "receptionist")
def record_payment(payment_id):
    # Fetch the record for display and admission-date validation.
    payment = db.session.execute(
        text("""
            SELECT
                pay.PaymentID,
                pay.AdmissionID,
                pay.PaymentDate,
                pay.TotalAmount,
                pay.AmountPaid,
                pay.PaymentMethod,
                pay.PaymentStatus,
                a.AdmissionDate,
                p.PatientName
            FROM payment pay
            JOIN admission a ON pay.AdmissionID = a.AdmissionID
            JOIN patient p ON a.PatientID = p.PatientID
            WHERE pay.PaymentID = :payment_id
        """),
        {"payment_id": payment_id}
    ).mappings().first()

    if payment is None:
        db.session.rollback()
        abort(404)

    total_amount = Decimal(str(payment.TotalAmount))
    amount_paid = Decimal(str(payment.AmountPaid))
    outstanding_balance = total_amount - amount_paid

    if request.method == "GET":
        db.session.rollback()
        if outstanding_balance <= Decimal("0"):
            flash("This bill has already been paid in full.", "warning")
            return redirect(url_for(
                "main.admission_details",
                admission_id=payment.AdmissionID
            ))
        return render_template(
            "record_payment.html",
            payment=payment,
            outstanding_balance=outstanding_balance
        )

    additional_amount = request.form.get("additional_amount", "").strip()
    payment_date = request.form.get("payment_date", "").strip()
    payment_method = request.form.get("payment_method", "").strip()

    if not additional_amount or not payment_date:
        db.session.rollback()
        flash("Payment date and payment amount are required.", "danger")
        return render_template(
            "record_payment.html",
            payment=payment,
            outstanding_balance=outstanding_balance
        )

    try:
        payment_date = validate_payment_date(
            payment_date, payment.AdmissionDate
        ).isoformat()
        additional_amount_value = validate_money_amount(additional_amount)
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "danger")
        return render_template(
            "record_payment.html",
            payment=payment,
            outstanding_balance=outstanding_balance
        )

    # Finish the read-only transaction before acquiring a fresh row lock.
    db.session.rollback()

    try:
        # InnoDB holds this row lock until commit or rollback.
        locked_payment = db.session.execute(
            text("""
                SELECT
                    PaymentID,
                    AdmissionID,
                    TotalAmount,
                    AmountPaid,
                    PaymentMethod
                FROM payment
                WHERE PaymentID = :payment_id
                FOR UPDATE
            """),
            {"payment_id": payment_id}
        ).mappings().first()

        if locked_payment is None:
            db.session.rollback()
            abort(404)

        current_total = Decimal(str(locked_payment.TotalAmount))
        current_paid = Decimal(str(locked_payment.AmountPaid))
        current_outstanding = current_total - current_paid

        if current_outstanding <= Decimal("0"):
            db.session.rollback()
            flash("This bill has already been paid in full.", "warning")
            return redirect(url_for(
                "main.admission_details",
                admission_id=locked_payment.AdmissionID
            ))

        if additional_amount_value > current_outstanding:
            db.session.rollback()
            flash(
                "Payment cannot exceed the current outstanding balance "
                f"of Rs. {current_outstanding:,.2f}.",
                "danger"
            )
            return redirect(url_for(
                "main.record_payment", payment_id=payment_id
            ))

        new_amount_paid = current_paid + additional_amount_value
        new_status = (
            "Paid" if new_amount_paid == current_total else "Partial"
        )
        final_payment_method = (
            payment_method or locked_payment.PaymentMethod
        )
        if not final_payment_method:
            db.session.rollback()
            flash("Select a payment method before recording payment.", "danger")
            return redirect(url_for(
                "main.record_payment", payment_id=payment_id
            ))

        db.session.execute(
            text("""
                UPDATE payment
                SET AmountPaid = :amount_paid,
                    PaymentDate = :payment_date,
                    PaymentMethod = :payment_method,
                    PaymentStatus = :payment_status
                WHERE PaymentID = :payment_id
            """),
            {
                "amount_paid": new_amount_paid,
                "payment_date": payment_date,
                "payment_method": final_payment_method,
                "payment_status": new_status,
                "payment_id": payment_id
            }
        )

        # Append an immutable installment record in the same transaction.
        db.session.execute(
            text("""
                INSERT INTO payment_transaction (
                    PaymentID, TransactionDate, Amount, PaymentMethod
                ) VALUES (
                    :payment_id, :transaction_date, :amount, :method
                )
            """),
            {
                "payment_id": payment_id,
                "transaction_date": payment_date,
                "amount": additional_amount_value,
                "method": final_payment_method
            }
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    flash(
        f"Payment of Rs. {additional_amount_value:,.2f} "
        "recorded successfully.",
        "success"
    )
    return redirect(url_for(
        "main.admission_details",
        admission_id=payment.AdmissionID
    ))


# =====================================
# PAYMENTS MANAGEMENT
# =====================================

@main_bp.route("/payments")
@roles_required("administrator", "receptionist")
def payments():

    # ---------------------------------
    # Financial Summary
    # ---------------------------------

    summary = db.session.execute(
        text("""
            SELECT
                COALESCE(SUM(TotalAmount), 0) AS TotalBilled,
                COALESCE(SUM(AmountPaid), 0) AS TotalCollected,
                COALESCE(
                    SUM(TotalAmount - AmountPaid),
                    0
                ) AS OutstandingBalance
            FROM payment
        """)
    ).mappings().first()


    # ---------------------------------
    # Payment Status Counts
    # ---------------------------------

    status_counts = db.session.execute(
        text("""
            SELECT
                SUM(
                    CASE
                        WHEN PaymentStatus = 'Paid'
                        THEN 1
                        ELSE 0
                    END
                ) AS PaidCount,

                SUM(
                    CASE
                        WHEN PaymentStatus = 'Partial'
                        THEN 1
                        ELSE 0
                    END
                ) AS PartialCount,

                SUM(
                    CASE
                        WHEN PaymentStatus = 'Pending'
                        THEN 1
                        ELSE 0
                    END
                ) AS PendingCount

            FROM payment
        """)
    ).mappings().first()


    # ---------------------------------
    # All Payment Records
    # ---------------------------------

    payment_list = db.session.execute(
        text("""
            SELECT
                pay.PaymentID,
                pay.AdmissionID,
                pay.PaymentDate,
                pay.TotalAmount,
                pay.AmountPaid,
                pay.PaymentMethod,
                pay.PaymentStatus,

                p.PatientID,
                p.PatientName,

                d.DoctorName,

                dep.DepartmentName

            FROM payment pay

            JOIN admission a
                ON pay.AdmissionID = a.AdmissionID

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            ORDER BY
                pay.PaymentDate DESC,
                pay.PaymentID DESC
        """)
    ).mappings().all()


    return render_template(
        "payments.html",
        summary=summary,
        status_counts=status_counts,
        payments=payment_list
    )



# =====================================
# DISEASE MANAGEMENT
# =====================================

@main_bp.route("/diseases")
def diseases():

    # ---------------------------------
    # Disease Records
    # ---------------------------------

    disease_list = db.session.execute(
        text("""
            SELECT
                dis.DiseaseID,
                dis.DiseaseName,
                dis.DiseaseCategory,
                dis.ICD10Code,
                dis.ICD11Code,
                dis.Description,

                COUNT(a.AdmissionID) AS AdmissionCount

            FROM disease dis

            LEFT JOIN admission a
                ON dis.DiseaseID = a.DiseaseID

            GROUP BY
                dis.DiseaseID,
                dis.DiseaseName,
                dis.DiseaseCategory,
                dis.ICD10Code,
                dis.ICD11Code,
                dis.Description

            ORDER BY
                dis.DiseaseName ASC
        """)
    ).mappings().all()


    # ---------------------------------
    # Disease Statistics
    # ---------------------------------

    disease_stats = db.session.execute(
        text("""
            SELECT
                COUNT(*) AS TotalDiseases,

                COUNT(
                    DISTINCT DiseaseCategory
                ) AS TotalCategories,

                SUM(
                    CASE
                        WHEN ICD10Code IS NOT NULL
                             AND ICD10Code <> ''
                        THEN 1
                        ELSE 0
                    END
                ) AS ICD10Count,

                SUM(
                    CASE
                        WHEN ICD11Code IS NOT NULL
                             AND ICD11Code <> ''
                        THEN 1
                        ELSE 0
                    END
                ) AS ICD11Count

            FROM disease
        """)
    ).mappings().first()


    return render_template(
        "diseases.html",
        diseases=disease_list,
        disease_stats=disease_stats
    )


# =====================================
# ADD DISEASE
# =====================================

@main_bp.route(
    "/diseases/add",
    methods=["GET", "POST"]
)
@roles_required("administrator")
def add_disease():

    if request.method == "POST":

        disease_name = request.form.get(
            "disease_name", ""
        ).strip()

        disease_category = request.form.get(
            "disease_category", ""
        ).strip()

        icd10_code = request.form.get(
            "icd10_code", ""
        ).strip()

        icd11_code = request.form.get(
            "icd11_code", ""
        ).strip()

        description = request.form.get(
            "description", ""
        ).strip()


        # Required fields
        if not disease_name or not disease_category:

            flash(
                "Disease name and category are required.",
                "danger"
            )

            return render_template(
                "add_disease.html"
            )


        # Check duplicate disease name
        existing_disease = db.session.execute(
            text("""
                SELECT DiseaseID
                FROM disease
                WHERE LOWER(DiseaseName) = LOWER(:disease_name)
                LIMIT 1
            """),
            {
                "disease_name": disease_name
            }
        ).mappings().first()


        if existing_disease:

            flash(
                "A disease with this name already exists.",
                "danger"
            )

            return render_template(
                "add_disease.html"
            )


        # Insert disease
        db.session.execute(
            text("""
                INSERT INTO disease
                (
                    DiseaseName,
                    DiseaseCategory,
                    ICD10Code,
                    ICD11Code,
                    Description
                )

                VALUES
                (
                    :disease_name,
                    :disease_category,
                    :icd10_code,
                    :icd11_code,
                    :description
                )
            """),
            {
                "disease_name": disease_name,
                "disease_category": disease_category,
                "icd10_code": icd10_code or None,
                "icd11_code": icd11_code or None,
                "description": description or None
            }
        )

        db.session.commit()

        flash(
            f"{disease_name} added successfully.",
            "success"
        )

        return redirect(
            url_for("main.diseases")
        )


    return render_template(
        "add_disease.html"
    )


# =====================================
# DISEASE DETAILS
# =====================================

@main_bp.route("/diseases/<int:disease_id>")
def disease_details(disease_id):

    # Disease information
    disease = db.session.execute(
        text("""
            SELECT
                DiseaseID,
                DiseaseName,
                DiseaseCategory,
                ICD10Code,
                ICD11Code,
                Description
            FROM disease
            WHERE DiseaseID = :disease_id
        """),
        {
            "disease_id": disease_id
        }
    ).mappings().first()


    if disease is None:
        abort(404)


    # Admissions associated with disease
    admissions = db.session.execute(
        text("""
            SELECT
                a.AdmissionID,
                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionType,
                a.AdmissionStatus,
                a.RoomNumber,

                p.PatientID,
                p.PatientName,

                d.DoctorName,

                dep.DepartmentName

            FROM admission a

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            WHERE a.DiseaseID = :disease_id

            ORDER BY
                a.AdmissionDate DESC,
                a.AdmissionID DESC
        """),
        {
            "disease_id": disease_id
        }
    ).mappings().all()


    return render_template(
        "disease_details.html",
        disease=disease,
        admissions=admissions
    )


# =====================================
# EDIT DISEASE
# =====================================

@main_bp.route(
    "/diseases/<int:disease_id>/edit",
    methods=["GET", "POST"]
)
@roles_required("administrator")
def edit_disease(disease_id):

    # ---------------------------------
    # Get Existing Disease
    # ---------------------------------

    disease = db.session.execute(
        text("""
            SELECT
                DiseaseID,
                DiseaseName,
                DiseaseCategory,
                ICD10Code,
                ICD11Code,
                Description
            FROM disease
            WHERE DiseaseID = :disease_id
        """),
        {
            "disease_id": disease_id
        }
    ).mappings().first()


    if disease is None:
        abort(404)


    # ---------------------------------
    # Process Update
    # ---------------------------------

    if request.method == "POST":

        disease_name = request.form.get(
            "disease_name", ""
        ).strip()

        disease_category = request.form.get(
            "disease_category", ""
        ).strip()

        icd10_code = request.form.get(
            "icd10_code", ""
        ).strip()

        icd11_code = request.form.get(
            "icd11_code", ""
        ).strip()

        description = request.form.get(
            "description", ""
        ).strip()


        # Required fields
        if not disease_name or not disease_category:

            flash(
                "Disease name and category are required.",
                "danger"
            )

            return render_template(
                "edit_disease.html",
                disease=disease
            )


        # ---------------------------------
        # Duplicate Name Check
        # ---------------------------------

        duplicate = db.session.execute(
            text("""
                SELECT DiseaseID
                FROM disease

                WHERE LOWER(DiseaseName) =
                      LOWER(:disease_name)

                AND DiseaseID <> :disease_id

                LIMIT 1
            """),
            {
                "disease_name": disease_name,
                "disease_id": disease_id
            }
        ).mappings().first()


        if duplicate:

            flash(
                "Another disease with this name already exists.",
                "danger"
            )

            return render_template(
                "edit_disease.html",
                disease=disease
            )


        # ---------------------------------
        # Update Disease
        # ---------------------------------

        db.session.execute(
            text("""
                UPDATE disease

                SET
                    DiseaseName = :disease_name,
                    DiseaseCategory = :disease_category,
                    ICD10Code = :icd10_code,
                    ICD11Code = :icd11_code,
                    Description = :description

                WHERE DiseaseID = :disease_id
            """),
            {
                "disease_name": disease_name,
                "disease_category": disease_category,
                "icd10_code": icd10_code or None,
                "icd11_code": icd11_code or None,
                "description": description or None,
                "disease_id": disease_id
            }
        )

        db.session.commit()


        flash(
            f"{disease_name} updated successfully.",
            "success"
        )


        return redirect(
            url_for(
                "main.disease_details",
                disease_id=disease_id
            )
        )


    return render_template(
        "edit_disease.html",
        disease=disease
    )


# =====================================
# DELETE DISEASE
# =====================================

@main_bp.route(
    "/diseases/<int:disease_id>/delete",
    methods=["POST"]
)
@roles_required("administrator")
def delete_disease(disease_id):

    # ---------------------------------
    # Get Disease
    # ---------------------------------

    disease = db.session.execute(
        text("""
            SELECT
                DiseaseID,
                DiseaseName
            FROM disease
            WHERE DiseaseID = :disease_id
        """),
        {
            "disease_id": disease_id
        }
    ).mappings().first()


    if disease is None:
        abort(404)


    # ---------------------------------
    # Check Admission History
    # ---------------------------------

    admission_count = db.session.execute(
        text("""
            SELECT COUNT(*) AS AdmissionCount
            FROM admission
            WHERE DiseaseID = :disease_id
        """),
        {
            "disease_id": disease_id
        }
    ).scalar()


    # ---------------------------------
    # Block Delete if Used
    # ---------------------------------

    if admission_count > 0:

        flash(
            f"{disease.DiseaseName} cannot be deleted because "
            f"it is linked to {admission_count} admission record(s).",
            "danger"
        )

        return redirect(
            url_for(
                "main.disease_details",
                disease_id=disease_id
            )
        )


    # ---------------------------------
    # Delete Disease
    # ---------------------------------

    db.session.execute(
        text("""
            DELETE FROM disease
            WHERE DiseaseID = :disease_id
        """),
        {
            "disease_id": disease_id
        }
    )

    db.session.commit()


    flash(
        f"{disease.DiseaseName} deleted successfully.",
        "success"
    )


    return redirect(
        url_for("main.diseases")
    )


# =====================================
# TREATMENT MANAGEMENT
# =====================================

@main_bp.route("/treatments")
def treatments():

    # ---------------------------------
    # Treatment Records
    # ---------------------------------

    treatment_list = db.session.execute(
        text("""
            SELECT
                t.TreatmentID,
                t.AdmissionID,
                t.TreatmentDate,
                t.TreatmentType,
                t.MedicineDetails,
                t.TreatmentOutcome,
                t.TreatmentCost,

                p.PatientID,
                p.PatientName,

                d.DoctorID,
                d.DoctorName,

                dis.DiseaseID,
                dis.DiseaseName,

                dep.DepartmentName,

                a.AdmissionStatus

            FROM treatment t

            JOIN admission a
                ON t.AdmissionID = a.AdmissionID

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            ORDER BY
                t.TreatmentDate DESC,
                t.TreatmentID DESC
        """)
    ).mappings().all()


    # ---------------------------------
    # Treatment Statistics
    # ---------------------------------

    treatment_stats = db.session.execute(
        text("""
            SELECT
                COUNT(*) AS TotalTreatments,

                COUNT(
                    DISTINCT a.PatientID
                ) AS PatientsTreated,

                COALESCE(
                    SUM(t.TreatmentCost),
                    0
                ) AS TotalTreatmentCost,

                SUM(
                    CASE
                        WHEN t.TreatmentOutcome = 'Under Treatment'
                        THEN 1
                        ELSE 0
                    END
                ) AS UnderTreatmentCount

            FROM treatment t

            JOIN admission a
                ON t.AdmissionID = a.AdmissionID
        """)
    ).mappings().first()


    return render_template(
        "treatments.html",
        treatments=treatment_list,
        treatment_stats=treatment_stats
    )


# =====================================
# TREATMENT DETAILS
# =====================================

@main_bp.route("/treatments/<int:treatment_id>")
def treatment_details(treatment_id):

    treatment = db.session.execute(
        text("""
            SELECT
                t.TreatmentID,
                t.AdmissionID,
                t.TreatmentDate,
                t.TreatmentType,
                t.MedicineDetails,
                t.TreatmentOutcome,
                t.TreatmentCost,

                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionStatus,

                p.PatientID,
                p.PatientName,

                d.DoctorID,
                d.DoctorName,

                dis.DiseaseID,
                dis.DiseaseName,

                dep.DepartmentID,
                dep.DepartmentName

            FROM treatment t

            JOIN admission a
                ON t.AdmissionID = a.AdmissionID

            JOIN patient p
                ON a.PatientID = p.PatientID

            JOIN doctor d
                ON a.DoctorID = d.DoctorID

            JOIN disease dis
                ON a.DiseaseID = dis.DiseaseID

            JOIN department dep
                ON a.DepartmentID = dep.DepartmentID

            WHERE t.TreatmentID = :treatment_id
        """),
        {
            "treatment_id": treatment_id
        }
    ).mappings().first()


    if treatment is None:
        abort(404)


    return render_template(
        "treatment_details.html",
        treatment=treatment
    )


# =====================================
# EDIT TREATMENT
# =====================================

@main_bp.route(
    "/treatments/<int:treatment_id>/edit",
    methods=["GET", "POST"]
)
def edit_treatment(treatment_id):

    # ---------------------------------
    # Get Treatment
    # ---------------------------------

    treatment = db.session.execute(
        text("""
            SELECT
                t.TreatmentID,
                t.AdmissionID,
                t.TreatmentDate,
                t.TreatmentType,
                t.MedicineDetails,
                t.TreatmentOutcome,
                t.TreatmentCost,

                a.AdmissionDate,
                a.DischargeDate,
                a.AdmissionStatus,

                p.PatientID,
                p.PatientName

            FROM treatment t

            JOIN admission a
                ON t.AdmissionID = a.AdmissionID

            JOIN patient p
                ON a.PatientID = p.PatientID

            WHERE t.TreatmentID = :treatment_id
        """),
        {
            "treatment_id": treatment_id
        }
    ).mappings().first()


    if treatment is None:
        abort(404)


    # ---------------------------------
    # Process Form
    # ---------------------------------

    if request.method == "POST":

        treatment_date = request.form.get(
            "treatment_date", ""
        ).strip()

        treatment_type = request.form.get(
            "treatment_type", ""
        ).strip()

        medicine_details = request.form.get(
            "medicine_details", ""
        ).strip()

        treatment_outcome = request.form.get(
            "treatment_outcome", ""
        ).strip()

        treatment_cost = request.form.get(
            "treatment_cost", ""
        ).strip()


        # ---------------------------------
        # Required Fields
        # ---------------------------------

        if (
            not treatment_date
            or not treatment_type
            or not treatment_cost
        ):

            flash(
                "Treatment date, treatment type and "
                "treatment cost are required.",
                "danger"
            )

            return render_template(
                "edit_treatment.html",
                treatment=treatment
            )


        # ---------------------------------
        # Validate Treatment Date
        # ---------------------------------

        if treatment_date < str(treatment.AdmissionDate):

            flash(
                "Treatment date cannot be earlier than "
                "the admission date.",
                "danger"
            )

            return render_template(
                "edit_treatment.html",
                treatment=treatment
            )


        if (
            treatment.DischargeDate
            and treatment_date > str(treatment.DischargeDate)
        ):

            flash(
                "Treatment date cannot be later than "
                "the discharge date.",
                "danger"
            )

            return render_template(
                "edit_treatment.html",
                treatment=treatment
            )


        # ---------------------------------
        # Validate Cost
        # ---------------------------------

        try:

            treatment_cost_value = Decimal(
                treatment_cost
            )

            if treatment_cost_value < Decimal("0"):
                raise InvalidOperation

        except (InvalidOperation, ValueError):

            flash(
                "Please enter a valid treatment cost.",
                "danger"
            )

            return render_template(
                "edit_treatment.html",
                treatment=treatment
            )


        # ---------------------------------
        # Update Treatment
        # ---------------------------------

        db.session.execute(
            text("""
                UPDATE treatment

                SET
                    TreatmentDate = :treatment_date,
                    TreatmentType = :treatment_type,
                    MedicineDetails = :medicine_details,
                    TreatmentOutcome = :treatment_outcome,
                    TreatmentCost = :treatment_cost

                WHERE TreatmentID = :treatment_id
            """),
            {
                "treatment_date": treatment_date,
                "treatment_type": treatment_type,
                "medicine_details": medicine_details or None,
                "treatment_outcome": treatment_outcome or None,
                "treatment_cost": treatment_cost_value,
                "treatment_id": treatment_id
            }
        )

        db.session.commit()


        flash(
            "Treatment updated successfully.",
            "success"
        )


        return redirect(
            url_for(
                "main.treatment_details",
                treatment_id=treatment_id
            )
        )


    return render_template(
        "edit_treatment.html",
        treatment=treatment
    )

# =====================================
# REPORTS & ANALYTICS
# =====================================

@main_bp.route("/reports")
@roles_required("administrator")
def reports():

    # ---------------------------------
    # Admissions Trend
    # ---------------------------------

    admissions_trend = db.session.execute(
        text("""
            SELECT
                DATE_FORMAT(AdmissionDate, '%Y-%m') AS Month,
                COUNT(*) AS AdmissionCount
            FROM admission
            GROUP BY
                YEAR(AdmissionDate),
                MONTH(AdmissionDate),
                DATE_FORMAT(AdmissionDate, '%Y-%m')
            ORDER BY
                YEAR(AdmissionDate),
                MONTH(AdmissionDate)
        """)
    ).mappings().all()

    # ---------------------------------
    # Disease Frequency
    # ---------------------------------

    disease_frequency = db.session.execute(
        text("""
            SELECT
                dis.DiseaseName,
                dis.DiseaseCategory,
                COUNT(a.AdmissionID) AS AdmissionCount
            FROM disease dis

            LEFT JOIN admission a
                ON dis.DiseaseID = a.DiseaseID

            GROUP BY
                dis.DiseaseID,
                dis.DiseaseName,
                dis.DiseaseCategory

            ORDER BY
                AdmissionCount DESC,
                dis.DiseaseName
        """)
    ).mappings().all()

    # ---------------------------------
    # Department Workload
    # ---------------------------------

    department_workload = db.session.execute(
        text("""
            SELECT
                dep.DepartmentName,
                COUNT(a.AdmissionID) AS TotalAdmissions,

                SUM(
                    CASE
                        WHEN a.AdmissionStatus = 'Active'
                        THEN 1
                        ELSE 0
                    END
                ) AS ActiveAdmissions,

                SUM(
                    CASE
                        WHEN a.AdmissionStatus = 'Discharged'
                        THEN 1
                        ELSE 0
                    END
                ) AS DischargedAdmissions

            FROM department dep

            LEFT JOIN admission a
                ON dep.DepartmentID = a.DepartmentID

            GROUP BY
                dep.DepartmentID,
                dep.DepartmentName

            ORDER BY
                TotalAdmissions DESC,
                dep.DepartmentName
        """)
    ).mappings().all()

    # ---------------------------------
    # Doctor Workload
    # ---------------------------------

    doctor_workload = db.session.execute(
        text("""
            SELECT
                d.DoctorName,
                d.Specialization,
                dep.DepartmentName,

                COUNT(a.AdmissionID) AS TotalAdmissions,

                SUM(
                    CASE
                        WHEN a.AdmissionStatus = 'Active'
                        THEN 1
                        ELSE 0
                    END
                ) AS ActiveAdmissions,

                SUM(
                    CASE
                        WHEN a.AdmissionStatus = 'Discharged'
                        THEN 1
                        ELSE 0
                    END
                ) AS DischargedAdmissions

            FROM doctor d

            LEFT JOIN department dep
                ON d.DepartmentID = dep.DepartmentID

            LEFT JOIN admission a
                ON d.DoctorID = a.DoctorID

            GROUP BY
                d.DoctorID,
                d.DoctorName,
                d.Specialization,
                dep.DepartmentName

            ORDER BY
                TotalAdmissions DESC,
                d.DoctorName
        """)
    ).mappings().all()

    # ---------------------------------
    # Treatment Cost Analysis
    # ---------------------------------

    treatment_cost_analysis = db.session.execute(
        text("""
            SELECT
                t.TreatmentType,

                COUNT(t.TreatmentID) AS TreatmentCount,

                COALESCE(
                    SUM(t.TreatmentCost),
                    0
                ) AS TotalTreatmentCost,

                COALESCE(
                    AVG(t.TreatmentCost),
                    0
                ) AS AverageTreatmentCost

            FROM treatment t

            GROUP BY
                t.TreatmentType

            ORDER BY
                TotalTreatmentCost DESC,
                t.TreatmentType
        """)
    ).mappings().all()

    # ---------------------------------
    # Financial Performance
    # ---------------------------------

    financial_performance = db.session.execute(
        text("""
            SELECT
                DATE_FORMAT(PaymentDate, '%Y-%m') AS Month,

                COALESCE(
                    SUM(TotalAmount),
                    0
                ) AS TotalBilled,

                COALESCE(
                    SUM(AmountPaid),
                    0
                ) AS TotalCollected,

                COALESCE(
                    SUM(TotalAmount - AmountPaid),
                    0
                ) AS OutstandingBalance

            FROM payment

            GROUP BY
                YEAR(PaymentDate),
                MONTH(PaymentDate),
                DATE_FORMAT(PaymentDate, '%Y-%m')

            ORDER BY
                YEAR(PaymentDate),
                MONTH(PaymentDate)
        """)
    ).mappings().all()

    # ---------------------------------
    # Payment Method Analysis
    # ---------------------------------

    payment_method_analysis = db.session.execute(
        text("""
            SELECT
                COALESCE(
                    PaymentMethod,
                    'Not Specified'
                ) AS PaymentMethod,

                COUNT(PaymentID) AS PaymentCount,

                COALESCE(
                    SUM(AmountPaid),
                    0
                ) AS TotalCollected

            FROM payment

            GROUP BY
                PaymentMethod

            ORDER BY
                TotalCollected DESC,
                PaymentMethod
        """)
    ).mappings().all()

    return render_template(
        "reports.html",
        admissions_trend=admissions_trend,
        disease_frequency=disease_frequency,
        department_workload=department_workload,
        doctor_workload=doctor_workload,
        treatment_cost_analysis=treatment_cost_analysis,
        financial_performance=financial_performance,
        payment_method_analysis=payment_method_analysis
    )
