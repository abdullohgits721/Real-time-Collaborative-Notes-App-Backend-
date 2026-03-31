from django.db import models
from django.conf import settings


class Note(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    is_done = models.BooleanField(default=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='shared_notes')

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class NoteVersion(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name='versions')
    title = models.CharField(max_length=255, blank=True, default='')
    content = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='note_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Version for {self.note_id} at {self.created_at.isoformat()}'


class NoteComment(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='note_comments',
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Comment {self.id} on note {self.note_id}'
