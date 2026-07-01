from django.shortcuts import render, redirect, get_object_or_404

from django.contrib import messages

from django.db import transaction
from django.core.paginator import Paginator

from django.conf import settings
from .forms import AcademicSessionForm, FeeStructureForm, MonthlyFeeForm, PromoteExistingStudentForm, NotPromoteStudentForm, EditStudentForm, SchoolSubscriptionForm, TeacherForm, StudentResultForm, SubjectForm, StudentUserForm, SyllabusForm, StaffForm, AssetsForm,ExpensesForm, TransportForm, EventsForm, AnalyticsForm
from .models import AcademicSession, FeeStructure, CLASS_CHOICES, SECTION_CHOICES, StudentAdmission, MonthlyFee, Student, School, SchoolSubscription, StudentResult, Subject, Teacher, TemporaryPassword, Syllabus, Announcement, Staff, Assets, Expenses, Transport, Events, CLASS_PROGRESSION
import uuid
import os
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET
from decimal import Decimal, InvalidOperation
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
from django.db.models import Q, Sum, Count, F

from django.urls import reverse
from urllib.parse import urlencode
import json

import logging
from django.db.models.functions import TruncMonth
from django.db.models import Value as V
from django.db.models import Avg, ExpressionWrapper, FloatField
from .view_helpers import calculate_overall_grade

from django.apps import apps
from plotly.offline import plot
import plotly.express as px
import plotly.graph_objects as go
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
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.http import urlencode

from .models import AcademicSession, StudentAdmission, FeeStructure, MonthlyFee, CLASS_PROGRESSION, Period, Timetable, Subject, Teacher, StudentResult
from .forms import PromoteExistingStudentForm
from .utils import (
    create_monthly_fees_for_student,
    generate_monthly_fee_months,
    get_active_session,
    get_pending_balance,
    recalculate_monthly_chain,
    create_repeat_admission,
    annotate_outstanding,
)
logger = logging.getLogger(__name__)



# ──────────────────────────────────────────────────────────────
# views.py  ── Timetable feature (3 views)
#
#   period_manage    → create/edit/delete school-wide Period slots
#   timetable_view   → select class+section, show editable grid
#   timetable_save   → POST handler for saving one period slot
# ──────────────────────────────────────────────────────────────
# ── Period Management ─────────────────────────────────────────
 
@school_staff_required
def period_manage(request):
    """
    Create, edit, and delete school-wide Period slots.
    Periods are shared across all class/section timetables.
    """
    school = request.user.school
    periods = Period.objects.filter(school=school).order_by('order')
 
    if request.method == 'POST':
        action = request.POST.get('action')
 
        # ── Delete ────────────────────────────────────────────
        if action == 'delete':
            period_id = request.POST.get('period_id')
            period = get_object_or_404(Period, id=period_id, school=school)
            slot_count = period.timetable_slots.count()
            if slot_count > 0:
                messages.error(
                    request,
                    f"Cannot delete '{period.name}' — it is used in {slot_count} timetable slot(s). "
                    f"Remove those assignments first."
                )
            else:
                period.delete()
                messages.success(request, f"Period '{period.name}' deleted.")
            return redirect('period_manage')
 
        # ── Create or Update ──────────────────────────────────
        period_id  = request.POST.get('period_id')   # set on edit, empty on create
        name       = request.POST.get('name', '').strip()
        start_time = request.POST.get('start_time', '').strip()
        end_time   = request.POST.get('end_time', '').strip()
        order      = request.POST.get('order', '1').strip()
        is_break   = request.POST.get('is_break') == 'on'
 
        errors = []
        if not name:
            errors.append("Period name is required.")
        if not start_time:
            errors.append("Start time is required.")
        if not end_time:
            errors.append("End time is required.")
        if start_time and end_time and start_time >= end_time:
            errors.append("End time must be after start time.")
 
        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('period_manage')
 
        try:
            order = int(order)
        except ValueError:
            order = periods.count() + 1
 
        if period_id:
            # Edit existing
            period = get_object_or_404(Period, id=period_id, school=school)
            period.name       = name
            period.start_time = start_time
            period.end_time   = end_time
            period.order      = order
            period.is_break   = is_break
            period.save()
            messages.success(request, f"Period '{name}' updated.")
        else:
            # Create new
            if Period.objects.filter(school=school, name=name).exists():
                messages.error(request, f"A period named '{name}' already exists.")
                return redirect('period_manage')
            Period.objects.create(
                school     = school,
                name       = name,
                start_time = start_time,
                end_time   = end_time,
                order      = order,
                is_break   = is_break,
            )
            messages.success(request, f"Period '{name}' created.")
 
        return redirect('period_manage')
 
    return render(request, 'main_app/period_manage.html', {
        'periods': periods,
        'school':  school,
    })
 
 
# ── Timetable Grid View ───────────────────────────────────────
 
@school_staff_required
def timetable_view(request):
    """
    Shows an editable timetable grid for a selected class + section.
    Each row is a Period; each cell has Subject + Teacher dropdowns.
 
    Teacher conflict warnings are computed here (soft — displayed,
    not blocking).
    """
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
 
    school   = request.user.school
    periods  = Period.objects.filter(school=school).order_by('order')
 
    # Class/section choices from existing admissions in this session
    from .models import StudentAdmission
    from django.db.models import Count
    class_section_qs = (
        StudentAdmission.objects
        .filter(academic_session=active_session, student__school=school)
        .values('class_name', 'section')
        .distinct()
        .order_by('class_name', 'section')
    )
    class_sections = list(class_section_qs)
 
    selected_class   = request.GET.get('class_name', '')
    selected_section = request.GET.get('section', '')
 
    grid        = []
    conflicts   = []
    slot_map    = {}
 
    if selected_class and selected_section:
        # Existing timetable slots for this class/section/session
        existing_slots = Timetable.objects.filter(
            school           = school,
            academic_session = active_session,
            class_name       = selected_class,
            section          = selected_section,
        ).select_related('period', 'subject', 'teacher')
 
        slot_map = {slot.period_id: slot for slot in existing_slots}
 
        # Available subjects and teachers for JS dropdowns
        subjects = Subject.objects.filter(school=school).order_by('name')
        teachers = Teacher.objects.filter(school=school).order_by('name')
 
        # Teacher conflict detection:
        # Find teachers already assigned in this session (any class/section)
        # keyed by period_id → set of teacher_ids
        all_slots = Timetable.objects.filter(
            school           = school,
            academic_session = active_session,
        ).exclude(
            class_name = selected_class,
            section    = selected_section,
        ).select_related('teacher', 'period')
 
        teacher_period_map = {}   # period_id → [(teacher_id, class, section)]
        for s in all_slots:
            if s.teacher_id:
                teacher_period_map.setdefault(s.period_id, []).append({
                    'teacher_id':  s.teacher_id,
                    'teacher_name': s.teacher.name,
                    'class_name':  s.class_name,
                    'section':     s.section,
                })
 
        # Build grid rows
        for period in periods:
            slot = slot_map.get(period.id)
 
            # Conflict check for this period
            period_conflicts = []
            if slot and slot.teacher_id:
                for other in teacher_period_map.get(period.id, []):
                    if other['teacher_id'] == slot.teacher_id:
                        period_conflicts.append(
                            f"{slot.teacher.name} is also assigned to "
                            f"{other['class_name']} - {other['section']} in {period.name}."
                        )
                        conflicts.extend(period_conflicts)
 
            grid.append({
                'period':           period,
                'slot':             slot,
                'slot_subject_id':  slot.subject_id  if slot else None,
                'slot_teacher_id':  slot.teacher_id  if slot else None,
                'conflicts':        period_conflicts,
            })
 
        # Serialize subjects/teachers for JS dynamic filtering
        subjects_json = json.dumps([
            {'id': s.id, 'name': s.name}
            for s in subjects
        ])
        teachers_json = json.dumps([
            {
                'id':       t.id,
                'name':     t.name,
                'subjects': list(t.subjects.values_list('id', flat=True)),
            }
            for t in teachers
        ])
    else:
        subjects_json = '[]'
        teachers_json = '[]'
        subjects      = []
        teachers      = []
 
    return render(request, 'main_app/timetable_view.html', {
        'active_session':  active_session,
        'school':          school,
        'periods':         periods,
        'class_sections':  class_sections,
        'selected_class':  selected_class,
        'selected_section': selected_section,
        'grid':            grid,
        'conflicts':       conflicts,
        'subjects_json':   subjects_json,
        'teachers_json':   teachers_json,
    })
 
 
# ── Timetable Save (single slot POST) ────────────────────────
 
@school_staff_required
def timetable_save(request):
    """
    Saves or clears a single timetable slot (one period in one
    class/section). Called via form POST from the grid.
 
    POST fields:
        class_name, section, period_id, subject_id, teacher_id
        (subject_id / teacher_id can be empty → clears the slot)
    """
    if request.method != 'POST':
        return redirect('timetable_view')
 
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('timetable_view')
 
    school = request.user.school
 
    class_name  = request.POST.get('class_name', '').strip()
    section     = request.POST.get('section', '').strip()
    period_id   = request.POST.get('period_id', '').strip()
    subject_id  = request.POST.get('subject_id', '').strip() or None
    teacher_id  = request.POST.get('teacher_id', '').strip() or None
 
    if not all([class_name, section, period_id]):
        messages.error(request, "Missing required fields.")
        return redirect(f"{request.META.get('HTTP_REFERER', 'timetable_view')}")
 
    period  = get_object_or_404(Period, id=period_id, school=school)
    subject = get_object_or_404(Subject, id=subject_id, school=school) if subject_id else None
    teacher = get_object_or_404(Teacher, id=teacher_id, school=school) if teacher_id else None
 
    try:
        with transaction.atomic():
            if subject is None and teacher is None:
                # Clear the slot
                Timetable.objects.filter(
                    school           = school,
                    academic_session = active_session,
                    class_name       = class_name,
                    section          = section,
                    period           = period,
                ).delete()
                messages.success(request, f"{period.name} cleared.")
            else:
                slot, created = Timetable.objects.update_or_create(
                    school           = school,
                    academic_session = active_session,
                    class_name       = class_name,
                    section          = section,
                    period           = period,
                    defaults={
                        'subject':  subject,
                        'teacher':  teacher,
                        'operator': request.user,
                    },
                )
                action = "saved" if created else "updated"
                messages.success(request, f"{period.name} {action}.")
 
    except Exception as exc:
        logger.error("Timetable save error: %s", exc, exc_info=True)
        messages.error(request, f"Error saving timetable: {exc}")
 
    return redirect(f"{reverse('timetable_view')}?class_name={class_name}&section={section}")





@school_staff_required
def dashboard(request):
    active_session = get_active_session(request)
    context = {
        'session_exists': AcademicSession.objects.filter(school=request.user.school).exists(),
        'active_session':  active_session,
    }

    if active_session:
        base_qs = StudentAdmission.objects.filter(
            academic_session = active_session,
            student__school  = request.user.school,
            status            = True,
        ).select_related('student')

        # === FIX: split new admissions from not-promoted (repeat) students ===
        stats = base_qs.aggregate(
            total_students       = Count('id'),
            new_admissions       = Count('id', filter=Q(promoted_from__isnull=True)),
            promoted_students    = Count('id', filter=Q(promoted=True)),
            not_promoted_students = Count('id', filter=Q(promoted_from__isnull=False, promoted=False)),
            total_classes        = Count('class_name', distinct=True),
            total_sections       = Count('section', distinct=True),
        )

        # === FIX: outstanding balance via latest-month subquery, not Sum() ===
        outstanding_qs = annotate_outstanding(base_qs)
        total_balance = outstanding_qs.aggregate(total=Sum('outstanding'))['total'] or 0

        # === NEW: Fee Collection Rate ===
        # total_billed  = one-time admission charges + every month's (monthly_fee + transport_fee)
        # total_received = one-time admission received + every month's received
        # These are GROSS totals (not running balances), so summing across
        # all months is correct here — unlike current_balance, monthly_fee/
        # transport_fee/received are NOT cumulative, they're per-month amounts.
        admission_totals = base_qs.aggregate(
            admission_billed   = Sum('total_dues'),
            admission_received = Sum('received'),
        )
        monthly_totals = MonthlyFee.objects.filter(
            student_admission__academic_session = active_session,
            student_admission__student__school   = request.user.school,
        ).aggregate(
            monthly_billed   = Sum(F('monthly_fee') + F('transport_fee')),
            monthly_received = Sum('received'),
        )

        total_billed = (admission_totals['admission_billed'] or 0) + (monthly_totals['monthly_billed'] or 0)
        total_received = (admission_totals['admission_received'] or 0) + (monthly_totals['monthly_received'] or 0)
        collection_rate = round((total_received / total_billed * 100), 1) if total_billed > 0 else 0.0

        # === NEW: Top 5 outstanding balances ===
        top_outstanding = (
            outstanding_qs
            .filter(outstanding__gt=0)
            .order_by('-outstanding')[:5]
        )

        context.update({
            'total_students':        stats['total_students'],
            'new_admissions':        stats['new_admissions'],
            'promoted_students':     stats['promoted_students'],
            'not_promoted_students': stats['not_promoted_students'],
            'total_classes':         stats['total_classes'],
            'total_sections':        stats['total_sections'],
            'total_balance':         total_balance,
            'collection_rate':       collection_rate,
            'total_billed':          total_billed,
            'total_received':        total_received,
            'top_outstanding':       top_outstanding,
        })

    return render(request, 'main_app/dashboard.html', context)



# ── Student Directory (list) ──────────────────────────────────
 
@school_staff_required
def student_directory(request):
    active_session = get_active_session(request)
    if not active_session:
        return render(request, 'main_app/student_directory.html', {
            'no_active_session': True,
        })
 
    school = request.user.school
 
    # ── Base queryset for THIS session (used for filtered table) ──
    base_qs = StudentAdmission.objects.filter(
        academic_session = active_session,
        student__school  = school,
    ).select_related('student')
 
    # Latest MonthlyFee balance per admission (subquery — avoids N+1)
    base_qs = annotate_outstanding(base_qs)
 
    # ── Top summary bar: ALWAYS unfiltered (whole active session) ──
    class_section_counts = (
        StudentAdmission.objects.filter(
            academic_session = active_session,
            student__school  = school,
        )
        .values('class_name', 'section')
        .annotate(count=Count('id'))
        .order_by('class_name', 'section')
    )
 
    total_students = sum(c['count'] for c in class_section_counts)
 
    total_outstanding = base_qs.aggregate(total=Sum('outstanding'))['total'] or 0
 
    # ── Filters (applied only to the table below) ─────────────────
    filter_class   = request.GET.get('class_name', '')
    filter_section = request.GET.get('section', '')
    filter_status  = request.GET.get('status', '')
    search_query   = request.GET.get('search', '').strip()
 
    filtered_qs = base_qs
 
    if filter_class:
        filtered_qs = filtered_qs.filter(class_name=filter_class)
    if filter_section:
        filtered_qs = filtered_qs.filter(section=filter_section)
 
    if filter_status == 'new':
        filtered_qs = filtered_qs.filter(promoted_from__isnull=True)
    elif filter_status == 'promoted':
        filtered_qs = filtered_qs.filter(promoted_from__isnull=False, promoted=True)
    elif filter_status == 'not_promoted':
        filtered_qs = filtered_qs.filter(promoted_from__isnull=False, promoted=False)
 
    if search_query:
        filtered_qs = filtered_qs.filter(
            Q(student__first_name__icontains=search_query)
            | Q(student__last_name__icontains=search_query)
            | Q(student__student_id__icontains=search_query)
            | Q(student__father_guardian_name__icontains=search_query)
            | Q(student__contact__icontains=search_query)
        )
 
    filtered_qs = filtered_qs.order_by('class_name', 'section', 'roll_number')
 
    # ── Pagination ──────────────────────────────────────────────
    paginator = Paginator(filtered_qs, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
 
    # Attach a display-friendly status label per row (Python side —
    # cheap since it's only for the current page, not the whole table)
    for admission in page_obj:
        if admission.promoted_from_id is None:
            admission.status_label = 'New Admission'
            admission.status_badge = 'primary'
        elif admission.promoted:
            admission.status_label = 'Promoted'
            admission.status_badge = 'success'
        else:
            admission.status_label = 'Not Promoted'
            admission.status_badge = 'warning'
 
    # Class/section choices for filter dropdowns (from this session's data)
    class_choices = sorted(set(c['class_name'] for c in class_section_counts))
    section_choices = sorted(set(c['section'] for c in class_section_counts))
 
    return render(request, 'main_app/student_directory.html', {
        'no_active_session':     False,
        'active_session':        active_session,
        'page_obj':              page_obj,
        'class_section_counts':  class_section_counts,
        'total_students':        total_students,
        'total_outstanding':     total_outstanding,
        'class_choices':         class_choices,
        'section_choices':       section_choices,
        'filter_class':          filter_class,
        'filter_section':        filter_section,
        'filter_status':         filter_status,
        'search_query':          search_query,
    })
 
 
# ── Student Profile (detail) ──────────────────────────────────
 
@school_staff_required
def student_profile(request, student_id):
    school = request.user.school
    active_session = get_active_session(request)
 
    # Prefer the admission in the active session; fall back to the
    # most recent admission overall if the student isn't in it
    # (e.g. they graduated, left, or session context is being viewed
    # for a student no longer current).
    admission = (
        StudentAdmission.objects
        .filter(
            student__student_id = student_id,
            student__school     = school,
            academic_session    = active_session,
        )
        .select_related('student', 'academic_session')
        .first()
    )
 
    if not admission:
        admission = get_object_or_404(
            StudentAdmission.objects
            .filter(student__student_id=student_id, student__school=school)
            .select_related('student', 'academic_session')
            .order_by('-academic_session__start_date')
        )
 
    student = admission.student
 
    # ── Status label for THIS admission record ────────────────────
    if admission.promoted_from_id is None:
        status_label, status_badge = 'New Admission', 'primary'
    elif admission.promoted:
        status_label, status_badge = 'Promoted', 'success'
    else:
        status_label, status_badge = 'Not Promoted', 'warning'
 
    # ── Admission history — walk promoted_from chain backward ──────
    history = []
    node = admission
    while node:
        if node.promoted_from_id is None:
            node_status = 'New Admission'
        elif node.promoted:
            node_status = 'Promoted'
        else:
            node_status = 'Not Promoted'
 
        history.append({
            'session':     node.academic_session,
            'class_name':  node.class_name,
            'section':     node.section,
            'roll_number': node.roll_number,
            'status':      node_status,
        })
        node = node.promoted_from
 
    # ── Fee summary for the displayed admission's session ──────────
    monthly_fees = admission.monthly_fees.order_by('year', 'month_number')
    fee_totals = monthly_fees.aggregate(
        total_received = Sum('received'),
        total_dues      = Sum('total_dues'),
    )
    last_fee = monthly_fees.last()
    outstanding = last_fee.current_balance if last_fee else (admission.balance or 0)
 
    # ── Results grouped by exam_type with per-exam average % ───────
    results_qs = (
        StudentResult.objects
        .filter(student_admission=admission)
        .select_related('subject')
        .order_by('exam_type', 'subject__name')
    )
 
    results_by_exam = {}
    for r in results_qs:
        results_by_exam.setdefault(r.exam_type, []).append(r)
 
    exam_summaries = []
    for exam_type, rows in results_by_exam.items():
        total_obtained = sum(r.obtained_marks for r in rows)
        total_possible = sum(r.total_marks for r in rows)
        avg_percentage = round((total_obtained / total_possible * 100), 2) if total_possible > 0 else 0.0
        exam_summaries.append({
            'exam_type':       exam_type,
            'rows':            rows,
            'avg_percentage':  avg_percentage,
        })
 
    return render(request, 'main_app/student_profile.html', {
        'student':           student,
        'admission':         admission,
        'status_label':      status_label,
        'status_badge':      status_badge,
        'history':           history,
        'fee_totals':        fee_totals,
        'outstanding':       outstanding,
        'monthly_fees':      monthly_fees,
        'exam_summaries':    exam_summaries,
        'active_session':    active_session,
    })


@school_staff_required
def create_academic_session(request):
    active_session = get_active_session(request)
 
    # Detect if we're in the setup wizard
    setup = request.GET.get('setup') or request.POST.get('setup')
 
    if request.method == 'POST':
        form = AcademicSessionForm(request.POST, request=request)
        if form.is_valid():
            session = form.save(commit=False)
            session.school = request.user.school
            session.save()
            messages.success(request, f"Session '{session.session_name}' created successfully.")
 
            if setup:
                # Wizard flow → go to fee structure
                return redirect(f"{reverse('add_fee_structure')}?setup=1")
            return redirect('create_session')
    else:
        form = AcademicSessionForm(request=request)
 
    sessions = AcademicSession.objects.filter(
        school=request.user.school
    ).order_by('-start_date')
 
    return render(request, 'main_app/create_session.html', {
        'form':           form,
        'sessions':       sessions,
        'active_session': active_session,
        'setup':          bool(setup),
    })

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
        messages.error(request, "No active session found. Please create one first.")
        return redirect('dashboard')
 
    # Detect wizard
    setup = request.GET.get('setup') or request.POST.get('setup')
 
    if request.method == 'POST':
        form = FeeStructureForm(request.POST)
        if form.is_valid():
            class_name = form.cleaned_data['class_name']
 
            if FeeStructure.objects.filter(
                academic_session = active_session,
                class_name       = class_name,
            ).exists():
                messages.warning(
                    request,
                    f"Fee structure for {class_name} already exists for this session."
                )
                redirect_url = f"{reverse('add_fee_structure')}{'?setup=1' if setup else ''}"
                return redirect(redirect_url)
 
            fee_structure = form.save(commit=False)
            fee_structure.school           = request.user.school
            fee_structure.academic_session = active_session
            fee_structure.save()
 
            messages.success(request, f"Fee structure for {class_name} added successfully!")
 
            # Stay on the fee structure page (admin may want to add more classes)
            # The "Continue to Subjects →" button in the template handles Step 3.
            redirect_url = f"{reverse('add_fee_structure')}{'?setup=1' if setup else ''}"
            return redirect(redirect_url)
    else:
        form = FeeStructureForm()
 
    existing_fees = FeeStructure.objects.filter(academic_session=active_session)
 
    return render(request, 'main_app/add_fee_structure.html', {
        'form':          form,
        'existing_fees': existing_fees,
        'active_session': active_session,
        'setup':         bool(setup),
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
        search_query = request.GET.get('search', '').strip()
        search_type  = request.GET.get('search_type', 'student_id')
 
        if search_query:
            queryset = StudentAdmission.objects.filter(
                academic_session=active_session,
                student__school=request.user.school,
            ).select_related('student')
 
            if search_type == 'student_id':
                query = Q(student__student_id__iexact=search_query)
            elif search_type == 'contact':
                query = Q(student__contact__iexact=search_query)
            elif search_type == 'father_name':
                query = Q(student__father_guardian_name__icontains=search_query)
            else:
                query = (
                    Q(student__student_id__iexact=search_query)
                    | Q(student__father_guardian_name__icontains=search_query)
                    | Q(student__contact__iexact=search_query)
                )
 
            students = list(queryset.filter(query).distinct())
            if not students:
                messages.warning(request, "No results found.")
 
    return render(request, 'main_app/monthly_fee.html', {
        'students':       students,
        'active_session': active_session,
        'search_type':    request.GET.get('search_type', 'student_id'),
    })
 
 
# ── Detail + payment page ─────────────────────────────────────
 
@school_staff_required
def monthly_fee_detail(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return render(request, 'main_app/monthly_fee_detail.html', {
            'student': None, 'month_data': [], 'active_session': None,
        })
 
    student_admission = get_object_or_404(
        StudentAdmission.objects.select_related('student'),
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school,
    )
 
    # Self-healing: generate records if missing
    if not student_admission.monthly_fees.exists():
        create_monthly_fees_for_student(student_admission)
 
    if request.method == 'POST':
        return _handle_payment_post(request, student_admission, student_id)
 
    month_data = _build_month_data(request, student_admission, active_session)
    all_fees     = student_admission.monthly_fees.order_by('year', 'month_number')
    last_fee     = all_fees.last()

    total_received  = sum(f.received or Decimal('0.00') for f in all_fees)
    outstanding     = last_fee.current_balance if last_fee else (student_admission.balance or Decimal('0.00'))
    total_dues_sum  = total_received + outstanding
 
    return render(request, 'main_app/monthly_fee_detail.html', {
        'student':        student_admission,
        'month_data':     month_data,
        'active_session': active_session,
        'summary_dues':     total_dues_sum,
        'summary_received': total_received,
        'summary_outstanding': outstanding,
    })
 
 
# ── POST handler (no MonthlyFeeForm) ─────────────────────────
 
def _handle_payment_post(request, student_admission, student_id):
    """
    Saves a monthly payment without going through MonthlyFeeForm.
 
    MonthlyFeeForm cannot be used for POST because:
      1. student_admission is required by the form but is not sent in POST
         (the view sets it after form.save(), which is too late for clean())
      2. total_dues is .disabled for existing records so the browser strips
         it from POST; cleaned_data.get('total_dues') returns None and
         the received > total_dues check crashes
      3. student_admission queryset is built from initial={} which is
         empty on POST, so even if the pk was sent it would fail lookup
 
    Instead we read only received + reviewed_by, validate them directly
    against the already-stored fee_record values, save, and recalculate.
    """
    post_month = request.POST.get('month', '').strip()
    post_year  = request.POST.get('year',  '').strip()
 
    if not post_month or not post_year:
        messages.error(request, "Invalid submission: month or year missing.")
        return redirect('monthly_fee_detail', student_id=student_id)
 
    try:
        post_year = int(post_year)
    except ValueError:
        messages.error(request, "Invalid year value.")
        return redirect('monthly_fee_detail', student_id=student_id)
 
    fee_record = MonthlyFee.objects.filter(
        student_admission=student_admission,
        month=post_month,
        year=post_year,
    ).first()
 
    if not fee_record:
        messages.error(request, f"No fee record found for {post_month} {post_year}.")
        return redirect('monthly_fee_detail', student_id=student_id)
 
    if fee_record.has_payment:
        messages.warning(
            request,
            f"{post_month} {post_year} already has a payment recorded."
        )
        return redirect('monthly_fee_detail', student_id=student_id)
 
    # Parse received amount
    received_raw = request.POST.get('received', '').strip()
    try:
        received = Decimal(received_raw)
    except (InvalidOperation, ValueError):
        messages.error(request, f"Invalid amount: '{received_raw}'. Please enter a number.")
        return redirect('monthly_fee_detail', student_id=student_id)
 
    if received < Decimal('0.00'):
        messages.error(request, "Received amount cannot be negative.")
        return redirect('monthly_fee_detail', student_id=student_id)
 
    if received > fee_record.total_dues:
        messages.error(
            request,
            f"Rs {received} exceeds total dues of Rs {fee_record.total_dues} "
            f"for {post_month} {post_year}."
        )
        return redirect('monthly_fee_detail', student_id=student_id)
 
    reviewed_by = request.POST.get('reviewed_by', '').strip() or request.user.username
 
    try:
        with transaction.atomic():
            # Save only the user-provided fields;
            # recalculate_monthly_chain() will update the chain fields.
            fee_record.received     = received
            fee_record.payment_date = datetime.date.today()
            fee_record.has_payment  = received > Decimal('0.00')
            fee_record.reviewed_by  = reviewed_by
            fee_record.operator     = request.user
            fee_record.save(update_fields=[
                'received', 'payment_date', 'has_payment',
                'reviewed_by', 'operator',
            ])
 
            # Cascade new balance through all subsequent months
            recalculate_monthly_chain(student_admission)
 
        messages.success(
            request,
            f"Payment of Rs {received} for {post_month} {post_year} recorded successfully."
        )
        return redirect('monthly_fee_success', monthly_fee_id=fee_record.id)
 
    except Exception as exc:
        logger.error("Payment save error: %s", exc, exc_info=True)
        messages.error(request, f"Error saving payment: {exc}")
        return redirect('monthly_fee_detail', student_id=student_id)
 
 
# ── GET: build month cards ────────────────────────────────────
 
def _build_month_data(request, student_admission, active_session):
    """
    Returns month_data list for the template.
 
    Each dict:
      fee=<MonthlyFee>  when record exists (paid or unpaid)
      form=<MonthlyFeeForm>  when month needs a payment form
        (used for display values only — NOT for POST validation)
    """
    monthly_fees = (
        student_admission.monthly_fees
        .order_by('year', 'month_number')
    )
    fee_map    = {(f.month, f.year): f for f in monthly_fees}
    months     = generate_monthly_fee_months(active_session)
    month_data = []
 
    for m in months:
        key        = (m['month'], m['year'])
        fee_record = fee_map.get(key)
 
        if fee_record:
            if fee_record.has_payment:
                # Paid or partial — read-only, no form needed
                month_data.append({
                    'month': m['month'],
                    'year':  m['year'],
                    'fee':   fee_record,
                    'form':  None,
                })
            else:
                # Record exists but no payment — show form pre-filled from record
                month_data.append({
                    'month': m['month'],
                    'year':  m['year'],
                    'fee':   fee_record,
                    'form':  MonthlyFeeForm(
                        initial={
                            'student_admission': student_admission,
                            'month':            fee_record.month,
                            'year':             fee_record.year,
                            'previous_balance': fee_record.previous_balance,
                            'monthly_fee':      fee_record.monthly_fee,
                            'transport_fee':    fee_record.transport_fee,
                            'total_dues':       fee_record.total_dues,
                            'received':         Decimal('0.00'),
                            'current_balance':  fee_record.current_balance,
                        },
                        request=request,
                    ),
                })
        else:
            # Fallback: record missing — compute from chain
            last_fee  = (
                student_admission.monthly_fees
                .filter(year__lte=m['year'])
                .order_by('year', 'month_number')
                .last()
            )
            prev_bal    = last_fee.current_balance if last_fee else (
                student_admission.balance or Decimal('0.00')
            )
            net_monthly = max(
                (student_admission.tuition_fee or Decimal('0.00'))
                - (student_admission.discount   or Decimal('0.00')),
                Decimal('0.00'),
            )
            transport   = (
                student_admission.transport_fee
                if student_admission.transport == 'Paid'
                else Decimal('0.00')
            )
            total = prev_bal + net_monthly + transport
 
            month_data.append({
                'month': m['month'],
                'year':  m['year'],
                'fee':   None,
                'form':  MonthlyFeeForm(
                    initial={
                        'student_admission': student_admission,
                        'month':            m['month'],
                        'year':             m['year'],
                        'previous_balance': prev_bal,
                        'monthly_fee':      net_monthly,
                        'transport_fee':    transport,
                        'total_dues':       total,
                        'received':         Decimal('0.00'),
                        'current_balance':  total,
                    },
                    request=request,
                ),
            })
 
    return month_data
 
 
# ── Success page ──────────────────────────────────────────────
@school_staff_required
def monthly_fee_success(request, monthly_fee_id):
    fee = get_object_or_404(
        MonthlyFee,
        id=monthly_fee_id,
        student_admission__student__school=request.user.school,
    )
    return render(request, 'main_app/monthly_fee_success.html', {'monthly_fee': fee})
 

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


# ──────────────────────────────────────────────────────────────
# views.py  ── promote_existing_students (listing + bulk not-promote)
# ──────────────────────────────────────────────────────────────
#
# Bulk "Promote" is intentionally NOT implemented — promotion requires
# per-student fee review (discount, transport, roll number) via
# PromoteExistingStudentForm, which cannot be safely auto-applied in bulk.
#
# Bulk "Not Promote" (repeat) IS implemented — create_repeat_admission()
# is already fully automatic (no manual review step), so looping it
# over selected students is safe.
# ──────────────────────────────────────────────────────────────
 
@school_staff_required
def promote_existing_students(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
 
    previous_session = AcademicSession.objects.filter(
        school       = request.user.school,
        is_active    = False,
        end_date__lt = active_session.start_date,
    ).order_by('-end_date').first()
 
    if not previous_session:
        return render(request, 'main_app/promote_existing_students.html', {
            'no_previous': True,
        })
 
    # ── Bulk "Not Promote" action ─────────────────────────────────
    if request.method == 'POST' and request.POST.get('action') == 'not_promote':
        return _handle_bulk_not_promote(request, active_session)
 
    # ── Students already carried into the active session ─────────
    # (promoted OR repeated — either way they already have a record
    # in active_session and must be excluded from this list)
    admitted_student_ids = list(
        StudentAdmission.objects.filter(
            academic_session = active_session,
            student__school  = request.user.school,
        ).values_list('student_id', flat=True)   # FK column, not Student.student_id string
    )
 
    pending_qs = StudentAdmission.objects.filter(
        academic_session = previous_session,
        student__school  = request.user.school,
        status            = True,
    ).exclude(
        student_id__in = admitted_student_ids,
    )
 
    class_choices = (
        pending_qs.values('class_name').distinct().order_by('class_name')
    )
    class_choices = [c['class_name'] for c in class_choices]
 
    section_choices = (
        pending_qs.values('section').distinct().order_by('section')
    )
    section_choices = [(s['section'], s['section']) for s in section_choices]
 
    # Bulk-action POST also lands here if action != 'not_promote'
    # (shouldn't normally happen) — fall through to GET-style filtering.
    selected_class_name = request.POST.get('class_name') or request.GET.get('class_name', '')
    selected_section    = request.POST.get('section')    or request.GET.get('section', '')
 
    students = []
    if selected_class_name and selected_section:
        students = (
            pending_qs.filter(
                class_name = selected_class_name,
                section    = selected_section,
            )
            .select_related('student')
            .order_by('roll_number')
        )
        for student in students:
            student.pending_balance = get_pending_balance(student)
 
    return render(request, 'main_app/promote_existing_students.html', {
        'class_choices':       class_choices,
        'section_choices':     section_choices,
        'selected_class_name': selected_class_name,
        'selected_section':    selected_section,
        'students':            students,
        'no_previous':         False,
        'active_session':      active_session,
    })
 
 
def _handle_bulk_not_promote(request, active_session):
    """
    Bulk-applies create_repeat_admission() to every selected student.
 
    Each student is processed independently — one failure (e.g. missing
    fee structure, roll number conflict) does not stop the others.
    A summary message reports successes and failures.
    """
    selected_class_name = request.POST.get('class_name', '')
    selected_section    = request.POST.get('section', '')
    student_ids         = request.POST.getlist('student_ids')   # Student.student_id strings
 
    if not student_ids:
        messages.warning(request, "No students selected.")
        return redirect(
            f"{reverse('promote_existing_students')}?"
            f"class_name={selected_class_name}&section={selected_section}"
        )
 
    previous_session = AcademicSession.objects.filter(
        school       = request.user.school,
        is_active    = False,
        end_date__lt = active_session.start_date,
    ).order_by('-end_date').first()
 
    succeeded = []
    failed    = []
 
    for sid in student_ids:
        try:
            old_admission = StudentAdmission.objects.get(
                student__student_id = sid,
                academic_session    = previous_session,
                student__school     = request.user.school,
            )
 
            # Skip if already carried over (defensive — UI shouldn't allow this)
            if StudentAdmission.objects.filter(
                student          = old_admission.student,
                academic_session = active_session,
            ).exists():
                failed.append(f"{sid} (already admitted in current session)")
                continue
 
            roll_number = old_admission.roll_number
 
            # Roll conflict check — skip rather than redirect mid-bulk-loop
            if StudentAdmission.objects.filter(
                roll_number      = roll_number,
                class_name       = old_admission.class_name,
                section          = old_admission.section,
                academic_session = active_session,
            ).exists():
                failed.append(f"{sid} (roll number {roll_number} conflict — handle individually)")
                continue
 
            create_repeat_admission(
                old_admission    = old_admission,
                academic_session = active_session,
                roll_number      = roll_number,
                operator         = request.user,
            )
            succeeded.append(sid)
 
        except StudentAdmission.DoesNotExist:
            failed.append(f"{sid} (record not found)")
        except ValueError as exc:
            failed.append(f"{sid} ({exc})")
        except Exception as exc:
            logger.error("Bulk not-promote error for %s: %s", sid, exc, exc_info=True)
            failed.append(f"{sid} (unexpected error)")
 
    if succeeded:
        messages.success(request, f"Marked not promoted: {len(succeeded)} student(s).")
    if failed:
        messages.error(request, f"Failed for {len(failed)} student(s): " + "; ".join(failed))
 
    return redirect(
        f"{reverse('promote_existing_students')}?"
        f"class_name={selected_class_name}&section={selected_section}"
    )





@school_staff_required
def promote_existing_student(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('promote_existing_students')
 
    # The session the student is being promoted FROM
    previous_session = (
        AcademicSession.objects
        .filter(
            school    = request.user.school,
            is_active = False,
            end_date__lt = active_session.start_date,
        )
        .order_by('-end_date')
        .first()
    )
 
    if not previous_session:
        messages.error(request, "No previous session found to promote from.")
        return redirect('promote_existing_students')
 
    student_admission_obj = get_object_or_404(
        StudentAdmission,
        student__student_id   = student_id,
        academic_session      = previous_session,
        student__school       = request.user.school,
    )
 
    # Guard: already promoted to this session?
    if StudentAdmission.objects.filter(
        student          = student_admission_obj.student,
        academic_session = active_session,
    ).exists():
        messages.error(
            request,
            f"{student_admission_obj.student.first_name} is already admitted in the current session."
        )
        return redirect('promote_existing_students')
 
    filtered_class   = request.GET.get('class_name')
    filtered_section = request.GET.get('section')
 
    form = PromoteExistingStudentForm(
        request.POST   or None,
        request.FILES  or None,
        student_admission = student_admission_obj,
        request           = request,
        filtered_class    = filtered_class,
        filtered_section  = filtered_section,
    )
 
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                # 1. Save the new StudentAdmission (form no longer
                #    creates MonthlyFee records — that is done here).
                new_admission = form.save(commit=True)
 
                # 2. Pre-generate all MonthlyFee records for the new
                #    session and calculate the full chain.
                create_monthly_fees_for_student(new_admission)
 
                messages.success(
                    request,
                    f"{student_admission_obj.student.first_name} promoted successfully!"
                )
 
            query_params = urlencode({
                'class_name': filtered_class,
                'section':    filtered_section,
            })
            return redirect(f"{reverse('promote_existing_students')}?{query_params}")
 
        except Exception as exc:
            logger.error("Promotion error: %s", exc, exc_info=True)
            messages.error(request, f"Promotion failed: {exc}")
 
    # Fee structures for JS dynamic fee-loading on the template
    fee_structures = FeeStructure.objects.filter(
        school            = request.user.school,
        academic_session  = active_session,
    ).values(
        'class_name', 'tuition_fee', 'paper_money',
        'books_dues', 'uniform_dues', 'other_charges', 'promotion_fee',
    )
 
    fee_structure_dict = {
        fs['class_name']: {
            'tuition_fee':  str(fs['tuition_fee']),
            'exam_fee':     str(fs['paper_money']),
            'book_fee':     str(fs['books_dues']),
            'uniform_fee':  str(fs['uniform_dues']),
            'other_fee':    str(fs.get('other_charges') or '0.00'),
            'promotion_fee': str(fs.get('promotion_fee') or '0.00'),
        }
        for fs in fee_structures
    }
 
    return render(request, 'main_app/promote_existing_student_form.html', {
        'form':             form,
        'student':          student_admission_obj,
        'current_class':    student_admission_obj.class_name,
        'fee_structures':   fee_structure_dict,
        'filtered_class':   filtered_class,
        'filtered_section': filtered_section,
        'active_session':   active_session,
    })

@school_staff_required
def promote_existing_success(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')

    student_admission = get_object_or_404(
        StudentAdmission,
        student__student_id = student_id,
        academic_session    = active_session,
        student__school     = request.user.school,
    )

    return render(request, 'main_app/promote_existing_success.html', {
        'student_admission': student_admission,
        'filtered_class':    request.GET.get('class_name', ''),
        'filtered_section':  request.GET.get('section', ''),
    })


# ──────────────────────────────────────────────────────────────
# views.py  ── mark_not_promoted (rewritten, no more create_repeated_admission)
# ──────────────────────────────────────────────────────────────

@school_staff_required
def mark_not_promoted(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('promote_existing_students')

    previous_session = AcademicSession.objects.filter(
        school       = request.user.school,
        is_active    = False,
        end_date__lt = active_session.start_date,
    ).order_by('-end_date').first()

    if not previous_session:
        messages.error(request, "No previous session found.")
        return redirect('promote_existing_students')

    student_admission_obj = get_object_or_404(
        StudentAdmission,
        student__student_id = student_id,
        academic_session    = previous_session,
        student__school     = request.user.school,
    )

    if StudentAdmission.objects.filter(
        student          = student_admission_obj.student,
        academic_session = active_session,
    ).exists():
        messages.error(
            request,
            f"{student_admission_obj.student.first_name} is already admitted in the current session."
        )
        return redirect('promote_existing_students')

    filtered_class   = request.GET.get('class_name')
    filtered_section = request.GET.get('section')

    form = NotPromoteStudentForm(
        request.POST  or None,
        request.FILES or None,
        student_admission = student_admission_obj,
        request            = request,
        filtered_class     = filtered_class,
        filtered_section   = filtered_section,
    )

    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                new_admission = form.save(commit=True)
                create_monthly_fees_for_student(new_admission)

                messages.success(
                    request,
                    f"{student_admission_obj.student.first_name} marked as not promoted "
                    f"and remains in {new_admission.class_name}."
                )

            query_params = urlencode({'class_name': filtered_class, 'section': filtered_section})
            return redirect(
                f"{reverse('promote_existing_success', args=[student_admission_obj.student.student_id])}"
                f"?{query_params}"
            )
        except Exception as exc:
            logger.error("Not-promote error: %s", exc, exc_info=True)
            messages.error(request, f"Not-promote failed: {exc}")

    fee_structures = FeeStructure.objects.filter(
        school           = request.user.school,
        academic_session = active_session,
    ).values('class_name', 'tuition_fee', 'paper_money', 'books_dues', 'uniform_dues', 'other_charges')

    fee_structure_dict = {
        fs['class_name']: {
            'tuition_fee':   str(fs['tuition_fee']),
            'exam_fee':      str(fs['paper_money']),
            'book_fee':      str(fs['books_dues']),
            'uniform_fee':   str(fs['uniform_dues']),
            'other_fee':     str(fs.get('other_charges') or '0.00'),
            'promotion_fee': '0.00',
        }
        for fs in fee_structures
    }

    return render(request, 'main_app/mark-not-promoted.html', {
        'form':             form,
        'student':          student_admission_obj,
        'current_class':    student_admission_obj.class_name,
        'fee_structures':   json.dumps(fee_structure_dict),
        'filtered_class':   filtered_class,
        'filtered_section': filtered_section,
        'active_session':   active_session,
    })
    


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
        form = TeacherForm(data=request.POST, request=request)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Teacher created successfully.")
                return redirect('teacher_list')
            except Exception as e:
                logger.error("Error creating teacher: %s", str(e))
                messages.error(request, f"Error creating teacher: {str(e)}")
        else:
            logger.warning("Teacher form invalid: %s", form.errors)
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = TeacherForm(request=request)
 
    # All currently assigned class+section pairs for this school.
    # Passed as JSON so the template JS can disable taken sections
    # dynamically when the admin selects a class.
    assigned_pairs_json = json.dumps(
        list(
            Teacher.objects.filter(school=request.user.school)
            .values_list('class_name', 'section')
        )
    )
 
    return render(request, 'main_app/teacher_form.html', {
        'form':                form,
        'action':              'Create',
        'assigned_pairs_json': assigned_pairs_json,
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
                return redirect('teacher_list')
            except Exception as e:
                logger.error("Error updating teacher: %s", str(e))
                messages.error(request, f"Error updating teacher: {str(e)}")
        else:
            logger.warning("Teacher form invalid: %s", form.errors)
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = TeacherForm(
            instance=teacher,
            request=request,
            initial={'username': teacher.user.username if teacher.user else ''},
        )
 
    # On edit: exclude the current teacher's own assignment so their
    # own class+section doesn't appear as "taken" in the JS filter.
    assigned_pairs_json = json.dumps(
        list(
            Teacher.objects.filter(school=request.user.school)
            .exclude(pk=teacher.pk)
            .values_list('class_name', 'section')
        )
    )
 
    return render(request, 'main_app/teacher_form.html', {
        'form':                form,
        'action':              'Update',
        'teacher':             teacher,
        'assigned_pairs_json': assigned_pairs_json,
    })

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
# ── 3. subject_list ───────────────────────────────────────────
 
@school_staff_or_teacher_required
def subject_list(request):
    # Detect wizard
    setup = request.GET.get('setup')
 
    if getattr(request.user, 'user_type', None) == 4:  # Teacher
        subjects = request.user.teacher.subjects.all().order_by('name')
        context = {
            'subjects':   subjects,
            'is_teacher': True,
            'page_title': 'My Subjects',
            'setup':      False,   # wizard never shown to teachers
        }
    else:  # Admin / Staff
        subjects = Subject.objects.filter(
            school=request.user.school
        ).order_by('name').prefetch_related('teacher_set')
        context = {
            'subjects':   subjects,
            'is_teacher': False,
            'page_title': 'All Subjects',
            'setup':      bool(setup),
        }
 
    return render(request, 'main_app/subject_list.html', context)

# ── 4. subject_create ─────────────────────────────────────────
# (Carry the ?setup=1 flag through subject creation so the step
# indicator stays visible while the admin adds multiple subjects.)
 
@school_staff_or_teacher_required
def subject_create(request):
    setup = request.GET.get('setup') or request.POST.get('setup')
 
    if request.method == 'POST':
        form = SubjectForm(request.POST, request=request)
        if form.is_valid():
            try:
                subject = form.save(commit=False)
                subject.school = request.user.school
                subject.save()
                messages.success(request, f"Subject '{subject.name}' created successfully.")
 
                # Return to subject list, preserving wizard flag
                redirect_url = f"{reverse('subject_list')}{'?setup=1' if setup else ''}"
                return redirect(redirect_url)
            except Exception as e:
                messages.error(request, f"Error creating subject: {str(e)}")
        else:
            messages.error(request, f"Form invalid: {form.errors.as_text()}")
    else:
        form = SubjectForm(request=request)
 
    return render(request, 'main_app/subject_form.html', {
        'form':   form,
        'action': 'Create',
        'setup':  bool(setup),
    })


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



# ── Analytics View (session-selectable deep dive) ────────────
 
def _safe_html(fig):
    """Standard layout tweaks + HTML export for every chart."""
    return fig.update_layout(
        margin=dict(l=10, r=10, t=20, b=50),
        font=dict(family='Segoe UI, system-ui, sans-serif', size=12),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    ).to_html(full_html=False)
 
 
@school_staff_required
def analytics(request):
    school = request.user.school
    form   = AnalyticsForm(request.POST or None, request=request)
 
    # ── Session selector ──────────────────────────────────────
    selected_session_id = request.GET.get('session')
    if selected_session_id:
        sel_session = AcademicSession.objects.filter(
            id=selected_session_id, school=school
        ).first()
    else:
        sel_session = AcademicSession.objects.filter(
            is_active=True, school=school
        ).first()
 
    all_sessions  = AcademicSession.objects.filter(school=school).order_by('start_date')
    all_sessions_list = list(all_sessions)
 
    # ── Session-scoped base querysets ─────────────────────────
    if sel_session:
        admission_qs   = StudentAdmission.objects.filter(
            student__school=school, academic_session=sel_session, status=True,
        )
        monthly_fee_qs = MonthlyFee.objects.filter(
            student_admission__academic_session=sel_session,
            student_admission__student__school=school,
        )
        expenses_qs    = Expenses.objects.filter(
            school=school,
            payment_date__range=(sel_session.start_date, sel_session.end_date),
        )
        events_qs = sel_session  # used only for scoping below
    else:
        admission_qs   = StudentAdmission.objects.none()
        monthly_fee_qs = MonthlyFee.objects.none()
        expenses_qs    = Expenses.objects.none()
 
    # ──────────────────────────────────────────────────────────
    # SECTION A — SCHOOL PROGRESS SCORE (cross-session)
    # Composite index per session:
    #   Collection Rate  30%
    #   Retention Rate   25%
    #   Enrollment Growth 25%
    #   Pass Rate        20%
    # ──────────────────────────────────────────────────────────
    progress_data   = []
    prev_student_ids = set()
 
    for idx, session in enumerate(all_sessions_list):
        s_adm = StudentAdmission.objects.filter(
            student__school=school, academic_session=session, status=True,
        )
        s_mf  = MonthlyFee.objects.filter(
            student_admission__academic_session=session,
            student_admission__student__school=school,
        )
 
        # Collection rate
        total_coll  = float(s_mf.aggregate(t=Sum('received'))['t'] or 0) + \
                      float(s_adm.aggregate(t=Sum('received'))['t'] or 0)
        adm_billed  = float(s_adm.aggregate(t=Sum('total_dues'))['t'] or 0)
        mon_billed  = float(s_mf.aggregate(t=Sum(F('monthly_fee') + F('transport_fee')))['t'] or 0)
        gross       = adm_billed + mon_billed
        coll_rate   = min(total_coll / gross * 100, 100) if gross > 0 else 0
 
        # Retention rate
        curr_ids    = set(s_adm.values_list('student_id', flat=True))
        retained    = len(curr_ids & prev_student_ids) if prev_student_ids else 0
        ret_rate    = (retained / len(prev_student_ids) * 100) if prev_student_ids else 0
        prev_student_ids = curr_ids
 
        # Enrollment growth (vs previous session)
        if idx > 0:
            prev_count = progress_data[-1]['students']
            curr_count = s_adm.count()
            growth     = ((curr_count - prev_count) / prev_count * 100) if prev_count > 0 else 0
            growth_capped = min(max(growth + 50, 0), 100)  # centre at 50% baseline
        else:
            curr_count    = s_adm.count()
            growth        = 0
            growth_capped = 50
 
        # Pass rate
        results     = StudentResult.objects.filter(student_admission__in=s_adm)
        total_r     = results.count()
        pass_r      = sum(1 for r in results.only('obtained_marks', 'total_marks') if r.grade != 'F')
        pass_rate   = (pass_r / total_r * 100) if total_r > 0 else 0
 
        # Composite score
        if idx == 0:
            score = coll_rate * 0.55 + pass_rate * 0.45
        else:
            score = (coll_rate * 0.30 + ret_rate * 0.25 +
                     growth_capped * 0.25 + pass_rate * 0.20)
 
        progress_data.append({
            'session':         session.session_name,
            'students':        curr_count,
            'score':           round(score, 1),
            'collection_rate': round(coll_rate, 1),
            'retention_rate':  round(ret_rate, 1),
            'enrollment_growth': round(growth, 1),
            'pass_rate':       round(pass_rate, 1),
        })
 
    # Progress gauge (latest / selected session)
    chart_progress_gauge = None
    current_progress = next(
        (p for p in reversed(progress_data)
         if sel_session and p['session'] == sel_session.session_name),
        progress_data[-1] if progress_data else None
    )
    if current_progress:
        score = current_progress['score']
        color = '#e53935' if score < 40 else '#fb8c00' if score < 70 else '#43a047'
        fig   = go.Figure(go.Indicator(
            mode  = 'gauge+number+delta',
            value = score,
            delta = {'reference': progress_data[-2]['score'] if len(progress_data) >= 2 else score,
                     'relative': False},
            gauge = {
                'axis':  {'range': [0, 100], 'tickwidth': 1},
                'bar':   {'color': color, 'thickness': 0.3},
                'steps': [
                    {'range': [0, 40],  'color': '#ffcdd2'},
                    {'range': [40, 70], 'color': '#fff9c4'},
                    {'range': [70, 100],'color': '#c8e6c9'},
                ],
                'threshold': {
                    'line': {'color': '#1565c0', 'width': 3},
                    'thickness': 0.75,
                    'value': 70,
                },
            },
            number={'suffix': '/100', 'font': {'size': 32}},
        ))
        fig.update_layout(
            height=250,
            margin=dict(l=20, r=20, t=20, b=20),
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(family='Segoe UI, system-ui, sans-serif'),
        )
        chart_progress_gauge = fig.to_html(full_html=False)
 
    # Progress trend line
    chart_progress_trend = None
    if len(progress_data) >= 2:
        df  = pd.DataFrame(progress_data)
        fig = px.line(
            df, x='session',
            y=['score', 'collection_rate', 'retention_rate', 'pass_rate'],
            markers=True, template='plotly_white',
            labels={'value': 'Score (%)', 'session': '', 'variable': ''},
            color_discrete_map={
                'score':           '#1565c0',
                'collection_rate': '#43a047',
                'retention_rate':  '#8e24aa',
                'pass_rate':       '#fb8c00',
            },
        )
        fig.update_layout(
            legend=dict(orientation='h', yanchor='bottom', y=-0.45, title=''),
            hovermode='x unified',
        )
        chart_progress_trend = _safe_html(fig)
 
    # ──────────────────────────────────────────────────────────
    # SECTION B — STUDENT CHARTS
    # ──────────────────────────────────────────────────────────
 
    # 1. Student distribution by class (donut)
    chart_student_dist = None
    class_data = list(admission_qs.values('class_name').annotate(count=Count('id')).order_by('class_name'))
    if class_data:
        df  = pd.DataFrame(class_data)
        fig = px.pie(df, names='class_name', values='count', hole=0.5, template='plotly_white')
        fig.update_layout(legend=dict(orientation='h', yanchor='bottom', y=-0.45))
        chart_student_dist = _safe_html(fig)
 
    # 2. Admission status donut
    chart_status = None
    stats = admission_qs.aggregate(
        new  = Count('id', filter=Q(promoted_from__isnull=True)),
        prom = Count('id', filter=Q(promoted=True)),
        notp = Count('id', filter=Q(promoted_from__isnull=False, promoted=False)),
    )
    status_rows = [
        {'Status': 'New Admission', 'Count': stats['new']},
        {'Status': 'Promoted',      'Count': stats['prom']},
        {'Status': 'Not Promoted',  'Count': stats['notp']},
    ]
    sdf = pd.DataFrame([r for r in status_rows if r['Count'] > 0])
    if not sdf.empty:
        fig = px.pie(
            sdf, names='Status', values='Count', hole=0.5, template='plotly_white',
            color='Status',
            color_discrete_map={
                'New Admission': '#1e88e5',
                'Promoted':      '#43a047',
                'Not Promoted':  '#fb8c00',
            },
        )
        fig.update_layout(legend=dict(orientation='h', yanchor='bottom', y=-0.45))
        chart_status = _safe_html(fig)
 
    # 3. Student retention rate per session (bar)
    chart_retention = None
    ret_rows = [p for p in progress_data if p['retention_rate'] > 0]
    if ret_rows:
        df  = pd.DataFrame(ret_rows)
        fig = px.bar(
            df, x='session', y='retention_rate',
            template='plotly_white',
            labels={'session': '', 'retention_rate': 'Retention (%)'},
            color='retention_rate', color_continuous_scale='Greens',
            text='retention_rate',
        )
        fig.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        fig.update_layout(coloraxis_showscale=False, yaxis_range=[0, 110])
        chart_retention = _safe_html(fig)
 
    # 4. Class progression funnel
    chart_funnel = None
    if class_data:
        # Define canonical class order; unknown classes go at end
        CLASS_ORDER = [
            'PG', 'Nursery', 'KG', 'Prep',
            'Class 1', 'Class 2', 'Class 3', 'Class 4', 'Class 5',
            'Class 6', 'Class 7', 'Class 8', 'Class 9', 'Class 10',
        ]
        df = pd.DataFrame(class_data)
        df['order'] = df['class_name'].apply(
            lambda c: CLASS_ORDER.index(c) if c in CLASS_ORDER else len(CLASS_ORDER)
        )
        df = df.sort_values('order')
        fig = go.Figure(go.Funnel(
            y    = df['class_name'].tolist(),
            x    = df['count'].tolist(),
            textposition = 'inside',
            textinfo     = 'value+percent initial',
            marker       = {'color': px.colors.sequential.Blues_r[:len(df)]},
        ))
        fig.update_layout(
            margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor='rgba(0,0,0,0)',
            font=dict(family='Segoe UI, system-ui, sans-serif', size=12),
            yaxis={'autorange': 'reversed'},
        )
        chart_funnel = fig.to_html(full_html=False)
 
    # ──────────────────────────────────────────────────────────
    # SECTION C — FEE CHARTS
    # ──────────────────────────────────────────────────────────
 
    # 5. Monthly fee trend (billed vs collected)
    chart_fee_trend = None
    trend_data = list(
        monthly_fee_qs
        .values('month', 'year', 'month_number')
        .annotate(
            collected = Sum('received'),
            billed    = Sum(F('monthly_fee') + F('transport_fee')),
        )
        .order_by('year', 'month_number')
    )
    if trend_data:
        df = pd.DataFrame(trend_data)
        df['period']    = df['month'] + ' ' + df['year'].astype(str)
        df['collected'] = df['collected'].fillna(0).astype(float)
        df['billed']    = df['billed'].fillna(0).astype(float)
        fig = px.line(
            df, x='period', y=['billed', 'collected'],
            markers=True, template='plotly_white',
            labels={'value': 'Amount (Rs)', 'period': '', 'variable': ''},
            color_discrete_map={'billed': '#ef5350', 'collected': '#43a047'},
        )
        fig.update_layout(
            hovermode='x unified',
            legend=dict(orientation='h', yanchor='bottom', y=-0.45, title=''),
            xaxis_tickangle=-30,
        )
        chart_fee_trend = _safe_html(fig)
 
    # 6. Fee defaulter aging
    chart_defaulters = None
    top_defaulters   = []
    if sel_session:
        defaulter_qs = (
            admission_qs
            .annotate(
                unpaid=Count(
                    'monthly_fees',
                    filter=Q(
                        monthly_fees__has_payment=False,
                        monthly_fees__total_dues__gt=0,
                    )
                )
            )
            .filter(unpaid__gt=0)
            .select_related('student')
            .order_by('-unpaid')
        )
        aging = {
            '1 Month':   defaulter_qs.filter(unpaid=1).count(),
            '2 Months':  defaulter_qs.filter(unpaid=2).count(),
            '3+ Months': defaulter_qs.filter(unpaid__gte=3).count(),
        }
        aging_df = pd.DataFrame([
            {'Aging': k, 'Students': v} for k, v in aging.items() if v > 0
        ])
        if not aging_df.empty:
            fig = px.bar(
                aging_df, x='Aging', y='Students',
                template='plotly_white',
                color='Aging',
                color_discrete_map={
                    '1 Month':   '#fb8c00',
                    '2 Months':  '#f4511e',
                    '3+ Months': '#e53935',
                },
                text='Students',
            )
            fig.update_traces(textposition='outside')
            fig.update_layout(showlegend=False)
            chart_defaulters = _safe_html(fig)
 
        top_defaulters = list(
            defaulter_qs[:10].values(
                'student__first_name', 'student__last_name',
                'student__student_id', 'class_name', 'section', 'unpaid',
            )
        )
 
    # ──────────────────────────────────────────────────────────
    # SECTION D — FINANCIAL CHARTS
    # ──────────────────────────────────────────────────────────
 
    # 7. Revenue vs Operational Cost per session
    chart_rev_vs_cost = None
    if all_sessions_list:
        rev_cost_rows = []
        for session in all_sessions_list:
            s_mf  = MonthlyFee.objects.filter(
                student_admission__academic_session=session,
                student_admission__student__school=school,
            )
            s_adm = StudentAdmission.objects.filter(
                student__school=school, academic_session=session,
            )
            revenue = float(s_mf.aggregate(t=Sum('received'))['t'] or 0) + \
                      float(s_adm.aggregate(t=Sum('received'))['t'] or 0)
            exp     = float(Expenses.objects.filter(
                school=school,
                payment_date__range=(session.start_date, session.end_date),
            ).aggregate(t=Sum('price'))['t'] or 0)
            t_sal   = float(Teacher.objects.filter(school=school).aggregate(t=Sum('salary'))['t'] or 0)
            s_sal   = float(Staff.objects.filter(school=school).aggregate(t=Sum('salary'))['t'] or 0)
            rev_cost_rows.append({
                'session':  session.session_name,
                'Revenue':  revenue,
                'Expenses': exp,
                'Salaries': t_sal + s_sal,
            })
        if rev_cost_rows:
            df  = pd.DataFrame(rev_cost_rows)
            fig = px.line(
                df, x='session', y=['Revenue', 'Expenses', 'Salaries'],
                markers=True, template='plotly_white',
                labels={'value': 'Amount (Rs)', 'session': '', 'variable': ''},
                color_discrete_map={
                    'Revenue':  '#43a047',
                    'Expenses': '#ef5350',
                    'Salaries': '#fb8c00',
                },
            )
            fig.update_layout(
                hovermode='x unified',
                legend=dict(orientation='h', yanchor='bottom', y=-0.45, title=''),
            )
            chart_rev_vs_cost = _safe_html(fig)
 
    # 8. Expense categories (horizontal bar)
    chart_expense_cat = None
    expense_cat = list(
        expenses_qs.values('expense_name').annotate(total=Sum('price')).order_by('-total')
    )
    if expense_cat:
        df  = pd.DataFrame(expense_cat)
        df['total'] = df['total'].astype(float)
        fig = px.bar(
            df, y='expense_name', x='total', orientation='h',
            template='plotly_white',
            labels={'expense_name': '', 'total': 'Amount (Rs)'},
            color='total', color_continuous_scale='Reds',
        )
        fig.update_layout(
            yaxis={'categoryorder': 'total ascending'},
            coloraxis_showscale=False,
        )
        chart_expense_cat = _safe_html(fig)
 
    # 9. Expense type trend (Monthly vs Daily stacked bar over time)
    chart_expense_trend = None
    exp_trend = list(
        expenses_qs
        .annotate(m=TruncMonth('payment_date'))
        .values('m', 'expense_type')
        .annotate(total=Sum('price'))
        .order_by('m', 'expense_type')
    )
    if exp_trend:
        df = pd.DataFrame(exp_trend)
        df['period'] = pd.to_datetime(df['m']).dt.strftime('%b %Y')
        df['total']  = df['total'].astype(float)
        fig = px.bar(
            df, x='period', y='total', color='expense_type',
            barmode='stack', template='plotly_white',
            labels={'period': '', 'total': 'Amount (Rs)', 'expense_type': 'Type'},
            color_discrete_map={'Monthly': '#1e88e5', 'Daily': '#fb8c00'},
        )
        fig.update_layout(
            xaxis_tickangle=-30,
            legend=dict(orientation='h', yanchor='bottom', y=-0.45, title=''),
        )
        chart_expense_trend = _safe_html(fig)
 
    # ──────────────────────────────────────────────────────────
    # SECTION E — ACADEMIC CHARTS
    # ──────────────────────────────────────────────────────────
 
    # 10. Subject performance heatmap
    chart_subject_heatmap = None
    result_agg = list(
        StudentResult.objects.filter(student_admission__in=admission_qs)
        .exclude(total_marks=0)
        .annotate(
            pct=ExpressionWrapper(
                F('obtained_marks') * 100.0 / F('total_marks'),
                output_field=FloatField(),
            )
        )
        .values('subject__name', 'student_admission__class_name')
        .annotate(avg_pct=Avg('pct'))
        .order_by('subject__name', 'student_admission__class_name')
    )
    if result_agg:
        df = pd.DataFrame(result_agg)
        df.columns = ['subject', 'class_name', 'avg_pct']
        df['avg_pct'] = df['avg_pct'].round(1)
        try:
            pivot = df.pivot(index='subject', columns='class_name', values='avg_pct')
            fig = px.imshow(
                pivot,
                labels=dict(x='Class', y='Subject', color='Avg %'),
                color_continuous_scale='RdYlGn',
                zmin=0, zmax=100,
                text_auto='.1f',
                template='plotly_white',
                aspect='auto',
            )
            fig.update_layout(
                margin=dict(l=10, r=10, t=20, b=60),
                paper_bgcolor='rgba(0,0,0,0)',
                font=dict(family='Segoe UI, system-ui, sans-serif', size=11),
                xaxis_tickangle=-30,
            )
            chart_subject_heatmap = fig.to_html(full_html=False)
        except Exception as e:
            logger.warning("Heatmap pivot failed: %s", e)
 
    # 11. Grade distribution bar
    chart_grades = None
    result_qs = StudentResult.objects.filter(student_admission__in=admission_qs)
    if result_qs.exists():
        grade_counts = {'A+': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0}
        for r in result_qs.only('obtained_marks', 'total_marks'):
            g = r.grade
            grade_counts[g] = grade_counts.get(g, 0) + 1
        gdf = pd.DataFrame([
            {'Grade': k, 'Count': v} for k, v in grade_counts.items() if v > 0
        ])
        if not gdf.empty:
            order = ['A+', 'A', 'B', 'C', 'D', 'F']
            gdf['Grade'] = pd.Categorical(gdf['Grade'], categories=order, ordered=True)
            gdf = gdf.sort_values('Grade')
            fig  = px.bar(
                gdf, x='Grade', y='Count', template='plotly_white',
                color='Grade',
                color_discrete_map={
                    'A+': '#1b5e20', 'A': '#43a047', 'B': '#1e88e5',
                    'C':  '#fb8c00', 'D': '#ff7043', 'F': '#e53935',
                },
                text='Count',
            )
            fig.update_traces(textposition='outside')
            fig.update_layout(showlegend=False)
            chart_grades = _safe_html(fig)
 
    # ──────────────────────────────────────────────────────────
    # SECTION F — OPERATIONS CHARTS
    # ──────────────────────────────────────────────────────────
 
    # 12. Transport utilization (vehicle vs students)
    chart_transport = None
    transport_data  = list(
        Transport.objects.filter(school=school)
        .values('vehicle_number', 'route', 'number_of_students')
        .order_by('-number_of_students')
    )
    if transport_data:
        df = pd.DataFrame(transport_data)
        df['label'] = df['vehicle_number'] + ' — ' + df['route'].str[:20]
        # Students on paid transport in current session
        paid_count  = admission_qs.filter(transport='Paid').count()
        fig = px.bar(
            df, x='label', y='number_of_students',
            template='plotly_white',
            labels={'label': '', 'number_of_students': 'Students'},
            color='number_of_students', color_continuous_scale='Blues',
            text='number_of_students',
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(
            coloraxis_showscale=False,
            xaxis_tickangle=-30,
            annotations=[dict(
                text=f'Total on paid transport this session: {paid_count}',
                xref='paper', yref='paper', x=0, y=1.08,
                showarrow=False,
                font=dict(size=11, color='#546e7a'),
            )],
        )
        chart_transport = _safe_html(fig)
 
    # ──────────────────────────────────────────────────────────
    # CUSTOM CHART (POST)
    # ──────────────────────────────────────────────────────────
    chart_html = None
    if request.method == 'POST' and form.is_valid():
        try:
            model_name = form.cleaned_data['model_name']
            x_axis     = form.cleaned_data['x_axis']
            y_axis     = form.cleaned_data['y_axis']
            chart_type = form.cleaned_data['chart_type']
 
            model    = apps.get_model('main_app', model_name)
            queryset = model.objects.all()
            if hasattr(model, 'school'):
                queryset = queryset.filter(school=school)
            elif hasattr(model, 'student_admission'):
                queryset = queryset.filter(student_admission__student__school=school)
            elif hasattr(model, 'student'):
                queryset = queryset.filter(student__school=school)
            if sel_session and hasattr(model, 'academic_session'):
                queryset = queryset.filter(academic_session=sel_session)
 
            df = pd.DataFrame.from_records(queryset.values(x_axis, y_axis)).dropna()
            if df.empty:
                chart_html = "<div class='alert alert-warning'>No data for these fields.</div>"
            elif df[x_axis].nunique() <= 1:
                chart_html = "<div class='alert alert-warning'>Not enough variation in X-axis.</div>"
            else:
                fig_fn = {'bar': px.bar, 'line': px.line, 'scatter': px.scatter}.get(chart_type, px.bar)
                chart_html = _safe_html(fig_fn(df, x=x_axis, y=y_axis, template='plotly_white'))
        except Exception as exc:
            chart_html = f"<div class='alert alert-danger'>Error: {exc}</div>"
 
    return render(request, 'main_app/analytics.html', {
        'form':                  form,
        'chart':                 chart_html,
        'all_sessions':          all_sessions,
        'selected_session':      sel_session,
        'progress_data':         progress_data,
        'current_progress':      current_progress,
        # Charts
        'chart_progress_gauge':  chart_progress_gauge,
        'chart_progress_trend':  chart_progress_trend,
        'chart_student_dist':    chart_student_dist,
        'chart_status':          chart_status,
        'chart_retention':       chart_retention,
        'chart_funnel':          chart_funnel,
        'chart_fee_trend':       chart_fee_trend,
        'chart_defaulters':      chart_defaulters,
        'top_defaulters':        top_defaulters,
        'chart_rev_vs_cost':     chart_rev_vs_cost,
        'chart_expense_cat':     chart_expense_cat,
        'chart_expense_trend':   chart_expense_trend,
        'chart_subject_heatmap': chart_subject_heatmap,
        'chart_grades':          chart_grades,
        'chart_transport':       chart_transport,
    })
 


# ── Session Analytics View (cross-session comparison) ────────
 
@school_staff_required
def session_analytics(request):
    if hasattr(request.user, 'school'):
        school = request.user.school
    elif hasattr(request.user, 'staff'):
        school = request.user.staff.school
    else:
        return HttpResponseForbidden("No school assigned to this account.")
 
    sessions     = AcademicSession.objects.filter(school=school).order_by('start_date')
    session_data = []
 
    for session in sessions:
        # FIX: use academic_session filter, not admission_date__range
        admission_qs = StudentAdmission.objects.filter(
            student__school  = school,
            academic_session = session,
            status           = True,
        )
        total_students = admission_qs.count()
 
        # FIX: filter MonthlyFee by academic_session, not payment_date range
        monthly_qs = MonthlyFee.objects.filter(
            student_admission__academic_session = session,
            student_admission__student__school  = school,
        )
 
        # received is per-month amount — summing is correct
        total_fee = monthly_qs.aggregate(
            total=Sum('received')
        )['total'] or 0
 
        # FIX: outstanding = sum of last month's balance per student
        total_balance = annotate_outstanding(admission_qs).aggregate(
            total=Sum('outstanding')
        )['total'] or 0
 
        teacher_salary = Teacher.objects.filter(school=school).aggregate(
            total=Sum('salary')
        )['total'] or 0
 
        staff_salary = Staff.objects.filter(school=school).aggregate(
            total=Sum('salary')
        )['total'] or 0
 
        total_expenses = Expenses.objects.filter(
            school        = school,
            payment_date__range = (session.start_date, session.end_date),
        ).aggregate(total=Sum('price'))['total'] or 0
 
        total_assets = Assets.objects.filter(
            school              = school,
            purchased_date__range = (session.start_date, session.end_date),
        ).aggregate(total=Sum('value'))['total'] or 0
 
        session_data.append({
            'session':         session.session_name,
            'students':        total_students,
            'fee_collected':   float(total_fee),
            'balance':         float(total_balance),
            'teacher_salaries': float(teacher_salary),
            'staff_salaries':  float(staff_salary),
            'expenses':        float(total_expenses),
            'assets':          float(total_assets),
        })
 
    df = pd.DataFrame(session_data)
 
    numeric_cols = ['fee_collected', 'teacher_salaries', 'staff_salaries', 'expenses', 'assets']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
 
    bar_chart = px.bar(
        df, x='session', y=numeric_cols,
        barmode='group', template='plotly_white',
        labels={'value': 'Amount (Rs)', 'session': 'Session', 'variable': ''},
    ).update_layout(
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation='h', yanchor='bottom', y=-0.35),
        xaxis_tickangle=-30,
    ).to_html(full_html=False)
 
    student_trend = px.line(
        df, x='session', y='students',
        markers=True, template='plotly_white',
        labels={'students': 'Student Count', 'session': 'Session'},
    ).update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
    ).to_html(full_html=False)
 
    pie_charts = {}
    for data in session_data:
        pie_df = pd.DataFrame({
            'Category': ['Fees Collected', 'Teacher Salaries', 'Staff Salaries', 'Expenses', 'Assets'],
            'Amount':   [
                data['fee_collected'], data['teacher_salaries'],
                data['staff_salaries'], data['expenses'], data['assets'],
            ],
        })
        pie_df = pie_df[pie_df['Amount'] > 0]
        if not pie_df.empty:
            pie_charts[data['session']] = px.pie(
                pie_df, names='Category', values='Amount',
                hole=0.4, template='plotly_white',
            ).update_layout(
                margin=dict(l=10, r=10, t=30, b=10),
                legend=dict(orientation='h', yanchor='bottom', y=-0.4),
            ).to_html(full_html=False)
 
    return render(request, 'main_app/session_analytics.html', {
        'session_data':  session_data,
        'bar_chart':     bar_chart,
        'student_trend': student_trend,
        'pie_charts':    pie_charts,
        'sessions':      sessions,
    })
 
 
@school_staff_required
def get_model_fields(request):
    allowed = {'MonthlyFee', 'StudentAdmission', 'Student', 'StudentResult', 'Assets', 'Expenses'}
    model_name = request.GET.get('model_name')
    if model_name in allowed:
        try:
            model  = apps.get_model('main_app', model_name)
            fields = [f.name for f in model._meta.fields if not f.is_relation]
            return JsonResponse({'fields': fields})
        except LookupError:
            return JsonResponse({'error': 'Model not found'}, status=400)
    return JsonResponse({'fields': []})

# ── Statistics View (current session snapshot) ────────────────
 
@school_staff_required
def statistics_view(request):
    """
    Fast at-a-glance snapshot for the currently active session.
    All numbers are session-scoped and consistent with each other.
    """
    school         = request.user.school
    active_session = AcademicSession.objects.filter(school=school, is_active=True).first()
 
    # ── Student counts (all session-scoped) ───────────────────
    admission_qs = StudentAdmission.objects.filter(
        student__school      = school,
        academic_session     = active_session,
        status               = True,
    ) if active_session else StudentAdmission.objects.none()
 
    student_stats = admission_qs.aggregate(
        total_students        = Count('id'),
        new_admissions        = Count('id', filter=Q(promoted_from__isnull=True)),
        promoted_count        = Count('id', filter=Q(promoted=True)),
        not_promoted_count    = Count('id', filter=Q(promoted_from__isnull=False, promoted=False)),
        transport_paid_count  = Count('id', filter=Q(transport='Paid')),
        transport_free_count  = Count('id', filter=Q(transport='Free')),
    )
 
    # ── Fee totals ────────────────────────────────────────────
    # FIX: use annotate_outstanding to get correct per-student
    # outstanding, then sum those — NOT Sum('current_balance').
    outstanding_qs = annotate_outstanding(admission_qs)
    total_outstanding = outstanding_qs.aggregate(
        total=Sum('outstanding')
    )['total'] or Decimal('0.00')
 
    monthly_fee_qs = MonthlyFee.objects.filter(
        student_admission__academic_session = active_session,
        student_admission__student__school  = school,
    ) if active_session else MonthlyFee.objects.none()
 
    # received is per-month amount (not cumulative) — summing is correct
    total_received = monthly_fee_qs.aggregate(
        total=Sum('received')
    )['total'] or Decimal('0.00')
 
    # admission-time received (one-time charges collected at admission)
    admission_received = admission_qs.aggregate(
        total=Sum('received')
    )['total'] or Decimal('0.00')
 
    total_collected = total_received + admission_received
 
    # Collection rate
    total_billed = admission_qs.aggregate(
        total=Sum('total_dues')
    )['total'] or Decimal('0.00')
    total_monthly_billed = monthly_fee_qs.aggregate(
        total=Sum(F('monthly_fee') + F('transport_fee'))
    )['total'] or Decimal('0.00')
    gross_billed = total_billed + total_monthly_billed
    collection_rate = round(
        float(total_collected) / float(gross_billed) * 100, 1
    ) if gross_billed > 0 else 0.0
 
    # ── School-wide financials ────────────────────────────────
    teacher_salary = Teacher.objects.filter(school=school).aggregate(
        total=Sum('salary')
    )['total'] or Decimal('0.00')
 
    staff_salary = Staff.objects.filter(school=school).aggregate(
        total=Sum('salary')
    )['total'] or Decimal('0.00')
 
    expenses_qs = Expenses.objects.filter(school=school)
    if active_session:
        expenses_qs = expenses_qs.filter(
            payment_date__range=(active_session.start_date, active_session.end_date)
        )
    total_expenses = expenses_qs.aggregate(
        total=Sum('price')
    )['total'] or Decimal('0.00')
 
    assets_qs = Assets.objects.filter(school=school)
    if active_session:
        assets_qs = assets_qs.filter(
            purchased_date__range=(active_session.start_date, active_session.end_date)
        )
    total_assets = assets_qs.aggregate(
        total=Sum('value')
    )['total'] or Decimal('0.00')
 
    # ── Class/section summary ─────────────────────────────────
    class_summary = (
        admission_qs
        .values('class_name', 'section')
        .annotate(count=Count('id'))
        .order_by('class_name', 'section')
    )
 
    # ── Student distribution chart ────────────────────────────
    class_totals = (
        admission_qs
        .values('class_name')
        .annotate(count=Count('id'))
        .order_by('class_name')
    )
    plot_div = None
    if class_totals:
        df = pd.DataFrame(list(class_totals))
        fig = px.bar(
            df, x='class_name', y='count',
            labels={'class_name': 'Class', 'count': 'Students'},
            color='count',
            color_continuous_scale='Redor',  # or any other color scale you prefer
            template='plotly_white',
        )
        fig.update_layout(
            margin=dict(l=20, r=20, t=30, b=20),
            showlegend=False,
            coloraxis_showscale=False,
        )
        plot_div = fig.to_html(full_html=False)
 
    return render(request, 'main_app/statistics.html', {
        'active_session':      active_session,
        'student_stats':       student_stats,
        'total_outstanding':   total_outstanding,
        'total_collected':     total_collected,
        'gross_billed':        gross_billed,
        'collection_rate':     collection_rate,
        'teacher_salary':      teacher_salary,
        'staff_salary':        staff_salary,
        'total_expenses':      total_expenses,
        'total_assets':        total_assets,
        'class_summary':       class_summary,
        'plot_div':            plot_div,
    })

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


