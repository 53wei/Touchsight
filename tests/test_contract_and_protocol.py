import unittest

from touchsight_edu.contract import COLS, ROWS, TactileFrame
from touchsight_edu.hardware import decode_wire_frame, encode_wire_frame


class ContractAndProtocolTests(unittest.TestCase):
    def test_contract_is_exactly_40_by_50_h0_to_h3(self):
        frame = TactileFrame(pins=tuple(index % 4 for index in range(COLS * ROWS)), frame_id=7)
        self.assertEqual((frame.cols, frame.rows, frame.levels), (40, 50, 4))
        self.assertEqual(len(frame.height_rows()), 50)
        self.assertTrue(all(len(row) == 40 for row in frame.height_rows()))

    def test_rejects_wrong_pin_count_and_height(self):
        with self.assertRaises(ValueError):
            TactileFrame(pins=(0,) * 1999)
        with self.assertRaises(ValueError):
            TactileFrame(pins=(0,) * 1999 + (4,))

    def test_two_bit_wire_round_trip(self):
        source = TactileFrame(pins=tuple(index % 4 for index in range(COLS * ROWS)), frame_id=99)
        encoded = encode_wire_frame(source)
        decoded = decode_wire_frame(encoded)
        self.assertEqual(decoded, source)
        self.assertEqual(len(encoded), 520)


if __name__ == "__main__":
    unittest.main()
