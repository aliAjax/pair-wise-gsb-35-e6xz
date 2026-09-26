"""网形检查规则：在平差前定位观测网的秩亏缺口。

边长和角度把点连成平面网，高程差单独连成高程网。检查孤立点、断开的
连通组、观测不足的点以及缺失的已知约束，并说明还缺哪段连接或哪个
方向。本模块只包含纯函数规则：接口在 app.py，页面在 static/index.html。
"""
from __future__ import annotations

import math
from typing import Any

# 平面网由边长和角度构成，高程网只由高程差构成。
PLANAR_KINDS = {"distance", "angle"}
ELEVATION_KINDS = {"height_difference"}

_ISSUE_ORDER = {
    "network.empty": 0,
    "planar.disconnected": 10,
    "planar.isolated": 11,
    "planar.underdetermined": 12,
    "planar.datum_missing": 13,
    "planar.orientation_missing": 14,
    "elevation.disconnected": 20,
    "elevation.isolated": 21,
    "elevation.datum_missing": 22,
}


class _UnionFind:
    """按点号合并连通分量，根固定取最小点号以保证输出确定。"""

    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        node = item
        while self.parent[node] != root:
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra


def _join(names) -> str:
    return "、".join(names)


def _planar_edges(observations) -> list[tuple[str, str]]:
    edges = []
    for obs in observations:
        kind = obs.get("kind")
        if kind == "distance":
            edges.append((obs.get("p1"), obs.get("p2")))
        elif kind == "angle" and obs.get("p3"):
            # 角度在测站点 p1 上连接两个方向，相当于 p1-p2、p1-p3 两条边。
            edges.append((obs.get("p1"), obs.get("p2")))
            edges.append((obs.get("p1"), obs.get("p3")))
    return edges


def _elevation_edges(observations) -> list[tuple[str, str]]:
    return [(o.get("p1"), o.get("p2")) for o in observations if o.get("kind") == "height_difference"]


def _components(names: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    uf = _UnionFind(names)
    for a, b in edges:
        if a in uf.parent and b in uf.parent:
            uf.union(a, b)
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(uf.find(name), []).append(name)
    return sorted(groups.values(), key=lambda members: members[0])


def _nearest_pair(group_a: list[str], group_b: list[str], by_name: dict[str, dict[str, Any]]):
    """挑两组之间最近的一对点；坐标缺失时退化为点号最小的一对。"""
    best = None
    for a in group_a:
        for b in group_b:
            pa, pb = by_name[a], by_name[b]
            coords = (pa.get("x"), pa.get("y"), pb.get("x"), pb.get("y"))
            dist = math.hypot(coords[2] - coords[0], coords[3] - coords[1]) if None not in coords else None
            key = (dist if dist is not None else math.inf, a, b)
            if best is None or key < best[0]:
                best = (key, a, b, dist)
    return best[1], best[2], best[3]


def _fmt_dist(dist) -> str:
    return f"约 {dist:.1f} m" if dist is not None else "距离待定"


def _analyze_planar(names: list[str], by_name: dict[str, dict[str, Any]], observations) -> dict[str, Any]:
    edges = _planar_edges(observations)
    comps = _components(names, edges)
    comp_of = {name: i for i, members in enumerate(comps) for name in members}
    edge_count = [0] * len(comps)
    has_distance = [False] * len(comps)
    incidence = {name: 0 for name in names}
    for a, _b in edges:
        edge_count[comp_of[a]] += 1
    for obs in observations:
        kind = obs.get("kind")
        if kind not in PLANAR_KINDS:
            continue
        if kind == "distance" and obs.get("p1") in comp_of:
            has_distance[comp_of[obs["p1"]]] = True
        for p in (obs.get("p1"), obs.get("p2"), obs.get("p3")):
            if p in incidence:
                incidence[p] += 1

    components: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    notes: list[str] = []
    for i, members in enumerate(comps):
        known = [m for m in members if by_name[m].get("x_known") and by_name[m].get("y_known")]
        known_set = set(known)
        unknown = [m for m in members if m not in known_set]
        # 已知点坐标完全重合时只相当于一个起算点，定向仍然缺失。
        sites = {(round(by_name[m].get("x") or 0.0, 9), round(by_name[m].get("y") or 0.0, 9)) for m in known}
        isolated = edge_count[i] == 0
        datum_ok = not unknown or len(sites) >= 2
        components.append({
            "points": members,
            "known_points": known,
            "unknown_points": unknown,
            "edge_count": edge_count[i],
            "has_distance": has_distance[i],
            "isolated": isolated,
            "datum_ok": datum_ok,
        })
        if isolated:
            point = members[0]
            if unknown:
                others = [n for n in names if n != point]
                if others:
                    _, peer, dist = _nearest_pair([point], others, by_name)
                    tip = f"补测 {point}–{peer} 的边长（{_fmt_dist(dist)}），或在 {peer} 设站联测 {point} 的方向"
                else:
                    tip = "至少再联测一个已知点"
                issues.append({
                    "code": "planar.isolated",
                    "points": [point],
                    "message": f"点 {point} 没有任何边长或角度连接，平面坐标不可解",
                    "suggestion": tip,
                })
            else:
                notes.append(f"已知点 {_join(members)} 未参与平面观测，不影响解算")
            continue
        for m in unknown:
            dof = (not by_name[m].get("x_known")) + (not by_name[m].get("y_known"))
            if dof == 2 and incidence[m] == 1:
                _, peer, dist = _nearest_pair([m], [x for x in members if x != m], by_name)
                issues.append({
                    "code": "planar.underdetermined",
                    "points": [m],
                    "message": f"点 {m} 只有 1 条平面观测，不足以解算平面坐标（至少两条独立边长或方向）",
                    "suggestion": f"补测 {m}–{peer} 的边长（{_fmt_dist(dist)}），或在 {peer} 设站联测 {m} 的方向",
                })
        if not unknown:
            continue
        if not sites:
            issues.append({
                "code": "planar.datum_missing",
                "points": members,
                "message": f"组 {_join(members)} 没有已知平面点，缺少位置基准",
                "suggestion": "联测至少两个已知点，或一个已知点加一个已知方位角",
            })
        elif len(sites) == 1:
            if len(known) > 1:
                msg = f"组 {_join(members)} 的已知点平面坐标相同，定向约束失效"
            else:
                msg = f"组 {_join(members)} 只有 {known[0]} 一个已知点，缺少定向约束"
            tip = "再联测一个已知点，或测定一个已知方位角"
            if not has_distance[i]:
                tip += "；组内还没有边长观测，比例尺也不确定，需补测一段边长"
            issues.append({"code": "planar.orientation_missing", "points": members, "message": msg, "suggestion": tip})

    linked = [c for c in components if c["unknown_points"] and not c["isolated"]]
    if len(linked) > 1:
        main = next((c for c in linked if c["datum_ok"]), max(linked, key=lambda c: len(c["points"])))
        tips = []
        for c in linked:
            if c is main:
                continue
            a, b, dist = _nearest_pair(main["points"], c["points"], by_name)
            tips.append(f"在 {a}–{b} 之间补测边长（{_fmt_dist(dist)}）或联测两组间的角度")
        issues.append({
            "code": "planar.disconnected",
            "groups": [c["points"] for c in linked],
            "message": f"平面网断开成 {len(linked)} 组（{'；'.join(_join(c['points']) for c in linked)}），组间缺少边长或角度连接",
            "suggestion": "；".join(tips),
        })
    issues.sort(key=lambda i: (_ISSUE_ORDER[i["code"]], i["message"]))
    return {"ok": not issues, "edge_count": sum(edge_count), "components": components, "issues": issues, "notes": notes}


def _analyze_elevation(names: list[str], by_name: dict[str, dict[str, Any]], observations) -> dict[str, Any]:
    edges = _elevation_edges(observations)
    comps = _components(names, edges)
    comp_of = {name: i for i, members in enumerate(comps) for name in members}
    edge_count = [0] * len(comps)
    for a, _b in edges:
        edge_count[comp_of[a]] += 1

    components: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    notes: list[str] = []
    for i, members in enumerate(comps):
        known = [m for m in members if by_name[m].get("elevation_known")]
        known_set = set(known)
        unknown = [m for m in members if m not in known_set]
        isolated = edge_count[i] == 0
        datum_ok = not unknown or bool(known)
        components.append({
            "points": members,
            "known_points": known,
            "unknown_points": unknown,
            "edge_count": edge_count[i],
            "isolated": isolated,
            "datum_ok": datum_ok,
        })
        if isolated:
            point = members[0]
            if unknown:
                others = [n for n in names if n != point]
                if others:
                    _, peer, dist = _nearest_pair([point], others, by_name)
                    tip = f"补测 {point}–{peer} 的高程差（路线{_fmt_dist(dist)}），把 {point} 接入水准网"
                else:
                    tip = "至少再联测一个已知高程点"
                issues.append({
                    "code": "elevation.isolated",
                    "points": [point],
                    "message": f"点 {point} 没有任何高程差观测，高程不可解",
                    "suggestion": tip,
                })
            else:
                notes.append(f"已知点 {_join(members)} 未参与高程观测，不影响解算")
            continue
        if unknown and not known:
            issues.append({
                "code": "elevation.datum_missing",
                "points": members,
                "message": f"组 {_join(members)} 缺少已知高程点，高程基准缺失",
                "suggestion": "至少联测一个已知水准点（已知高程点）",
            })

    linked = [c for c in components if c["unknown_points"] and not c["isolated"]]
    if len(linked) > 1:
        main = next((c for c in linked if c["datum_ok"]), max(linked, key=lambda c: len(c["points"])))
        tips = []
        for c in linked:
            if c is main:
                continue
            a, b, dist = _nearest_pair(main["points"], c["points"], by_name)
            tips.append(f"在 {a}–{b} 之间补测高程差（路线{_fmt_dist(dist)}）")
        issues.append({
            "code": "elevation.disconnected",
            "groups": [c["points"] for c in linked],
            "message": f"高程网断开成 {len(linked)} 组（{'；'.join(_join(c['points']) for c in linked)}），组间缺少高程差观测",
            "suggestion": "；".join(tips),
        })
    issues.sort(key=lambda i: (_ISSUE_ORDER[i["code"]], i["message"]))
    return {"ok": not issues, "edge_count": sum(edge_count), "components": components, "issues": issues, "notes": notes}


def check_network(points: list[dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """检查一期观测网是否可解，返回连通组、缺口和补测建议。

    points 为点表记录（point/x/y/elevation/x_known/y_known/elevation_known），
    observations 为参与平差的有效观测（kind/p1/p2/p3/value/weight）。
    """
    by_name = {p["point"]: p for p in points}
    names = sorted(by_name)
    planar = _analyze_planar(names, by_name, observations)
    elevation = _analyze_elevation(names, by_name, observations)
    issues: list[dict[str, Any]] = []
    if not names:
        issues.append({
            "code": "network.empty",
            "message": "期次内还没有任何点，无法组成观测网",
            "suggestion": "先录入已知点和待求点，再录入观测",
        })
    issues.extend(planar["issues"])
    issues.extend(elevation["issues"])
    issues.sort(key=lambda i: (_ISSUE_ORDER.get(i["code"], 99), i["message"]))
    summary = [i["message"] for i in issues] if issues else ["网形完整：平面网与高程网各自连通，已知约束充足"]
    return {
        "solvable": not issues,
        "point_count": len(names),
        "observation_count": len(observations),
        "planar": planar,
        "elevation": elevation,
        "issues": issues,
        "summary": summary,
    }
