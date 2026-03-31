from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from config.asgi import application
from notes.models import Note
from notes.services import create_note_version
from users.models import User


class WebSocketFlowTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_user(
            username='socket_owner',
            email='socket_owner@example.com',
            password='testpass123',
        )
        self.collaborator = User.objects.create_user(
            username='socket_collab',
            email='socket_collab@example.com',
            password='testpass123',
        )
        self.invited = User.objects.create_user(
            username='socket_invited',
            email='socket_invited@example.com',
            password='testpass123',
        )
        self.stranger = User.objects.create_user(
            username='socket_stranger',
            email='socket_stranger@example.com',
            password='testpass123',
        )

        self.note = Note.objects.create(
            title='Realtime Note',
            content='Original content',
            owner=self.owner,
        )
        self.note.collaborators.add(self.collaborator)
        create_note_version(
            self.note,
            title=self.note.title,
            content=self.note.content,
            actor=self.owner,
        )

        self.owner_token = str(RefreshToken.for_user(self.owner).access_token)
        self.collaborator_token = str(RefreshToken.for_user(self.collaborator).access_token)
        self.invited_token = str(RefreshToken.for_user(self.invited).access_token)
        self.stranger_token = str(RefreshToken.for_user(self.stranger).access_token)

        self.owner_client = APIClient()
        self.owner_client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.owner_token}')

        self.collaborator_client = APIClient()
        self.collaborator_client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {self.collaborator_token}'
        )

    async def _receive_until_type(self, communicator, expected_type, predicate=None):
        for _ in range(8):
            message = await communicator.receive_json_from(timeout=1)
            if message['type'] == expected_type and (
                predicate is None or predicate(message)
            ):
                return message
        self.fail(f'Expected websocket message type "{expected_type}"')

    def test_notification_socket_receives_share_event(self):
        async_to_sync(self._test_notification_socket_receives_share_event)()

    async def _test_notification_socket_receives_share_event(self):
        communicator = WebsocketCommunicator(
            application,
            f'/ws/notifications/?token={self.invited_token}',
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        response = await sync_to_async(
            lambda: self.owner_client.post(
                f'/api/notes/{self.note.id}/add-user/',
                {'email': self.invited.email},
                format='json',
            )
        )()
        self.assertEqual(response.status_code, 200)

        message = await communicator.receive_json_from(timeout=1)
        self.assertEqual(message['type'], 'note_shared')
        self.assertEqual(message['note_id'], self.note.id)
        self.assertEqual(message['note_title'], self.note.title)

        await communicator.disconnect()

    def test_collaborator_can_join_and_edit_in_realtime(self):
        async_to_sync(self._test_collaborator_can_join_and_edit_in_realtime)()

    async def _test_collaborator_can_join_and_edit_in_realtime(self):
        stranger = WebsocketCommunicator(
            application,
            f'/ws/notes/{self.note.id}/?token={self.stranger_token}',
        )
        connected, _ = await stranger.connect()
        self.assertFalse(connected)

        owner = WebsocketCommunicator(
            application,
            f'/ws/notes/{self.note.id}/?token={self.owner_token}',
        )
        owner_connected, _ = await owner.connect()
        self.assertTrue(owner_connected)
        owner_initial = await owner.receive_json_from(timeout=1)
        self.assertEqual(owner_initial['type'], 'initial_state')
        self.assertEqual(owner_initial['note']['content'], 'Original content')

        collaborator = WebsocketCommunicator(
            application,
            f'/ws/notes/{self.note.id}/?token={self.collaborator_token}',
        )
        collaborator_connected, _ = await collaborator.connect()
        self.assertTrue(collaborator_connected)
        collaborator_initial = await collaborator.receive_json_from(timeout=1)
        self.assertEqual(collaborator_initial['type'], 'initial_state')
        self.assertEqual(collaborator_initial['note']['title'], 'Realtime Note')

        owner_presence = await self._receive_until_type(
            owner,
            'presence_update',
            predicate=lambda message: len(message['online_users']) == 2,
        )
        self.assertEqual(len(owner_presence['online_users']), 2)

        collaborator_presence = await self._receive_until_type(
            collaborator,
            'presence_update',
            predicate=lambda message: len(message['online_users']) == 2,
        )
        self.assertEqual(len(collaborator_presence['online_users']), 2)

        await collaborator.send_json_to(
            {
                'type': 'update',
                'title': 'Realtime Note v2',
                'content': 'Updated through collaborator websocket',
            }
        )

        owner_note_state = await self._receive_until_type(owner, 'note_state')
        self.assertEqual(owner_note_state['note']['title'], 'Realtime Note v2')
        self.assertEqual(owner_note_state['note']['content'], 'Updated through collaborator websocket')
        self.assertEqual(owner_note_state['note']['updated_by']['id'], self.collaborator.id)

        collaborator_content = await self._receive_until_type(collaborator, 'content_update')
        self.assertEqual(collaborator_content['content'], 'Updated through collaborator websocket')

        await collaborator.send_json_to(
            {
                'type': 'cursor',
                'position': 14,
            }
        )
        owner_cursor = await self._receive_until_type(owner, 'cursor_update')
        self.assertEqual(owner_cursor['position'], 14)
        self.assertEqual(owner_cursor['user']['id'], self.collaborator.id)

        await sync_to_async(self.note.refresh_from_db)()
        self.assertEqual(self.note.title, 'Realtime Note v2')
        self.assertEqual(self.note.content, 'Updated through collaborator websocket')

        await collaborator.disconnect()
        await owner.disconnect()

    def test_comment_events_reach_connected_collaborators(self):
        async_to_sync(self._test_comment_events_reach_connected_collaborators)()

    async def _test_comment_events_reach_connected_collaborators(self):
        owner = WebsocketCommunicator(
            application,
            f'/ws/notes/{self.note.id}/?token={self.owner_token}',
        )
        connected, _ = await owner.connect()
        self.assertTrue(connected)
        await owner.receive_json_from(timeout=1)

        response = await sync_to_async(
            lambda: self.collaborator_client.post(
                f'/api/notes/{self.note.id}/comments/',
                {'content': 'Looks good to me.'},
                format='json',
            )
        )()
        self.assertEqual(response.status_code, 201)

        comment_event = await self._receive_until_type(owner, 'comment_added')
        self.assertEqual(comment_event['comment']['content'], 'Looks good to me.')
        self.assertEqual(comment_event['comment']['author']['id'], self.collaborator.id)

        await owner.disconnect()
