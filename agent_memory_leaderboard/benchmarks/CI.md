# GitHub Actions benchmark

## 范围

**AML benchmark** 手动调用已部署的阿里云 dev Add/Search，运行公开数据的检索评测。
第一版可选择 **LoCoMo-Refined** 和 **LongMemEval-S**，复用 `src/benchmark.py`；
不会重新部署服务，也不会启动 Answer/Eval 或 AML 官方 Smoke／Full。
不需要 LLM、Memoria master key、数据库、kubeconfig 或 Pulumi 凭据。
唯一业务 Secret 为本仓库已有的 `AML_ADAPTER_API_KEY`，仅传入实际执行步骤，并映射为
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

Summary 包含状态、评测进程耗时、写入分块数、已处理／已查询／跳过的问题数、空结果、
有证据标签的问题数、召回率、Search P50／P95、按数据集 category 分组的结果及版本。
准备或执行失败时仍尝试生成 Summary 和 artifact，包含已完成部分及非秘密错误类型／HTTP
状态；依赖安装失败可从步骤日志定位。硬取消／整 job 超时不能保证最终步骤执行。

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

当前 **不计算 Token F1、BLEU-1、答案正确率或 AML 官方分数**。这些需要另外实现
Answer/Eval 及各数据集的原生评分。成功的 workflow 表示本次流程完成，不代表达到
预设质量阈值；第一版不设置未经基线验证的分数门槛。

Artifact 名称为 `aml-benchmark-<run id>-<attempt>`，保存 30 天，包含：

- `selection.json`：固定数据来源、输入和版本信息。
- `run.json`：已有 runner 的完整报告／断点、随机 run ID 和用户列表。
- `run.evidence/`：返回证据 ID、内容 hash 等审计信息，不保存检索正文。
- `summary.json`／`summary.md`：统计与 Summary 内容。
- `failure.json`：存在准备／执行辅助错误时的非秘密错误信息。

默认评测步骤最多运行 120 分钟；整个 job 160 分钟，为下载、依赖及最终报告留出时间。
超时之前成功的写入仍可能存在，重新点击 Run workflow 使用新 UUID，**不是原运行续跑**。
workflow 第一版不支持跨 run 自动恢复。需要恢复时下载断点和相同数据，在项目目录
调用现有 runner 的 `--resume`，保持 kind、数据 SHA256、历史范围、URL、Top K 和原报告
一致；不要通过删除 journal 或换 request_id 恢复。

## 数据保留与运行成本

新运行按 UUID 和 sample 隔离记忆，不清空其他用户，也不自动删除本运行数据。
报告按现有保留策略记录清理期限；120 分钟步骤被强制终止、runner 未执行 finally 时，
最终报告步骤补写未设置的保留期限，不延长已有期限。
Artifact 过期不清理 Memoria、用户库、journal 或历史快照；物理清理需要独立运维方案。
正式评测前执行发布冻结及保留管理流程，见[比赛方案](../docs/competition-plan.md)。

该 workflow 消耗我们自己的 embedding、数据库和 Actions 资源，不消耗 AML 官方 Full
配额。请求串行发送；限制 histories 和执行时长不等于完整的全局 QPS／token 预算。
不要同时开启多个独立客户端；更大范围运行前先从小集测量容量。
