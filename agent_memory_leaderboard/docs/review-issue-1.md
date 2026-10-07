# AML Issue #1：当前核实结论与处理状态

核实日期：2026-10-07。对象为 [Issue #1 正文及修正评论](https://github.com/matrixorigin/memory_benchmarks/issues/1)。正文审查的是 `b037c42`；本轮真实评测代码为 `origin/main@003dda1c5f1681d6a307dec9203968e3168992e6`。关联 Memoria 核对实际部署源码 `81c5cc6d5e86a34072ecbc1bb41979b06364e7c7`，并检查原评论引用的 `689f3f9` 中相关回退路径。代码、部署记录与评测结果分别作为各自范围的证据，不将旧版本结论直接视为当前状态。

## 总体结论

适配器已有真实 embedding、公网 HTTPS、镜像构建及完整小集 Add/Search 验证。输入标签隔离、Answer/Eval、重试观测、增量断点和诊断工具已合入，首个无标签 LoCoMo 历史的 87 道文本题全部完成作答判分。时间混合场景已复现历史证据不可见的配置风险；未知写入恢复、embedding token 边界、正式容量和官方 Smoke 仍待解决或验收。不能继续使用“没有真实环境验证”“全部只用模拟后端”的结论，也不能以小集成功表示参赛就绪。

Issue 按当前实现维护未完成任务、验收条件及版本证据，移除过时或无证据的断言。下表
保留原编号以便定位审查范围；算法方向属于实验，不将所有建议列为已确认 bug。

## 逐项处理状态

| 原项 | 当前事实与应更正结论 | 处理状态 |
|---|---|---|
| #1 指标不等价 | 首个无标签历史已完成 Answer/Eval：自建 judge 56.32%、Recall@100 80.23%、覆盖 87／87。提示词与评分器由项目维护，原生 evaluator 和 AML 等价性未确认 | 自建闭环及首个基线完成；扩大覆盖、裁判审计和官方评分待验收 |
| #2 存储粒度 | 仍按消息及 32 KiB 字节切块，没有返回时邻接补全。实测首历史实际 prompt 约 4.9k–6.7k token，100 条记录全部进入预算；不能仅凭未填满窗口认定浪费 | 保留为对照实验；先审查标注证据已到位仍判错的题目 |
| #3 超限永久失败 | 32 KiB 仍不是模型 token 上限；持续 embedding 拒绝仍可能留下未知写入。实际使用上海 BAAI/bge-m3，不能用 text-embedding-v4 的限制代替当前 provider 契约。响应体重试修复不解决超长输入 | 待按实际模型核对边界及真实复现 |
| #4 未知写入恢复 | 已提交且能找到 marker 的写入可恢复；查不到时仍停留 inflight。timeout + grace 不能排除迟到提交；重复 marker 会使 recover 返回 502 | 缺口仍在，否定盲目超时重发方案 |
| #5 写入吞吐 | 每个记忆分片正常执行 store/get/search；全内容探针会再次计算 embedding。已测固定小集和 20 次低负载 Search，但未做正式规模写入／容量测试，也未接入批量写入 | 部分测量，尚未完成容量验收 |
| #6 真实部署验证 | 已有 embedding 真实最小联调、适配器／Memoria 镜像、公网 HTTPS，以及两套完整小集回归 | 本地真实联调和部署已完成；官方 Smoke 仍待执行 |
| #7 标签进入正文 | 新 runner 不把来源标签写入 Add，使用外置内容 hash manifest；首历史 8700 条返回记录全部匹配，歧义和未匹配均为 0；旧报告仍单独保留 | 已合入并完成首个无标签真实基线；改写／合块前仍需稳定 ID 映射 |
| #8 options | 接收但不参与 query；不自动违反协议，收益需要对照 | 实验项，不是必需协议修复 |
| #9 rerank／rewriting | 尚未添加。后端公开 Search 最多 100，300→100 需要内部候选能力；HyDE 生成内容不能冒充返回证据 | 实验项，先固定可信质量基线 |
| #10 时间衰减 | 来源 timestamp 仍映射 observed_at。实测旧记忆单独时走 fulltext 可见；新旧混合时走 hybrid，仅近期可见，旧记录仍 active；阈值两侧也只返回较新一侧 | 条件风险已复现；时间适配实验待实施，不宣称所有 Add 失败或全局衰减为 bug |
| #11 Memoria 能力 | 未接入独立事实／时间线双层抽取；普通写入已有实体抽取入口，无过滤主表 Search 已有图检索分支。unique subject 不等于禁用所有治理 | 更正“所有特色能力未使用”；实际启用与命中需看配置及 Explain |

## 已取得的真实验证

[LoCoMo 回归 37571328104](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37571328104) 完成 101/101 个 Add 批次、242/242 道文本检索，跳过 104 道多模态题；Recall@100 为 83.61%，Search P50/P95 为 0.99/2.63 秒，执行约 34.94 分钟。

[LongMemEval-S 回归 37574166791](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37574166791) 完成 69/69 个 Add 批次和唯一一道题，证据排第一，执行约 11.02 分钟。两份 contract 与各自修复前失败运行一致，原失败批次全部完成；没有改写输入或重置旧 journal。

这些运行验证固定选择的数据接收和检索链路。LongMemEval 的 `answer_` 标签仍进入模型可见正文，其 100% 不能称作无标签泄漏的质量基线；也不能推断本轮高分由该标签造成。LoCoMo 已披露的异常 gold ID 仍按原 exact-ID 规则计分。两套结果都没有 Answer/Eval，不是官方 AML 成绩。

适配器访问日志还有 11/4 次中间 Add 503，既有有界重试后均完成。现有产物没有保存中间响应原因，不能将这些 503 归因于解码、未知写入或索引延迟。应补安全的重试次数／原因观测后定位。完整小集不是正式数据规模吞吐证明，也没有解决所有未知写入。

[干净基线 37594716119](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37594716119)
完成 28 Add／87 Search／87 判分，覆盖率 100%，自建 judge 49／87；source attribution
无歧义／未匹配。5 次 Add 503 均为 `write_not_readable`，涉及 4 个逻辑批次并恢复；
这是新运行的诊断，不能反推旧运行 11／4 次 503 的原因。25 道标注证据全部进入
预算但 judge 未通过，需受控抽查实际回答与裁判；不能直接认定检索算法有缺陷。

PR #9 的 [CI 37589473987](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37589473987)
已通过，包含 280 项 pytest（1 warning）。真实 Explain 发现诊断白名单漏记 `hybrid`
后，本分支补充该路径和 8 个用例，完整 pytest 288 passed、Ruff 通过。聚合报告与
验收范围见[当前方案](competition-plan.md#干净端到端基线2026-10-07)。

## 对历史来源时间评论的更正

Memoria [混合检索评分](https://github.com/matrixorigin/memoria/blob/81c5cc6d5e86a34072ecbc1bb41979b06364e7c7/memoria/crates/memoria-storage/src/store.rs#L6204) 确实按 observed_at 衰减，并剔除有效置信度小于 0.05 的候选。T1 初始 0.95、365 天衰减常数的计算本身没有变化。

但 [服务层](https://github.com/matrixorigin/memoria/blob/81c5cc6d5e86a34072ecbc1bb41979b06364e7c7/memoria/crates/memoria-service/src/service.rs#L1963) 在混合结果为空时继续全文检索；原评论引用的旧版也有这条分支。因此“混合路径过滤了旧证据”不能直接等同于“Search 一定没有结果／Add 一直失败”。普通无过滤 Search 还可能走图路径，最终行为与查询和过滤条件有关。

已有隔离合成探针使用 2023-05-08 和当前来源时间，两组 Add/Search 均 200，旧时间保存为 observed_at；未复现仅因该旧时间就阻塞 Add。常规公开数据 runner 仍只在正文保存日期、不发送 timestamp，故完整 benchmark 不能覆盖这条时间映射。

新增四组时间场景共 16 次请求全部成功，但混合场景三种 query（含旧文本精确查询）
均只返回近期记录；阈值两侧只返回较新侧。独立 owner-scoped Explain 分别观察到
旧-only 为 fulltext、混合／阈值为 hybrid；结构化查询确认旧记录 active，observed_at
为来源时间、created_at 为本次写入时间。该结果与部署源码的过滤／回退条件一致。

现在应实验验证适配边界如何解耦来源时间与置信度年龄，同时保留原时间用于证据与
时序推理；不将条件风险扩大为“所有 Add 必定失败”，也不据此全局关闭 Memoria 衰减。
LoCoMo 基线未发送 timestamp，因此这项风险不能解释其答案分数。完整安全证据见
[时间探针验收](competition-plan.md#时间与低负载探针验收2026-10-07)。

## 官方规则与策略需澄清

- **组别名称与模型要求仍有不一致之处。** 当前 Full checklist 已写 Open-source Methods 的 Add 阶段期望使用 gpt-4o-mini；不能继续笼统写“开源方法模型规则完全未出现”。但 FAQ 仍使用学术／工业分类，学术榜 embedding 指定 text-embedding-v4，开放权重模型建议工业榜。两套名称的映射、上海 bge-m3 对本次申报的资格，以及不使用 LLM 的纯存储 Add 是否适用该要求，仍需官方明确；不要自行把规则扩大到所有组件，也不要直接认定现配置合规。[当前文档](https://agentmemories.ai/zh-cn/docs)、[赛事 FAQ](https://agentmemoryleaderboard.ai/competition/)
- **117,760 是总输入预算。** 指令、问题及 options 也占预算，不能全部分配给检索证据；精确 Answer 模型、tokenizer、提示词和 evaluator 需按冻结契约核对。[API Guide](https://agentmemories.ai/api-guide)
- **配额仍是两次，保守规划一次是时间判断。** 第二次 Full 在第一次完成 30 天后解锁；失败／取消支持符合条件的断点续跑，不应理解成无法恢复或每次续跑都消耗新 Full。材料截止为 10 月 31 日，评测停止为 11 月 4 日。按 10 月 7 日当前时间规划，新的首次 Full 完成后再等 30 天已超出窗口。[赛事 FAQ](https://agentmemoryleaderboard.ai/competition/)
- **开放材料不要一刀切删除。** 需要清理凭据、私有部署配置和不能公开的评测轨迹；公开数据的下载器、解析器、来源链接和方法设计可按授权保留。公开 GitHub 固定 commit 是要求，所需完整复现范围仍需对齐官方审核。
- **接口“逐点完全符合”过于绝对。** 基本 schema、认证与用户隔离已有验证，但内容策略拒绝和权限拒绝仍共用 403 等边界尚未解决；官方 Smoke、长请求、恢复和容量不能由单元测试或健康检查代替。

## P2 更新

- `/health` 是 liveness，符合基本协议；独立 `/ready` 已实现并有健康／上游不可用／journal 不可用及线程隔离用例。该项可完成，无需把上游依赖加入 liveness。
- journal 仍是单 Pod SQLite／文件锁，支持同机多进程；不能水平扩展的范围限制成立，实测瓶颈没有确定。不以增加 worker 数量当作容量验收。
- 逐题原子文件、最终合并及恢复读取方式已合入，有离线断点／失败续跑用例；真实首个 87 题流程通过，运行进程强杀和更大规模仍需验收。
- README、部署／接入示例、Actions Summary、按版本与数据集的结果记录已经补齐；原“没有记录”应更新为现有证据与剩余覆盖范围。私人 artifacts 不应为了证明跑过而直接提交原始正文。

## 当前优先级

1. 抽查 25 道“标注证据已进入预算、judge 未通过”的题目；区分上下文、作答与裁判问题，避免只看总分修改算法。
2. 针对已经复现的时间风险做适配边界对照；扩大干净 LoCoMo 与 LongMemEval-S 覆盖，保持模型、提示词、预算与评分版本固定。
3. 处理 token／字节边界、可信的未知写入恢复；诊断新运行的 read-back 暂时不可读，不改变安全重试边界。
4. 可信来源 ID 映射完成后再做粒度／邻接实验；options、rerank 的收益需对照。另行验收写入容量、公网长请求和物理清理。
5. 对齐正式组别／模型／开放材料，完成官方 Smoke，再冻结版本并安排 Full。

正式方案、部署及结果的唯一维护入口为 [共建方案与工作计划](competition-plan.md)。继续基于同事的 memory_benchmarks 实现共建；旧独立适配工作只作为参考，不作为当前项目成绩。

本地开发能力、保留语义及验收边界见[当前设计](competition-plan.md#本地质量闭环与诊断设计)和[操作指南](../benchmarks/EVALUATION.md)。代码实现状态不替代已发布版本或真实运行证据。
