from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login
from django.contrib import messages

from django.utils.encoding import force_bytes, force_str
from django.db import transaction


from django.template.loader import render_to_string
from django.conf import settings
from .forms import SchoolRegisterForm, AcademicSessionForm, FeeStructureForm, StudentAdmissionForm, MonthlyFeeForm, PromoteOfflineStudentForm, PromoteExistingStudentForm, MarkNotPromotedForm, EditStudentForm, RollNumberPromptForm, SchoolSubscriptionForm, TeacherForm, StudentResultForm, SubjectForm, StudentUserForm, SyllabusForm, StaffForm, AssetsForm,ExpensesForm, TransportForm, EventsForm, AnalyticsForm

from .models import CustomUser, AcademicSession, FeeStructure, CLASS_CHOICES, SECTION_CHOICES, StudentAdmission, MonthlyFee, Student, School, SchoolSubscription, StudentResult, Subject, Teacher, TemporaryPassword, Syllabus, Announcement, Staff, Assets, Expenses, Transport, Events, CLASS_PROGRESSION
import uuid
import os
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_GET
from decimal import Decimal
from django.db import IntegrityError
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from io import BytesIO
import datetime
from django.db.models import Q, Sum, Count, DecimalField, F
from dateutil.relativedelta import relativedelta
from django.urls import reverse
from urllib.parse import urlencode
import json
from django.core.exceptions import ValidationError
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.forms import AuthenticationForm
import logging
from django.db.models.functions import Coalesce
from django.db.models import Value as V

from django.apps import apps
from plotly.offline import plot
import plotly.express as px
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
import pandas as pd
import io
from openpyxl import Workbook
from io import BytesIO, StringIO
from zipfile import ZipFile, ZIP_DEFLATED
from functools import wraps

logger = logging.getLogger(__name__)

class CustomAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username, password, **kwargs)
        if user and user.user_type == 1 and user.school and not user.school.is_active:
            if request:
                messages.error(request, "Your account is blocked by the Powerlink Team due to payment issues.")
            return None
        return user

def is_admin(user):
    return user.is_authenticated and user.user_type == 1

def is_teacher(user):
    return user.is_authenticated and user.user_type == 4

def is_student(user):
    return user.is_authenticated and user.user_type == 5

def is_developer(user):
    return user.is_authenticated and user.is_superuser

def custom_login(request):
    logger.debug(f"Login attempt: Method={request.method}, User={request.user}, POST={request.POST}")
    if request.user.is_authenticated:
        logger.info(f"User {request.user.username} already authenticated, redirecting.")
        if is_developer(request.user):
            return redirect('developer_dashboard')
        elif is_admin(request.user):
            return redirect('dashboard')
        elif is_teacher(request.user):
            return redirect('teacher_dashboard')
        elif is_student(request.user):
            return redirect('student_dashboard')
        return redirect('index')  # Fallback for unknown user types

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                logger.info(f"Login successful: User={username}, Superuser={user.is_superuser}, UserType={user.user_type}")
                if is_developer(user):
                    return redirect('developer_dashboard')
                elif is_admin(user):
                    return redirect('dashboard')
                elif is_teacher(user):
                    return redirect('teacher_dashboard')
                elif is_student(user):
                    return redirect('student_dashboard')
                else:
                    messages.error(request, "Invalid user type.")
                    logger.warning(f"Invalid user type: {user.user_type}")
                    return redirect('login')
            else:
                messages.error(request, "Invalid username or password.")
                logger.warning(f"Login failed: Username={username}")
        else:
            messages.error(request, "Invalid form data. Please check your input.")
            logger.debug(f"Form errors: {form.errors}")
    else:
        form = AuthenticationForm()
    return render(request, 'main_app/login.html', {'form': form})

# Put this near the top of views.py, with other helpers like is_admin()
def get_active_session(request):
    """
    Returns the active AcademicSession for the current user's school.
    Cached per request to avoid repeated database queries.
    """
    if not hasattr(request, '_active_session'):
        if request.user.is_authenticated and hasattr(request.user, 'school') and request.user.school:
            request._active_session = AcademicSession.objects.filter(
                school=request.user.school,
                is_active=True
            ).first()
        else:
            request._active_session = None
    return request._active_session


def admin_required(view_func):
    """Only Admin (user_type == 1) can access"""
    @login_required(login_url='login')
    @user_passes_test(is_admin, login_url='dashboard')
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def teacher_required(view_func):
    """Only Teacher (user_type == 4) can access"""
    @login_required(login_url='login')
    @user_passes_test(is_teacher, login_url='dashboard')
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def student_required(view_func):
    """Only Student (user_type == 5) can access"""
    @login_required(login_url='login')
    @user_passes_test(is_student, login_url='dashboard')
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        return view_func(request, *args, **kwargs)
    return _wrapped_view

def admin_or_teacher_required(view_func):
    """Allow both Admin (1) and Teacher (4)"""
    @login_required(login_url='login')
    @user_passes_test(lambda user: is_admin(user) or is_teacher(user), 
                      login_url='dashboard')
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        return view_func(request, *args, **kwargs)
    return _wrapped_view

def get_previous_balance(student_admission, active_session):
    """Get carried forward balance from the last month of previous session."""
    previous_session = AcademicSession.objects.filter(
        school=student_admission.student.school,
        is_active=False,
        end_date__lt=active_session.start_date
    ).order_by('-end_date').first()

    if not previous_session:
        return Decimal('0.00')

    last_month = previous_session.end_date.strftime('%B')
    last_year = previous_session.end_date.year

    # Try exact last month first
    last_fee = MonthlyFee.objects.filter(
        student_admission=student_admission,
        month=last_month,
        year=last_year
    ).order_by('-id').first()

    if not last_fee:
        # Fallback to most recent fee in previous session
        last_fee = MonthlyFee.objects.filter(
            student_admission=student_admission,
            student_admission__academic_session=previous_session
        ).order_by('-year', '-id').first()

    return last_fee.current_balance if last_fee else Decimal('0.00')


def create_repeated_admission(student_admission, active_session, roll_number, operator):
    """Create a new admission for a student who is repeating the class (Not Promoted)."""
    previous_balance = get_previous_balance(student_admission, active_session)

    # Calculate total dues
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



@admin_required
def student_user_create(request):
    
    if request.method == 'POST':
        form = StudentUserForm(request.POST, school=request.user.school)
        if form.is_valid():
            try:
                # Verify StudentAdmission exists for active session
                student_id = form.cleaned_data['student_id']
                student = Student.objects.get(student_id=student_id, school=request.user.school)
                active_session = get_active_session(request)
                if not active_session:
                    messages.error(request, "No active academic session found. Please create one.")
                    logger.error(f"No active AcademicSession for school: {request.user.school}")
                    return redirect('student_user_create')
                admission = StudentAdmission.objects.filter(student=student, academic_session=active_session).first()
                if not admission:
                    messages.error(request, f"No admission record found for {student_id} in the current session.")
                    logger.error(f"No StudentAdmission for student: {student_id}, session: {active_session.session_name}")
                    return redirect('student_user_create')
                
                user, plain_password = form.save()
                messages.success(request, f"Student account for {user.username} created successfully. Temporary password: {plain_password} — please note it down now.")
                logger.info(f"Student account created by admin {request.user.username}: Username={user.username}")
                return redirect('student_list')
            except Exception as e:
                messages.error(request, f"Error creating student account: {str(e)}")
                logger.error(f"Error in student_user_create: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")
            logger.debug(f"StudentUserForm errors: {form.errors.as_json()}")
    else:
        form = StudentUserForm(school=request.user.school)
    
    return render(request, 'main_app/student_user_create.html', {'form': form})

@admin_required
def student_list(request):

    active_session = get_active_session(request)

    # Base queryset with optimizations
    admissions = StudentAdmission.objects.filter(
        student__school=request.user.school
    ).select_related('student')   # Important: reduces queries for student data

    if active_session:
        admissions = admissions.filter(academic_session=active_session)

    # Apply filters
    if class_filter := request.GET.get('class_assigned'):
        admissions = admissions.filter(class_name=class_filter)
    if section_filter := request.GET.get('section'):
        admissions = admissions.filter(section=section_filter)
    if student_id_filter := request.GET.get('student_id'):
        admissions = admissions.filter(student__student_id__icontains=student_id_filter)

    # === BULK FETCHING TO AVOID N+2 QUERIES ===
    student_ids = list(admissions.values_list('student__student_id', flat=True))

    # Fetch all users + their temporary passwords in just 2 queries
    users = CustomUser.objects.filter(
        username__in=student_ids
    ).select_related('temporary_password')   # Use select_related, not prefetch_related

    # Create lookup dictionary
    user_map = {user.username: user for user in users}

    # Build student data with zero extra queries inside the loop
    student_data = []
    for admission in admissions:
        user = user_map.get(admission.student.student_id)
        temp_password = user.temporary_password if user and hasattr(user, 'temporary_password') else None

        student_data.append({
            'student_id': admission.student.student_id,
            'class_assigned': admission.class_name,
            'section': admission.section,
            'username': user.username if user else 'N/A',
            'password': temp_password.password if temp_password else 'Pending',
            'account_status': 'Created' if user else 'Pending',
        })

    context = {
        'student_data': student_data,
        'classes': StudentAdmission.objects.filter(student__school=request.user.school)
                        .values_list('class_name', flat=True).distinct(),
        'sections': StudentAdmission.objects.filter(student__school=request.user.school)
                        .values_list('section', flat=True).distinct(),
        'active_session': active_session,
    }

    return render(request, 'main_app/student_list.html', context)

@student_required
def student_dashboard(request):
    
    try:
        student = Student.objects.get(student_id=request.user.username)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found.")
        logger.error(f"No Student found for username: {request.user.username}")
        return render(request, 'main_app/student_dashboard.html', {'results': [], 'fees': [], 'syllabus': [], 'announcements': []})

    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active academic session found for your school.")
        logger.error(f"No active AcademicSession for school: {student.school}")
        return render(request, 'main_app/student_dashboard.html', {
            'student': student,
            'results': [],
            'fees': [],
            'syllabus': [],
            'announcements': []
        })

    admission = StudentAdmission.objects.filter(student=student, academic_session=active_session).first()
    if not admission:
        messages.error(request, "No admission record found for the current session.")
        logger.error(f"No StudentAdmission for student: {student.student_id}, session: {active_session.session_name}")
        return render(request, 'main_app/student_dashboard.html', {
            'student': student,
            'results': [],
            'fees': [],
            'syllabus': [],
            'announcements': []
        })

    # Get filter parameters
    exam_type_filter = request.GET.get('exam_type')
    subject_filter = request.GET.get('subject')

    # Results query
    results = StudentResult.objects.filter(student_admission=admission).select_related('subject', 'student_admission')
    if exam_type_filter:
        results = results.filter(exam_type=exam_type_filter)
    if subject_filter:
        results = results.filter(subject__id=subject_filter)

    class_teacher = Teacher.objects.filter(class_name=admission.class_name, section=admission.section, school=student.school).first()
    result_data = [
        {
            'subject_name': result.subject.name,
            'exam_type': result.exam_type,
            'teacher_name': class_teacher.name if class_teacher else 'N/A',
            'total_marks': result.total_marks,
            'obtained_marks': result.obtained_marks,
            'percentage': result.percentage,
            'grade': result.grade,
        } for result in results
    ]

    # Monthly Fees
    fees = [
        {
            'id': fee.id,
            'month': fee.month,
            'monthly_fee': fee.monthly_fee,
            'status': 'Paid' if fee.has_payment else 'Unpaid',
            'due_date': fee.payment_date,
            'balance': fee.current_balance,
        } for fee in MonthlyFee.objects.filter(student_admission=admission)
    ]

    # Syllabus
    # Syllabus
    syllabus = Syllabus.objects.filter(academic_session=active_session,class_name=admission.class_name).select_related('subject')
    # Announcements
    announcements = Announcement.objects.filter(school=student.school).order_by('-created_at')

    # Events
    events = Events.objects.filter(school=student.school, event_for__in=['All', 'Students']).order_by('event_date')

    # Chart data for filtered results
    chart_data = {
        'labels': [result['subject_name'] + ' (' + result['exam_type'] + ')' for result in result_data],
        'marks': [result['obtained_marks'] for result in result_data],
        'total_marks': [result['total_marks'] for result in result_data],
    }

    context = {
        'student': student,
        'admission': admission,
        'results': result_data,
        'fees': fees,
        'syllabus': syllabus,
        'announcements': announcements,
        'events': events,  # Added events to context
        'exam_types': StudentResult.objects.filter(student_admission=admission).values_list('exam_type', flat=True).distinct(),
        'subjects': Subject.objects.filter(studentresult__student_admission=admission).distinct(),
        'active_session': active_session,
        'class_teacher': class_teacher,
        'chart_data': chart_data,
        'selected_exam_type': exam_type_filter,
        'selected_subject': subject_filter,
    }
    logger.info(f"Dashboard loaded for student: {student.student_id}, session: {active_session.session_name}, admission: {admission.id}, filters: exam_type={exam_type_filter}, subject={subject_filter}")
    return render(request, 'main_app/student_dashboard.html', context)



@user_passes_test(is_developer)
def developer_dashboard(request):
    schools = School.objects.all().select_related('admin_user').prefetch_related('subscriptions')
    total_schools = schools.count()
    active_schools = schools.filter(is_active=True).count()
    total_students = sum(school.number_of_students for school in schools)
    pending_payments = SchoolSubscription.objects.filter(is_valid=False).count()
    schools_with_sub = []
    for school in schools:
        latest_sub = school.subscriptions.order_by('-payment_date').first()
        schools_with_sub.append({
            'school': school,
            'latest_sub': latest_sub,
        })
    context = {
        'schools_with_sub': schools_with_sub,
        'total_schools': total_schools,
        'active_schools': active_schools,
        'total_students': total_students,
        'pending_payments': pending_payments,
    }
    return render(request, 'main_app/developer_dashboard.html', context)

@user_passes_test(is_developer)
def toggle_school_access(request, school_id):
    # Get school object or 404 if not found
    school = get_object_or_404(School, id=school_id)

    # Toggle status
    old_status = school.is_active
    school.is_active = not school.is_active
    school.save(update_fields=['is_active'])  # Save only the changed field

    # Log change
    logger.debug(
        f"User: {request.user} toggled {school.school_name} "
        f"from {old_status} to {school.is_active}"
    )

    # Show confirmation to user
    messages.success(
        request,
        f"{school.school_name} access {'enabled' if school.is_active else 'disabled'}."
    )

    return redirect('developer_dashboard')

@user_passes_test(is_developer)
def reset_admin_password(request, school_id):
    school = get_object_or_404(School, id=school_id)
    admin = school.admin_user
    if request.method == 'POST':
        new_password = request.POST.get('new_password')
        if new_password and len(new_password) >= 8:
            admin.set_password(new_password)
            admin.save()
            messages.success(request, f"Password reset for {admin.username}.")
            return redirect('developer_dashboard')
        else:
            messages.error(request, "Password must be at least 8 characters long.")
    return render(request, 'main_app/reset_password.html', {'admin': admin, 'school': school})

@user_passes_test(is_developer)
def add_payment(request, school_id):
    school = get_object_or_404(School, id=school_id)
    if request.method == 'POST':
        form = SchoolSubscriptionForm(request.POST)
        if form.is_valid():
            subscription = form.save(commit=False)
            subscription.school = school
            subscription.is_valid = True
            subscription.save()
            messages.success(request, f"Payment added for {school.school_name}.")
            return redirect('developer_dashboard')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = SchoolSubscriptionForm()
    return render(request, 'main_app/add_payment.html', {'school': school, 'form': form})

def register_school(request):
    if request.method == 'POST':
        form = SchoolRegisterForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                user = form.save()  # ← form handles everything now
                login(request, user)
                messages.success(request, f"School '{user.school.school_name}' registered successfully.")
                logger.info(f"School registered: Username={user.username}, School={user.school.school_name}")
                return redirect('dashboard')
            except Exception as e:
                messages.error(request, f"Failed to register school: {str(e)}")
                logger.error(f"Registration error: {str(e)}")
        else:
            messages.error(request, 'Please correct the errors below.')
            logger.debug(f"Form errors: {form.errors}")
    else:
        form = SchoolRegisterForm()
    return render(request, 'main_app/register.html', {'form': form})





@admin_required
def dashboard(request):
    if request.user.is_superuser:
        return redirect('developer_dashboard')

    active_session = get_active_session(request)
    context = {
        'session_exists': AcademicSession.objects.filter(school=request.user.school).exists(),
        'active_session': active_session,
    }

    if active_session:
        base_qs = StudentAdmission.objects.filter(
            academic_session=active_session,
            student__school=request.user.school,
            status=True
        )

        # Main statistics
        stats = base_qs.aggregate(
            total_students=Count('id'),
            new_admissions=Count('id', filter=Q(promoted=False)),
            promoted_students=Count('id', filter=Q(promoted=True)),
            total_classes=Count('class_name', distinct=True),
            total_sections=Count('section', distinct=True),
        )

        # Total Balance - Separate query (this was causing the error)
        balance_result = MonthlyFee.objects.filter(
            student_admission__academic_session=active_session,
            student_admission__student__school=request.user.school,
        ).aggregate(total=Sum('current_balance'))

        total_balance = balance_result['total'] or 0

        # Search functionality
        search_results = []
        search_type = request.GET.get('search_type', request.session.get('search_type', ''))
        search_value = request.GET.get('search_value', request.session.get('search_value', '')).strip().lower()

        if request.GET.get('clear_search'):
            request.session.pop('search_type', None)
            request.session.pop('search_value', None)
            search_type = ''
            search_value = ''

        if search_type and search_value:
            queryset = base_qs
            if search_type == 'student_id':
                search_results = queryset.filter(student__student_id__iexact=search_value)
            elif search_type == 'contact':
                search_results = queryset.filter(student__contact__iexact=search_value)
            elif search_type == 'father_name':
                search_results = queryset.filter(student__father_guardian_name__iexact=search_value)

            if not search_results:
                messages.warning(request, "No results found.")

            request.session['search_type'] = search_type
            request.session['search_value'] = search_value

        context.update({
            'total_students': stats['total_students'],
            'new_admissions': stats['new_admissions'],
            'promoted_students': stats['promoted_students'],
            'total_classes': stats['total_classes'],
            'total_sections': stats['total_sections'],
            'total_balance': total_balance,          # ← Fixed
            'search_results': search_results,
            'search_type': search_type,
            'search_value': search_value,
        })

    return render(request, 'main_app/dashboard.html', context)

@admin_required
def edit_student(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school
    )
    if request.method == 'POST':
        form = EditStudentForm(request.POST, request.FILES, instance=student_admission, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Student {student_admission.student.first_name} {student_admission.student.last_name} updated successfully.")
            return redirect('dashboard')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = EditStudentForm(instance=student_admission, user=request.user)
    return render(request, 'main_app/edit_student.html', {
        'form': form,
        'student': student_admission,
    })


@admin_required
@user_passes_test(is_admin)
def create_academic_session(request):
    logger.debug(f"Create session: User={request.user}, Superuser={request.user.is_superuser}, UserType={request.user.user_type}, School={request.user.school}")
    if not request.user.school:
        logger.error("User has no associated school.")
        messages.error(request, "No school associated with your account.")
        return redirect('some_fallback_url')

    if request.method == 'POST':
        form = AcademicSessionForm(request.POST, request=request)
        if form.is_valid():
            session = form.save(commit=False)
            session.school = request.user.school  # Set school before validation
            session.save()
            logger.info(f"Session {session.session_name} created for school {session.school}")
            messages.success(request, f"Academic session {session.session_name} created successfully.")
            return redirect('add_fee_structure')

        else:
            logger.error(f"Form errors: {form.errors}")
    else:
        form = AcademicSessionForm(request=request)
    return render(request, 'main_app/create_session.html', {'form': form})


@admin_required
def search_session(request):
    user = request.user
    
    # Ensure the logged-in user has a school
    if not hasattr(user, 'school') or not user.school:
        logger.error(f"User {user} has no associated school.")
        return render(request, 'error.html', {'message': 'No school assigned to user.'})

    school = user.school
    logger.debug(f"Searching sessions for User={user}, School={school}")

    # Get all sessions for the school
    sessions = AcademicSession.objects.filter(school=school).order_by('-start_date')

    if not sessions.exists():
        logger.error(f"No sessions found for {school}.")
        return render(request, 'main_app/search_session.html', {
            'message': 'No academic sessions found for this school.'
        })

    # === Queries that run ONLY ONCE (outside the loop) ===
    teacher_salaries = Teacher.objects.filter(school=school).aggregate(
        total=Sum('salary')
    )['total'] or 0

    staff_salaries = Staff.objects.filter(school=school).aggregate(
        total=Sum('salary')
    )['total'] or 0

    session_data = []

    for session in sessions:
        start_date = session.start_date
        end_date = session.end_date

        # Combined student statistics
        student_stats = StudentAdmission.objects.filter(
            academic_session=session,
            student__school=school
        ).aggregate(
            total_students=Count('id'),
            total_classes=Count('class_name', distinct=True),
        )

        # Students by class breakdown (needed for template display)
        students_by_class = StudentAdmission.objects.filter(
            academic_session=session,
            student__school=school
        ).values('class_name').annotate(count=Count('id')).order_by('class_name')

        # Fee collected in this session
        fee_stats = MonthlyFee.objects.filter(
            student_admission__academic_session=session,
            student_admission__student__school=school
        ).aggregate(
            total_fee=Sum('received'),
            total_balance=Sum('current_balance')
        )

        # Expenses in this session
        total_expenses = Expenses.objects.filter(
            school=school,
            payment_date__range=(start_date, end_date)
        ).aggregate(total=Sum('price'))['total'] or 0

        session_data.append({
            'session': session,
            'student_count': student_stats['total_students'],
            'students_by_class': students_by_class,
            'total_fee': fee_stats['total_fee'] or 0,
            'total_balance': fee_stats['total_balance'] or 0,
            'teacher_salaries': teacher_salaries,
            'staff_salaries': staff_salaries,
            'total_expenses': total_expenses,
        })

    return render(request, 'main_app/search_session.html', {
        'session_data': session_data
    })

@admin_required
def add_fee_structure(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found. Please create an active session first.")
        return redirect('dashboard')
    if request.method == 'POST':
        form = FeeStructureForm(request.POST)
        if form.is_valid():
            class_name = form.cleaned_data['class_name']
            if FeeStructure.objects.filter(academic_session=active_session, class_name=class_name).exists():
                messages.warning(request, f"Fee structure for {class_name} already exists for this session. Please edit it instead.")
                return redirect('add_fee_structure')
            fee_structure = form.save(commit=False)
            fee_structure.academic_session = active_session
            fee_structure.save()
            messages.success(request, "Fee structure added successfully!")
            return redirect('add_fee_structure')
    else:
        form = FeeStructureForm()
    existing_fees = FeeStructure.objects.filter(academic_session=active_session)
    return render(request, 'main_app/add_fee_structure.html', {
        'form': form,
        'existing_fees': existing_fees,
        'active_session': active_session,
    })

@admin_required
def edit_fee_structure(request, fee_id):
    fee_structure = get_object_or_404(FeeStructure, id=fee_id, academic_session__school=request.user.school)
    if request.method == 'POST':
        form = FeeStructureForm(request.POST, instance=fee_structure)
        if form.is_valid():
            form.save()
            messages.success(request, f"Fee structure for {fee_structure.class_name} updated successfully!")
            return redirect('add_fee_structure')
    else:
        form = FeeStructureForm(instance=fee_structure)
    return render(request, 'main_app/edit_fee_structure.html', {
        'form': form,
        'fee_structure': fee_structure,
    })

def index(request):
    return render(request, 'main_app/index.html')

# add (or confirm) these imports at the top of views.py
# from django.db import transaction, IntegrityError
# import uuid, os
# from django.conf import settings

@admin_required
def student_admission(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found. Please create an active session first.")
        return redirect('dashboard')

    selected_class = request.GET.get('class_name', None)
    initial_data = {'class_name': selected_class} if selected_class else {}

    classes = {
        'PG': 'Play Group', 'Nursery': 'Nursery', 'KG': 'KG',
        '1': 'Class 1', '2': 'Class 2', '3': 'Class 3', '4': 'Class 4',
        '5': 'Class 5', '6': 'Class 6', '7': 'Class 7', '8': 'Class 8',
        '9': 'Class 9', '10': 'Class 10'
    }

    image_preview_url = None

    if request.method == 'POST':
        form = StudentAdmissionForm(request.POST, request.FILES, request=request)

        if form.is_valid():
            # Pull fields needed for duplicate check BEFORE saving anything
            class_name   = form.cleaned_data.get('class_name')
            section      = form.cleaned_data.get('section')
            roll_number  = form.cleaned_data.get('roll_number')

            # Duplicate check: same roll_number within same class+section in the active session
            duplicate_exists = StudentAdmission.objects.filter(
                academic_session=active_session,
                class_name=class_name,
                section=section,
                roll_number=roll_number,
            ).exists()

            if duplicate_exists:
                # Inline form error + message; nothing saved yet
                form.add_error(
                    'roll_number',
                    f"Roll Number {roll_number} already exists in {class_name} – {section} for session {active_session.session_name}."
                )
                messages.error(
                    request,
                    f"Duplicate roll number detected for {class_name} – {section}. Please choose a different Roll Number."
                )
                return render(request, 'main_app/admission_form.html', {
                    'form': form, 'classes': classes, 'selected_class': selected_class,
                    'operator_name': request.user.username, 'image_preview_url': image_preview_url,
                })

            try:
                with transaction.atomic():
                    # 1) Create Student
                    student = Student(
                        school=request.user.school,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        father_guardian_name=form.cleaned_data['father_guardian_name'],
                        contact=form.cleaned_data['contact'],
                        date_of_birth=form.cleaned_data['date_of_birth'],
                        gender=form.cleaned_data['gender'],
                        image=form.cleaned_data.get('image')
                    )
                    student.save()

                    # 2) Create Admission
                    admission = form.save(commit=False)
                    admission.student = student
                    admission.academic_session = active_session
                    admission.operator = request.user
                    admission.save()

                return redirect('admission_success', student_id=admission.student.student_id)

            except IntegrityError:
                # Safety net in case of race condition: surface a clean error
                form.add_error(
                    'roll_number',
                    f"Another admission just used Roll Number {roll_number} in {class_name} – {section}. Please choose another."
                )
                messages.error(request, "Could not save admission due to a duplicate roll number.")
                # transaction.atomic rollback ensures no orphan Student

        else:
            # Keep your temp image preview flow on general validation errors
            temp_image = request.FILES.get('temp_image')
            if temp_image:
                temp_filename = f"temp/{uuid.uuid4()}_{temp_image.name}"
                temp_path = os.path.join(settings.MEDIA_ROOT, temp_filename)
                os.makedirs(os.path.dirname(temp_path), exist_ok=True)
                with open(temp_path, 'wb+') as destination:
                    for chunk in temp_image.chunks():
                        destination.write(chunk)
                image_preview_url = f"/media/{temp_filename}"
            logger.debug(f"Form errors: {form.errors}")

    else:
        form = StudentAdmissionForm(initial=initial_data, request=request)

    return render(request, 'main_app/admission_form.html', {
        'form': form, 'classes': classes, 'selected_class': selected_class,
        'operator_name': request.user.username, 'image_preview_url': image_preview_url,
    })

@admin_required
def admission_success(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    student = get_object_or_404(Student, student_id=student_id, school=request.user.school)
    admission = get_object_or_404(StudentAdmission, student=student, academic_session=active_session)
    return render(request, 'main_app/admission_success.html', {'student': student, 'admission': admission})

@admin_required
def generate_pdf(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school
    )
    student = admission.student
    school = student.school
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=1.5 * inch)
    elements = []
    styles = getSampleStyleSheet()
    title_style = styles['Heading1']
    normal_style = styles['Normal']
    small_style = styles['Italic']
    school_name = school.school_name if school else "Sun Rise School"
    current_date = datetime.datetime.now().strftime("%B %d, %Y %I:%M %p PKT")
    if school and school.school_logo and os.path.exists(school.school_logo.path):
        try:
            logo = Image(school.school_logo.path, width=1 * inch, height=1 * inch)
            logo.hAlign = 'RIGHT'
            logo.drawHeight = 1 * inch
            logo.drawWidth = 1 * inch
            elements.append(logo)
        except Exception as e:
            elements.append(Paragraph(f"Error loading school logo: {str(e)}", normal_style))
    else:
        elements.append(Paragraph("No school logo available", normal_style))
    elements.append(Spacer(1, 0.1 * inch))
    header_elements = [
        Paragraph(f"<b>{school_name}</b>", title_style),
        Paragraph(f"Date: {current_date}", small_style),
    ]
    for elem in header_elements:
        elem.hAlign = 'CENTER'
        elements.append(elem)
        elements.append(Spacer(1, 0.1 * inch))
    if student.image and os.path.exists(student.image.path):
        try:
            img = Image(student.image.path, width=1.5 * inch, height=1.5 * inch)
            img.hAlign = 'LEFT'
            elements.append(img)
            elements.append(Spacer(1, 0.2 * inch))
        except Exception as e:
            elements.append(Paragraph(f"Error loading student image: {str(e)}", normal_style))
            elements.append(Spacer(1, 0.2 * inch))
    else:
        elements.append(Paragraph("No student image available", normal_style))
        elements.append(Spacer(1, 0.2 * inch))
    details = [
        f"<b>Student ID:</b> {student.student_id}",
        f"<b>Student Name:</b> {student.first_name} {student.last_name}",
        f"<b>Father/Guardian Name:</b> {student.father_guardian_name}",
        f"<b>Class:</b> {admission.class_name}",
        f"<b>Section:</b> {admission.section or 'N/A'}",
        f"<b>Roll Number:</b> {admission.roll_number}",
        "<b>Fee Details:</b>",
        f"  Admission Fee: Rs{admission.admission_fee or '0.00'}",
        f"  Tuition Fee: Rs{admission.tuition_fee or '0.00'}",
        f"  Exam Fee: Rs{admission.exam_fee or '0.00'}",
        f"  Book Fee: Rs{admission.book_fee or '0.00'}",
        f"  Uniform Fee: Rs{admission.uniform_fee or '0.00'}",
        f"  Other Fee: Rs{admission.other_fee or '0.00'}",
        f"  Promotion Fee: Rs{admission.promotion_fee or '0.00'}",
        f"  Transport Fee: Rs{admission.transport_fee or '0.00'}",
        f"  Discount: Rs{admission.discount or '0.00'}",
        "<b>Calculations:</b>",
        f"  Total Dues: Rs{admission.total_dues or '0.00'}",
        f"  Received: Rs{admission.received or '0.00'}",
        f"  Balance: Rs{admission.balance or '0.00'}",
    ]
    for detail in details:
        elements.append(Paragraph(detail, normal_style))
        elements.append(Spacer(1, 0.1 * inch))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(Paragraph("Powered By AliyarWeb Solutions", small_style))
    doc.build(elements)
    buffer.seek(0)
    pdf = buffer.getvalue()
    buffer.close()
    student_name = f"{student.first_name}_{student.last_name}".replace(" ", "_")
    roll_number = str(admission.roll_number)
    safe_file_name = f"{student_name}_{roll_number}.pdf"
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{safe_file_name}"'
    response.write(pdf)
    return response

@admin_required
def get_fee_structure(request):
    class_name = request.GET.get('class_name')
    try:
        active_session = AcademicSession.objects.get(school=request.user.school, is_active=True)
        fee_structure = FeeStructure.objects.get(class_name=class_name, academic_session=active_session)
        data = {
            'tuition_fee': float(fee_structure.tuition_fee or 0),
            'admission_fee': float(fee_structure.admission_fee or 0),
            'book_fee': float(fee_structure.books_dues or 0),
            'uniform_fee': float(fee_structure.uniform_dues or 0),
            'exam_fee': float(fee_structure.paper_money or 0),
            'promotion_fee': float(fee_structure.promotion_fee or 0),
            'other_fee': float(fee_structure.other_charges or 0),
            'transport_fee': 0.0,
        }
    except (AcademicSession.DoesNotExist, FeeStructure.DoesNotExist):
        data = {
            'error': 'No fee structure found for selected class in the active session.',
            'tuition_fee': 0.0, 'admission_fee': 0.0, 'book_fee': 0.0,
            'uniform_fee': 0.0, 'exam_fee': 0.0, 'promotion_fee': 0.0,
            'other_fee': 0.0, 'transport_fee': 0.0,
        }
    return JsonResponse(data)

@admin_required
def get_transport_details(request):
    option = request.GET.get('transport_option')
    if option == 'Paid':
        data = {'transport_fee': 800, 'note': 'Standard transport fee for local area'}
    elif option == 'Free':
        data = {'transport_fee': 0, 'note': 'No fee for staff children or nearby locality'}
    else:
        data = {'transport_fee': 0, 'note': 'No transport selected'}
    return JsonResponse(data)

@admin_required
def list_admissions(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    admissions = StudentAdmission.objects.filter(
        academic_session=active_session,
        student__school=request.user.school
    ).order_by('student__student_id')
    return render(request, 'main_app/list_admissions.html', {
        'admissions': admissions,
    })

@admin_required
def monthly_fee(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    students = []
    if request.method == 'GET' and 'search' in request.GET:
        search_query = request.GET.get('search', '').strip().lower()
        search_type = request.GET.get('search_type', 'student_id')
        if search_query:
            queryset = StudentAdmission.objects.filter(
                academic_session=active_session,
                student__school=request.user.school
            )
            if search_type == 'student_id':
                query = Q(student__student_id__iexact=search_query)
            elif search_type == 'contact':
                query = Q(student__contact__iexact=search_query)
            elif search_type == 'father_name':
                query = Q(student__father_guardian_name__iexact=search_query)
            else:
                query = Q(student__student_id__iexact=search_query) | Q(student__father_guardian_name__iexact=search_query) | Q(student__contact__iexact=search_query)
            students = queryset.filter(query).distinct()
            if not students:
                messages.warning(request, "No results found.")
            elif len(students) == 1:
                students = [students.first()]
    return render(request, 'main_app/monthly_fee.html', {
        'students': students,
        'active_session': active_session,
        'search_type': request.GET.get('search_type', 'student_id'),
    })

@admin_required
def monthly_fee_detail(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        logger.error("No active session found for user: %s", request.user)
        return render(request, 'main_app/monthly_fee_detail.html', {'student': None, 'month_data': [], 'active_session': None})

    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school
    )

    monthly_fees = MonthlyFee.objects.filter(student_admission=student_admission).order_by('year', 'month')
    monthly_fee_dict = {fee.month: fee for fee in monthly_fees}

    start_month_date = active_session.start_date + relativedelta(months=1)
    end_month_date = active_session.end_date
    start_month = datetime.datetime(1900, start_month_date.month, 1).strftime('%B')
    end_month = datetime.datetime(1900, end_month_date.month, 1).strftime('%B')
    current_year = active_session.start_date.year
    all_months = []
    current_month = start_month
    current_month_num = start_month_date.month
    current_year_for_months = current_year
    while True:
        all_months.append((current_month, current_year_for_months))
        if current_month == end_month and current_year_for_months == active_session.end_date.year:
            break
        current_month_num = (current_month_num % 12) + 1
        if current_month_num == 1:
            current_year_for_months += 1
        current_month = datetime.datetime(1900, current_month_num, 1).strftime('%B')

    month_data = []
    last_current_balance = student_admission.balance if student_admission.balance else Decimal('0.00')

    for month, year in all_months:
        fee = monthly_fee_dict.get(month)
        if fee:
            month_data.append({
                'month': month,
                'year': year,
                'fee': fee,
                'form': None,
                'status': 'Paid' if fee.has_payment else 'Pending',
                'disabled': fee.has_payment,
            })
            last_current_balance = fee.current_balance
        else:
            monthly_fee_value = (student_admission.tuition_fee or Decimal('0.00')) - (student_admission.discount or Decimal('0.00'))
            transport_fee_value = student_admission.transport_fee if student_admission.transport == 'Paid' else Decimal('0.00')
            month_index = datetime.datetime.strptime(month, '%B').month
            prev_month_num = (month_index - 2) % 12 + 1
            prev_year = year if prev_month_num < month_index else year - 1
            prev_month = datetime.datetime(1900, prev_month_num, 1).strftime('%B')
            prev_fee = monthly_fee_dict.get(prev_month)
            previous_balance = prev_fee.current_balance if prev_fee else last_current_balance
            total_dues = previous_balance + monthly_fee_value + transport_fee_value
            initial_data = {
                'student_admission': student_admission,
                'month': month,
                'year': year,
                'previous_balance': previous_balance,
                'monthly_fee': monthly_fee_value,
                'transport_fee': transport_fee_value,
                'total_dues': total_dues,
                'received': Decimal('0.00'),
                'current_balance': total_dues,
            }
            form = MonthlyFeeForm(initial=initial_data, request=request)  # Pass request to form
            month_data.append({
                'month': month,
                'year': year,
                'fee': None,
                'form': form,
                'status': 'Pending',
                'disabled': False,
            })
            last_current_balance = total_dues

    if request.method == 'POST':
        logger.debug("POST request received: %s", request.POST)
        post_month = request.POST.get('month')
        post_year = request.POST.get('year', current_year)
        post_year = int(post_year) if post_year else current_year
        existing_fee = MonthlyFee.objects.filter(
            student_admission=student_admission, month=post_month, year=post_year
        ).first()
        initial_data = {
            'student_admission': student_admission,
            'month': post_month,
            'year': post_year,
            'previous_balance': Decimal(request.POST.get('previous_balance', '0.00')),
            'monthly_fee': Decimal(request.POST.get('monthly_fee', '0.00')),
            'transport_fee': Decimal(request.POST.get('transport_fee', '0.00')),
            'total_dues': Decimal(request.POST.get('total_dues', '0.00')),
            'received': Decimal(request.POST.get('received', '0.00')),
            'current_balance': Decimal(request.POST.get('total_dues', '0.00')) - Decimal(request.POST.get('received', '0.00')),
        }
        logger.debug("Initial data: %s", initial_data)
        if existing_fee and not existing_fee.has_payment:
            form = MonthlyFeeForm(request.POST, instance=existing_fee, initial=initial_data, request=request)
        else:
            form = MonthlyFeeForm(request.POST, initial=initial_data, request=request)
        if form.is_valid():
            try:
                monthly_fee = form.save(commit=False)
                monthly_fee.student_admission = student_admission
                monthly_fee.previous_balance = form.cleaned_data['previous_balance']
                monthly_fee.operator = request.user
                monthly_fee.received = form.cleaned_data['received']
                monthly_fee.save()
                logger.info("Payment saved for %s %s, redirecting to monthly_fee_success with ID %s", monthly_fee.month, monthly_fee.year, monthly_fee.id)
                messages.success(request, f"Payment for {monthly_fee.month} {monthly_fee.year} saved successfully.")
                return redirect('monthly_fee_success', monthly_fee_id=monthly_fee.id)
            except Exception as e:
                logger.error("Error saving payment: %s", str(e))
                messages.error(request, f"Error saving payment: {str(e)}")
        else:
            logger.warning("Form invalid: %s", form.errors)
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
            for data in month_data:
                if data['month'] == post_month and data['year'] == post_year:
                    data['form'] = form
                    break

    return render(request, 'main_app/monthly_fee_detail.html', {
        'student': student_admission,
        'month_data': month_data,
        'active_session': active_session,
    })

@admin_required
def monthly_fee_success(request, monthly_fee_id):
    monthly_fee = get_object_or_404(MonthlyFee, id=monthly_fee_id, student_admission__student__school=request.user.school)
    messages.success(request, f"Payment for {monthly_fee.month} {monthly_fee.year} was successfully saved!")
    return render(request, 'main_app/monthly_fee_success.html', {'monthly_fee': monthly_fee})

@admin_required
def monthly_fee_pdf(request, monthly_fee_id):
    monthly_fee = get_object_or_404(MonthlyFee, id=monthly_fee_id, student_admission__student__school=request.user.school)
    student_admission = monthly_fee.student_admission
    student = student_admission.student
    school = student.school
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=1.5 * inch)
    elements = []
    styles = getSampleStyleSheet()
    title_style = styles['Heading1']
    normal_style = styles['Normal']
    small_style = styles['Italic']
    school_name = school.school_name if school else "Sun Rise School"
    current_date = datetime.datetime.now().strftime("%B %d, %Y %I:%M %p PKT")
    if school and school.school_logo and os.path.exists(school.school_logo.path):
        try:
            logo = Image(school.school_logo.path, width=1 * inch, height=1 * inch)
            logo.hAlign = 'RIGHT'
            logo.drawHeight = 1 * inch
            logo.drawWidth = 1 * inch
            elements.append(logo)
        except Exception as e:
            elements.append(Paragraph(f"Error loading school logo: {str(e)}", normal_style))
    else:
        elements.append(Paragraph("No school logo available", normal_style))
    elements.append(Spacer(1, 0.1 * inch))
    header_elements = [
        Paragraph(f"<b>{school_name}</b>", title_style),
        Paragraph(f"Date: {current_date}", small_style),
    ]
    for elem in header_elements:
        elem.hAlign = 'CENTER'
        elements.append(elem)
        elements.append(Spacer(1, 0.1 * inch))
    elements.append(Paragraph(f"Payment Receipt for {monthly_fee.month} {monthly_fee.year}", title_style))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(Paragraph("<b>Student Information</b>", normal_style))
    student_details = [
        f"Name: {student.first_name} {student.last_name}",
        f"Student ID: {student.student_id}",
        f"Father/Guardian Name: {student.father_guardian_name}",
        f"Class: {student_admission.class_name}",
        f"Section: {student_admission.section or 'N/A'}",
        f"Roll Number: {student_admission.roll_number}",
        f"Contact: {student.contact or 'N/A'}",
    ]
    for detail in student_details:
        elements.append(Paragraph(detail, normal_style))
        elements.append(Spacer(1, 0.1 * inch))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(Paragraph(f"<b>Payment Details for {monthly_fee.month} {monthly_fee.year}</b>", normal_style))
    payment_details = [
        f"Previous Balance: Rs{monthly_fee.previous_balance:.2f}",
        f"Monthly Fee: Rs{monthly_fee.monthly_fee:.2f}",
        f"Transport Fee: Rs{monthly_fee.transport_fee:.2f}",
        f"Total Dues: Rs{monthly_fee.total_dues:.2f}",
        f"Received: Rs{monthly_fee.received:.2f}",
        f"Current Balance: Rs{monthly_fee.current_balance:.2f}",
        f"Reviewed By: {monthly_fee.reviewed_by or 'Not specified'}",
        f"Payment Date: {monthly_fee.payment_date.strftime('%B %d, %Y')}",
    ]
    for detail in payment_details:
        elements.append(Paragraph(detail, normal_style))
        elements.append(Spacer(1, 0.1 * inch))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(Paragraph("Powered By AliyarWeb Solutions", small_style))
    doc.build(elements)
    buffer.seek(0)
    pdf = buffer.getvalue()
    buffer.close()
    filename = f"{student.first_name}_{student.last_name}_{student_admission.class_name}_{monthly_fee.month}_{monthly_fee.year}.pdf"
    filename = filename.replace(" ", "_").replace("/", "_")
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write(pdf)
    return response

@admin_required
def promote_offline_student(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found. Please create an active session first.")
        return redirect('dashboard')
    initial_data = request.session.get('promote_offline_data', {'transport': 'No'})
    temp_image_path = request.session.get('temp_image_path')
    temp_image_relative = None
    if temp_image_path:
        temp_image_relative = os.path.relpath(temp_image_path, settings.MEDIA_ROOT).replace('\\', '/')
    if request.method == 'POST':
        form = PromoteOfflineStudentForm(request.POST, request.FILES, request=request)
        if form.is_valid():
            cleaned_data = form.cleaned_data.copy()
            if 'admission_date' in cleaned_data and isinstance(cleaned_data['admission_date'], datetime.date):
                cleaned_data['admission_date'] = cleaned_data['admission_date'].isoformat()
            if 'date_of_birth' in cleaned_data and isinstance(cleaned_data['date_of_birth'], datetime.date):
                cleaned_data['date_of_birth'] = cleaned_data['date_of_birth'].isoformat()
            decimal_fields = [
                'transport_fee', 'tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee',
                'other_fee', 'promotion_fee', 'discount', 'received', 'total_dues', 'balance'
            ]
            for field in decimal_fields:
                if field in cleaned_data and isinstance(cleaned_data[field], Decimal):
                    cleaned_data[field] = float(cleaned_data[field])
            temp_image = cleaned_data.get('temp_image')
            new_temp_image_path = None
            if temp_image:
                if temp_image_path and os.path.exists(temp_image_path):
                    os.remove(temp_image_path)
                temp_filename = f"temp/{uuid.uuid4()}_{temp_image.name}"
                temp_path = os.path.join(settings.MEDIA_ROOT, temp_filename)
                os.makedirs(os.path.dirname(temp_path), exist_ok=True)
                with open(temp_path, 'wb+') as destination:
                    for chunk in temp_image.chunks():
                        destination.write(chunk)
                new_temp_image_path = temp_path
                cleaned_data['temp_image'] = temp_filename
            else:
                cleaned_data['temp_image'] = initial_data.get('temp_image')
                new_temp_image_path = temp_image_path
            request.session['promote_offline_data'] = cleaned_data
            request.session['temp_image_path'] = new_temp_image_path
            return redirect('promote_offline_preview')
    else:
        form = PromoteOfflineStudentForm(initial=initial_data, request=request)
    context = {
        'form': form,
        'active_session': active_session,
        'temp_image_path': temp_image_relative,
    }
    return render(request, 'main_app/promote_offline_student.html', context)

@admin_required
def promote_offline_preview(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found. Please create an active session first.")
        return redirect('dashboard')
    promote_data = request.session.get('promote_offline_data')
    temp_image_path = request.session.get('temp_image_path')
    if not promote_data:
        messages.error(request, "No promotion data found. Please start the process again.")
        return redirect('promote_offline_student')
    temp_image_relative = None
    if temp_image_path:
        temp_image_relative = os.path.relpath(temp_image_path, settings.MEDIA_ROOT).replace('\\', '/')
    promote_data = promote_data.copy()
    if 'admission_date' in promote_data:
        promote_data['admission_date'] = datetime.date.fromisoformat(promote_data['admission_date'])
    if 'date_of_birth' in promote_data:
        promote_data['date_of_birth'] = datetime.date.fromisoformat(promote_data['date_of_birth'])
    decimal_fields = [
        'transport_fee', 'tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee',
        'other_fee', 'promotion_fee', 'discount', 'received', 'total_dues', 'balance'
    ]
    for field in decimal_fields:
        if field in promote_data and promote_data[field] is not None:
            promote_data[field] = Decimal(str(promote_data[field]))
    temp_image = None
    if temp_image_path and os.path.exists(temp_image_path):
        with open(temp_image_path, 'rb') as f:
            from django.core.files.uploadedfile import InMemoryUploadedFile
            import io
            temp_image = InMemoryUploadedFile(
                file=io.BytesIO(f.read()),
                field_name='temp_image',
                name=os.path.basename(temp_image_path),
                content_type='image/jpeg',
                size=os.path.getsize(temp_image_path),
                charset=None
            )
            promote_data['temp_image'] = temp_image
    try:
        fee_structure = FeeStructure.objects.get(
            academic_session=active_session,
            class_name=promote_data['class_name']
        )
        fees = {
            'tuition_fee': fee_structure.tuition_fee,
            'book_fee': fee_structure.books_dues,
            'uniform_fee': fee_structure.uniform_dues,
            'other_fee': fee_structure.paper_money,
            'promotion_fee': fee_structure.promotion_fee,
            'other_charges': fee_structure.other_charges or 0.00,
            'admission_fee': 0.00,
        }
    except FeeStructure.DoesNotExist:
        fees = {
            'tuition_fee': 0.00,
            'book_fee': 0.00,
            'uniform_fee': 0.00,
            'other_fee': 0.00,
            'promotion_fee': 0.00,
            'other_charges': 0.00,
            'admission_fee': 0.00,
        }
    if request.method == 'POST':
        if 'confirm' in request.POST:
            files = {}
            if temp_image:
                files['temp_image'] = temp_image
            form = PromoteOfflineStudentForm(promote_data, files, request=request)
            if form.is_valid():
                admission = form.save(commit=False)
                admission.student.school = request.user.school
                admission.academic_session = active_session
                admission.operator = request.user
                admission.save()
                messages.success(request, f"Student {admission.student.first_name} {admission.student.last_name} promoted successfully to {admission.class_name}, section {admission.section}.")
                if temp_image_path and os.path.exists(temp_image_path):
                    os.remove(temp_image_path)
                if 'promote_offline_data' in request.session:
                    del request.session['promote_offline_data']
                if 'temp_image_path' in request.session:
                    del request.session['temp_image_path']
                return redirect('promote_offline_success', student_id=admission.student.student_id)
            else:
                messages.error(request, "Error saving the student. Please try again.")
                return redirect('promote_offline_student')
        elif 'edit' in request.POST:
            return redirect('promote_offline_student')
    context = {
        'promote_data': promote_data,
        'fees': fees,
        'active_session': active_session,
        'temp_image_path': temp_image_relative,
    }
    return render(request, 'main_app/promote_offline_preview.html', context)

@admin_required
def promote_offline_success(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school
    )
    messages.success(request, f"Student {admission.student.first_name} {admission.student.last_name} has been successfully promoted to {admission.class_name}, section {admission.section}.")
    return render(request, 'main_app/promote_offline_success.html', {'student': admission})

@admin_required
def promoted_students_report(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    promoted_students = StudentAdmission.objects.filter(
        academic_session=active_session,
        student__school=request.user.school,
        promoted=True
    )
    return render(request, 'main_app/promoted_students_report.html', {
        'students': promoted_students,
        'session': active_session,
    })

@admin_required
def promote_existing_students(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    previous_session = AcademicSession.objects.filter(
        school=request.user.school,
        is_active=False,
        end_date__lt=active_session.start_date
    ).order_by('-end_date').first()
    if not previous_session:
        return render(request, 'main_app/promote_existing_students.html', {
            'no_previous': True
        })
    class_choices = StudentAdmission.objects.filter(
        academic_session=previous_session,
        student__school=request.user.school
    ).values('class_name').distinct().order_by('class_name')
    class_choices = [c['class_name'] for c in class_choices]
    section_choices = StudentAdmission.objects.filter(
        academic_session=previous_session,
        student__school=request.user.school
    ).values('section').distinct().order_by('section')
    section_choices = [(s['section'], s['section']) for s in section_choices]
    selected_class_name = request.POST.get('class_name') or request.GET.get('class_name', '')
    selected_section = request.POST.get('section') or request.GET.get('section', '')

    students = []
    if selected_class_name and selected_section:
        students = StudentAdmission.objects.filter(
            academic_session=previous_session,
            student__school=request.user.school,
            class_name=selected_class_name,
            section=selected_section
        ).select_related('student').order_by('roll_number')
    return render(request, 'main_app/promote_existing_students.html', {
        'class_choices': class_choices,
        'section_choices': section_choices,
        'selected_class_name': selected_class_name,
        'selected_section': selected_section,
        'students': students,
        'no_previous': False
    })

@admin_required
def promote_existing_student(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('promote_existing_students')
    
    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        student__school=request.user.school
    )
    
    if StudentAdmission.objects.filter(
        student=student_admission.student,
        academic_session=active_session
    ).exists():
        messages.error(request, f"Student {student_admission.student.first_name} is already admitted in the current session.")
        return redirect('promote_existing_students')
    
    filtered_class = request.GET.get('class_name')
    filtered_section = request.GET.get('section')
    current_class = student_admission.class_name
    
    # Get fee structures for all classes
    fee_structures = FeeStructure.objects.filter(academic_session=active_session).values(
        'class_name', 'tuition_fee', 'paper_money', 'books_dues', 'uniform_dues', 'other_charges', 'promotion_fee'
    )
    fee_structure_dict = {
        fs['class_name']: {
            'tuition_fee': str(fs['tuition_fee']),
            'exam_fee': str(fs['paper_money']),
            'book_fee': str(fs['books_dues']),
            'uniform_fee': str(fs['uniform_dues']),
            'other_fee': str(fs['other_charges'] or Decimal('0.00')),
            'promotion_fee': str(fs['promotion_fee'])
        } for fs in fee_structures
    }
    
    # Check for fee structure for initial class
    initial_class = filtered_class or CLASS_PROGRESSION.get(current_class, current_class) or current_class
    fee_structure_exists = FeeStructure.objects.filter(
        academic_session=active_session,
        class_name=initial_class
    ).exists()
    if not fee_structure_exists:
        messages.warning(request, f"No fee structure found for class {initial_class} in the current session. Fee fields will be set to 0.")
    
    form = PromoteExistingStudentForm(
        request.POST or None,
        request.FILES or None,
        instance=student_admission,
        student_admission=student_admission,
        request=request,
        filtered_class=filtered_class,
        filtered_section=filtered_section
    )
    
    if request.method == 'POST' and form.is_valid():
        try:
            new_admission = form.save(commit=False)
            new_admission.academic_session = active_session
            new_admission.student.school = request.user.school
            new_admission.promoted = True
            new_admission.failed_to_promote = False
            new_admission.operator = request.user
            new_admission.save()
            messages.success(request, f"Student {student_admission.student.first_name} promoted successfully.")
            query_params = urlencode({'class_name': filtered_class, 'section': filtered_section})
            return redirect(f"{reverse('promote_existing_students')}?{query_params}")
        except Exception as e:
            messages.error(request, f"Error promoting student: {str(e)}")
    
    return render(request, 'main_app/promote_existing_student_form.html', {
        'form': form,
        'student': student_admission,
        'current_class': current_class,
        'fee_structures': fee_structure_dict,
        'filtered_class': filtered_class,
        'filtered_section': filtered_section,
        'active_session': active_session  # Pass active_session for template
    })

@admin_required
def mark_not_promoted(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('promote_existing_students')

    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        student__school=request.user.school
    )

    if StudentAdmission.objects.filter(
        student=student_admission.student,
        academic_session=active_session
    ).exists():
        messages.error(request, f"Student {student_admission.student.first_name} is already admitted in the current session.")
        return redirect('promote_existing_students')

    roll_number = student_admission.roll_number

    # Check for roll number conflict
    if StudentAdmission.objects.filter(
        roll_number=roll_number,
        class_name=student_admission.class_name,
        section=student_admission.section,
        academic_session=active_session
    ).exists():
        return redirect('roll_number_prompt', student_id=student_id)

    try:
        new_admission = create_repeated_admission(
            student_admission, active_session, roll_number, request.user
        )
        new_admission.save()
        
        messages.success(request, f"Student {student_admission.student.first_name} marked as not promoted.")
        return redirect('promote_existing_success', student_id=student_admission.student.student_id)
    
    except Exception as e:
        messages.error(request, f"Error creating new admission: {str(e)}")
        return redirect('promote_existing_students')

@admin_required
def roll_number_prompt(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('main_app/promote_existing_students')
    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        student__school=request.user.school
    )
    if StudentAdmission.objects.filter(
        student=student_admission.student,
        academic_session=active_session
    ).exists():
        messages.error(request, f"Student {student_admission.student.first_name} is already admitted in the current session.")
        return redirect('main_app/promote_existing_students')
    filtered_class = request.GET.get('class_name')
    filtered_section = request.GET.get('section')
    if request.method == 'POST':
        form = RollNumberPromptForm(
            request.POST,
            class_name=student_admission.class_name,
            section=student_admission.section,
            academic_session=active_session
        )
        if form.is_valid():
            roll_number = form.cleaned_data['roll_number']
            try:
                new_admission = create_repeated_admission(
                    student_admission, active_session, roll_number, request.user
                )
                new_admission.save()
        
                messages.success(request, f"Student {student_admission.student.first_name} marked as not promoted.")
                return redirect('promote_existing_success', student_id=student_admission.student.student_id)
    
            except Exception as e:
                messages.error(request, f"Error creating new admission: {str(e)}")
                return redirect('promote_existing_students')

@admin_required
def promote_existing_success(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school
    )
    filtered_class = request.GET.get('class_name')
    filtered_section = request.GET.get('section')
    return render(request, 'main_app/promote_existing_success.html', {
        'student': student_admission.student,
        'class_name': filtered_class,
        'section': filtered_section
    })
    
@admin_required
def teacher_list(request):
    
    teachers = Teacher.objects.filter(school=request.user.school)
    return render(request, 'main_app/teacher_list.html', {'teachers': teachers})

@admin_required
def teacher_create(request):
    
    if request.method == 'POST':
        form = TeacherForm(request.POST, request=request)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Teacher created successfully.")
                logger.info("Teacher created: %s", form.cleaned_data['name'])
                return redirect('teacher_list')
            except Exception as e:
                logger.error("Error creating teacher: %s", str(e))
                messages.error(request, f"Error creating teacher: {str(e)}")
        else:
            logger.warning("Teacher form invalid: %s", form.errors)
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = TeacherForm(request=request)
    return render(request, 'main_app/teacher_form.html', {'form': form, 'action': 'Create'})

@admin_required
def teacher_update(request, teacher_id):
    teacher = get_object_or_404(Teacher, id=teacher_id, school=request.user.school)
    if request.method == 'POST':
        form = TeacherForm(request.POST, instance=teacher, request=request)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Teacher updated successfully.")
                logger.info("Teacher updated: %s", teacher.name)
                return redirect('teacher_list')
            except Exception as e:
                logger.error("Error updating teacher: %s", str(e))
                messages.error(request, f"Error updating teacher: {str(e)}")
        else:
            logger.warning("Teacher form invalid: %s", form.errors)
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = TeacherForm(instance=teacher, request=request, initial={'username': teacher.user.username})
    return render(request, 'main_app/teacher_form.html', {'form': form, 'action': 'Update', 'teacher': teacher})

@admin_required
def teacher_delete(request, teacher_id):
    teacher = get_object_or_404(Teacher, id=teacher_id, school=request.user.school)
    if request.method == 'POST':
        try:
            teacher.user.delete()
            teacher.delete()
            messages.success(request, "Teacher deleted successfully.")
            logger.info("Teacher deleted: %s", teacher.name)
        except Exception as e:
            logger.error("Error deleting teacher: %s", str(e))
            messages.error(request, f"Error deleting teacher: {str(e)}")
        return redirect('teacher_list')
    return render(request, 'main_app/teacher_confirm_delete.html', {'teacher': teacher})

@admin_or_teacher_required
def teacher_dashboard(request):
    
    teacher = get_object_or_404(Teacher, user=request.user, school=request.user.school)
    active_session = AcademicSession.objects.filter(school=request.user.school, is_active=True).first()
    
    students = StudentAdmission.objects.filter(
        student__school=teacher.school,
        class_name=teacher.class_name,
        section=teacher.section,
        academic_session=active_session
    )
    
    results = StudentResult.objects.filter(
        student_admission__in=students,
        subject__in=teacher.subjects.all()
    ).order_by('student_admission__student__first_name', 'subject__name', 'exam_type')
    
    exam_type = request.GET.get('exam_type', '')
    subject_id = request.GET.get('subject', '')
    if exam_type:
        results = results.filter(exam_type=exam_type)
    if subject_id:
        results = results.filter(subject_id=subject_id)


    # Events
    events = Events.objects.filter(school=teacher.school, event_for__in=['All', 'Teacher']).order_by('event_date')    
    
    context = {
        'active_session': active_session,
        'results': results,
        'subjects': teacher.subjects.all(),
        'exam_types': StudentResult.EXAM_TYPE_CHOICES,
        'exam_type': exam_type,
        'subject_id': subject_id,
        'events': events,
    }
    return render(request, 'main_app/teacher_dashboard.html', context)


@login_required
@user_passes_test(is_teacher, login_url='dashboard')
def result_create(request):
    teacher = get_object_or_404(
        Teacher,
        user=request.user,
        school=request.user.school
    )
    if request.method == 'POST':
        form = StudentResultForm(request.POST, teacher=teacher)
        if form.is_valid():
            try:
                result = form.save(commit=False)
                result.save()
                messages.success(request, "Result added successfully.")
                return redirect('teacher_dashboard')
            except Exception as e:
                messages.error(request, f"Error adding result: {str(e)}")
        else:
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = StudentResultForm(teacher=teacher)
    return render(request, 'main_app/result_form.html', {'form': form, 'action': 'Add'})


@login_required
@user_passes_test(is_teacher, login_url='dashboard')
def result_edit(request, result_id):
    teacher = get_object_or_404(
        Teacher,
        user=request.user,
        school=request.user.school
    )
    # ✅ Scoped to teacher's school + class + section
    # prevents Teacher A editing Teacher B's results in same school
    result = get_object_or_404(
        StudentResult,
        id=result_id,
        student_admission__student__school=teacher.school,
        student_admission__class_name=teacher.class_name,
        student_admission__section=teacher.section
    )
    if request.method == 'POST':
        form = StudentResultForm(request.POST, instance=result, teacher=teacher)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Result updated successfully.")
                return redirect('teacher_dashboard')
            except Exception as e:
                messages.error(request, f"Error updating result: {str(e)}")
        else:
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = StudentResultForm(instance=result, teacher=teacher)
    return render(request, 'main_app/result_form.html', {'form': form, 'action': 'Edit'})


@login_required
@user_passes_test(is_teacher, login_url='dashboard')
def result_delete(request, result_id):
    teacher = get_object_or_404(
        Teacher,
        user=request.user,
        school=request.user.school
    )
    # ✅ Scoped to teacher's school + class + section
    result = get_object_or_404(
        StudentResult,
        id=result_id,
        student_admission__student__school=teacher.school,
        student_admission__class_name=teacher.class_name,
        student_admission__section=teacher.section
    )
    if request.method == 'POST':
        result.delete()
        messages.success(request, "Result deleted successfully.")
        return redirect('teacher_dashboard')
    return render(
        request,
        'main_app/result_confirm_delete.html',
        {'result': result}
    )


@admin_or_teacher_required
def subject_list(request):
    subjects = Subject.objects.filter(school=request.user.school)
    return render(request, 'main_app/subject_list.html', {'subjects': subjects})

@admin_or_teacher_required
def subject_create(request):
    
    if request.method == 'POST':
        form = SubjectForm(request.POST, request=request)
        if form.is_valid():
            try:
                subject = form.save(commit=False)
                subject.school = request.user.school
                subject.save()
                messages.success(request, "Subject created successfully.")
                return redirect('subject_list')
            except Exception as e:
                messages.error(request, f"Error creating subject: {str(e)}")
        else:
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = SubjectForm(request=request)
    return render(request, 'main_app/subject_form.html', {'form': form, 'action': 'Create'})


@admin_or_teacher_required
def subject_edit(request, subject_id):

    subject = get_object_or_404(Subject, id=subject_id, school=request.user.school)

    if request.method == 'POST':
        form = SubjectForm(request.POST, instance=subject, request=request)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Subject updated successfully.")
                return redirect('subject_list')
            except Exception as e:
                messages.error(request, f"Error updating subject: {str(e)}")
        else:
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = SubjectForm(instance=subject, request=request)

    return render(request, 'main_app/subject_form.html', {'form': form, 'action': 'Edit'})



@admin_or_teacher_required
def subject_delete(request, pk):
    
    subject = get_object_or_404(Subject, pk=pk, school=request.user.school)
    if request.method == 'POST':
        subject.delete()
        messages.success(request, "Subject deleted successfully.")
        return redirect('subject_list')
    
    messages.error(request, "Invalid request.")
    return redirect('subject_list')






@admin_or_teacher_required
def add_syllabus(request):

    if request.method == 'POST':
        form = SyllabusForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            try:
                syllabus = form.save(commit=False)
                syllabus.school = request.user.school
                syllabus.uploaded_by = request.user
                syllabus.academic_session = AcademicSession.objects.get(school=request.user.school, is_active=True)
                syllabus.save()
                messages.success(request, f"Syllabus added for {syllabus.subject.name} ({syllabus.class_name}).")
                return redirect('syllabus_list')
            except AcademicSession.DoesNotExist:
                messages.error(request, "No active academic session found.")
            except Exception as e:
                messages.error(request, f"Error adding syllabus: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = SyllabusForm(user=request.user)

    return render(request, 'main_app/add_syllabus.html', {'form': form, 'action': 'Add'})


@admin_or_teacher_required
def syllabus_list(request):

    syllabi = Syllabus.objects.filter(school=request.user.school).select_related('subject', 'uploaded_by')
    return render(request, 'main_app/syllabus_list.html', {'syllabus_list': syllabi})


@admin_or_teacher_required
def edit_syllabus(request, syllabus_id):
    syllabus = get_object_or_404(Syllabus, id=syllabus_id, school=request.user.school)


    if request.method == 'POST':
        form = SyllabusForm(request.POST, request.FILES, instance=syllabus, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Syllabus updated successfully.")
            return redirect('syllabus_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = SyllabusForm(instance=syllabus, user=request.user)

    return render(request, 'main_app/add_syllabus.html', {'form': form, 'action': 'Edit'})


@admin_or_teacher_required
def delete_syllabus(request, syllabus_id):
    syllabus = get_object_or_404(Syllabus, id=syllabus_id, school=request.user.school)


    if request.method == 'POST':
        syllabus.delete()
        messages.success(request, "Syllabus deleted successfully.")
        return redirect('syllabus_list')

    messages.error(request, "Invalid request method.")
    return redirect('syllabus_list')




def calculate_overall_grade(percentage):
    if percentage >= 90: return 'A+'
    elif percentage >= 80: return 'A'
    elif percentage >= 70: return 'B'
    elif percentage >= 60: return 'C'
    elif percentage >= 50: return 'D'
    else: return 'F'

@admin_or_teacher_required
def generate_results_pdf(request):
    if not is_student(request.user):
        messages.error(request, "Only students can access this feature.")
        return redirect('index')

    try:
        student = Student.objects.get(student_id=request.user.username)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found.")
        logger.error(f"No Student found for username: {request.user.username}")
        return redirect('student_dashboard')

    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active academic session found.")
        logger.error(f"No active AcademicSession for school: {student.school}")
        return redirect('student_dashboard')

    admission = StudentAdmission.objects.filter(student=student, academic_session=active_session).first()
    if not admission:
        messages.error(request, "No admission record found for the current session.")
        logger.error(f"No StudentAdmission for student: {student.student_id}, session: {active_session.session_name}")
        return redirect('student_dashboard')

    # Get filter parameters
    exam_type_filter = request.GET.get('exam_type')
    subject_filter = request.GET.get('subject')

    # Results query
    results = StudentResult.objects.filter(student_admission=admission).select_related('subject', 'student_admission')
    if exam_type_filter:
        results = results.filter(exam_type=exam_type_filter)
    if subject_filter:
        results = results.filter(subject__id=subject_filter)

    class_teacher = Teacher.objects.filter(class_name=admission.class_name, section=admission.section, school=student.school).first()
    result_data = [
        {
            'subject_name': result.subject.name,
            'exam_type': result.exam_type,
            'teacher_name': class_teacher.name if class_teacher else 'N/A',
            'total_marks': result.total_marks,
            'obtained_marks': result.obtained_marks,
            'percentage': result.percentage,
            'grade': result.grade,
        } for result in results
    ]

    # Calculate summary
    total_exam_marks = sum(result['total_marks'] for result in result_data)
    total_obtained_marks = sum(result['obtained_marks'] for result in result_data)
    overall_percentage = (total_obtained_marks / total_exam_marks * 100) if total_exam_marks > 0 else 0
    overall_grade = calculate_overall_grade(overall_percentage)

    # Create PDF response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="DMC_{student.student_id}_{active_session.session_name}.pdf"'

    # Build PDF with ReportLab
    doc = SimpleDocTemplate(response, pagesize=letter, rightMargin=0.3*inch, leftMargin=0.3*inch, topMargin=0.3*inch, bottomMargin=0.3*inch)  # Reduced margins
    elements = []

    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(name='Title', fontSize=14, alignment=1, spaceAfter=8)  # Adjusted font size
    subtitle_style = ParagraphStyle(name='Subtitle', fontSize=10, alignment=1, spaceAfter=8)
    info_style = ParagraphStyle(name='Info', fontSize=9, alignment=1, spaceAfter=5)  # Adjusted font size

    # Header with logo in top right
    header_table_data = [[Paragraph(student.school.school_name, title_style), '']]
    if student.school.school_logo and os.path.exists(student.school.school_logo.path):
        logo = Image(student.school.school_logo.path, width=0.8*inch, height=0.8*inch)  # Smaller logo
        header_table_data[0][1] = logo
    header_table = Table(header_table_data, colWidths=[5*inch, 1.5*inch])  # Adjusted for Letter size
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)
    elements.append(Paragraph(student.school.city or "School Address", subtitle_style))
    elements.append(Paragraph("Detailed Marks Certificate", title_style))
    elements.append(Paragraph(f"Academic Session: {active_session.session_name}", subtitle_style))
    elements.append(Spacer(1, 0.15*inch))  # Reduced spacing

    # Student Info (centered)
    elements.append(Paragraph(f"Student Name: {student.first_name} {student.last_name}", info_style))
    elements.append(Paragraph(f"Student ID: {student.student_id}", info_style))
    elements.append(Paragraph(f"Class: {admission.class_name} ({admission.section})", info_style))
    elements.append(Paragraph(f"Class Teacher: {class_teacher.name if class_teacher else 'Not assigned'}", info_style))
    elements.append(Spacer(1, 0.15*inch))

    # Results Table with Summary
    if result_data:
        table_data = [['Subject', 'Exam Type', 'Teacher', 'Obtained Marks', 'Total Marks', 'Percentage', 'Grade']]
        for result in result_data:
            table_data.append([
                result['subject_name'],
                result['exam_type'],
                result['teacher_name'],
                f"{result['obtained_marks']:.1f}",
                f"{result['total_marks']:.1f}",
                f"{result['percentage']:.2f}%",
                result['grade']
            ])
        # Add summary rows
        table_data.append(['', '', '', '', '', '', ''])  # Separator
        table_data.append(['Total', '', '', f"{total_obtained_marks:.1f}", f"{total_exam_marks:.1f}", f"{overall_percentage:.2f}%", overall_grade])
        table = Table(table_data, colWidths=[1.4*inch, 0.9*inch, 1.4*inch, 1.2*inch, 1*inch, 0.9*inch, 0.6*inch])  # Increased Obtained Marks width
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),  # Bold for summary
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),  # Highlight summary
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No results available.", info_style))

    # Build PDF
    doc.build(elements)
    logger.info(f"DMC generated for student: {student.student_id}, session: {active_session.session_name}, filters: exam_type={exam_type_filter}, subject={subject_filter}")
    return response



@admin_required
def staff_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    staff = Staff.objects.filter(school=request.user.school)
    return render(request, 'main_app/staff_list.html', {'staff': staff})  # Updated path

@admin_required
def staff_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    staff = get_object_or_404(Staff, pk=pk, school=request.user.school)
    return render(request, 'main_app/staff_detail.html', {'staff': staff})  # Updated path

@admin_required
def staff_create(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if request.method == 'POST':
        form = StaffForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Staff member created successfully.")
            return redirect('staff_list')
    else:
        form = StaffForm(user=request.user)
    return render(request, 'main_app/staff_form.html', {'form': form, 'title': 'Add Staff'})  # Updated path

@admin_required
def staff_update(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    staff = get_object_or_404(Staff, pk=pk, school=request.user.school)
    if request.method == 'POST':
        form = StaffForm(request.POST, instance=staff, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Staff member updated successfully.")
            return redirect('staff_list')
    else:
        form = StaffForm(instance=staff, user=request.user)
    return render(request, 'main_app/staff_form.html', {'form': form, 'title': 'Edit Staff'})  # Updated path

@admin_required
def staff_delete(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    staff = get_object_or_404(Staff, pk=pk, school=request.user.school)
    if request.method == 'POST':
        staff.delete()
        messages.success(request, "Staff member deleted successfully.")
        return redirect('staff_list')
    return render(request, 'main_app/staff_confirm_delete.html', {'staff': staff})


@admin_required
def assets_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not AcademicSession.objects.filter(school=request.user.school, is_active=True).exists():
        messages.error(request, "No active academic session found.")
        return render(request, 'main_app/no_session.html')
    assets = Assets.objects.filter(school=request.user.school)
    return render(request, 'main_app/assets_list.html', {'assets': assets})

@admin_required
def assets_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not AcademicSession.objects.filter(school=request.user.school, is_active=True).exists():
        messages.error(request, "No active academic session found.")
        return render(request, 'main_app/no_session.html')
    asset = get_object_or_404(Assets, pk=pk, school=request.user.school)
    return render(request, 'main_app/assets_detail.html', {'asset': asset})

@admin_required
def assets_create(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    if request.method == 'POST':
        form = AssetsForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Asset created successfully.")
            return redirect('assets_list')
    else:
        form = AssetsForm(user=request.user)
    return render(request, 'main_app/assets_form.html', {'form': form, 'title': 'Add Asset'})

@admin_required
def assets_update(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    asset = get_object_or_404(Assets, pk=pk, school=request.user.school)
    if request.method == 'POST':
        form = AssetsForm(request.POST, instance=asset, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Asset updated successfully.")
            return redirect('assets_list')
    else:
        form = AssetsForm(instance=asset, user=request.user)
    return render(request, 'main_app/assets_form.html', {'form': form, 'title': 'Edit Asset'})

@admin_required
def assets_delete(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    asset = get_object_or_404(Assets, pk=pk, school=request.user.school)
    if request.method == 'POST':
        asset.delete()
        messages.success(request, "Asset deleted successfully.")
        return redirect('assets_list')
    return render(request, 'main_app/assets_confirm_delete.html', {'asset': asset})    




@admin_required
def expenses_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    expenses = Expenses.objects.filter(school=request.user.school)
    return render(request, 'main_app/expenses_list.html', {'expenses': expenses})

@admin_required
def expenses_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    expense = get_object_or_404(Expenses, pk=pk, school=request.user.school)
    return render(request, 'main_app/expenses_detail.html', {'expense': expense})

@admin_required
def expenses_create(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    if request.method == 'POST':
        form = ExpensesForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Expense created successfully.")
            return redirect('expenses_list')
    else:
        form = ExpensesForm(user=request.user)
    return render(request, 'main_app/expenses_form.html', {'form': form, 'title': 'Add Expense'})

@admin_required
def expenses_update(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    expense = get_object_or_404(Expenses, pk=pk, school=request.user.school)
    if request.method == 'POST':
        form = ExpensesForm(request.POST, instance=expense, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Expense updated successfully.")
            return redirect('expenses_list')
    else:
        form = ExpensesForm(instance=expense, user=request.user)
    return render(request, 'main_app/expenses_form.html', {'form': form, 'title': 'Edit Expense'})

@admin_required
def expenses_delete(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    expense = get_object_or_404(Expenses, pk=pk, school=request.user.school)
    if request.method == 'POST':
        expense.delete()
        messages.success(request, "Expense deleted successfully.")
        return redirect('expenses_list')
    return render(request, 'main_app/expenses_confirm_delete.html', {'expense': expense})


@admin_required
def transport_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    transports = Transport.objects.filter(school=request.user.school)
    return render(request, 'main_app/transport_list.html', {'transports': transports})

@admin_required
def transport_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    transport = get_object_or_404(Transport, pk=pk, school=request.user.school)
    return render(request, 'main_app/transport_detail.html', {'transport': transport})

@admin_required
def transport_create(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    if request.method == 'POST':
        form = TransportForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Transport created successfully.")
            return redirect('transport_list')
    else:
        form = TransportForm(user=request.user)
    return render(request, 'main_app/transport_form.html', {'form': form, 'title': 'Add Transport'})

@admin_required
def transport_update(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    transport = get_object_or_404(Transport, pk=pk, school=request.user.school)
    if request.method == 'POST':
        form = TransportForm(request.POST, instance=transport, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Transport updated successfully.")
            return redirect('transport_list')
    else:
        form = TransportForm(instance=transport, user=request.user)
    return render(request, 'main_app/transport_form.html', {'form': form, 'title': 'Edit Transport'})

@admin_required
def transport_delete(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    transport = get_object_or_404(Transport, pk=pk, school=request.user.school)
    if request.method == 'POST':
        transport.delete()
        messages.success(request, "Transport deleted successfully.")
        return redirect('transport_list')
    return render(request, 'main_app/transport_confirm_delete.html', {'transport': transport})






@admin_required
def events_list(request):
    
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    events = Events.objects.filter(school=request.user.school)
    return render(request, 'main_app/events_list.html', {'events': events})

@admin_required
def events_detail(request, pk):
    
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        # No active session
        return render(request, 'main_app/no_session.html')
    event = get_object_or_404(Events, pk=pk, school=request.user.school)
    return render(request, 'main_app/events_detail.html', {'event': event})

@admin_required
def events_create(request):
    
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        # No active session
        return render(request, 'main_app/no_session.html')
    if request.method == 'POST':
        form = EventsForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Event created successfully.")
            return redirect('events_list')
    else:
        form = EventsForm(user=request.user)
    return render(request, 'main_app/events_form.html', {'form': form, 'title': 'Add Event'})

@admin_required
def events_update(request, pk):
    
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        # No active session
        return render(request, 'main_app/no_session.html')
    event = get_object_or_404(Events, pk=pk, school=request.user.school)
    if request.method == 'POST':
        form = EventsForm(request.POST, instance=event, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Event updated successfully.")
            return redirect('events_list')
    else:
        form = EventsForm(instance=event, user=request.user)
    return render(request, 'main_app/events_form.html', {'form': form, 'title': 'Edit Event'})

@admin_required
def events_delete(request, pk):
    
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        # No active session
        return render(request, 'main_app/no_session.html')
    event = get_object_or_404(Events, pk=pk, school=request.user.school)
    if request.method == 'POST':
        event.delete()
        messages.success(request, "Event deleted successfully.")
        return redirect('events_list')
    return render(request, 'main_app/events_confirm_delete.html', {'event': event})





@admin_required
def analytics(request):
    chart_html = None
    form = AnalyticsForm(request.POST or None, request=request)
    user_school = request.user.school

    # 📌 Sessions for dropdown
    all_sessions = AcademicSession.objects.filter(school=user_school).order_by('-start_date')
    selected_session_id = request.GET.get('session')

    if selected_session_id:
        active_session = AcademicSession.objects.filter(id=selected_session_id, school=user_school).first()
    else:
        active_session = AcademicSession.objects.filter(is_active=True, school=user_school).first()

    # 1️⃣ Pie Chart - Student distribution
    student_counts = StudentAdmission.objects.filter(student__school=user_school)
    if active_session:
        student_counts = student_counts.filter(academic_session=active_session)

    pie_df = (
        student_counts
        .values('class_name')
        .annotate(count=Count('id'))
        .order_by('class_name')
    )
    pie_chart = px.pie(
        pie_df,
        names='class_name',
        values='count',
        title='🎓 Student Distribution by Class',
        hole=0.4,
        color_discrete_sequence=px.colors.sequential.RdBu
    ).to_html()

    # 2️⃣ Bar Chart - Financial Overview
    teacher_salary = Teacher.objects.filter(school=user_school).aggregate(total=Sum('salary'))['total'] or Decimal('0.00')
    staff_salary = Staff.objects.filter(school=user_school).aggregate(total=Sum('salary'))['total'] or Decimal('0.00')

    total_expenses_qs = Expenses.objects.filter(school=user_school)
    if active_session:
        total_expenses_qs = total_expenses_qs.filter(payment_date__range=(active_session.start_date, active_session.end_date))
    total_expenses = total_expenses_qs.aggregate(total=Sum('price'))['total'] or Decimal('0.00')

    monthly_fee_qs = MonthlyFee.objects.filter(student_admission__student__school=user_school)
    admission_fee_qs = StudentAdmission.objects.filter(student__school=user_school)

    if active_session:
        monthly_fee_qs = monthly_fee_qs.filter(student_admission__academic_session=active_session)
        admission_fee_qs = admission_fee_qs.filter(academic_session=active_session)

    monthly_fee_total = monthly_fee_qs.aggregate(total=Sum('received'))['total'] or Decimal('0.00')
    admission_fee_total = admission_fee_qs.aggregate(total=Sum('received'))['total'] or Decimal('0.00')
    total_fee = monthly_fee_total + admission_fee_total

    total_balance = monthly_fee_qs.aggregate(total=Sum('current_balance'))['total'] or Decimal('0.00')

    bar_df = pd.DataFrame({
        "Category": ["Expenses", "Teacher Salaries", "Staff Salaries", "Fee Collected", "Balance"],
        "Amount": [total_expenses, teacher_salary, staff_salary, total_fee, total_balance]
    })
    bar_chart = px.bar(
        bar_df,
        x="Category",
        y="Amount",
        title="💰 Financial Overview",
        color="Category",
        text="Amount"
    ).to_html()

    # 3️⃣ Scatter Chart - Activity Trend
    monthly_fees = MonthlyFee.objects.filter(student_admission__student__school=user_school)
    admissions = StudentAdmission.objects.filter(student__school=user_school)
    expenses = Expenses.objects.filter(school=user_school)

    if active_session:
        monthly_fees = monthly_fees.filter(student_admission__academic_session=active_session)
        admissions = admissions.filter(academic_session=active_session)
        expenses = expenses.filter(payment_date__range=(active_session.start_date, active_session.end_date))

    progress_data = []
    for fee in monthly_fees.values('payment_date', 'received'):
        progress_data.append({"date": fee['payment_date'], "type": "Fee Collected", "amount": float(fee['received'])})
    for adm in admissions.values('admission_date'):
        progress_data.append({"date": adm['admission_date'], "type": "Admission", "amount": 1})
    for exp in expenses.values('payment_date', 'price'):
        progress_data.append({"date": exp['payment_date'], "type": "Expense", "amount": float(exp['price']) * -1})

    progress_df = pd.DataFrame(progress_data)
    if not progress_df.empty:
        progress_df['date'] = pd.to_datetime(progress_df['date'])

    trend_chart = px.scatter(
        progress_df,
        x="date",
        y="amount",
        color="type",
        title="📈 School Activity Trend Over Time",
        trendline="ols"
    ).to_html()

    # 4️⃣ Interactive Chart via Form
    if form.is_valid():
        model_name = form.cleaned_data['model_name']
        x_axis = form.cleaned_data['x_axis']
        y_axis = form.cleaned_data['y_axis']
        chart_type = form.cleaned_data['chart_type']

        model = apps.get_model('main_app', model_name)
        queryset = model.objects.all()

        if 'school' in [f.name for f in model._meta.fields]:
            queryset = queryset.filter(school=user_school)
        if 'academic_session' in [f.name for f in model._meta.fields] and active_session:
            queryset = queryset.filter(academic_session=active_session)

        df = pd.DataFrame.from_records(queryset.values(x_axis, y_axis)).dropna()
        if df.empty:
            chart_html = "<p style='color:red;'>No data available for the selected model and fields.</p>"
        elif df[x_axis].nunique() <= 1:
            chart_html = "<p style='color:red;'>Not enough variation in the X-axis to plot this chart.</p>"
        else:
            if chart_type == 'bar':
                fig = px.bar(df, x=x_axis, y=y_axis)
            elif chart_type == 'line':
                fig = px.line(df, x=x_axis, y=y_axis)
            elif chart_type == 'scatter':
                try:
                    fig = px.scatter(df, x=x_axis, y=y_axis, trendline="ols")
                except Exception:
                    fig = px.scatter(df, x=x_axis, y=y_axis)
            chart_html = fig.to_html()

    return render(request, 'main_app/analytics.html', {
        'form': form,
        'chart': chart_html,
        'pie_chart': pie_chart,
        'bar_chart': bar_chart,
        'trend_chart': trend_chart,
        'all_sessions': all_sessions,
        'selected_session': active_session,
    })


@admin_required
def session_analytics(request):
    # 1️⃣ Get school for logged-in user
    if hasattr(request.user, 'school'):
        school = request.user.school
    elif hasattr(request.user, 'staff'):
        school = request.user.staff.school
    else:
        logger.error(f"No school assigned to user {request.user}")
        return HttpResponseForbidden("No school assigned to this account.")

    # 2️⃣ Get all sessions for this school
    sessions = AcademicSession.objects.filter(school=school).order_by("start_date")
    logger.debug(f"Found {sessions.count()} sessions for school {school}")

    session_data = []

    for session in sessions:
        start_date = session.start_date
        end_date = session.end_date

        # 📌 Total Students admitted in session
        total_students = StudentAdmission.objects.filter(
            student__school=school,
            admission_date__range=[start_date, end_date]
        ).count()

        # 📌 Total Fee Collected
        total_fee = MonthlyFee.objects.filter(
            student_admission__student__school=school,
            payment_date__range=[start_date, end_date]
        ).aggregate(Sum('received'))['received__sum'] or 0

        # 📌 Total Balance
        total_balance = MonthlyFee.objects.filter(
            student_admission__student__school=school,
            payment_date__range=[start_date, end_date]
        ).aggregate(Sum('current_balance'))['current_balance__sum'] or 0

        # 📌 Teacher Salaries
        total_teacher_salary = Teacher.objects.filter(
            school=school
        ).aggregate(Sum('salary'))['salary__sum'] or 0

        # 📌 Staff Salaries
        total_staff_salary = Staff.objects.filter(
            school=school
        ).aggregate(Sum('salary'))['salary__sum'] or 0

        # 📌 Expenses
        total_expenses = Expenses.objects.filter(
            school=school,
            payment_date__range=[start_date, end_date]
        ).aggregate(Sum('price'))['price__sum'] or 0

        # 📌 Assets Purchased (fixed: purchased_date)
        total_assets = Assets.objects.filter(
            school=school,
            purchased_date__range=[start_date, end_date]
        ).aggregate(Sum('value'))['value__sum'] or 0

        session_data.append({
            "session": session.session_name,
            "students": total_students,
            "fee_collected": total_fee,
            "balance": total_balance,
            "teacher_salaries": total_teacher_salary,
            "staff_salaries": total_staff_salary,
            "expenses": total_expenses,
            "assets": total_assets,
        })
        logger.debug(f"Session {session.session_name}: students={total_students}, expenses={total_expenses}, assets={total_assets}")

    # 3️⃣ Bar Chart
    # 3️⃣ Bar Chart
    df = pd.DataFrame(session_data)

    # Ensure numeric columns are float
    numeric_cols = ["fee_collected", "teacher_salaries", "staff_salaries", "expenses", "assets"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(float)

    bar_chart = px.bar(
        df,
        x="session",
        y=numeric_cols,
        barmode="group",
        title="Session Comparison (Financial Metrics)"
    )

    # 4️⃣ Pie Charts
    pie_charts = {}
    for data in session_data:
        pie_df = pd.DataFrame({
            "Category": ["Fee Collected", "Teacher Salaries", "Staff Salaries", "Expenses", "Assets"],
            "Amount": [
                data["fee_collected"],
                data["teacher_salaries"],
                data["staff_salaries"],
                data["expenses"],
                data["assets"]
            ]
        })
        pie_charts[data["session"]] = px.pie(
            pie_df,
            names="Category",
            values="Amount",
            title=f"Proportions in {data['session']}"
        )

    context = {
        "session_data": session_data,
        "bar_chart": bar_chart.to_html(full_html=False),
        "pie_charts": {k: v.to_html(full_html=False) for k, v in pie_charts.items()},
        "sessions": sessions
    }

    return render(request, "main_app/session_analytics.html", context)






@admin_required
def get_model_fields(request):
    model_name = request.GET.get('model_name')
    try:
        if model_name in ['MonthlyFee', 'StudentAdmission', 'Student', 'StudentResult', 'Assets', 'Expenses']:
            model = apps.get_model('main_app', model_name)
            fields = [field.name for field in model._meta.fields if not field.is_relation]
            return JsonResponse({'fields': fields})
        else:
            return JsonResponse({'fields': []})
    except LookupError:
        return JsonResponse({'error': 'Model not found'}, status=400)


 
@admin_required
def statistics_view(request):
    school = request.user.school
    active_session = AcademicSession.objects.filter(school=school, is_active=True).first()

    # Basic counts
    total_students = Student.objects.filter(school=school).count()
    new_admissions = StudentAdmission.objects.filter(student__school=school, academic_session=active_session).count()

    total_collected = MonthlyFee.objects.filter(student_admission__student__school=school).aggregate(
        total=Sum('received')
    )['total'] or Decimal('0.00')

    total_balance = MonthlyFee.objects.filter(student_admission__student__school=school).aggregate(
        total=Sum('current_balance')
    )['total'] or Decimal('0.00')

    teacher_salaries = Teacher.objects.filter(school=school).aggregate(
        total=Sum('salary')
    )['total'] or Decimal('0.00')

    staff_salaries = Staff.objects.filter(school=school).aggregate(
        total=Sum('salary')
    )['total'] or Decimal('0.00')

    total_expenses = Expenses.objects.filter(school=school).aggregate(
        total=Sum('price')
    )['total'] or Decimal('0.00')

    total_assets_value = Assets.objects.filter(school=school).aggregate(
        total=Sum('value')
    )['total'] or Decimal('0.00')

    # Summary per class (top bar summary)
    summary_qs = StudentAdmission.objects.filter(
        student__school=school,
        academic_session=active_session
    ).values('class_name').annotate(count=Count('student')).order_by('class_name')

    summary_data = list(summary_qs)

    # Plotly bar chart
    fig = px.bar(
        summary_data,
        x='class_name',
        y='count',
        title='Students by Class',
        labels={'class_name': 'Class', 'count': 'Student Count'},
        template='plotly_white',
        color='count'
    )
    plot_div = fig.to_html(full_html=False)

    # Context to template
    context = {
        "cards": [
            {"title": "Total Students", "value": total_students, "color": "primary"},
            {"title": "New Admissions", "value": new_admissions, "color": "success"},
            {"title": "Total Fee Collected", "value": f"Rs {total_collected:,.0f}", "color": "info"},
            {"title": "Total Student Balance", "value": f"Rs {total_balance:,.0f}", "color": "warning"},
            {"title": "Teacher Salaries", "value": f"Rs {teacher_salaries:,.0f}", "color": "secondary"},
            {"title": "Staff Salaries", "value": f"Rs {staff_salaries:,.0f}", "color": "dark"},
            {"title": "Total Expenses", "value": f"Rs {total_expenses:,.0f}", "color": "danger"},
            {"title": "Total Assets", "value": f"Rs {total_assets_value:,.0f}", "color": "primary"},
        ],
        "class_summary": summary_data,
        "plot_div": plot_div,
    }

    return render(request, 'main_app/statistics.html', context)


@admin_required
def download_backup(request):
    user_school = request.user.school
    sessions = AcademicSession.objects.filter(school=user_school)
    active_session = sessions.filter(is_active=True).first()
    selected_session_id = request.GET.get('session') or request.POST.get('session')
    format_choice = request.POST.get('format') or 'excel'
    preview = request.method == 'POST' and 'preview' in request.POST

    session = (
        AcademicSession.objects.filter(id=selected_session_id, school=user_school).first()
        if selected_session_id else active_session
    )

    models = {
        'Student': Student,
        'StudentAdmission': StudentAdmission,
        'MonthlyFee': MonthlyFee,
        'Expenses': Expenses,
        'Teacher': Teacher,
        'Staff': Staff,
        'Assets': Assets,
        'AcademicSession': AcademicSession,
        'StudentResult': StudentResult,
        'Transport': Transport,
    }

    model_dataframes = {}
    for name, model in models.items():
        qs = model.objects.all()

        # Try filtering by school field
        model_fields = [f.name for f in model._meta.get_fields()]
        if 'school' in model_fields:
            qs = qs.filter(school=user_school)
        elif 'student' in model_fields:
            qs = qs.filter(student__school=user_school)
        elif 'student_admission' in model_fields:
            qs = qs.filter(student_admission__student__school=user_school)
        elif 'teacher' in model_fields:
            qs = qs.filter(teacher__school=user_school)
        elif 'staff' in model_fields:
            qs = qs.filter(staff__school=user_school)
        elif 'transport' in model_fields:
            qs = qs.filter(transport__school=user_school)

        # Filter by session if possible
        if 'academic_session' in model_fields and session:
            qs = qs.filter(academic_session=session)
        elif 'session' in model_fields and session:
            qs = qs.filter(session=session)

        df = pd.DataFrame.from_records(qs.values())
        if not df.empty:
            df = df.copy()
            for col in df.select_dtypes(['datetimetz']).columns:
                df[col] = df[col].dt.tz_localize(None)
        model_dataframes[name] = df

    if preview:
        preview_data = {
            name: df.head(5).to_html(classes="table table-bordered table-sm table-striped", index=False)
            for name, df in model_dataframes.items() if not df.empty
        }
        return render(request, 'main_app/backup_preview.html', {
            'sessions': sessions,
            'active_session': session,
            'preview_data': preview_data,
            'format': format_choice,
        })

    # If download triggered
    if format_choice == 'excel':
        buffer = BytesIO()
        wb = Workbook()
        wb.remove(wb.active)
        for sheet_name, df in model_dataframes.items():
            ws = wb.create_sheet(title=sheet_name[:31])
            ws.append(df.columns.tolist())
            for row in df.itertuples(index=False):
                ws.append(list(row))
        wb.save(buffer)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{user_school.school_name}_backup_{session.session_name}.xlsx"'
        return response

    elif format_choice == 'csv':
        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, 'w', ZIP_DEFLATED) as zip_file:
            for sheet_name, df in model_dataframes.items():
                csv_stream = StringIO()
                df.to_csv(csv_stream, index=False)
                zip_file.writestr(f"{sheet_name}.csv", csv_stream.getvalue())
    
        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{user_school.school_name}_csv_backup_{session.session_name}.zip"'
        return response

    return HttpResponse("Unsupported format selected.", status=400)


