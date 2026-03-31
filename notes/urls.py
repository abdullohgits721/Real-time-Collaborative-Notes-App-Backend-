from django.urls import path
from .views import *

urlpatterns = [
    path('', get_notes),
    path('<int:note_id>/detail/', get_note_detail),
    path('create/', create_note),
    path('<int:note_id>/', update_note),
    path('<int:note_id>/toggle-done/', toggle_note_done),
    path('<int:note_id>/delete/', delete_note),
    path('<int:note_id>/share/', add_collaborator),
    path('<int:note_id>/add-user/', add_collaborator),
    path('<int:note_id>/versions/', get_note_versions),
    path('<int:note_id>/versions/<int:version_id>/restore/', restore_note_version),
    path('<int:note_id>/comments/', note_comments),
    path('<int:note_id>/comments/<int:comment_id>/delete/', delete_comment),
]
