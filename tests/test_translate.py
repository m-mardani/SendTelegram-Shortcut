import unittest
from unittest import mock

from hold2tg import translate as translate_mod
from hold2tg.translate import Popup, TranslateError, Translator, looks_like, markup, parse, plain
from tests.test_sender import RecordingNotifier

RUN = {  # trimmed from a real answer for "run"
    "sentences": [{"trans": "اجرا کنید", "orig": "run"},
                  {"translit": "ejra konid", "src_translit": "rən"}],
    "src": "en",
    "dict": [{"pos": "فعل", "terms": ["دویدن", "راندن"]}, {"pos": "اسم", "terms": []}],
    "definitions": [{"pos": "فعل", "entry": [
        {"gloss": "move fast", "example": "the dog ran across the road"},
        {"gloss": "x", "example": "I run every morning"}]}],
    "examples": {"example": [{"text": "I <b>run</b> every morning"},
                             {"text": "O&#39;Brien &amp; I <b>run</b>"}]},
}


class Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class PureTests(unittest.TestCase):
    def test_parse_a_word(self):
        r = parse("run", RUN, "fa")
        self.assertEqual((r.src_lang, r.tgt_lang, r.text, r.pronunciation),
                         ("en", "fa", "اجرا کنید", "rən"))
        self.assertEqual(r.meanings, [("فعل", ["دویدن", "راندن"])])  # empty groups dropped
        # Google's examples first, then the definitions' ones, without repeats.
        self.assertEqual([plain(e) for e in r.examples],
                         ["I run every morning", "O'Brien & I run", "the dog ran across the road"])

    def test_parse_a_sentence(self):
        r = parse("Hi there.", {"sentences": [{"trans": "سلام "}, {"trans": "آنجا."}],
                                "src": "en"}, "fa")
        self.assertEqual((r.text, r.meanings, r.examples), ("سلام آنجا.", [], []))

    def test_markup_keeps_only_the_bold(self):
        self.assertEqual(markup("O&#39;Brien <i>&</i> <b>run</b>"),
                         "O&#x27;Brien &lt;i&gt;&amp;&lt;/i&gt; <b>run</b>")

    def test_looks_like_persian(self):
        self.assertTrue(looks_like("fa", "امروز هوا خوب است"))
        self.assertTrue(looks_like("fa", "این API خوب است"))
        self.assertFalse(looks_like("fa", "hello دنیا and more words"))
        self.assertFalse(looks_like("fa", "123 !!"))
        self.assertFalse(looks_like("de", "امروز"))  # only known for Arabic script


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self.t = Translator("fa", "en")
        self.calls = []
        self.answers = []

        def post(url, params, data, timeout):
            self.calls.append((dict(params)["tl"], data["q"]))
            return self.answers.pop(0)

        self.t.session.post = post

    def test_other_languages_go_into_persian_in_one_request(self):
        self.answers = [Response(200, RUN)]
        r = self.t.translate("  run\n")
        self.assertEqual(self.calls, [("fa", "run")])
        self.assertEqual(r.tgt_lang, "fa")

    def test_persian_goes_into_english_in_one_request(self):
        self.answers = [Response(200, {"sentences": [{"trans": "book"}], "src": "fa"})]
        self.assertEqual(self.t.translate("کتاب").text, "book")
        self.assertEqual(self.calls, [("en", "کتاب")])

    def test_a_wrong_guess_is_corrected(self):
        # Arabic looks Persian, but is not Persian: it must end up in Persian.
        self.answers = [Response(200, {"sentences": [{"trans": "book"}], "src": "ar"}),
                        Response(200, {"sentences": [{"trans": "کتاب"}], "src": "ar"})]
        r = self.t.translate("الكتاب")
        self.assertEqual([c[0] for c in self.calls], ["en", "fa"])
        self.assertEqual((r.text, r.tgt_lang), ("کتاب", "fa"))

    def test_long_text_is_capped(self):
        self.answers = [Response(200, {"sentences": [], "src": "en"})]
        self.t.translate("a" * 9000)
        self.assertEqual(len(self.calls[0][1]), translate_mod.MAX_CHARS)

    def test_lines_come_back_one_to_one_or_not_at_all(self):
        self.answers = [Response(200, {"sentences": [{"trans": "یک\n"}, {"trans": "دو"}]}),
                        Response(200, {"sentences": [{"trans": "یک و دو"}]})]
        self.assertEqual(self.t.translate_lines(["one", "two"], "en", "fa"), ["یک", "دو"])
        self.assertEqual(self.t.translate_lines(["one", "two"], "en", "fa"), [])
        self.assertEqual(self.t.translate_lines([], "en", "fa"), [])

    def test_errors(self):
        self.answers = [Response(429, None)]
        with self.assertRaises(TranslateError) as cm:
            self.t.translate("x")
        self.assertTrue(cm.exception.blocked)
        for answer in (Response(500, None), Response(200, ValueError()), Response(200, [1])):
            self.answers = [answer]
            with self.assertRaises(TranslateError) as cm:
                self.t.translate("x")
            self.assertFalse(cm.exception.blocked)

    def test_network_error(self):
        self.t.session.post = mock.Mock(
            side_effect=self.t._requests.ConnectionError("refused"))
        with self.assertRaises(TranslateError) as cm:
            self.t.translate("x")
        self.assertIn("network error", str(cm.exception))


class PopupTests(unittest.TestCase):
    def setUp(self):
        self.notifier = RecordingNotifier()
        self.popup = Popup(self.notifier)

    def test_nothing_selected(self):
        with mock.patch.object(translate_mod.subprocess, "Popen") as popen:
            self.popup.show("  \n")
        popen.assert_not_called()
        self.assertEqual(self.notifier.shown[0][0], self.notifier.t("nothing"))

    def test_text_goes_through_stdin_and_a_new_box_replaces_the_old(self):
        with mock.patch.object(translate_mod.subprocess, "Popen") as popen:
            first = popen.return_value
            first.poll.return_value = None  # still open
            self.popup.show("سلام")
            argv = popen.call_args[0][0]
            self.assertEqual(argv[1:], ["-m", "hold2tg", "translate", "-"])
            first.stdin.write.assert_called_once_with("سلام".encode("utf-8"))
            self.popup.show("again")
            first.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
