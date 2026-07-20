import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services.incident_duration import age_for_incident, calculate_incident_duration, duration_for_incident, is_final_status


class IncidentDurationTest(unittest.TestCase):
    def test_open_incident_uses_current_local_date_by_civil_days(self):
        today = date(2026, 7, 20)
        self.assertEqual(calculate_incident_duration(start_date=datetime(2026, 7, 20, 0, 0), today=today).label, "0 dias")
        self.assertEqual(calculate_incident_duration(start_date=datetime(2026, 7, 19, 23, 59), today=today).label, "1 dia")
        self.assertEqual(calculate_incident_duration(start_date=datetime(2026, 6, 20, 0, 0), today=today).label, "30 dias")
        self.assertEqual(calculate_incident_duration(start_date=date(2025, 7, 20), today=today).days, 365)

    def test_closed_incident_uses_end_date(self):
        self.assertEqual(
            calculate_incident_duration(
                start_date=datetime(2026, 7, 20, 8, 0),
                end_date=datetime(2026, 7, 20, 18, 0),
                today=date(2026, 7, 20),
            ).label,
            "0 dias",
        )
        self.assertEqual(
            calculate_incident_duration(
                start_date=datetime(2026, 7, 20, 23, 59),
                end_date=datetime(2026, 7, 21, 0, 1),
            ).label,
            "1 dia",
        )
        self.assertEqual(
            calculate_incident_duration(
                start_date=datetime(2025, 12, 31, 12, 0),
                end_date=datetime(2026, 1, 2, 12, 0),
            ).label,
            "2 dias",
        )

    def test_zero_or_inconsistent_past_incident_falls_back_to_today(self):
        self.assertEqual(
            calculate_incident_duration(
                start_date=datetime(2026, 7, 2, 8, 0),
                end_date=datetime(2026, 7, 2, 9, 0),
                today=date(2026, 7, 20),
            ).label,
            "18 dias",
        )
        recovered = calculate_incident_duration(
            start_date=datetime(2026, 7, 19),
            end_date=datetime(2026, 7, 18),
            today=date(2026, 7, 20),
        )
        self.assertEqual(recovered.label, "1 dia")
        self.assertEqual(recovered.reference, "start_date->today")

    def test_zero_time_is_valid_date_not_missing(self):
        duration = calculate_incident_duration(
            start_date=datetime(2026, 7, 1, 0, 0, 0),
            today=date(2026, 7, 20),
        )
        self.assertEqual(duration.days, 19)
        self.assertEqual(duration.status, "valid")

    def test_missing_and_inconsistent_dates_are_not_converted_to_zero(self):
        self.assertEqual(calculate_incident_duration(start_date=None).label, "Não informado")
        inconsistent = calculate_incident_duration(
            start_date=datetime(2026, 7, 21),
            end_date=datetime(2026, 7, 20),
            today=date(2026, 7, 20),
        )
        self.assertEqual(inconsistent.label, "Data inconsistente")
        self.assertIsNone(inconsistent.days)

    def test_text_and_timezone_values_are_normalized_to_sao_paulo_date(self):
        sao_paulo = ZoneInfo("America/Sao_Paulo")
        utc_start = datetime(2026, 7, 20, 2, 30, tzinfo=timezone.utc)
        local_end = datetime(2026, 7, 20, 1, 0, tzinfo=sao_paulo)
        duration = calculate_incident_duration(start_date=utc_start, end_date=local_end)
        self.assertEqual(duration.days, 1)
        self.assertEqual(
            calculate_incident_duration(start_date="2026-07-18 00:00:00", today=date(2026, 7, 20)).label,
            "2 dias",
        )
        self.assertEqual(calculate_incident_duration(start_date="data inválida").label, "Não informado")

    def test_duration_for_incident_uses_model_like_fields(self):
        incident = SimpleNamespace(
            start_date=datetime(2026, 7, 10),
            end_date=None,
            status_incident="Em análise",
            created_at=datetime(2026, 7, 11),
        )
        self.assertEqual(duration_for_incident(incident, today=date(2026, 7, 20)).days, 10)

    def test_listing_age_ignores_end_date_and_counts_from_opening_to_today(self):
        incident = SimpleNamespace(
            start_date=datetime(2026, 6, 15, 14, 34),
            end_date=datetime(2026, 6, 17, 23, 34, 50),
            status_incident="Encerrado",
            created_at=datetime(2026, 6, 15, 14, 34),
        )
        self.assertEqual(duration_for_incident(incident, today=date(2026, 7, 20)).label, "2 dias")
        self.assertEqual(age_for_incident(incident, today=date(2026, 7, 20)).label, "35 dias")

    def test_final_status_normalization(self):
        self.assertTrue(is_final_status(" encerrado "))
        self.assertTrue(is_final_status("FALSO POSITIVO"))
        self.assertFalse(is_final_status("Em mitigação"))


if __name__ == "__main__":
    unittest.main()
