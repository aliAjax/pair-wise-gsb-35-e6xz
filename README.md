# 大地测量网平差

一个仅使用 Python 标准库实现的大地测量观测管理与平差服务。支持角度、边长、高程差、已知点和未知点近似坐标，使用加权最小二乘求解，并输出残差、单位权中误差和点位精度。

## 运行

需要 Python 3.11+。

```bash
python app.py --init
python app.py --port 8006
```

浏览器打开 <http://127.0.0.1:8006>。数据库默认写入 `geodetic.db`，可以通过 `--db` 或环境变量 `GEODETIC_DB` 修改。

`--init` 会初始化一个包含 A、B 已知点和 C、D 待求点的示例期次。重复执行不会重复添加。

## 主要 API

所有修改接口使用 `X-User`、`X-Role` 请求头区分身份，角色为 `editor`、`reviewer` 或 `viewer`。

- `POST /api/epochs`：创建期次。
- `POST /api/epochs/{id}/points`：设置已知点或未知点近似坐标。
- `POST /api/epochs/{id}/observations`：录入 `distance`、`angle`、`height_difference` 观测；疑似重复值会返回 409。
- `POST /api/epochs/{id}/adjust`：迭代加权最小二乘平差；残差超过 3.5 倍单位权中误差的观测标记为 `outlier`。
- `GET /api/epochs/{id}/network-check`：按当前点数据和观测**实时重算**网形检查结论。
- `POST /api/epochs/{id}/network-check`：重算并把结论快照写入数据库（重开页面仍可读取保存的结论与时间）。
- `POST /api/epochs/{id}/transition/submit|approve|reject|publish`：审核发布状态机。提交时服务端强制执行网形检查，不可解返回 422，期次停留在 `draft`。
- `GET /api/epochs/{id}/results`：查看点位坐标和精度。
- `GET /api/epochs/{id}/compare/{other_id}`：比较两期成果。
- `GET /api/epochs/{id}/audit`：查看操作审计。

## 网形检查

平差报“秩亏”时，网形检查负责指出缺口具体在哪。规则、接口、页面三者分离：

- **规则**：`network_check.py`，纯函数、只用标准库，输入点和观测，输出结构化结论；可单独单测。
- **接口**：`app.py` 的 `/network-check` 路由 + `network_checks` 快照表 + 提交闸门。
- **页面**：`static/index.html`（结构）、`static/styles.css`（样式）、`static/app.js`（交互）。

### 两张网

- 平面网：`distance` 构成 p1-p2 无向边；`angle` 在测站 p1 上产生 p1-p2、p1-p3 两条连接。
- 高程网：`height_difference` 单独构成无向网，与平面互不影响。

### 检查规则（结论中每条问题都带 code 与补测建议）

1. **孤立点**：没有任何同类观测连接。平面待求点需补至少 2 个独立观测量（2 段边长，或 1 段边长 + 1 个方向）；高程待求点需补高差或直接给定高程。
2. **断开的组**：连通分量各自成组显示。自身基准不足的组若与含已知点的组断开，报 `group_disconnected`，并给出“在两点之间补 1 段边长 + 1 个方向（或 2 段边长）”的具体建议；自带 2 个已知点的独立网可单独求解，不阻断。
3. **缺已知约束（基准）**：
   - 平面自由网有 4 个基准亏缺（X/Y 平移、旋转、尺度）。组内要求至少 2 个坐标不重合、X/Y 均已知的控制点；只有 1 个时报 `datum_one_control`（缺方向/尺度），只有单坐标已知时报 `datum_partial`，缺 X 或 Y 已知点分别报 `datum_missing_x/y`，已知点坐标重合报 `datum_coincident`。纯测角组另加尺度亏缺提示。
   - 高程网每个连通组至少需要 1 个已知高程点，否则报 `datum_missing`（整组可整体平移）。
4. **内部秩亏**：基准齐全后用近似坐标构建设计矩阵（与平差同一套一阶导数）做数值秩检验。只由 1 条边悬挂的点报 `hanging_point` 并指出缺 1 个方向约束；其余报 `rank_deficient` 及欠缺阶数。
5. **数据缺陷**：标记了已知却没填坐标/高程（`known_value_missing`）、待求点缺近似坐标（`approx_coordinate_missing`）、近似坐标重合（`coincident_points`）。

### 交互行为

- 录入/修改点或观测后，页面立即重新拉取检查结论；提交按钮在 `solvable=false` 时禁用。
- 服务端提交闸门以重算结论为准，绕过页面前端也无法提交不可解的网。
- 检查结论保存在 `network_checks` 表（每期一行快照），重开页面会显示最近保存的结论时间，同时实时重算当前结果。

## 业务约束

- 只有在 `draft` 状态可修改点、观测和重新平差；提交前必须通过网形检查（`network_checks.solvable=1`），提交后必须由非创建人、非提交人的 `reviewer` 批准才能发布。
- 同类型、同端点、容差内的观测被视为重复；确实需要保留时可在请求体传 `allow_duplicate: true`。
- 法方程秩亏或坐标不可解时返回 422，不会写出平差结果。
- 每期结果独立保存，发布只改变期次状态，不删除历史观测。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖完整示例平差、提交审核发布、重复观测冲突和越权操作。
