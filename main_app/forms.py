import re

from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django.forms import NumberInput, DateInput
from decimal import Decimal
from dateutil.relativedelta import relativedelta
import datetime
from django.core.exceptions import ValidationError
from .models import CustomUser, School, FeeStructure, AcademicSession, StudentAdmission, MonthlyFee, CLASS_CHOICES, SECTION_CHOICES, Student, SchoolSubscription, Subject, StudentResult, Teacher, Student, TemporaryPassword, Syllabus, Staff, Assets, Expenses, Transport, Events, CLASS_PROGRESSION
import logging
from django.apps import apps
from .utils import calculate_total_dues, validate_transport, get_pending_balance
from .view_helpers import next_available_roll
from .permissions import (
    SCHOOL_STAFF_TYPES,
    USER_TYPE_STUDENT,
    USER_TYPE_TEACHER,
)





logger = logging.getLogger(__name__)


class LoginForm(AuthenticationForm):
    """Adds a role tab (Staff / Teacher / Student) that must match the account type."""

    ROLE_STAFF = 'staff'
    ROLE_TEACHER = 'teacher'
    ROLE_STUDENT = 'student'

    ROLE_CHOICES = (
        (ROLE_STAFF, 'Staff'),
        (ROLE_TEACHER, 'Teacher'),
        (ROLE_STUDENT, 'Student'),
    )

    ROLE_USER_TYPES = {
        ROLE_STAFF: SCHOOL_STAFF_TYPES,      # {1, 2, 3} -> Admin, Operator, Owner
        ROLE_TEACHER: {USER_TYPE_TEACHER},   # {4}
        ROLE_STUDENT: {USER_TYPE_STUDENT},   # {5}
    }

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.HiddenInput(),
        required=True,
        error_messages={'required': 'Please choose Staff, Teacher, or Student before signing in.'},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Username'
        self.fields['username'].help_text = 'Use the username created for your school portal.'
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your username',
            'autocomplete': 'username',
            'autofocus': 'autofocus',
        })
        self.fields['password'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password',
        })

    def clean(self):
        if self.data is not None:
            mutable = self.data.copy()
            if mutable.get('username') is not None:
                mutable['username'] = str(mutable.get('username', '')).strip()
            if mutable.get('password') is not None:
                mutable['password'] = str(mutable.get('password', '')).strip()
            self.data = mutable

        cleaned_data = super().clean()  # authenticates username/password, sets self.user_cache

        role = cleaned_data.get('role')
        user = self.user_cache
        if user is not None and role:
            allowed_types = self.ROLE_USER_TYPES.get(role, set())
            if getattr(user, 'user_type', None) not in allowed_types:
                role_label = dict(self.ROLE_CHOICES).get(role, role)
                self.user_cache = None  # block get_user() from returning this account
                raise forms.ValidationError(
                    f"This is not a {role_label} account. Please choose the correct tab and try again.",
                    code='role_mismatch',
                )
        return cleaned_data

ALLOWED_MODELS = ['MonthlyFee', 'StudentAdmission', 'Student', 'StudentResult', 'Assets', 'Expenses']

class AnalyticsForm(forms.Form):
    model_name = forms.ChoiceField(label='Select Table')
    x_axis = forms.ChoiceField(label='X-Axis (Category)', required=True)
    y_axis = forms.ChoiceField(label='Y-Axis (Numeric Value)', required=True)
    chart_type = forms.ChoiceField(
        choices=[('bar', 'Bar Chart'), ('line', 'Line Chart'), ('scatter', 'Scatter')],
        label='Chart Type'
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        # Populate Model Choices
        models = apps.get_models()
        filtered_models = [
            (model.__name__, model.__name__)
            for model in models
            if model.__name__ in ALLOWED_MODELS and model._meta.app_label == 'main_app'
        ]
        self.fields['model_name'].choices = [('', '---------')] + filtered_models

        # Dynamic field population based on selected model
        data = self.data or self.initial
        model_name = data.get('model_name')

        if model_name:
            try:
                model = apps.get_model('main_app', model_name)
                all_fields = model._meta.fields

                # ================== X-AXIS (Good for categories) ==================
                x_choices = []
                for f in all_fields:
                    if f.is_relation:
                        continue
                    # Prefer CharField, TextField, DateField, etc. for X-axis
                    if f.get_internal_type() in ['CharField', 'TextField', 'DateField', 'DateTimeField', 'IntegerField']:
                        x_choices.append((f.name, f.verbose_name or f.name.replace('_', ' ').title()))

                self.fields['x_axis'].choices = [('', 'Select X Axis')] + x_choices

                # ================== Y-AXIS (Must be Numeric) ==================
                numeric_choices = []
                for f in all_fields:
                    if f.is_relation:
                        continue
                    internal_type = f.get_internal_type()
                    if internal_type in ['IntegerField', 'DecimalField', 'FloatField', 
                                       'PositiveIntegerField', 'PositiveSmallIntegerField']:
                        numeric_choices.append((f.name, f.verbose_name or f.name.replace('_', ' ').title()))

                # Fallback: allow all fields if no numeric found
                if numeric_choices:
                    self.fields['y_axis'].choices = [('', 'Select Y Axis')] + numeric_choices
                else:
                    self.fields['y_axis'].choices = [('', 'Select Y Axis')] + x_choices

            except LookupError as e:
                logger.error(f"AnalyticsForm: Model not found - {model_name}")
                self.fields['x_axis'].choices = [('', 'Model not found')]
                self.fields['y_axis'].choices = [('', 'Model not found')]
            except Exception as e:
                logger.error(f"Error in AnalyticsForm __init__: {e}")
                self.fields['x_axis'].choices = []
                self.fields['y_axis'].choices = []
        else:
            # Default empty choices
            self.fields['x_axis'].choices = [('', 'Select a table first')]
            self.fields['y_axis'].choices = [('', 'Select a table first')]

    def clean(self):
        cleaned_data = super().clean()
        model_name = cleaned_data.get('model_name')
        x_axis = cleaned_data.get('x_axis')
        y_axis = cleaned_data.get('y_axis')

        if model_name and not x_axis:
            self.add_error('x_axis', 'Please select X-Axis field')
        if model_name and not y_axis:
            self.add_error('y_axis', 'Please select Y-Axis field')

        return cleaned_data



TRANSPORT_CHOICES = [
    ('Paid', 'Paid'),
    ('Free', 'Free'),
    ('No', 'No'),
]


class StudentUserForm(forms.Form):
    student_id = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="Select a student ID.",
    )

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        if school:
            existing = CustomUser.objects.filter(
                school=school, user_type=5
            ).values_list('username', flat=True)
            students = Student.objects.filter(school=school).exclude(
                student_id__in=existing
            )
            choices = [(s.student_id, s.student_id) for s in students]
            self.fields['student_id'].choices = choices
            logger.debug(f"StudentUserForm choices for school {school}: {choices}")

    def clean_student_id(self):
        student_id = self.cleaned_data.get('student_id')
        if not student_id:
            raise forms.ValidationError("Student ID is required.")
        if student_id not in [choice[0] for choice in self.fields['student_id'].choices]:
            raise forms.ValidationError(
                f"{student_id} is not a valid choice. Ensure the student exists and has no account."
            )
        if CustomUser.objects.filter(username=student_id).exists():
            raise forms.ValidationError("This student ID is already in use as a username.")
        if not Student.objects.filter(student_id=student_id).exists():
            raise forms.ValidationError("Invalid student ID. Student must exist in the system.")
        return student_id

    def save(self, *, school):
        from django.db import transaction

        student_id = self.cleaned_data['student_id']
        logger.debug(f"Saving StudentUserForm: StudentID={student_id}")
        student = Student.objects.get(student_id=student_id, school=school)

        with transaction.atomic():
            user = CustomUser(
                username=student_id,
                user_type=5,
                is_active=True,
                school=student.school,
            )
            plain_password = TemporaryPassword.assign_to_user(user)
            logger.info(
                f"Student account created: Username={user.username}, School={user.school.school_name}"
            )
        return user, plain_password

class SchoolSubscriptionForm(forms.ModelForm):
    payment_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )

    class Meta:
        model = SchoolSubscription
        fields = ['payment_type', 'payment_method', 'amount', 'payment_date']
        widgets = {
            'payment_type': forms.Select(attrs={'class': 'form-control'}),
            'payment_method': forms.Select(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'step': '0.01', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Optional: Add empty choice for better UX
        self.fields['payment_type'].empty_label = "Select Payment Type"
        self.fields['payment_method'].empty_label = "Select Payment Method"


class SchoolRegisterForm(UserCreationForm):
    school_name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter school name'}))
    school_logo = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-control'}))
    city = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'}))
    owner_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Owner / Principal name'}))
    contact_number = forms.CharField(max_length=15, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+923001234567'}), help_text="Enter a valid phone number (10-15 digits, e.g., +923001234567).")
    number_of_students = forms.IntegerField(min_value=0, initial=0, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'admin@example.com'}))

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'password1', 'password2', 'school_name', 'school_logo', 'city', 'owner_name', 'contact_number', 'number_of_students']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Choose a username'}),
            'password1': forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Create a password'}),
            'password2': forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Repeat password'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'autocomplete': 'username'})
        self.fields['password1'].help_text = 'Use at least 8 characters and mix letters with numbers.'
        self.fields['password2'].help_text = 'Re-enter the password to confirm it.'

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if len(username) < 1:
            raise forms.ValidationError("Username cannot be empty.")
        if len(username) > 150:
            raise forms.ValidationError("Username cannot exceed 150 characters.")
        if not re.fullmatch(r'[\w.@+-]+', username):
            raise forms.ValidationError("Only letters, numbers, and @ . + - _ are allowed in the username.")
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1:
            if len(password1) < 8:
                raise forms.ValidationError('Password must be at least 8 characters long.')
            if password1.isalpha() or password1.isdigit():
                raise forms.ValidationError('Password should include both letters and numbers.')
            try:
                validate_password(password1, self.instance)
            except ValidationError as exc:
                raise forms.ValidationError(list(exc.messages)) from exc
        return password1

    def clean_contact_number(self):
        contact_number = (self.cleaned_data.get('contact_number') or '').strip()
        if not contact_number:
            raise forms.ValidationError("Contact number is required.")
        if not re.fullmatch(r'^\+?\d{10,15}$', contact_number):
            raise forms.ValidationError("Enter a valid phone number (10-15 digits, optional '+' prefix).")
        return contact_number

    def clean_school_name(self):
        school_name = self.cleaned_data.get('school_name')
        if School.objects.filter(school_name=school_name).exists():
            raise forms.ValidationError("A school with this name already exists.")
        return school_name

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email address is already in use.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_type = 1
        user.is_active = True
        if commit:
            try:
                user.save()
                school = School.objects.create(
                    school_name=self.cleaned_data['school_name'],
                    school_logo=self.cleaned_data.get('school_logo'),
                    city=self.cleaned_data['city'],
                    owner_name=self.cleaned_data['owner_name'],
                    contact_number=self.cleaned_data['contact_number'],
                    number_of_students=self.cleaned_data.get('number_of_students', 0),
                    admin_user=user,
                    is_active=True
                )
                user.school = school
                user.save()
                logger.info(f"School registered: Username={user.username}, School={school.school_name}")
            except Exception as e:
                logger.error(f"Error saving school/user: {str(e)}")
                raise
        return user


class EditStudentForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    father_guardian_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    contact = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': 'form-control'}))
    date_of_birth = forms.DateField(widget=DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    gender = forms.ChoiceField(choices=[('Male', 'Male'), ('Female', 'Female')], widget=forms.Select(attrs={'class': 'form-control'}))
    image = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-control'}))

    class Meta:
        model = StudentAdmission
        fields = [
            'first_name', 'last_name', 'father_guardian_name', 'contact', 'date_of_birth', 'gender', 'image',
            'class_name', 'section', 'roll_number', 'admission_date', 'class_teacher',
            'admission_fee', 'tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee', 'other_fee', 'promotion_fee',
            'transport', 'vehicle_no', 'route', 'driver_contact', 'transport_fee',
            'discount', 'admission_discount', 'discount_behalf', 'received', 'total_dues', 'balance',
        ]
        widgets = {
            'class_name': forms.Select(attrs={'class': 'form-control'}),
            'section': forms.Select(attrs={'class': 'form-control'}),
            'roll_number': forms.NumberInput(attrs={'class': 'form-control'}),
            'admission_date': DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'class_teacher': forms.TextInput(attrs={'class': 'form-control'}),
            'admission_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'tuition_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'exam_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'book_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'uniform_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'other_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'promotion_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'transport': forms.Select(attrs={'class': 'form-control'}),
            'vehicle_no': forms.TextInput(attrs={'class': 'form-control'}),
            'route': forms.TextInput(attrs={'class': 'form-control'}),
            'driver_contact': forms.TextInput(attrs={'class': 'form-control'}),
            'transport_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'discount': forms.NumberInput(attrs={'class': 'form-control'}),
            'admission_discount': forms.NumberInput(attrs={'class': 'form-control'}),
            'discount_behalf': forms.Select(attrs={'class': 'form-control'}),
            'received': forms.NumberInput(attrs={'class': 'form-control'}),
            'total_dues': forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['section'].choices = [('', 'Select Section')] + SECTION_CHOICES
        self.fields['discount'].label = 'Monthly Fee Discount'
        self.fields['discount_behalf'].choices = [('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES
        self.helper = FormHelper()
        self.helper.form_method = 'POST'
        self.helper.add_input(Submit('submit', 'Update'))
        if self.instance and self.instance.student:
            student = self.instance.student
            self.fields['first_name'].initial = student.first_name
            self.fields['last_name'].initial = student.last_name
            self.fields['father_guardian_name'].initial = student.father_guardian_name
            self.fields['contact'].initial = student.contact
            self.fields['date_of_birth'].initial = student.date_of_birth
            self.fields['gender'].initial = student.gender
            self.fields['image'].initial = student.image

    def clean_roll_number(self):
        roll_number = self.cleaned_data.get('roll_number')
        class_name = self.cleaned_data.get('class_name')
        section = self.cleaned_data.get('section')
        if self.user and self.user.school:
            current_session = AcademicSession.objects.filter(is_active=True, school=self.user.school).first()
            if roll_number and class_name and section and current_session:
                query = StudentAdmission.objects.filter(
                    roll_number=roll_number, class_name=class_name, section=section,
                    academic_session=current_session, student__school=self.user.school
                )
                if self.instance.pk:
                    query = query.exclude(pk=self.instance.pk)
                if query.exists():
                    raise forms.ValidationError(
                        f"Roll number {roll_number} is already assigned in {class_name} Section {section}."
                    )
        return roll_number

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data = validate_transport(cleaned_data, self)
        if not cleaned_data.get('section'):
            self.add_error('section', 'Section name is required.')
        if cleaned_data.get('discount', Decimal('0.00')) > 0 and not cleaned_data.get('discount_behalf'):
            self.add_error('discount_behalf', 'Please specify the behalf for the discount.')
        return cleaned_data

    def save(self, commit=True):
        student = self.instance.student
        student.first_name = self.cleaned_data['first_name']
        student.last_name = self.cleaned_data['last_name']
        student.father_guardian_name = self.cleaned_data['father_guardian_name']
        student.contact = self.cleaned_data['contact']
        student.date_of_birth = self.cleaned_data['date_of_birth']
        student.gender = self.cleaned_data['gender']
        if self.cleaned_data['image']:
            student.image = self.cleaned_data['image']
        student.school = self.user.school if self.user and self.user.school else None
        if commit:
            student.save()
        admission = super().save(commit=False)
        admission.operator = self.user
        admission.total_dues = (
            Decimal(str(admission.admission_fee or 0.00)) +
            Decimal(str(admission.tuition_fee or 0.00)) +
            Decimal(str(admission.exam_fee or 0.00)) +
            Decimal(str(admission.book_fee or 0.00)) +
            Decimal(str(admission.uniform_fee or 0.00)) +
            Decimal(str(admission.other_fee or 0.00)) +
            Decimal(str(admission.promotion_fee or 0.00)) +
            Decimal(str(admission.transport_fee or 0.00)) -
            Decimal(str(admission.discount or 0.00)) -
            Decimal(str(admission.admission_discount or 0.00))
        )
        admission.balance = admission.total_dues - Decimal(str(admission.received or 0.00))
        if commit:
            admission.save()
        return admission

class AcademicSessionForm(forms.ModelForm):
    class Meta:
        model = AcademicSession
        fields = ['session_name', 'start_date', 'end_date']
        widgets = {
            'session_name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.school:
            self.instance.school = self.request.user.school  # Set school on instance

    def clean(self):
        cleaned_data = super().clean()
        session_name = cleaned_data.get('session_name')
        if not self.request or not self.request.user.school:
            raise forms.ValidationError("No school associated with your account.")
        if AcademicSession.objects.filter(
            session_name=session_name,
            school=self.request.user.school
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A session with this name already exists for your school.")
        return cleaned_data

class FeeStructureForm(forms.ModelForm):
    class Meta:
        model = FeeStructure
        fields = [
            'class_name', 'tuition_fee', 'admission_fee', 'books_dues',
            'uniform_dues', 'paper_money', 'promotion_fee', 'other_charges'
        ]
        widgets = {
            'class_name': forms.Select(attrs={'class': 'form-control'}),
            'tuition_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'admission_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'books_dues': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'uniform_dues': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'paper_money': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'promotion_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'other_charges': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

class StudentAdmissionForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    father_guardian_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    contact = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': 'form-control'}))
    date_of_birth = forms.DateField(widget=DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    gender = forms.ChoiceField(choices=[('Male', 'Male'), ('Female', 'Female')], widget=forms.Select(attrs={'class': 'form-control'}))
    image = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-control'}))

    transport_fee = forms.DecimalField(max_digits=8, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control transport-field', 'step': '0.01'}))
    admission_discount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        initial=Decimal('0.00'),
        widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )

    # Hidden fields for new admission (forced to 0)
    previous_balance = forms.DecimalField(
        max_digits=10, decimal_places=2,
        required=False,
        initial=Decimal('0.00'),
        widget=forms.HiddenInput()
    )

    closing_balance = forms.DecimalField(
        max_digits=10, decimal_places=2,
        required=False,
        initial=Decimal('0.00'),
        widget=forms.HiddenInput()
    )

    class Meta:
        model = StudentAdmission
        exclude = ['student', 'academic_session', 'operator']
        widgets = {
            'admission_date': DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'total_dues': NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control'}),
            'balance': NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control'}),
            'promoted': forms.CheckboxInput(attrs={'class': 'form-check-input', 'readonly': 'readonly'}),
            'class_name': forms.Select(attrs={'class': 'form-control'}),
            'section': forms.Select(attrs={'class': 'form-control'}),
            'roll_number': forms.NumberInput(attrs={'class': 'form-control'}),
            'class_teacher': forms.TextInput(attrs={'class': 'form-control'}),
            'admission_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'tuition_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'exam_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'book_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'uniform_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'other_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'promotion_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'transport': forms.Select(attrs={'class': 'form-control'}),
            'vehicle_no': forms.TextInput(attrs={'class': 'form-control'}),
            'route': forms.TextInput(attrs={'class': 'form-control'}),
            'driver_contact': forms.TextInput(attrs={'class': 'form-control'}),
            'discount': forms.NumberInput(attrs={'class': 'form-control'}),
            'discount_behalf': forms.Select(attrs={'class': 'form-control'}),
            'received': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        self.fields['section'].choices = [('', 'Select Section')] + SECTION_CHOICES
        self.fields['discount'].label = 'Monthly Fee Discount'
        self.fields['discount_behalf'].choices = [('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES

        self.helper = FormHelper()
        self.helper.form_method = 'POST'
        self.helper.add_input(Submit('submit', 'Admission'))

        self.fields['promoted'].initial = False
        self.fields['promoted'].widget.attrs['disabled'] = True
        self.fields['admission_discount'].initial = Decimal('0.00')

        # Force defaults for new admission
        self.fields['previous_balance'].initial = Decimal('0.00')
        self.fields['closing_balance'].initial = Decimal('0.00')

        transport_fields = ['vehicle_no', 'route', 'driver_contact', 'transport_fee']
        for field in transport_fields:
            self.fields[field].widget.attrs.update({'class': 'form-control transport-field'})

    def clean(self):
        cleaned_data = super().clean()

        # Force zero for new admissions
        cleaned_data['previous_balance'] = Decimal('0.00')
        cleaned_data['closing_balance'] = Decimal('0.00')

        # Roll number validation
        roll_number = cleaned_data.get('roll_number')
        if roll_number is None or roll_number == '':
            self.add_error('roll_number', 'Roll number is required.')
        else:
            try:
                cleaned_data['roll_number'] = int(roll_number)
            except (ValueError, TypeError):
                self.add_error('roll_number', 'Roll number must be a valid integer.')

        if not cleaned_data.get('class_name'):
            self.add_error('class_name', 'Class name is required.')
        if not cleaned_data.get('section'):
            self.add_error('section', 'Section is required.')

        # === NEW: Roll number uniqueness check (class + section + session) ===
        # Prevents the DB UNIQUE constraint from being hit raw — gives a
        # clean form error instead of an IntegrityError crash.
        if (
            not self.errors.get('roll_number')
            and cleaned_data.get('class_name')
            and cleaned_data.get('section')
            and self.request
            and self.request.user.school
        ):
            academic_session = AcademicSession.objects.filter(
                is_active=True,
                school=self.request.user.school,
            ).first()

            if academic_session:
                conflict_exists = StudentAdmission.objects.filter(
                    academic_session = academic_session,
                    class_name        = cleaned_data['class_name'],
                    section           = cleaned_data['section'],
                    roll_number       = cleaned_data['roll_number'],
                ).exists()

                if conflict_exists:
                    self.add_error(
                        'roll_number',
                        f"Roll number {cleaned_data['roll_number']} is already assigned "
                        f"in {cleaned_data['class_name']} - Section {cleaned_data['section']} "
                        f"for {academic_session.session_name}."
                    )

        cleaned_data = validate_transport(cleaned_data, self)

        # Default fee fields to zero
        for field in ['admission_fee', 'tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee', 'other_fee']:
            if cleaned_data.get(field) is None:
                cleaned_data[field] = Decimal('0.00')

        cleaned_data['admission_discount'] = cleaned_data.get('admission_discount') or Decimal('0.00')
        cleaned_data['promotion_fee'] = Decimal('0.00')
        cleaned_data['promoted'] = False

        # Discount validation
        if cleaned_data.get('discount', Decimal('0.00')) > 0 and not cleaned_data.get('discount_behalf'):
            self.add_error('discount_behalf', 'Please specify the behalf for the discount.')

        return cleaned_data

    def save(self, commit=True):
        admission = super().save(commit=False)

        admission.academic_session = AcademicSession.objects.filter(
            is_active=True, 
            school=self.request.user.school
        ).first() if self.request and self.request.user.school else None

        admission.operator = self.request.user if self.request else None
        admission.admission_date = datetime.date.today()
        admission.status = True
        admission.promoted = False
        admission.previous_balance = Decimal('0.00')
        admission.closing_balance = admission.balance or Decimal('0.00')

        admission.admission_fee = admission.admission_fee or Decimal('0.00')
        admission.admission_discount = self.cleaned_data.get('admission_discount') or Decimal('0.00')
        admission.total_dues = (
            (admission.admission_fee or Decimal('0.00')) +
            (admission.tuition_fee or Decimal('0.00')) +
            (admission.exam_fee or Decimal('0.00')) +
            (admission.book_fee or Decimal('0.00')) +
            (admission.uniform_fee or Decimal('0.00')) +
            (admission.other_fee or Decimal('0.00')) +
            (admission.promotion_fee or Decimal('0.00')) +
            (admission.transport_fee or Decimal('0.00')) -
            (admission.discount or Decimal('0.00')) -
            (admission.admission_discount or Decimal('0.00'))
        )
        admission.balance = admission.total_dues - (admission.received or Decimal('0.00'))

        if admission.roll_number:
            admission.roll_number = int(admission.roll_number)

        if commit:
            admission.save()

        return admission

class MonthlyFeeForm(forms.ModelForm):
    month = forms.CharField(widget=forms.HiddenInput(), required=True)
    year = forms.IntegerField(widget=forms.HiddenInput(), required=True)
    
    received = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=True,  # Changed to True for better UX (you can revert if needed)
        widget=forms.NumberInput(attrs={
            'step': '0.01',
            'class': 'form-control form-control-lg received-input',
            'placeholder': '0.00',
            'min': '0'
        })
    )

    class Meta:
        model = MonthlyFee
        fields = [
            'student_admission', 'month', 'year', 'previous_balance',
            'monthly_fee', 'transport_fee', 'total_dues', 'received',
            'current_balance', 'reviewed_by'
        ]
        widgets = {
            'student_admission': forms.HiddenInput(),
            'year': forms.HiddenInput(),
            'previous_balance': forms.HiddenInput(),
            'monthly_fee': forms.HiddenInput(),
            'transport_fee': forms.HiddenInput(),
            'total_dues': forms.HiddenInput(),
            'current_balance': forms.TextInput(attrs={'readonly': 'readonly', 'class': 'form-control'}),
            'reviewed_by': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        initial = kwargs.get('initial', {})
        student_admission = initial.get('student_admission', None)

        super().__init__(*args, **kwargs)

        # Student queryset restriction
        if student_admission and self.request and self.request.user.school:
            self.fields['student_admission'].queryset = StudentAdmission.objects.filter(
                pk=student_admission.pk,
                student__school=self.request.user.school
            )
            self.fields['student_admission'].initial = student_admission.pk
        else:
            self.fields['student_admission'].queryset = StudentAdmission.objects.none()

        # Apply base classes
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})

        # Required flags
        self.fields['received'].required = True
        self.fields['student_admission'].required = True
        self.fields['previous_balance'].required = False
        self.fields['monthly_fee'].required = False
        self.fields['transport_fee'].required = False
        self.fields['year'].required = True
        self.fields['current_balance'].required = False

        # Valid months logic (unchanged)
        self.valid_months = []
        if self.request and self.request.user.school:
            try:
                active_session = AcademicSession.objects.get(
                    is_active=True,
                    school=self.request.user.school
                )
                start_month_num = (active_session.start_date + relativedelta(months=1)).month
                end_month_num = active_session.end_date.month
                year = active_session.start_date.year

                for month_num in range(start_month_num, 13):
                    month_name = datetime.datetime(1900, month_num, 1).strftime('%B')
                    self.valid_months.append(month_name)

                if active_session.end_date.year > year:
                    for month_num in range(1, end_month_num + 1):
                        month_name = datetime.datetime(1900, month_num, 1).strftime('%B')
                        self.valid_months.append(month_name)

                self.fields['year'].initial = active_session.start_date.year
            except AcademicSession.DoesNotExist:
                self.valid_months = []
                self.fields['year'].initial = None

        # Disable fields only for existing records (unchanged core logic)
        if self.instance and self.instance.pk:
            for f in ['previous_balance', 'monthly_fee', 'transport_fee', 'total_dues']:
                if f in self.fields:
                    self.fields[f].disabled = True
        elif student_admission:
            # Initial values for new payment form
            previous_balance = Decimal(str(initial.get('previous_balance', '0.00')))
            monthly_fee_value = Decimal(str(initial.get('monthly_fee', '0.00')))
            transport_fee_value = Decimal(str(initial.get('transport_fee', '0.00')))
            total_dues = Decimal(str(initial.get('total_dues', '0.00')))
            received = Decimal(str(initial.get('received', '0.00')))

            self.fields['previous_balance'].initial = previous_balance
            self.fields['monthly_fee'].initial = monthly_fee_value
            self.fields['transport_fee'].initial = transport_fee_value
            self.fields['total_dues'].initial = total_dues
            self.fields['received'].initial = received
            self.fields['current_balance'].initial = total_dues - received
        else:
            for field in ['previous_balance', 'monthly_fee', 'transport_fee',
                          'total_dues', 'received', 'current_balance']:
                if field in self.fields:
                    self.fields[field].initial = Decimal('0.00')

        # Default reviewer
        if self.request and self.request.user:
            self.fields['reviewed_by'].initial = self.request.user.username

    def clean(self):
        cleaned_data = super().clean()
        received = cleaned_data.get('received')
        total_dues = cleaned_data.get('total_dues')
        student_admission = cleaned_data.get('student_admission')
        year = cleaned_data.get('year')

        if received is None:
            self.add_error('received', "Received amount is required.")
        elif received < Decimal('0.00'):
            self.add_error('received', "Received amount cannot be negative.")
        elif total_dues is not None and received > total_dues:
            self.add_error('received', "Received amount cannot exceed total dues.")

        if not student_admission:
            self.add_error('student_admission', "Student admission is required.")

        if not year:
            self.add_error('year', "Year is required.")

        # Auto-calculate current balance
        cleaned_data['current_balance'] = (total_dues or Decimal('0.00')) - (received or Decimal('0.00'))

        return cleaned_data

    def clean_month(self):
        month = self.cleaned_data.get('month')
        if not month:
            raise forms.ValidationError("Month is required.")
        if month not in self.valid_months:
            raise forms.ValidationError(
                f"Invalid month: {month}. Valid choices are {', '.join(self.valid_months)}."
            )
        return month



# Assuming these helpers exist in your project path structure
# from .utils import next_available_roll, validate_transport, calculate_total_dues
# from .models import StudentAdmission, AcademicSession, MonthlyFee, FeeStructure, CLASS_PROGRESSION

"""gender = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
        required=False
    )"""

class PromoteExistingStudentForm(forms.ModelForm):
    # Student Profile - Mostly Readonly
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}))
    father_guardian_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}))
    contact = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': 'form-control'}))
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'readonly': 'readonly'}))
    gender = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
        required=False
    )
    image = forms.ImageField(required=False, label="Update Image", widget=forms.FileInput(attrs={'class': 'form-control'}))

    # Previous Balance - Always Carry (Readonly)
    previous_balance = forms.DecimalField(
        max_digits=10, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control bg-light'})
    )

    # Fee Fields
    tuition_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    exam_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    book_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    uniform_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    promotion_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    other_fee = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    transport_fee = forms.DecimalField(max_digits=8, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control transport-field'}))
    discount = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    admission_discount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    discount_behalf = forms.ChoiceField(
        choices=[('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    received = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False, initial=Decimal('0.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = StudentAdmission
        fields = ['class_name', 'section', 'roll_number', 'transport', 'vehicle_no', 'route', 'driver_contact', 'total_dues', 'balance']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        self.student_admission = kwargs.pop('student_admission', None)
        self.filtered_class = kwargs.pop('filtered_class', None)
        self.filtered_section = kwargs.pop('filtered_section', None)

        super().__init__(*args, **kwargs)

        self.helper = FormHelper()
        self.helper.form_method = 'POST'
        self.helper.add_input(Submit('submit', 'Promote Student', css_class='btn btn-success'))

        if self.student_admission and self.request:
            self._prefill_for_promotion()

    def _prefill_for_promotion(self):
        old = self.student_admission
        student = old.student

        # Student Info (mostly readonly)
        self.fields['first_name'].initial = student.first_name
        self.fields['last_name'].initial = student.last_name
        self.fields['father_guardian_name'].initial = student.father_guardian_name
        self.fields['contact'].initial = student.contact
        self.fields['date_of_birth'].initial = student.date_of_birth
        self.fields['gender'].initial = student.gender
        self.fields['image'].initial = student.image

        # === CLASS PROGRESSION LOGIC ===
        next_class = CLASS_PROGRESSION.get(old.class_name, old.class_name)
        
        # Priority: Use filtered_class from URL only if it's different, otherwise use auto-progression
        if self.filtered_class and self.filtered_class != old.class_name:
            target_class = self.filtered_class
        else:
            target_class = next_class

        self.fields['class_name'].initial = target_class
        self.fields['section'].initial = self.filtered_section or old.section

        # Roll Number
        try:
            active_session = AcademicSession.objects.get(is_active=True, school=self.request.user.school)
            suggested_roll = next_available_roll(active_session, target_class, self.fields['section'].initial)
            self.fields['roll_number'].initial = suggested_roll
        except Exception:
            self.fields['roll_number'].initial = old.roll_number

        # Transport & Discount
        self.fields['transport'].initial = old.transport
        self.fields['transport_fee'].initial = old.transport_fee or Decimal('0.00')
        self.fields['vehicle_no'].initial = old.vehicle_no
        self.fields['route'].initial = old.route
        self.fields['driver_contact'].initial = old.driver_contact
        self.fields['discount'].initial = old.discount or Decimal('0.00')
        self.fields['admission_discount'].initial = old.admission_discount or Decimal('0.00')
        self.fields['discount_behalf'].initial = old.discount_behalf
        self.fields['received'].initial = Decimal('0.00')

        # Previous Balance
        self.fields['previous_balance'].initial = get_pending_balance(old)

        # === Load Fee Structure for TARGET (next) class ===
        try:
            fee = FeeStructure.objects.filter(
                school=self.request.user.school,
                academic_session__is_active=True,
                class_name=target_class
            ).first()

            if fee:
                self.fields['tuition_fee'].initial = fee.tuition_fee
                self.fields['exam_fee'].initial = fee.paper_money
                self.fields['book_fee'].initial = fee.books_dues
                self.fields['uniform_fee'].initial = fee.uniform_dues
                self.fields['promotion_fee'].initial = fee.promotion_fee or Decimal('0.00')
                self.fields['other_fee'].initial = fee.other_charges or Decimal('0.00')
        except Exception as e:
            print(f"Warning: Fee structure not found for {target_class}: {e}")


    def clean(self):
        cleaned_data = super().clean()
        cleaned_data = validate_transport(cleaned_data, self)

        if not cleaned_data.get('section'):
            self.add_error('section', 'Section is required.')

        if cleaned_data.get('discount', Decimal('0.00')) > 0 and not cleaned_data.get('discount_behalf'):
            self.add_error('discount_behalf', 'Please specify who approved the discount.')

        total_dues = calculate_total_dues(cleaned_data)
        cleaned_data['total_dues'] = total_dues
        cleaned_data['balance'] = total_dues - cleaned_data.get('received', Decimal('0.00'))

        return cleaned_data

    def save(self, commit=True):
        if not self.student_admission:
            raise ValueError("Old admission record is required.")

        old     = self.student_admission
        student = old.student

        if self.cleaned_data.get('image'):
            student.image = self.cleaned_data['image']
            student.save(update_fields=['image'])

        self.instance.pk = None
        new_admission = super().save(commit=False)

        new_admission.student          = student
        new_admission.academic_session = AcademicSession.objects.get(
            is_active=True, school=self.request.user.school
        )
        new_admission.promoted_from    = old
        new_admission.promoted         = True
        new_admission.status           = True
        new_admission.operator         = self.request.user
        new_admission.admission_date   = datetime.date.today()

        # === FIX: these fields are declared on the form but NOT in
        # Meta.fields, so ModelForm.save() never copies them onto the
        # instance automatically. Must be assigned explicitly. ===
        new_admission.tuition_fee      = self.cleaned_data.get('tuition_fee') or Decimal('0.00')
        new_admission.exam_fee         = self.cleaned_data.get('exam_fee') or Decimal('0.00')
        new_admission.book_fee         = self.cleaned_data.get('book_fee') or Decimal('0.00')
        new_admission.uniform_fee      = self.cleaned_data.get('uniform_fee') or Decimal('0.00')
        new_admission.promotion_fee    = self.cleaned_data.get('promotion_fee') or Decimal('0.00')
        new_admission.other_fee        = self.cleaned_data.get('other_fee') or Decimal('0.00')
        new_admission.discount         = self.cleaned_data.get('discount') or Decimal('0.00')
        new_admission.admission_discount = self.cleaned_data.get('admission_discount') or Decimal('0.00')
        new_admission.discount_behalf  = self.cleaned_data.get('discount_behalf')
        new_admission.transport_fee    = self.cleaned_data.get('transport_fee') or Decimal('0.00')

        # One-time fees for the promotion month
        new_admission.admission_fee    = Decimal('0.00')
        new_admission.previous_balance = self.cleaned_data['previous_balance']
        new_admission.received         = self.cleaned_data.get('received') or Decimal('0.00')
        new_admission.total_dues       = self.cleaned_data['total_dues']
        new_admission.balance          = self.cleaned_data['balance']
        new_admission.closing_balance  = self.cleaned_data['balance']

        if commit:
            new_admission.save()
            StudentAdmission.objects.filter(pk=old.pk).update(
                promoted=True,
                status=False,
            )

        return new_admission

class NotPromoteStudentForm(PromoteExistingStudentForm):
    """
    Same review flow as PromoteExistingStudentForm (transport, discount,
    fee adjustments, roll number) but the student stays in the SAME
    class — used when a student fails to advance and the admin needs
    to review/adjust their fees for the repeat year.
    """

    promotion_fee = forms.DecimalField(
        max_digits=10, decimal_places=2,
        required=False,
        initial=Decimal('0.00'),
        widget=forms.HiddenInput()
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Replace inherited "Promote Student" button
        self.helper.inputs = []
        self.helper.add_input(Submit('submit', 'Mark as Not Promoted', css_class='btn btn-danger'))
        # Class cannot be changed here — disabled (not just readonly,
        # since <select> ignores readonly; disabled also protects
        # cleaned_data from any tampered POST value).
        self.fields['class_name'].disabled = True

    def _prefill_for_promotion(self):
        old     = self.student_admission
        student = old.student

        self.fields['first_name'].initial = student.first_name
        self.fields['last_name'].initial = student.last_name
        self.fields['father_guardian_name'].initial = student.father_guardian_name
        self.fields['contact'].initial = student.contact
        self.fields['date_of_birth'].initial = student.date_of_birth
        self.fields['gender'].initial = student.gender
        self.fields['image'].initial = student.image

        # SAME class — no progression
        target_class = old.class_name
        self.fields['class_name'].initial = target_class
        self.fields['section'].initial = self.filtered_section or old.section

        try:
            active_session = AcademicSession.objects.get(is_active=True, school=self.request.user.school)
            suggested_roll = next_available_roll(active_session, target_class, self.fields['section'].initial)
            self.fields['roll_number'].initial = suggested_roll
        except Exception:
            self.fields['roll_number'].initial = old.roll_number

        # Transport — fully carried forward (vehicle/route/driver fix included)
        self.fields['transport'].initial       = old.transport
        self.fields['transport_fee'].initial   = old.transport_fee or Decimal('0.00')
        self.fields['vehicle_no'].initial      = old.vehicle_no
        self.fields['route'].initial           = old.route
        self.fields['driver_contact'].initial  = old.driver_contact
        self.fields['discount'].initial        = old.discount or Decimal('0.00')
        self.fields['discount_behalf'].initial = old.discount_behalf
        self.fields['received'].initial        = Decimal('0.00')

        self.fields['previous_balance'].initial = get_pending_balance(old)

        try:
            fee = FeeStructure.objects.filter(
                school           = self.request.user.school,
                academic_session__is_active = True,
                class_name       = target_class,
            ).first()
            if fee:
                self.fields['tuition_fee'].initial   = fee.tuition_fee
                self.fields['exam_fee'].initial      = fee.paper_money
                self.fields['book_fee'].initial      = fee.books_dues
                self.fields['uniform_fee'].initial   = fee.uniform_dues
                self.fields['promotion_fee'].initial = Decimal('0.00')   # no promotion fee on repeat
                self.fields['other_fee'].initial     = fee.other_charges or Decimal('0.00')
        except Exception as e:
            print(f"Warning: Fee structure not found for {target_class}: {e}")

    def clean(self):
        cleaned_data = super().clean()

        # === Roll number uniqueness check (was missing — DB constraint
        # would otherwise crash on save instead of showing a form error) ===
        if (
            not self.errors.get('roll_number')
            and cleaned_data.get('class_name')
            and cleaned_data.get('section')
            and self.request and self.request.user.school
        ):
            active_session = AcademicSession.objects.filter(
                is_active=True, school=self.request.user.school
            ).first()
            if active_session:
                conflict = StudentAdmission.objects.filter(
                    academic_session = active_session,
                    class_name       = cleaned_data['class_name'],
                    section          = cleaned_data['section'],
                    roll_number      = cleaned_data.get('roll_number'),
                ).exists()
                if conflict:
                    self.add_error(
                        'roll_number',
                        f"Roll number {cleaned_data.get('roll_number')} is already assigned "
                        f"in {cleaned_data['class_name']} - Section {cleaned_data['section']} "
                        f"for this session."
                    )

        cleaned_data['promotion_fee'] = Decimal('0.00')   # never charged on repeat
        return cleaned_data

    def save(self, commit=True):
        if not self.student_admission:
            raise ValueError("Old admission record is required.")

        old     = self.student_admission
        student = old.student

        if self.cleaned_data.get('image'):
            student.image = self.cleaned_data['image']
            student.save(update_fields=['image'])

        self.instance.pk = None
        # Skip PromoteExistingStudentForm.save() — call ModelForm.save() directly
        new_admission = forms.ModelForm.save(self, commit=False)

        new_admission.student          = student
        new_admission.academic_session = AcademicSession.objects.get(
            is_active=True, school=self.request.user.school
        )
        new_admission.promoted_from = old
        new_admission.promoted      = False     # NOT promoted — stayed in same class
        new_admission.status        = True
        new_admission.operator      = self.request.user
        new_admission.admission_date = datetime.date.today()

        new_admission.tuition_fee     = self.cleaned_data.get('tuition_fee') or Decimal('0.00')
        new_admission.exam_fee        = self.cleaned_data.get('exam_fee') or Decimal('0.00')
        new_admission.book_fee        = self.cleaned_data.get('book_fee') or Decimal('0.00')
        new_admission.uniform_fee     = self.cleaned_data.get('uniform_fee') or Decimal('0.00')
        new_admission.promotion_fee   = Decimal('0.00')
        new_admission.other_fee       = self.cleaned_data.get('other_fee') or Decimal('0.00')
        new_admission.discount        = self.cleaned_data.get('discount') or Decimal('0.00')
        new_admission.discount_behalf = self.cleaned_data.get('discount_behalf')
        new_admission.transport_fee   = self.cleaned_data.get('transport_fee') or Decimal('0.00')

        new_admission.admission_fee    = Decimal('0.00')
        new_admission.previous_balance = self.cleaned_data['previous_balance']
        new_admission.received         = self.cleaned_data.get('received') or Decimal('0.00')
        new_admission.total_dues       = self.cleaned_data['total_dues']
        new_admission.balance          = self.cleaned_data['balance']
        new_admission.closing_balance  = self.cleaned_data['balance']

        if commit:
            new_admission.save()
            StudentAdmission.objects.filter(pk=old.pk).update(
                status            = False,
                failed_to_promote = True,
            )

        return new_admission

class TeacherForm(forms.ModelForm):
    username = forms.CharField(
        max_length=150, 
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}), 
        required=False,
        help_text="Leave blank if not changing password"
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}), 
        required=False
    )

    class Meta:
        model = Teacher
        fields = [
            'name', 'contact', 'cnic', 'gender', 'class_name', 'section', 
            'subjects', 'education', 'salary', 'date_of_join', 'dob', 
            'address', 'children_in_school'
        ]
        widgets = {
            'date_of_join': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'address': forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}),
            'class_name': forms.Select(attrs={'class': 'form-control'}),
            'section': forms.Select(attrs={'class': 'form-control'}),
            'gender': forms.Select(attrs={'class': 'form-control'}),
            'subjects': forms.SelectMultiple(attrs={
                'class': 'form-control', 
                'size': '10', 
                'style': 'height: 200px;'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Choose a username',
            'autocomplete': 'username',
        })
        self.fields['password'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Leave blank to auto-generate a secure password',
            'autocomplete': 'new-password',
        })
        self.fields['confirm_password'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Confirm password',
            'autocomplete': 'new-password',
        })
 
        if self.request and self.request.user.school:
            school = self.request.user.school
 
            # ── Always show ALL classes and sections ──────────
            # Section filtering is handled by JavaScript so the
            # admin can see which sections are taken vs free
            # without the form hiding options completely.
            self.fields['class_name'].choices = (
                [('', 'Select Class')] + list(CLASS_CHOICES)
            )
            self.fields['section'].choices = (
                [('', 'Select Section')] + list(SECTION_CHOICES)
            )
 
            # ── FIX: always show ALL subjects ─────────────────
            # A subject can be taught by more than one teacher
            # (e.g. two Maths teachers for different classes).
            # The old code excluded any subject already assigned
            # to any teacher, which incorrectly hid subjects from
            # the list even when a new teacher could teach them.
            self.fields['subjects'].queryset = Subject.objects.filter(
                school=school
            ).order_by('name')
 
        # Set username initial in edit mode
        if self.instance.pk and hasattr(self.instance, 'user') and self.instance.user:
            self.fields['username'].initial = self.instance.user.username
 
    def clean(self):
        cleaned_data = super().clean()
        class_name = cleaned_data.get('class_name')
        section    = cleaned_data.get('section')

        if cleaned_data.get('username'):
            cleaned_data['username'] = str(cleaned_data['username']).strip()
 
        if class_name and section and self.request:
            # ── FIX: runs on BOTH create AND edit ────────────
            # Previously only checked on create (not self.instance.pk),
            # which allowed editing a teacher onto an already-taken slot.
            qs = Teacher.objects.filter(
                school     = self.request.user.school,
                class_name = class_name,
                section    = section,
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)   # exclude self on edit
            if qs.exists():
                self.add_error(
                    'section',
                    f"{class_name} - {section} is already assigned to another teacher."
                )
 
        # Password validation (unchanged)
        password         = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        if not self.instance.pk:
            if not password:
                cleaned_data['password'] = TemporaryPassword.generate_password(10)
                cleaned_data['confirm_password'] = cleaned_data['password']
            elif password != confirm_password:
                self.add_error('confirm_password', 'Passwords do not match.')
        elif password and password != confirm_password:
            self.add_error('confirm_password', 'Passwords do not match.')
 
        return cleaned_data

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if username:
            qs = CustomUser.objects.filter(username=username)
            if self.instance.pk and self.instance.user:
                qs = qs.exclude(pk=self.instance.user.pk)
            if qs.exists():
                raise ValidationError("A user with that username already exists.")
        return username

    def clean_cnic(self):
        cnic = self.cleaned_data.get('cnic')
        if cnic and self.request and self.request.user.school:
            qs = Teacher.objects.filter(cnic=cnic, school=self.request.user.school)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError("A teacher with this CNIC already exists in your school.")
        return cnic

    def clean_children_in_school(self):
        children = self.cleaned_data.get('children_in_school', 0)
        if children < 0:
            raise ValidationError("Number of children cannot be negative.")
        return children

    def save(self, commit=True):
        teacher = super().save(commit=False)
        teacher.school = self.request.user.school
        generated_password = None

        if commit:
            if self.instance.pk and self.instance.user:
                user = self.instance.user
                user.username = self.cleaned_data['username']
                if self.cleaned_data.get('password'):
                    user.set_password(self.cleaned_data['password'])
                user.save()
            else:
                password = self.cleaned_data.get('password') or TemporaryPassword.generate_password(10)
                generated_password = password
                user = CustomUser.objects.create_user(
                    username=self.cleaned_data['username'],
                    password=password,
                    user_type=4,
                    school=self.request.user.school
                )
                teacher.user = user

            teacher.save()
            self.save_m2m()

        self.generated_password = generated_password
        return teacher

class StudentResultForm(forms.ModelForm):
    class Meta:
        model = StudentResult
        fields = ['student_admission', 'subject', 'theory', 'practical', 'obtained_marks', 'total_marks', 'exam_type']
        widgets = {
            'student_admission': forms.Select(),
            'subject': forms.Select(),
            'exam_type': forms.Select(),
            'theory': forms.NumberInput(attrs={'step': '0.1', 'min': '0', 'placeholder': 'Enter theory marks (optional)'}),
            'practical': forms.NumberInput(attrs={'step': '0.1', 'min': '0', 'placeholder': 'Enter practical marks (optional)'}),
            'obtained_marks': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
            'total_marks': forms.NumberInput(attrs={'step': '0.1', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        self.teacher = kwargs.pop('teacher', None)
        super().__init__(*args, **kwargs)
        if self.teacher:
            self.fields['student_admission'].queryset = StudentAdmission.objects.filter(
                student__school=self.teacher.school,
                class_name=self.teacher.class_name,
                section=self.teacher.section,
                academic_session__is_active=True,
                status=True,
            )
            self.fields['subject'].queryset = self.teacher.subjects.all()
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})

    def clean(self):
        cleaned_data = super().clean()
        theory = cleaned_data.get('theory') or 0
        practical = cleaned_data.get('practical') or 0
        obtained_marks = cleaned_data.get('obtained_marks')
        total_marks = cleaned_data.get('total_marks')

        # If theory/practical provided, derive total_marks from them
        if theory > 0 or practical > 0:
            cleaned_data['total_marks'] = theory + practical
            total_marks = cleaned_data['total_marks']

        # Now validate total_marks — safely handles None
        if total_marks is None or total_marks <= 0:
            raise forms.ValidationError("Total marks must be greater than 0.")

        if obtained_marks is None:
            raise forms.ValidationError("Obtained marks are required.")

        if obtained_marks > total_marks:
            raise forms.ValidationError("Obtained marks cannot exceed total marks.")

        if (theory + practical) > 0 and obtained_marks > (theory + practical):
            raise forms.ValidationError(
            "Obtained marks cannot exceed the sum of theory and practical marks."
            )

        if self.teacher:
            student_admission = cleaned_data.get('student_admission')
            subject = cleaned_data.get('subject')
            if student_admission and (
                student_admission.student.school_id != self.teacher.school_id
                or student_admission.class_name != self.teacher.class_name
                or student_admission.section != self.teacher.section
                or not student_admission.status
            ):
                raise forms.ValidationError("Selected student is not in your active class.")
            if subject and not self.teacher.subjects.filter(pk=subject.pk).exists():
                raise forms.ValidationError("You are not assigned to teach this subject.")

        return cleaned_data


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter subject name'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)



class SyllabusForm(forms.ModelForm):
    class Meta:
        model = Syllabus
        fields = ['subject', 'class_name', 'description', 'file']
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-control'}),
            'class_name': forms.Select(attrs={'class': 'form-control'}, choices=CLASS_CHOICES),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'file': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if self.user and self.user.school:
            # Filter subjects by school
            self.fields['subject'].queryset = Subject.objects.filter(school=self.user.school)

            # If user is a teacher, restrict subject/class to their assignments
            if self.user.user_type == 4:
                teacher = Teacher.objects.filter(user=self.user, school=self.user.school).first()
                if teacher:
                    self.fields['subject'].queryset = teacher.subjects.all()
                    self.fields['class_name'].initial = teacher.class_name

    def clean(self):
        cleaned_data = super().clean()
        subject = cleaned_data.get('subject')
        class_name = cleaned_data.get('class_name')
        academic_session = AcademicSession.objects.filter(is_active=True, school=self.user.school).first()

        if not academic_session:
            raise forms.ValidationError("No active academic session found.")

        # Prevent duplicate syllabus for same subject + class + session
        if subject and class_name:
            qs = Syllabus.objects.filter(
                subject=subject,
                class_name=class_name,
                academic_session=academic_session,
                school=self.user.school
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("A syllabus for this subject, class, and session already exists.")

        return cleaned_data

    def save(self, commit=True):
        syllabus = super().save(commit=False)
        syllabus.academic_session = AcademicSession.objects.filter(is_active=True, school=self.user.school).first()
        syllabus.school = self.user.school
        syllabus.uploaded_by = self.user
        if commit:
            syllabus.save()
        return syllabus


class StaffForm(forms.ModelForm):
    class Meta:
        model = Staff
        fields = ['name', 'designation', 'salary', 'date_of_join', 'cnic', 'contact', 'address', 'date_of_birth']
        widgets = {
            'date_of_join': forms.DateInput(attrs={'type': 'date'}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user and self.user.school:
            self.instance.school = self.user.school

    def clean(self):
        cleaned_data = super().clean()
        if not self.user or not self.user.school:
            raise forms.ValidationError("User must be associated with a school.")
        return cleaned_data



class AssetsForm(forms.ModelForm):
    class Meta:
        model  = Assets
        fields = [
            'name', 'purchased_date', 'file_number',
            'value', 'purchased_by', 'description', 'condition',
        ]
        widgets = {
            'purchased_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'}
            ),
            'description': forms.Textarea(
                attrs={'rows': 3, 'class': 'form-control'}
            ),
            'name':         forms.TextInput(attrs={'class': 'form-control'}),
            'file_number':  forms.TextInput(attrs={'class': 'form-control'}),
            'value':        forms.NumberInput(
                attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}
            ),
            'purchased_by': forms.TextInput(attrs={'class': 'form-control'}),
            'condition':    forms.Select(attrs={'class': 'form-control'}),
        }
 
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
 
        # Default date for new records
        if not self.instance.pk:
            self.initial['purchased_date'] = timezone.now().date()
 
        # file_number is optional after removing unique constraint
        self.fields['file_number'].required = False
 
    def clean(self):
        cleaned_data = super().clean()
 
        # Only check that the user has a school — assets are NOT
        # session-scoped, so we never block on "no active session".
        if not self.user or not self.user.school:
            raise forms.ValidationError(
                "Your account is not associated with a school."
            )
 
        return cleaned_data
 
    def save(self, commit=True):
        asset = super().save(commit=False)
        # Always stamp the school from the logged-in user
        if self.user and self.user.school:
            asset.school = self.user.school
        if commit:
            asset.save()
        return asset



class ExpensesForm(forms.ModelForm):
    period = forms.ChoiceField(
        choices=[], 
        required=True,
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = Expenses
        fields = [
            'expense_type', 'expense_name', 'price', 'payment_date',
            'payment_by', 'file_number', 'period', 'description'
        ]
        widgets = {
            'payment_date': forms.DateInput(attrs={
                'type': 'date', 
                'class': 'form-control'
            }),
            'description': forms.Textarea(attrs={
                'rows': 4, 
                'class': 'form-control'
            }),
            'price': forms.NumberInput(attrs={
                'class': 'form-control', 
                'step': '0.01'
            }),
            'payment_by': forms.TextInput(attrs={'class': 'form-control'}),
            'file_number': forms.TextInput(attrs={'class': 'form-control'}),
            'expense_type': forms.Select(attrs={'class': 'form-control'}),
            'expense_name': forms.Select(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Associate expense with user's school
        if self.user and self.user.school:
            self.instance.school = self.user.school

        # Set default payment date for new records
        if not self.instance.pk:
            self.initial.setdefault('payment_date', timezone.now().date())

        # ==================== DYNAMIC CHOICES LOGIC ====================
        # Priority: POST data > existing instance > default
        expense_type = (
            self.data.get('expense_type') if self.data else None
            or getattr(self.instance, 'expense_type', None)
            or 'Monthly'
        )

        # Update choices and labels based on expense type
        if expense_type == 'Daily':
            self.fields['expense_name'].choices = Expenses.DAILY_NAME_CHOICES
            self.fields['period'].choices = Expenses.DAY_CHOICES
            self.fields['period'].label = "Day Name"
        else:
            self.fields['expense_name'].choices = Expenses.MONTHLY_NAME_CHOICES
            self.fields['period'].choices = Expenses.MONTH_CHOICES
            self.fields['period'].label = "Month Name"

        # Preserve period value when editing
        if self.instance.pk and self.instance.period:
            self.initial['period'] = self.instance.period

    def clean(self):
        cleaned = super().clean()

        if not self.user or not self.user.school:
            raise ValidationError("User must be associated with a school.")

        if not AcademicSession.objects.filter(
            school=self.user.school, is_active=True
        ).exists():
            raise ValidationError("No active academic session found for this school.")

        return cleaned


class TransportForm(forms.ModelForm):
    class Meta:
        model = Transport
        fields = ['vehicle_number', 'driver_name', 'driver_cnic', 'address', 'date_of_joining', 'date_of_birth', 'number_of_students', 'route', 'contact']
        widgets = {
            'date_of_joining': forms.DateInput(attrs={'type': 'date'}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 4}),
            'route': forms.TextInput(attrs={'placeholder': 'e.g., Main City to School Campus'}),
            'contact': forms.TextInput(attrs={'placeholder': 'e.g., +923001234567'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user and self.user.school:
            self.instance.school = self.user.school
        # Set default dates
        if not self.instance.pk:
            self.initial['date_of_joining'] = timezone.now().date()
            self.initial['date_of_birth'] = timezone.now().date()

    def clean(self):
        cleaned_data = super().clean()
        if not self.user or not self.user.school:
            raise forms.ValidationError("User must be associated with a school.")
        if not AcademicSession.objects.filter(school=self.user.school, is_active=True).exists():
            raise forms.ValidationError("No active academic session found for this school.")
        return cleaned_data




class EventsForm(forms.ModelForm):
    class Meta:
        model = Events
        fields = ['event_type', 'event_for', 'description', 'event_date', 'announcement_date', 'arranged_by']
        widgets = {
            'event_date': forms.DateInput(attrs={'type': 'date'}),
            'announcement_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 4}),
            'arranged_by': forms.TextInput(attrs={'placeholder': 'e.g., School Administration'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user and self.user.school:
            self.instance.school = self.user.school
        if not self.instance.pk:
            self.initial['announcement_date'] = timezone.now().date()

    def clean(self):
        cleaned_data = super().clean()
        if not self.user or not self.user.school:
            raise forms.ValidationError("User must be associated with a school.")
        if not AcademicSession.objects.filter(school=self.user.school, is_active=True).exists():
            raise forms.ValidationError("No active academic session found for this school.")
        event_date = cleaned_data.get('event_date')
        announcement_date = cleaned_data.get('announcement_date')
        if event_date and announcement_date and event_date < announcement_date:
            raise forms.ValidationError("Event date cannot be before announcement date.")
        return cleaned_data
