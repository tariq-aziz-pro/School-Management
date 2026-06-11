from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.db.models import Q, Sum, Count
from django.db import transaction
import logging

from ..forms import SchoolRegisterForm, AcademicSessionForm, FeeStructureForm, SchoolSubscriptionForm
from ..models import School, SchoolSubscription, AcademicSession, FeeStructure, StudentAdmission, MonthlyFee, Teacher, Staff, Expenses
from ..permissions import (
    developer_required,
    get_active_session,
    is_developer,
    is_school_staff,
    school_staff_required,
)

logger = logging.getLogger(__name__)


def custom_login(request):
    if request.user.is_authenticated:
        logger.info(
            f"Already authenticated user {request.user.username} (Type: {request.user.user_type}) redirected."
        )

        if is_developer(request.user) and request.user.user_type != 1:
            return redirect("developer_dashboard")
        elif is_school_staff(request.user):
            return redirect("dashboard")
        elif request.user.user_type == 4:
            return redirect("teacher_dashboard")
        elif request.user.user_type == 5:
            return redirect("student_dashboard")
        else:
            return redirect("index")

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            logger.info(
                f"Login successful: {user.username} (Type: {user.user_type})"
            )

            next_url = request.GET.get("next")
            if next_url and next_url != "/logout/" and next_url.startswith("/"):
                return redirect(next_url)

            if is_developer(user) and user.user_type != 1:
                return redirect("developer_dashboard")
            elif is_school_staff(user):
                return redirect("dashboard")
            elif user.user_type == 4:
                return redirect("teacher_dashboard")
            elif user.user_type == 5:
                return redirect("student_dashboard")
            else:
                return redirect("index")
        else:
            messages.error(request, "Invalid username or password.")
            logger.warning(f"Login failed: {form.errors}")
    else:
        form = AuthenticationForm()

    return render(request, "main_app/login.html", {"form": form})


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


def toggle_school_access(request, school_id):
    school = get_object_or_404(School, id=school_id)
    old_status = school.is_active
    school.is_active = not school.is_active
    school.save(update_fields=['is_active'])
    logger.debug(
        f"User: {request.user} toggled {school.school_name} "
        f"from {old_status} to {school.is_active}"
    )
    messages.success(
        request,
        f"{school.school_name} access {'enabled' if school.is_active else 'disabled'}."
    )
    return redirect('developer_dashboard')


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
                user = form.save()
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


def index(request):
    return render(request, 'main_app/index.html')
