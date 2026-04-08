from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from decimal import Decimal
from django.core.exceptions import ValidationError
from dateutil.relativedelta import relativedelta
from datetime import datetime
from django.core.validators import RegexValidator, MinValueValidator
import datetime
import secrets
import string
# Global Choices
CLASS_CHOICES = [
    ("PG", "PG"),
    ("Nursery", "Nursery"),
    ("KG", "KG"),
    ("Class 1", "Class 1"),
    ("Class 2", "Class 2"),
    ("Class 3", "Class 3"),
    ("Class 4", "Class 4"),
    ("Class 5", "Class 5"),
    ("Class 6", "Class 6"),
    ("Class 7", "Class 7"),
    ("Class 8", "Class 8"),
    ("Class 9", "Class 9"),
    ("Class 10", "Class 10"),
]

SECTION_CHOICES = [
    ('A_Red', 'A Red'),
    ('B_Blue', 'B Blue'),
    ('C_Green', 'C Green'),
    ('D_Yellow', 'D Yellow'),
    ('E_Purple', 'E Purple'),
    ('F_Orange', 'F Orange'),
]


DESIGNATION_CHOICES = [
    ('Principal', 'Principal'),
    ('Vice Principal', 'Vice Principal'),
    ('Coordinator', 'Coordinator'),
    ('Librarian', 'Librarian'),
    ('Clerk', 'Clerk'),
    ('Accountant', 'Accountant'),
    ('Security Guard', 'Security Guard'),
    ('Janitor', 'Janitor'),
    ('Driver', 'Driver'),
    ('Other', 'Other'),
]



class CustomUser(AbstractUser):
    USER_TYPE_CHOICES = (
        (1, "Admin"),
        (2, "Operator"),
        (3, "Owner"),
        (4, "Teacher"),
        (5, "Student"),
    )
    user_type = models.PositiveSmallIntegerField(choices=USER_TYPE_CHOICES, default=1)
    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True, related_name='users')

    def clean(self):
        # Skip validation for new users (during creation, pk is None)
        if self.pk is None:
            return
        if self.user_type == 1 and not self.school:
            raise ValidationError("Admin users must be associated with a school.")
        super().clean()

    def __str__(self):
        return self.username

class School(models.Model):
    school_name = models.CharField(max_length=200, unique=True)
    school_logo = models.ImageField(upload_to='school_logos/', null=True, blank=True)
    city = models.CharField(max_length=100)
    owner_name = models.CharField(max_length=100)
    contact_number = models.CharField(
        max_length=15,
        validators=[RegexValidator(r'^\+?\d{10,15}$', message="Enter a valid phone number (10-15 digits, optional '+' prefix).")],
    )
    number_of_students = models.PositiveIntegerField(
        validators=[MinValueValidator(0, message="Number of students cannot be negative.")],
        default=0,
    )
    admin_user = models.OneToOneField(
        CustomUser, on_delete=models.CASCADE, related_name='school_admin', limit_choices_to={'user_type': 1}
    )
    is_active = models.BooleanField(default=True)  # Control system access
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.school_name

class SchoolSubscription(models.Model):
    PAYMENT_TYPE_CHOICES = (
        ('monthly', 'Monthly'),
        ('annual', 'Annual'),
    )
    PAYMENT_METHOD_CHOICES = (
        ('cash', 'Cash'),
        ('online', 'Online'),
    )
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='subscriptions')
    payment_type = models.CharField(max_length=10, choices=PAYMENT_TYPE_CHOICES)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_date = models.DateField()
    is_valid = models.BooleanField(default=True)  # Indicates if subscription is active
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.school.school_name} - {self.payment_type} ({self.payment_date})"

class AcademicSession(models.Model):
    session_name = models.CharField(max_length=20)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField()
    is_active = models.BooleanField(default=False)
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='academic_sessions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('session_name', 'school')  # Ensure unique session names per school

    def clean(self):
        if not self.school:
            raise ValidationError("School must be set for the academic session.")
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValidationError("End date must be after the start date.")
        overlapping_sessions = AcademicSession.objects.filter(
            school=self.school,
            start_date__lte=self.end_date,
            end_date__gte=self.start_date
        ).exclude(pk=self.pk)
        if overlapping_sessions.exists():
            raise ValidationError("This session overlaps with an existing session for this school.")

    def save(self, *args, **kwargs):
        self.clean()
        if not self.pk:
            AcademicSession.objects.filter(school=self.school, is_active=True).update(is_active=False)
            self.is_active = True
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.session_name} ({self.school.school_name})"

class FeeStructure(models.Model):
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    class_name = models.CharField(max_length=20, choices=CLASS_CHOICES)
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2)
    admission_fee = models.DecimalField(max_digits=10, decimal_places=2)
    books_dues = models.DecimalField(max_digits=10, decimal_places=2)
    uniform_dues = models.DecimalField(max_digits=10, decimal_places=2)
    paper_money = models.DecimalField(max_digits=10, decimal_places=2)
    promotion_fee = models.DecimalField(max_digits=10, decimal_places=2)
    other_charges = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('academic_session', 'class_name')

    def __str__(self):
        return f"{self.class_name} - {self.academic_session.session_name}"

class Student(models.Model):
    student_id = models.CharField(max_length=20, unique=True, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='students')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    father_guardian_name = models.CharField(max_length=100)
    contact = models.CharField(max_length=20)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=6, choices=[('Male', 'Male'), ('Female', 'Female')])
    image = models.ImageField(upload_to='student_images/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.student_id:
            # Use school ID or a unique school identifier for prefix
            school_prefix = f"{self.school.id}"  # e.g., '1' for school with id=1
            last_student = Student.objects.filter(school=self.school).order_by('created_at').last()
            if last_student:
                # Extract the numeric part after the prefix
                last_id = int(last_student.student_id.split('-')[-1])
                self.student_id = f"SCH{school_prefix}-{last_id + 1:04d}"  # e.g., SCH1-0002
            else:
                self.student_id = f"SCH{school_prefix}-0001"  # e.g., SCH1-0001
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.student_id})"

class StudentAdmission(models.Model):
    DISCOUNT_BEHALF_CHOICES = [
        ('owner', 'Owner'),
        ('principal', 'Principal'),
        ('teacher', 'Teacher'),
        ('staff', 'Staff'),
        ('driver', 'Driver'),
        ('brothers_sisters', 'Brothers/Sisters'),
    ]
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='admissions', null=True, blank=True)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.PROTECT, related_name='admissions')
    class_name = models.CharField(max_length=50, choices=CLASS_CHOICES)
    roll_number = models.IntegerField()
    section = models.CharField(max_length=10, choices=SECTION_CHOICES)
    admission_date = models.DateField()
    class_teacher = models.CharField(max_length=100, null=True, blank=True)
    admission_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    exam_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    book_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    uniform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    other_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, null=True, blank=True)
    promotion_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    transport = models.CharField(max_length=10, choices=[('Free', 'Free'), ('Paid', 'Paid'), ('No', 'No')], default='No')
    vehicle_no = models.CharField(max_length=20, blank=True, null=True)
    route = models.CharField(max_length=100, blank=True, null=True)
    driver_contact = models.CharField(max_length=15, blank=True, null=True)
    transport_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, blank=True, null=True)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount_behalf = models.CharField(max_length=20, choices=DISCOUNT_BEHALF_CHOICES, blank=True, null=True)
    total_dues = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    received = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.BooleanField(default=True)
    promoted = models.BooleanField(default=False)
    failed_to_promote = models.BooleanField(default=False)
    operator = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('roll_number', 'class_name', 'section', 'academic_session')

    def save(self, *args, **kwargs):
        if self.transport == 'No':
            self.vehicle_no = None
            self.route = None
            self.driver_contact = None
            self.transport_fee = 0.00
        elif self.transport == 'Free':
            self.transport_fee = 0.00
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.first_name} {self.student.last_name} - {self.class_name} ({self.roll_number})"

class Subject(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='subjects')
    name = models.CharField(max_length=50)  # e.g., "Math", "English"

    class Meta:
        unique_together = ('school', 'name')

    def __str__(self):
        return f"{self.name} ({self.school.school_name})"



        

class StudentResult(models.Model):
    EXAM_TYPE_CHOICES = [
        ('Midterm', 'Midterm'),
        ('Final', 'Final'),
        ('January', 'January'),
        ('February', 'February'),
        ('March', 'March'),
        ('April', 'April'),
        ('May', 'May'),
        ('June', 'June'),
        ('July', 'July'),
        ('August', 'August'),
        ('September', 'September'),
        ('October', 'October'),
        ('November', 'November'),
        ('December', 'December'),
    ]
    student_admission = models.ForeignKey(StudentAdmission, on_delete=models.CASCADE, related_name='results')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    exam_type = models.CharField(max_length=20, choices=EXAM_TYPE_CHOICES)
    theory = models.FloatField(default=0.0)
    practical = models.FloatField(default=0.0)
    total_marks = models.FloatField()
    obtained_marks = models.FloatField()
    percentage = models.FloatField(editable=False)
    grade = models.CharField(max_length=2, editable=False)

    def save(self, *args, **kwargs):
        # Calculate total_marks from theory + practical if either is non-zero
        if self.theory > 0 or self.practical > 0:
            self.total_marks = self.theory + self.practical
        # Calculate percentage
        if self.total_marks > 0:
            self.percentage = (self.obtained_marks / self.total_marks) * 100
        else:
            self.percentage = 0.0
        # Calculate grade
        if self.percentage >= 90:
            self.grade = 'A+'
        elif self.percentage >= 80:
            self.grade = 'A'
        elif self.percentage >= 70:
            self.grade = 'B'
        elif self.percentage >= 60:
            self.grade = 'C'
        elif self.percentage >= 50:
            self.grade = 'D'
        else:
            self.grade = 'F'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student_admission.student.first_name} - {self.subject.name} - {self.exam_type}"

class MonthlyFee(models.Model):
    student_admission = models.ForeignKey(StudentAdmission, on_delete=models.CASCADE, related_name='monthly_fees')
    month = models.CharField(max_length=20)
    year = models.PositiveIntegerField()
    previous_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    transport_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_dues = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    received = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    current_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    has_payment = models.BooleanField(default=False)  # Mark months with saved payments
    reviewed_by = models.CharField(max_length=100, blank=True, null=True)
    payment_date = models.DateField(auto_now_add=True)
    operator = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('student_admission', 'month', 'year')
        indexes = [
            models.Index(fields=['student_admission', 'month', 'year']),
        ]

    def save(self, *args, **kwargs):
        try:
            active_session = AcademicSession.objects.get(is_active=True, school=self.student_admission.student.school)
            self.year = int(active_session.session_name.split('-')[0])
        except AcademicSession.DoesNotExist:
            raise ValidationError("No active session is available.")

        # Set previous_balance to current_balance of previous month
        if not self.pk:
            month_index = datetime.datetime.strptime(self.month, '%B').month
            prev_month_num = (month_index - 2) % 12 + 1
            prev_year = self.year if prev_month_num < month_index else self.year - 1
            prev_month = datetime.datetime(1900, prev_month_num, 1).strftime('%B')
            prev_fee = MonthlyFee.objects.filter(
                student_admission=self.student_admission,
                month=prev_month,
                year=prev_year
            ).first()
            self.previous_balance = prev_fee.current_balance if prev_fee else (self.student_admission.balance or Decimal('0.00'))

        # Calculate fees
        if self.monthly_fee == Decimal('0.00'):
            tuition_fee = Decimal(str(self.student_admission.tuition_fee)) if self.student_admission.tuition_fee else Decimal('0.00')
            discount = Decimal(str(self.student_admission.discount)) if self.student_admission.discount else Decimal('0.00')
            self.monthly_fee = tuition_fee - discount
        if self.transport_fee == Decimal('0.00'):
            self.transport_fee = Decimal(str(self.student_admission.transport_fee)) if self.student_admission.transport == 'Paid' and self.student_admission.transport_fee else Decimal('0.00')
        if self.total_dues == Decimal('0.00'):
            self.total_dues = self.previous_balance + self.monthly_fee + self.transport_fee
        received = Decimal(str(self.received)) if self.received is not None else Decimal('0.00')
        self.current_balance = self.total_dues - received
        self.has_payment = received > Decimal('0.00') or self.has_payment  # Mark as having payment if received > 0
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student_admission.student.first_name} {self.student_admission.student.last_name} - {self.month} {self.year}"





class Teacher(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, null=True, blank=True)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='teachers')
    name = models.CharField(max_length=100)
    contact = models.CharField(
        max_length=13,
        validators=[RegexValidator(r'^\+92\d{10}$|^03\d{9}$', 'Enter a valid Pakistani phone number (e.g., +923xxxxxxxxx or 03xxxxxxxxx).')]
    )
    cnic = models.CharField(
        max_length=15,
        validators=[RegexValidator(r'^\d{5}-\d{7}-\d$', 'Enter a valid CNIC (e.g., 12345-1234567-1).')]
    )
    gender = models.CharField(max_length=6, choices=[('Male', 'Male'), ('Female', 'Female')])
    class_name = models.CharField(max_length=20, choices=CLASS_CHOICES)
    section = models.CharField(max_length=10, choices=SECTION_CHOICES)
    subjects = models.ManyToManyField(Subject, blank=True)
    education = models.CharField(max_length=100, blank=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    date_of_join = models.DateField()
    dob = models.DateField()
    address = models.TextField(blank=True)
    children_in_school = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('school', 'cnic', 'class_name', 'section')

    def __str__(self):
        return f"{self.name} ({self.school.school_name})"




class Staff(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='staff')
    name = models.CharField(max_length=100)
    designation = models.CharField(max_length=20, choices=DESIGNATION_CHOICES)
    salary = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    date_of_join = models.DateField()
    cnic = models.CharField(
        max_length=15,
        unique=True,
        validators=[RegexValidator(r'^\d{5}-\d{7}-\d$', 'Enter a valid CNIC (e.g., 12345-1234567-1).')]
    )
    contact = models.CharField(
        max_length=13,
        validators=[RegexValidator(r'^\+92\d{10}$|^03\d{9}$', 'Enter a valid Pakistani phone number (e.g., +923xxxxxxxxx or 03xxxxxxxxx).')]
    )
    address = models.TextField(blank=True)
    date_of_birth = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('school', 'cnic')

    def __str__(self):
        return f"{self.name} - {self.designation} ({self.school.school_name})"




class Syllabus(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE)  # ✅ New
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    class_name = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    file = models.FileField(upload_to='syllabus/', blank=True, null=True)
    uploaded_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('school', 'subject', 'class_name', 'academic_session')  # ✅ Prevent duplicates

    def __str__(self):
        return f"{self.subject.name} - {self.class_name} ({self.school.school_name})"

class Announcement(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.title
            


class TemporaryPassword(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='temporary_password')
    password = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.password}"

    @staticmethod
    def generate_password(length=12):
        characters = string.ascii_letters + string.digits + string.punctuation
        return ''.join(secrets.choice(characters) for _ in range(length))





class Assets(models.Model):
    CONDITION_CHOICES = [
        ('New', 'New'),
        ('Used', 'Used'),
    ]
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(max_length=100)
    purchased_date = models.DateField()
    file_number = models.CharField(max_length=50, unique=True)
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.0)])
    purchased_by = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    condition = models.CharField(max_length=10, choices=CONDITION_CHOICES, default='New')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('school', 'file_number')

    def __str__(self):
        return f"{self.name} ({self.school.school_name})"



class Expenses(models.Model):
    EXPENSE_TYPE_CHOICES = [
        ('Monthly', 'Monthly'),
        ('Daily', 'Daily'),
    ]
    EXPENSE_NAME_CHOICES = [
        ('Electricity Bill', 'Electricity Bill'),
        ('WiFi Bill', 'WiFi Bill'),
        ('Building Rent', 'Building Rent'),
        ('Carpenter', 'Carpenter'),
        ('Transport', 'Transport'),
        ('Welder', 'Welder'),
        ('Event', 'Event'),
        ('Electrecian', 'Elecrecian'),
        ('Maintain', 'Maintain'),
        ('Plumber', 'Plumber'),
        ('Painter', 'Painter'),
        ('Tools', 'Tools'),
        ('First Aid', 'First Aid'),
        ('Sports', 'Sports'),
        ('Exam', 'Exam'),
        ('Other', 'Other'),
        ('Stationary', 'Stationary'),  # Daily only
        ('Food', 'Food'),  # Daily only
    ]
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='expenses')
    expense_type = models.CharField(max_length=10, choices=EXPENSE_TYPE_CHOICES)
    expense_name = models.CharField(max_length=50, choices=EXPENSE_NAME_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.0)])
    payment_date = models.DateField()
    payment_by = models.CharField(max_length=100)
    file_number = models.CharField(max_length=50, blank=True, null=True)
    period = models.CharField(max_length=20, blank=True, null=True)  # Renamed from month_name
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        daily_choices = ['Stationary', 'Food']
        if self.expense_type == 'Daily' and self.expense_name not in daily_choices:
            raise ValidationError("Daily expenses can only be 'Stationary' or 'Food'.")
        if self.expense_type == 'Monthly' and self.expense_name in daily_choices:
            raise ValidationError("Stationary and Food are only for Daily expenses.")
        if self.expense_type == 'Monthly' and not self.period:
            raise ValidationError("Month name is required for Monthly expenses.")
        if self.expense_type == 'Daily' and not self.period:
            raise ValidationError("Day name is required for Daily expenses.")
        if self.expense_type == 'Daily' and self.period and self.period not in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
            raise ValidationError("Day name must be a valid day of the week for Daily expenses.")

    def __str__(self):
        return f"{self.expense_name} ({self.expense_type}) - {self.school.school_name}"




class Transport(models.Model):
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='transports')
    vehicle_number = models.CharField(max_length=20, unique=True)  # e.g., ABC-1234
    driver_name = models.CharField(max_length=100)
    driver_cnic = models.CharField(
        max_length=15,
        validators=[RegexValidator(r'^\d{5}-\d{7}-\d{1}$', 'CNIC must be in the format 12345-1234567-1')],
        unique=True
    )
    address = models.TextField()
    date_of_joining = models.DateField()
    date_of_birth = models.DateField()
    number_of_students = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    route = models.CharField(max_length=200)  # e.g., "Main City to School Campus"
    contact = models.CharField(
        max_length=15,
        validators=[RegexValidator(r'^\+?\d{10,15}$', 'Contact must be a valid phone number')]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('school', 'vehicle_number')

    def __str__(self):
        return f"{self.vehicle_number} - {self.driver_name} ({self.school.school_name})"







class Events(models.Model):
    EVENT_TYPE_CHOICES = [
        ('Exam', 'Exam'),
        ('TPM', 'TPM'),
        ('Teacher Meeting', 'Teacher Meeting'),
        ('Result', 'Result'),
        ('Celebration', 'Celebration'),
        ('Tour', 'Tour'),
        ('Other', 'Other'),
    ]
    EVENT_FOR_CHOICES = [
        ('All', 'All'),
        ('Students', 'Students'),
        ('Teacher', 'Teacher'),
    ]
    school = models.ForeignKey('School', on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)
    event_for = models.CharField(max_length=10, choices=EVENT_FOR_CHOICES)
    description = models.TextField(blank=True)
    event_date = models.DateField()
    announcement_date = models.DateField(default=timezone.now)
    arranged_by = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_event_type_display()} ({self.get_event_for_display}) - {self.school.school_name}"        