# AML 文本赛道：共建方案与工作计划

## 工作范围

团队在 [matrixorigin/memory_benchmarks](https://github.com/matrixorigin/memory_benchmarks) 共建参评适配、评测和部署工程，基于同事现有实现继续开展工作。本文是后续方案与设计决策的维护入口；更新有效章节，区分已实现、待实施和验证证据，不记录中间讨论。

根目录 [README](../../README.md) 是新成员的项目入口，沿用英文，集中说明用途、当前架构、
公网 Add/Search 最小示例、手动评测及部署步骤、数据集支持范围和指标语义。
运维凭据／发布细节由 `deploy/README.md` 维护，评测参数／产物／保留规则由
`benchmarks/CI.md` 维护，本文维护设计及验收状态。首页支持表区分可执行检索、接口诊断、
未取得数据和未接入原生评分；不以数据集目录存在或 workflow 成功表述正式 AML 分数。

此前独立的 Memoria `feat/aml-text-adapter` 分支、`aml-local-client` 和相关优化／评测计划停止推进。已有代码、PR、文档和结果保留为参考，不删除或关闭；不以完成旧计划或迁入旧实现作为新项目的前置条件。后续是否采用其中的代码或用例，由新项目的具体任务决定。

## CI/CD 交付概要与 Review 入口

CI/CD、Memoria 部署模板和自动证书配置已合入 `memory_benchmarks` 的 `origin/main`。
**Memoria 与适配器已部署到阿里云 dev，公网 HTTPS 和真实 Add/Search 最小联调通过。**
具体运行、实际 SHA／digest 和剩余验收范围见[当前交付状态](#当前交付状态)。
部署操作、Actions 参数及可复制的公网 curl／Python 示例见
[发布与接入说明](../../deploy/README.md#发布流程)和[Add/Search 接入](../../deploy/README.md#接入已部署的-addsearch)。

### 完成状态

| 部分 | 代码／配置状态 | 已执行的工作 | 待验证／待完成 |
|---|---|---|---|
| 离线 CI | 单个 `checks` job 执行 Ruff、pytest、Helm lint／template、适配器镜像构建、Go Pulumi 检查；八个健康探针用例；不构建 Memoria | 已有 CI 运行通过，pytest 为 154 passed、1 warning；Helm 3.17.4 两组检查及适配器镜像构建通过 | 当前提交以 PR checks 为准；CI 不推送镜像或部署 |
| benchmark workflow | 已编写手动选择 LoCoMo／LongMemEval-S、固定数据校验、Summary 与 artifact；复用已有执行程序 | 来源 revision、大小与 SHA256 已获取，未调用线上 benchmark | 首次 Actions 小集运行及质量基线待验收；无 Answer/Eval |
| dev CD | main 手动 preview／deploy、冻结检查、单项更新选择；自动部署默认关闭 | 首次完整 CD 成功，两个镜像推送和 Pulumi up 通过 | 真实单项更新保留效果仍待验收 |
| Memoria 部署 | 源码／现成镜像模式、分支选择、版本记录及数据库／embedding 配置 | 源码模式部署通过，实际 Pod 在非 root／只读根文件系统下 Ready | 现成镜像模式与重启恢复仍待验收 |
| Add/Search 部署 | Deployment、Service、journal PVC 和 `/ready` | PVC Bound；公网真实 Add/Search 七项最小联调通过 | 持久性、未知写入恢复及容量仍待验收 |
| 数据库 | 专用用户／角色／共享库初始化及动态用户库授权 | 权限边界、共享及用户 schema 初始化、真实写入／检索通过 | 恢复及保留期限／物理清理仍待验收 |
| GitHub 配置 | 运行凭据／环境变量配置，上海 embedding key 为必填 | 本次部署验证集群、Pulumi、ACR、数据库及应用认证链路 | 未来凭据轮换与权限变更重新验收 |
| HTTPS | Ingress、Certificate 和证书 Ready 等待 | DNS 与入口匹配，证书 Ready，公网健康检查和 Add/Search 通过正常 TLS 校验 | 真实 SLB 长请求超时仍待验收 |

已有完整构建验证见 [Actions run 37452656686](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37452656686)，对应 `c54dc53`，2026-10-06 检查和当时的 Memoria 源码构建 job 均通过，pytest 为 154 passed、1 warning。当前离线 CI 只保留 `checks`，Memoria 源码构建由发布 workflow 执行，后续提交的验证以对应 PR checks 为准。上述离线记录本身不证明上线；实际发布证据见当前交付状态。

交付范围包括 `.github/`、`deploy/`、`/ready`、测试和本目录方案文档；后续变更继续通过 PR
交付到 `origin/main`。实际 dev 配置、凭据和本地运行产物排除提交。

### 源码导航

| 入口 | Review 内容 |
|---|---|
| [离线 CI](../../.github/workflows/aml-ci.yml) | 触发条件、依赖锁定、检查／构建步骤和独立于发布凭据的运行范围 |
| [dev CD](../../.github/workflows/aml-dev-deploy.yml) | 分支限制、并发、Secret 使用、镜像 digest、preview／deploy、证书等待及临时文件清理 |
| [Memoria 来源配置](../../deploy/memoria-source.json)、[镜像选择与记录](../../deploy/scripts/memoria_image.py)、[构建 Dockerfile](../../deploy/docker/memoria.Dockerfile) | 默认 main、分支／tag／commit 选择、实际 SHA 与 digest、编译器／运行镜像和构建缓存 |
| [Pulumi 程序](../../deploy/pulumi/main.go)、[配置模板](../../deploy/pulumi/Pulumi.new-dev.example.yaml)、[私有配置生成](../../deploy/scripts/dev_config.py) | 目标集群、私有标识、独立 stack、资源所有权、DSN 编码、Secret 传播、PVC 保护与 Helm 依赖 |
| [更新范围选择](../../deploy/scripts/deployment_target.py)、[Pulumi 配置选择](../../deploy/pulumi/selection.go)、[选择回归用例](../../deploy/tests/test_deployment_target.py) | 首次完整发布、未选应用版本／配置保留、共享凭据与 chart 变动检查、构建后复查 |
| [Memoria 模板](../../deploy/chart/templates/memoria.yaml) | 镜像入口、非 root／只读文件系统、显式多库、共享库连接与授权、模型环境变量及健康探针 |
| [适配器模板](../../deploy/chart/templates/deployment.yaml)、[Service](../../deploy/chart/templates/service.yaml) | 单副本 Recreate、journal 挂载、Secret checksum、请求结束与重启行为 |
| [Ingress](../../deploy/chart/templates/ingress.yaml)、[Certificate](../../deploy/chart/templates/certificate.yaml) | NGINX 路由、长请求配置、TLS Secret 名和现有 ClusterIssuer 的引用 |
| [集群检查](../../deploy/scripts/preflight.py) | API Server／UID 校验、权限与存储检查、域名范围和 Issuer Ready 条件 |
| [数据库初始化](../../deploy/scripts/bootstrap_db.py) | 管理员配置解析、专用用户／角色的权限、重复执行和凭据保留 |
| [GitHub 配置脚本](../../deploy/scripts/configure_github.py) | 固定仓库和目标集群、stdin 上传及错误输出中的凭据保护 |
| [适配器源码](../src/app.py) | 新增 `/ready` 的开销、失败行为和健康状态语义 |
| [冻结检查](../../deploy/scripts/deployment_guard.py)、[Helm 渲染](../../deploy/scripts/render_chart.py)、[ready 用例](../tests/test_ready.py) | 冻结输入校验、两组非秘密渲染配置，以及健康／失败返回码 |
| [发布操作说明](../../deploy/README.md) | 必填配置、执行步骤、外部依赖和验收流程 |

Review 使用源码、部署模板、非秘密配置及已记录的执行证据。管理员 `.env`、`deploy/.runtime.env` 和 kubeconfig 包含真实凭据，不纳入方案或 Review 产物。

### Review 重点

1. **工作流能否执行**：Actions 表达式、依赖／版本、Go 编译、镜像构建、digest 传递和 Pulumi 参数是否一致。离线 CI 及首次完整 CD 的镜像发布、digest 传递和 Pulumi 执行已通过，实际版本见当前交付状态。
2. **发布范围是否正确**：只操作指定 unit 集群及独立 stack；集群身份检查、应用 namespace、共享资源引用和删除保护是否完整。
3. **Memoria 能否启动并供适配器调用**：固定镜像是否具备普通 `/v1/memories` 路径所需的 owner、metadata 和 query 能力；运行参数、schema 初始化、数据库角色权限和 embedding 环境是否满足该版本。
4. **状态与恢复是否可靠**：SQLite 单写者、PVC 绑定／权限、Recreate、发布中断、凭据更新及已有 journal 的保留；确认 `protect` 不等价于备份或恢复方案。
5. **公网链路是否成立**：域名、DNS solver 范围、Certificate／Secret／Ingress 关联和 Ready 等待；SLB 与 NGINX 的真实长请求限制仍需验收。
6. **验证边界是否清楚**：`/ready` 只覆盖 journal 可读及上游健康接口；新数据库登录成功不证明 Memoria 迁移或 Add/Search 可用。既有比赛质量／幂等问题按下文独立跟踪。

### 部署架构

```mermaid
flowchart LR
    AML["AML 客户端"] -->|HTTPS| NG["现有 NGINX 与应用 Ingress"]
    subgraph APP["已部署：aml-memory-benchmarks namespace"]
        A["Add/Search：aml:80"] --> M["Memoria：aml-memoria:8100"]
        A --> J["journal PVC：20Gi"]
        C["应用 Certificate 与 TLS Secret"]
    end
    NG --> A
    M --> DB["现有 MatrixOne：专用共享库与动态用户库"]
    M --> EMB["上海 BAAI/bge-m3 服务"]
    CM["现有 cert-manager 与 ClusterIssuer"] --> C
    C --> NG
```

Memoria 默认从源码 `main` 分支构建，由本项目 CD 部署，也可指定分支、tag、commit 或现成镜像；源码构建、镜像推送、dev 部署及公网 Add/Search 最小联调已通过，未修改 Memoria 源码。数据库引擎、模型服务、NGINX 和证书控制器是既有基础设施。详细设计和资源边界见下文，具体配置与操作见发布说明。

## 当前工程与职责

本次核实的同事仓库版本为 `main@b037c42`。

| 部分 | 职责与当前状态 |
|---|---|
| `memory_benchmarks/agent_memory_leaderboard` | 已有 FastAPI Add/Search、SQLite 幂等 journal、隐私保护、断点执行、多数据集解析和检索诊断 |
| Memoria | 作为记忆存储与检索后端；当前适配器调用普通 `/v1/memories` 接口 |
| 本地质量评测 | 当前仅有证据召回诊断；Answer/Eval、预算内证据及可信质量基线待建立 |
| AML 平台 | 调度参评接口并统一回答、评分；本次没有运行官方评测 |

以现有 FastAPI 适配器为后续工作基础。协议适配、数据集执行、评测和报告在本项目维护；只有新项目确认需要后端能力或修复时，才安排相应 Memoria 修改。当前并未切换到此前 Memoria `/aml/*` 路由，也未迁入其来源策略、原子幂等或邻接补全。

## 已确认问题与实施顺序

详细事实、条件与 review 表述修正见 [Issue #1 核实结论](review-issue-1.md)。该文档是核实记录，不代表其中建议已实施。

1. **接收实际运行材料**：确认同事使用的 Memoria commit／镜像、模型与配置、失败状态及完整性汇总。私人结果被 gitignore 排除，clone 缺少结果不能证明从未运行。
2. **保证输入与写入可信**：隔离模型可见的诊断 ID／答案来源标签；核查来源时间触发的过滤；处理不确定且未提交写入的恢复，以及 token／字节边界。需逐项确认实现位置与验收条件，不通过等待后盲目重发保证幂等。
3. **建立本项目质量闭环**：增加 Answer/Eval、Recall@K、预算内证据、失败与时延汇总；在真实 Memoria 和 embedding 环境建立固定小集基线。按数据集选择适用评分规则；模型可见输入不含 gold 或诊断标签。
4. **按失败原因做实验**：一次改变一个因素，对照粒度／邻接／排序，再决定 options、rerank 或 query rewriting。检索到更多内容、填满预算或具备更多模块均不能单独作为质量提升证据。
5. **完成容量与正式验收**：测量写入吞吐，核查批量接口语义与重试，验证部署、认证、数据清理和公网可达性；核对组别与冻结契约，再安排官方 Smoke／Full。

## 评测与方案边界

- 旧 Memoria 分支的 LoCoMo 150 题和七维开发结果属于旧路径与已记录镜像，不作为本项目成绩。采用旧用例或工具后也须通过本项目重新运行。
- 同事 README 记录过无 embedding 的真实 HTTP smoke，且存在重复运行失败；尚不能据此认定 embedding 质量、稳定性或正式部署完成。
- 来源时间、接收时间与事件时间须区分。普通 Memoria 路径中的年龄过滤可能使历史来源无法通过 Add 探针；最终处理设计在对应修复任务中明确，不能仅靠返回后重排解决。
- 超时不证明未提交，回查暂时为空也不证明旧调用不会迟到提交。写入恢复必须说明幂等边界和重复处理。
- 自动 Judge 需保留逐题证据与争议，不能用一个未经验证的裁判分数代替可信质量结论。
- 正式模型、预算、排序、重试和配额按 AML 所选组别的有效契约核对；开发配置和自建 Answer/Eval 不自动等价于官方评测。

## 运行与发布方案

### 部署范围

**部署目标是阿里云 dev unit 集群，CI/CD 全部维护在 `memory_benchmarks`。** 本地 Compose 继续用于开发。本项目部署专用 Memoria 和 Add/Search 适配器，使用独立数据库登录用户与专用数据库；公网由目标集群现有 NGINX Controller 和 Kubernetes Ingress 接入。生产 `memoria-website` 的 Memoria／MatrixOne 不作为评测后端。

| 位置 | 当前职责 |
|---|---|
| `memory_benchmarks/agent_memory_leaderboard` | 维护 FastAPI Add/Search、幂等 journal、数据集执行、评测和报告 |
| `memory_benchmarks/.github/workflows/aml-ci.yml` | 离线 lint、既有单元／契约测试和 Docker 构建检查；不推送镜像或触发部署 |
| `memory_benchmarks/.github/workflows/aml-dev-deploy.yml` | 阿里云 dev 手动 preview／deploy；main 自动部署由显式开关启用 |
| `memory_benchmarks/deploy` | 独立 Go Pulumi project、Helm chart、集群检查及一次性数据库初始化脚本 |
| 阿里云 dev unit | 运行独立 Memoria 和适配器；复用现有 MatrixOne、CSI 与 NGINX |
| 本地 Memoria 和 MatrixOne | 独立评测后端；通过已有 Compose 或明确配置的开发实例运行 |
| `memoria-website` | 继续承担生产网站与原服务的职责；本项目没有新增发布流程或共享 Ingress 路由 |

### 阿里云环境与资源所有权

2026-10-06 通过 gitops 源码、Pulumi 已保存状态及用户提供的 `~/dev/unit-kubeconfig` 核对环境：

| 项目 | 已确认事实／选择 |
|---|---|
| gitops `new-dev` | 多个 Pulumi project 的环境名；基础设施、组件、cloud-service 和 cos-resources 各自管理独立 stack |
| 控制面集群 | `cos-infrastructure/new-dev` 导出 `controller-plane-clusterID`；`cloud-service/new-dev` 已保存状态记录 `moi-main/moi-memoria`，属于 Astra 共用后端 |
| 本项目目标 | 用户提供的 **unit 集群**，与上述控制面不同；API Server 和 `kube-system` UID 固定在本项目 dev 配置中 |
| 目标集群实况 | Kubernetes `v1.28.9-aliyun.1`，19 个 amd64 节点；本次未发现 Memoria Deployment |
| 公网入口 | 现有 `nginx` IngressClass，`kube-system/nginx-ingress-lb`，当前公网 IP `47.97.230.53` |
| 持久存储 | 已确认 `alicloud-disk-essd-pl0`，WaitForFirstConsumer；journal 使用 20Gi RWO PVC |
| 数据库服务 | dev `freetier-01`，有 `proxy:6001` Service；运行 DSN 配置为 `proxy.freetier-01.svc.cluster.local:6001` |

采用 **`MatrixOneCloud/memory-benchmarks/new-dev`** 独立应用 stack，namespace 为 `aml-memory-benchmarks`；该 stack 尚未初始化。首次 preview／deploy 使用 upsert 创建 stack。该 stack 管理 Memoria、适配器、运行／拉取 Secret、journal PVC、Service、可选 Ingress 和应用 Certificate。集群、数据库引擎、NGINX、公网负载均衡器及 cert-manager／ClusterIssuer 由现有基础设施／运维管理。应用 workflow 直接部署本项目，不触发整套 gitops `cloud-service/new-dev` 发布，不接管共享资源。

### 数据库、认证与模型

现有 dev MatrixOne 租户内使用独立 **SQL 登录用户** `aml_memoria`、角色 `aml_memoria_role` 和共享库 `aml_memoria`。运行用户名显式选择该角色；不新建整个 MatrixOne account。管理员凭据仅用于本地初始化。角色拥有共享库及其表的权限，并具有 `CREATE DATABASE ON ACCOUNT *`，以支持 Memoria 动态创建用户库。新库及表由创建时的运行角色拥有，不授予 `accountadmin`、全局表读写或租户级删库权限。权限模型见 [MatrixOne GRANT](https://docs.matrixorigin.cn/en/v26.3.0.13/MatrixOne/Reference/SQL-Reference/Data-Control-Language/grant/)。

专用用户、角色和数据库已创建，并通过该用户完成登录及库名回查。运行密码、独立 Memoria master key 和适配器 API key 位于 gitignore 排除的 `deploy/.runtime.env`，权限 `600`。管理员配置保留在用户指定文件，仅用于一次性初始化，不进入 Pod 或持续发布 Secret。数据库初始化不删除既有数据、不重设既有用户密码；恢复使用同一份凭据文件。

Memoria 内部 Service 为 `aml-memoria:8100`；适配器使用该 Memoria 的 master key，并为 AML 用户映射稳定 owner。master key 与 AML 对外 API key 相互独立；向 AML 平台提供适配器 key。调用者携带 `Authorization: Bearer <AML_ADAPTER_API_KEY>` 并提交 `user_id`；适配器对 `[namespace, user_id]` 做稳定哈希，以内部 `Memoria-Owner` 认证和 `X-User-Id` 调用 Memoria。无需预注册 Memoria 用户或逐用户生成 API key；首次需要用户存储时由 DbRouter 按需创建用户库。SQL 运行用户仍须由部署初始化脚本预先准备。Add/Search 应保持相同 `user_id`；重试不能改变 owner 前缀或用户标识。

接入说明以公网 Add/Search 为最小客户端边界，提供 curl 和 Python 标准库示例、请求字段及
响应结构。普通客户端只持有适配器 key，不持有 Memoria master key、数据库或集群凭据。
示例使用随机用户隔离演示数据，不自动清理；完整 `src.smoke` 才额外访问内部 Memoria，
执行认证范围核对和测试记忆逻辑停用。示例用于接口联调，不替代正式 AML 重试、超时和评测契约。

#### 数据库模式与部署兼容性

源码基线 `matrixorigin/memoria@e180dff` 的自动迁移是既有设计：[PR #178](https://github.com/matrixorigin/memoria/pull/178) 实现旧配置的拓扑检测与迁移，[PR #188](https://github.com/matrixorigin/memoria/pull/188) 明确将全新环境切到多库，并包含对应测试。比赛工程应适配这一设计，不修改 Memoria 启动语义来维持当前模板的单库假设。

| 控制项 | 当前上游语义 |
|---|---|
| `MEMORIA_MULTI_DB=1/true` | 直接按多库启动，跳过旧库自动检测／迁移 |
| `MEMORIA_MULTI_DB=false/0` 或未设置 | 进入自动拓扑检测；不构成强制单库开关 |
| `DATABASE_URL` | 自动检测时的旧库／初始库连接 |
| `MEMORIA_SHARED_DATABASE_URL` | 多库共享库连接；未设置时从 `DATABASE_URL` 推导为 `memoria_shared` |
| 数据库现有状态 | 全新环境进入多库；已迁移环境直接使用多库；旧数据环境先迁移再切换 |
| `MEMORIA_DB_NAME` | 数据库名称配置，不是强制单库开关 |

已核对 `memoria-website/memoria_aws` 的部署模板：显式设置 `MEMORIA_MULTI_DB=1`，并将 `DATABASE_URL` 和 `MEMORIA_SHARED_DATABASE_URL` 配成同一共享库连接。该事实来自部署源码，不代表本次读取了线上 Pod 的实际配置。

部署显式设置 `MEMORIA_MULTI_DB=1`，`DATABASE_URL` 和 `MEMORIA_SHARED_DATABASE_URL` 引用同一 Secret 中的专用 `aml_memoria` 连接，避免使用默认 `memoria_shared` 或触发旧库迁移。Memoria 根据 owner 派生 `mem_u_<16位hash>` 用户库；SQL 连接持续使用专用角色，由它创建库、初始化表并获得对象所有权。初始化脚本可幂等补齐建库授权，保留现有运行密码和 API key。

**权限边界：** `CREATE DATABASE` 是租户级建库能力，无法仅授权 `mem_u_` 前缀。该角色可以新建其他名称的数据库；namespace／owner 哈希用于应用数据分区，不构成 SQL 名称限制。现有非授权业务库的数据仍应不可访问，计算容量和租户资源仍共用。需要硬性租户隔离时应另建 MatrixOne account；本方案不宣称已有完整租户隔离。

2026-10-06 已在指定 dev MatrixOne（报告版本 `v4.2.0`）幂等补齐授权，保留原凭据。使用专用角色验证共享库 `CREATE DATABASE IF NOT EXISTS`、新建临时用户库／表、CRUD、ALTER、索引和自有库删除；读取、更新及删除管理员角色创建的另一个临时库均被拒绝（错误码 `20101`）。仅使用随机命名的测试库和合成数据，结束后已清理，未读取其他业务库。该检查证明本次所测 SQL 操作和权限边界，不代表所有 Memoria 功能均已验证。

当前实际镜像的共享及用户 schema 初始化、首次 Add/Search 已通过真实联调，重启恢复仍须验收。权限检查不替代 HTTP 路径、embedding 或质量评测。本项目只调整比赛部署配置和初始化授权，不修改 Memoria 的数据库路由或启动语义。

embedding 默认沿用 gitops dev 配置：上海 OpenAI-compatible `BAAI/bge-m3`、1024 维。其网络、认证、预算及真实检索质量需要联调确认；当前发布配置不要求 LLM 或 rerank，实际 Add/Search 路径没有调用 Answer/Eval 或 Observe 抽取。镜像均固定 digest，不混用不同 embedding 模型的既有向量。

上海 embedding 的公网接口需要鉴权。2026-10-06 从本机以正常 HTTPS 校验向 `https://bgem3.model.shanghai.idc.matrixorigin.cn:30443/v1/embeddings` 发送单条短文本、`model=BAAI/bge-m3` 的无 key 请求，返回 HTTP `401`、`{"error":"Unauthorized"}`，约 0.071 秒。结论限定为该接口不接受匿名调用，尚未验证有效 key、1024 维输出或 dev Pod 网络。发布使用 `AML_EMBEDDING_API_KEY`，由服务维护者提供有效 key；workflow 和 Pulumi 都要求其非空，缺失时不继续发布。模型调用仍需带 key 验收，`/ready` 不替代该验收。

### CI/CD 凭据边界与配置

离线 CI 无须手工配置 Secret，也不 checkout Memoria；发布 workflow 的公开 Memoria 源码 checkout 使用 Actions 自动提供的 `GITHUB_TOKEN`。dev preview／deploy 均需要八个必需凭据：Repository Secrets `KUBECONFIG`、`PULUMI_TOKEN`、`AML_DATABASE_PASSWORD`、`AML_MEMORIA_MASTER_KEY`、`AML_ADAPTER_API_KEY`、`AML_EMBEDDING_API_KEY`，以及本仓库可访问的 Organization Secrets `NEW_ACR_USERNAME`／`NEW_ACR_PASSWORD`。来源、填写方法和当前准备状态统一列于 [发布说明的 Secrets 表](../../deploy/README.md#secrets)。

`KUBECONFIG` 存储用户提供的阿里云 unit 完整原始 YAML，不存本机路径或 base64；workflow 写临时文件供 Kubernetes provider 使用，结束时清理。`PULUMI_TOKEN` 映射为 `PULUMI_ACCESS_TOKEN`，只用于独立应用 stack 的 Pulumi Cloud 访问。ACR 凭据用于镜像推送并生成 namespace 内的拉取 Secret。数据库密码、master key、对外 adapter key 和 embedding key 进入 `aml-runtime`，按需供各 Pod 使用；数据库运行 DSN 使用专用用户，由 Pulumi 编码和生成。管理员数据库密码不进入持续发布。

环境标识使用 `AML_EXPECTED_API_SERVER`、`AML_EXPECTED_CLUSTER_UID`、`AML_DATABASE_ACCOUNT` 三个 Repository Variables，已从现有私有配置上传。仓库只提交 `Pulumi.new-dev.example.yaml`；本地实际 `Pulumi.new-dev.yaml` 被 gitignore 排除。CD 生成同一格式配置，preflight、初始化和凭据上传脚本继续读取该文件，保留集群及租户身份检查，不增加 Secret 数量。公开前仍须按基础设施披露要求检查文档和脚本中的其他环境信息。

2026-10-06 GitHub 名称元数据显示六个必需 Repository Secrets 均已存在，包括用户配置的 embedding key，两个组织 ACR Secrets 已对本仓库开放；当前没有缺失的必需 Secret 名称。这里只核对名称元数据，不读取密钥值，不等价于有效性或权限验证。`AML_ADAPTER_REPOSITORY`／`AML_MEMORIA_REPOSITORY` 均配置为共享 `moc-pub/plugin` 仓库，供现成镜像模式使用的 `AML_MEMORIA_IMAGE` 也已保存；源码模式与 main 默认值来自已编写的来源配置，其余非秘密参数按发布说明填写。

当前应用 Pulumi 只操作 Kubernetes，复用已有证书控制器与 Issuer，不将阿里云 RAM AccessKey、DNS 签发凭据或证书私钥加入本仓库 CI。DNS 凭据由基础设施管理。当前 Add/Search 发布不需要 LLM／rerank key，也不自动调用官方评测。正式参评时提供给 AML 的认证值是 adapter key，上游 Memoria master key 在内部使用。

### Kubernetes Ingress 公网方案

访问链路为 **AML → HTTPS 域名 → unit 集群 NGINX → 适配器 Service → 内部 Memoria**。本项目新增自己的 Ingress，使用 `nginx` 类；公共路由覆盖 `/add`、`/search`，对 Memoria 保留 ClusterIP 内部入口。

证书自动化复用现有 **cert-manager + `aliyun-prod-letsencrypt` ClusterIssuer**。已核实 cert-manager `v1.12.1` 的 controller、webhook、cainjector 均有可用副本，生产和 staging Issuer 均为 Ready；应用 CD 不安装或升级共享控制器，旧版本维护由基础设施负责人处理。Issuer 的 DNS-01 webhook 配置范围为 `cn-dev.matrixone.tech`，应用域名需位于该范围；其他域名需要提供对应的有效 Issuer。

启用公网时设置明确域名，并配置 DNS 指向 NGINX 公网地址。chart 声明专用 Certificate，cert-manager 自动申请、保存及续期 TLS Secret；发布等待 Certificate Ready。证书私钥由集群控制器管理，CI/CD 不负责重复签发或更新该 Secret。Let’s Encrypt 当前默认有效期为 90 天，足以覆盖一个月比赛；以真实 `notAfter` 为准并保留自动续期。[cert-manager DNS-01](https://cert-manager.io/docs/configuration/acme/dns01/)，[证书有效期](https://letsencrypt.org/docs/cert-lifetimes/)

公网域名选定为 `aml.cn-dev.matrixone.tech`，GitHub Repository Variable `AML_PUBLIC_HOST` 已设置，A 记录已配置。部署后的公开入口为 `https://aml.cn-dev.matrixone.tech/add` 与 `https://aml.cn-dev.matrixone.tech/search`。应用 Ingress 与证书已创建，正常 TLS 校验下的公网健康检查和 Add/Search 最小联调已通过。[Kubernetes Ingress](https://kubernetes.io/docs/concepts/services-networking/ingress/)

DNS 使用独立的 `cn-dev.matrixone.tech` 阿里云解析区域，已核对其 NS 为 `ns1.alidns.com`／`ns2.alidns.com`。域名管理员已配置 `aml` 主机的 A 记录。2026-10-06 使用公共解析器 `223.5.5.5` 的 TCP 查询返回 `NOERROR`、`47.97.230.53`、TTL 600 秒；同次只读核查的 unit NGINX LoadBalancer IP 也为 `47.97.230.53`。后续入口 IP 变化时维护该区域的记录，不在父域的其他解析系统中创建替代记录。

现有应用 CD 管理 Ingress 和 Certificate，cert-manager 管理 DNS-01 验证及证书续期；它们不会自动创建业务 A 记录。当前未发现 unit 集群的 ExternalDNS Deployment，A 记录由域名管理员一次性配置并维护，DNS 管理凭据不进入本项目 CI。阿里云也支持通过有权限的 CLI／API 或既有基础设施流程创建记录。[阿里云 DNS API](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-adddomainrecord)

Ingress 配置 1800 秒读／发送超时，并关闭上游自动重试。正式参评前还需核查 SLB、NGINX 控制器限制、请求体大小与整批 Add 的真实时长；不能据此声明整个链路已满足所有 AML 30 分钟请求。本项目不将控制面 `mocloud-alb` 的路由或监听器配置复制到 unit 集群。

### 本地运行与配置

现有 [Docker Compose overlay](../compose.adapter.yaml) 接入 Memoria 自身的 Compose 网络，将适配器绑定到 `127.0.0.1:8000`，通过 `http://api:8100` 调用本地后端。启动方式见 [项目 README](../README.md#docker-deployment-with-memoria)。本次没有启动本地服务或执行真实 Add/Search；运行前核对实际 `.env` 中的 Memoria URL、数据库地址及模型配置。

| 配置 | 用途 |
|---|---|
| `MEMORIA_URL` | 指向评测专用 Memoria，不使用生产端点 |
| `MEMORIA_MASTER_KEY` | 上游认证；沿用评测实例自身的配置，不能复用生产凭据作为默认值 |
| `ADAPTER_API_KEY` | 独立适配器认证，客户端用于调用 Add/Search |
| `ADAPTER_STATE_DIR` | 持久保存 journal 和锁文件；Compose overlay 使用 `/app/var` 命名卷 |
| `MEMORIA_NAMESPACE` | 稳定的评测 owner 前缀；同一次运行／恢复不随意更改 |
| `MEMORIA_TIMEOUT_SECONDS` | 当前默认单次上游调用 120 秒，不代表整批 Add 的总时长 |
| Memoria embedding／LLM 配置 | 配置于评测后端；质量联调与正式参评分别核对模型要求，不混用不同模型的既有向量 |

`/health` 为异步轻量路由，直接检查事件循环存活，不占用 Add/Search 的同步线程池。`/ready` 检查 journal 可读和 Memoria `/health`，使用独立 HTTP 连接与线程容量；每个 worker 最多一个检查，并发探针直接返回 503，不排队，也不写入记忆或调用模型。ready 不能证明 master key、embedding、Add/Search 和重试恢复正确；这些能力需要真实联调验收。

### 离线 CI

PR、main push 或手动触发 **AML offline CI** 时，执行依赖锁定安装、Ruff、pytest、固定版本 `actionlint v1.7.7` 的 workflow YAML／Actions 表达式检查、Helm lint／template、适配器镜像构建及 Pulumi 程序编译／测试，统一在 `checks` job 中执行；适配器镜像不推送到 registry，离线 CI 不 checkout 或构建 Memoria。Helm 检查使用假镜像 digest、模型 URL 和域名，覆盖内部入口／单 worker，以及公网 Ingress／Certificate／两个 worker。workflow 不引用集群、Pulumi、ACR 或跨仓库发布 Secret，不自动运行真实 HTTP smoke、LLM 基准或官方 Smoke／Full。这里的离线指不调用真实业务／评测接口，依赖安装和本仓库 checkout 仍需要网络。

Memoria 是独立来源仓库，日常适配器、评测或文档变更不重复编译它。其源码构建只在
**AML Aliyun dev deployment** 选择更新 `both`／`memoria`、且有效模式为 `source` 时执行；
`preview` 和 `deploy` 均构建并推送。仅更新适配器或使用现成镜像时跳过。
源码构建成功并取得 digest 后才继续发布，构建失败阻止更新。若显式启用 main push
自动发布，则该发布运行也遵循此规则；普通合并触发的离线 CI 不构建 Memoria。

### 手动 benchmark workflow

独立 `.github/workflows/aml-benchmark.yml` 使用 GitHub hosted runner 执行已有
`src/benchmark.py`，手动选择 LoCoMo-Refined 或 LongMemEval-S。默认前 1 个完整历史，
允许 1–10 个历史和 Top K 1–100；不自动随 PR、push、部署或定时任务运行。
工作流仅接受本仓库 main，固定调用当前 dev 公网入口；唯一业务凭据为已配置的
`AML_ADAPTER_API_KEY`，映射为 runner 的 `ADAPTER_API_KEY`，仅在执行步骤注入。
不重新部署应用，不要求 SQL、master key、kubeconfig、Pulumi 或 LLM key。

两套来源固定 revision、原始文件大小和 SHA256，先下载／验证再调用服务。
`benchmarks/ci-datasets.json` 维护可扩展来源注册；新数据集必须补齐数据准备与指标语义，
已有解析入口不代表已具备原生质量评分。后端版本说明由操作者填写并明确标注未自动
验证，报告同时保存 benchmark commit、数据版本、选择范围和 Top K。

Summary 展示执行状态、完整／部分进度、耗时、证据召回率、全部证据命中率、空结果、
Search P50／P95 和原始 category 分组；未知指标显示 N/A，有证据标签的问题才进入召回率
分母。LoCoMo 使用消息证据，LongMemEval-S 使用 session 证据；不跨数据集直接排名，
category 也不映射为 AML 七个能力维度。当前不生成答案、不提供 Token F1／BLEU-1 或官方
AML 分数，未设置无基线支持的质量门槛。

报告生成和上传在失败时也执行，artifact 30 天保存 checkpoint、版本及证据 ID／hash，
不上传原始数据或检索正文；硬取消／整个 job 超时的最终步骤仅尽力执行。
执行步骤上限 120 分钟、job 160 分钟，与 CD 使用相同并发组且不取消正在运行的任务；
不自动修改 AML_FREEZE。并发组不控制外部客户端／官方评测，默认 pending 队列也不保证
保留多个待执行任务。第一版无跨 Actions run 自动续跑；重新触发生成新 UUID，原数据
按既有保留期限管理，artifact 过期不代替后台物理清理。
首次小集 Actions 运行尚未验收；使用步骤和指标定义见[benchmark 说明](../benchmarks/CI.md)。

### dev CI/CD

发布流程选择 Memoria 来源并在源码模式下 checkout、记录实际 commit；核对 kubeconfig 的 API Server、集群 UID、权限和 StorageClass，再运行离线检查、构建及推送适配器与所选 Memoria 镜像，然后 preview／up 独立 Pulumi stack。benchmark workflow 手动入口仅接受本仓库 main 分支，Memoria 来源 ref 可以独立选择；main push 自动部署默认关闭，通过 `AML_DEV_CD_ENABLED=true` 显式开启。preview 会发布镜像并可能初始化空的 Pulumi stack，但不创建 Kubernetes 应用资源。发布任务串行执行，不中断正在进行的 dev 发布。

#### 独立应用更新

手动发布增加 `deployment_target=both|memoria|memory_benchmarks`，默认 `both`。
`memory_benchmarks` 指本项目提供的 AML Add/Search 适配器。首次部署选择 `both`；
后续可单独更新任一服务，`preview` 和 `deploy` 使用相同选择逻辑。

未选应用的实际镜像 digest、来源元数据和应用配置从当前 Helm release 读取；
核对 Deployment 镜像后保存非凭据配置快照。Pulumi 以已部署 values 为基础，只替换所选
应用的字段，保留另一项及共享配置。仅更新 Memoria 时保留适配器连接 URL 和 key 引用；
仅更新适配器时跳过 Memoria checkout／构建，并以 `reuse` 记录原来源。首次环境没有完整
release 时拒绝单项更新，不根据旧 Variable 猜测当前版本。

共享数据库连接、master key、adapter key 和 embedding key 若有变动，要求选择 `both`。
未选 Deployment 的旧 manifest 与当前 chart 使用旧 values 的渲染结果必须一致，避免
chart 改动意外更新另一项。构建完成后、Pulumi 操作前复查快照；变化时停止并要求重新运行。
快照权限为 `600`，不含原始运行凭据，忽略提交并在 workflow 结束清理。共享 Ingress／证书
等 Helm 配置在单项更新时保留，变更时使用 `both`。独立更新无需新增 Secret。

所有模式仍使用同一 Pulumi stack、namespace 和 Helm release，保留串行发布与冻结规则；
自动发布启用后仍默认更新两项。独立更新控制应用版本和配置，不使用 Pulumi resource
`--target` 裁剪资源图。Memoria Recreate 期间上游会暂时不可用，适配器 Pod 保留不代表
Add/Search 全程可用，比赛评测期间仍须冻结发布。

离线验证覆盖选择分支、已部署版本复用、共享凭据变更拒绝、未选应用 chart 变动拒绝、
构建期间配置变化拒绝、首次 `both` 和 Pulumi 配置保留。真实单项部署及实际 Pod 的保留
效果仍需在初次完整部署后验收。

`KUBECONFIG` Secret 使用阿里云 dev unit 的完整原始 YAML，不做 base64 编码，不填写文件路径；原有 AWS 配置不能用于该目标。工作流使用既有 `PULUMI_TOKEN`、组织 ACR Secret 和新建运行凭据。复用现有 DNS solver 时无需新增阿里云 RAM AK；域名入口解析由运维配置，证书由 cert-manager 自动管理。变量、Secret、初始化和操作步骤见 [dev 发布说明](../../deploy/README.md)。

Memoria 和适配器各单副本，journal 持久保存，使用 Recreate 更新以维持 journal 和 RWO 卷的单 Pod 部署约束；同一 Pod 中的多个进程可共享现有 journal 和文件锁。namespace／journal PVC 受删除保护。凭据变化通过 Secret checksum 触发重启。发布等待 Pod 就绪，ready 仅检查 journal 和上游健康接口，不等价于数据库权限、embedding、真实 Add/Search 或完整幂等恢复验收。

### 发布安全与工程补充

以下工程措施已在代码中实现；首次完整发布及最小运行联调已通过，恢复与容量等剩余验收见后文。

| 项目 | 当前事实 | 处理要求 |
|---|---|---|
| 发布冻结（P1） | `AML_FREEZE=true` 在任务入口及 `up` 前拒绝自动和手动部署，Pulumi 真实更新再检查 | 空值／`false` 允许，非法值拒绝继续；preview 可执行，已运行任务按下述流程处理 |
| Helm 检查（P2） | CI 和 CD 均安装 Helm 3.17.4；CI 两组 lint／template 已通过，本地 Helm 4.1.4 两组也通过 | 合成配置不含真实凭据或集群访问，不代替真实发布 |
| `/ready` 测试（P2） | 八个用例覆盖原有六种依赖状态，以及 40 个 Search 饱和时探针可用、并发 ready 不排队；本地完整 pytest 154 项通过 | 同时检查探针无鉴权、失败不泄露上游内容、journal 失败不访问上游；公网健康状态返回 200，真实故障注入仍未执行 |
| 依赖与 Action 锁定（P2） | 部署辅助依赖列出 PyPI SHA256，安装启用 `--require-hashes --only-binary=:all:`；Actions 引用完整 commit SHA | 保留对应主版本注释，更新时重新核对来源和锁定值；应用 `uv.lock`／Go `go.sum` 继续使用 |
| 环境配置公开边界（P2） | 实际 dev YAML 已忽略；公开模板使用占位符，CI 从三个非秘密 Variables 生成 | 标识已上传，不增加 Secret；继续检查其他环境信息的公开边界 |
| 初始化凭据恢复（P2） | 先查询专用用户；凭据缺失且用户存在时，生成新凭据和 DDL 前停止，并输出恢复指引 | 不重设旧密码；首次创建使用明确 CREATE USER，凭据更新原子替换，临时文件忽略；恢复行为尚未执行数据库验收 |

冻结变量在 workflow 中使用当次解析值，入口和 `up` 前的检查不代表实时重新查询 GitHub Variable。进入正式评测前，设置冻结、关闭自动发布、取消排队的 deploy，等待已经运行的发布结束，再确认镜像 digest 和运行配置。冻结不能撤销已经开始的部署，也不能约束绕过 workflow 的手工操作；不假定修改变量能中止长时间构建中的任务。preview 可继续用于评审，但当前 preview 会推送镜像并可能初始化空 stack，不能当作完全无外部写入的检查。

保留 Recreate 策略。适配器已有 1800 秒终止宽限和 1740 秒 Uvicorn 优雅退出等待，Memoria 的终止宽限为 120 秒；这些配置不保证发布期间持续可用，也不保证所有在途请求完成。发布串行化只协调 CD 任务，不感知 AML 评测。正式评测期间禁止应用发布、凭据更新和手工重启；冻结不是高可用机制。

初始化恢复应优先保留已生效的数据库密码、master key 和 adapter key。确需轮换数据库密码时，使用管理员执行明确的密码更新，并同步 GitHub Secret 与运行实例；两种认证 key 另行管理，避免因重新初始化而意外替换。错误指引只输出恢复步骤和非秘密错误信息。

API 地址、集群 UID 和租户标识属于环境信息，不等同于密码泄露。私有配置与公开模板分离后，三个辅助脚本和 Pulumi 继续读取同一目标配置；公开整理不能通过删除身份检查来隐藏目标环境。

### 并发与上线验收

**worker 数量是容量参数，当前单 worker 不能直接判定为吞吐缺陷。** Add/Search 使用同步 `def` 路由，经 Starlette 线程池执行；其默认线程池容量为 40 个 token，一个进程能并发处理多个请求。journal 使用独立 SQLite 连接和请求级文件锁，支持同 Pod 多进程，但数据库写入仍有锁竞争。当前适配器资源上限为 1 CPU／1Gi，增加 worker 会增加线程、HTTP 客户端和内存，并放大后端流量。[Starlette 线程池](https://starlette.dev/threadpool/)

默认保留单 worker，已增加 Helm `workers` 参数及正整数 schema 校验；CD 的 `AML_ADAPTER_WORKERS` 传入 Pulumi，再配置 Uvicorn `--workers`，不增加 Pod 副本数。结合总并发预算和负载测量选值，比较吞吐、P50／P95、错误率、CPU／内存、journal 锁等待、数据库及 embedding 压力后再增加，不把固定多个 worker 作为正式参评的前置要求。

运行验收按具体版本记录，剩余范围如下：

1. **只读 Memoria 容器**：当前交付状态所列 SHA／digest 在 UID 1000、只读根文件系统、仅 `/tmp` 可写的条件下，启动、共享及用户 schema 初始化、健康检查和真实 Add/Search 已通过，重启恢复仍未验收。自有 Dockerfile 直接执行 `memoria serve`，日志写标准输出。未来来源版本若实测需要其他可写路径，应依据证据增加最小挂载或修正配置；不预先关闭只读限制。结论关联实际 SHA／digest，不能泛化到所有 0.5.x 镜像。
2. **公网长请求**：核对真实 SLB 产品、80／443 listener 协议及空闲／请求超时，再核对 NGINX 和客户端。当前 Kubernetes Service 为 LoadBalancer、端口标记 TCP；这只是 Service 的传输协议，不能证明云端 listener 类型和超时值。阿里云 CLB HTTP 文档区分空闲超时默认 15 秒与请求超时默认 60 秒；CLB TCP 空闲超时默认 900 秒。默认值不能替代当前实例的实际配置。[CLB HTTP](https://www.alibabacloud.com/help/en/slb/classic-load-balancer/user-guide/add-an-http-listener-1)、[CLB TCP](https://www.alibabacloud.com/help/en/slb/classic-load-balancer/user-guide/add-a-tcp-listener)
3. **长请求验收范围**：5 分钟无中间响应的请求可作为初步检查，但不能验证 900 秒限制或接近 AML 允许上限的整批 Add。继续验证超过 900 秒、接近所选组别有效契约上限的静默请求，并核对真实整批 Add 的耗时、超时后的恢复和重复提交。流式心跳不能代替最终 JSON 响应的协议验收；共享 SLB 的修改由基础设施负责人处理。

以上工程补充与运行验收在正式评测前完成；它们不替代输入可信、幂等恢复与 Answer/Eval 等质量工作。

### 评测负载与资源边界

- 正常成功路径中，每个新记忆分片执行写入、ID 回读、搜索确认三次上游调用。设分片数为 M、外部 Search 次数为 Q，不计失败／恢复／重试，Memoria HTTP 请求量约为 **3M + Q**。20 条各不超过分片上限的消息约需 60 次上游调用；该数字不等于 SQL、模型计费次数或实际 QPS，长消息分片和重试会增加请求量。
- 启用 embedding 时，写入和搜索探针均可能产生模型流量，数据库承担写入、去重和检索。实际调用量受配置、缓存与重试影响；当前适配器没有接上 Observe／LLM 抽取或 Answer/Eval，不能假定每条记忆消耗一次 qwen-plus。
- 本地 benchmark runner 串行发送请求，但没有 QPS／token 预算或跨运行并发控制。适配器同步路由通过线程池执行，一个进程也可以同时处理不同请求；请求级 journal 锁不能控制整套评测的总负载。全局总并发、上游速率和总量预算尚未实现。[FastAPI 同步处理器](https://fastapi.tiangolo.com/async/#path-operation-functions)
- owner 前缀隔离的是数据范围。共享数据库计算资源或同一模型账户时仍会竞争资源；独立 database 或新建模型 key 不自动提供独立容量。
- 开发阶段一次运行一个任务，从小集开始记录分片数、写入吞吐、Search P50／P95、错误／429、数据库等待、模型 token／额度和数据增长。停止条件依据评测实例的预算和实测确定，不推测一个未经验证的安全 QPS。
- 保留 journal 支持同一请求恢复；数据清理覆盖 Memoria 记录、journal 与其他产物。逻辑删除、独立 owner、持久卷均不能代替物理保留期限与清理方案。

### 正式参评前的工作

优先完成输入与来源时间、未知写入恢复、token／字节边界等已确认问题，并建立本项目自己的 Answer/Eval 和固定小集质量基线。在阿里云 dev 独立应用部署后，测量共享资源上的容量，确认模型和组别契约，验收 HTTPS、认证、用户隔离、时延、重启／重试及清理。发布流程不自动启动基准或官方 Smoke／Full。

### Memoria 镜像选择与源码构建

**默认从 `matrixorigin/memoria` 的 `main` 分支构建。** 非秘密来源配置位于 `deploy/memoria-source.json`，维护默认模式、仓库和 ref；`AML_MEMORIA_REF` 可以设定长期来源，手动发布的 `memoria_ref` 可以选择本次分支、tag 或 commit。分支选择符合日常开发需要，每次 checkout 后记录实际完整 SHA；后续构建使用这份 checkout，部署使用构建返回的镜像 digest。源码 ref 和本仓库 workflow 的 main 分支限制相互独立。

来源必须存在于配置的远端仓库，本地未推送修改不进入构建。Memoria 分支更新不会自行触发本仓库 workflow；手动发布或已启用的本仓库 main push 发布会读取当次来源版本。默认不增加源码仓库反向触发或定时自动发布。

本项目的 `deploy/docker/memoria.Dockerfile` 是源码构建入口，构建上下文为检出的 Memoria 仓库根目录。原源码 Dockerfile 默认 Rust 1.85，而当前 lock 含要求 1.88 的依赖；比赛构建采用 Rust 1.90.0，builder／runtime 同为 Debian Bookworm，并固定基础镜像 digest，避免构建器与运行环境的 glibc 差异。`cargo build --locked --release --bin memoria` 使用来源仓库的 Cargo.lock、vendored 依赖及 SQLX offline 数据；下载／编译采用缓存。运行镜像以 UID 1000 执行 `memoria serve`，日志写标准输出。该构建文件在 benchmark 仓库维护，无须修改所选 Memoria 分支；未来编译器要求变化需更新此文件。

离线 CI 只检查适配器镜像；Memoria 的 `linux/amd64` 源码镜像仅由 CD 按更新范围构建，并推送至指定 Hangzhou ACR 仓库。源码模式下 `AML_MEMORIA_REPOSITORY` 默认使用适配器的同一仓库，由不同项目 tag 前缀区分两个镜像；两个镜像的实际推送与 Pod 拉取已通过。只有构建成功并返回有效 digest 后才进入 Pulumi／Helm；不会把新 digest 写回 GitHub Variable。记录包含请求的 ref、实际完整 commit、构建 tag／UTC 日期、镜像 digest、构建 Dockerfile hash、benchmark commit 和 Actions run。Actions summary／artifact 保存构建信息，Pulumi output 和 Memoria Pod annotation 标记来源版本。artifact 当前保留 30 天，长期评测结果应另存版本信息。

当前 ACR registry 为 `registry.cn-hangzhou.aliyuncs.com`，namespace 为 `moc-pub`，repository 统一为 `plugin`。tag 采用用户指定的 `<project>-<YYYYMMDD>-<7 位 SHA>` 格式；project 分别为 `aml-memory-adapter` 与 `aml-memoria`，日期统一取工作流生成 tag 时的 UTC 构建日期，SHA 来自各自的实际源码版本。共享 repository 由项目前缀区分，实际部署引用为 `<registry>/<ACR namespace>/<repository>@sha256:<digest>`。完整源码 SHA 仍保存在版本记录，不依赖短 SHA 追溯。ACR namespace 与 Kubernetes namespace `aml-memory-benchmarks` 是独立概念；可选现成 Memoria 镜像保留 `moc-pub/memoria` 的有效 digest 引用。完整示例和 Variable 填写规则见 [镜像名称结构](../../deploy/README.md#镜像名称结构)。

`main` 或其他分支在不同运行中可以更新；重放某次 preview／评测时可把实际 SHA 填入 `memoria_ref`，或改用那次镜像 digest。镜像 tag 只用于查找，实际发布与回退使用 digest；源码版本固定与最终镜像版本均可追溯。preview 后若分支改变，下一次默认分支 deploy 可能构建不同版本，需用记录的 SHA 指定相同来源。

保留 `image` 模式用于部署现成镜像与回退，手动 `memoria_build_mode` 可覆盖本次选择；`configured` 按 Variable 或 JSON 中默认模式执行。该模式要求 `AML_MEMORIA_IMAGE=repository@sha256:...`，跳过 Memoria checkout／构建。现有 Variable 沿用 gitops dev 的 `apiserver-20260817-a8c0397-dbfc6241`，不是源码模式的默认版本，也没有已确认的完整源码 SHA。当前源码构建版本的真实 Add/Search 已通过；该既有现成镜像的运行兼容性仍待验收。

#### ACR 镜像发布兼容性

当前 Hangzhou ACR 入口拒绝 BuildKit provenance 附件使用的
`application/vnd.oci.empty.v1+json`。发布适配器和 Memoria 时显式设置
`provenance: false` 与 `sbom: false`，避免将这类附件随镜像推送；离线 CI 的适配器镜像构建
使用相同设置。[Docker 构建附件说明](https://docs.docker.com/build/metadata/attestations/)

镜像仍按 digest 部署。Memoria 来源 SHA、Dockerfile hash、镜像 tag／digest、benchmark
commit 和 Actions run 继续写入原有版本记录／镜像标签；这类记录提供版本追溯，不等同于
签名或可验证 provenance。需要构建证明时，应在兼容的独立存储中保存，或在 registry
升级并验证附件支持后重新启用。

### 当前交付状态

**阿里云 dev 首次完整部署和公网最小联调已完成。**
[部署 run 37459492335](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37459492335)
于 2026-10-06 成功完成，使用 benchmark commit `3048f6bab82966ee63c25fbf5f6cb51cadf87c6d`，
总耗时约 14 分 48 秒。离线检查、源码构建、两个 ACR 镜像推送、Pulumi up、Pod 拉取与
证书等待均通过；现有 Pulumi token、registry 和数据库运行凭据满足本次发布。

| 应用 | 实际版本 |
|---|---|
| 适配器 | `registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin@sha256:c273b9605da1ec63f4462c8253d3254399c54ebd01a6cf55767b0105d36e4164` |
| Memoria | `registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin@sha256:f4c4c21c6d1cfe8f1623f58c6b1e11df9ed0f4e0a8a00e98455dacf3852b3721` |
| Memoria 来源 | `matrixorigin/memoria`，ref `main`，实际 SHA `e180dff308447efb487f313187968e77323dd33f` |

两个 Deployment 均为 1/1 Ready、验收时无重启，20Gi journal PVC Bound。
Ingress 为 `aml.cn-dev.matrixone.tech`，公网地址 `47.97.230.53`，`aml-tls` Certificate Ready。
使用正常 HTTPS 证书验证请求公网 `/health` 和 `/ready` 均为 200，匿名 `/search` 为 401。
Memoria 实际以 UID 1000、只读根文件系统运行，显式采用多库及独立共享库配置。

本项目现有 `src.smoke` 通过公网适配器与临时 Memoria Service 转发完成 7 项联调：
owner 范围认证、同步 Add 后立即 Search、跨会话检索、用户隔离、幂等重试、top_k、空用户。
耗时 16.32 秒；真实数据库初始化、写入／检索与上海 `BAAI/bge-m3` 配置下的调用通过。
测试只使用随机 UUID 用户，测试记忆随后逻辑停用，临时转发已关闭；用户库、journal
及历史快照没有物理删除。此测试不等同于 AML 官方 Smoke，不产生榜单质量分数，也未
独立校验模型原始向量维度。

剩余运行验收包括单项更新时未选 Pod 的保留、重启及未知写入恢复、公网长请求与真实
SLB 超时、容量／P50／P95、凭据丢失恢复及数据物理清理策略。旧现成镜像模式未作运行验收。
本项目的 Answer/Eval、基准质量与正式 AML Smoke／Full 也未由本次部署执行；部署成功
不代表正式参评就绪。发布冻结、worker 参数、Helm 检查、ready 用例、依赖锁定、私有配置
及初始化恢复保护均保留现有设计。

后续代码和实验通过本项目任务／PR 共建。旧独立 Memoria 路径未迁入；既有核实结论见 [Issue #1 独立修正评论](https://github.com/matrixorigin/memory_benchmarks/issues/1#issuecomment-5987006462)。
