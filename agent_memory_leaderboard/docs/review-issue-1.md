# memory_benchmarks：Issue #1 核实结论

核实日期：2026-10-05。对象：[Issue #1](https://github.com/matrixorigin/memory_benchmarks/issues/1)，本地与 issue 指定版本一致：`b037c42011f0ef3bf073c9c945ff0e97c0f1a074`。关联 Memoria 源码检查使用本机 `memoria` 的 `689f3f9badf0ae1eba3652ab91a8a329dc08701b`；不能由该源码版本推定同事实际运行的二进制版本。

## 结论与证据范围

这份 review 的主要方向成立：可靠的 Add/Search 底座已建立，但本仓库还没有回答与评分闭环，且存在可能阻塞完整评测的写入恢复与历史来源问题。它同时混合了源码事实、运行记录、算法假设与竞赛策略，不宜将每条建议直接视为必须实施的修复。

本次核对了适配器、执行器、部署文件、相关测试断言和公开官方材料。OCR 确定的 22 个自有文件均已阅读，覆盖率 22/22；54 个排除项包括测试、文档、数据集模块与依赖锁文件，未将其计入完整审查覆盖。另补充核对了相关测试、README 和 LongMemEval 等解析器。没有复跑 146 项测试／ruff、真实超长输入、Docker 构建或官方 Smoke；这些通过声明不是本次新测得的结论。逐项澄清已作为 [Issue #1 独立修正评论](https://github.com/matrixorigin/memory_benchmarks/issues/1#issuecomment-5987006462) 发布，未改写原 issue。

本机 clone 没有 `state/`、`var/` 或 benchmark result JSON。README 与 `.gitignore` 明确这些属于不入库的私人产物，因此只能认定当前交接缺少运行结果，不能认定同事从未运行。

## 官方背景核对

- 参评方提供 Add/Search，平台统一回答与评分；自托管接口路径可配置，不必恰好叫 `/add`、`/search`。正式返回最多 100 条，须按相关性排序。[API Guide](https://agentmemories.ai/api-guide)
- 117,760 是共享 Answer 的输入预算，来自 128,000 窗口扣除输出与安全余量。问题、选项、指令也需要计入；候选按返回顺序取可容纳的前缀，不能给证据单独分配全部预算。[API Guide：Answer context budget](https://agentmemories.ai/api-guide)
- 赛事 FAQ 确实列出学术榜的 embedding／LLM 限制和 rerank 例外；接口申请页用“开源方法／商业产品”表述，正式提交需核对所选组别及绑定版本。此前 BAAI/bge-m3、qwen-plus 的本地成绩只能作为开发证据。[赛事 FAQ，第 5 问](https://agentmemoryleaderboard.ai/competition/)
- Full 配额与 30 天解锁规则、Smoke 的频率与总量限制有官方依据。按当前准备进度保守规划一次正式机会合理，但不是官方将配额改成一次。10 月 31 日是材料截止，11 月 4 日为评测停止；应给执行与复核留时间。[官方文档](https://agentmemories.ai/zh-cn/docs)

Answer/Eval 的具体模型、提示词、tokenizer、裁判和数据版本仍须按冻结契约核对。补上一个 gpt-4o-mini 调用不自动成为官方等价评测。

## 逐项判断

### #1：当前指标不是比赛质量指标——成立，高优先级

`src/benchmark.py:46` 仅计算正文中的 evidence ID 覆盖，报告明确标记 `retrieval-only`；`src/suite.py` 的 `native_benchmark_score` 为 null。源码没有 Answer／Judge，也没有预算内证据覆盖。

应补回答、适用评分器、Recall@K、预算内 Recall、失败与时延。ID 召回保留为诊断指标，不能代替最终回答分数。不同数据集的评分规则需要分别对齐，不能以统一宽松 LLM Judge 代替全部原生评测。

### #2：粒度值得实验——成立；“预算没填满就是浪费”没有证据

`src/service.py:51` 按消息／32 KiB 分块，没有返回时的邻接补全。单消息可能缺少指代或承接上下文，是合理的质量风险。

10k—20k token 使用率依赖实际消息长度与 tokenizer，不能从代码直接测得；更多上下文也可能引入噪声、重复和“中间遗失”。整会话或 1k token 窗口不能仅凭乘法判为更优。粒度对照应固定回答与评分条件，比较准确性、预算内证据、重复／噪声和时延，不以填满 117,760 token 为目标。

### #3：超长输入可能把 Add 卡住——条件成立，尚未真实复现

`src/service.py:64` 按 UTF-8 字节切分，32 KiB 不是 token 上限。阿里云文档确认 text-embedding-v4 每条输入最多 8192 token；具体 tokenizer 必须与实际 provider 一致。[阿里云 Embedding API](https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api)

关联 Memoria 在 `service.rs:1334` 先同步 embedding，再插入；embedding 错误返回 HTTP 500。适配器 `src/memoria.py:76` 将该 500 转为 502，且不标记为明确未提交。随后 journal 保持 `inflight`，回查不到记录则持续 503。issue 的主因果链成立，需修正“适配器直接返回 500”的细节；是否被具体输入触发仍需真实验证。风险同样适用于文本赛道，不能只当代码赛道问题。

切分需要同时满足模型 token 限制、Memoria 字节限制和头部余量。上游可明确证明未提交的拒绝应有类型化状态，不能将所有 5xx 一律认为未提交。

### #4：不确定且未提交的写入无法自动恢复——成立；建议的恢复方案不够安全

`src/service.py:93` 回查无记录时永远返回 503，没有超时过期或重发状态。`tests/test_endpoints.py:220` 还专门断言这一行为。进程在写 journal 后、真正发 POST 前崩溃也会触发。已经提交且能按 marker 找到的写入可以自动恢复，因此不应泛称所有超时都需要人工处理。

“等待 timeout + grace 后查不到即可重发”不能证明旧调用不会稍后提交。重复不仅影响分数：`recover()` 发现两条同 marker 记录会返回 502，可能使请求继续失败，且破坏重试幂等。

优先使用后端原子幂等写入／唯一键与请求凭据；若只能在适配层恢复，必须定义有界状态机、迟到提交及重复归并机制。不能仅依靠时间窗口保证 exactly-once。

### #5：串行写入与探针昂贵——成立；吞吐数量未测量

正常单块写入在 `src/service.py:106`、`:114`、`:117` 顺序执行 store、get、Search；`src/memoria.py:150` 用完整内容作为查询，会再次调用已配置的 embedding。还有多次 journal 事务。文本赛道规模由官方披露，但具体消息数、耗时与瓶颈必须实测。

批量写入及轻量探针值得实施或实验，但不能直接换成 `/v1/memories/batch`：须核对角色、时间、metadata、逐条状态、部分失败及重试幂等。关联 Memoria 的 batch handler 没有将逐条 `observed_at` 传入服务；相关字段保真应纳入迁移验收。减少探针不能降低“Add 200 后可立即检索”的保证。

### #6：真实环境未验证——部分成立，标题失实

README“Local Memoria integration”明确记录一次真实 HTTP live smoke 通过及重复运行的即时全文召回失败，还提供 `src/smoke.py` 和真实回归脚本。正确结论是：**同事记录的无 embedding 环境做过真实验证，但缺少稳定通过、有 embedding 的质量结果及正式部署验收。**

Docker 镜像未构建／公网未验证是 README 当时的记录，不是本次重新验证的运行事实。单元 endpoint 使用 FakeMemoria/MockTransport；不能把这些测试当真实检索质量证明。

### #7：诊断 ID 进入正文，LongMemEval 可能泄漏标签——成立，应提高优先级

`src/benchmark.py:31` 把 `[dialogue:dia_id]` 写入模型可见正文。LongMemEval 解析器 `benchmarks/textual/longmemeval_s/dataset.py:19` 直接把原 session ID 用作 dia_id；上游生成脚本明确为答案来源生成 `answer_` 前缀。[LongMemEval 官方生成脚本](https://github.com/xiaowu0162/LongMemEval/blob/main/data/custom_history/sample_haystack_and_timestamp.py)

这已不是普通噪声前缀，而是可暴露来源相关性的标签。当前测试只确认 `answer`、`has_answer` 字段未入库，没有覆盖 ID 携带标签的情况。未检查本机真实数据文件，但解析器对这种上游 ID 没有隔离，风险路径已确认。

必要来源映射应保存在本地 manifest 或内部 metadata，移出 embedding／Answer 可见内容；不要给官方 Add 增加未声明的 metadata 字段。来源日期和角色是有效语义信息，不能与诊断 ID 一并删除；官方隐藏 Adapter 的具体正文尚未核实，不能声称其必定完全不带日期或来源格式。

### #8：options 未用于检索——事实成立，是实验项

`src/service.py:120` 只转发 query。接收但不使用 options 不自动违反协议；拼接选项、按选项召回或重排的收益没有本仓库证据，可能引入干扰。保留原始 query 作为基线，单独对照；不能称“通常明显提升”已被证实。

### #9：未实现 reranker／query rewriting——事实成立，不是必需修复

增加候选和 rerank 可以实验，但后端 `/v1/memories/search` 当前限 100；“取 300 再排 100”还需要内部候选接口或后端改动。query expansion／HyDE 需要遵守组别模型与 Search 证据边界，不能把生成的假设答案作为检索到的记忆返回。先建立可信 baseline，再决定是否引入额外模型与耗时。

### #10：来源时间进入衰减——成立，而且可能直接阻塞 Add

`src/service.py:54` 将 AML timestamp 转为 `observed_at`。关联 Memoria `store.rs:6172` 同时计算时间分数与置信度衰减，`:6215` 的 `MIN_CONF=0.05` 会直接剔除记录，不只是调整排名。

例如按该版本默认 T1（初始置信度 0.95、衰减常数 365 天），2023-05-08 的来源在 2026-10-05 已有 1246 天，置信度约 0.031273，低于阈值。配置 embedding 后，Add 的 subject-filtered 探针会进入混合路径；即便原文已持久化，也可能一直无法通过可检索性检查。此为源码和公式推导，未声称已完成真实 HTTP 复现。

另一个测试缺口是 `benchmark.batches()` 只把日期放在正文，不给消息设置 timestamp；本地常规 benchmark 因而不能覆盖这条正式来源时间映射路径。无 timestamp 的 Memoria 记录实际被设置为接收时间，置信度接近初值；并非持久化后仍完全没有 observed_at。

建议把本项提升到写入可靠性优先级。应区分来源时间、接收时间与原始证据的年龄策略；仅在返回后做 rerank 无法找回已被过滤的候选。我们此前 Memoria AML 分支已有 source_evidence／年龄策略和批次内补全设计，但此适配器调用普通 `/v1/memories`，没有携带该标记或使用来源服务，不能认为已自动继承这些修正。

### #11：Memoria 特色能力未使用——部分成立，不能泛化

确实主要存原始消息，未增加事实／事件抽取与双层证据结构。唯一 subject_id 隔离近重复覆盖有明确理由，移除它可能使不同来源互相覆盖。

但关联 Memoria 通用 store 已将实体抽取入队（`service.rs:1510`），无过滤的主表检索在 embedding 可用时已有 ActivationRetriever 路径（`:1778`）。不应直接断言图检索没有实现／启用，或唯一 subject_id 关闭所有治理；年龄衰减及过滤仍在生效。应先确认部署配置、Explain 中的 graph_attempted／hit 和后台任务，再决定抽取或图策略实验。缺少特色、不会获奖属于策略判断，不能作为源码缺陷或不具竞争力的证据。

## P2 意见

| 意见 | 核实判断 |
|---|---|
| Health 不检查 Memoria | 成立；源码明确为 liveness，符合当前基本 Health 协议。建议增加 readiness，不必把两者混成一个接口 |
| SQLite／文件锁影响扩展 | 单机多进程已支持；独立主机不支持，README 已声明。是否形成实际吞吐瓶颈需要测试，不必为单机参评立即重构分布式状态 |
| 整体 JSON 重写产生平方累计 I/O | 成立：报告随问题增长，每题多次全量保存；可改逐题文件／追加事件与周期快照，保留安全权限和断点一致性 |
| 缺少配置与分数据集成绩记录 | 当前交接中成立；应接收同事私人运行材料，再形成可公开的脱敏指标汇总，不能由 ignored 产物缺失反推没有工作 |

## 后续共建范围与实施顺序

1. 接收同事的实际运行 commit／二进制来源、模型配置、失败状态与完整性汇总，明确 public 数据范围和已完成的工作。
2. 先修正标签进入正文的路径，确认历史来源策略，再解决未提交写入的有界恢复和模型 token／字节边界；这些影响 baseline 是否可信及完整流程是否能完成。
3. 在本项目补统一 Answer/Eval、预算内证据与分项／时延，先得到真实 embedding 环境的小集 baseline。旧工具与用例仅作为可选参考，不要求迁入；采用后须通过本项目重新运行。
4. 固定上述条件后逐项实验粒度／邻接／排序，再考虑 options、rerank 或 rewriting；与吞吐、部署准备并行安排，避免同时改变多因素。
5. 固定候选版本、验证容量和公网接口、运行官方 Smoke，再安排 Full。完整物理清理、正式模型／组别核对及材料准备仍是交付事项。

后续与同事共建 `memory_benchmarks`，基于其现有 FastAPI 适配和执行工程开展工作。此前独立的 Memoria AML 分支、`aml-local-client` 及旧优化／评测计划停止推进；不要求先完成旧计划或迁移旧实现。Memoria 后端若需修改，由新项目的具体问题驱动，实施位置与验收条件在对应任务中明确。

已有 Memoria 分支的 LoCoMo 150 题对照与七维 42 任务／48 检查点来自另一条适配路径和已记录镜像，不能作为这个仓库 `b037c42` 的复测成绩。七维自动分数还有裁判争议；旧代码、PR、文档及结果保留为参考，不删除或关闭。后续有效方案维护于本仓库的 [共建方案与工作计划](competition-plan.md)。
