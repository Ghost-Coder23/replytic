from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SignInView.as_view(), name="login"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("logout/", views.SignOutView.as_view(), name="logout"),
    path("password/change/", views.ChangePasswordView.as_view(), name="password_change"),
    path("password/reset/", views.ResetPasswordView.as_view(), name="password_reset"),
    path("password/reset/sent/", views.ResetPasswordDoneView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", views.ResetPasswordConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/complete/", views.ResetPasswordCompleteView.as_view(), name="password_reset_complete"),
]
