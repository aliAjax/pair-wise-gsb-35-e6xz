import math
import tempfile
import unittest
from pathlib import Path

from app import Database, DomainError, seed_demo
from network_check import check_network


def build(db: Database, points, observations):
    """points: (name,x,y,h,xk,yk,hk); observations: (kind,p1,p2,p3,value)"""
    epoch = db.create_epoch("网形检查-" + next(build._n), "alice")
    for name, x, y, h, xk, yk, hk in points:
        db.set_point(epoch, "alice", name, x, y, h, xk, yk, hk)
    for kind, p1, p2, p3, value in observations:
        db.add_observation(epoch, "alice", kind, p1, p2, value, 1.0, p3, allow_duplicate=True)
    return epoch


build._n = iter([f"t{i}" for i in range(100)])


class NetworkCheckRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "check.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _report(self, epoch):
        return check_network(self.db.list_points(epoch), self.db.list_observations(epoch))

    def _codes(self, report, network=None, severity="error"):
        return {i["code"] for i in report["issues"]
                if i["severity"] == severity and (network is None or i["network"] == network)}

    def test_seed_demo_is_solvable(self):
        epoch = seed_demo(self.db)
        report = self._report(epoch)
        self.assertTrue(report["solvable"], report["issues"])

    def test_isolated_point_is_flagged(self):
        epoch = build(
            self.db,
            [("A", 0, 0, 0, True, True, True), ("B", 100, 0, 5, True, True, True), ("C", 50, 50, 3, False, False, False)],
            [("distance", "A", "B", None, 100.0)],
        )
        report = self._report(epoch)
        self.assertFalse(report["solvable"])
        self.assertIn("isolated_point", self._codes(report, "planar"))
        self.assertIn("isolated_point", self._codes(report, "height"))
        planar_groups = report["planar"]["groups"]
        self.assertTrue(any(g["isolated"] and g["status"] == "error" for g in planar_groups))

    def test_missing_planar_datum(self):
        epoch = build(
            self.db,
            # A/B 平面均已知，但所有点高程都未知 -> 高差网整体可平移
            [("A", 0, 0, 0, True, True, False), ("B", 100, 0, 5, True, True, False), ("C", 50, 50, 3, False, False, False)],
            [
                ("distance", "A", "B", None, 100.0),
                ("distance", "B", "C", None, math.hypot(50, 50)),
                ("distance", "A", "C", None, math.hypot(50, 50)),
                ("height_difference", "A", "B", None, 5.0),
                ("height_difference", "B", "C", None, -2.0),
            ],
        )
        report = self._report(epoch)
        self.assertFalse(report["solvable"])
        # 平面两个已知点齐全 -> 平面基准无缺失，可解
        self.assertNotIn("datum_one_control", self._codes(report, "planar"))
        # 高差组没有已知高程
        self.assertIn("datum_missing", self._codes(report, "height"))

    def test_disconnected_groups_with_direction_advice(self):
        epoch = build(
            self.db,
            [
                ("A", 0, 0, 0, True, True, True),
                ("B", 100, 0, 5, True, True, True),
                ("C", 50, 50, 3, False, False, False),
                ("D", 60, 60, 4, False, False, False),
            ],
            [
                ("distance", "A", "B", None, 100.0),
                ("distance", "C", "D", None, math.hypot(10, 10)),
                ("height_difference", "C", "D", None, 1.0),
            ],
        )
        report = self._report(epoch)
        self.assertFalse(report["solvable"])
        self.assertIn("group_disconnected", self._codes(report, "planar"))
        self.assertEqual(len(report["planar"]["groups"]), 2)
        advice = " ".join(report["suggestions"])
        self.assertIn("补测", advice)
        self.assertIn("方向", advice)

    def test_hanging_point_by_single_edge(self):
        epoch = build(
            self.db,
            [("A", 0, 0, 0, True, True, True), ("B", 100, 0, 5, True, True, True), ("C", 50, 50, 3, False, False, False)],
            [
                ("distance", "A", "B", None, 100.0),
                ("distance", "B", "C", None, math.hypot(50, 50)),
                ("height_difference", "A", "B", None, 5.0),
                ("height_difference", "B", "C", None, -2.0),
            ],
        )
        report = self._report(epoch)
        self.assertFalse(report["solvable"])
        self.assertIn("hanging_point", self._codes(report, "planar"))
        issue = next(i for i in report["issues"] if i["code"] == "hanging_point")
        self.assertEqual(issue["points"], ["C"])

    def test_suggested_fix_resolves_network(self):
        epoch = build(
            self.db,
            [("A", 0, 0, 0, True, True, True), ("B", 100, 0, 5, True, True, True), ("C", 50, 50, 3, False, False, False)],
            [
                ("distance", "A", "B", None, 100.0),
                ("height_difference", "A", "B", None, 5.0),
            ],
        )
        before = self._report(epoch)
        self.assertFalse(before["solvable"])
        # 按建议把 C 接上网：两条独立观测量 + 一段高差
        self.db.add_observation(epoch, "alice", "distance", "B", "C", math.hypot(50, 50))
        self.db.add_observation(epoch, "alice", "distance", "A", "C", math.hypot(50, 50))
        self.db.add_observation(epoch, "alice", "height_difference", "B", "C", -2.0)
        after = self._report(epoch)
        self.assertTrue(after["solvable"], [i["message"] for i in after["issues"] if i["severity"] == "error"])


class SubmitGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "gate.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _sparse_epoch(self):
        epoch = self.db.create_epoch("缺基准期次", "alice")
        self.db.set_point(epoch, "alice", "A", 0, 0, 0, True, True, True)
        self.db.set_point(epoch, "alice", "B", 100, 0, 5)
        self.db.add_observation(epoch, "alice", "distance", "A", "B", 100.0)
        self.db.add_observation(epoch, "alice", "height_difference", "A", "B", 5.0)
        return epoch

    def test_submit_blocked_until_solvable(self):
        epoch = self._sparse_epoch()
        with self.assertRaises(DomainError) as cm:
            self.db.transition(epoch, "alice", "editor", "submit")
        self.assertEqual(cm.exception.status, 422)
        self.assertIn("网形检查", str(cm.exception))
        self.assertEqual(self.db.get_epoch(epoch)["status"], "draft")
        # 闸门同时落了快照，重开页面（新连接）仍能看到保存的不可解结论
        saved = self.db.get_saved_network_check(epoch)
        self.assertIsNotNone(saved)
        self.assertFalse(saved["solvable"])
        # GET 按当前数据实时重算，并带回最近快照时间
        state = self.db.network_check_state(epoch)
        self.assertFalse(state["solvable"])
        self.assertIn("last_saved_at", state)

        # 补上第二个已知点后：平面与高程都可解，提交放行
        self.db.set_point(epoch, "alice", "B", 100, 0, 5, True, True, False)
        self.db.transition(epoch, "alice", "editor", "submit")
        self.assertEqual(self.db.get_epoch(epoch)["status"], "review")
        saved_ok = self.db.get_saved_network_check(epoch)
        self.assertTrue(saved_ok["solvable"])

    def test_check_refreshes_after_mutation_and_seed_still_flows(self):
        epoch = seed_demo(self.db)
        report = self.db.save_network_check(epoch, "alice")
        self.assertTrue(report["solvable"])
        # 加入一个新孤立点后，结论立刻变为不可解
        self.db.set_point(epoch, "alice", "Z", 999, 999, 999)
        fresh = self.db.evaluate_network(epoch)
        self.assertFalse(fresh["solvable"])


if __name__ == "__main__":
    unittest.main()
