from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .services.validators import ALLOWED_EXTENSIONS


class UploadForm(forms.Form):

    image = forms.ImageField(
        label="Document image",
        error_messages={
            "required": "Please choose an image to scan.",
            "invalid": "Upload a valid image file (JPG, PNG or WebP).",
        },
        widget=forms.ClearableFileInput(attrs={"accept": ",".join(sorted(ALLOWED_EXTENSIONS))}),
    )


class RegisterForm(UserCreationForm):

    email = forms.EmailField(required=False, help_text="Optional.")

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("username", "email", "password1", "password2"):
            widget = self.fields[field_name].widget
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} form-control".strip()
