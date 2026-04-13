import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import validates

from app.db.base import Base
from app.core.encryption import EncryptedString


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("organization_id", "medical_record_number", name="uq_patients_org_mrn"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    medical_record_number = Column(EncryptedString, nullable=False, index=True)
    medical_record_number_search = Column(String(64), nullable=False, index=True)
    full_name = Column(String(255), nullable=False, index=True)
    email = Column(EncryptedString, nullable=True, index=True)
    email_search = Column(String(255), nullable=True, index=True)
    phone = Column(EncryptedString, nullable=True, index=True)
    phone_search = Column(String(32), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    date_of_birth = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    @validates("medical_record_number", "email", "phone")
    def _populate_search_fields(self, key, value):
        if key == "medical_record_number":
            self.medical_record_number_search = value
        elif key == "email":
            self.email_search = value
        elif key == "phone":
            self.phone_search = value
        return value


class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = (UniqueConstraint("organization_id", "employee_number", name="uq_doctors_org_employee_number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    employee_number = Column(String(64), nullable=False, index=True)
    full_name = Column(String(255), nullable=False, index=True)
    specialty = Column(String(128), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_on_call = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (UniqueConstraint("organization_id", "appointment_number", name="uq_appointments_org_appointment_number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True, index=True)
    appointment_number = Column(EncryptedString, nullable=False, index=True)
    appointment_number_search = Column(String(64), nullable=False, index=True)
    department = Column(String(128), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    channel = Column(String(32), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="scheduled", index=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    @validates("appointment_number")
    def _populate_search_fields(self, key, value):
        self.appointment_number_search = value
        return value


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (UniqueConstraint("organization_id", "expense_number", name="uq_expenses_org_expense_number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True, index=True)
    expense_number = Column(EncryptedString, nullable=False, index=True)
    expense_number_search = Column(String(64), nullable=False, index=True)
    category = Column(String(128), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    incurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    approved_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    @validates("expense_number")
    def _populate_search_fields(self, key, value):
        self.expense_number_search = value
        return value


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("organization_id", "attendance_number", name="uq_attendance_org_attendance_number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    attendance_number = Column(EncryptedString, nullable=False, index=True)
    attendance_number_search = Column(String(64), nullable=False, index=True)
    shift_name = Column(String(128), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="present", index=True)
    anomaly_type = Column(String(64), nullable=True, index=True)
    location = Column(String(128), nullable=True, index=True)
    check_in_at = Column(DateTime(timezone=True), nullable=True, index=True)
    check_out_at = Column(DateTime(timezone=True), nullable=True, index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    @validates("attendance_number")
    def _populate_search_fields(self, key, value):
        self.attendance_number_search = value
        return value


class CommunicationMessage(Base):
    __tablename__ = "communication_messages"
    __table_args__ = (UniqueConstraint("organization_id", "message_number", name="uq_messages_org_message_number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    recipient_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    message_number = Column(EncryptedString, nullable=False, index=True)
    message_number_search = Column(String(64), nullable=False, index=True)
    channel = Column(String(32), nullable=False, index=True)
    campaign_name = Column(String(255), nullable=True, index=True)
    delivery_status = Column(String(32), nullable=False, default="queued", index=True)
    reach_status = Column(String(32), nullable=False, default="pending", index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True, index=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True, index=True)
    opened_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    @validates("message_number")
    def _populate_search_fields(self, key, value):
        self.message_number_search = value
        return value
