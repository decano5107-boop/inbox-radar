"""GmailSource against a fake of the Gmail API's client surface: no network, no credentials."""
import base64
import unittest

from inbox_radar.sources import GmailSource


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class Call:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGmail:
    def __init__(self, messages):
        self.store = messages
        self.queries, self.formats = [], []

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def list(self, userId, q, pageToken=None):
        self.queries.append(q)
        if q.startswith("from:"):
            ids = [m for m, v in self.store.items() if v["ruled"]]
        else:
            ids = [m for m, v in self.store.items() if not v["ruled"]]
        return Call({"messages": [{"id": i} for i in ids]})

    def get(self, userId, id, format=None, messageId=None, metadataHeaders=None):
        if messageId:                                   # attachments().get
            return Call({"size": 10, "data": ""})
        self.formats.append((id, format))
        self.asked_headers = metadataHeaders
        v = self.store[id]
        headers = [{"name": "From", "value": v["from"]}, {"name": "Subject", "value": "s"},
                   {"name": "X-Secret", "value": "nope"}]
        if metadataHeaders is not None:                 # what the real API does
            headers = [h for h in headers if h["name"] in metadataHeaders]
        payload = {"headers": headers}
        if format == "full":
            payload["mimeType"] = "multipart/alternative"
            payload["parts"] = [{"mimeType": "text/html", "body": {"data": b64(v["html"])}}]
        return Call({"id": id, "internalDate": "1778750000000", "payload": payload})


class Gmail(unittest.TestCase):
    def setUp(self):
        self.api = FakeGmail({
            "a": {"ruled": True, "from": "Acme <bills@acme.example>",
                  "html": "<p>Amount due <b>$80.10</b></p><script>steal()</script>"},
            "b": {"ruled": False, "from": "Shop <news@shop.example>", "html": "code 482913"},
            "c": {"ruled": True, "from": "bills@acme.example <impostor@else.example>", "html": "code 551203"},
        })
        self.messages = {m.id: m for m in GmailSource(self.api).messages(["bills@acme.example"])}

    def test_ruled_senders_get_bodies_others_only_headers(self):
        self.assertIn(("a", "full"), self.api.formats)
        self.assertIn(("b", "metadata"), self.api.formats)
        self.assertEqual(self.messages["b"].body, "")

    def test_a_display_name_that_imitates_a_ruled_address_gets_no_body(self):
        self.assertEqual(self.messages["c"].body, "")
        self.assertNotIn(("c", "full"), self.api.formats)

    def test_html_is_reduced_to_text_without_scripts(self):
        body = self.messages["a"].body
        self.assertIn("Amount due", body)
        self.assertIn("$80.10", body)
        self.assertNotIn("steal", body)

    def test_unruled_senders_do_not_even_give_a_subject(self):
        self.assertEqual(self.messages["b"].subject, "")
        self.assertNotIn("Subject", GmailSource.TRIAGE_HEADERS)

    def test_deep_mime_nesting_is_cut_off(self):
        part = {"mimeType": "text/plain", "body": {"data": b64("deep")}}
        for _ in range(2000):
            part = {"mimeType": "multipart/mixed", "parts": [part]}
        plain = []
        GmailSource(self.api)._walk(part, plain, [], [])
        self.assertEqual(plain, [])

    def test_only_triage_headers_are_kept(self):
        self.assertNotIn("X-Secret", self.messages["a"].headers)

    def test_queries_name_the_ruled_senders_and_exclude_them_from_the_header_scan(self):
        self.assertIn("from:(bills@acme.example)", self.api.queries[0])
        self.assertIn("-from:bills@acme.example", self.api.queries[1])
