from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import AcademicSession, MonthlyFee, StudentAdmission, FeeStructure

from django.db.models import OuterRef, Subquery, F
from django.db.models.functions import Coalesce

def generate_session_months(session):
    """
    Returns an ordered list of dicts covering every month of the session.
 
    Each dict: { 'month': 'May', 'month_number': 5, 'year': 2026 }
 
    Example for session 26-27 (Apr 2026 – Mar 2027):
        April 2026, May 2026, …, December 2026, January 2027, …, March 2027
    """
    import datetime
    months = []
    current = session.start_date.replace(day=1)
    end     = session.end_date.replace(day=1)
 
    while current <= end:
        months.append({
            'month':        current.strftime('%B'),
            'month_number': current.month,
            'year':         current.year,
        })
        # Advance by one month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
 
    return months
 
 
def generate_monthly_fee_months(session):
    """
    Returns the months that belong to the MonthlyFee chain.
 
    The FIRST month of the session is handled by the admission /
    promotion form (it collects one-time + first-month tuition there).
    Monthly fee records therefore start from the SECOND month.
 
    For session 26-27 (Apr 2026 start):
        May 2026, June 2026, …, March 2027   (11 records)
    """
    all_months = generate_session_months(session)
    # Skip the first month; it is the admission/promotion month.
    return all_months[1:]
 
 
# ── Core chain helpers ────────────────────────────────────────
 
def create_monthly_fees_for_student(student_admission):
    """
    Bulk-creates MonthlyFee records (May→March for a 26-27 session).
    Already-existing records are skipped (idempotent).
 
    Call this immediately after saving a new StudentAdmission, whether
    from a new admission or a promotion.
 
    The full balance chain is recalculated at the end so every record
    has correct previous_balance / total_dues / current_balance from day 1.
    """
    admission  = student_admission
    session    = admission.academic_session
 
    # Net monthly fee = tuition minus any discount on the admission
    tuition        = admission.tuition_fee or Decimal('0.00')
    discount       = admission.discount    or Decimal('0.00')
    net_monthly    = max(tuition - discount, Decimal('0.00'))
 
    transport_fee  = (
        admission.transport_fee
        if admission.transport == 'Paid'
        else Decimal('0.00')
    )
 
    months = generate_monthly_fee_months(session)
 
    # Build only the records that do not yet exist
    existing = set(
        admission.monthly_fees.values_list('month', 'year')
    )
    to_create = [
        MonthlyFee(
            student_admission = admission,
            month             = m['month'],
            month_number      = m['month_number'],
            year              = m['year'],
            monthly_fee       = net_monthly,
            transport_fee     = transport_fee,
            # Chain fields are placeholder zeros; recalculate_monthly_chain
            # fills them in correctly right below.
            previous_balance  = Decimal('0.00'),
            total_dues        = Decimal('0.00'),
            received          = Decimal('0.00'),
            current_balance   = Decimal('0.00'),
            has_payment       = False,
        )
        for m in months
        if (m['month'], m['year']) not in existing
    ]
 
    if to_create:
        MonthlyFee.objects.bulk_create(to_create)
 
    # Always recalculate the full chain after any structural change.
    recalculate_monthly_chain(student_admission)
 
 
def recalculate_monthly_chain(student_admission):
    """
    Recomputes previous_balance → total_dues → current_balance for every
    MonthlyFee of this admission in chronological order.
 
    Chain seed:
        The first month's previous_balance = student_admission.balance
        (the outstanding balance left after the admission / promotion form).
 
    After recalculation:
        student_admission.closing_balance is updated to the last month's
        current_balance so the next session's promotion form can read it.
 
    This function is the SINGLE place that touches these derived fields.
    Call it after:
        • creating MonthlyFee records (create_monthly_fees_for_student)
        • recording / editing a monthly payment
        • any retrospective change to tuition or transport fee
    """
    monthly_fees = list(
        student_admission.monthly_fees
        .order_by('year', 'month_number')
    )
 
    if not monthly_fees:
        return
 
    # Seed from the admission / promotion form balance
    running_balance = student_admission.balance or Decimal('0.00')
 
    for fee in monthly_fees:
        fee.previous_balance = running_balance
        fee.total_dues       = running_balance + fee.monthly_fee + fee.transport_fee
        fee.current_balance  = fee.total_dues - (fee.received or Decimal('0.00'))
        fee.has_payment      = (fee.received or Decimal('0.00')) > Decimal('0.00')
        running_balance      = fee.current_balance
 
    # Persist all chain fields in one database round-trip
    MonthlyFee.objects.bulk_update(
        monthly_fees,
        ['previous_balance', 'total_dues', 'current_balance', 'has_payment'],
    )
 
    # Keep closing_balance on the admission in sync (used by next-session
    # promotion to know how much this student still owes).
    last_fee = monthly_fees[-1]
    StudentAdmission.objects.filter(pk=student_admission.pk).update(
        closing_balance=last_fee.current_balance
    )
 
 
def get_pending_balance(student_admission):
    """
    Returns the balance a student carries into the next session.
    This is the current_balance of the LAST month of the session.
 
    Used by the promotion form to pre-fill previous_balance.
    """
    last_fee = (
        student_admission.monthly_fees
        .order_by('year', 'month_number')
        .last()
    )
    if last_fee:
        return last_fee.current_balance
    # Fallback: no monthly fees yet — use the raw admission balance
    return student_admission.balance or Decimal('0.00')
 
 
def get_active_session(request):
    """Convenience: returns the active AcademicSession for the school."""
    try:
        return AcademicSession.objects.get(
            school=request.user.school, is_active=True
        )
    except AcademicSession.DoesNotExist:
        return None
 
 


def validate_transport(cleaned_data, form):
    """
    Validate transport fields and clear irrelevant ones.
    Call from any form's clean() that has transport fields.
    Pass the form instance so add_error() works.
    """
    transport = cleaned_data.get('transport')

    if transport == 'Paid':
        for field in ['vehicle_no', 'route', 'driver_contact', 'transport_fee']:
            if not cleaned_data.get(field):
                form.add_error(field, 'This field is required for paid transport.')

    elif transport == 'Free':
        for field in ['vehicle_no', 'route', 'driver_contact']:
            if not cleaned_data.get(field):
                form.add_error(field, 'This field is required for free transport.')
        cleaned_data['transport_fee'] = Decimal('0.00')

    elif transport == 'No':
        cleaned_data['vehicle_no'] = ''
        cleaned_data['route'] = ''
        cleaned_data['driver_contact'] = ''
        cleaned_data['transport_fee'] = Decimal('0.00')

    return cleaned_data


# Backward-compatible alias (forms.py imports this name)
get_previous_balance = get_pending_balance

def calculate_total_dues(cleaned_data):
    fields = [
        'previous_balance', 'tuition_fee', 'exam_fee', 'book_fee',
        'uniform_fee', 'promotion_fee', 'other_fee', 'transport_fee',
    ]
    total = sum(cleaned_data.get(f) or Decimal('0.00') for f in fields)
    discount = cleaned_data.get('discount') or Decimal('0.00')
    return max(total - discount, Decimal('0.00'))




# ──────────────────────────────────────────────────────────────
# utils.py  ── ADD this function (repeat / "not promoted" flow)
# ──────────────────────────────────────────────────────────────
#
# Two ways a student enters a session:
#   1. New admission  → previous_balance = 0.00 (handled in student_admission view)
#   2. Carried over from previous session, which has TWO sub-cases:
#        a) Promoted to next class  → PromoteExistingStudentForm
#        b) Repeats the same class  → create_repeat_admission() (this function)
#
# In BOTH carry-over cases, previous_balance comes from
# get_pending_balance(old_admission) — the old session's last month's
# current_balance. The only difference between (a) and (b) is the
# target class_name; the chaining logic afterward is identical.
# ──────────────────────────────────────────────────────────────


def create_repeat_admission(old_admission, academic_session, roll_number, operator):
    """
    Creates a new StudentAdmission for a student who is NOT promoted —
    i.e. repeats the same class in the new session.

    previous_balance carries forward exactly like the promotion flow
    (via get_pending_balance), satisfying the requirement that both
    promoted and repeating students chain their balance the same way.

    Raises ValueError if no FeeStructure exists for the class in the
    target session (caller should catch and message the user).
    """
    student     = old_admission.student
    class_name  = old_admission.class_name   # SAME class — no progression
    section     = old_admission.section

    fee = FeeStructure.objects.filter(
        school           = student.school,
        academic_session = academic_session,
        class_name       = class_name,
    ).first()

    if not fee:
        raise ValueError(
            f"No fee structure found for {class_name} in "
            f"{academic_session.session_name}. Please set one up first."
        )

    # ── Carry forward balance — identical source to promotion flow ──
    previous_balance = get_pending_balance(old_admission)

    # ── Fee values for the (same) class in the new session ──────────
    tuition_fee    = fee.tuition_fee
    exam_fee       = fee.paper_money
    book_fee       = fee.books_dues
    uniform_fee    = fee.uniform_dues
    other_fee      = fee.other_charges or Decimal('0.00')
    promotion_fee  = Decimal('0.00')   # no promotion fee — student is repeating

    # ── Carry forward transport / discount settings from old record ─
    discount         = old_admission.discount or Decimal('0.00')
    discount_behalf  = old_admission.discount_behalf
    transport        = old_admission.transport
    transport_fee    = old_admission.transport_fee if transport == 'Paid' else Decimal('0.00')
    vehicle_no       = old_admission.vehicle_no
    route            = old_admission.route
    driver_contact   = old_admission.driver_contact

    received = Decimal('0.00')   # collected later via Monthly Fee page

    total_dues = max(
        previous_balance + tuition_fee + exam_fee + book_fee + uniform_fee
        + promotion_fee + other_fee + transport_fee - discount,
        Decimal('0.00'),
    )
    balance = total_dues - received

    with transaction.atomic():
        new_admission = StudentAdmission.objects.create(
            student            = student,
            academic_session   = academic_session,
            class_name         = class_name,
            section            = section,
            roll_number        = roll_number,
            admission_date     = timezone.now().date(),
            class_teacher      = old_admission.class_teacher,

            admission_fee      = Decimal('0.00'),
            tuition_fee        = tuition_fee,
            exam_fee           = exam_fee,
            book_fee           = book_fee,
            uniform_fee        = uniform_fee,
            other_fee          = other_fee,
            promotion_fee      = promotion_fee,

            discount           = discount,
            discount_behalf    = discount_behalf,

            transport          = transport,
            transport_fee      = transport_fee,
            vehicle_no         = vehicle_no,
            route              = route,
            driver_contact     = driver_contact,

            previous_balance   = previous_balance,
            total_dues         = total_dues,
            received           = received,
            balance            = balance,
            closing_balance    = balance,   # updated later by chain

            promoted_from      = old_admission,
            promoted           = False,     # NOT promoted — repeating
            status             = True,
            operator           = operator,
        )

        # Mark old admission inactive + flag as not-promoted
        StudentAdmission.objects.filter(pk=old_admission.pk).update(
            status            = False,
            failed_to_promote = True,
        )

        # Pre-generate the full monthly fee chain for the new session
        create_monthly_fees_for_student(new_admission)

    return new_admission


def annotate_outstanding(queryset):
    """
    Annotates a StudentAdmission queryset with `outstanding` = the
    current_balance of the student's LATEST MonthlyFee record, falling
    back to admission.balance if no monthly fees exist yet.

    Use this instead of Sum('current_balance') across MonthlyFee rows,
    which double-counts since current_balance is a running total that
    already includes every prior month's carried-forward balance.
    """


    latest_fee_balance = (
        MonthlyFee.objects
        .filter(student_admission=OuterRef('pk'))
        .order_by('-year', '-month_number')
        .values('current_balance')[:1]
    )
    return queryset.annotate(
        outstanding=Coalesce(Subquery(latest_fee_balance), F('balance'))
    )