# 股票研究辅助系统：数据库需求

版本：0.1｜日期：2026-10-05｜状态：逻辑设计基线，尚未创建数据库

配套文件：[前端需求](frontend-requirements.md)、[后端需求](backend-requirements.md)。

## 1. 选型与原则

建议使用PostgreSQL：存储行情、导入版本、实验、模型指标、任务及预测记录。模型文件、原始上传文件和大型诊断产物存储于本机专用目录，数据库保存路径、校验值和元数据。

首期单只股票，但所有行情、实验和预测均携带stock_id。采用统一逻辑库与股票标识隔离，不为每只股票建立独立数据库。以后可按时间分区，数据量较大时评估时间序列扩展；首期不强制安装扩展或分区。

PostgreSQL支持原生表分区；是否启用应由规模和查询表现决定。[官方资料](https://www.postgresql.org/docs/current/ddl-partitioning.html)

所有表名与字段为需求级草案，物理类型、迁移和约束由实施阶段固化。本文件不包含执行SQL。

## 2. 时间、标识与精度

- 主键使用稳定ID；股票唯一标识为市场/交易所＋股票代码，代码按字符串保存，保留前导零。
- 交易日期使用date；实际时刻采用timestamptz，A股业务按Asia/Shanghai解释；审计时间统一带时区。
- 行情价格采用定点数，成交量以明确的基础单位存储，金额采用定点数；模型计算可用浮点数，持久化必须保存单位与精度约定。
- 缺失值用NULL及原因字段表达，不用0表示未知价格。
- 周期、价格口径、数据来源与版本必须可追踪；不混合不复权、前复权和后复权价格。

## 3. 行情、导入与数据版本

| 实体 | 主要字段 | 约束与用途 |
|---|---|---|
| stocks | id、exchange、code、name、active、created_at | exchange＋code唯一 |
| data_sources | id、name、provider_type、capabilities | 手动文件或未来接口；不存明文密钥 |
| file_artifacts | id、kind、path、checksum、size、created_at | 原始文件、模型与诊断产物索引 |
| imports | id、stock_id、source_id、file_id、mapping、units、price_basis、status、report、created_at | 记录预检、确认策略和导入结果 |
| market_bar_revisions | id、stock_id、interval、bar_time、trade_date、OHLC、volume、amount、source_id、import_id、price_basis、revision、recorded_at | 保留每次行情修订；合法OHLC、价格正、量非负 |
| market_bar_current | stock_id、interval、bar_time、price_basis、revision_id | 当前版本指针，组合唯一；引用行情修订 |
| adjustment_factor_revisions | id、stock_id、effective_date、factor、source_id、recorded_at | 保存复权因子修订与可得时间 |
| trading_calendar | exchange、trade_date、is_open、session_definition、source_id、version | 交易日历及交易时段 |
| data_snapshots | id、stock_id、cutoff、price_basis、manifest_file_id、checksum、transform_version、calendar_version、created_at | 不可变训练/预测快照 |

interval取值包含1m、5m、30m、1h、1d、1mo；由原始数据聚合的记录需标记derived及聚合规则版本。日线bar_time采用固定、明确的时间约定，trade_date为主要交易日键。

修订不能覆盖已被实验引用的数据。快照manifest须枚举使用的行情修订、复权因子、来源和转换规则，或指向完整不可变数据产物；仅保存截止时间不能保证复现。

训练数据、验证数据和最终测试数据的范围属于实验配置，不能因更新当前行情指针而改变历史实验输入。

## 4. 训练、模型与评估

| 实体 | 主要字段 | 约束与用途 |
|---|---|---|
| jobs | id、type、status、stage、progress、cancel_requested、started_at、finished_at、error、heartbeat_at、idempotency_key | 通用异步任务；进度可为空 |
| training_runs | id、job_id、stock_id、snapshot_id、config、seed、code_version、dependency_manifest、split_policy、budget、created_at | 完整训练配置与资源预算 |
| optimization_trials | id、run_id、model_family、horizon、parameters、status、objective、duration、error | 每次搜索试验可追踪 |
| models | id、run_id、trial_id、stock_id、family、horizon、version、artifact_id、parameters、feature_schema、price_basis、validation_horizon、status | 模型产物不可变；状态与入选结果分开 |
| evaluation_windows | id、run_id、model_id、split_type、train_range、calibration_range、evaluation_range、step_definition | 区分训练/验证/最终测试和校准范围 |
| model_metrics | id、model_id、window_id、target、forecast_step、metric_name、metric_value、sample_count、metric_definition_version | 指标按目标和步长保存 |
| model_diagnostics | id、model_id、window_id、target、forecast_step、diagnostic_type、summary、artifact_id | 残差序列或图表的大数据放文件 |
| selection_policies | id、version、horizon、thresholds、weights、created_at | 门槛在实验前冻结；可无综合评分 |
| model_selections | id、stock_id、horizon、policy_id、model_id、rank、decision、reason、created_at、valid_from、valid_to | 支持不同周期的候选与入选历史 |
| model_state_snapshots | id、model_id、data_snapshot_id、artifact_id、method_version、created_at | 参数未重训但预测状态更新时的快照 |

horizon使用next_day、one_month、three_months作为产品目标，同时记录实际交易步数及终点算法。未来其他期限通过新增配置扩展，不能假定three_months支持7个月。

模型所属股票、数据价格口径和预测目标必须在应用层和可行的数据库约束中保持一致。每个周期最多三个有效入选模型；不足三个是正常结果，全部不达标也保存选择记录。

## 5. 预测、AI与持有辅助

| 实体 | 主要字段 | 约束与用途 |
|---|---|---|
| predictions | id、job_id、stock_id、snapshot_id、horizon、origin_date、target_end_date、generated_at、numeric_status、ai_status、request_config | 一次请求产生独立记录，不覆盖旧预测 |
| prediction_model_runs | id、prediction_id、model_id、state_snapshot_id、status、error | 单个模型失败可保留其他结果 |
| prediction_points | id、model_run_id、target_date、forecast_step、target、estimate、lower、upper、nominal_coverage、interval_type | 区分开高低收、逐日收盘和期末等目标 |
| prediction_summaries | id、prediction_id、summary_type、payload、method_version | 模型分歧、聚合结果；不存在未验证的隐式集成 |
| strategy_rules | id、version、name、parameters、cost_assumptions、execution_assumptions | 参考买卖区间规则；不存订单 |
| strategy_evaluations | id、rule_id、run_id、model_id、window_id、metrics、sample_count、artifact_id | 可执行规则的历史评估 |
| holding_analyses | id、prediction_id、rule_id、reference_price、reference_date、reference_source、observation_start、target_low、target_mid、target_high、upside_values、conditions、status | 保存剩余空间、期限与范围限制 |
| ai_analyses | id、prediction_id、version、provider、model_name、prompt_version、status、content、generated_at、error | 追加版本，不替换原解释 |
| information_sources | id、url、title、publisher、published_at、retrieved_at、content_hash、excerpt | 来源和时间证据；遵守来源可保存范围 |
| ai_analysis_sources | analysis_id、source_id、usage | 多对多来源关联 |
| prediction_outcomes | id、prediction_point_id、actual_revision_id、observed_at、evaluation_version、error、covered | 后续真实结果及数据版本 |

point区间满足lower≤upper；OHLC点估计在同一模型和目标日满足价格关系。interval_type明确边际、期末或路径含义；名义覆盖率不是实际可信度。

信息来源发布时间未知时可为空并标注未知，不用抓取时间冒充发布时间。AI不能篡改prediction_points；解释失败仍可保留成功数值结果。

追加实际结果时引用明确的行情修订；数据后来修正则追加评估版本，保留旧评估。保存七个月持有观察不意味着模型拥有七个月预测能力。

## 6. 索引与扩展

- 行情主要查询索引：stock_id＋interval＋price_basis＋bar_time；修订增加revision/recorded_at。
- 模型索引：stock_id＋horizon＋status；指标索引：model_id＋window_id＋target＋forecast_step。
- 预测索引：stock_id＋generated_at；点序列索引：model_run_id＋target_date＋target。
- 任务索引：status＋created_at，幂等键在约定作用域唯一。
- 初期使用普通表与分页；分钟数据达到需要分区的规模后按时间范围分区，必要时再评估股票维度子分区。
- 模型和大规模特征文件不存为数据库大对象；优先可校验的文件格式与路径索引。

性能验证使用真实数据，测定行情窗口查询、批量导入和预测历史访问。无依据地按每只股票建表或数据库不属于首版方案。

## 7. 一致性、备份与生命周期

导入提交采用事务或暂存后原子发布，避免部分成功被当作完整数据集。模型产物先写入并校验，再登记成功状态；预测结果完成后才能发布为成功。

数据库默认仅供本机访问。配置和密钥独立存放，日志不保存敏感配置。首版不做多人权限系统。

备份覆盖数据库、上传文件、数据快照和模型产物，提供恢复验证；备份频率与保留期限待确认。数据库迁移有版本记录。

原始数据、实验、正式预测采用可追溯保留。失败任务的临时文件可按配置清理；被历史模型/预测引用的文件不可自动删除。删除股票优先停用，物理清理需检查引用并明确确认。

## 8. 验收标准

1. 同一时间的冲突上传保留旧修订，当前指针可确定，旧快照可复现。
2. 股票代码保留前导零，多股票数据与模型不混用。
3. 训练、参数试验、校准、最终测试和选模策略均可追踪。
4. 预测可定位到输入快照、模型产物、状态快照及代码/依赖版本。
5. 历史预测和AI解释不被新分析覆盖，后续行情可用于追踪误差。
6. 无达标模型、AI失败、任务取消均有明确状态和原因。
7. 备份恢复后，数据库引用的文件仍可访问并通过校验。

## 9. 待确认与变更

待确认：数据量、原始文件样本、精度与单位、默认保留期、备份方式、区间规则、选模门槛及实际部署方式。

字段或实体修改须记录版本、迁移影响和前后端接口影响；不可通过修改历史结果来适配新规则，应产生新版本记录。
