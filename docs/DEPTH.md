# 深度登记 光伏电站限电事件与损失电量复算

规模：中型。使用者：新能源场站运营分析员。核心规则：仅用有效对照逆变器和相同辐照区间拟合基线；重叠限电事件先求并集再积分。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 遥测采信 | 执行并复核遥测采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 设备映射 | 执行并复核设备映射 | `POST /api/devices` | `app/modules/devices/rules.py` | `tests/test_devices.py` |
| M3 事件切片 | 执行并复核事件切片 | `POST /api/slicing` | `app/modules/slicing/rules.py` | `tests/test_slicing.py` |
| M4 基线拟合 | 执行并复核基线拟合 | `POST /api/baseline` | `app/modules/baseline/rules.py` | `tests/test_baseline.py` |
| M5 损失核算 | 执行并复核损失核算 | `POST /api/loss` | `app/modules/loss/rules.py` | `tests/test_loss.py` |
| M6 能量对账 | 执行并复核能量对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 运行归档 | 执行并复核运行归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码中文状态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_source` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_source` |
| TRAP-04 | 分段表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_segment_header` |
| TRAP-05 | 缺列记录 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bad_column` |
| TRAP-18 | 空态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_three_empty_types` |
| TRAP-07 | 遥测哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_sentinel_real` |
| TRAP-14 | 设备时刻冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_conflict_both_isolated` |
| TRAP-15 | 功率单位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_megawatt_conversion` |
| TRAP-06 | 全角千分位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_fullwidth_number` |
| TRAP-11 | UTC归一 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/devices/rules.py` | `tests/test_devices.py::test_time_equivalence` |
| TRAP-23 | 限电区间并集 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/slicing/rules.py` | `tests/test_slicing.py::test_overlapping_real` |
| TRAP-31 | 旧编码混入新批 | 静默按新格式解码污染新批 | 边界闸按编码强制泳道，互串即阻断 | `app/boundary.py` | `tests/test_boundary.py::test_legacy_bytes_only_enter_legacy_lane` |
| TRAP-32 | 停用后旧口仍收口 | 停用无法收口 | cutover 阶段旧入口对新文件 423，快照重放豁免 | `app/boundary.py` | `tests/test_boundary.py::test_cutover_closes_legacy_intake_but_snapshot_replays` |
| TRAP-33 | 先转换再归档丢原件 | 复核失去依据 | 归档包字节级封存 `source_original.bin` | `app/archive.py` | `tests/test_boundary.py::test_archive_keeps_original_bytes` |
| TRAP-34 | 切换失败污染现网边界 | 半切换状态 | 先全量探测后提交，阻断即保持旧版，支持回滚 | `app/governance.py` | `tests/test_boundary.py::test_switch_blocked_keeps_previous_boundary` |
| TRAP-35 | 来源规则变化未重判 | 沿用过期退出结论 | 规则指纹漂移即置阻断，要求重新取证 | `app/boundary.py` | `tests/test_boundary.py::test_rule_fingerprint_change_forces_rejudge` |

## 来源切换边界治理

| 能力 | 入口 | 核心文件 | 测试 |
|---|---|---|---|
| 边界总览（逐来源可退出/阻断） | `GET /api/boundary`、`GET /boundary` | `app/governance.py` | `tests/test_boundary.py::test_source_status_page_lists_exit_or_block` |
| 阶段切换（事务化） | `POST /api/boundary/switch` | `app/governance.py`、`app/store.py` | `tests/test_boundary.py::test_switch_blocked_keeps_previous_boundary` |
| 回滚上一版边界 | `POST /api/boundary/rollback` | `app/store.py` | `tests/test_boundary.py::test_rollback_restores_previous_boundary` |
| 历史旧批重放漂移取证 | `governance.replay_legacy` | `app/governance.py` | `tests/test_boundary.py::test_replay_drift_blocks_exit` |

退出判定的四项必要证据：规则指纹一致、旧批待复核数为 0、历史旧批重放漂移为 0、来源自测通过。新采集口为现役来源，不评估退出。

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
