from django.urls import path
from .views import register, login, verify_token

urlpatterns = [
    path('register/', register),
    path('login/', login),
    path('verify/', verify_token),
]