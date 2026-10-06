"""Offline tests for the rainfall CSV audit.
    python -m unittest discover -s tests -v      (or)      pytest -v
"""
import hashlib
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rainfall_csv_processor as rc  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
UNIT_RX = re.compile(r"(^|_)(mm|unit|units|cm|inch)($|_)")


def audit(path, **kw):
    df, enc = rc.read_rainfall_csv(path)
    return rc.audit_rainfall(df, "rainfall/x.csv", dict(encoding_used=enc), **kw)


def metric(res, name):
    q = res["quality"]
    return q[q["metric"] == name].iloc[0]["value"]


class RainfallAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = audit(FIX / "rainfall" / "sample.csv", hints=("zone 13",))
        cls.rows = cls.res["rows"]

    def test_ddmmyyyy_parsing_is_dayfirst(self):
        self.assertEqual(self.rows.iloc[5]["parsed_date"], "2020-01-13")  # day 13 => day-first
        self.assertEqual(self.rows.iloc[6]["parsed_date"], "2020-03-02")  # 2 March, not 3 Feb
        self.assertTrue(self.rows["date_format_conformant_ddmmyyyy"].all())
        self.assertEqual(metric(self.res, "first_date"), "2020-01-01")
        self.assertEqual(metric(self.res, "last_date"), "2021-01-01")

    def test_zero_parse_failures(self):
        self.assertEqual(metric(self.res, "total_rows"), "11")
        self.assertEqual(metric(self.res, "parsed_rows"), "11")
        self.assertEqual(metric(self.res, "unparseable_rows"), "0")
        self.assertEqual(metric(self.res, "date_parse_failures"), "0")

    def test_unparseable_dates_reported_and_rows_kept(self):
        bad = audit(FIX / "rainfall_bad_date" / "bad.csv")
        self.assertEqual(metric(bad, "date_parse_failures"), "2")
        self.assertEqual(metric(bad, "parsed_rows"), "1")
        self.assertEqual(len(bad["rows"]), 3)                       # nothing dropped
        self.assertEqual(int((bad["rows"]["date_status"] == "unparseable").sum()), 2)
        self.assertEqual(set(bad["rows"]["record_status"]) - {"unique"}, {"missing_key_kept"})

    def test_duplicate_station_date_detection(self):
        self.assertEqual(metric(self.res, "true_duplicate_station_date_rows"), "4")
        self.assertEqual(metric(self.res, "duplicate_station_date_keys"), "3")
        self.assertEqual(metric(self.res, "rows_in_duplicated_keys"), "7")

    def test_exact_vs_conflicting_duplicates(self):
        self.assertEqual(metric(self.res, "exact_duplicate_keys"), "1")
        self.assertEqual(metric(self.res, "exact_duplicate_extra_rows"), "1")
        self.assertEqual(metric(self.res, "conflicting_keys"), "2")
        self.assertEqual(metric(self.res, "conflicting_extra_rows"), "3")
        self.assertEqual(metric(self.res, "rows_in_conflicting_keys"), "5")
        self.assertEqual(metric(self.res, "duplicate_full_rows"), "2")
        self.assertEqual(metric(self.res, "conflict_max_abs_rainfall_difference"), "1.0")
        rs = self.rows["record_status"].value_counts()
        self.assertEqual(rs["conflicting_source_versions"], 5)
        self.assertEqual(rs["exact_duplicate_first_kept"], 2)
        self.assertEqual(int(self.rows["exact_duplicate_later_copy"].sum()), 1)
        self.assertEqual(len(self.rows), 11)                        # nothing dropped

    def test_null_rainfall_preserved(self):
        r = self.rows.iloc[7]
        self.assertEqual(r["rainfall_raw"], "")
        self.assertTrue(pd.isna(r["rainfall_value"]))               # not zero
        self.assertEqual(r["rainfall_status"], "null")

    def test_station_coverage(self):
        st = self.res["stations"].set_index("station")
        a, b = st.loc["Alpha"], st.loc["Beta"]
        self.assertEqual((a.row_count, a.unique_dates), (5, 3))
        self.assertEqual((a.missing_calendar_days, a.largest_missing_gap_days), (2, 2))
        self.assertEqual(a.duplicate_station_date_count, 2)
        self.assertEqual(a.conflicting_station_date_keys, 1)
        self.assertEqual((b.first_date, b.last_date), ("2020-01-13", "2020-03-03"))
        self.assertEqual((b.missing_calendar_days, b.largest_missing_gap_days), (48, 48))
        self.assertEqual(b.null_rainfall_rows, 1)

    def test_yearly_counts(self):
        y = self.res["yearly"].set_index("year")
        self.assertEqual(y.loc["2020", "row_count"], 8)
        self.assertEqual(y.loc["2020", "station_count"], 2)
        self.assertEqual(y.loc["2020", "unique_station_dates"], 6)
        self.assertEqual(y.loc["2020", "duplicate_extra_rows"], 2)
        self.assertEqual(y.loc["2021", "row_count"], 3)
        self.assertEqual(len(self.res["station_year"]), 3)

    def test_name_hint_is_text_match_only(self):
        st = self.res["stations"].set_index("station")
        self.assertEqual(st.loc["Gamma Zone 13", "study_area_name_hint_match"], "zone 13")
        self.assertTrue(pd.isna(st.loc["Alpha", "study_area_name_hint_match"]))
        self.assertIn("geography unverified", st.loc["Alpha", "hint_basis"])

    def test_reference_values_not_fabricated(self):
        # the sample does NOT contain 1447 duplicates, so the check must say False
        self.assertEqual(metric(self.res, "true_duplicate_station_date_rows_reproduces_user_reported"), "False")
        self.assertEqual(metric(self.res, "date_parse_failures_reproduces_user_reported"), "True")

    def test_no_unit_fabrication(self):
        for key in ("rows", "stations", "yearly", "station_year", "quality"):
            for col in self.res[key].columns:
                self.assertIsNone(UNIT_RX.search(col.lower()), col)
        notes = " ".join(self.res["quality"]["note"])
        self.assertIn("UNIT UNVERIFIED", notes)


class RainfallRunTests(unittest.TestCase):
    def test_raw_preserved_and_outputs_written(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            raw = tmp / "Datasets" / "raw" / "rainfall"
            raw.mkdir(parents=True)
            csv = raw / "Chennai Daily Rainfall test.csv"
            shutil.copy(FIX / "rainfall" / "sample.csv", csv)
            before = hashlib.sha256(csv.read_bytes()).hexdigest()
            rc.run_rainfall(root=tmp, verbose=False)
            self.assertEqual(hashlib.sha256(csv.read_bytes()).hexdigest(), before)

            out = pd.read_csv(tmp / "Datasets/processed/rainfall_csv_source_rows.csv",
                              dtype=str, keep_default_na=False)
            self.assertEqual(len(out), 11)                          # no rows dropped
            self.assertEqual(out.iloc[5]["date_raw"], "13-01-2020") # raw string intact
            self.assertEqual(out.iloc[7]["rainfall_raw"], "")
            self.assertEqual(out.iloc[7]["rainfall_value"], "")     # not "0"
            self.assertTrue(out["source_file"].str.startswith("Datasets/raw/rainfall/").all())

            q = pd.read_csv(tmp / "Datasets/metadata/rainfall_csv_quality.csv",
                            dtype=str, keep_default_na=False)
            m = dict(zip(q["metric"], q["value"]))
            self.assertEqual(m["source_sha256"], before)
            for name in ("rainfall_csv_station_coverage.csv", "rainfall_csv_yearly.csv",
                         "rainfall_csv_station_year.csv"):
                self.assertTrue((tmp / "Datasets/metadata" / name).exists(), name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()