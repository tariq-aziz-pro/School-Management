import datetime
from decimal import Decimal
from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import (
    AcademicSession,
    Events,
    FeeStructure,
    MonthlyFee,
    Period,
    StudentAdmission,
    StudentResult,
    Timetable,
    CLASS_PROGRESSION,
)


def get_previous_balance(student_admission, active_session):
    """Get carried forward balance from the last month of the previous session."""
    previous_session = AcademicSession.objects.filter(
        school=student_admission.student.school,
        is_active=False,
        end_date__lt=active_session.start_date
    ).order_by('-end_date').first()

    if not previous_session:
        last_fee = MonthlyFee.objects.filter(
            student_admission=student_admission
        ).order_by('-year', '-id').first()
        if last_fee:
            return last_fee.current_balance
        return Decimal('0.00')

    last_month = previous_session.end_date.strftime('%B')
    last_year = previous_session.end_date.year

    last_fee = MonthlyFee.objects.filter(
        student_admission=student_admission,
        month=last_month,
        year=last_year
    ).order_by('-id').first()
    if last_fee:
        return last_fee.current_balance

    last_fee = MonthlyFee.objects.filter(
        student_admission=student_admission,
        student_admission__academic_session=previous_session
    ).order_by('-year', '-id').first()
    return last_fee.current_balance if last_fee else Decimal('0.00')


def bulk_promote_students(admission_ids, target_session, target_section, student_data_mapping, operator_user):
    """Bulk promote multiple admissions into a target academic session."""
    if not admission_ids:
        raise ValueError("No admission IDs provided for bulk promotion.")
    if not target_section:
        raise ValueError("Target section is required for bulk promotion.")

    with transaction.atomic():
        admissions = list(
            StudentAdmission.objects.select_for_update().filter(
                pk__in=admission_ids
            ).select_related('student')
        )

        if len(admissions) != len(admission_ids):
            missing = set(map(str, admission_ids)) - {str(a.pk) for a in admissions}
            raise ValueError(f"Admissions not found for IDs: {', '.join(sorted(missing))}")

        promoted_admissions = []
        for admission in admissions:
            student_key = admission.student.student_id
            overrides = student_data_mapping.get(student_key, {})
            is_passed = bool(overrides.get('is_passed', True))
            target_class = CLASS_PROGRESSION.get(admission.class_name, admission.class_name) if is_passed else admission.class_name

            roll_number = overrides.get('roll_number')
            if roll_number is not None and str(roll_number).strip() != '':
                try:
                    roll_number = int(roll_number)
                except (TypeError, ValueError):
                    raise ValueError(f"Invalid roll number for student {student_key}: {roll_number}")
            else:
                roll_number = next_available_roll(target_session, target_class, target_section)

            duplicate_exists = StudentAdmission.objects.filter(
                academic_session=target_session,
                class_name=target_class,
                section=target_section,
                roll_number=roll_number
            ).exists()
            if duplicate_exists:
                raise ValueError(
                    f"Roll number {roll_number} is already taken in {target_class} {target_section} for session {target_session.session_name}."
                )

            fee_structure = FeeStructure.objects.filter(
                academic_session=target_session,
                class_name=target_class,
                school=admission.student.school
            ).first()
            if not fee_structure:
                raise ValueError(
                    f"Fee structure not found for class {target_class} in session {target_session.session_name}."
                )

            previous_balance = get_previous_balance(admission, target_session)
            transport_fee = admission.transport_fee or Decimal('0.00')
            other_fee = fee_structure.other_charges or Decimal('0.00')
            total_dues = (
                fee_structure.tuition_fee +
                fee_structure.paper_money +
                fee_structure.books_dues +
                fee_structure.uniform_dues +
                fee_structure.promotion_fee +
                other_fee +
                transport_fee -
                (admission.discount or Decimal('0.00')) -
                (admission.admission_discount or Decimal('0.00')) +
                previous_balance
            )

            new_admission = StudentAdmission(
                student=admission.student,
                academic_session=target_session,
                class_name=target_class,
                section=target_section,
                roll_number=roll_number,
                admission_date=target_session.start_date,
                class_teacher=admission.class_teacher,
                admission_fee=Decimal('0.00'),
                tuition_fee=fee_structure.tuition_fee,
                exam_fee=fee_structure.paper_money,
                book_fee=fee_structure.books_dues,
                uniform_fee=fee_structure.uniform_dues,
                promotion_fee=fee_structure.promotion_fee,
                other_fee=other_fee,
                transport=admission.transport,
                transport_fee=transport_fee,
                vehicle_no=admission.vehicle_no,
                route=admission.route,
                driver_contact=admission.driver_contact,
                discount=admission.discount or Decimal('0.00'),
                admission_discount=admission.admission_discount or Decimal('0.00'),
                discount_behalf=admission.discount_behalf,
                total_dues=total_dues,
                received=Decimal('0.00'),
                balance=total_dues,
                status=True,
                promoted=is_passed,
                failed_to_promote=not is_passed,
                operator=operator_user
            )
            new_admission.save()

            MonthlyFee.objects.create(
                student_admission=new_admission,
                month=target_session.start_date.strftime('%B'),
                year=target_session.start_date.year,
                previous_balance=previous_balance,
                monthly_fee=fee_structure.tuition_fee,
                transport_fee=transport_fee,
                total_dues=previous_balance + fee_structure.tuition_fee + transport_fee,
                received=Decimal('0.00'),
                current_balance=previous_balance + fee_structure.tuition_fee + transport_fee,
                operator=operator_user
            )

            admission.status = False
            admission.promoted = is_passed
            admission.failed_to_promote = not is_passed
            admission.save(update_fields=['status', 'promoted', 'failed_to_promote'])

            promoted_admissions.append(new_admission)

        return promoted_admissions


def create_repeated_admission(student_admission, active_session, roll_number, operator):
    """Create a new admission for a student who is repeating the class."""
    previous_balance = get_previous_balance(student_admission, active_session)

    fee_total = (
        student_admission.tuition_fee +
        student_admission.exam_fee +
        student_admission.book_fee +
        student_admission.uniform_fee +
        (student_admission.other_fee or Decimal('0.00')) +
        student_admission.promotion_fee +
        (student_admission.transport_fee or Decimal('0.00')) -
        (student_admission.discount or Decimal('0.00'))
    )

    total_dues = previous_balance + fee_total

    return StudentAdmission(
        student=student_admission.student,
        academic_session=active_session,
        class_name=student_admission.class_name,
        roll_number=roll_number,
        section=student_admission.section,
        admission_date=datetime.date.today(),
        class_teacher=student_admission.class_teacher,
        admission_fee=Decimal('0.00'),
        tuition_fee=student_admission.tuition_fee,
        exam_fee=student_admission.exam_fee,
        book_fee=student_admission.book_fee,
        uniform_fee=student_admission.uniform_fee,
        other_fee=student_admission.other_fee or Decimal('0.00'),
        promotion_fee=student_admission.promotion_fee,
        transport=student_admission.transport,
        vehicle_no=student_admission.vehicle_no,
        route=student_admission.route,
        driver_contact=student_admission.driver_contact,
        transport_fee=student_admission.transport_fee or Decimal('0.00'),
        discount=student_admission.discount or Decimal('0.00'),
        discount_behalf=student_admission.discount_behalf,
        total_dues=total_dues,
        received=Decimal('0.00'),
        balance=total_dues,
        status=True,
        promoted=True,
        failed_to_promote=True,
        operator=operator
    )


def next_available_roll(academic_session, class_name, section):
    """Return the next available roll number (max + 1) for the given class/section in the session."""
    existing = StudentAdmission.objects.filter(
        academic_session=academic_session,
        class_name=class_name,
        section=section
    ).aggregate(max_roll=Coalesce(Max('roll_number'), 0))
    # aggregate returns dict; get max_roll
    max_roll = existing.get('max_roll') if existing else 0
    return (max_roll or 0) + 1


def calculate_overall_grade(percentage):
    if percentage >= 90:
        return 'A+'
    if percentage >= 80:
        return 'A'
    if percentage >= 70:
        return 'B'
    if percentage >= 60:
        return 'C'
    if percentage >= 50:
        return 'D'
    return 'F'


def get_teacher_class_admissions(teacher, active_session):
    """Active admissions in the teacher's assigned class/section for the given session."""
    if not active_session:
        return StudentAdmission.objects.none()
    return StudentAdmission.objects.filter(
        student__school=teacher.school,
        class_name=teacher.class_name,
        section=teacher.section,
        academic_session=active_session,
        status=True,
    ).select_related('student')


def get_teacher_managed_results(teacher, active_session=None):
    """Results the teacher may view or manage (own subjects + class/section)."""
    qs = StudentResult.objects.filter(
        student_admission__student__school=teacher.school,
        student_admission__class_name=teacher.class_name,
        student_admission__section=teacher.section,
        student_admission__status=True,
        subject__in=teacher.subjects.all(),
    ).select_related('student_admission__student', 'subject')
    if active_session:
        qs = qs.filter(student_admission__academic_session=active_session)
    return qs


def get_teacher_managed_result(teacher, result_id, active_session=None):
    """Single result row scoped to this teacher's subjects and class."""
    return get_object_or_404(
        get_teacher_managed_results(teacher, active_session=active_session),
        pk=result_id,
    )


def get_teacher_upcoming_events(school, limit=8):
    """School events for teachers on or after today."""
    return Events.objects.filter(
        school=school,
        event_for__in=['All', 'Teacher'],
        event_date__gte=timezone.localdate(),
    ).order_by('event_date')[:limit]


def get_student_upcoming_events(school, limit=8):
    return Events.objects.filter(
        school=school,
        event_for__in=['All', 'Students'],
        event_date__gte=timezone.localdate(),
    ).order_by('event_date')[:limit]


def get_class_timetable_grid(school, active_session, class_name, section):
    """Read-only timetable grid for a class/section (admin-created slots)."""
    if not school or not active_session or not class_name or not section:
        return None

    periods = list(Period.objects.filter(school=school).order_by('order'))
    if not periods:
        return {
            'class_name': class_name,
            'section': section,
            'grid': [],
            'has_entries': False,
            'period_count': 0,
        }

    slots = Timetable.objects.filter(
        school=school,
        academic_session=active_session,
        class_name=class_name,
        section=section,
    ).select_related('period', 'subject', 'teacher')

    slot_map = {slot.period_id: slot for slot in slots}

    grid = []
    has_entries = False
    for period in periods:
        slot = slot_map.get(period.id)
        if slot and (slot.subject_id or slot.teacher_id):
            has_entries = True
        grid.append({
            'period': period,
            'slot': slot,
            'subject_name': slot.subject.name if slot and slot.subject else None,
            'teacher_name': slot.teacher.name if slot and slot.teacher else None,
            'teacher_id': slot.teacher_id if slot else None,
            'is_break': period.is_break,
        })

    return {
        'class_name': class_name,
        'section': section,
        'grid': grid,
        'has_entries': has_entries,
        'period_count': len(periods),
    }


def get_teacher_timetable_assignments(teacher, active_session):
    """All timetable slots where this teacher is assigned (any class)."""
    if not teacher or not active_session:
        return []
    return list(
        Timetable.objects.filter(
            school=teacher.school,
            academic_session=active_session,
            teacher=teacher,
        )
        .select_related('period', 'subject')
        .order_by('class_name', 'section', 'period__order')
    )


def monthly_fee_status(fee):
    """Paid / Partial / Unpaid for student-facing fee cards."""
    balance = fee.current_balance or Decimal('0')
    received = fee.received or Decimal('0')
    if balance <= 0:
        return 'Paid'
    if received > 0:
        return 'Partial'
    return 'Unpaid'


def summarize_student_results(result_rows):
    """Overall marks across displayed results (list of dicts with obtained/total)."""
    if not result_rows:
        return None
    total_obtained = sum(r['obtained_marks'] for r in result_rows)
    total_marks = sum(r['total_marks'] for r in result_rows)
    if total_marks <= 0:
        return None
    percentage = (total_obtained / total_marks) * 100
    return {
        'total_obtained': total_obtained,
        'total_marks': total_marks,
        'percentage': percentage,
        'grade': calculate_overall_grade(percentage),
        'count': len(result_rows),
    }

