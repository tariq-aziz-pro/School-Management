from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from decimal import Decimal
from io import BytesIO
import os
import datetime
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
import logging

from ..forms import StudentUserForm, StudentAdmissionForm, EditStudentForm
from ..models import CustomUser, AcademicSession, StudentAdmission, MonthlyFee, Student, Subject, Teacher, StudentResult, Syllabus, Announcement, Events, TemporaryPassword
from ..permissions import school_staff_required, student_required, get_active_session

logger = logging.getLogger(__name__)


@school_staff_required
def student_user_create(request):
    if request.method == 'POST':
        form = StudentUserForm(request.POST, school=request.user.school)
        if form.is_valid():
            try:
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


@school_staff_required
def student_list(request):
    active_session = get_active_session(request)

    admissions = StudentAdmission.objects.filter(
        student__school=request.user.school
    ).select_related('student', 'academic_session').order_by('student__student_id')

    if active_session:
        admissions = admissions.filter(academic_session=active_session)

    if class_filter := request.GET.get('class_assigned'):
        admissions = admissions.filter(class_name=class_filter)
    if section_filter := request.GET.get('section'):
        admissions = admissions.filter(section=section_filter)
    if student_id_filter := request.GET.get('student_id'):
        admissions = admissions.filter(student__student_id__icontains=student_id_filter)

    paginator = Paginator(admissions, 50)
    page_number = request.GET.get('page')
    try:
        admissions_page = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        admissions_page = paginator.get_page(1)

    student_ids = [adm.student.student_id for adm in admissions_page]
    users = CustomUser.objects.filter(username__in=student_ids).select_related('temporary_password')
    user_map = {user.username: user for user in users}

    student_data = []
    for admission in admissions_page:
        user = user_map.get(admission.student.student_id)
        temp_password = getattr(user, 'temporary_password', None) if user else None

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
        'page_obj': admissions_page,
        'paginator': paginator,
        'classes': StudentAdmission.objects.filter(student__school=request.user.school)
                        .values_list('class_name', flat=True).distinct().order_by('class_name'),
        'sections': StudentAdmission.objects.filter(student__school=request.user.school)
                        .values_list('section', flat=True).distinct().order_by('section'),
        'active_session': active_session,
    }

    return render(request, 'main_app/student_list.html', context)


@student_required
def student_dashboard(request):
    try:
        student = Student.objects.select_related('school').get(
            student_id=request.user.username
        )
    except Student.DoesNotExist:
        messages.error(request, "Student profile not found.")
        return render(request, 'main_app/student_dashboard.html', {'student': None})

    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active academic session found.")
        return render(request, 'main_app/student_dashboard.html', {
            'student': student,
            'active_session': None
        })

    admission = StudentAdmission.objects.select_related(
        'student', 'academic_session'
    ).filter(
        student=student,
        academic_session=active_session
    ).first()

    if not admission:
        messages.error(request, "No admission record found for the current session.")
        return render(request, 'main_app/student_dashboard.html', {
            'student': student,
            'active_session': active_session
        })

    exam_type_filter = request.GET.get('exam_type')
    subject_filter = request.GET.get('subject')

    results_qs = StudentResult.objects.filter(
        student_admission=admission
    ).select_related('subject')

    if exam_type_filter:
        results_qs = results_qs.filter(exam_type=exam_type_filter)
    if subject_filter:
        results_qs = results_qs.filter(subject_id=subject_filter)

    results = results_qs.order_by('subject__name', 'exam_type')

    result_data = []
    chart_labels = []
    chart_obtained = []
    chart_total = []

    for result in results:
        data = {
            'subject_name': result.subject.name,
            'exam_type': result.exam_type,
            'total_marks': result.total_marks,
            'obtained_marks': result.obtained_marks,
            'percentage': result.percentage,
            'grade': result.grade,
        }
        result_data.append(data)
        chart_labels.append(f"{result.subject.name} ({result.exam_type})")
        chart_obtained.append(result.obtained_marks)
        chart_total.append(result.total_marks)

    class_teacher = Teacher.objects.filter(
        class_name=admission.class_name,
        section=admission.section,
        school=student.school
    ).only('name').first()

    fees = MonthlyFee.objects.filter(
        student_admission=admission
    ).order_by('year', 'month')

    fee_data = [
        {
            'id': fee.id,
            'month': fee.month,
            'year': fee.year,
            'monthly_fee': fee.monthly_fee,
            'transport_fee': fee.transport_fee or Decimal('0'),
            'total_dues': fee.total_dues,
            'received': fee.received,
            'current_balance': fee.current_balance,
            'status': 'Paid' if fee.has_payment or fee.current_balance <= 0 else 'Unpaid',
            'payment_date': fee.payment_date,
        } for fee in fees
    ]

    syllabus = Syllabus.objects.filter(
        academic_session=active_session,
        class_name=admission.class_name
    ).select_related('subject', 'uploaded_by')

    announcements = Announcement.objects.filter(
        school=student.school
    ).order_by('-created_at')[:5]

    events = Events.objects.filter(
        school=student.school,
        event_for__in=['All', 'Students']
    ).order_by('event_date')[:8]

    context = {
        'student': student,
        'admission': admission,
        'class_teacher': class_teacher,
        'active_session': active_session,
        'results': result_data,
        'exam_types': results_qs.values_list('exam_type', flat=True).distinct(),
        'subjects': Subject.objects.filter(
            studentresult__student_admission=admission
        ).distinct(),
        'selected_exam_type': exam_type_filter,
        'selected_subject': subject_filter,
        'fees': fee_data,
        'syllabus': syllabus,
        'announcements': announcements,
        'events': events,
        'chart_data': {
            'labels': chart_labels,
            'obtained_marks': chart_obtained,
            'total_marks': chart_total,
        },
    }

    return render(request, 'main_app/student_dashboard.html', context)


@school_staff_required
def student_admission(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found. Please create an active session first.")
        return redirect('dashboard')

    if request.method == 'POST':
        form = StudentAdmissionForm(request.POST, request.FILES, request=request)

        if form.is_valid():
            try:
                with transaction.atomic():
                    student = Student.objects.create(
                        school=request.user.school,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        father_guardian_name=form.cleaned_data['father_guardian_name'],
                        contact=form.cleaned_data['contact'],
                        date_of_birth=form.cleaned_data['date_of_birth'],
                        gender=form.cleaned_data['gender'],
                        image=form.cleaned_data.get('image')
                    )

                    admission = form.save(commit=False)
                    admission.student = student
                    admission.academic_session = active_session
                    admission.operator = request.user
                    admission.admission_date = datetime.date.today()
                    admission.status = True
                    admission.admission_fee = admission.admission_fee or Decimal('0.00')
                    admission.total_dues = admission.total_dues or Decimal('0.00')
                    admission.balance = admission.balance or Decimal('0.00')
                    admission.save()

                messages.success(request, f"Student {student.first_name} {student.last_name} admitted successfully!")
                return redirect('admission_success', student_id=student.student_id)
            except Exception as e:
                logger.error(f"Admission error: {e}")
                messages.error(request, f"Failed to save: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.replace('_', ' ').title()}: {error}")
    else:
        form = StudentAdmissionForm(request=request)

    return render(request, 'main_app/admission_form.html', {
        'form': form,
        'operator_name': request.user.username,
    })


@school_staff_required
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


@school_staff_required
def admission_success(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')
    student = get_object_or_404(Student, student_id=student_id, school=request.user.school)
    admission = get_object_or_404(StudentAdmission, student=student, academic_session=active_session)
    return render(request, 'main_app/admission_success.html', {'student': student, 'admission': admission})


@school_staff_required
def generate_pdf(request, student_id):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')

    admission = get_object_or_404(
        StudentAdmission.objects.select_related('student', 'student__school'),
        student__student_id=student_id,
        academic_session=active_session,
        student__school=request.user.school
    )

    student = admission.student
    school = student.school

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=1.0*inch,
        bottomMargin=0.8*inch,
        leftMargin=0.8*inch,
        rightMargin=0.8*inch
    )

    elements = []
    styles = getSampleStyleSheet()
    title_style = styles['Heading1']
    normal_style = styles['Normal']
    small_style = styles['Italic']

    current_date = datetime.datetime.now().strftime("%B %d, %Y %I:%M %p PKT")

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

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()

    filename = f"{student.first_name}_{student.last_name}_{admission.roll_number}.pdf".replace(" ", "_")

    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@school_staff_required
def list_admissions(request):
    active_session = get_active_session(request)
    if not active_session:
        messages.error(request, "No active session found.")
        return redirect('dashboard')

    admissions = StudentAdmission.objects.filter(
        academic_session=active_session,
        student__school=request.user.school
    ).select_related('student').order_by('roll_number')

    paginator = Paginator(admissions, 50)
    page_number = request.GET.get('page')
    try:
        admissions_page = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        admissions_page = paginator.get_page(1)

    context = {
        'admissions': admissions_page,
        'page_obj': admissions_page,
        'paginator': paginator,
        'active_session': active_session,
    }

    return render(request, 'main_app/list_admissions.html', context)
