import unittest

from analyzer import telegram_order_tickets as tickets


class TelegramOrderTicketTests(unittest.TestCase):
    def test_conservative_leverage_uses_only_okx_steps(self):
        expected = {
            1: 1,
            2: 1,
            3: 2,
            4: 2,
            5: 3,
            6: 3,
            8: 5,
            10: 5,
            50: 10,
        }
        for signal, reduced in expected.items():
            with self.subTest(signal=signal):
                self.assertEqual(tickets.reduced_leverage(signal), reduced)
                self.assertIn(reduced, tickets.OKX_AVAILABLE_LEVERAGES)

    def test_position_size_respects_risk_and_capital_caps(self):
        result = tickets.calculate_ticket_size(798.0, 100.0, 96.0)
        self.assertLessEqual(result["risk_usdc"], 11.97)
        self.assertLessEqual(result["amount_usdc"], 199.50)
        self.assertEqual(result["cost_buffer_pct"], 0.26)

    def test_tight_stop_is_still_capped_at_25_percent_capital(self):
        result = tickets.calculate_ticket_size(798.0, 100.0, 99.5)
        self.assertEqual(result["amount_usdc"], 199.50)
        self.assertLessEqual(result["risk_usdc"], 11.97)

    def test_invalid_long_stop_is_rejected(self):
        with self.assertRaises(ValueError):
            tickets.calculate_ticket_size(798.0, 100.0, 101.0)


if __name__ == "__main__":
    unittest.main()
