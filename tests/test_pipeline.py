"""Offline tests. Run from project root:
    python -m unittest discover -s tests -v      (or)      pytest -v
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_common as pc  # noqa: E402
import rtff_arg_processor as ap  # noqa: E402
import rtff_reservoir_processor as rp  # noqa: E402
from pipeline_common import classify_duplicates, gap_stats  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def full_rec(date_val="01-04-2022", fill=0.0, daily=0.0, argid="S"):
    r = {"argid": argid, "date_val": date_val}
    for h in ap.ARG_HOUR_FIELDS:
        r[h] = fill
    r["dailyrainfall"] = daily
    return r


class ArgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hourly, cls.recs, cls.issues, cls.tiles = ap.process_arg_directory(
            FIX / "arg_ok", FIX)
        cls.q = ap.compute_arg_quality(cls.recs, cls.issues, cls.tiles).iloc[0]

    def test_arg_json_parsing(self):
        self.assertEqual(self.tiles, {"STN1": 2})
        self.assertEqual(len(self.recs), 6)
        self.assertEqual(len(self.hourly), 6 * 24)
        self.assertTrue((self.recs["source_date"].str.match(r"\d{2}-\d{2}-\d{4}")).all())

    def test_null_rainfall_preserved(self):
        self.assertEqual(int(self.hourly["rainfall_value"].isna().sum()), 49)
        self.assertEqual(self.q["null_hourly_values"], 49)
        self.assertEqual(self.q["non_null_hourly_values"], 95)
        self.assertFalse((self.hourly["rainfall_value"] == 0).all())
        # an all-null source day must stay all-null (not zero)
        day = self.hourly[(self.hourly.source_date == "02-04-2022")
                          & (self.hourly.source_file.str.contains("04-01_2022-04-08"))]
        self.assertTrue(day["rainfall_value"].isna().all())

    def test_all_24_hour_fields(self):
        recs, hourly, _ = ap.process_arg_object([full_rec()], "S", "x.json")
        self.assertEqual(len(hourly), 24)
        self.assertEqual([h["source_hour_label"] for h in hourly], ap.ARG_HOUR_FIELDS)
        self.assertEqual(ap.ARG_HOUR_FIELDS[0], "h09_30")
        self.assertEqual(ap.ARG_HOUR_FIELDS[15], "h00_30")
        self.assertEqual(ap.ARG_HOUR_FIELDS[-1], "h08_30")
        self.assertEqual(recs[0]["missing_hour_fields"], "")

    def test_duplicate_detection(self):
        q = self.q
        self.assertEqual(q["raw_day_rows"], 6)
        self.assertEqual(q["unique_source_dates"], 4)
        self.assertEqual(q["duplicate_source_dates"], 2)
        self.assertEqual(q["duplicate_extra_rows"], 2)
        self.assertEqual(q["repeated_dates_across_tiles"], 2)
        self.assertEqual(q["conflicting_dates"], 1)
        r = self.recs.sort_values(["source_file", "source_record_index"])
        c = classify_duplicates(r, ["station_id", "source_date"], "source_record_signature")
        self.assertEqual(int((~c["keep"]).sum()), 1)  # only the exact 04-04 copy
        self.assertEqual(int((c.record_status == "conflicting_source_versions").sum()), 2)

    def test_missing_date_detection_not_zero_filled(self):
        self.assertEqual(self.q["missing_calendar_days"], 3)   # 03, 05, 06
        self.assertEqual(self.q["largest_missing_gap_days"], 2)
        self.assertEqual(self.q["span_days"], 7)
        self.assertNotIn("2022-04-03", set(self.recs["parsed_date"].dropna()))
        s = gap_stats([date(2022, 1, 1), date(2022, 1, 5)])
        self.assertEqual((s["missing_days"], s["largest_gap"]), (3, 3))

    def test_malformed_handling(self):
        h, r, i, t = ap.process_arg_directory(FIX / "arg_bad", FIX)
        types = set(i["issue_type"])
        self.assertTrue({"malformed_record", "unreadable_file", "non_numeric_hourly"} <= types)
        self.assertEqual(len(r), 1)                       # "oops" skipped, reported
        self.assertEqual(len(h), 3)                       # only present fields emitted
        self.assertIsNone(r.iloc[0]["parsed_date"])       # 31-02-2022 invalid
        reasons = r.iloc[0]["suspicious_reasons"]
        for k in ("unparsable_date", "missing_hour_fields", "unexpected_fields",
                  "daily_non_numeric", "hourly_negative"):
            self.assertIn(k, reasons)
        self.assertTrue(h["rainfall_value"].isna().iloc[1])  # "abc" -> null, not 0
        self.assertEqual(t["STN2"], 2)

    def test_no_unit_fabrication(self):
        forbidden = ("mm", "unit", "pct", "percent", "cumec", "tmc", "mcft")
        for df in (self.hourly, self.recs):
            for col in df.columns:
                self.assertFalse(any(f in col.lower() for f in forbidden), col)

    def test_source_provenance_preserved(self):
        self.assertTrue(self.hourly["source_file"].str.startswith("arg_ok/STN1/").all())
        self.assertTrue(self.hourly["source_tile_start"].notna().all())
        first = self.hourly[self.hourly.source_file.str.contains("2022-04-01_2022-04-08")].iloc[0]
        self.assertEqual((first.source_tile_start, first.source_tile_end),
                         ("2022-04-01", "2022-04-08"))
        self.assertEqual(first.source_hour_label, "h09_30")
        self.assertEqual(set(self.recs["source_tile_range_origin"]), {"filename"})

    def test_filename_range_fallback_to_records(self):
        recs, _, _ = ap.process_arg_object([full_rec("05-04-2022"), full_rec("09-04-2022")],
                                           "S", "x.json", None)
        self.assertEqual((recs[0]["source_tile_start"], recs[0]["source_tile_end"]),
                         ("2022-04-05", "2022-04-09"))
        self.assertEqual(recs[0]["source_tile_range_origin"], "records")


class ReservoirTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df, cls.issues, cls.tiles = rp.process_reservoir_directory(
            FIX / "reservoir", FIX)
        cls.q = rp.compute_reservoir_quality(cls.df, cls.issues, cls.tiles).iloc[0]

    def test_double_encoded_json(self):
        obj, layers = pc.load_json_file(FIX / "reservoir/RES1/tile_2022-04-08_2022-04-15.json")
        self.assertEqual(layers, 1)
        self.assertIsInstance(obj, list)
        self.assertEqual(len(obj), 3)
        single = json.dumps([{"date": "2022-04-08T00:00:00"}])
        self.assertEqual(pc.decode_json_text(single)[1], 0)
        triple = json.dumps(json.dumps(single))
        self.assertIsInstance(pc.decode_json_text(triple)[0], list)

    def test_records_and_nulls(self):
        self.assertEqual(len(self.df), 3)
        self.assertEqual(self.q["null_waterlevel"], 1)
        self.assertTrue(pd.isna(self.df.iloc[1]["waterlevel"]))   # not zero
        self.assertEqual(self.df.iloc[0]["storage"], 82.51)       # numeric type
        self.assertEqual(self.q["storage_min"], 81.90)
        self.assertEqual(self.q["storage_max"], 82.51)

    def test_gaps_and_duplicates(self):
        self.assertEqual(self.q["missing_calendar_dates"], 1)
        self.assertEqual(self.q["largest_gap_days"], 1)
        self.assertEqual(self.q["duplicate_dates"], 0)
        self.assertEqual(self.q["distinct_time_labels"], "6:00 AM")

    def test_no_unit_fabrication(self):
        forbidden = ("pct", "percent", "cumec", "tmc", "mcft", "unit", "_mm")
        for col in self.df.columns:
            self.assertFalse(any(f in col.lower() for f in forbidden), col)
        for c in ("storage", "inflow_total", "outflow_total"):
            self.assertIn(c, self.df.columns)

    def test_provenance(self):
        self.assertTrue(self.df["source_file"].str.startswith("reservoir/RES1/").all())
        self.assertEqual(self.df.iloc[0]["date_source"], "2022-04-08T00:00:00")
        self.assertEqual(self.df.iloc[0]["source_tile_start"], "2022-04-08")

    def test_reservoir_malformed(self):
        rows, issues = rp.process_reservoir_object(
            [1, {"date": "bad", "waterlevel": "x", "tankid": "OTHER"}], "R", "f.json")
        self.assertEqual(len(rows), 1)
        self.assertTrue(pd.isna(rows[0]["waterlevel"]))
        self.assertIn("unparsable_date", rows[0]["suspicious_reasons"])
        self.assertIn("tankid_differs_from_directory", rows[0]["suspicious_reasons"])
        self.assertEqual({i["issue_type"] for i in issues},
                         {"malformed_record", "non_numeric_value"})


if __name__ == "__main__":
    unittest.main()