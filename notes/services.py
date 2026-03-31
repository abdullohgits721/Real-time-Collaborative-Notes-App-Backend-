from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import NoteVersion


MAX_NOTE_VERSIONS = 20


def user_can_access_note(note, user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.id == note.owner_id:
        return True
    return note.collaborators.filter(id=user.id).exists()


def trim_note_versions(note):
    version_ids = list(
        NoteVersion.objects.filter(note=note)
        .order_by('-created_at')
        .values_list('id', flat=True)[MAX_NOTE_VERSIONS:]
    )
    if version_ids:
        NoteVersion.objects.filter(id__in=version_ids).delete()


def create_note_version(note, *, title=None, content=None, actor=None):
    version = NoteVersion.objects.create(
        note=note,
        title=note.title if title is None else title,
        content=note.content if content is None else content,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    trim_note_versions(note)
    return version


def get_note_room_name(note_id):
    return f'note_{note_id}'


def get_user_notification_room_name(user_id):
    return f'user_notifications_{user_id}'


def get_note_recipient_ids(note, actor_id=None):
    recipient_ids = set(note.collaborators.values_list('id', flat=True))
    recipient_ids.add(note.owner_id)
    if actor_id is not None:
        recipient_ids.discard(actor_id)
    return list(recipient_ids)


def serialize_note_state(note, actor=None):
    payload = {
        'id': note.id,
        'title': note.title,
        'content': note.content,
        'is_done': note.is_done,
        'owner_id': note.owner_id,
        'updated_at': note.updated_at.isoformat(),
    }
    if actor is not None:
        payload['updated_by'] = {
            'id': actor.id,
            'username': actor.username,
            'email': actor.email,
        }
    return payload


def broadcast_note_event(note_id, event):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(get_note_room_name(note_id), event)


def notify_note_recipients(note, *, actor=None, event_type, message):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    actor_id = getattr(actor, 'id', None)
    for recipient_id in get_note_recipient_ids(note, actor_id=actor_id):
        async_to_sync(channel_layer.group_send)(
            get_user_notification_room_name(recipient_id),
            {
                'type': event_type,
                'note_id': note.id,
                'note_title': note.title,
                'message': message,
            }
        )
