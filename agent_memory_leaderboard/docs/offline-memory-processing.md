# 时间注释与历史加工实验

## 当前方案：只附时间注释，不占检索槽位

`MEMORY_TIME_NORMALIZATION_ENABLED=true` 可启用 Search 时间注释，默认关闭。
实现位于 [`src/temporal.py`](../src/temporal.py)，规则版本 `calendar-hints-v1`。
Add 的正文、幂等身份和 journal 不变，不调用加工 LLM，不额外查询或写入 Memoria。
本轮不需要常驻 Worker，也不再使用宽泛的事实提取提示词。

先按现有方式检索原文，再为其中有明确消息时间锚点的相对时间附注。
原文作为完整前缀保留，记录 ID、数量、分数和排序不变：有几条符合条件就注释几条，
没有符合条件的记录就全部返回原文。不存在固定 10% 或 30% 派生配额。
每条注释最多 1024 字符、8 个不同表达，整个响应最多增加 8192 字符。
预算不足时跳过注释，不删除原文。下游仍需为完整回答提示词预留 token 预算；
如果注释导致超限，应先移除注释，再按原有原文预算策略处理。

支持 yesterday/today/tomorrow、昨天/今天/明天、数字天前、上/本/下周和月份、
去年/今年/明年及对应英文。周范围明确按周一至周日；英文 last Friday 按最近一个
严格早于消息日期的星期五解释，上周五按上一日历周的星期五解释。
这些是明确标记的日历解释约定，并非从原文推断出的新事件事实。

锚点仅来自原消息 timestamp、adapter 渲染的消息时间头，或原文开头完整的
`[1:00 pm on 15 July, 2023]` 格式时间，不使用 created_at 或当前日期。
多锚点日期冲突、无锚点、续接分片、引号内表达和未支持表达直接跳过。
不猜测人物本地时区，不解决跨消息指代、引述转述、隐含日期或事件关联。
目前是有范围限制的规则实验，仍可能遇到自然语言歧义，不能预设能提高准确率。

验证时应使用同一批原文候选、回答模型、回答提示词和完整官方 Judge prompt，
同时记录改善与退化，不能只展示收益题，也不能把本地结果称为官方成绩。
该开关通过部署配置启用；回滚为 false 即恢复原始 Search 内容，不需要迁移数据库。

## 历史方案：宽泛 LLM 提取（仅保留复现实验入口）

下文的 extract/publish/search CLI 是此前宽泛提取实验的独立工具，
不会被 adapter 或上述时间开关调用。其 30% 混合策略不代表当前实现；
旧实验文件和规则版本保留以便核对此前结果，不应混入新一轮时间注释对照。

本功能用于验证：从历史提取有来源的事实／事件，是否能改善后续检索。
使用项目自定义提示词，不能把实验结果称为官方 benchmark 或 AML 成绩。
生产 Add/Search 不导入下述 LLM 实验模块。没有常驻 Worker；加工和派生检索均需显式启用。

## 输入与提示词

仅接受有权加工和保存的公开／合成历史，不用于采集官方评测内容。
加工入口的 schema 不接受 question、answer、judge、owner 或 branch 等字段。
请从历史转换，不能将题目或评分内容塞进消息正文。

输入示例 `history.json`：

```json
{
  "data_policy": "synthetic",
  "user_id": "example-person",
  "messages": [
    {
      "source_id": "message-1",
      "source_version": "v1",
      "conversation_id": "conversation-1",
      "speaker": "人物A",
      "role": "user",
      "content": "昨天我去了上海，下个月准备搬到杭州。",
      "message_time": "2026-10-10T09:00:00+08:00",
      "timezone": "Asia/Shanghai"
    }
  ]
}
```

source_id 在输入内唯一，source_version 随原文更新。原文按自然顺序输入，
不同 conversation_id 不共享加工窗口。speaker、message_time、timezone 未知时填 null。
消息角色与正文人物分别表达；日期缺失时不补当前时间。长消息超出预算会明确失败，
不会静默截断。输入文件上限 64 MiB；较大的数据集按用户／独立历史拆分执行。

完整提示词位于 [`src/processing/prompt.py`](../src/processing/prompt.py)，
输出 JSON schema 位于 [`models.py`](../src/processing/models.py)。
模型不生成权限或幂等身份。提示词要求保留主体、否定、条件、数量、状态和歧义，
并引用原始消息。引用存在只是必要检查，不证明提取出的语义一定正确。

日期校验目前支持 ISO 日期、昨天／今天／明天、N 天前，以及上／本／下个月的中英文表达。
未支持的表达（例如有歧义的 last Friday）、缺少时间锚点或计算不一致时，
清除归一化日期，并将该条生成正文回退为原文引用。这样不会只清 metadata，
却在正文中留下错误日期。事件关联、主体推断仍需人工抽样检查。

## 三步执行

从 `agent_memory_leaderboard` 运行，安装已有 evaluation 可选依赖：

```sh
uv sync --locked --extra evaluation
```

1. 加工。通过安全的本地配置设置 `MEMORY_PROCESSOR_MODEL`、
   `MEMORY_PROCESSOR_BASE_URL`、`MEMORY_PROCESSOR_API_KEY`，不复用 Judge 凭据或隐式选择模型。

```sh
uv run python -m src.processing extract \
  --input history.json --output .private/processing/snapshot.json --enable-processing
```

可选环境变量：`MEMORY_PROCESSOR_ENCODING=o200k_base`、`MEMORY_PROCESSOR_INPUT_TOKENS=8192`、
`MEMORY_PROCESSOR_OUTPUT_TOKENS=4096`、`MEMORY_PROCESSOR_WINDOW_MESSAGES=12`、
`MEMORY_PROCESSOR_OVERLAP_MESSAGES=2`、`MEMORY_PROCESSOR_TIMEOUT_SECONDS=180`。
input_tokens 是完整输入请求的估算上限，需另为 output_tokens 保留模型上下文容量；
选择与模型相符的编码和预算。记录提示词 hash、模型配置指纹和全部预算，凭据不入文件。

每个窗口结束后原子保存；失败／部分完成会记录原因、HTTP 状态（若有）、未处理消息，
继续后续窗口。命令退出码 2 表示存在失败／未完成，不表示遇到第一处错误就停止。
加 `--resume` 复用完成窗口、重试失败窗口；输入或契约变化时拒绝续跑，应使用新输出路径。
没有自动模型重试循环。模型返回截断、非法 JSON 或越界来源均不会被认定为完成。

2. 发布到独立实验范围。使用现有 `MEMORIA_URL`、`MEMORIA_MASTER_KEY`、
   `MEMORIA_NAMESPACE` 配置。明确选择实验服务，使用专门的 experiment-id。

```sh
uv run python -m src.processing publish \
  --input .private/processing/snapshot.json \
  --output .private/processing/publication.json \
  --experiment-id example-offline-v1 --enable-processing
```

这一步复制原文并写入派生结果，两类数据使用不同的、程序生成的实验 owner；
原文 owner 由实验和历史内容固定，派生 owner 额外绑定加工结果版本。
不会写入既有 adapter 用户的数据范围。需要额外存储和 embedding 成本。
正常关闭派生检索时，派生数据不会进入原文候选集。memory_type 仍表达语义类型。

复用现有原子幂等写入及可检索确认；重复运行同一发布命令不会重复新增相同计划中的记录。
写入失败也会继续，并在完整发布清单中保留失败项；重跑同一命令可恢复。
原文未全部确认时拒绝搜索；加工未全部完成或派生未全部确认时，只返回原文。
上一个结果版本不会混入新版本检索。不同版本的旧实验数据不自动删除，
由操作者按 publication 中的精确范围另行清理。
输入 source_id 与 Memoria memory_id 是不同身份；通过 publication 中原文条目的
source_ids 和 memory_id 关联，不能将输入 ID 直接当作后端记录 ID。
存在相同发布清单时，会复用已确认条目，仅继续未完成的写入／确认。

已有原文检索上下文时，可给 publish 增加 `--derived-only`，只发布派生记录。
随后 search 使用 `--baseline-file baseline.json`；文件采用标准 SearchResponse
结构（`{"data":[{"id":"...","content":"..."}]}`），不包含问题或答案。
此模式复用同一批原文候选，输出记录基线 hash，不查询实验原文 owner。
只发布派生记录而未提供 baseline-file 时，search 会拒绝执行。

若部分加工窗口失败，默认仍回退全部原文。实验可显式传
`--allow-partial-extraction` 使用已经校验且发布确认的派生子集；输出标记
`partial_extraction`，报告必须单列加工覆盖率，不能声称已完成全量加工。
该选项不绕过发布确认。窗口失败不会让剩余窗口停止执行。

3. 检索对照。把同一个查询写入本地 `query.txt`，分别使用两个输出文件：

```sh
uv run python -m src.processing search \
  --input .private/processing/publication.json --query-file query.txt \
  --output .private/processing/raw-search.json --top-k 100 --token-budget 8192

uv run python -m src.processing search \
  --input .private/processing/publication.json --query-file query.txt \
  --output .private/processing/combined-search.json --top-k 100 --token-budget 8192 \
  --enable-derived-search
```

两种模式使用同一原文索引、top-k 和证据 token 预算。联合检索最多使用 top-k 的 30%
作为派生条目，按两条原文、一条派生交错，优先原文首条；这是固定的实验策略，
尚未证明最优。不会直接比较两个通道的相关性分数。
本实验按上面的通用历史结构重新渲染原文，A 是本工具生成的原文基线；
应在本工具内做同条件 A/C 对照，不能直接与先前 benchmark runner 的旧分数相减。
保留完整记录的预算内前缀，超出预算不截断原文。返回数可能少于 top-k；
当预算容纳不了首条时可以为空。每条派生内容附有原文引用、说话人和消息时间。

派生通道失败时返回原文并记录 `derived_status=failed_raw_fallback`；
原文失败会显式失败。Search 不调用加工模型。两次召回目前为串行，可能重复 query
embedding，因此要实测时延，不能宣称无额外成本。

搜索文件含 `data[].content`、channel 和 source_ids，以及时延、候选数量、快照身份。
可把 content 列表交给现有回答／评分器，固定相同回答提示词、模型和评分方式。
此入口只产出检索结果，不自动运行回答或评分，也未接入现有全量 benchmark runner。
下游回答模型的完整提示词、问题及输出额度需要另外预留；这里的 token_budget 仅约束证据。

## 开关、文件和实验边界

- extract/publish 未传 `--enable-processing` 时不读取凭据、不访问网络、不写结果。
- search 未传 `--enable-derived-search` 时只查询原文，不做“混合召回后过滤”。
- 文件采用 UTF-8 可读 JSON、原子替换、0600 权限；控制台只输出计数／固定错误码。
  原始历史、派生记忆及检索结果仍是内容文件，应留在私有工作目录，不能提交仓库。
- 同一输出文件只允许一个写入进程。锁文件不删除，避免并发创建不同锁 inode。
- 先检查少量独立合成样例，再用按历史分组的留出样本比较修正、回归、加工错误和成本。
  只展示获益错题不足以证明有效；没有更简单方案对照时，不宣称其优于排序或邻接补全。
- observe 对照按接入成本决定，不是前置条件。常驻 Worker、在线接入、生命周期联动
  和图能力不在本轮实现范围。启用实验开关不等于启用正式评测的二次加工。

容器内需要执行实验命令时，构建时传 `--build-arg INSTALL_EXPERIMENT_DEPS=true`。
依赖锁定在 requirements-experiment.lock；默认镜像仍只安装 adapter 运行依赖。
