from django.test import TestCase
from decimal import Decimal
from datetime import date

from .models import (
    CustomUser, School, AcademicSession, Student, StudentAdmission,
    MonthlyFee, FeeStructure
)
from .forms import LoginForm, SchoolRegisterForm
from .view_helpers import bulk_promote_students


class AuthFormTests(TestCase):
    def test_school_registration_rejects_weak_password(self):
        form = SchoolRegisterForm(data={
            'username': 'adminuser',
            'email': 'admin@example.com',
            'password1': '1234567',
            'password2': '1234567',
            'school_name': 'Bright Stars School',
            'city': 'Lahore',
            'owner_name': 'Ali Khan',
            'contact_number': '+923001234567',
            'number_of_students': 100,
        })

        self.assertFalse(form.is_valid())
        self.assertIn('password1', form.errors)

    def test_login_form_trims_username_and_password(self):
        form = LoginForm(data={'username': ' admin ', 'password': ' secret '})
        self.assertEqual(form.data['username'], 'admin')
        self.assertEqual(form.data['password'], 'secret')


class PromotionIntegrationTest(TestCase):
    def test_promote_creates_monthly_fee_with_previous_balance(self):
        # Create admin user and school
        admin = CustomUser.objects.create_user(username='admin', password='pass', user_type=1)
        school = School.objects.create(
            school_name='Test School',
            city='Test City',
            owner_name='Owner',
            contact_number='+12345678901',
            admin_user=admin
        )

        # Previous (ended) session
        prev_session = AcademicSession.objects.create(
            session_name='2020',
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),
            school=school
        )

        # Active target session (this will deactivate the previous session)
        target_session = AcademicSession.objects.create(
            session_name='2021',
            start_date=date(2021, 1, 1),
            end_date=date(2021, 12, 31),
            school=school
        )

        # Create a student and an admission in the previous session
        student = Student.objects.create(
            first_name='John', last_name='Doe', father_guardian_name='Father',
            contact='+923001234567', date_of_birth=date(2010, 1, 1), gender='Male', school=school
        )

        admission_prev = StudentAdmission.objects.create(
            student=student,
            academic_session=prev_session,
            class_name='Class 1',
            roll_number=1,
            section='A_Red',
            tuition_fee=Decimal('500.00'),
            total_dues=Decimal('500.00'),
            received=Decimal('0.00'),
            balance=Decimal('500.00')
        )

        # Create a last-month MonthlyFee for the previous session with a non-zero current_balance
        prev_month_fee = MonthlyFee.objects.create(
            student_admission=admission_prev,
            month=prev_session.end_date.strftime('%B'),
            year=prev_session.end_date.year,
            previous_balance=Decimal('0.00'),
            monthly_fee=Decimal('500.00'),
            transport_fee=Decimal('0.00'),
            total_dues=Decimal('500.00'),
            received=Decimal('100.00'),
            current_balance=Decimal('400.00')
        )

        # Create fee structure for the target session and promoted class
        FeeStructure.objects.create(
            school=school,
            academic_session=target_session,
            class_name='Class 2',
            tuition_fee=Decimal('600.00'),
            admission_fee=Decimal('0.00'),
            books_dues=Decimal('0.00'),
            uniform_dues=Decimal('0.00'),
            paper_money=Decimal('0.00'),
            promotion_fee=Decimal('0.00')
        )

        # Run bulk promotion helper
        promoted = bulk_promote_students([admission_prev.pk], target_session, 'A_Red', {}, admin)
        self.assertEqual(len(promoted), 1)
        new_admission = promoted[0]

        # MonthlyFee should have been created for the promoted admission with previous_balance == prev_month_fee.current_balance
        month = target_session.start_date.strftime('%B')
        year = target_session.start_date.year
        monthly = MonthlyFee.objects.get(student_admission=new_admission, month=month, year=year)
        self.assertEqual(monthly.previous_balance, prev_month_fee.current_balance)

