from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from notes.models import Note
from notes.services import create_note_version
from users.models import User


class NotePermissionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner_ui',
            email='owner_ui@example.com',
            password='testpass123',
        )
        self.collaborator = User.objects.create_user(
            username='collab_ui',
            email='collab_ui@example.com',
            password='testpass123',
        )
        self.other_user = User.objects.create_user(
            username='other_ui',
            email='other_ui@example.com',
            password='testpass123',
        )

        self.note = Note.objects.create(
            title='Launch plan',
            content='Ship the new release',
            owner=self.owner,
        )
        create_note_version(
            self.note,
            title=self.note.title,
            content=self.note.content,
            actor=self.owner,
        )

        self.owner_client = APIClient()
        self.owner_client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.owner).access_token}'
        )

        self.collaborator_client = APIClient()
        self.collaborator_client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.collaborator).access_token}'
        )

        self.other_client = APIClient()
        self.other_client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.other_user).access_token}'
        )

    def test_owner_can_share_with_collaborator_by_email(self):
        response = self.owner_client.post(
            f'/api/notes/{self.note.id}/share/',
            {'email': self.collaborator.email},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.note.refresh_from_db()
        self.assertTrue(self.note.collaborators.filter(id=self.collaborator.id).exists())
        self.assertEqual(response.data['note']['id'], self.note.id)

    def test_collaborator_can_edit_and_restore_versions(self):
        self.note.collaborators.add(self.collaborator)

        update_response = self.collaborator_client.put(
            f'/api/notes/{self.note.id}/',
            {'title': 'Updated launch plan', 'content': 'Coordinate release notes'},
            format='json',
        )

        self.assertEqual(update_response.status_code, 200)
        self.note.refresh_from_db()
        self.assertEqual(self.note.title, 'Updated launch plan')
        self.assertEqual(self.note.content, 'Coordinate release notes')

        versions_response = self.collaborator_client.get(
            f'/api/notes/{self.note.id}/versions/'
        )
        self.assertEqual(versions_response.status_code, 200)
        self.assertGreaterEqual(len(versions_response.data), 2)
        self.assertEqual(versions_response.data[0]['title'], 'Launch plan')
        self.assertEqual(versions_response.data[0]['content'], 'Ship the new release')

        restore_response = self.collaborator_client.post(
            f"/api/notes/{self.note.id}/versions/{versions_response.data[0]['id']}/restore/",
            format='json',
        )
        self.assertEqual(restore_response.status_code, 200)

        self.note.refresh_from_db()
        self.assertEqual(self.note.title, 'Launch plan')
        self.assertEqual(self.note.content, 'Ship the new release')

    def test_comment_visibility_and_deletion_permissions(self):
        self.note.collaborators.add(self.collaborator)

        create_response = self.collaborator_client.post(
            f'/api/notes/{self.note.id}/comments/',
            {'content': 'Please add the rollout checklist.'},
            format='json',
        )
        self.assertEqual(create_response.status_code, 201)
        comment_id = create_response.data['id']

        list_response = self.owner_client.get(f'/api/notes/{self.note.id}/comments/')
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]['content'], 'Please add the rollout checklist.')

        forbidden_delete = self.other_client.delete(
            f'/api/notes/{self.note.id}/comments/{comment_id}/delete/'
        )
        self.assertEqual(forbidden_delete.status_code, 403)

        owner_delete = self.owner_client.delete(
            f'/api/notes/{self.note.id}/comments/{comment_id}/delete/'
        )
        self.assertEqual(owner_delete.status_code, 200)
