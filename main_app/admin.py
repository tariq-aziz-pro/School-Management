from django.contrib import admin
from .models import CustomUser, School, AcademicSession, FeeStructure, Student, StudentAdmission, StudentResult, MonthlyFee

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['student_id', 'first_name', 'last_name', 'father_guardian_name', 'contact', 'school']
    search_fields = ['student_id', 'first_name', 'last_name', 'father_guardian_name']
    list_filter = ['school', 'gender']

@admin.register(StudentAdmission)
class StudentAdmissionAdmin(admin.ModelAdmin):
    def get_student_id(self, obj):
        return obj.student.student_id
    get_student_id.short_description = 'Registration ID'

    def get_first_name(self, obj):
        return obj.student.first_name
    get_first_name.short_description = 'First Name'

    def get_last_name(self, obj):
        return obj.student.last_name
    get_last_name.short_description = 'Last Name'

    list_display = ['get_student_id', 'get_first_name', 'get_last_name', 'class_name', 'section', 'roll_number', 'academic_session']
    search_fields = ['student__student_id', 'student__first_name', 'student__last_name', 'class_name', 'section', 'roll_number']
    list_filter = ['class_name', 'section', 'academic_session', 'status', 'promoted', 'failed_to_promote']
    readonly_fields = ['total_dues', 'balance']

@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ['username', 'email', 'user_type', 'school']
    search_fields = ['username', 'email']
    list_filter = ['user_type', 'school']

@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ['school_name', 'city', 'owner_name', 'contact_number', 'number_of_students']
    search_fields = ['school_name', 'city', 'owner_name']
    list_filter = ['city']

@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ['session_name', 'start_date', 'end_date', 'is_active', 'school', 'school_id']
    search_fields = ['session_name']
    list_filter = ['is_active', 'school']

@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ['class_name', 'academic_session', 'tuition_fee', 'admission_fee']
    search_fields = ['class_name', 'academic_session__session_name']
    list_filter = ['class_name', 'academic_session']

@admin.register(StudentResult)
class StudentResultAdmin(admin.ModelAdmin):
    list_display = ['student_admission', 'subject', 'exam_type', 'obtained_marks', 'total_marks', 'percentage', 'grade']
    search_fields = ['student_admission__student__first_name', 'student_admission__student__last_name', 'subject']
    list_filter = ['exam_type', 'grade', 'student_admission__class_name']

@admin.register(MonthlyFee)
class MonthlyFeeAdmin(admin.ModelAdmin):
    list_display = ['student_admission', 'month', 'year', 'previous_balance', 'monthly_fee', 'transport_fee', 'total_dues', 'received', 'current_balance']
    search_fields = ['student_admission__student__first_name', 'student_admission__student__last_name', 'month', 'year']
    list_filter = ['month', 'year', 'student_admission__class_name']