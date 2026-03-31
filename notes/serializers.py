from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import Note, NoteComment, NoteVersion

User = get_user_model()


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class NoteSerializer(serializers.ModelSerializer):
    owner = UserSummarySerializer(read_only=True)
    collaborators = UserSummarySerializer(many=True, read_only=True)

    class Meta:
        model = Note
        fields = ['id', 'title', 'content', 'is_done', 'owner', 'collaborators', 'updated_at']
        read_only_fields = ['id', 'owner', 'collaborators', 'updated_at']


class NoteVersionSerializer(serializers.ModelSerializer):
    created_by = UserSummarySerializer(read_only=True)

    class Meta:
        model = NoteVersion
        fields = ['id', 'title', 'content', 'created_at', 'created_by']
        read_only_fields = fields


class NoteCommentSerializer(serializers.ModelSerializer):
    author = UserSummarySerializer(read_only=True)

    class Meta:
        model = NoteComment
        fields = ['id', 'content', 'author', 'created_at', 'updated_at']
        read_only_fields = ['id', 'author', 'created_at', 'updated_at']
