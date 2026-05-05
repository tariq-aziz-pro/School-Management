from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import CustomUser, School, FeeStructure, AcademicSession, StudentAdmission, MonthlyFee, CLASS_CHOICES, SECTION_CHOICES, Student, SchoolSubscription, Subject, StudentResult, Teacher, Student, TemporaryPassword, Syllabus, Staff, Assets, Expenses, Transport, Events, CLASS_PROGRESSION
from django.utils import timezone
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django.forms import TextInput, NumberInput, DateInput
from decimal import Decimal
from dateutil.relativedelta import relativedelta
import datetime
from django.core.exceptions import ValidationError
from django.contrib.auth.hashers import make_password
from django.contrib.auth.forms import UserCreationForm
import re
import logging
from django.apps import apps
from .utils import get_previous_balance, calculate_total_dues, validate_transport





logger = logging.getLogger(__name__)



ALLOWED_MODELS = ['MonthlyFee', 'StudentAdmission', 'Student', 'StudentResult', 'Assets', 'Expenses']

class AnalyticsForm(forms.Form):
    model_name = forms.ChoiceField(label='Select Table')
    x_axis = forms.ChoiceField(label='Select X Column', required=True)
    y_axis = forms.ChoiceField(label='Select Y Column', required=True)
    chart_type = forms.ChoiceField(
        choices=[('bar', 'Bar Chart'), ('line', 'Line Chart'), ('scatter', 'Scatter')],
        label='Chart Type'
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        models = apps.get_models()
        filtered_models = [
            (model.__name__, model.__name__)
            for model in models
            if model.__name__ in ALLOWED_MODELS and model._meta.app_label == 'main_app'
        ]
        self.fields['model_name'].choices = filtered_models

        data = self.data or self.initial
        model_name = data.get('model_name')

        if model_name:
            try:
                model = apps.get_model('main_app', model_name)
                field_choices = [(f.name, f.name) for f in model._meta.fields if not f.is_relation]

                # All fields for X-axis
                self.fields['x_axis'].choices = field_choices
                # Numeric-only fields for Y-axis
                numeric_choices = [(f.name, f.name) for f in model._meta.fields if hasattr(f, 'get_internal_type') and f.get_internal_type() in ['IntegerField', 'DecimalField', 'FloatField', 'PositiveIntegerField', 'PositiveSmallIntegerField']]
                self.fields['y_axis'].choices = numeric_choices or field_choices
            except LookupError:
                self.fields['x_axis'].choices = []
                self.fields['y_axis'].choices = []



TRANSPORT_CHOICES = [
    ('Paid', 'Paid'),
    ('Free', 'Free'),
    ('No', 'No'),
]


class StudentUserForm(forms.ModelForm):
    student_id = forms.ChoiceField(choices=[], widget=forms.Select(attrs={'class': 'form-control'}), help_text="Select a student ID.")

    class Meta:
        model = CustomUser
        fields = ['student_id']

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        if school:
            # Students without accounts
            students = Student.objects.filter(school=school).exclude(student_id__in=CustomUser.objects.filter(user_type=5).values('username'))
            choices = [(s.student_id, s.student_id) for s in students]
            self.fields['student_id'].choices = choices
            logger.debug(f"StudentUserForm choices for school {school}: {choices}")  # Debug choices

    def clean_student_id(self):
        student_id = self.cleaned_data.get('student_id')
        if not student_id:
            raise forms.ValidationError("Student ID is required.")
        if student_id not in [choice[0] for choice in self.fields['student_id'].choices]:
            raise forms.ValidationError(f"{student_id} is not a valid choice. Ensure the student exists and has no account.")
        if CustomUser.objects.filter(username=student_id).exists():
            raise forms.ValidationError("This student ID is already in use as a username.")
        if not Student.objects.filter(student_id=student_id).exists():
            raise forms.ValidationError("Invalid student ID. Student must exist in the system.")
        return student_id

    def save(self, commit=True):
        logger.debug(f"Saving StudentUserForm: StudentID={self.cleaned_data.get('student_id')}")
        user = super().save(commit=False)
        user.username = self.cleaned_data['student_id']
        user.user_type = 5  # Student
        user.is_active = True
        plain_password = TemporaryPassword.generate_password()
        user.set_password(plain_password)
        if commit:
            try:
                user.save()
                student = Student.objects.get(student_id=self.cleaned_data['student_id'])
                user.school = student.school
                user.save()
                TemporaryPassword.objects.create(user=user, password=plain_password)
                logger.info(f"Student account created: Username={user.username}, School={user.school.school_name}")
            except Exception as e:
                logger.error(f"Error saving student user: {str(e)}")
                raise
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
    school_name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'class': 'form-control'}))
    school_logo = forms.ImageField(required=False, widget=forms.FileInput(attrs={'class': 'form-control'}))
    city = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    owner_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    contact_number = forms.CharField(max_length=15, widget=forms.TextInput(attrs={'class': 'form-control'}), help_text="Enter a valid phone number (10-15 digits, e.g., +923001234567).")
    number_of_students = forms.IntegerField(min_value=0, initial=0, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control'}))

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'password1', 'password2', 'school_name', 'school_logo', 'city', 'owner_name', 'contact_number', 'number_of_students']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'password1': forms.PasswordInput(attrs={'class': 'form-control'}),
            'password2': forms.PasswordInput(attrs={'class': 'form-control'}),
        }

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if len(username) < 1:
            raise forms.ValidationError("Username cannot be empty.")
        if len(username) > 150:
            raise forms.ValidationError("Username cannot exceed 150 characters.")
        if CustomUser.objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean_contact_number(self):
        contact_number = self.cleaned_data.get('contact_number')
        if not re.match(r'^\+?\d{10,15}$', contact_number):
            raise forms.ValidationError("Enter a valid phone number (10-15 digits, optional '+' prefix).")
        return contact_number

    def clean_school_name(self):
        school_name = self.cleaned_data.get('school_name')
        if School.objects.filter(school_name=school_name).exists():
            raise forms.ValidationError("A school with this name already exists.")
        return school_name

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if CustomUser.objects.filter(email=email).exists():
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
            'discount', 'discount_behalf', 'received', 'total_dues', 'balance',
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
            'discount_behalf': forms.Select(attrs={'class': 'form-control'}),
            'received': forms.NumberInput(attrs={'class': 'form-control'}),
            'total_dues': forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['section'].choices = [('', 'Select Section')] + SECTION_CHOICES
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
            Decimal(str(admission.discount or 0.00))
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
        self.fields['discount_behalf'].choices = [('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES
        self.helper = FormHelper()
        self.helper.form_method = 'POST'
        self.helper.add_input(Submit('submit', 'Admission'))
        self.fields['promoted'].initial = False
        self.fields['promoted'].widget.attrs['disabled'] = True
        transport_fields = ['vehicle_no', 'route', 'driver_contact', 'transport_fee']
        for field in transport_fields:
            self.fields[field].widget.attrs.update({'class': 'form-control transport-field'})

    def clean_roll_number(self):
        roll_number = self.cleaned_data.get('roll_number')
        class_name = self.cleaned_data.get('class_name')
        section = self.cleaned_data.get('section')
        if self.request and self.request.user.school:
            current_session = AcademicSession.objects.filter(is_active=True, school=self.request.user.school).first()
            if roll_number is not None:
                try:
                    roll_number = int(roll_number)
                except (ValueError, TypeError):
                    raise forms.ValidationError("Roll number must be a valid integer.")
            else:
                raise forms.ValidationError("Roll number is required.")
            if class_name and section and current_session:
                query = StudentAdmission.objects.filter(
                    roll_number=roll_number, class_name=class_name, section=section,
                    academic_session=current_session, student__school=self.request.user.school
                )
                if self.instance.pk:
                    query = query.exclude(pk=self.instance.pk)
                if query.exists():
                    raise forms.ValidationError(
                        f"Roll number {roll_number} is already assigned in {class_name} Section {section} for the current session."
                    )
            elif not current_session:
                raise forms.ValidationError("No active academic session found for your school.")
        return roll_number

    def clean(self):
        cleaned_data = super().clean()

        # Roll number validation
        roll_number = cleaned_data.get('roll_number')
        if roll_number is None or roll_number == '':
            self.add_error('roll_number', 'Roll number is required.')
        else:
            try:
                cleaned_data['roll_number'] = int(roll_number)
            except (ValueError, TypeError):
                self.add_error('roll_number', 'Roll number must be a valid integer.')

        # Required field checks
        if not cleaned_data.get('class_name'):
            self.add_error('class_name', 'Class name is required.')
        if not cleaned_data.get('section'):
            self.add_error('section', 'Section is required.')

        # Transport validation via helper
        cleaned_data = validate_transport(cleaned_data, self)

        # Default fee fields to zero
        for field in ['admission_fee', 'tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee', 'other_fee']:
            if cleaned_data.get(field) is None:
                cleaned_data[field] = Decimal('0.00')

        cleaned_data['promotion_fee'] = Decimal('0.00')
        cleaned_data['promoted'] = False

        # Discount validation
        if cleaned_data.get('discount', Decimal('0.00')) > 0 and not cleaned_data.get('discount_behalf'):
            self.add_error('discount_behalf', 'Please specify the behalf for the discount.')

        return cleaned_data

    def save(self, commit=True):
        """Only creates/updates StudentAdmission. Student should be created in the view."""
        admission = super().save(commit=False)
    
        # Set required fields
        admission.academic_session = AcademicSession.objects.filter(
            is_active=True, 
            school=self.request.user.school
        ).first() if self.request and self.request.user.school else None
    
        admission.operator = self.request.user if self.request else None
        admission.admission_date = datetime.date.today()
        admission.admission_fee = Decimal('0.00')
        admission.status = True
        admission.promoted = False

        if admission.roll_number:
            admission.roll_number = int(admission.roll_number)

        if commit:
            if not all([admission.roll_number, admission.class_name, 
                      admission.section, admission.academic_session]):
                raise ValueError(
                    f"Cannot save admission: missing required fields. "
                    f"roll_number={admission.roll_number}, class_name={admission.class_name}, "
                    f"section={admission.section}, academic_session={admission.academic_session}"
                )
            admission.save()
    
        return admission

class MonthlyFeeForm(forms.ModelForm):
    month = forms.CharField(widget=forms.HiddenInput(), required=True)
    received = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=forms.NumberInput(attrs={'step': '0.01', 'class': 'form-control'}))

    class Meta:
        model = MonthlyFee
        fields = ['student_admission', 'month', 'year', 'previous_balance', 'monthly_fee', 'transport_fee', 'total_dues', 'received', 'current_balance', 'reviewed_by']
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
        if student_admission and self.request and self.request.user.school:
            self.fields['student_admission'].queryset = StudentAdmission.objects.filter(pk=student_admission.pk, student__school=self.request.user.school)
            self.fields['student_admission'].initial = student_admission.pk
        else:
            self.fields['student_admission'].queryset = StudentAdmission.objects.none()
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})
        self.fields['received'].required = False
        self.fields['student_admission'].required = True  # Ensure student_admission is required
        self.fields['previous_balance'].required = False
        self.fields['monthly_fee'].required = False
        self.fields['transport_fee'].required = False
        self.fields['year'].required = True  # Ensure year is required
        self.fields['current_balance'].required = False
        self.valid_months = []
        if self.request and self.request.user.school:
            try:
                active_session = AcademicSession.objects.get(is_active=True, school=self.request.user.school)
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
        if self.instance.pk:
            self.fields['previous_balance'].disabled = True
            self.fields['monthly_fee'].disabled = True
            self.fields['transport_fee'].disabled = True
            self.fields['total_dues'].disabled = True
        elif student_admission:
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
            for field in ['previous_balance', 'monthly_fee', 'transport_fee', 'total_dues', 'received', 'current_balance']:
                self.fields[field].initial = Decimal('0.00')

    def clean(self):
        cleaned_data = super().clean()
        received = cleaned_data.get('received', Decimal('0.00'))
        total_dues = cleaned_data.get('total_dues', Decimal('0.00'))
        student_admission = cleaned_data.get('student_admission')
        year = cleaned_data.get('year')
        if received is not None and received < Decimal('0.00'):
            self.add_error('received', "Received amount cannot be negative.")
        if received is not None and received > total_dues:
            self.add_error('received', "Received amount cannot exceed total dues.")
        if not student_admission:
            self.add_error('student_admission', "Student admission is required.")
        if not year:
            self.add_error('year', "Year is required.")
        cleaned_data['current_balance'] = total_dues - (received or Decimal('0.00'))
        return cleaned_data

    def clean_month(self):
        month = self.cleaned_data.get('month')
        if not month:
            raise forms.ValidationError("Month is required.")
        if month not in self.valid_months:
            raise forms.ValidationError(f"Invalid month: {month}. Valid choices are {', '.join(self.valid_months)}.")
        return month

class PromoteOfflineStudentForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    father_guardian_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    contact = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': 'form-control'}))
    date_of_birth = forms.DateField(widget=DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    gender = forms.ChoiceField(choices=[('Male', 'Male'), ('Female', 'Female')], widget=forms.Select(attrs={'class': 'form-control'}))
    temp_image = forms.ImageField(required=False, label="Student Image", widget=forms.FileInput(attrs={'class': 'form-control'}))
    transport_fee = forms.DecimalField(max_digits=8, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control transport-field', 'step': '0.01'}))

    class Meta:
        model = StudentAdmission
        fields = [
            'class_name', 'section', 'roll_number', 'first_name', 'last_name', 'father_guardian_name',
            'date_of_birth', 'contact', 'gender', 'admission_date', 'transport', 'vehicle_no', 'route',
            'driver_contact', 'transport_fee', 'tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee',
            'other_fee', 'promotion_fee', 'discount', 'discount_behalf', 'received', 'total_dues', 'balance',
            'temp_image'
        ]
        exclude = ['student', 'academic_session', 'operator', 'promoted', 'admission_fee']
        widgets = {
            'admission_date': DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'total_dues': forms.NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control'}),
            'balance': forms.NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control'}),
            'class_name': forms.Select(attrs={'class': 'form-control'}),
            'section': forms.Select(attrs={'class': 'form-control'}),
            'roll_number': forms.NumberInput(attrs={'class': 'form-control'}),
            'transport': forms.Select(attrs={'class': 'form-control'}),
            'vehicle_no': forms.TextInput(attrs={'class': 'form-control'}),
            'route': forms.TextInput(attrs={'class': 'form-control'}),
            'driver_contact': forms.TextInput(attrs={'class': 'form-control'}),
            'tuition_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'exam_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'book_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'uniform_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'other_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'promotion_fee': forms.NumberInput(attrs={'class': 'form-control'}),
            'discount': forms.NumberInput(attrs={'class': 'form-control'}),
            'discount_behalf': forms.Select(attrs={'class': 'form-control'}),
            'received': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        self.fields['section'].choices = [('', 'Select Section')] + SECTION_CHOICES
        self.fields['class_name'].choices = CLASS_CHOICES
        self.fields['discount_behalf'].choices = [('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES
        self.fields['admission_date'].initial = datetime.date.today()
        class_name = self.initial.get('class_name') or (self.data.get('class_name') if self.data else None)
        if class_name and self.request and self.request.user.school:
            try:
                current_session = AcademicSession.objects.get(is_active=True, school=self.request.user.school)
                fee_structure = FeeStructure.objects.get(academic_session=current_session, class_name=class_name)
                self.fields['tuition_fee'].initial = fee_structure.tuition_fee
                self.fields['exam_fee'].initial = fee_structure.paper_money
                self.fields['book_fee'].initial = fee_structure.books_dues
                self.fields['uniform_fee'].initial = fee_structure.uniform_dues
                self.fields['other_fee'].initial = fee_structure.other_charges or Decimal('0.00')
                self.fields['promotion_fee'].initial = fee_structure.promotion_fee
            except (AcademicSession.DoesNotExist, FeeStructure.DoesNotExist):
                self.fields['tuition_fee'].initial = Decimal('0.00')
                self.fields['exam_fee'].initial = Decimal('0.00')
                self.fields['book_fee'].initial = Decimal('0.00')
                self.fields['uniform_fee'].initial = Decimal('0.00')
                self.fields['other_fee'].initial = Decimal('0.00')
                self.fields['promotion_fee'].initial = Decimal('0.00')
        else:
            self.fields['tuition_fee'].initial = Decimal('0.00')
            self.fields['exam_fee'].initial = Decimal('0.00')
            self.fields['book_fee'].initial = Decimal('0.00')
            self.fields['uniform_fee'].initial = Decimal('0.00')
            self.fields['other_fee'].initial = Decimal('0.00')
            self.fields['promotion_fee'].initial = Decimal('0.00')

    def clean_roll_number(self):
        roll_number = self.cleaned_data.get('roll_number')
        class_name = self.cleaned_data.get('class_name')
        section = self.cleaned_data.get('section')
        if self.request and self.request.user.school:
            current_session = AcademicSession.objects.filter(is_active=True, school=self.request.user.school).first()
            if roll_number and class_name and section and current_session:
                if StudentAdmission.objects.filter(
                    roll_number=roll_number, class_name=class_name, section=section,
                    academic_session=current_session, student__school=self.request.user.school
                ).exists():
                    raise forms.ValidationError(
                        f"Roll number {roll_number} is already assigned in {class_name} Section {section} for the current session."
                    )
        return roll_number

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data = validate_transport(cleaned_data, self)

        if not cleaned_data.get('section'):
            self.add_error('section', 'Section is required.')

        # Default fee fields to zero
        for field in ['tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee', 'other_fee', 'promotion_fee']:
            if cleaned_data.get(field) is None:
                cleaned_data[field] = Decimal('0.00')

        if cleaned_data.get('discount', Decimal('0.00')) > 0 and not cleaned_data.get('discount_behalf'):
            self.add_error('discount_behalf', 'Please specify the behalf for the discount.')

        total_dues = calculate_total_dues(cleaned_data)
        cleaned_data['total_dues'] = total_dues
        cleaned_data['balance'] = total_dues - Decimal(str(cleaned_data.get('received') or '0.00'))
        return cleaned_data

    def save(self, commit=True):
        student = Student(
            school=self.request.user.school if self.request and hasattr(self.request, 'user') and self.request.user.school else None,
            first_name=self.cleaned_data['first_name'],
            last_name=self.cleaned_data['last_name'],
            father_guardian_name=self.cleaned_data['father_guardian_name'],
            contact=self.cleaned_data['contact'],
            date_of_birth=self.cleaned_data['date_of_birth'],
            gender=self.cleaned_data['gender'],
            image=self.cleaned_data['temp_image']
        )
        if commit:
            student.save()
        admission = super().save(commit=False)
        admission.student = student
        admission.academic_session = AcademicSession.objects.filter(is_active=True, school=self.request.user.school).first() if self.request and self.request.user.school else None
        admission.promoted = True
        admission.admission_fee = Decimal('0.00')
        admission.total_dues = self.cleaned_data['total_dues']
        admission.balance = self.cleaned_data['balance']
        admission.operator = self.request.user if self.request else None
        if commit:
            admission.save()
        return admission

class PromoteExistingStudentForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_first_name'}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_last_name'}))
    father_guardian_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_father_guardian_name'}))
    contact = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_contact'}))
    date_of_birth = forms.DateField(widget=DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_date_of_birth'}))
    gender = forms.ChoiceField(choices=[('Male', 'Male'), ('Female', 'Female')], widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_gender'}))
    image = forms.ImageField(required=False, label="New Image (to replace existing)", widget=forms.FileInput(attrs={'class': 'form-control', 'id': 'id_image'}))
    previous_balance = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control', 'id': 'id_previous_balance'}))
    tuition_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_tuition_fee'}))
    exam_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_exam_fee'}))
    book_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_book_fee'}))
    uniform_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_uniform_fee'}))
    other_fee = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_other_fee'}))
    promotion_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_promotion_fee'}))
    transport_fee = forms.DecimalField(max_digits=8, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control transport-field', 'step': '0.01', 'id': 'id_transport_fee'}))
    discount = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_discount'}))
    discount_behalf = forms.ChoiceField(choices=[('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_discount_behalf'}))
    received = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_received'}))

    class Meta:
        model = StudentAdmission
        fields = [
            'class_name', 'section', 'roll_number', 'first_name', 'last_name', 'father_guardian_name',
            'date_of_birth', 'contact', 'gender', 'transport', 'vehicle_no', 'route',
            'driver_contact', 'transport_fee', 'tuition_fee', 'exam_fee', 'book_fee',
            'uniform_fee', 'other_fee', 'promotion_fee', 'previous_balance', 'discount',
            'discount_behalf', 'received', 'total_dues', 'balance', 'image'
        ]
        widgets = {
            'roll_number': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_roll_number'}),
            'class_name': forms.Select(attrs={'class': 'form-control', 'id': 'id_class_name'}),
            'section': forms.Select(attrs={'class': 'form-control', 'id': 'id_section'}),
            'transport': forms.Select(attrs={'class': 'form-control transport-field', 'id': 'id_transport'}),
            'vehicle_no': forms.TextInput(attrs={'class': 'form-control transport-field', 'id': 'id_vehicle_no'}),
            'route': forms.TextInput(attrs={'class': 'form-control transport-field', 'id': 'id_route'}),
            'driver_contact': forms.TextInput(attrs={'class': 'form-control transport-field', 'id': 'id_driver_contact'}),
            'total_dues': NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control', 'id': 'id_total_dues'}),
            'balance': NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control', 'id': 'id_balance'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        self.student_admission = kwargs.pop('student_admission', None)
        self.filtered_class = kwargs.pop('filtered_class', None)
        self.filtered_section = kwargs.pop('filtered_section', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'POST'
        self.helper.add_input(Submit('submit', 'Promote Student'))

        if self.student_admission and self.request and self.request.user.school:
            self._prefill_student_data()
            try:
                current_session = AcademicSession.objects.get(
                    is_active=True, school=self.request.user.school
                )
                self._set_previous_balance(current_session)
                self._set_fee_structure(current_session)
                self._calculate_totals()
            except AcademicSession.DoesNotExist:
                self._set_all_fees_zero()
        else:
            self._set_all_fees_zero()

    def _prefill_student_data(self):
        student = self.student_admission.student
        current_class = self.student_admission.class_name
        next_class = CLASS_PROGRESSION.get(current_class, current_class)
        self.fields['class_name'].initial = self.filtered_class or next_class or current_class
        self.fields['section'].initial = self.filtered_section or self.student_admission.section
        self.fields['roll_number'].initial = self.student_admission.roll_number
        self.fields['first_name'].initial = student.first_name
        self.fields['last_name'].initial = student.last_name
        self.fields['father_guardian_name'].initial = student.father_guardian_name
        self.fields['contact'].initial = student.contact
        self.fields['date_of_birth'].initial = student.date_of_birth
        self.fields['gender'].initial = student.gender
        self.fields['image'].initial = student.image
        self.fields['transport'].initial = self.student_admission.transport
        self.fields['vehicle_no'].initial = self.student_admission.vehicle_no
        self.fields['route'].initial = self.student_admission.route
        self.fields['driver_contact'].initial = self.student_admission.driver_contact
        self.fields['transport_fee'].initial = self.student_admission.transport_fee or Decimal('0.00')
        self.fields['discount'].initial = self.student_admission.discount or Decimal('0.00')
        self.fields['discount_behalf'].initial = self.student_admission.discount_behalf
        self.fields['received'].initial = Decimal('0.00')
        
    def _set_previous_balance(self, current_session):
        previous_balance = get_previous_balance(self.student_admission, current_session)
        self.fields['previous_balance'].initial = previous_balance

    def _set_fee_structure(self, current_session):
        selected_class = self.fields['class_name'].initial
        try:
            fee = FeeStructure.objects.get(
                academic_session=current_session,
                class_name=selected_class
            )
            self.fields['tuition_fee'].initial = fee.tuition_fee
            self.fields['exam_fee'].initial = fee.paper_money
            self.fields['book_fee'].initial = fee.books_dues
            self.fields['uniform_fee'].initial = fee.uniform_dues
            self.fields['other_fee'].initial = fee.other_charges or Decimal('0.00')
            self.fields['promotion_fee'].initial = fee.promotion_fee
        except FeeStructure.DoesNotExist:
            self._set_fees_to_zero()

    def _calculate_totals(self):
        initial_data = {
            field: self.fields[field].initial
            for field in [
                'previous_balance', 'tuition_fee', 'exam_fee', 'book_fee',
                'uniform_fee', 'other_fee', 'promotion_fee', 'transport_fee', 'discount'
            ]
        }
        total_dues = calculate_total_dues(initial_data)
        self.fields['total_dues'].initial = total_dues
        self.fields['balance'].initial = total_dues - (self.fields['received'].initial or Decimal('0.00'))

    def _set_fees_to_zero(self):
        for field in ['tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee', 'other_fee', 'promotion_fee']:
            self.fields[field].initial = Decimal('0.00')

    def _set_all_fees_zero(self):
        self._set_fees_to_zero()
        for field in ['previous_balance', 'discount', 'received', 'total_dues', 'balance', 'transport_fee']:
            self.fields[field].initial = Decimal('0.00')            

    def clean_roll_number(self):
        roll_number = self.cleaned_data.get('roll_number')
        class_name = self.cleaned_data.get('class_name')
        section = self.cleaned_data.get('section')
        if self.request and self.request.user.school:
            current_session = AcademicSession.objects.filter(is_active=True, school=self.request.user.school).first()
            if roll_number and class_name and section and current_session:
                query = StudentAdmission.objects.filter(
                    roll_number=roll_number, class_name=class_name, section=section,
                    academic_session=current_session, student__school=self.request.user.school
                )
                if self.student_admission:
                    query = query.exclude(pk=self.student_admission.pk)
                if query.exists():
                    raise forms.ValidationError(
                        f"Roll number {roll_number} is already assigned in {class_name} Section {section} for the current session."
                    )
        return roll_number

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data = validate_transport(cleaned_data, self)

        if not cleaned_data.get('section'):
            self.add_error('section', 'Section is required.')

        if cleaned_data.get('discount', Decimal('0.00')) > 0 and not cleaned_data.get('discount_behalf'):
            self.add_error('discount_behalf', 'Please specify the behalf for the discount.')

        total_dues = calculate_total_dues(cleaned_data)
        cleaned_data['total_dues'] = total_dues
        cleaned_data['balance'] = total_dues - Decimal(str(cleaned_data.get('received') or '0.00'))
        return cleaned_data

    def save(self, commit=True):
        if not self.student_admission:
            raise ValueError("Student admission instance is required for promotion.")
        student = self.student_admission.student
        student.first_name = self.cleaned_data['first_name']
        student.last_name = self.cleaned_data['last_name']
        student.father_guardian_name = self.cleaned_data['father_guardian_name']
        student.contact = self.cleaned_data['contact']
        student.date_of_birth = self.cleaned_data['date_of_birth']
        student.gender = self.cleaned_data['gender']
        if self.cleaned_data['image']:
            student.image = self.cleaned_data['image']
        student.school = self.request.user.school if self.request and self.request.user.school else None
        if commit:
            student.save()
        admission = super().save(commit=False)
        admission.student = student
        admission.academic_session = AcademicSession.objects.filter(is_active=True, school=self.request.user.school).first() if self.request and self.request.user.school else None
        admission.promoted = True
        admission.admission_fee = Decimal('0.00')
        admission.admission_date = datetime.date.today()
        admission.status = True
        admission.operator = self.request.user if self.request else None
        if commit:
            admission.save()
        return admission

class MarkNotPromotedForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_first_name'}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_last_name'}))
    father_guardian_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_father_guardian_name'}))
    contact = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_contact'}))
    date_of_birth = forms.DateField(widget=DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_date_of_birth'}))
    gender = forms.ChoiceField(choices=[('Male', 'Male'), ('Female', 'Female')], widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_gender'}))
    image = forms.ImageField(required=False, label="New Image (to replace existing)", widget=forms.FileInput(attrs={'class': 'form-control', 'id': 'id_image'}))
    transport_fee = forms.DecimalField(max_digits=8, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control transport-field', 'step': '0.01', 'id': 'id_transport_fee'}))
    previous_balance = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control', 'id': 'id_previous_balance'}))
    discount = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_discount'}))
    discount_behalf = forms.ChoiceField(choices=[('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_discount_behalf'}))
    received = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_received'}))
    tuition_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_tuition_fee'}))
    exam_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_exam_fee'}))
    book_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_book_fee'}))
    uniform_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_uniform_fee'}))
    other_fee = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_other_fee'}))
    promotion_fee = forms.DecimalField(max_digits=10, decimal_places=2, widget=NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'id': 'id_promotion_fee'}))

    class Meta:
        model = StudentAdmission
        fields = [
            'class_name', 'section', 'roll_number', 'first_name', 'last_name', 'father_guardian_name',
            'date_of_birth', 'contact', 'gender', 'transport', 'vehicle_no', 'route',
            'driver_contact', 'transport_fee', 'tuition_fee', 'exam_fee', 'book_fee',
            'uniform_fee', 'other_fee', 'promotion_fee', 'previous_balance', 'discount',
            'discount_behalf', 'received', 'total_dues', 'balance', 'image'
        ]
        widgets = {
            'total_dues': NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control', 'id': 'id_total_dues'}),
            'balance': NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control', 'id': 'id_balance'}),
            'roll_number': NumberInput(attrs={'class': 'form-control', 'id': 'id_roll_number'}),
            'transport': forms.Select(attrs={'class': 'form-control transport-field', 'id': 'id_transport'}),
            'vehicle_no': TextInput(attrs={'class': 'form-control transport-field', 'id': 'id_vehicle_no'}),
            'route': TextInput(attrs={'class': 'form-control transport-field', 'id': 'id_route'}),
            'driver_contact': TextInput(attrs={'class': 'form-control transport-field', 'id': 'id_driver_contact'}),
            'class_name': forms.Select(attrs={'class': 'form-control', 'id': 'id_class_name'}),
            'section': forms.Select(attrs={'class': 'form-control', 'id': 'id_section'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        self.student_admission = kwargs.pop('student', None)  # ← note: 'student' not 'student_admission'
        self.filtered_class = kwargs.pop('filtered_class', None)
        self.filtered_section = kwargs.pop('filtered_section', None)
        super().__init__(*args, **kwargs)
        self.fields['section'].choices = [('', 'Select Section')] + SECTION_CHOICES
        self.fields['class_name'].choices = CLASS_CHOICES
        self.fields['discount_behalf'].choices = [('', 'Select Discount Behalf')] + StudentAdmission.DISCOUNT_BEHALF_CHOICES

        if self.student_admission and self.request and self.request.user.school:
            self._prefill_student_data()
            try:
                current_session = AcademicSession.objects.get(
                    is_active=True, school=self.request.user.school
                )
                self._set_previous_balance(current_session)
                self._set_fee_structure(current_session)
                self._calculate_totals()
            except AcademicSession.DoesNotExist:
                self._set_all_fees_zero()
        else:
            self._set_all_fees_zero()

    def _prefill_student_data(self):
        student = self.student_admission.student
        # MarkNotPromoted stays in same class — no CLASS_PROGRESSION needed
        self.fields['class_name'].initial = self.student_admission.class_name
        self.fields['section'].initial = self.filtered_section or self.student_admission.section
        self.fields['roll_number'].initial = self.student_admission.roll_number
        self.fields['first_name'].initial = student.first_name
        self.fields['last_name'].initial = student.last_name
        self.fields['father_guardian_name'].initial = student.father_guardian_name
        self.fields['contact'].initial = student.contact
        self.fields['date_of_birth'].initial = student.date_of_birth
        self.fields['gender'].initial = student.gender
        self.fields['transport'].initial = self.student_admission.transport
        self.fields['vehicle_no'].initial = self.student_admission.vehicle_no
        self.fields['route'].initial = self.student_admission.route
        self.fields['driver_contact'].initial = self.student_admission.driver_contact
        self.fields['transport_fee'].initial = self.student_admission.transport_fee or Decimal('0.00')
        self.fields['discount'].initial = self.student_admission.discount or Decimal('0.00')
        self.fields['discount_behalf'].initial = self.student_admission.discount_behalf
        self.fields['received'].initial = Decimal('0.00')

    def _set_previous_balance(self, current_session):
        previous_balance = get_previous_balance(self.student_admission, current_session)
        self.fields['previous_balance'].initial = previous_balance

    def _set_fee_structure(self, current_session):
        current_class = self.student_admission.class_name
        try:
            fee = FeeStructure.objects.get(
                academic_session=current_session,
                class_name=current_class
            )
            self.fields['tuition_fee'].initial = fee.tuition_fee
            self.fields['exam_fee'].initial = fee.paper_money
            self.fields['book_fee'].initial = fee.books_dues
            self.fields['uniform_fee'].initial = fee.uniform_dues
            self.fields['other_fee'].initial = fee.other_charges or Decimal('0.00')
            self.fields['promotion_fee'].initial = Decimal('0.00')  # not promoted
        except FeeStructure.DoesNotExist:
            self._set_fees_to_zero()

    def _calculate_totals(self):
        initial_data = {
            field: self.fields[field].initial
            for field in [
                'previous_balance', 'tuition_fee', 'exam_fee', 'book_fee',
                'uniform_fee', 'other_fee', 'promotion_fee', 'transport_fee', 'discount'
            ]
        }
        total_dues = calculate_total_dues(initial_data)
        self.fields['total_dues'].initial = total_dues
        self.fields['balance'].initial = total_dues  # received is always 0.00 at this point

    def _set_fees_to_zero(self):
        for field in ['tuition_fee', 'exam_fee', 'book_fee', 'uniform_fee', 'other_fee', 'promotion_fee']:
            self.fields[field].initial = Decimal('0.00')

    def _set_all_fees_zero(self):
        self._set_fees_to_zero()
        for field in ['previous_balance', 'discount', 'received', 'total_dues', 'balance', 'transport_fee']:
            self.fields[field].initial = Decimal('0.00')        

    def clean_roll_number(self):
        roll_number = self.cleaned_data.get('roll_number')
        class_name = self.cleaned_data.get('class_name')
        section = self.cleaned_data.get('section')
        if self.request and self.request.user.school:
            current_session = AcademicSession.objects.filter(is_active=True, school=self.request.user.school).first()
            if roll_number and class_name and section and current_session:
                query = StudentAdmission.objects.filter(
                    roll_number=roll_number, class_name=class_name, section=section,
                    academic_session=current_session, student__school=self.request.user.school
                )
                if self.student_admission:
                    query = query.exclude(pk=self.student_admission.pk)
                if query.exists():
                    raise forms.ValidationError(
                        f"Roll number {roll_number} is already assigned in {class_name} Section {section} for the current session."
                    )
        return roll_number

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data = validate_transport(cleaned_data, self)

        if not cleaned_data.get('section'):
            self.add_error('section', 'Section is required.')

        if cleaned_data.get('discount', Decimal('0.00')) > 0 and not cleaned_data.get('discount_behalf'):
            self.add_error('discount_behalf', 'Please specify the behalf for the discount.')

        total_dues = calculate_total_dues(cleaned_data)
        cleaned_data['total_dues'] = total_dues
        cleaned_data['balance'] = total_dues - Decimal(str(cleaned_data.get('received') or '0.00'))
        return cleaned_data

    def save(self, commit=True):
        if not self.student_admission:
            raise ValueError("Student admission instance is required for promotion.")
        student = self.student_admission.student
        student.first_name = self.cleaned_data['first_name']
        student.last_name = self.cleaned_data['last_name']
        student.father_guardian_name = self.cleaned_data['father_guardian_name']
        student.contact = self.cleaned_data['contact']
        student.date_of_birth = self.cleaned_data['date_of_birth']
        student.gender = self.cleaned_data['gender']
        if self.cleaned_data['image']:
            student.image = self.cleaned_data['image']
        student.school = self.request.user.school if self.request and self.request.user.school else None
        if commit:
            student.save()
        admission = super().save(commit=False)
        admission.student = student
        admission.academic_session = AcademicSession.objects.filter(is_active=True, school=self.request.user.school).first() if self.request and self.request.user.school else None
        admission.promoted = True
        admission.failed_to_promote = False
        admission.admission_fee = Decimal('0.00')
        admission.admission_date = datetime.date.today()
        admission.status = True
        admission.operator = self.request.user if self.request else None
        if commit:
            admission.save()
        return admission

class RollNumberPromptForm(forms.Form):
    roll_number = forms.IntegerField(
        label="New Roll Number",
        help_text="Enter a unique roll number for this class and section.",
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        self.class_name = kwargs.pop('class_name', None)
        self.section = kwargs.pop('section', None)
        self.academic_session = kwargs.pop('academic_session', None)
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.add_input(Submit('submit', 'Submit', css_class='btn btn-primary'))

    def clean_roll_number(self):
        roll_number = self.cleaned_data['roll_number']
        if self.request and self.request.user.school and self.academic_session:
            if StudentAdmission.objects.filter(
                roll_number=roll_number, class_name=self.class_name, section=self.section,
                academic_session=self.academic_session, student__school=self.request.user.school
            ).exists():
                raise forms.ValidationError(
                    f"Roll number {roll_number} is already assigned in {self.class_name} - {self.section} for this session."
                )
        return roll_number     



class TeacherForm(forms.ModelForm):
    username = forms.CharField(max_length=150, required=True)
    password = forms.CharField(widget=forms.PasswordInput, required=False)
    confirm_password = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = Teacher
        fields = ['name', 'contact', 'cnic', 'gender', 'class_name', 'section', 'subjects', 'education', 'salary', 'date_of_join', 'dob', 'address', 'children_in_school']
        widgets = {
            'date_of_join': forms.DateInput(attrs={'type': 'date'}),
            'dob': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 4}),
            'class_name': forms.Select(choices=CLASS_CHOICES),
            'section': forms.Select(choices=SECTION_CHOICES),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.school:
            self.fields['subjects'].queryset = Subject.objects.filter(school=self.request.user.school)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})
        if self.instance.pk:
            self.fields['username'].initial = self.instance.user.username

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        # Required on create, optional on update
        if not self.instance.pk and not password:
            self.add_error('password', 'Password is required for new teacher accounts.')

        if password or confirm_password:
            if password != confirm_password:
                self.add_error('confirm_password', 'Passwords do not match.')

        return cleaned_data

    def clean_children_in_school(self):
        children = self.cleaned_data.get('children_in_school', 0)
        if children < 0:
            raise forms.ValidationError("Number of children cannot be negative.")
        return children

    def save(self, commit=True):
        teacher = super().save(commit=False)
        teacher.school = self.request.user.school

        if commit:
            if self.instance.pk and self.instance.user:
                # Updating existing teacher user
                user = self.instance.user
                user.username = self.cleaned_data['username']
                if self.cleaned_data.get('password'):
                    user.set_password(self.cleaned_data['password'])
                user.save()
            else:
                # Creating new teacher user
                password = self.cleaned_data.get('password')
                if not password:
                    raise forms.ValidationError("Password is required when creating a new teacher account.")

                # ✅ Use create_user — handles hashing, signals, validation correctly
                user = CustomUser.objects.create_user(
                    username=self.cleaned_data['username'],
                    password=password,
                    user_type=4,
                    school=self.request.user.school
                )
                teacher.user = user

            teacher.save()
            self.save_m2m()
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
                academic_session__is_active=True
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
        model = Assets
        fields = ['name', 'purchased_date', 'file_number', 'value', 'purchased_by', 'description', 'condition']
        widgets = {
            'purchased_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user and self.user.school:
            self.instance.school = self.user.school
        # Set default purchased_date to today
        if not self.instance.pk:
            self.initial['purchased_date'] = timezone.now().date()

    def clean(self):
        cleaned_data = super().clean()
        if not self.user or not self.user.school:
            raise forms.ValidationError("User must be associated with a school.")
        # Check for active academic session
        if not AcademicSession.objects.filter(school=self.user.school, is_active=True).exists():
            raise forms.ValidationError("No active academic session found for this school.")
        return cleaned_data



class ExpensesForm(forms.ModelForm):
    class Meta:
        model = Expenses
        fields = ['expense_type', 'expense_name', 'price', 'payment_date', 'payment_by', 'file_number', 'period', 'description']
        widgets = {
            'payment_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 4}),
            'period': forms.TextInput(attrs={'placeholder': 'e.g., January or Monday'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user and self.user.school:
            self.instance.school = self.user.school
        # Set default payment_date to today
        if not self.instance.pk:
            self.initial['payment_date'] = timezone.now().date()
        # Dynamically filter expense_name choices and set period label
        if self.data.get('expense_type') == 'Daily' or (self.instance.pk and self.instance.expense_type == 'Daily'):
            self.fields['expense_name'].choices = [(name, name) for name, _ in Expenses.EXPENSE_NAME_CHOICES if name in ['Stationary', 'Food']]
            self.fields['period'].label = 'Day Name'
        else:
            self.fields['expense_name'].choices = [(name, name) for name, _ in Expenses.EXPENSE_NAME_CHOICES if name not in ['Stationary', 'Food']]
            self.fields['period'].label = 'Month Name'

    def clean(self):
        cleaned_data = super().clean()
        if not self.user or not self.user.school:
            raise forms.ValidationError("User must be associated with a school.")
        if not AcademicSession.objects.filter(school=self.user.school, is_active=True).exists():
            raise forms.ValidationError("No active academic session found for this school.")
        return cleaned_data


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
