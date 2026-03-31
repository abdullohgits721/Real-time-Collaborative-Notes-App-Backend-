from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Note, NoteComment, NoteVersion
from .serializers import NoteCommentSerializer, NoteSerializer, NoteVersionSerializer
from .services import (
    broadcast_note_event,
    create_note_version,
    notify_note_recipients,
    serialize_note_state,
    user_can_access_note,
)

User = get_user_model()


def check_permission(user, note):
    if not user_can_access_note(note, user):
        raise PermissionDenied("No access")


def check_owner_permission(user, note):
    if user != note.owner:
        raise PermissionDenied("Only the owner can perform this action")


def get_note_or_404(note_id):
    try:
        return Note.objects.prefetch_related('collaborators').select_related('owner').get(id=note_id)
    except Note.DoesNotExist:
        return None


def broadcast_note_state(note, actor, reason, message=None):
    broadcast_note_event(
        note.id,
        {
            'type': 'note_state',
            'note': serialize_note_state(note, actor=actor),
            'reason': reason,
            'message': message,
        }
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_notes(request):
    notes = (
        Note.objects.filter(Q(owner=request.user) | Q(collaborators=request.user))
        .select_related('owner')
        .prefetch_related('collaborators')
        .distinct()
        .order_by('-updated_at')
    )
    serializer = NoteSerializer(notes, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_note_detail(request, note_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_permission(request.user, note)
    serializer = NoteSerializer(note)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_note(request):
    serializer = NoteSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    note = serializer.save(owner=request.user)
    create_note_version(
        note,
        title=note.title,
        content=note.content,
        actor=request.user,
    )
    return Response(NoteSerializer(note).data, status=201)


@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_note(request, note_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_permission(request.user, note)

    new_title = request.data.get('title', note.title)
    new_content = request.data.get('content', note.content)

    if new_title == note.title and new_content == note.content:
        return Response(NoteSerializer(note).data)

    old_title = note.title
    old_content = note.content

    note.title = new_title
    note.content = new_content
    note.save()

    create_note_version(
        note,
        title=old_title,
        content=old_content,
        actor=request.user,
    )

    broadcast_note_state(
        note,
        request.user,
        reason='note_updated',
        message=f'{request.user.username} updated "{note.title}"',
    )
    notify_note_recipients(
        note,
        actor=request.user,
        event_type='note_updated',
        message=f'{request.user.username} updated "{note.title}"',
    )

    return Response(NoteSerializer(note).data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def toggle_note_done(request, note_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_permission(request.user, note)

    requested_state = request.data.get('is_done')
    if isinstance(requested_state, bool):
        note.is_done = requested_state
    elif isinstance(requested_state, str) and requested_state.lower() in {'true', 'false'}:
        note.is_done = requested_state.lower() == 'true'
    else:
        note.is_done = not note.is_done

    note.save()

    verb = 'marked as done' if note.is_done else 'reopened'
    broadcast_note_state(
        note,
        request.user,
        reason='done_toggled',
        message=f'{request.user.username} {verb} "{note.title}"',
    )
    notify_note_recipients(
        note,
        actor=request.user,
        event_type='note_updated',
        message=f'{request.user.username} {verb} "{note.title}"',
    )

    return Response(NoteSerializer(note).data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_note(request, note_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_owner_permission(request.user, note)

    broadcast_note_event(
        note.id,
        {
            'type': 'note_deleted',
            'note_id': note.id,
            'message': f'{request.user.username} deleted "{note.title}"',
        }
    )
    notify_note_recipients(
        note,
        actor=request.user,
        event_type='note_updated',
        message=f'{request.user.username} deleted "{note.title}"',
    )

    note.delete()
    return Response({"message": "Deleted"})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_collaborator(request, note_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_owner_permission(request.user, note)

    email = (request.data.get('email') or '').strip()
    if not email:
        return Response({"error": "Collaborator email is required"}, status=400)

    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return Response({"error": "User with that email was not found"}, status=404)

    if user == note.owner:
        return Response({"error": "Owner already has access to this note"}, status=400)

    if note.collaborators.filter(id=user.id).exists():
        return Response({"error": "User is already a collaborator"}, status=400)

    note.collaborators.add(user)
    note.refresh_from_db()

    broadcast_note_event(
        note.id,
        {
            'type': 'collaborator_added',
            'note_id': note.id,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
            },
            'message': f'{user.username} can now collaborate on "{note.title}"',
        }
    )
    notify_note_recipients(
        note,
        actor=request.user,
        event_type='note_shared',
        message=f'{request.user.username} shared "{note.title}"',
    )

    return Response(
        {
            "message": "Collaborator added",
            "note": NoteSerializer(note).data,
        }
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_note_versions(request, note_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_permission(request.user, note)

    versions = (
        NoteVersion.objects.filter(note=note)
        .select_related('created_by')
        .order_by('-created_at')[:20]
    )
    return Response(NoteVersionSerializer(versions, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def restore_note_version(request, note_id, version_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_permission(request.user, note)

    try:
        version = NoteVersion.objects.get(id=version_id, note=note)
    except NoteVersion.DoesNotExist:
        return Response({"error": "Version not found"}, status=404)

    create_note_version(note, title=note.title, content=note.content, actor=request.user)

    note.title = version.title or note.title
    note.content = version.content
    note.save()

    broadcast_note_state(
        note,
        request.user,
        reason='version_restored',
        message=f'{request.user.username} restored "{note.title}" to an earlier version',
    )
    notify_note_recipients(
        note,
        actor=request.user,
        event_type='note_updated',
        message=f'{request.user.username} restored "{note.title}" to an earlier version',
    )

    return Response(NoteSerializer(note).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def note_comments(request, note_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_permission(request.user, note)

    if request.method == 'GET':
        comments = note.comments.select_related('author').all()
        return Response(NoteCommentSerializer(comments, many=True).data)

    serializer = NoteCommentSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    comment = serializer.save(note=note, author=request.user)
    comment_data = NoteCommentSerializer(comment).data

    broadcast_note_event(
        note.id,
        {
            'type': 'comment_added',
            'note_id': note.id,
            'comment': comment_data,
            'message': f'{request.user.username} commented on "{note.title}"',
        }
    )
    notify_note_recipients(
        note,
        actor=request.user,
        event_type='note_updated',
        message=f'{request.user.username} commented on "{note.title}"',
    )

    return Response(comment_data, status=201)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_comment(request, note_id, comment_id):
    note = get_note_or_404(note_id)
    if note is None:
        return Response({"error": "Note not found"}, status=404)

    check_permission(request.user, note)

    try:
        comment = NoteComment.objects.select_related('author').get(id=comment_id, note=note)
    except NoteComment.DoesNotExist:
        return Response({"error": "Comment not found"}, status=404)

    if comment.author != request.user and note.owner != request.user:
        raise PermissionDenied("Only the author or note owner can delete this comment")

    comment_payload = {
        'id': comment.id,
        'note_id': note.id,
    }
    comment.delete()

    broadcast_note_event(
        note.id,
        {
            'type': 'comment_deleted',
            'comment': comment_payload,
            'message': f'Comment {comment_payload["id"]} was removed',
        }
    )

    return Response({"message": "Comment deleted"})
