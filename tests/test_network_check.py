import tempfile
import unittest
from pathlib import Path

from app import Database, DomainError, seed_demo
from network_check import check_network


def make_point(name, x=None, y=None, elev=None, x_known=False, y_known=False, elev_known=False):
    return {
        "point": name, "x": x, "y": y, "elevation": elev,
        "x_known": int(x_known), "y_known": int(y_known), "elevation_known": int(elev_known),
    }


def make_obs(kind, p1, p2, value, p3=None):
    return {"kind": kind, "p1": p1, "p2": p2, "p3": p3, "value": value, "weight": 1.0, "status": "valid"}


def codes(check, net=None):
    issues = check["issues"] if net is None else check[net]["issues"]
    return [issue["code"] for issue in issues]


class NetworkCheckRulesTest(unittest.TestCase):
    def test_complete_network_is_solvable(self):
        points = [
            make_point("A", 0, 0, 100, True, True, True),
            make_point("B", 100, 0, 100, True, True, True),
            make_point("C", 50, 50, 110),
        ]
        observations = [
            make_obs("distance", "A", "C", 70.71),
            make_obs("distance", "B", "C", 70.71),
            make_obs("height_difference", "B", "C", 10.0),
        ]
        check = check_network(points, observations)
        self.assertTrue(check["solvable"])
        self.assertEqual(check["issues"], [])
        self.assertEqual(check["planar"]["components"][0]["points"], ["A", "B", "C"])

    def test_isolated_unknown_point_gets_segment_suggestion(self):
        points = [
            make_point("A", 0, 0, 100, True, True, True),
            make_point("B", 100, 0, 100, True, True, True),
            make_point("C", 50, 50, 110),
            make_point("D", 150, 60, 108),
        ]
        observations = [
            make_obs("distance", "A", "C", 70.71),
            make_obs("distance", "B", "C", 70.71),
            make_obs("height_difference", "B", "C", 10.0),
        ]
        check = check_network(points, observations)
        self.assertFalse(check["solvable"])
        self.assertIn("planar.isolated", codes(check))
        self.assertIn("elevation.isolated", codes(check))
        issue = next(i for i in check["issues"] if i["code"] == "planar.isolated")
        self.assertIn("D", issue["message"])
        self.assertIn("D–B", issue["suggestion"])

    def test_disconnected_groups_and_missing_datum_are_both_reported(self):
        points = [
            make_point("A", 0, 0, 100, True, True, True),
            make_point("B", 100, 0, 100, True, True, True),
            make_point("C", 50, 50, 110),
            make_point("D", 200, 0, 120),
            make_point("E", 200, 100, 122),
        ]
        observations = [
            make_obs("distance", "A", "C", 70.71),
            make_obs("distance", "B", "C", 70.71),
            make_obs("distance", "D", "E", 100.0),
            make_obs("height_difference", "B", "C", 10.0),
            make_obs("height_difference", "D", "E", 2.0),
        ]
        check = check_network(points, observations)
        self.assertIn("planar.disconnected", codes(check))
        self.assertIn("planar.datum_missing", codes(check))
        self.assertIn("elevation.disconnected", codes(check))
        self.assertIn("elevation.datum_missing", codes(check))
        broken = next(i for i in check["issues"] if i["code"] == "planar.disconnected")
        self.assertEqual(broken["groups"], [["A", "B", "C"], ["D", "E"]])
        self.assertIn("B–D", broken["suggestion"])

    def test_single_known_point_needs_orientation(self):
        points = [
            make_point("A", 0, 0, 100, True, True, True),
            make_point("B", 100, 0, 105),
        ]
        observations = [
            make_obs("distance", "A", "B", 100.0),
            make_obs("height_difference", "A", "B", 5.0),
        ]
        check = check_network(points, observations)
        self.assertFalse(check["solvable"])
        issue = next(i for i in check["planar"]["issues"] if i["code"] == "planar.orientation_missing")
        self.assertIn("定向", issue["message"])

    def test_single_observation_point_is_underdetermined(self):
        points = [
            make_point("A", 0, 0, 100, True, True, True),
            make_point("B", 100, 0, 100, True, True, True),
            make_point("C", 50, 50, 110),
        ]
        observations = [
            make_obs("distance", "A", "C", 70.71),
            make_obs("height_difference", "A", "C", 10.0),
        ]
        check = check_network(points, observations)
        codes_flat = codes(check)
        self.assertIn("planar.underdetermined", codes_flat)
        self.assertNotIn("planar.isolated", codes_flat)
        issue = next(i for i in check["issues"] if i["code"] == "planar.underdetermined")
        self.assertEqual(issue["points"], ["C"])

    def test_elevation_datum_missing_without_planar_gap(self):
        points = [
            make_point("C", 0, 0, None, True, True),
            make_point("D", 10, 0, None, True, True),
        ]
        check = check_network(points, [make_obs("height_difference", "C", "D", 2.0)])
        self.assertFalse(check["solvable"])
        issue = next(i for i in check["elevation"]["issues"] if i["code"] == "elevation.datum_missing")
        self.assertEqual(issue["points"], ["C", "D"])
        self.assertEqual(check["planar"]["issues"], [])

    def test_angle_observation_links_station_to_both_targets(self):
        points = [
            make_point("A", 0, 0, 100, True, True, True),
            make_point("B", 100, 0, 100, True, True, True),
            make_point("C", 50, 50, 100, True, True, True),
        ]
        check = check_network(points, [make_obs("angle", "A", "B", 45.0, "C")])
        self.assertEqual(check["planar"]["components"][0]["points"], ["A", "B", "C"])
        self.assertNotIn("planar.isolated", codes(check))

    def test_known_only_network_is_solvable_with_notes(self):
        points = [
            make_point("A", 0, 0, 100, True, True, True),
            make_point("B", 100, 0, 100, True, True, True),
        ]
        check = check_network(points, [])
        self.assertTrue(check["solvable"])
        self.assertTrue(check["planar"]["notes"])

    def test_empty_epoch_is_not_solvable(self):
        check = check_network([], [])
        self.assertFalse(check["solvable"])
        self.assertEqual(codes(check), ["network.empty"])


class NetworkCheckFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_demo_epoch_passes_check(self):
        epoch = seed_demo(self.db)
        check = self.db.network_check(epoch)
        self.assertTrue(check["solvable"])
        self.assertEqual(check["issues"], [])

    def test_submit_blocked_until_shape_fixed(self):
        epoch = self.db.create_epoch("网形测试", "alice")
        self.db.set_point(epoch, "alice", "A", 0, 0, 100, True, True, True)
        self.db.set_point(epoch, "alice", "B", 100, 0, 105)
        self.db.add_observation(epoch, "alice", "distance", "A", "B", 100.0)
        self.db.add_observation(epoch, "alice", "height_difference", "A", "B", 5.0)
        self.assertFalse(self.db.network_check(epoch)["solvable"])
        with self.assertRaisesRegex(DomainError, "网形检查未通过") as cm:
            self.db.transition(epoch, "alice", "editor", "submit")
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.db.get_epoch(epoch)["status"], "draft")

        # 补齐约束（B 联测为已知点）后提交放行。
        self.db.set_point(epoch, "alice", "B", 100, 0, 105, True, True, True)
        self.assertTrue(self.db.network_check(epoch)["solvable"])
        self.assertEqual(self.db.transition(epoch, "alice", "editor", "submit")["status"], "review")

    def test_deleting_observation_reopens_gap(self):
        epoch = self.db.create_epoch("删观测测试", "alice")
        self.db.set_point(epoch, "alice", "A", 0, 0, 100, True, True, True)
        self.db.set_point(epoch, "alice", "B", 100, 0, 100, True, True, True)
        self.db.set_point(epoch, "alice", "C", 50, 50, 110)
        first = self.db.add_observation(epoch, "alice", "distance", "A", "C", 70.710678)["id"]
        second = self.db.add_observation(epoch, "alice", "distance", "B", "C", 70.710678)["id"]
        self.db.add_observation(epoch, "alice", "height_difference", "A", "C", 10.0)
        self.assertTrue(self.db.network_check(epoch)["solvable"])

        self.db.delete_observation(epoch, second, "alice", "editor")
        check = self.db.network_check(epoch)
        self.assertFalse(check["solvable"])
        self.assertIn("planar.underdetermined", codes(check))

        with self.assertRaisesRegex(DomainError, "网形检查未通过"):
            self.db.transition(epoch, "alice", "editor", "submit")

        with self.assertRaises(DomainError) as cm:
            self.db.delete_observation(epoch, 999, "alice", "editor")
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(DomainError) as cm:
            self.db.delete_observation(epoch, first, "bob", "viewer")
        self.assertEqual(cm.exception.status, 403)

        # 恢复后提交，复核中不允许再删观测。
        self.db.add_observation(epoch, "alice", "distance", "B", "C", 70.710679)
        self.db.transition(epoch, "alice", "editor", "submit")
        with self.assertRaises(DomainError) as cm:
            self.db.delete_observation(epoch, first, "alice", "editor")
        self.assertEqual(cm.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
