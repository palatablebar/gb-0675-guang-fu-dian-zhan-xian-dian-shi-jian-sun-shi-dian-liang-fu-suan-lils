# 光伏电站限电事件与损失电量复算

给新能源场站运营分析员使用。仅用有效对照逆变器和相同辐照区间拟合基线；重叠限电事件先求并集再积分。

七个业务模块：遥测采信、设备映射、事件切片、基线拟合、损失核算、能量对账、运行归档。

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

访问 http://localhost:18244 。启动直接载入现场脏批。切换干净样例和旧编码样例可对照核验。完整对照批INV-A重叠事件取并集后30分钟，基线100kW、实际60kW，损失20kWh；无合格对照或遥测冲突必须保持未定，不能填0。

各模块页面对应 `/<模块英文名>`，各模块执行入口为 `POST /api/<模块英文名>`，不传参即可独立演示。完整运行 `POST /api/runs` 支持 `dataset`（dirty/clean/legacy）、`csv_text` 与 `overrides` 参数。批次历史 `GET /api/runs`，详情 `GET /api/runs/{run_id}`。

同输入与规则版本生成相同批号，只保留一条批次。复核入口 `POST /api/runs/{run_id}/review` 提交当前 `revision`，陈旧提交409。归档包只有一个下载入口 `GET /api/export/{run_id}`，含证据和运行摘要。原始隔离在页面同屏显示。

SQLite使用命名卷，容器重启后保留历史。要重建演示可在Compose中改用新的卷名称，保留旧卷。

源码在 `app/`，样例在 `sample_data/`，测试在 `tests/`；定义见 `docs/DATA-DICTIONARY.md`，模块和边界测试对应关系见 `docs/DEPTH.md`。输入上限2MB，属于离线复核工具。

采信页支持上传或粘贴CSV。完整源字节与参数随批次持久化，跨页通过run_id读取同一结果。`POST /api/runs/{run_id}/rerun` 读取该批不可变输入快照重新计算，规则未变时批号不变。切换另一示例则创建或复用另一批次。

## 来源切换边界（旧采集口停用治理）

旧采集口（GB18030 旧编码）与新采集口（UTF-8 新格式）各自绑定唯一批次泳道，边界规则集中在 `app/boundary.py`，装配与取证在 `app/governance.py`，页面 `/boundary`：

- **泳道隔离**：采信读取字节前先做编码识别与入口闸 `admit()`。旧编码字节只能进旧批泳道，新格式字节只能进新批泳道，互串返回 `ENCODING_MISMATCH`；原始字节可经 `raw_b64` 通道提交，避免文本通道破坏编码。
- **两阶段边界**：`dual`（双轨并行，旧口仍收口）与 `cutover`（旧口停用，新文件返回 423 `LEGACY_INTAKE_CLOSED`）。
- **旧批只读重放**：`rerun` 携带批次不可变快照并豁免入口闸（`replay=True`），不经过来源开闭判断；规则未变时批号、视图、四账全部一致，漂移即报 `REPLAY_DRIFT`。归档包内 `source_original.bin` 字节级保留旧原件，禁止先转换再归档。
- **可退出/阻断取证**：`GET /api/boundary` 逐来源返回状态。旧采集口退出须同时满足规则指纹未变、无待复核旧批、全部历史旧批重放零漂移、来源自测通过；任一不过给出稳定原因码。来源规则（解码链、字段、别名）变化会改变 `rule_fingerprint()`，退出结论立即失效并要求重新取证。
- **切换事务化与回滚**：`POST /api/boundary/switch` 先全量探测，任一阻断则保持旧版边界（HTTP 423）并返回原因；成功才写入新版本，旧版进入 `boundary_history`。`POST /api/boundary/rollback` 恢复上一版边界。历史与逐来源状态在 `/boundary` 同屏展示。
