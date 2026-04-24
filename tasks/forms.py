from django import forms
from django.contrib.auth import get_user_model

from .models import Project, Task, TaskComment

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
    project = forms.ModelChoiceField(queryset=Project.objects.filter(archived=False))
    product_slug = forms.CharField(max_length=32)
    item_type = forms.CharField(max_length=48)
    item_id = forms.CharField(max_length=120, required=False)
    url = forms.URLField()
