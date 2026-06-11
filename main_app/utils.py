from decimal import Decimal
from .models import AcademicSession, MonthlyFee, StudentAdmission
from django.db import models

def get_previous_balance(old_admission, active_session):
    """
    Finds the final month ledger entry (e.g., March) of the previous session
    and returns its 'current_balance' to populate the promotion form.
    """
    if not old_admission:
        return Decimal('0.00')

    # 1. Identify the previous session tied to this old admission record
    previous_session = old_admission.academic_session

    if not previous_session:
        # Fallback to model base if session is detached
        return getattr(old_admission, 'balance', Decimal('0.00'))

    # 2. Extract the expected final month name and year from the session's end date
    # E.g., if end_date is 2026-03-31, this evaluates to 'March' and 2026
    last_month_name = previous_session.end_date.strftime('%B')  
    last_year_val = previous_session.end_date.year

    # 3. Target the specific last monthly fee ledger entry for this student's old session link
    last_month_fee = MonthlyFee.objects.filter(
        student_admission=old_admission,
        month=last_month_name,
        year=last_year_val
    ).first()

    if last_month_fee:
        # This represents total_dues minus what they paid in that closing month
        return last_month_fee.current_balance

    # 4. Fallback 1: If the final month's ledger doesn't exist yet, get their latest ledger in that session
    latest_fee_any_month = MonthlyFee.objects.filter(
        student_admission=old_admission
    ).order_by('-year', '-id').first()

    if latest_fee_any_month:
        return latest_fee_any_month.current_balance

    # 5. Fallback 2: General safety net using the base balance field on the old session record
    return getattr(old_admission, 'balance', Decimal('0.00'))



def get_pending_balance(student_admission):
    """Use closing_balance for promotion - This is what we finalized"""
    if not student_admission:
        return Decimal('0.00')
    
    # This is the final projected remaining balance of the entire session
    return max(student_admission.closing_balance, Decimal('0.00'))


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