from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, MinValueValidator
from decimal import Decimal
from dateutil.relativedelta import relativedelta
import secrets
import string

# ====================== GLOBAL CHOICES ======================
CLASS_CHOICES = [
    ("PG", "PG"), ("Nursery", "Nursery"), ("KG", "KG"),
    ("Class 1", "Class 1"), ("Class 2", "Class 2"), ("Class 3", "Class 3"),
    ("Class 4", "Class 4"), ("Class 5", "Class 5"), ("Class 6", "Class 6"),
    ("Class 7", "Class 7"), ("Class 8", "Class 8"), ("Class 9", "Class 9"),
    ("Class 10", "Class 10"),
]

SECTION_CHOICES = [
    ('A_Red', 'A Red'), ('B_Blue', 'B Blue'), ('C_Green', 'C Green'),
    ('D_Yellow', 'D Yellow'), ('E_Purple', 'E Purple'), ('F_Orange', 'F Orange'),
]

GENDER_CHOICES = [('Male', 'Male'), ('Female', 'Female')]



# Add this after SECTION_CHOICES
CLASS_PROGRESSION = {
    'PG': 'Nursery',
    'Nursery': 'KG',
    'KG': 'Class 1',
    'Class 1': 'Class 2',
    'Class 2': 'Class 3',
    'Class 3': 'Class 4',
    'Class 4': 'Class 5',
    'Class 5': 'Class 6',
    'Class 6': 'Class 7',
    'Class 7': 'Class 8',
    'Class 8': 'Class 9',
    'Class 9': 'Class 10',
    'Class 10': 'Class 10',
}

# ====================== ABSTRACT BASE MODEL ======================
class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ====================== CUSTOM USER ======================
class CustomUser(AbstractUser, TimestampedModel):
    USER_TYPE_CHOICES = (
        (1, "Admin"), (2, "Operator"), (3, "Owner"),
        (4, "Teacher"), (5, "Student"),
    )
    user_type = models.PositiveSmallIntegerField(choices=USER_TYPE_CHOICES, default=1)
    school = models.ForeignKey('School', on_delete=models.CASCADE, null=True, blank=True,
                               related_name='users')

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    def clean(self):
        if self.pk and self.user_type in (1, 2, 3) and not self.school:
            raise ValidationError("School staff accounts must be associated with a school.")
        super().clean()

    def __str__(self):
        return f"{self.username} ({self.get_user_type_display()})"


# ====================== SCHOOL ======================
class School(TimestampedModel):
    school_name = models.CharField(max_length=200, unique=True)
    school_logo = models.ImageField(upload_to='school_logos/', null=True, blank=True)
    city = models.CharField(max_length=100)
    owner_name = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=15, validators=[
        RegexValidator(r'^\+?\d{10,15}$', "Enter a valid phone number.")
    ])
    number_of_students = models.PositiveIntegerField(default=0)
    admin_user = models.OneToOneField(
        CustomUser, on_delete=models.CASCADE,
        related_name='school_admin', limit_choices_to={'user_type': 1}
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "School"
        verbose_name_plural = "Schools"
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['city']),
        ]

    def __str__(self):
        return self.school_name


# ====================== SCHOOL SUBSCRIPTION ======================
class SchoolSubscription(TimestampedModel):
    PAYMENT_TYPE_CHOICES = (('monthly', 'Monthly'), ('annual', 'Annual'))
    PAYMENT_METHOD_CHOICES = (('cash', 'Cash'), ('online', 'Online'))

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='subscriptions')
    payment_type = models.CharField(max_length=10, choices=PAYMENT_TYPE_CHOICES)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    payment_date = models.DateField(default=timezone.now)
    expiry_date = models.DateField(null=True, blank=True)
    is_valid = models.BooleanField(default=True)

    class Meta:
        verbose_name = "School Subscription"
        verbose_name_plural = "School Subscriptions"

    def save(self, *args, **kwargs):
        if not self.expiry_date:
            delta = relativedelta(months=1) if self.payment_type == 'monthly' else relativedelta(years=1)
            self.expiry_date = self.payment_date + delta
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.school.school_name} - {self.payment_type} ({self.payment_date})"


# ====================== ACADEMIC SESSION ======================
class AcademicSession(TimestampedModel):
    session_name = models.CharField(max_length=20)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='academic_sessions')

    class Meta:
        unique_together = (('session_name', 'school'),)
        indexes = [
            models.Index(fields=['school', 'is_active']),
            models.Index(fields=['school', 'start_date', 'end_date']),
        ]
        verbose_name = "Academic Session"
        verbose_name_plural = "Academic Sessions"

    def clean(self):
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValidationError("End date must be after the start date.")

    def save(self, *args, **kwargs):
        if not self.pk:
            with transaction.atomic():
                AcademicSession.objects.select_for_update().filter(
                    school=self.school, is_active=True
                ).update(is_active=False)
                self.is_active = True
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.session_name} ({self.school.school_name})"


# ====================== FEE STRUCTURE ======================
class FeeStructure(TimestampedModel):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='fee_structures')
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    class_name = models.CharField(max_length=20, choices=CLASS_CHOICES)

    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    admission_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    books_dues = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    uniform_dues = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    paper_money = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    promotion_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    other_charges = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    class Meta:
        unique_together = ('school', 'academic_session', 'class_name')
        verbose_name = "Fee Structure"
        verbose_name_plural = "Fee Structures"

    def __str__(self):
        return f"{self.class_name} - {self.academic_session.session_name}"


# ====================== STUDENT MODELS ======================
class Student(TimestampedModel):
    student_id = models.CharField(max_length=20, unique=True, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='students')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    father_guardian_name = models.CharField(max_length=100)
    contact = models.CharField(max_length=20)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES)
    image = models.ImageField(upload_to='student_images/', null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['school', 'student_id']),
            models.Index(fields=['first_name', 'last_name']),
        ]

    def save(self, *args, **kwargs):
        if not self.student_id:
            with transaction.atomic():
                last_student = Student.objects.select_for_update().filter(
                    school=self.school
                ).order_by('created_at').last()
                school_prefix = self.school.id
                last_id = int(last_student.student_id.split('-')[-1]) if last_student else 0
                self.student_id = f"SCH{school_prefix}-{last_id + 1:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.student_id})"


class StudentAdmission(TimestampedModel):
    DISCOUNT_BEHALF_CHOICES = [
        ('owner', 'Owner'), ('principal', 'Principal'), ('teacher', 'Teacher'),
        ('staff', 'Staff'), ('driver', 'Driver'), ('brothers_sisters', 'Brothers/Sisters'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='admissions')
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.PROTECT)
    class_name = models.CharField(max_length=20, choices=CLASS_CHOICES)
    roll_number = models.IntegerField()
    section = models.CharField(max_length=10, choices=SECTION_CHOICES)

    # === Fields kept for form compatibility ===
    admission_date = models.DateField(default=timezone.now)
    class_teacher = models.CharField(max_length=100, blank=True, null=True)
    
    admission_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    exam_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    book_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    uniform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    other_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), blank=True, null=True)
    promotion_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_behalf = models.CharField(max_length=20, choices=DISCOUNT_BEHALF_CHOICES, blank=True, null=True)

    transport = models.CharField(max_length=10, choices=[('Free', 'Free'), ('Paid', 'Paid'), ('No', 'No')], default='No')
    transport_fee = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    vehicle_no = models.CharField(max_length=20, blank=True, null=True)
    route = models.CharField(max_length=100, blank=True, null=True)
    driver_contact = models.CharField(max_length=15, blank=True, null=True)

    total_dues = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    received = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # === NEW FIELDS FOR PROMOTION ===
    previous_balance = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Balance carried over from previous academic session"
    )
    closing_balance = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Final pending balance of this session (updated after every monthly payment)"
    )

    promoted_from = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='promoted_to',
        help_text="Reference to the StudentAdmission record from previous session"
    )

    status = models.BooleanField(default=True)
    promoted = models.BooleanField(default=False)
    failed_to_promote = models.BooleanField(default=False)
    operator = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = (('roll_number', 'class_name', 'section', 'academic_session'),)
        indexes = [
            models.Index(fields=['academic_session', 'class_name', 'section']),
            models.Index(fields=['student', 'academic_session']),
            models.Index(fields=['status']),
        ]

    def save(self, *args, **kwargs):
        if self.transport == 'No':
            self.transport_fee = Decimal('0.00')
            self.vehicle_no = None
            self.route = None
            self.driver_contact = None
        elif self.transport == 'Free':
            self.transport_fee = Decimal('0.00')

        # === Initialize for NEW admissions ===
        if not self.pk:                     # This is a new record
            self.previous_balance = Decimal('0.00')
            self.closing_balance = self.balance or Decimal('0.00')

        super().save(*args, **kwargs)

    def get_pending_balance_for_promotion(self):
        #Delegate to utils for consistency
        from .utils import get_pending_balance
        return get_pending_balance(self)

    def __str__(self):
        return f"{self.student} - {self.class_name} ({self.roll_number})"


# ====================== SUBJECT & RESULT ======================
class Subject(TimestampedModel):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='subjects')
    name = models.CharField(max_length=50)

    class Meta:
        unique_together = ('school', 'name')

    def __str__(self):
        return f"{self.name} ({self.school.school_name})"


class StudentResult(TimestampedModel):
    EXAM_TYPE_CHOICES = [
        ('Midterm', 'Midterm'), ('Final', 'Final'),
        ('January', 'January'), ('February', 'February'), ('March', 'March'),
        ('April', 'April'), ('May', 'May'), ('June', 'June'),
        ('July', 'July'), ('August', 'August'), ('September', 'September'),
        ('October', 'October'), ('November', 'November'), ('December', 'December'),
    ]

    student_admission = models.ForeignKey(StudentAdmission, on_delete=models.CASCADE, related_name='results')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    exam_type = models.CharField(max_length=20, choices=EXAM_TYPE_CHOICES)

    theory = models.FloatField(default=0.0)
    practical = models.FloatField(default=0.0)
    total_marks = models.FloatField(default=0.0)
    obtained_marks = models.FloatField(default=0.0)

    class Meta:
        unique_together = ('student_admission', 'subject', 'exam_type')
        indexes = [
            models.Index(fields=['student_admission', 'subject', 'exam_type']),
        ]

    @property
    def percentage(self):
        return round((self.obtained_marks / self.total_marks * 100), 2) if self.total_marks > 0 else 0.0

    @property
    def grade(self):
        p = self.percentage
        if p >= 90: return 'A+'
        elif p >= 80: return 'A'
        elif p >= 70: return 'B'
        elif p >= 60: return 'C'
        elif p >= 50: return 'D'
        return 'F'

    def save(self, *args, **kwargs):
        self.total_marks = (self.theory or 0) + (self.practical or 0)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student_admission.student} - {self.subject.name} ({self.exam_type})"


# ====================== MONTHLY FEE ======================
class MonthlyFee(TimestampedModel):
    student_admission = models.ForeignKey(StudentAdmission, on_delete=models.CASCADE, related_name='monthly_fees')
    month = models.CharField(max_length=20)
    year = models.PositiveIntegerField()

    previous_balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    monthly_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    transport_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_dues = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    received = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    current_balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    has_payment = models.BooleanField(default=False)
    payment_date = models.DateField(default=timezone.now)
    
    reviewed_by = models.CharField(max_length=100, blank=True, null=True)
    
    operator = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = (('student_admission', 'month', 'year'),)
        indexes = [
            models.Index(fields=['student_admission', 'month', 'year']),
            models.Index(fields=['has_payment']),
        ]

    def save(self, *args, **kwargs):
        if self.monthly_fee == Decimal('0.00'):
            self.monthly_fee = self.student_admission.tuition_fee - (self.student_admission.discount or Decimal('0.00'))

        if self.transport_fee == Decimal('0.00') and self.student_admission.transport == 'Paid':
            self.transport_fee = self.student_admission.transport_fee or Decimal('0.00')

        self.total_dues = self.previous_balance + self.monthly_fee + self.transport_fee
        self.current_balance = self.total_dues - (self.received or Decimal('0.00'))
        self.has_payment = self.received > Decimal('0.00')

        super().save(*args, **kwargs)

        # Update closing balance of StudentAdmission
        if self.student_admission:
            latest = MonthlyFee.objects.filter(
                student_admission=self.student_admission
            ).order_by('-year', '-id').first()

            if latest:
                self.student_admission.closing_balance = latest.current_balance
            else:
                self.student_admission.closing_balance = self.student_admission.balance or Decimal('0.00')

            self.student_admission.save(update_fields=['closing_balance'])

    def __str__(self):
        return f"{self.student_admission.student} - {self.month} {self.year}"


# ====================== TEACHER & STAFF ======================
# ====================== TEACHER ======================
class Teacher(TimestampedModel):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, null=True, blank=True)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='teachers')
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=13, validators=[
        RegexValidator(r'^\+92\d{10}$|^03\d{9}$', 'Enter a valid Pakistani phone number.')
    ])
    cnic = models.CharField(max_length=15, validators=[
        RegexValidator(r'^\d{5}-\d{7}-\d$', 'Enter valid CNIC format.')
    ])
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES)
    class_name = models.CharField(max_length=20, choices=CLASS_CHOICES, blank=True, null=True)
    section = models.CharField(max_length=10, choices=SECTION_CHOICES, blank=True, null=True)
    
    # === Added back for form compatibility ===
    education = models.CharField(max_length=100, blank=True, null=True)
    
    subjects = models.ManyToManyField(Subject, blank=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    date_of_join = models.DateField()
    dob = models.DateField()
    address = models.TextField(blank=True)
    children_in_school = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = (('school', 'cnic'),)
        indexes = [
            models.Index(fields=['school']),
            models.Index(fields=['school', 'cnic']),
        ]

    def __str__(self):
        return f"{self.name} ({self.school.school_name})"


class Staff(TimestampedModel):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='staff')
    name = models.CharField(max_length=100)
    designation = models.CharField(max_length=30, choices=[
        ('Principal', 'Principal'), ('Vice Principal', 'Vice Principal'), ('Coordinator', 'Coordinator'),
        ('Librarian', 'Librarian'), ('Clerk', 'Clerk'), ('Accountant', 'Accountant'),
        ('Security Guard', 'Security Guard'), ('Janitor', 'Janitor'), ('Driver', 'Driver'),
        ('Other', 'Other'),
    ])
    salary = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    date_of_join = models.DateField()
    cnic = models.CharField(max_length=15, unique=True, validators=[
        RegexValidator(r'^\d{5}-\d{7}-\d$', 'Enter valid CNIC format.')
    ])
    contact = models.CharField(max_length=13, validators=[
        RegexValidator(r'^\+92\d{10}$|^03\d{9}$', 'Enter a valid Pakistani phone number.')
    ])
    address = models.TextField(blank=True)
    date_of_birth = models.DateField()

    def __str__(self):
        return f"{self.name} - {self.designation} ({self.school.school_name})"


# ====================== REMAINING MODELS ======================
class Announcement(TimestampedModel):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='announcements')
    title = models.CharField(max_length=200)
    content = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"

    def __str__(self):
        return self.title


class TemporaryPassword(TimestampedModel):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='temporary_password')
    password = models.CharField(max_length=50)
    
    class Meta:
        verbose_name = "Temporary Password"
        verbose_name_plural = "Temporary Passwords"

    def __str__(self):
        return f"Temporary Password for {self.user.username}"

    @staticmethod
    def generate_password(length=12):
        characters = string.ascii_letters + string.digits + string.punctuation
        return ''.join(secrets.choice(characters) for _ in range(length))


class Assets(TimestampedModel):
    CONDITION_CHOICES = [('New', 'New'), ('Used', 'Used')]

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(max_length=100)
    purchased_date = models.DateField()
    file_number = models.CharField(max_length=50, unique=True)
    value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                validators=[MinValueValidator(0)])
    purchased_by = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    condition = models.CharField(max_length=10, choices=CONDITION_CHOICES, default='New')

    class Meta:
        verbose_name = "Asset"
        verbose_name_plural = "Assets"
        unique_together = ('school', 'file_number')

    def __str__(self):
        return f"{self.name} ({self.school.school_name})"


class Expenses(TimestampedModel):
    EXPENSE_TYPE_CHOICES = [('Monthly', 'Monthly'), ('Daily', 'Daily')]
    EXPENSE_NAME_CHOICES = [
        ('Electricity Bill', 'Electricity Bill'), ('WiFi Bill', 'WiFi Bill'),
        ('Building Rent', 'Building Rent'), ('Carpenter', 'Carpenter'),
        ('Transport', 'Transport'), ('Welder', 'Welder'), ('Event', 'Event'),
        ('Electrician', 'Electrician'), ('Maintain', 'Maintain'),
        ('Plumber', 'Plumber'), ('Painter', 'Painter'), ('Tools', 'Tools'),
        ('First Aid', 'First Aid'), ('Sports', 'Sports'), ('Exam', 'Exam'),
        ('Stationary', 'Stationary'), ('Food', 'Food'), ('Other', 'Other'),
    ]

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='expenses')
    expense_type = models.CharField(max_length=10, choices=EXPENSE_TYPE_CHOICES)
    expense_name = models.CharField(max_length=50, choices=EXPENSE_NAME_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
                                validators=[MinValueValidator(0)])
    payment_date = models.DateField(default=timezone.now)
    payment_by = models.CharField(max_length=100)
    file_number = models.CharField(max_length=50, blank=True, null=True)
    period = models.CharField(max_length=20, blank=True, null=True)  # Month or Day
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"

    def clean(self):
        daily_choices = {'Stationary', 'Food'}
        if self.expense_type == 'Daily' and self.expense_name not in daily_choices:
            raise ValidationError("Daily expenses can only be Stationary or Food.")
        if self.expense_type == 'Monthly' and self.expense_name in daily_choices:
            raise ValidationError("Stationary and Food are only for Daily expenses.")

    def __str__(self):
        return f"{self.expense_name} ({self.expense_type}) - {self.school.school_name}"


class Transport(TimestampedModel):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='transports')
    vehicle_number = models.CharField(max_length=20, unique=True)
    driver_name = models.CharField(max_length=100)
    driver_cnic = models.CharField(max_length=15, unique=True, validators=[
        RegexValidator(r'^\d{5}-\d{7}-\d$', 'Enter valid CNIC format.')
    ])
    address = models.TextField()
    date_of_joining = models.DateField()
    date_of_birth = models.DateField()
    number_of_students = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    route = models.CharField(max_length=200)
    contact = models.CharField(max_length=15, validators=[
        RegexValidator(r'^\+?\d{10,15}$', 'Enter a valid phone number.')
    ])

    class Meta:
        unique_together = ('school', 'vehicle_number')
        verbose_name = "Transport"
        verbose_name_plural = "Transports"

    def __str__(self):
        return f"{self.vehicle_number} - {self.driver_name} ({self.school.school_name})"



class Events(TimestampedModel):
    EVENT_TYPE_CHOICES = [
        ('Exam', 'Exam'),
        ('TPM', 'TPM'),
        ('Teacher Meeting', 'Teacher Meeting'),
        ('Result', 'Result'),
        ('Celebration', 'Celebration'),
        ('Tour', 'Tour'),
        ('Other', 'Other'),
        ('Vocation', 'Vocation'),
    ]
    EVENT_FOR_CHOICES = [
        ('All', 'All'),
        ('Students', 'Students'),
        ('Teacher', 'Teacher'),
    ]

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)
    event_for = models.CharField(max_length=10, choices=EVENT_FOR_CHOICES)
    description = models.TextField(blank=True)
    event_date = models.DateField()
    announcement_date = models.DateField(default=timezone.now)
    arranged_by = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Event"
        verbose_name_plural = "Events"
        indexes = [models.Index(fields=['school', 'event_date'])]

    def __str__(self):
        return f"{self.get_event_type_display()} ({self.get_event_for_display()}) - {self.school.school_name}"



        # ====================== SYLLABUS ======================
class Syllabus(TimestampedModel):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='syllabus')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    class_name = models.CharField(max_length=20, choices=CLASS_CHOICES)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    description = models.TextField(blank=True, null=True)
    file = models.FileField(upload_to='syllabus/', blank=True, null=True)
    uploaded_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('school', 'subject', 'class_name', 'academic_session')
        verbose_name = "Syllabus"
        verbose_name_plural = "Syllabus"

    def __str__(self):
        return f"{self.subject.name} - {self.class_name} ({self.school.school_name})"