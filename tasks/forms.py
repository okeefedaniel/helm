from django import forms
from django.contrib.auth import get_user_model

from .models import (
    Project, ProjectAttachment, ProjectCollaborator, ProjectNote,
    Task, TaskComment,
)

User = get_user_model()


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description', 'color']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'description', 'status', 'priority', 'assignee', 'due_date']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }


class TaskCommentForm(forms.ModelForm):
    class Meta:
        model = TaskComment
        fields = ['body']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a comment…'}),
        }


class PromoteForm(forms.Form):
    title = forms.CharField(max_length=240)
    description = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)
    priority = forms.ChoiceField(choices=Task.Priority.choices, initial=Task.Priority.MEDIUM)
    project = forms.ModelChoiceField(queryset=Project.objects.active())
    product_slug = forms.CharField(max_length=32)
    item_type = forms.CharField(max_length=48)
    item_id = forms.CharField(max_length=120, required=False)
    url = forms.URLField()


class ProjectCollaboratorForm(forms.Form):
    """Add a project-level collaborator. Either user_id or email required."""

    user_id = forms.CharField(required=False)
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(
        choices=ProjectCollaborator.Role.choices,
        initial=ProjectCollaborator.Role.CONTRIBUTOR,
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('user_id') and not cleaned.get('email'):
            raise forms.ValidationError('Provide either an internal user or an email.')
        return cleaned


class ProjectNoteForm(forms.ModelForm):
    class Meta:
        model = ProjectNote
        fields = ['content', 'is_internal']
        widgets = {
            'content': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'Add a diligence note…',
            }),
        }


class ProjectAttachmentForm(forms.ModelForm):
    class Meta:
        model = ProjectAttachment
        fields = ['file', 'description', 'visibility']


class ProjectTransitionForm(forms.Form):
    """Single-field status change. Engine validates the transition."""

    status = forms.ChoiceField(choices=Project.Status.choices)
    comment = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
    )
