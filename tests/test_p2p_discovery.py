import json
import socket
import unittest
import uuid
from unittest.mock import patch

from app.services.p2p_discovery import DiscoverySession, announcement, parse_announcement


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.session = str(uuid.uuid4())
        self.packet = announcement(self.session, 41235, now=1000)

    def test_no_network_on_construction(self):
        with patch('app.services.p2p_discovery.socket.socket') as create:
            DiscoverySession()
        create.assert_not_called()

    def test_valid_packet_and_source(self):
        item = parse_announcement(self.packet, '192.168.1.2', now=1000)
        self.assertEqual((item.session_id, item.host, item.tls_port), (self.session, '192.168.1.2', 41235))

    def test_stale_or_future_packet_rejected(self):
        for now in (969, 1031):
            self.assertIsNone(parse_announcement(self.packet, '127.0.0.1', now=now))

    def test_no_note_data_in_protocol(self):
        data = json.loads(self.packet)
        data['notes'] = 'private note'
        self.assertIsNone(parse_announcement(json.dumps(data).encode(), '127.0.0.1', now=1000))
        self.assertNotIn(b'name', self.packet)

    def test_malformed_and_untrusted_packets(self):
        for packet in (b'x' * 513, b'[]', b'null', b'\xff', b'{', self.packet[:-1] + b',"version":1}'):
            self.assertIsNone(parse_announcement(packet, '127.0.0.1', now=1000))
        for host in ('8.8.8.8', '0.0.0.0', '224.0.0.1', 'invalid', '::1'):
            self.assertIsNone(parse_announcement(self.packet, host, now=1000))

    def test_invalid_fields(self):
        for key, value in [('version', True), ('tls_port', True), ('tls_port', 0),
                           ('timestamp', True), ('session', 'not a uuid')]:
            data = json.loads(self.packet)
            data[key] = value
            self.assertIsNone(parse_announcement(json.dumps(data).encode(), '127.0.0.1', now=1000))

    def test_real_loopback_discovery_and_close(self):
        sender, receiver = DiscoverySession(), DiscoverySession()
        self.addCleanup(sender.close)
        self.addCleanup(receiver.close)
        sender.start()
        target = receiver.start()
        sender.advertise(target, 41235)
        found = receiver.poll(timeout=1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].session_id, sender.session_id)
        receiver.close()
        with self.assertRaises(RuntimeError):
            receiver.poll()

    def test_oversized_udp_packet_is_not_truncated_into_valid_packet(self):
        receiver = DiscoverySession()
        self.addCleanup(receiver.close)
        target = receiver.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(self.packet + b' ' * 600, target)
        self.assertEqual(receiver.poll(timeout=1), [])

    def test_own_announcement_ignored_and_rate_limited(self):
        receiver = DiscoverySession()
        self.addCleanup(receiver.close)
        target = receiver.start()
        receiver.advertise(target, 41235)
        self.assertEqual(receiver.poll(timeout=1), [])
        with self.assertRaises(RuntimeError):
            receiver.advertise(target, 41235)

    def test_public_destination_and_invalid_timeout_rejected(self):
        item = DiscoverySession()
        self.addCleanup(item.close)
        item.start()
        with self.assertRaises(ValueError):
            item.advertise(('8.8.8.8', 41235), 41235)
        with self.assertRaises(ValueError):
            item.poll(timeout=2)
