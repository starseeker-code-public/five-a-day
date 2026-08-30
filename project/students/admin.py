from django.contrib import admin

from students.models import Group, Parent, Student, StudentParent, Teacher

admin.site.register(Teacher)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ["group_name", "teacher", "max_students", "enrolled_count", "available_spots", "active"]
    list_filter = ["active", "teacher"]
    search_fields = ["group_name"]


# Students and parents
class StudentParentInline(admin.TabularInline):
    model = StudentParent
    extra = 1  # Number of empty forms to display
    autocomplete_fields = ["parent"]


class ParentStudentInline(admin.TabularInline):
    model = StudentParent
    extra = 1
    autocomplete_fields = ["student"]


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "group", "active", "is_waiting", "waiting_priority", "birth_date"]
    list_filter = ["group", "active", "is_waiting", "waiting_priority", "gdpr_signed"]
    search_fields = ["first_name", "last_name"]
    inlines = [StudentParentInline]
    readonly_fields = ["waiting_since"]

    fieldsets = (
        ("Personal Information", {"fields": ("first_name", "last_name", "birth_date")}),
        ("School Information", {"fields": ("school", "group")}),
        ("Health & Preferences", {"fields": ("allergies", "gdpr_signed")}),
        (
            "Status",
            {
                "fields": (
                    "active",
                    "is_waiting",
                    "waiting_since",
                    "waiting_priority",
                    "withdrawal_date",
                    "withdrawal_reason",
                )
            },
        ),
    )


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "dni", "phone", "email"]
    search_fields = ["first_name", "last_name", "dni", "email"]
    inlines = [ParentStudentInline]

    fieldsets = (
        ("Personal Information", {"fields": ("first_name", "last_name", "dni")}),
        ("Contact Information", {"fields": ("phone", "email", "iban")}),
    )


@admin.register(StudentParent)
class StudentParentAdmin(admin.ModelAdmin):
    list_display = ["student", "parent"]
    list_filter = ["student__group"]
    autocomplete_fields = ["student", "parent"]
