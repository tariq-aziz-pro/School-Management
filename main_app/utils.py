from decimal import Decimal
from .models import AcademicSession, MonthlyFee, StudentAdmission


def get_previous_balance(student_admission, active_session):
    """
    Get the carried-over balance from the previous session for a student.
    Used by promotion views and forms.
    """
    previous_session = AcademicSession.objects.filter(
        school=student_admission.student.school,
        is_active=False,
        end_date__lt=active_session.start_date
    ).order_by('-end_date').first()

    if not previous_session:
        # No previous session — fall back to most recent fee or admission balance
        last_fee = MonthlyFee.objects.filter(
            student_admission=student_admission
        ).order_by('-year', '-id').first()
        if last_fee:
            return last_fee.current_balance
        return student_admission.balance or Decimal('0.00')

    last_month = previous_session.end_date.strftime('%B')
    last_year = previous_session.end_date.year

    # Try exact month match first
    last_fee = MonthlyFee.objects.filter(
        student_admission=student_admission,
        month=last_month,
        year=last_year
    ).order_by('-id').first()

    if last_fee:
        return last_fee.current_balance

    # Fall back to most recent fee in that year
    last_fee = MonthlyFee.objects.filter(
        student_admission=student_admission,
        year__lte=last_year
    ).order_by('-year', '-id').first()

    return last_fee.current_balance if last_fee else Decimal('0.00')


def calculate_total_dues(cleaned_data):
    """
    Calculate total dues from fee fields in cleaned_data.
    Used by admission and promotion forms.
    """
    fee_fields = [
        'previous_balance', 'tuition_fee', 'exam_fee', 'book_fee',
        'uniform_fee', 'other_fee', 'promotion_fee', 'transport_fee',
    ]
    total = sum(
        Decimal(str(cleaned_data.get(field) or '0.00'))
        for field in fee_fields
    )
    discount = Decimal(str(cleaned_data.get('discount') or '0.00'))
    return total - discount


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