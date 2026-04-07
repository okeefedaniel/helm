from django import forms
from django.contrib.auth.forms import AuthenticationForm
from keel.accounts.forms import LoginForm  # noqa: F401


# LoginForm is now shared in Keel for suite-wide consistency.
