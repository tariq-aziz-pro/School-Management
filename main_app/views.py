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
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
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
from django.contrib.auth.forms import AuthenticationForm
import logging
from django.db.models.functions import Coalesce
from django.db.models import Value as V
from .view_helpers import bulk_promote_students, create_repeated_admission, get_previous_balance, calculate_overall_grade

from django.apps import apps
from plotly.offline import plot
import plotly.express as px
from django.http import JsonResponse
import pandas as pd
import io
from openpyxl import Workbook
from io import BytesIO, StringIO
from zipfile import ZipFile, ZIP_DEFLATED
from .permissions import (
    developer_required,
    get_active_session,
    is_developer,
    is_school_staff,
    is_student,
    is_teacher,
    portal_user_required,
    school_staff_or_teacher_required,
    school_staff_required,
    student_required,
    teacher_required,
)
from .view_parts.auth import (
    custom_login,
    register_school,
    developer_dashboard,
    toggle_school_access,
    reset_admin_password,
    add_payment,
    index,
)
from .view_parts.admissions import (
    student_user_create,
    student_list,
    student_dashboard,
    student_admission,
    edit_student,
    admission_success,
    generate_pdf,
    list_admissions,
)
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

import datetime
from decimal import Decimal
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.http import urlencode

from .models import AcademicSession, StudentAdmission, FeeStructure, MonthlyFee, CLASS_PROGRESSION
from .forms import PromoteExistingStudentForm
from .utils import get_pending_balance
logger = logging.getLogger(__name__)


@school_staff_required
def dashboard(request):
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
        ).select_related('student')

        stats = base_qs.aggregate(
            total_students=Count('id'),
            new_admissions=Count('id', filter=Q(promoted=False)),
            promoted_students=Count('id', filter=Q(promoted=True)),
            total_classes=Count('class_name', distinct=True),
            total_sections=Count('section', distinct=True),
        )

        balance_result = MonthlyFee.objects.filter(
            student_admission__academic_session=active_session,
            student_admission__student__school=request.user.school,
        ).aggregate(total=Sum('current_balance'))

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
                search_results = list(queryset.filter(student__student_id__iexact=search_value))
            elif search_type == 'contact':
                search_results = list(queryset.filter(student__contact__iexact=search_value))
            elif search_type == 'father_name':
                search_results = list(queryset.filter(student__father_guardian_name__iexact=search_value))

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
            'total_balance': balance_result['total'] or 0,
            'search_results': search_results,
            'search_type': search_type,
            'search_value': search_value,
        })

    return render(request, 'main_app/dashboard.html', context)


@school_staff_required
def create_academic_session(request):
    active_session = get_active_session(request)
    if request.method == 'POST':
        form = AcademicSessionForm(request.POST, request=request)
        if form.is_valid():
            session = form.save(commit=False)
            session.school = request.user.school
            session.save()
            messages.success(request, 'Academic session created successfully.')
            return redirect('create_session')
    else:
        form = AcademicSessionForm(request=request)

    sessions = AcademicSession.objects.filter(school=request.user.school).order_by('-start_date')
    return render(request, 'main_app/create_session.html', {'form': form, 'sessions': sessions, 'active_session': active_session})


@school_staff_required
def search_session(request):
    query = request.GET.get('q', '').strip()
    sessions = AcademicSession.objects.filter(school=request.user.school).order_by('-start_date')
    if query:
        sessions = sessions.filter(name__icontains=query)
    return render(request, 'main_app/search_session.html', {'sessions': sessions, 'query': query})


@school_staff_required
def add_fee_structure(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found. Please create an active session first.")
        return redirect('dashboard')

    if request.method == 'POST':
        form = FeeStructureForm(request.POST)
        if form.is_valid():
            class_name = form.cleaned_data['class_name']

            # Check if already exists
            if FeeStructure.objects.filter(
                academic_session=active_session, 
                class_name=class_name
            ).exists():
                messages.warning(request, f"Fee structure for {class_name} already exists for this session.")
                return redirect('add_fee_structure')

            fee_structure = form.save(commit=False)
            fee_structure.school = request.user.school
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

#------------------------Monthly Fee------------------------
@school_staff_required
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

@school_staff_required
def edit_fee_structure(request, fee_id):
    fee_structure = get_object_or_404(
        FeeStructure, 
        id=fee_id, 
        school=request.user.school          # ← Added security
    )

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

    

    current_date = datetime.datetime.now().strftime("%B %d, %Y %I:%M %p PKT")

    # ====================== SCHOOL LOGO (Only once) ======================
    if school and school.school_logo and os.path.exists(school.school_logo.path):
        try:
            logo = Image(school.school_logo.path, width=1.0*inch, height=1.0*inch)
            logo.hAlign = 'RIGHT'
            elements.append(logo)
        except Exception as e:
            logger.warning(f"School logo failed: {e}")
    else:
        elements.append(Paragraph("School Logo", normal_style))

    elements.append(Spacer(1, 0.1*inch))
    elements.append(Paragraph(f"<b>{school.school_name if school else 'School'}</b>", title_style))
    elements.append(Paragraph(f"Date: {current_date}", small_style))
    elements.append(Spacer(1, 0.4*inch))

    # ====================== STUDENT PHOTO ======================
    if student.image and os.path.exists(student.image.path):
        try:
            student_img = Image(student.image.path, width=1.7*inch, height=2.0*inch)
            student_img.hAlign = 'LEFT'
            elements.append(student_img)
        except Exception as e:
            logger.warning(f"Student image failed: {e}")
            elements.append(Paragraph("Student Photo: Not Available", normal_style))
    else:
        elements.append(Paragraph("Student Photo: Not Available", normal_style))

    elements.append(Spacer(1, 0.3*inch))

    # ====================== STUDENT DETAILS ======================
    details = [
        f"<b>Student ID:</b> {student.student_id}",
        f"<b>Name:</b> {student.first_name} {student.last_name}",
        f"<b>Father/Guardian:</b> {student.father_guardian_name}",
        f"<b>Class:</b> {admission.class_name} - {admission.section or 'N/A'}",
        f"<b>Roll No:</b> {admission.roll_number}",
        f"<b>Admission Date:</b> {admission.admission_date.strftime('%d %b %Y') if admission.admission_date else 'N/A'}",
    ]

    for line in details:
        elements.append(Paragraph(line, normal_style))
        elements.append(Spacer(1, 0.12*inch))

    # Fee Summary
    elements.append(Spacer(1, 0.25*inch))
    elements.append(Paragraph("<b>Fee Summary</b>", styles['Heading3']))

    fee_lines = [
        f"Total Dues: <b>Rs {admission.total_dues or 0:.2f}</b>",
        f"Received: Rs {admission.received or 0:.2f}",
        f"Balance: <b>Rs {admission.balance or 0:.2f}</b>",
    ]

    for line in fee_lines:
        elements.append(Paragraph(line, normal_style))
        elements.append(Spacer(1, 0.1*inch))

    elements.append(Spacer(1, 0.6*inch))
    elements.append(Paragraph("Powered By AliyarWeb Solutions", small_style))

    # Build PDF
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()

    filename = f"{student.first_name}_{student.last_name}_{admission.roll_number}.pdf".replace(" ", "_")

    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

@school_staff_required
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

@school_staff_required
def get_transport_details(request):
    option = request.GET.get('transport_option')
    if option == 'Paid':
        data = {'transport_fee': 800, 'note': 'Standard transport fee for local area'}
    elif option == 'Free':
        data = {'transport_fee': 0, 'note': 'No fee for staff children or nearby locality'}
    else:
        data = {'transport_fee': 0, 'note': 'No transport selected'}
    return JsonResponse(data)

#------------------------Monthly Fee------------------------
@school_staff_required
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

@school_staff_required
def monthly_fee_detail(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return render(request, 'main_app/monthly_fee_detail.html', {
            'student': None, 'month_data': [], 'active_session': None
        })

    student_admission = get_object_or_404(
        StudentAdmission.objects.select_related('student'),
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school
    )

    # Prefetch existing fees
    monthly_fees = MonthlyFee.objects.filter(student_admission=student_admission).order_by('year', 'month')
    monthly_fee_dict = {fee.month: fee for fee in monthly_fees}

    # Generate all months
    start_month_date = active_session.start_date + relativedelta(months=1)
    end_month_date = active_session.end_date
    all_months = []
    current_month_num = start_month_date.month
    current_year = start_month_date.year

    while True:
        month_name = datetime.datetime(1900, current_month_num, 1).strftime('%B')
        all_months.append((month_name, current_year))
        
        if current_month_num == end_month_date.month and current_year == end_month_date.year:
            break
        current_month_num += 1
        if current_month_num > 12:
            current_month_num = 1
            current_year += 1

    month_data = []
    last_current_balance = student_admission.balance or Decimal('0.00')

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
            
            previous_balance = last_current_balance
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
            
            form = MonthlyFeeForm(initial=initial_data, request=request)
            month_data.append({
                'month': month,
                'year': year,
                'fee': None,
                'form': form,
                'status': 'Pending',
                'disabled': False,
            })
            last_current_balance = total_dues
            student_admission.closing_balance = total_dues
            student_admission.save(update_fields=['closing_balance'])

    # ==================== POST Handling ====================
    if request.method == 'POST':
        post_month = request.POST.get('month')
        post_year = int(request.POST.get('year', current_year))

        existing_fee = MonthlyFee.objects.filter(
            student_admission=student_admission,
            month=post_month,
            year=post_year
        ).first()

        if existing_fee and not existing_fee.has_payment:
            form = MonthlyFeeForm(request.POST, instance=existing_fee, request=request)
        else:
            form = MonthlyFeeForm(request.POST, initial={'student_admission': student_admission}, request=request)

        if form.is_valid():
            try:
                monthly_fee = form.save(commit=False)
                monthly_fee.student_admission = student_admission
                monthly_fee.operator = request.user
                
                # === CRITICAL FIX ===
                if monthly_fee.received >= monthly_fee.total_dues:
                    monthly_fee.has_payment = True
                    monthly_fee.current_balance = Decimal('0.00')
                else:
                    monthly_fee.has_payment = False
                    monthly_fee.current_balance = monthly_fee.total_dues - monthly_fee.received
                
                monthly_fee.save()

                messages.success(request, f"Payment for {monthly_fee.month} {monthly_fee.year} saved successfully.")
                return redirect('monthly_fee_success', monthly_fee_id=monthly_fee.id)

            except Exception as e:
                logger.error("Error saving payment: %s", str(e))
                messages.error(request, f"Error saving payment: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")
            # Re-attach invalid form
            for data in month_data:
                if data.get('month') == post_month and data.get('year') == post_year:
                    data['form'] = form
                    break

    context = {
        'student': student_admission,
        'month_data': month_data,
        'active_session': active_session,
    }
    return render(request, 'main_app/monthly_fee_detail.html', context)

@school_staff_required
def monthly_fee_success(request, monthly_fee_id):
    monthly_fee = get_object_or_404(MonthlyFee, id=monthly_fee_id, student_admission__student__school=request.user.school)
    messages.success(request, f"Payment for {monthly_fee.month} {monthly_fee.year} was successfully saved!")
    return render(request, 'main_app/monthly_fee_success.html', {'monthly_fee': monthly_fee})

@school_staff_required
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


#-----------------------Promote offline studnets--------------------
@school_staff_required
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

@school_staff_required
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

@school_staff_required
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



# Helper to fetch previous balance (using your existing utility function)
# from .utils import get_previous_balance, calculate_total_dues, next_available_roll, validate_transport

@school_staff_required
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


@school_staff_required
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

    # Filter Logic
    class_choices = StudentAdmission.objects.filter(
        academic_session=previous_session,
        student__school=request.user.school,
        promoted=False  # Only show students not yet promoted
    ).values('class_name').distinct().order_by('class_name')
    class_choices = [c['class_name'] for c in class_choices]

    section_choices = StudentAdmission.objects.filter(
        academic_session=previous_session,
        student__school=request.user.school,
        promoted=False
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
            section=selected_section,
            promoted=False
        ).select_related('student').order_by('roll_number')

        # Annotate pending balance for display
        for student in students:
            student.pending_balance = get_pending_balance(student)

    admitted_student_ids = list(StudentAdmission.objects.filter(
        academic_session=active_session,
        student__school=request.user.school
    ).values_list('student__student_id', flat=True))

    return render(request, 'main_app/promote_existing_students.html', {
        'class_choices': class_choices,
        'section_choices': section_choices,
        'selected_class_name': selected_class_name,
        'selected_section': selected_section,
        'students': students,
        'no_previous': False,
        'active_session': active_session,
        'admitted_student_ids': admitted_student_ids,
    })


@school_staff_required
def bulk_promote_students_view(request):
    #Processes mass student choices using system rules from table form states."""
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('promote_existing_students')

    # Reads checkboxes from your grid layout: <input type="checkbox" name="admission_ids" value="ID">
    admission_ids = request.POST.getlist('admission_ids')
    target_section = request.POST.get('target_section', '').strip()

    if not admission_ids:
        messages.error(request, "No students selected for promotion.")
        return redirect('promote_existing_students')

    if not target_section:
        messages.error(request, "Target section is required for bulk promotion.")
        return redirect('promote_existing_students')

    success_count = 0
    
    # Run the mass block safely inside an atomic transaction scope
    try:
        with transaction.atomic():
            old_admissions = StudentAdmission.objects.select_for_update().filter(
                id__in=admission_ids,
                student__school=request.user.school
            )

            for old_admission in old_admissions:
                # Direct dynamic row matching checks
                roll_key = f'roll_number_{old_admission.id}'
                pass_key = f'is_passed_{old_admission.id}'
                
                roll_value = request.POST.get(roll_key)
                is_passed = request.POST.get(pass_key, 'true').lower() in ('1', 'true', 'yes', 'on')

                try:
                    target_roll = int(roll_value) if roll_value else old_admission.roll_number
                except (TypeError, ValueError):
                    raise ValueError(f"Invalid roll assignment input for {old_admission.student}")

                # Calculate class progress tier
                current_class = old_admission.class_name
                target_class = CLASS_PROGRESSION.get(current_class, current_class) if is_passed else current_class

                # Guard check for fee rules configurations
                try:
                    fee_struct = FeeStructure.objects.get(
                        academic_session=active_session,
                        class_name=target_class
                    )
                except FeeStructure.DoesNotExist:
                    raise ValueError(f"No configured fee structure found for {target_class} in current active session.")

                # Fee calculation logic matching single form structures
                promo_charge = fee_struct.promotion_fee if is_passed else Decimal('0.00')
                admission_session_dues = (
                    fee_struct.paper_money +
                    fee_struct.books_dues +
                    fee_struct.uniform_dues +
                    promo_charge +
                    (fee_struct.other_charges or Decimal('0.00'))
                )
                
                # Fetch balance from previous session
                prev_session_balance = get_previous_balance(old_admission, active_session)
                
                # Setup unified final balance targets
                total_session_dues = prev_session_balance + admission_session_dues + fee_struct.tuition_fee + (old_admission.transport_fee or Decimal('0.00'))

                # Build the new session record entry link
                new_admission = StudentAdmission.objects.create(
                    student=old_admission.student,
                    academic_session=active_session,
                    class_name=target_class,
                    section=target_section,
                    roll_number=target_roll,
                    admission_date=datetime.date.today(),
                    promoted=True,
                    failed_to_promote=not is_passed,
                    status=True,
                    operator=request.user,
                    
                    # Store applied structures layout values
                    admission_fee=Decimal('0.00'), # Standard for promotions
                    tuition_fee=fee_struct.tuition_fee,
                    exam_fee=fee_struct.paper_money,
                    book_fee=fee_struct.books_dues,
                    uniform_fee=fee_struct.uniform_dues,
                    promotion_fee=promo_charge,
                    other_fee=fee_struct.other_charges,
                    
                    # Retain student route choices
                    transport=old_admission.transport,
                    transport_fee=old_admission.transport_fee,
                    vehicle_no=old_admission.vehicle_no,
                    route=old_admission.route,
                    driver_contact=old_admission.driver_contact,
                    
                    previous_balance=prev_session_balance,
                    discount=old_admission.discount or Decimal('0.00'),
                    discount_behalf=old_admission.discount_behalf,
                    received=Decimal('0.00'),
                    total_dues=total_session_dues,
                    balance=total_session_dues
                )

                # Initialize billing logs for the month
                MonthlyFee.objects.create(
                    student_admission=new_admission,
                    month=active_session.start_date.strftime('%B'),
                    year=active_session.start_date.year,
                    previous_balance=prev_session_balance,
                    monthly_fee=new_admission.tuition_fee or Decimal('0.00'),
                    transport_fee=new_admission.transport_fee or Decimal('0.00'),
                    total_dues=(prev_session_balance + (new_admission.tuition_fee or Decimal('0.00')) + (new_admission.transport_fee or Decimal('0.00'))),
                    received=Decimal('0.00'),
                    current_balance=(prev_session_balance + (new_admission.tuition_fee or Decimal('0.00')) + (new_admission.transport_fee or Decimal('0.00'))),
                    operator=request.user
                )

                # Flag the historical record state
                if is_passed:
                    old_admission.promoted = True
                    old_admission.failed_to_promote = False
                else:
                    old_admission.promoted = False
                    old_admission.failed_to_promote = True
                old_admission.status = False
                old_admission.save()

                success_count += 1
                
        messages.success(request, f"Successfully processed promotions for {success_count} students.")
    except Exception as e:
        messages.error(request, f"Bulk promotion aborted entirely due to error: {str(e)}")

    return redirect('promote_existing_students')


@school_staff_required
def promote_existing_student(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('promote_existing_students')

    previous_session = AcademicSession.objects.filter(
        school=request.user.school,
        is_active=False,
        end_date__lt=active_session.start_date
    ).order_by('-end_date').first()

    if not previous_session:
        messages.error(request, "No previous session found to promote from.")
        return redirect('promote_existing_students')

    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        academic_session=previous_session,
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

    form = PromoteExistingStudentForm(
        request.POST or None,
        request.FILES or None,
        student_admission=student_admission,
        request=request,
        filtered_class=filtered_class,
        filtered_section=filtered_section
    )

    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                new_admission = form.save(commit=True)

                messages.success(request, f"{student_admission.student.first_name} has been successfully promoted!")

                query_params = urlencode({
                    'class_name': filtered_class,
                    'section': filtered_section
                })
                return redirect(f"{reverse('promote_existing_students')}?{query_params}")

        except Exception as e:
            messages.error(request, f"Promotion failed: {str(e)}")

    # Fee structures for JS dynamic loading
    fee_structures = FeeStructure.objects.filter(
        academic_session=active_session
    ).values(
        'class_name', 'tuition_fee', 'paper_money', 'books_dues',
        'uniform_dues', 'other_charges', 'promotion_fee'
    )

    fee_structure_dict = {
        fs['class_name']: {
            'tuition_fee': str(fs['tuition_fee']),
            'exam_fee': str(fs['paper_money']),
            'book_fee': str(fs['books_dues']),
            'uniform_fee': str(fs['uniform_dues']),
            'other_fee': str(fs.get('other_charges') or '0.00'),
            'promotion_fee': str(fs.get('promotion_fee') or '0.00')
        } for fs in fee_structures
    }

    return render(request, 'main_app/promote_existing_student_form.html', {
        'form': form,
        'student': student_admission,
        'current_class': student_admission.class_name,
        'fee_structures': fee_structure_dict,
        'filtered_class': filtered_class,
        'filtered_section': filtered_section,
        'active_session': active_session,
    })

@school_staff_required
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


@school_staff_required
def mark_not_promoted(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('promote_existing_students')
    # operate on the previous session admission
    previous_session = AcademicSession.objects.filter(
        school=request.user.school,
        is_active=False,
        end_date__lt=active_session.start_date
    ).order_by('-end_date').first()

    if not previous_session:
        messages.error(request, "No previous session found to mark not promoted.")
        return redirect('promote_existing_students')

    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id=student_id,
        academic_session=previous_session,
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

@school_staff_required
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



#----------------------Teacher---------------------------    
@school_staff_required
def teacher_list(request):
    teachers = Teacher.objects.filter(
        school=request.user.school
    ).select_related('user').order_by('name')

    # Pagination
    paginator = Paginator(teachers, 30)
    page_number = request.GET.get('page')
    try:
        teachers_page = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        teachers_page = paginator.get_page(1)

    context = {
        'teachers': teachers_page,
        'page_obj': teachers_page,
        'paginator': paginator,
    }

    return render(request, 'main_app/teacher_list.html', context)

@school_staff_required
def teacher_create(request):
   
    if request.method == 'POST':
        form = TeacherForm(data=request.POST, request=request)   # Better way
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Teacher created successfully.")
                logger.info("Teacher created: %s", form.cleaned_data.get('name'))
                return redirect('teacher_list')
            except Exception as e:
                logger.error("Error creating teacher: %s", str(e))
                messages.error(request, f"Error creating teacher: {str(e)}")
        else:
            logger.warning("Teacher form invalid: %s", form.errors)
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = TeacherForm(request=request)   # GET request

    return render(request, 'main_app/teacher_form.html', {
        'form': form, 
        'action': 'Create'
    })

@school_staff_required
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

@school_staff_required
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


#-------------------------Teacher Dashboard--------------------------------
@teacher_required
def teacher_dashboard(request):
    teacher = get_object_or_404(
        Teacher.objects.select_related('user', 'school'),
        user=request.user,
        school=request.user.school
    )
    
    active_session = AcademicSession.objects.filter(
        school=request.user.school, 
        is_active=True
    ).first()

    if not active_session:
        context = {
            'teacher': teacher,
            'active_session': None,
        }
        return render(request, 'main_app/teacher_dashboard.html', context)

    # Get all students in teacher's class/section
    students_qs = StudentAdmission.objects.filter(
        student__school=teacher.school,
        class_name=teacher.class_name,
        section=teacher.section,
        academic_session=active_session
    ).select_related('student')

    total_students = students_qs.count()

    # Results - Only teacher's subjects
    results_qs = StudentResult.objects.filter(
        student_admission__in=students_qs,
        subject__in=teacher.subjects.all()
    ).select_related('student_admission__student', 'subject')

    # Apply filters
    exam_type = request.GET.get('exam_type', '')
    subject_id = request.GET.get('subject', '')

    if exam_type:
        results_qs = results_qs.filter(exam_type=exam_type)
    if subject_id:
        results_qs = results_qs.filter(subject_id=subject_id)

    results = results_qs.order_by(
        'student_admission__student__first_name', 
        'subject__name', 
        'exam_type'
    )

    # Upcoming Events
    events = Events.objects.filter(
        school=teacher.school,
        event_for__in=['All', 'Teacher']
    ).order_by('event_date')[:8]

    context = {
        'teacher': teacher,
        'active_session': active_session,
        'total_students': total_students,
        'results': results,
        'subjects': teacher.subjects.all(),
        'exam_types': StudentResult.EXAM_TYPE_CHOICES,   # or get dynamic
        'exam_type': exam_type,
        'subject_id': subject_id,
        'events': events,
        
        # Extra useful stats
        'total_results': results.count(),
        'recent_results': results[:10],   # Already slicing in template, but good to have
    }
    
    return render(request, 'main_app/teacher_dashboard.html', context)

#-------------------------Result---------------------------------
@teacher_required
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


@teacher_required
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


@teacher_required
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

#------------------------------Subjects---------------------------
@school_staff_or_teacher_required
def subject_list(request):
    if getattr(request.user, 'user_type', None) == 4:  # Teacher
        # Show only subjects assigned to this teacher
        subjects = request.user.teacher.subjects.all().order_by('name')
        
        context = {
            'subjects': subjects,
            'is_teacher': True,
            'page_title': 'My Subjects'
        }
    else:  # Admin / Staff
        subjects = Subject.objects.filter(
            school=request.user.school
        ).order_by('name').prefetch_related('teacher_set')
        
        context = {
            'subjects': subjects,
            'is_teacher': False,
            'page_title': 'All Subjects'
        }
    
    return render(request, 'main_app/subject_list.html', context)

@school_staff_or_teacher_required
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


@school_staff_or_teacher_required
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



@school_staff_or_teacher_required
def subject_delete(request, pk):
    
    subject = get_object_or_404(Subject, pk=pk, school=request.user.school)
    if request.method == 'POST':
        subject.delete()
        messages.success(request, "Subject deleted successfully.")
        return redirect('subject_list')
    
    messages.error(request, "Invalid request.")
    return redirect('subject_list')




#=====================Syllabus================================

@school_staff_or_teacher_required
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

@portal_user_required
def syllabus_list(request):
    """Staff, teachers, and students: syllabus filtered by role."""
    # Filter syllabus based on user type
    if is_student(request.user):
        try:
            student = Student.objects.get(student_id=request.user.username)
            admission = StudentAdmission.objects.filter(
                student=student,
                academic_session__is_active=True
            ).first()
            
            if admission:
                syllabi = Syllabus.objects.filter(
                    school=request.user.school,
                    class_name=admission.class_name,
                    academic_session=admission.academic_session
                ).select_related('subject', 'uploaded_by')
            else:
                syllabi = Syllabus.objects.none()
                
        except Student.DoesNotExist:
            syllabi = Syllabus.objects.none()

    else:
        syllabi = Syllabus.objects.filter(
            school=request.user.school
        ).select_related('subject', 'uploaded_by')

    context = {
        'syllabus_list': syllabi,
        'is_student': is_student(request.user),
    }
    
    return render(request, 'main_app/syllabus_list.html', context)


@school_staff_or_teacher_required
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


@school_staff_or_teacher_required
def delete_syllabus(request, syllabus_id):
    syllabus = get_object_or_404(Syllabus, id=syllabus_id, school=request.user.school)


    if request.method == 'POST':
        syllabus.delete()
        messages.success(request, "Syllabus deleted successfully.")
        return redirect('syllabus_list')

    messages.error(request, "Invalid request method.")
    return redirect('syllabus_list')




@student_required
def generate_results_pdf(request):
    try:
        student = Student.objects.get(student_id=request.user.username)
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found.")
        logger.error(f"No Student found for username: {request.user.username}")
        return redirect('student_dashboard')

    if student.school_id != request.user.school_id:
        messages.error(request, "Invalid student account for this school.")
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


#----------------------------Staff-------------------------------

@school_staff_required
def staff_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    staff = Staff.objects.filter(school=request.user.school)
    return render(request, 'main_app/staff_list.html', {'staff': staff})  # Updated path

@school_staff_required
def staff_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    staff = get_object_or_404(Staff, pk=pk, school=request.user.school)
    return render(request, 'main_app/staff_detail.html', {'staff': staff})  # Updated path

@school_staff_required
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

@school_staff_required
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

@school_staff_required
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


#--------------------------Assets-------------------------------------
@school_staff_required
def assets_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not AcademicSession.objects.filter(school=request.user.school, is_active=True).exists():
        messages.error(request, "No active academic session found.")
        return render(request, 'main_app/no_session.html')
    assets = Assets.objects.filter(school=request.user.school)
    return render(request, 'main_app/assets_list.html', {'assets': assets})

@school_staff_required
def assets_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not AcademicSession.objects.filter(school=request.user.school, is_active=True).exists():
        messages.error(request, "No active academic session found.")
        return render(request, 'main_app/no_session.html')
    asset = get_object_or_404(Assets, pk=pk, school=request.user.school)
    return render(request, 'main_app/assets_detail.html', {'asset': asset})

@school_staff_required
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

@school_staff_required
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

@school_staff_required
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


#----------------------------Expenses---------------------------------

@school_staff_required
def expenses_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    expenses = Expenses.objects.filter(school=request.user.school)
    return render(request, 'main_app/expenses_list.html', {'expenses': expenses})

@school_staff_required
def expenses_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    expense = get_object_or_404(Expenses, pk=pk, school=request.user.school)
    return render(request, 'main_app/expenses_detail.html', {'expense': expense})

@school_staff_required
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

@school_staff_required
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

@school_staff_required
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

#--------------------------Taransport--------------------------------------
@school_staff_required
def transport_list(request):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    transports = Transport.objects.filter(school=request.user.school)
    return render(request, 'main_app/transport_list.html', {'transports': transports})

@school_staff_required
def transport_detail(request, pk):
    if not request.user.school:
        messages.error(request, "You are not associated with any school.")
        return redirect('dashboard')
    if not get_active_session(request):
        return render(request, 'main_app/no_session.html')
    transport = get_object_or_404(Transport, pk=pk, school=request.user.school)
    return render(request, 'main_app/transport_detail.html', {'transport': transport})

@school_staff_required
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

@school_staff_required
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

@school_staff_required
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




#---------------------------------Events--------------------------

# ==================== EVENTS LIST ====================
@portal_user_required
def events_list(request):
    # Filter events based on user type
    if is_student(request.user):
        events = Events.objects.filter(
            school=request.user.school,
            event_for__in=['All', 'Students'],
        ).order_by('-event_date')
    else:
        events = Events.objects.filter(school=request.user.school).order_by('-event_date')

    context = {
        'events': events,
        'is_student': is_student(request.user),
    }
    return render(request, 'main_app/events_list.html', context)


# ==================== CREATE EVENT ====================
@school_staff_or_teacher_required
def events_create(request):
    if request.method == 'POST':
        form = EventsForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Event created successfully.")
            return redirect('events_list')
    else:
        form = EventsForm(user=request.user)

    return render(request, 'main_app/events_form.html', {
        'form': form, 
        'title': 'Add New Event'
    })


# ==================== UPDATE EVENT ====================
@school_staff_or_teacher_required
def events_update(request, pk):
    event = get_object_or_404(Events, pk=pk, school=request.user.school)
    
    if request.method == 'POST':
        form = EventsForm(request.POST, instance=event, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Event updated successfully.")
            return redirect('events_list')
    else:
        form = EventsForm(instance=event, user=request.user)

    return render(request, 'main_app/events_form.html', {
        'form': form, 
        'title': 'Edit Event'
    })


# ==================== DELETE EVENT (Admin Only) ====================
@school_staff_required
def events_delete(request, pk):
    event = get_object_or_404(Events, pk=pk, school=request.user.school)
    
    if request.method == 'POST':
        event.delete()
        messages.success(request, "Event deleted successfully.")
        return redirect('events_list')
    
    return render(request, 'main_app/events_confirm_delete.html', {'event': event})


# Detail View (Optional - keep as is or simplify)
@portal_user_required
def events_detail(request, pk):
    event = get_object_or_404(Events, pk=pk, school=request.user.school)
    if is_student(request.user) and event.event_for not in ('All', 'Students'):
        messages.error(request, "You do not have access to this event.")
        return redirect('events_list')
    return render(request, 'main_app/events_detail.html', {'event': event})



#-------------------------Alnalytics---------------------------------

@school_staff_required
def analytics(request):
    chart_html = None
    form = AnalyticsForm(request.POST or None, request=request)
    
    user_school = request.user.school
    selected_session_id = request.GET.get('session')
    
    if selected_session_id:
        active_session = AcademicSession.objects.filter(
            id=selected_session_id, school=user_school
        ).first()
    else:
        active_session = AcademicSession.objects.filter(
            is_active=True, school=user_school
        ).first()

    all_sessions = AcademicSession.objects.filter(school=user_school).order_by('-start_date')

    # ====================== PRE-COMPUTED CHARTS (Fast) ======================
    
    # 1. Pie Chart - Student Distribution
    student_qs = StudentAdmission.objects.filter(student__school=user_school)
    if active_session:
        student_qs = student_qs.filter(academic_session=active_session)

    pie_df = pd.DataFrame(list(
        student_qs.values('class_name').annotate(count=Count('id')).order_by('class_name')
    ))

    pie_chart = px.pie(
        pie_df, names='class_name', values='count',
        title='🎓 Student Distribution by Class', hole=0.4
    ).to_html() if not pie_df.empty else None

    # 2. Financial Bar Chart
    teacher_salary = Teacher.objects.filter(school=user_school).aggregate(total=Sum('salary'))['total'] or 0
    staff_salary = Staff.objects.filter(school=user_school).aggregate(total=Sum('salary'))['total'] or 0

    expenses_qs = Expenses.objects.filter(school=user_school)
    if active_session:
        expenses_qs = expenses_qs.filter(payment_date__range=(active_session.start_date, active_session.end_date))
    total_expenses = expenses_qs.aggregate(total=Sum('price'))['total'] or 0

    monthly_qs = MonthlyFee.objects.filter(student_admission__student__school=user_school)
    if active_session:
        monthly_qs = monthly_qs.filter(student_admission__academic_session=active_session)

    fee_stats = monthly_qs.aggregate(
        received=Sum('received'),
        balance=Sum('current_balance')
    )

    bar_df = pd.DataFrame({
        "Category": ["Expenses", "Teacher Salaries", "Staff Salaries", "Fee Collected", "Balance"],
        "Amount": [float(total_expenses), float(teacher_salary), float(staff_salary),
                   float(fee_stats['received'] or 0), float(fee_stats['balance'] or 0)]
    })

    bar_chart = px.bar(bar_df, x="Category", y="Amount", title="💰 Financial Overview", 
                       color="Category", text="Amount").to_html()

    # 3. Trend Chart (Monthly Fee)
    trend_chart = None
    if active_session:
        trend_qs = monthly_qs.values('payment_date').annotate(total=Sum('received')).order_by('payment_date')
        trend_df = pd.DataFrame(list(trend_qs))
        if not trend_df.empty:
            trend_df['payment_date'] = pd.to_datetime(trend_df['payment_date'])
            trend_chart = px.line(trend_df, x='payment_date', y='total', 
                                title="📈 Monthly Fee Collection Trend", markers=True).to_html()

    # ====================== INTERACTIVE CHART (Safely) ======================
    if request.method == 'POST' and form.is_valid():
        try:
            model_name = form.cleaned_data['model_name']
            x_axis = form.cleaned_data['x_axis']
            y_axis = form.cleaned_data['y_axis']
            chart_type = form.cleaned_data['chart_type']

            model = apps.get_model('main_app', model_name)
            queryset = model.objects.all()

            # Safe filtering
            if hasattr(model, 'school'):
                queryset = queryset.filter(school=user_school)
            elif hasattr(model, 'student_admission'):
                queryset = queryset.filter(student_admission__student__school=user_school)
            elif hasattr(model, 'student'):
                queryset = queryset.filter(student__school=user_school)

            if active_session and hasattr(model, 'academic_session'):
                queryset = queryset.filter(academic_session=active_session)

            # Get only requested fields safely
            df = pd.DataFrame.from_records(
                queryset.values(x_axis, y_axis)
            ).dropna()

            if df.empty:
                chart_html = "<p class='alert alert-warning'>No data available for the selected fields.</p>"
            elif df[x_axis].nunique() <= 1:
                chart_html = "<p class='alert alert-warning'>Not enough variation in X-axis to generate chart.</p>"
            else:
                if chart_type == 'bar':
                    fig = px.bar(df, x=x_axis, y=y_axis)
                elif chart_type == 'line':
                    fig = px.line(df, x=x_axis, y=y_axis)
                else:
                    fig = px.scatter(df, x=x_axis, y=y_axis)

                chart_html = fig.to_html()

        except Exception as e:
            chart_html = f"<p class='alert alert-danger'>Error generating chart: {str(e)}</p>"

    return render(request, 'main_app/analytics.html', {
        'form': form,
        'chart': chart_html,
        'pie_chart': pie_chart,
        'bar_chart': bar_chart,
        'trend_chart': trend_chart,
        'all_sessions': all_sessions,
        'selected_session': active_session,
    })


@school_staff_required
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






@school_staff_required
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

#----------------------------Statistics--------------------------------
 
@school_staff_required
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

#-------------------------Backup----------------------------------
@school_staff_required
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


