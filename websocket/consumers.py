import json
from collections import defaultdict

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from notes.models import Note
from notes.services import (
    create_note_version,
    get_note_recipient_ids,
    serialize_note_state,
    user_can_access_note,
)


class NoteConsumer(AsyncWebsocketConsumer):
    active_users = defaultdict(dict)

    async def connect(self):
        self.note_id = int(self.scope['url_route']['kwargs']['note_id'])
        self.room = f'note_{self.note_id}'

        user = self.scope.get('user')
        if not getattr(user, 'is_authenticated', False):
            await self.close(code=4401)
            return

        self.user = user
        self.user_id = user.id

        note_state = await self._get_note_state_for_connection()
        if note_state is None:
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.room, self.channel_name)
        self._register_presence()
        await self.accept()

        await self.send(
            text_data=json.dumps(
                {
                    'type': 'initial_state',
                    'note': note_state,
                    'online_users': self._presence_payload(),
                }
            )
        )

        await self.channel_layer.group_send(
            self.room,
            {
                'type': 'user_joined',
                'user': self._user_payload(),
                'message': 'User joined',
            }
        )
        await self.channel_layer.group_send(
            self.room,
            {
                'type': 'presence_update',
                'online_users': self._presence_payload(),
            }
        )

    async def disconnect(self, close_code):
        if not getattr(self, 'room', None):
            return

        if hasattr(self, 'user_id'):
            self._unregister_presence()
            await self.channel_layer.group_send(
                self.room,
                {
                    'type': 'user_left',
                    'user': self._user_payload(),
                    'message': 'User left',
                }
            )
            await self.channel_layer.group_send(
                self.room,
                {
                    'type': 'presence_update',
                    'online_users': self._presence_payload(),
                }
            )

        await self.channel_layer.group_discard(self.room, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type', 'update')

            if message_type == 'update':
                await self.handle_update(data)
            elif message_type == 'cursor':
                await self.handle_cursor(data)
            elif message_type == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
            else:
                await self.send(text_data=json.dumps({'error': 'Unknown message type'}))
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({'error': 'Invalid JSON'}))
        except Exception as exc:
            await self.send(text_data=json.dumps({'error': str(exc)}))

    async def handle_update(self, data):
        title = data.get('title')
        content = data.get('content')

        updated, note_state, recipient_ids = await self._update_note_state(title, content)
        if not updated:
            return

        await self.channel_layer.group_send(
            self.room,
            {
                'type': 'note_state',
                'note': note_state,
                'reason': 'realtime_update',
                'message': f'{self.user.username} updated the note',
            }
        )

        if content is not None:
            await self.channel_layer.group_send(
                self.room,
                {
                    'type': 'content_update',
                    'content': note_state['content'],
                    'user_id': self.user_id,
                    'timestamp': timezone.now().isoformat(),
                }
            )

        for recipient_id in recipient_ids:
            await self.channel_layer.group_send(
                f'user_notifications_{recipient_id}',
                {
                    'type': 'note_updated',
                    'note_id': self.note_id,
                    'note_title': note_state['title'],
                    'message': f'Note "{note_state["title"]}" was updated',
                }
            )

    async def handle_cursor(self, data):
        await self.channel_layer.group_send(
            self.room,
            {
                'type': 'cursor_update',
                'user_id': self.user_id,
                'user': self._user_payload(),
                'position': data.get('position', 0),
                'timestamp': timezone.now().isoformat(),
            }
        )

    async def content_update(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'content_update',
                    'content': event['content'],
                    'user_id': event['user_id'],
                    'timestamp': event['timestamp'],
                }
            )
        )

    async def note_state(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'note_state',
                    'note': event['note'],
                    'reason': event.get('reason'),
                    'message': event.get('message'),
                }
            )
        )

    async def cursor_update(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'cursor_update',
                    'user_id': event['user_id'],
                    'user': event['user'],
                    'position': event['position'],
                    'timestamp': event['timestamp'],
                }
            )
        )

    async def user_joined(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'user_joined',
                    'user': event['user'],
                    'message': event['message'],
                }
            )
        )

    async def user_left(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'user_left',
                    'user': event['user'],
                    'message': event['message'],
                }
            )
        )

    async def presence_update(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'presence_update',
                    'online_users': event['online_users'],
                }
            )
        )

    async def collaborator_added(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'collaborator_added'}))

    async def note_deleted(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'note_deleted'}))

    async def comment_added(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'comment_added'}))

    async def comment_deleted(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'comment_deleted'}))

    def _user_payload(self):
        return {
            'id': self.user.id,
            'username': self.user.username,
            'email': self.user.email,
        }

    def _register_presence(self):
        self.active_users[self.note_id][self.channel_name] = self._user_payload()

    def _unregister_presence(self):
        room_users = self.active_users.get(self.note_id, {})
        room_users.pop(self.channel_name, None)
        if not room_users and self.note_id in self.active_users:
            del self.active_users[self.note_id]

    def _presence_payload(self):
        seen = {}
        for user in self.active_users.get(self.note_id, {}).values():
            seen[user['id']] = user
        return list(seen.values())

    @database_sync_to_async
    def _get_note_state_for_connection(self):
        try:
            note = Note.objects.prefetch_related('collaborators').get(id=self.note_id)
        except Note.DoesNotExist:
            return None

        if not user_can_access_note(note, self.user):
            return None

        return serialize_note_state(note)

    @database_sync_to_async
    def _update_note_state(self, title, content):
        try:
            note = Note.objects.prefetch_related('collaborators').get(id=self.note_id)
        except Note.DoesNotExist:
            return False, None, []

        if not user_can_access_note(note, self.user):
            return False, None, []

        new_title = note.title if title is None else title
        new_content = note.content if content is None else content

        if new_title == note.title and new_content == note.content:
            return False, serialize_note_state(note, actor=self.user), []

        old_title = note.title
        old_content = note.content

        note.title = new_title
        note.content = new_content
        note.save()

        create_note_version(
            note,
            title=old_title,
            content=old_content,
            actor=self.user,
        )

        recipient_ids = get_note_recipient_ids(note, actor_id=self.user_id)
        return True, serialize_note_state(note, actor=self.user), recipient_ids


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user_id = None
        self.user = None

        user = self.scope.get('user')
        if not getattr(user, 'is_authenticated', False):
            await self.close(code=4401)
            return

        self.user = user
        self.user_id = user.id
        self.room = f'user_notifications_{self.user_id}'

        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.user_id:
            await self.channel_layer.group_discard(self.room, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
        except Exception:
            return

    async def note_shared(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'note_shared',
                    'note_id': event['note_id'],
                    'note_title': event['note_title'],
                    'message': event.get('message', 'A note has been shared with you'),
                }
            )
        )

    async def note_updated(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    'type': 'note_updated',
                    'note_id': event['note_id'],
                    'note_title': event['note_title'],
                    'message': event.get('message', 'A shared note has been updated'),
                }
            )
        )
