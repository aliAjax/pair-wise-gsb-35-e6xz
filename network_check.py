"""网形可解性检查规则（纯函数模块）。

本模块只依赖 Python 标准库，不访问数据库、不处理 HTTP，便于单独测试和复用。
检查结论完全由点数据和有效观测推导，可随时重算。

两套独立的网：

* 平面网：``distance`` 作为 p1-p2 的无向边；``angle`` 在测站 p1 上产生
  p1-p2、p1-p3 两条连接（角度把三个点绑在同一平面组内）。
* 高程网：``height_difference`` 作为 p1-p2 的无向边，单独成网，
  与平面网互不影响。

对每个连通组检查：

* 孤立点（没有任何同类观测连接的点）；
* 与其他组断开的连通组，以及组内是否自带基准；
* 已知约束是否齐全：平面自由网有 4 个基准亏缺（x/y 平移、旋转、尺度），
  标准布网要求组内至少有 2 个近似坐标不重合、且 x/y 均已知的控制点；
  高程网每组至少需要 1 个已知高程点；
* 用近似坐标构建设计矩阵做数值秩检验，定位基准之外的内部秩亏
  （例如只由一条边挂在网上的悬支点）。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

PLANAR_KINDS = {"distance", "angle"}
_RANK_REL_TOL = 1e-10
_RANK_ABS_TOL = 1e-12


def _is_true(value: Any) -> bool:
    return value in (1, True, "1", "1.0")


def _components(nodes: list[str], edges: set[frozenset[str]]) -> tuple[list[list[str]], dict[str, set[str]]]:
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for edge in edges:
        a, b = tuple(edge)
        if a in adjacency and b in adjacency:
            adjacency[a].add(b)
            adjacency[b].add(a)
    seen: set[str] = set()
    groups: list[list[str]] = []
    for root in nodes:
        if root in seen:
            continue
        stack = [root]
        seen.add(root)
        members: list[str] = []
        while stack:
            current = stack.pop()
            members.append(current)
            for nxt in adjacency[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        groups.append(sorted(members))
    return groups, adjacency


def _fmt_group(points: Iterable[str]) -> str:
    return "[" + ", ".join(points) + "]"


def _eigenvalues_sym(matrix: list[list[float]]) -> list[float]:
    """循环 Jacobi 法求对称矩阵特征值（矩阵规模很小：最多 2×点数）。"""
    n = len(matrix)
    if n == 0:
        return []
    a = [row[:] for row in matrix]
    for _ in range(60):
        off = math.sqrt(sum(a[p][q] ** 2 for p in range(n) for q in range(p + 1, n)))
        if off <= 1e-14:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                apq = a[p][q]
                if abs(apq) <= 1e-15:
                    continue
                tau = (a[q][q] - a[p][p]) / (2 * apq)
                t = (1 if tau >= 0 else -1) / (abs(tau) + math.sqrt(1 + tau * tau))
                c = 1 / math.sqrt(1 + t * t)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
    return [a[i][i] for i in range(n)]


def _matrix_rank(rows: list[dict[tuple[str, str], float]], columns: list[tuple[str, str]]) -> int:
    """通过 AᵀA 的特征值判数值秩，行缩放不影响秩判断。"""
    n = len(columns)
    if n == 0:
        return 0
    index = {column: i for i, column in enumerate(columns)}
    gram = [[0.0] * n for _ in range(n)]
    for row in rows:
        entries = [(index[key], value) for key, value in row.items() if key in index]
        for i, vi in entries:
            for j, vj in entries:
                gram[i][j] += vi * vj
    values = [max(0.0, v) for v in _eigenvalues_sym(gram)]
    largest = max(values, default=0.0)
    threshold = max(_RANK_ABS_TOL, largest * _RANK_REL_TOL)
    return sum(1 for value in values if value > threshold)


def _planar_derivatives(obs: Mapping[str, Any], coords: dict[str, dict[str, float]]) -> dict[tuple[str, str], float]:
    """与平差主流程一致的平面观测一阶导数（角度按弧度）。"""
    kind = obs["kind"]
    p1, p2 = obs["p1"], obs["p2"]
    row: dict[tuple[str, str], float] = {}
    if kind == "distance":
        dx = coords[p2]["x"] - coords[p1]["x"]
        dy = coords[p2]["y"] - coords[p1]["y"]
        dist = math.hypot(dx, dy)
        if dist <= 1e-12:
            raise ZeroDivisionError(f"{p1}、{p2} 近似坐标重合")
        row[(p1, "x")] = -dx / dist
        row[(p1, "y")] = -dy / dist
        row[(p2, "x")] = dx / dist
        row[(p2, "y")] = dy / dist
        return row
    p3 = obs["p3"]
    dx1 = coords[p2]["x"] - coords[p1]["x"]
    dy1 = coords[p2]["y"] - coords[p1]["y"]
    dx2 = coords[p3]["x"] - coords[p1]["x"]
    dy2 = coords[p3]["y"] - coords[p1]["y"]
    r1, r2 = math.hypot(dx1, dy1), math.hypot(dx2, dy2)
    if min(r1, r2) <= 1e-12:
        raise ZeroDivisionError(f"{p1} 测站的照准点近似坐标重合")
    row[(p1, "x")] = -dy1 / r1**2 + dy2 / r2**2
    row[(p1, "y")] = dx1 / r1**2 - dx2 / r2**2
    row[(p2, "x")] = dy1 / r1**2
    row[(p2, "y")] = -dx1 / r1**2
    row[(p3, "x")] = -dy2 / r2**2
    row[(p3, "y")] = dx2 / r2**2
    return row


def _issue(severity: str, network: str, code: str, message: str,
           points: list[str] | None = None, suggestion: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "severity": severity,
        "network": network,
        "code": code,
        "message": message,
        "points": points or [],
    }
    if suggestion:
        item["suggestion"] = suggestion
    return item


def _check_planar(points: list[Mapping[str, Any]], observations: list[Mapping[str, Any]]) -> dict[str, Any]:
    pmap = {p["point"]: p for p in points}
    names = sorted(pmap)
    edges: set[frozenset[str]] = set()
    for obs in observations:
        if obs["kind"] == "distance":
            edges.add(frozenset((obs["p1"], obs["p2"])))
        else:
            edges.add(frozenset((obs["p1"], obs["p2"])))
            edges.add(frozenset((obs["p1"], obs["p3"])))
    groups_raw, adjacency = _components(names, edges)

    issues: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    full_known_all = [n for n in names if _is_true(pmap[n].get("x_known")) and _is_true(pmap[n].get("y_known"))]
    multi_group = len(groups_raw) > 1

    for comp in groups_raw:
        compset = set(comp)
        full = [n for n in comp if _is_true(pmap[n].get("x_known")) and _is_true(pmap[n].get("y_known"))]
        x_known = [n for n in comp if _is_true(pmap[n].get("x_known"))]
        y_known = [n for n in comp if _is_true(pmap[n].get("y_known"))]
        comp_edges = [e for e in edges if e <= compset]
        comp_obs = [
            o for o in observations
            if {o["p1"], o["p2"]} <= compset and (o["kind"] != "angle" or o["p3"] in compset)
        ]
        group: dict[str, Any] = {
            "points": comp,
            "edge_count": len(comp_edges),
            "known_points": full,
            "x_known_points": x_known,
            "y_known_points": y_known,
            "isolated": len(comp) == 1 and not adjacency[comp[0]],
            "status": "ok",
            "note": "",
        }

        if group["isolated"]:
            name = comp[0]
            if name in full:
                group["status"] = "info"
                group["note"] = f"{name} 为已知点，但没有任何边长/角度连接，独立成组"
                issues.append(_issue("info", "planar", "isolated_known",
                                     f"已知点 {name} 未参与任何平面观测，对其他点没有约束作用", [name]))
            else:
                group["status"] = "error"
                anchor = f"已知点 {full_known_all[0]}" if full_known_all else "网内其他已定点"
                issues.append(_issue(
                    "error", "planar", "isolated_point",
                    f"{name} 在平面网中是孤立点：没有边长或角度连接，x/y 坐标不可求", [name],
                    f"补测 {name} 到{anchor}的至少 2 个独立观测量（2 段边长，或 1 段边长加 1 个方向），把 {name} 接入网中",
                ))
            groups.append(group)
            continue

        # 基准（datum）检查：平面自由网亏缺 = x/y 平移、旋转、尺度。
        unknown_points = [n for n in comp if n not in full]
        datum_errors_before = len(issues)
        if not x_known:
            issues.append(_issue("error", "planar", "datum_missing_x",
                                 f"组 {_fmt_group(comp)} 没有 X 坐标已知点，缺 X 方向平移基准", comp))
        if not y_known:
            issues.append(_issue("error", "planar", "datum_missing_y",
                                 f"组 {_fmt_group(comp)} 没有 Y 坐标已知点，缺 Y 方向平移基准", comp))
        if len(full) == 0 and (x_known or y_known):
            only_x = sorted(set(x_known) - set(y_known))
            only_y = sorted(set(y_known) - set(x_known))
            detail = f"（仅 X 已知: {only_x or '无'}；仅 Y 已知: {only_y or '无'}）"
            issues.append(_issue(
                "error", "planar", "datum_partial",
                f"组 {_fmt_group(comp)} 只有单坐标已知点{detail}，无法固定整网旋转和尺度", comp,
                f"再补 1 个同时已知 X、Y 且与现有点不重合的控制点（如 {comp[0]} 附近）",
            ))
        if len(full) == 1 and x_known and y_known and len(comp) > 1:
            anchor = full[0]
            target = next((n for n in comp if n != anchor), anchor)
            issues.append(_issue(
                "error", "planar", "datum_one_control",
                f"组 {_fmt_group(comp)} 只有 {anchor} 一个平面已知点：位置可固定，整网仍可绕 {anchor} 旋转、缩放",
                comp,
                f"补 1 个与 {anchor} 不重合的已知点；或补测 {anchor}→{target} 的已知方位角固定方向，并增加 1 段已知边长固定尺度",
            ))
        if len(full) >= 2:
            coords_ok = all(pmap[n].get("x") is not None and pmap[n].get("y") is not None for n in full)
            if coords_ok:
                for i, a in enumerate(full):
                    for b in full[i + 1:]:
                        gap = math.hypot(float(pmap[a]["x"]) - float(pmap[b]["x"]),
                                         float(pmap[a]["y"]) - float(pmap[b]["y"]))
                        if gap <= 1e-9:
                            issues.append(_issue(
                                "error", "planar", "datum_coincident",
                                f"已知点 {a}、{b} 近似坐标重合，不能固定整网方向和尺度", [a, b],
                                f"核对 {a}、{b} 坐标，或改用另一个不重合的已知点",
                            ))

        # 多组断开时的连接提示（只针对自身基准不足的组；自带 2 个已知点的独立网可单独求解）。
        datum_complete = len(full) >= 2
        if multi_group and not datum_complete and unknown_points:
            anchor_group = next(
                (g for g in groups_raw if g is not comp and any(n in full_known_all for n in g)), None
            )
            if anchor_group:
                here, there = sorted(compset)[0], sorted(set(anchor_group))[0]
                issues.append(_issue(
                    "error", "planar", "group_disconnected",
                    f"平面组 {_fmt_group(comp)} 与含已知点的组 {_fmt_group(anchor_group)} 断开，观测无法传递坐标",
                    comp,
                    f"在 {there}-{here} 之间补测 1 段边长加 1 个方向（或 2 段边长），把两个组连成一体",
                ))
            else:
                issues.append(_issue(
                    "error", "planar", "group_no_datum",
                    f"平面组 {_fmt_group(comp)} 与其他组断开，且组内没有足够的已知控制点", comp,
                    f"在组内补 2 个不重合的已知点，或补测边长/方向连接到其他组（共 {len(groups_raw)} 个断开的平面组）",
                ))

        group["status"] = "error" if len(issues) > datum_errors_before else group["status"]

        # 数值秩检验：定位基准齐全后仍然存在的内部秩亏。
        missing_known = [
            n for n in comp
            if ((_is_true(pmap[n].get("x_known")) and pmap[n].get("x") is None)
                or (_is_true(pmap[n].get("y_known")) and pmap[n].get("y") is None))
        ]
        for name in missing_known:
            issues.append(_issue("error", "planar", "known_value_missing",
                                 f"{name} 标记为已知控制点，但平面坐标没有填写", [name],
                                 f"补全 {name} 的已知 X、Y 坐标，或取消其已知标记"))
        missing_approx = [
            n for n in unknown_points
            if pmap[n].get("x") is None or pmap[n].get("y") is None
        ]
        for name in missing_approx:
            issues.append(_issue("error", "planar", "approx_coordinate_missing",
                                 f"{name} 缺少近似平面坐标，无法构网计算方向，点位暂不可解", [name],
                                 f"在点数据中补录 {name} 的近似 X、Y 坐标"))
        group_errors = [i for i in issues[datum_errors_before:] if i["severity"] == "error"]
        group["status"] = "error" if group_errors else group["status"]
        if unknown_points and not missing_known and not missing_approx:
            coords = {n: {"x": float(pmap[n]["x"]), "y": float(pmap[n]["y"])} for n in comp}
            try:
                rows = [_planar_derivatives(o, coords) for o in comp_obs]
            except ZeroDivisionError as exc:
                issues.append(_issue("error", "planar", "coincident_points", str(exc), comp,
                                     "核对相关点的近似坐标，重合点无法构成边长或方向"))
            else:
                anchored_cols = [(n, c) for n in comp for c in ("x", "y")
                                 if not _is_true(pmap[n].get(f"{c}_known"))]
                rank = _matrix_rank(rows, anchored_cols)
                defect = len(anchored_cols) - rank
                free_cols = [(n, c) for n in comp for c in ("x", "y")]
                free_rank = _matrix_rank(rows, free_cols)
                has_distance = any(o["kind"] == "distance" for o in comp_obs)
                baseline = 4 if has_distance else 5  # 纯测角网多一个尺度亏缺
                internal = max(0, 2 * len(comp) - free_rank - baseline)
                if datum_complete and (defect > 0 or internal > 0):
                    already_explained = any(
                        issue.get("code") == "datum_coincident" and set(issue["points"]) <= compset
                        for issue in issues
                    )
                    leaves = [n for n in unknown_points if len(adjacency[n]) == 1]
                    if leaves:
                        for name in leaves:
                            anchor = full[0] if full else next(iter(adjacency[name]))
                            issues.append(_issue(
                                "error", "planar", "hanging_point",
                                f"{name} 只由 1 条边挂在网上，可绕连接点转动，位置缺 1 个方向约束",
                                [name],
                                f"补测 {name} 到已定点（如 {anchor}）的 1 段边长或 1 个方向观测",
                            ))
                    elif not already_explained:
                        issues.append(_issue(
                            "error", "planar", "rank_deficient",
                            f"组 {_fmt_group(comp)} 设计矩阵秩亏 {defect or internal} 阶，现有观测不足以确定全部点位",
                            comp,
                            f"在组内补测 {defect or internal} 个独立边长或方向观测（优先在与已有观测方向不平行的方向布测）",
                        ))
                    if leaves or not already_explained:
                        group["status"] = "error"
                elif not datum_complete and internal > 0:
                    issues.append(_issue(
                        "error", "planar", "internal_weak",
                        f"组 {_fmt_group(comp)} 除基准缺失外，内部还存在 {internal} 阶自由度，部分点仅靠单方向悬挂",
                        comp,
                        "补齐已知基准的同时，为悬挂点补测边长或方向观测",
                    ))
                    group["status"] = "error"
        if group["status"] == "ok":
            group["note"] = "连通且控制点约束齐全"
        groups.append(group)

    active = bool(observations)
    return {"active": active, "groups": groups, "issues": issues}


def _check_height(points: list[Mapping[str, Any]], observations: list[Mapping[str, Any]]) -> dict[str, Any]:
    pmap = {p["point"]: p for p in points}
    names = sorted(pmap)
    edges = {frozenset((o["p1"], o["p2"])) for o in observations}
    groups_raw, adjacency = _components(names, edges)

    issues: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    known_all = [n for n in names if _is_true(pmap[n].get("elevation_known"))]

    for comp in groups_raw:
        known = [n for n in comp if _is_true(pmap[n].get("elevation_known"))]
        unknown_value = [
            n for n in known if pmap[n].get("elevation") is None
        ]
        for name in unknown_value:
            issues.append(_issue("error", "height", "known_value_missing",
                                 f"{name} 标记为已知高程点，但没有填写高程值", [name],
                                 f"补全 {name} 的已知高程，或取消其已知高程标记"))
        group: dict[str, Any] = {
            "points": comp,
            "edge_count": sum(1 for e in edges if e <= set(comp)),
            "known_points": known,
            "isolated": len(comp) == 1 and not adjacency[comp[0]],
            "status": "ok",
            "note": "",
        }
        if group["isolated"]:
            name = comp[0]
            if known:
                group["status"] = "info"
                group["note"] = f"{name} 高程已知，但没有高差连接，独立成组"
                issues.append(_issue("info", "height", "isolated_known",
                                     f"已知高程点 {name} 未通过高差连接到任何点，不参与其他点的高程传递", [name]))
            else:
                group["status"] = "error"
                anchor = f"已知高程点 {known_all[0]}" if known_all else "本组其他点"
                issues.append(_issue(
                    "error", "height", "isolated_point",
                    f"{name} 在高程网中是孤立点：既无高差连接也没有已知高程，高程不可求", [name],
                    f"加测 {name} 到{anchor}的高差，或直接给定 {name} 的已知高程",
                ))
        elif not known:
            group["status"] = "error"
            issues.append(_issue(
                "error", "height", "datum_missing",
                f"高差组 {_fmt_group(comp)} 没有已知高程点，整组高程可整体平移，缺 1 个高程起算", comp,
                f"指定组内 1 个点（如 {comp[0]}）的已知高程，或加测高差把本组连到已知高程点",
            ))
        else:
            group["note"] = "高差连通且有高程起算点"
        groups.append(group)

    return {"active": bool(observations), "groups": groups, "issues": issues}


def check_network(points: list[Mapping[str, Any]], observations: list[Mapping[str, Any]]) -> dict[str, Any]:
    """检查整期网形，返回结构化结论。

    返回字段：

    * ``solvable``：整体是否可解（不存在 severity=error 的问题）；
    * ``planar`` / ``height``：各自的连通组 ``groups`` 和问题 ``issues``；
    * ``issues``：全部问题的平铺列表（error/warning/info）；
    * ``suggestions``：去重后的可执行补测/补基准建议。
    """
    valid_obs = [o for o in observations if o.get("status", "valid") == "valid"]
    planar_obs = [o for o in valid_obs if o["kind"] in PLANAR_KINDS]
    height_obs = [o for o in valid_obs if o["kind"] == "height_difference"]

    issues: list[dict[str, Any]] = []
    if not points:
        issues.append(_issue("error", "network", "empty_epoch",
                             "期次中还没有任何控制点或待求点", suggestion="先录入控制点和待求点"))

    planar = _check_planar(points, planar_obs)
    height = _check_height(points, height_obs)
    issues.extend(planar["issues"])
    issues.extend(height["issues"])

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (order.get(item["severity"], 3), item["network"], item["code"]))
    suggestions: list[str] = []
    for item in issues:
        text = item.get("suggestion")
        if text and text not in suggestions:
            suggestions.append(text)

    return {
        "solvable": not any(item["severity"] == "error" for item in issues),
        "planar": planar,
        "height": height,
        "issues": issues,
        "suggestions": suggestions,
        "point_count": len(points),
        "observation_count": len(valid_obs),
    }
