from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views
from django.contrib.auth.forms import AuthenticationForm

urlpatterns = [
    path('', views.index, name='index'),

    # Developer logic
    path('developer/', views.developer_dashboard, name='developer_dashboard'),
    path('developer/toggle/<int:school_id>/', views.toggle_school_access, name='toggle_school_access'),
    path('developer/reset-password/<int:school_id>/', views.reset_admin_password, name='reset_admin_password'),
    path('developer/add-payment/<int:school_id>/', views.add_payment, name='add_payment'),

    # Authentication
    path('register/', views.register_school, name='register_school'),
    path('login/', views.custom_login, name='login'),  # Updated to custom_login
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    
    path('dashboard/', views.dashboard, name='dashboard'),

    # Student editing
    path('edit-student/<str:student_id>/', views.edit_student, name='edit_student'),

    # Academic session
    path('session/search/', views.search_session, name='search_session'),
    path('session/create/', views.create_academic_session, name='create_session'),

    # Fee structure
    path('add-fee-structure/', views.add_fee_structure, name='add_fee_structure'),
    path('edit-fee-structure/<int:fee_id>/', views.edit_fee_structure, name='edit_fee_structure'),

    # Student Admission
    path('admission/', views.student_admission, name='admission_form'),
    path('admission/success/<str:student_id>/', views.admission_success, name='admission_success'),

    # AJAX for dynamic fee loading
    path('ajax/get-fee-structure/', views.get_fee_structure, name='get_fee_structure'),
    path('ajax/get-transport-details/', views.get_transport_details, name='get_transport_details'),
    path('admission/generate-pdf/<str:student_id>/', views.generate_pdf, name='generate_pdf'),

    path('admission/list/', views.list_admissions, name='list_admission'),

    # Monthly fee logic
    path('monthly-fee/', views.monthly_fee, name='monthly_fee'),
    path('monthly-fee/success/<int:monthly_fee_id>/', views.monthly_fee_success, name='monthly_fee_success'),
    path('monthly-fee/detail/<str:student_id>/', views.monthly_fee_detail, name='monthly_fee_detail'),
    path('monthly-fee/pdf/<int:monthly_fee_id>/', views.monthly_fee_pdf, name='monthly_fee_pdf'),

    # Promotion offline logic
    path('promote-offline/', views.promote_offline_student, name='promote_offline_student'),
    path('promote-offline/preview/', views.promote_offline_preview, name='promote_offline_preview'),
    path('promote-offline/success/<int:student_id>/', views.promote_offline_success, name='promote_offline_success'),
    path('promoted-students-report/', views.promoted_students_report, name='promoted_students_report'),

    # Existing promotion URLs
    path('promote-existing/', views.promote_existing_students, name='promote_existing_students'),
    path('promote-existing/<str:student_id>/', views.promote_existing_student, name='promote_existing_student'),
    path('mark-not-promoted/<str:student_id>/', views.mark_not_promoted, name='mark_not_promoted'),
    path('promote-existing/success/<str:student_id>/', views.promote_existing_success, name='promote_existing_success'),
    path('roll-number-prompt/<str:student_id>/', views.roll_number_prompt, name='roll_number_prompt'),
    #teacher
    path('teachers/', views.teacher_list, name='teacher_list'),
    path('teachers/create/', views.teacher_create, name='teacher_create'),
    path('teachers/update/<int:teacher_id>/', views.teacher_update, name='teacher_update'),
    path('teachers/delete/<int:teacher_id>/', views.teacher_delete, name='teacher_delete'),
    
    path('teacher/dashboard/', views.teacher_dashboard, name='teacher_dashboard'),
    path('result/create/', views.result_create, name='result_create'),
    path('result/edit/<int:result_id>/', views.result_edit, name='result_edit'),
    path('result/delete/<int:result_id>/', views.result_delete, name='result_delete'),
    #suject
    path('subjects/', views.subject_list, name='subject_list'),
    path('subjects/create/', views.subject_create, name='subject_create'),

    #student urls
    path('student/create/', views.student_user_create, name='student_user_create'),
    path('student/list/', views.student_list, name='student_list'),
    path('student/dashboard/', views.student_dashboard, name='student_dashboard'),
    path('results/pdf/', views.generate_results_pdf, name='generate_results_pdf'),


    path('syllabus/add/', views.add_syllabus, name='add_syllabus'),

    #staff
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/<int:pk>/', views.staff_detail, name='staff_detail'),
    path('staff/create/', views.staff_create, name='staff_create'),
    path('staff/<int:pk>/edit/', views.staff_update, name='staff_update'),
    path('staff/<int:pk>/delete/', views.staff_delete, name='staff_delete'),

    #assets
    path('assets/', views.assets_list, name='assets_list'),
    path('assets/<int:pk>/', views.assets_detail, name='assets_detail'),
    path('assets/create/', views.assets_create, name='assets_create'),
    path('assets/<int:pk>/edit/', views.assets_update, name='assets_update'),
    path('assets/<int:pk>/delete/', views.assets_delete, name='assets_delete'),

    #expense
    path('expenses/', views.expenses_list, name='expenses_list'),
    path('expenses/<int:pk>/', views.expenses_detail, name='expenses_detail'),
    path('expenses/create/', views.expenses_create, name='expenses_create'),
    path('expenses/<int:pk>/edit/', views.expenses_update, name='expenses_update'),
    path('expenses/<int:pk>/delete/', views.expenses_delete, name='expenses_delete'),

    #transport
    path('transport/', views.transport_list, name='transport_list'),
    path('transport/<int:pk>/', views.transport_detail, name='transport_detail'),
    path('transport/create/', views.transport_create, name='transport_create'),
    path('transport/<int:pk>/edit/', views.transport_update, name='transport_update'),
    path('transport/<int:pk>/delete/', views.transport_delete, name='transport_delete'),

    #events
    path('events/', views.events_list, name='events_list'),
    path('events/<int:pk>/', views.events_detail, name='events_detail'),
    path('events/create/', views.events_create, name='events_create'),
    path('events/<int:pk>/edit/', views.events_update, name='events_update'),
    path('events/<int:pk>/delete/', views.events_delete, name='events_delete'),

    path('analytics/', views.analytics, name='analytics'),
    path('statistics/', views.statistics_view, name='statistics'),

    #path('ajax/get-model-fields/', views.get_model_fields, name='get_model_fields'),
    path('get_model_fields/', views.get_model_fields, name='get_model_fields'),

    path('download-backup/', views.download_backup, name='download_backup'),

    path('subjects/edit/<int:subject_id>/', views.subject_edit, name='subject_edit'),
    path('subjects/<int:pk>/delete/', views.subject_delete, name='subject_delete'),

    #syllabus
    path('syllabus/', views.syllabus_list, name='syllabus_list'),
    path('syllabus/add/', views.add_syllabus, name='add_syllabus'),
    path('syllabus/<int:syllabus_id>/edit/', views.edit_syllabus, name='edit_syllabus'),
    path('syllabus/<int:syllabus_id>/delete/', views.delete_syllabus, name='delete_syllabus'),

    path('session-analytics/', views.session_analytics, name='session_analytics'),



]

