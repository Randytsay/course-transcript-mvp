"""Offline contract probes for the existing two-step Drive gateway.

The fake's 409 is a modeled provider response, not proof of provider behavior.
"""
import unittest
from unittest.mock import patch

import requests

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_delivery import DriveDeliveryGateway


class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.content = b''

    def json(self):
        return self._body


class CommitThenTimeoutSession:
    def __init__(self):
        self.files = {}
        self.history = []

    def request(self, method, url, *, params=None, json=None, data=None, **kwargs):
        self.history.append((method, url, json))
        if method == 'GET':
            return Response(404)
        assert method == 'POST' and json is not None
        ident = json['id']
        if ident in self.files:
            return Response(409)
        self.files[ident] = dict(json)
        raise requests.Timeout('server committed; response lost')

    def patch(self, *args, **kwargs):
        self.history.append(('PATCH', args, kwargs))
        raise AssertionError('content must not be written after unverified 409')


class GatewayEvidenceTests(unittest.TestCase):
    @patch('app.jobs.drive_api._refresh_access_token', return_value='offline-fake-token')
    def test_lost_metadata_response_then_409_is_not_success_or_media_upload(self, _token):
        gateway = DriveDeliveryGateway()
        gateway.http = CommitThenTimeoutSession()
        args = ('pre-generated-exact-id', 'parent', '.pending', b'content', 'job')
        with self.assertRaises(requests.Timeout):
            gateway.create_pending(*args)
        with self.assertRaises(V2Conflict):
            gateway.create_pending(*args)
        self.assertEqual(list(gateway.http.files), ['pre-generated-exact-id'])
        self.assertEqual([item[0] for item in gateway.http.history], ['POST', 'POST'])
        self.assertEqual(gateway.http.history[0][2], gateway.http.history[1][2])

    @patch('app.jobs.drive_api._refresh_access_token', return_value='offline-fake-token')
    def test_get_404_maps_to_none_without_proving_never_created(self, _token):
        gateway = DriveDeliveryGateway()
        gateway.http = CommitThenTimeoutSession()
        self.assertIsNone(gateway.get('inaccessible-or-deleted-or-unused'))
        self.assertFalse(hasattr(gateway, 'prove_never_created'))


if __name__ == '__main__':
    unittest.main()
