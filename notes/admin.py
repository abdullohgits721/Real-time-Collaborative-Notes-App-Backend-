from django.contrib import admin

from .models import Note, NoteComment, NoteVersion


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'owner', 'is_done', 'updated_at')
    search_fields = ('title', 'content', 'owner__username', 'owner__email')
    filter_horizontal = ('collaborators',)


@admin.register(NoteVersion)
class NoteVersionAdmin(admin.ModelAdmin):
    list_display = ('id', 'note', 'created_by', 'created_at')
    search_fields = ('note__title', 'created_by__username', 'created_by__email')


@admin.register(NoteComment)
class NoteCommentAdmin(admin.ModelAdmin):
    list_display = ('id', 'note', 'author', 'created_at', 'updated_at')
    search_fields = ('note__title', 'author__username', 'author__email', 'content')
