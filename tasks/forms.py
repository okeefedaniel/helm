from django import forms
from django.contrib.auth import get_user_model

from .models import (
    Project, ProjectAttachment, ProjectCollaborator, ProjectNote,
    Task, TaskComment,
)

User = get_user_model()


class BootstrapFormMixin:
    """Auto-apply Bootstrap classes to widgets that don't already declare one.

    Keeps every Helm form aligned with the keel design system without each
    field having to set `attrs={'class': 'form-control'}` by hand.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get('class', '')
            if 'form-control' in existing or 'form-select' in existing or 'form-check-input' in existing:
                continue
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                bs_class = 'form-check-input'
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                bs_class = 'form-select'
            elif isinstance(widget, forms.FileInput):
                bs_class = 'form-control'
            else:
                bs_class = 'form-control'
            widget.attrs['class'] = (existing + ' ' + bs_class).strip()


class ProjectForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description', 'color']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class TaskForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Task
        fields = ['title', 'description', 'status', 'priority', 'assignee', 'due_date']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }


class TaskCommentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = TaskComment
        fields = ['body']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Add a comment…'}),
        }


class PromoteForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(max_length=240)
    description = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)
    priority = forms.ChoiceField(choices=Task.Priority.choices, initial=Task.Priority.MEDIUM)
    project = forms.ModelChoiceField(queryset=Project.objects.active())
    product_slug = forms.CharField(max_length=32)
    item_type = forms.CharField(max_length=48)
    item_id = forms.CharField(max_length=120, required=False)
    url = forms.URLField()


class ProjectCollaboratorForm(BootstrapFormMixin, forms.Form):
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


class ProjectNoteForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectNote
        fields = ['content', 'is_internal']
        widgets = {
            'content': forms.Textarea(attrs={
                'rows': 3, 'placeholder': 'Add a diligence note…',
            }),
        }


class ProjectAttachmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProjectAttachment
        fields = ['file', 'description', 'visibility']


class ProjectTransitionForm(BootstrapFormMixin, forms.Form):
    """Single-field status change. Engine validates the transition."""

    status = forms.ChoiceField(choices=Project.Status.choices)
    comment = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
    )
