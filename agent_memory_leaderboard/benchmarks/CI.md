# GitHub Actions benchmark

## 范围

**AML benchmark** 手动调用已部署的阿里云 dev Add/Search，运行公开数据的检索评测或可选的本地 Answer/Eval。
第一版可选择 **LoCoMo-Refined** 和 **LongMemEval-S**，复用 `src/benchmark.py`；
不会重新部署服务，也不会启动 AML 官方 Smoke／Full。默认 `retrieval` 不调用回答／裁判模型；
`answer-eval` 调用独立配置的模型，详见[评测与诊断](EVALUATION.md)。
默认检索模式不需要 LLM；两种模式均不需要 Memoria master key、数据库、kubeconfig 或 Pulumi 凭据。
Add/Search 使用本仓库已有的 `AML_ADAPTER_API_KEY`，仅传入实际执行步骤，并映射为
runner 读取的 `ADAPTER_API_KEY`。

## 如何运行

合并包含 `.github/workflows/aml-benchmark.yml` 的变更后：

1. 确认 dev 服务已部署，并与团队协调评测负载；流程不会自动开启或解除发布冻结。
2. 进入本仓库 **Actions → AML benchmark → Run workflow**。
3. workflow 分支选择 **main**，填写以下参数。
4. 运行结束后打开该 run 的 **Summary**，查看指标并下载 **Artifacts**。

| 参数 | 默认值／填写方法 |
|---|---|
| `dataset` | 默认 `locomo`；`longmemeval` 指 LongMemEval-S，不是 Refined |
| `mode` | 默认 `retrieval`；可选 `answer-eval`，需要下列模型配置 |
| `histories` | 默认 `1`，允许 `1–10`；选定数据版本的前 N 个完整历史，不是问题数 |
| `top_k` | 默认 `100`，允许 `1–100` |
| `runtime_label` | 可选非秘密版本备注，默认留空；可填实际部署的 Memoria commit／镜像 digest 和适配器版本，最多 200 字符，不要填 key 或连接串；仅用于报告 |

第一次建议选 `locomo`、`histories=1`、`top_k=100`。所有选中历史内容先写入，随后查询
该历史的问题；多模态问题按现有 runner 跳过，不为缩短耗时删去历史内容。
LongMemEval-S 也从 `histories=1` 开始；它通常每个历史只有一道问题，但历史可能很长。
当前输入上限允许 LoCoMo 的 10 个完整历史，不允许通过本 workflow 运行 LongMemEval-S
全部 500 个历史。两种数据的工作量不能按相同 histories 数量推算。

目标固定为 `https://aml.cn-dev.matrixone.tech`，不能输入任意外部 URL，避免把部署的访问
key 发往其他服务。手动触发仅接受本仓库 main；不会通过 PR／push／schedule 自动执行。
其他数据集后续通过新增固定来源、准备步骤和必要的评分支持接入同一 workflow。

## 数据与版本

[ci-datasets.json](ci-datasets.json) 固定来源 commit、下载 URL、原始文件大小、SHA256、
历史数量和证据单位。下载完成后核对大小和 SHA256；不匹配时在调用业务接口前停止。
不执行来源仓库代码，不使用浮动 `main` 下载，不把答案或标签传给 Add/Search。
准备步骤未通过时不会安装后续依赖或调用服务。

LoCoMo 原始文件约 2.75 MB，LongMemEval-S 约 277 MB；小集仍下载完整原始文件并校验，
随后仅写入／评测所选前 N 个历史。数据文件不上传为 artifact。

报告记录 benchmark checkout SHA、dataset revision／SHA256、选择范围、Top K 和后端
版本说明。后端说明可选，由操作者填写，**不是自动读取或验证的在线镜像版本**。
留空仍可正常运行，JSON 记录 `null`，Summary 显示 `Not recorded`（版本未记录）；
版本比较时应从最近实际部署记录取得准确来源。此字段不选择、构建或部署服务，
benchmark 始终请求 `https://aml.cn-dev.matrixone.tech` 当前部署的 Add/Search。
workflow 与 CD 使用同一并发组
`aml-aliyun-unit-new-dev`，防止通过这两个入口在评测过程中更新应用。
并发组不阻止其他本地客户端、AML 官方任务或手工操作；GitHub 默认只保留一个 pending
运行，新 pending 可能替换旧 pending，不应同时排队多个部署／评测任务。

## Summary 与完整报告

已完成运行的安全聚合指标与解释见[固定基线](baselines/README.md)。完整断点仍在
对应 Actions artifact；Git 中的聚合报告不包含数据正文、回答或可恢复的 private cache。

Summary 包含状态、评测进程耗时、写入分块数、已处理／已查询／跳过的问题数、空结果、
有证据标签的问题数、召回率、Search P50／P95、按数据集 category 分组的结果及版本。
准备或执行失败时仍尝试生成 Summary 和 artifact，包含已完成部分及非秘密错误类型／HTTP
状态；依赖安装失败可从步骤日志定位。硬取消／整 job 超时不能保证最终步骤执行。
执行程序仅对固定状态码和固定适配器错误文案做精确匹配，记录静态
`adapter_reason_code`：`write_outcome_unknown`、`write_not_searchable`、
`memoria_request_denied` 或 `memoria_connection_unavailable`。未知响应、异常格式及
过大响应只保存错误类型／状态码，不保存原始错误正文。`memoria_request_denied`
本身不能区分认证范围和内容策略，需要结合后端证据定位；原因码不改变重试或写入语义。

| 指标 | 解释 |
|---|---|
| Mean evidence recall | 每题召回的 gold 证据比例，再对有正证据标签的问题取平均；0–1，越大越好 |
| All evidence hit rate | 所有 gold 证据均命中的问题占有正证据标签问题的比例；0–1，越大越好 |
| Empty results | 已成功执行的 Search 中返回空列表的数量；不能仅凭非空判断答案正确 |
| Search P50／P95 | 成功 Search 的客户端耗时，按 nearest-rank 计算，包含重试；秒，越小越快，不含跳过或失败问题 |
| Execution elapsed | 评测进程墙钟耗时，不含数据准备、依赖安装及 ready 前置检查；不是每条请求的时延 |

无证据标签的弃答题不进入召回率分母，也不以 0 计分。`N/A` 表示不可计算，失败时的
指标仅代表已完成部分；checkpoint 若仍标记 `running`，应结合 Execute outcome 判断
是否超时／中断，不能当作完成。LoCoMo 的证据单位是消息，LongMemEval-S 是 session，
两者不能直接据此作横向排名。数据集 category 不是 AML 榜单七个能力维度。

Summary 与 `summary.json` 还按选定历史分别展示 Search／跳过／有证据题数、召回率、
全部证据命中率、平均返回证据数和 Search P95；全局指标也包含平均返回证据数。
报告记录每题所属 `sample_id`，以便不同数据集使用稳定的分组。
新报告还保存取回的 `retrieved_evidence_ids` 和无法匹配原始历史的
`unmatched_gold_evidence`，均为来源 ID，不保存正文或 gold answer。Summary 展示
已审计 Search 数和带不匹配 gold 标签的题数；这些题仍按原始 exact-ID 规则计分，
不自动修正或排除。旧版报告没有此审计字段，不匹配数量显示 N/A，不能当作零。旧版 LoCoMo／LongMemEval
workflow 报告通过原始问题 ID 兼容分组；无法分配的记录保留在全局指标并单独提示。
未执行历史可显示 Search 数为 0、召回率为 N/A，不能据此认定检索失败或全量完成。
返回证据数用于理解检索量，不是越大越好的质量指标；Top K 100 下的召回不代表
较小证据预算下同样有效。

新报告还记录每题 `evidence_recall_at_k`，按同一次 Search 返回顺序截取前
1／5／10／20／50／100 条证据及本次实际 Top K，仅计算不超过请求 Top K 的切点。
Summary 展示各切点的有标签覆盖题数、平均召回和全部证据命中率；无标签题不计入
分母。不同长度回包仍按其可用前缀计算，不发额外 Search，也不额外写入或调用模型。
它衡量当前回包的排序效果，不代表另发较小 Top K 请求一定得到相同结果或时延。
旧报告缺少前缀指标，显示不可用；续跑混合旧／新记录时，以各切点实际覆盖题数解释，
不能假设覆盖所有已检索问题。

默认 retrieval 不计算答案指标。answer-eval 显示 Token F1、BLEU-1、本地 judge 分数、
预算内证据召回和未完成评分题数。全部质量指标是 0–1、越大越好；没有 gold 的题目
标记 skip。局部 judge 分数不宣称等价于数据集原生评分或 AML 分数。成功只表示流程
完成，不设置未经干净基线验证的质量门槛。

模型客户端对网络错误、429／5xx 最多尝试 4 次，指数退避为 0.5／1／2 秒；429 的
数字 Retry-After 最多等待 30 秒。裁判支持完整代码块包裹的严格 JSON；无效判分、
截断输出或重试耗尽只将该题标为 answer_failed／judge_failed，继续后续题目。
401／403／404／405 等认证／地址错误仍尽早停止整轮。存在失败待评题时最终 status
为 partial、退出码为 1，workflow 会显示失败，但 Summary 和 artifact 仍发布。
Summary 单列 eligible／scored／pending、评分覆盖率及两类失败题数；覆盖率分母排除
多模态和 missing-gold skip。各均值只使用有该指标的题目，judge_failed 的有效答案
仍可能参与词法指标。不能将部分成功题的均值视为完整质量基线。

### Answer/Eval 的额外配置

在 repository Settings → Secrets and variables → Actions 中配置：

| 类型 | 名称 | 用途 |
|---|---|---|
| Variable | `AML_BENCHMARK_ANSWER_MODEL` | 回答模型名称 |
| Variable | `AML_BENCHMARK_ANSWER_BASE_URL` | OpenAI-compatible `/v1` base URL，不含密钥或 query |
| Secret | `AML_BENCHMARK_ANSWER_API_KEY` | 回答模型凭据 |
| Variable | `AML_BENCHMARK_JUDGE_MODEL` | 裁判模型名称 |
| Variable | `AML_BENCHMARK_JUDGE_BASE_URL` | 裁判服务 base URL |
| Secret | `AML_BENCHMARK_JUDGE_API_KEY` | 裁判凭据，可与回答模型服务相同 |

这些配置仅在选择 answer-eval 时必需；retrieval 模式不使用它们。执行前验证必填项，
缺失时不写入记忆。准备步骤不读取模型凭据。工作流安装锁定的 evaluation extra；
适配器镜像不需要此 extra，也不需要重新部署。

当前开发配置（2026-10-07）：回答和裁判的 MODEL 均为 `qwen-plus`，两个 BASE_URL
均为 `https://dashscope.aliyuncs.com/compatible-mode/v1`，分别配置上述两个 API_KEY
Secrets，复用已有百炼凭据。GitHub 配置已保存并回读确认；尚未验证本次凭据的模型
调用或执行新契约基准。密钥不进入仓库或日志。

运行 `AML benchmark` 时选择 `main`、`dataset=locomo`、`mode=answer-eval`、
`histories=1`、`top_k=100`，`runtime_label` 可留空。模型设置在 benchmark 进程中生效，
不需要重新构建或部署 Memoria／适配器。qwen-plus 为提供商可更新别名，跨日期比较
不能假定底层权重相同；长期可比实验需选择并记录可用快照版本。回答与裁判同模型的
分数用于本地迭代，不能作为独立裁判或官方评分等价性的证明。

正式 AML 的 Answer/Eval 由平台固定，我们的配置不会替换平台模型。官方 Full checklist
写 Open-source Methods 的 Add 期望使用 `gpt-4o-mini`，商业产品无 Add/Search 模型限制；
FAQ 仍写学术榜 embedding 为 `text-embedding-v4`、LLM 组件为 `gpt-4o-mini`、reranker
不限制。两套组别名称的对应及纯存储 Add／上海 bge-m3 资格需向官方确认；不能用本地
qwen-plus 配置推断正式参评合规。
依据：[AML 文档](https://agentmemories.ai/zh-cn/docs)、
[赛事 FAQ](https://agentmemoryleaderboard.ai/competition/)。

全部输入（指令、角色、问题、选项／问题日期、证据）按固定 `o200k_base` 编码加
256 token 余量进行本地预算计数，上限 117760；保留完整记录的排名前缀。它不是所有
模型或 AML 的准确 tokenizer／chat template。报告记录模型实际 prompt usage（如有）；
上游报告超过预算会失败。正式可比性仍需确认目标 tokenizer 和原生评测契约。

每个 HTTP attempt 的状态、固定原因码、耗时、重试标记与请求标识 hash 写入
`run.requests.jsonl`，Summary 展示按 route／status／reason 聚合的数据。
未知错误只记为 unclassified，不保存原始错误正文；重试集合、次数和 Add ID 不变。

来源 ID 已移出模型可见内容。`run.sources/` 用本地内容 hash 映射消息和 UTF-8 分片；
多来源重复文本标记歧义，改写／未知内容标记 unmapped，均不计作已命中来源。
Summary 同时显示这些数量。新契约与旧标签版本不同，必须重新建立质量基线。

Artifact 名称为 `aml-benchmark-<run id>-<attempt>`，保存 30 天，包含：

- `selection.json`：固定数据来源、输入和版本信息。
- `run.json`：运行中的小型 header，结束时包含完整问题结果、随机 run ID 和用户列表。
- `run.questions/`：逐题原子保存的增量断点；硬中断后的报告／续跑需要一起保留。
- `run.sources/`：本地来源 ID／内容 hash 映射，无正文或 gold answer。
- `run.requests.jsonl`：安全 HTTP attempt 诊断。
- `run.evidence/`：返回证据 ID、内容 hash 等审计信息，不保存检索正文。
- `summary.json`／`summary.md`：统计与 Summary 内容。
- `failure.json`：存在准备／执行辅助错误时的非秘密错误信息。

默认评测步骤最多运行 120 分钟；整个 job 160 分钟，为下载、依赖及最终报告留出时间。
超时之前成功的写入仍可能存在，重新点击 Run workflow 使用新 UUID，**不是原运行续跑**。
workflow 第一版不支持跨 run 自动恢复。需要恢复时下载断点和相同数据，在项目目录
调用现有 runner 的 `--resume`，保持 kind、数据 SHA256、历史范围、URL、Top K 和原报告
一致；不要通过删除 journal 或换 request_id 恢复。旧 runner version 1 标签契约不能在
version 2 中续跑，只能保留旧代码恢复；新运行使用新 UUID，避免混用输入版本。
Answer/Eval 在 Search 完成后先保存 pending 断点，回答已保存时裁判失败不会重复生成
答案。它需要原始 private cache 才能恢复 pending 问题。CI private cache 在 artifact 目录
外，不上传模型正文；仅下载 artifact 无法恢复缺少该 cache 的 pending Answer/Eval。
工作流仍不提供跨 run 自动续跑。

回答／判分的重试、解析、上下文选择版本也进入 evaluation 契约，本次强化之前的
answer-eval 断点需要使用原 runner 或新开运行，不能混用；retrieval 断点契约不受影响。
离线 CI 安装 evaluation extra 并用 --no-sync 执行 Ruff／pytest，确保专项模型测试
具有 tokenizer 依赖；测试使用确定性的本地编码与模拟 HTTP，不调用真实模型。

## 数据保留与运行成本

新运行按 UUID 和 sample 隔离记忆，不清空其他用户，也不自动删除本运行数据。
Answer/Eval 的 private cache 也受同一保留期约束，不能随报告公开上传。
报告按现有保留策略记录清理期限；120 分钟步骤被强制终止、runner 未执行 finally 时，
最终报告步骤补写未设置的保留期限，不延长已有期限。
Artifact 过期不清理 Memoria、用户库、journal 或历史快照；物理清理需要独立运维方案。
正式评测前执行发布冻结及保留管理流程，见[比赛方案](../docs/competition-plan.md)。

该 workflow 消耗我们自己的 embedding、数据库和 Actions 资源，不消耗 AML 官方 Full
配额。请求串行发送；限制 histories 和执行时长不等于完整的全局 QPS／token 预算。
不要同时开启多个独立客户端；更大范围运行前先从小集测量容量。
