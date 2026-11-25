import unittest

from mcbbs.util import base_call, chunk_lines, valid_callsign


def blen(s):
    """Niezalezny od kodu produkcyjnego licznik bajtow UTF-8 (test nie moze uzywac testowanej funkcji)."""
    return len(s.encode("utf-8"))


class UtilTests(unittest.TestCase):
    def test_callsigns(self):
        for ok in ("SP2ONG", "sp2ong-1", "SQ9GIN-12", "DB0SEP-12", "OE1XAR-26", "3Z9ABC", "DL4QB"):
            self.assertTrue(valid_callsign(ok), ok)
        for bad in ("260", "*", "", "SP2", "TOOLONGCALL1", "SP2ONG-123", "A B"):
            self.assertFalse(valid_callsign(bad), bad)
        self.assertEqual(base_call("sp2ong-12"), "SP2ONG")

    def test_single_frame_untouched(self):
        self.assertEqual(chunk_lines(["pong"], 140), ["pong"])

    def test_lines_joined_in_one_frame(self):
        self.assertEqual(chunk_lines(["a", "b"], 140), ["a | b"])

    def test_multi_frame_numbered_and_within_limit(self):
        lines = ["linia numer %d z dosc dlugim tekstem" % i for i in range(12)]
        frames = chunk_lines(lines, 100)
        self.assertGreater(len(frames), 1)
        for i, f in enumerate(frames, 1):
            self.assertLessEqual(blen(f), 100)
            self.assertTrue(f.endswith("(%d/%d)" % (i, len(frames))))

    def test_utf8_never_split_and_limit_in_bytes(self):
        frames = chunk_lines(["zażółć gęślą jaźń " * 20], 60)
        for f in frames:
            self.assertLessEqual(blen(f), 60)
            f.encode("utf-8").decode("utf-8")     # nie rzuca - brak uciętych znaków
        self.assertIn("zażółć", frames[0])

    def test_long_word_hard_split(self):
        frames = chunk_lines(["ą" * 100], 40)
        for f in frames:
            self.assertLessEqual(blen(f), 40)


if __name__ == "__main__":
    unittest.main()
