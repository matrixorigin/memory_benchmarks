# AML 文本赛道：共建方案与工作计划

## 工作范围

团队在 [matrixorigin/memory_benchmarks](https://github.com/matrixorigin/memory_benchmarks) 共建参评适配、评测和部署工程，基于同事现有实现继续开展工作。本文是后续方案与设计决策的维护入口；更新有效章节，区分已实现、待实施和验证证据，不记录中间讨论。

此前独立的 Memoria `feat/aml-text-adapter` 分支、`aml-local-client` 和相关优化／评测计划停止推进。已有代码、PR、文档和结果保留为参考，不删除或关闭；不以完成旧计划或迁入旧实现作为新项目的前置条件。后续是否采用其中的代码或用例，由新项目的具体任务决定。

## CI/CD 交付概要与 Review 入口

当前交付位于 `memory_benchmarks` 的 **`feat/aml-cicd`** 分支，基于 `b037c42`。CI/CD、Memoria 部署模板和自动证书配置已编写；离线 CI 已通过，公网 DNS 已配置并核实。**本次没有将 Memoria 或适配器部署到集群。** 数据库初始化和 GitHub 凭据配置已执行，CD 和运行验收仍待执行。

### 完成状态

| 部分 | 代码／配置状态 | 已执行的工作 | 待验证／待完成 |
|---|---|---|---|
| 离线 CI | 已编写 Ruff、pytest、Helm lint／template、适配器／Memoria 源码镜像构建、Go Pulumi 编译；八个健康探针用例 | Actions 两个 job 均通过，pytest 为 154 passed、1 warning；Helm 3.17.4 两组检查及两个镜像构建通过 | CI 不推送镜像或部署；后续提交检查以 PR checks 为准 |
| dev CD | 已编写 main 手动 preview／deploy、冻结检查；自动部署默认关闭 | 目标集群身份、存储、现有入口已只读核实；三个私有环境 Variables 已上传 | 未运行 CD Actions、Pulumi preview／up；发布凭据实际权限未验证 |
| Memoria 部署 | 已编写源码／现成镜像模式、分支选择、构建与版本记录、Deployment、Service、Secret 及数据库／embedding 配置 | CI 源码构建通过；已核实上游默认多库及自动迁移设计 | 显式多库、专用共享库与动态用户库授权已配置；尚未创建 Pod |
| Add/Search 部署 | 已编写 Deployment、Service、journal PVC 和 `/ready` | 准备稳定 owner 前缀及独立认证 key | 未部署或运行真实 Add/Search；持久性和恢复未验收 |
| 数据库 | 初始化脚本已编写 | 已创建 `aml_memoria` 用户、`aml_memoria_role` 角色和 `aml_memoria` 库；完成新用户登录及库名回查 | 建库／DDL／CRUD 及其他角色测试库的拒绝访问已验证；完整 Memoria schema／检索待验收 |
| GitHub 配置 | 凭据／环境变量上传脚本已编写，上海 embedding key 为必填 | 六个必需 Repository Secrets 均存在，两个组织 ACR Secrets 可用，镜像和三个私有环境 Variables 已配置；无 key 模型探针返回 401 | Pulumi／ACR 实际权限及带 key 的 embedding 调用待验收 |
| HTTPS | 已编写 Ingress、Certificate 和证书 Ready 等待步骤 | 已核实 NGINX、cert-manager、生产 Issuer；`AML_PUBLIC_HOST` 已设置，公共 DNS 解析为 `47.97.230.53`，与 NGINX 入口一致 | 尚无应用 Ingress；未申请证书或验收公网 HTTPS |

离线 CI 证据为 [Actions run 37452656686](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37452656686)，对应 PR 分支版本 `c54dc53`，2026-10-06 两个 job 均为 Success。当前多库配置修复的本地完整 pytest 为 154 passed、1 warning，Ruff 通过；此前本地 Go 编译及 Helm 4.1.4 两组渲染也通过。容器构建的通过证据来自上述 CI 版本。该记录不证明镜像已发布或应用已上线。

变更通过 `feat/aml-cicd` 分支向 `origin/main` 提交 PR，交付范围包括 `.github/`、`deploy/`、`/ready`、测试和本目录方案文档。Review 以 PR 完整 diff 为准；实际 dev 配置、凭据和本地运行产物已排除提交。

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

1. **工作流能否执行**：Actions 表达式、依赖／版本、Go 编译、镜像构建、digest 传递和 Pulumi 参数是否一致。离线 CI 已通过，CD 的镜像发布、digest 传递及 Pulumi 执行仍待验收。
2. **发布范围是否正确**：只操作指定 unit 集群及独立 stack；集群身份检查、应用 namespace、共享资源引用和删除保护是否完整。
3. **Memoria 能否启动并供适配器调用**：固定镜像是否具备普通 `/v1/memories` 路径所需的 owner、metadata 和 query 能力；运行参数、schema 初始化、数据库角色权限和 embedding 环境是否满足该版本。
4. **状态与恢复是否可靠**：SQLite 单写者、PVC 绑定／权限、Recreate、发布中断、凭据更新及已有 journal 的保留；确认 `protect` 不等价于备份或恢复方案。
5. **公网链路是否成立**：域名、DNS solver 范围、Certificate／Secret／Ingress 关联和 Ready 等待；SLB 与 NGINX 的真实长请求限制仍需验收。
6. **验证边界是否清楚**：`/ready` 只覆盖 journal 可读及上游健康接口；新数据库登录成功不证明 Memoria 迁移或 Add/Search 可用。既有比赛质量／幂等问题按下文独立跟踪。

### 部署架构

```mermaid
flowchart LR
    AML["AML 客户端"] -->|HTTPS| NG["现有 NGINX 与应用 Ingress"]
    subgraph APP["计划部署：aml-memory-benchmarks namespace"]
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

Memoria 默认从源码 `main` 分支构建，由本项目 CD 部署，也可指定分支、tag、commit 或现成镜像；CI 构建已通过，尚未发布或部署，未修改 Memoria 源码。数据库引擎、模型服务、NGINX 和证书控制器是既有基础设施。详细设计和资源边界见下文，具体配置与操作见发布说明。

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

完整 Memoria schema、首次用户 Add/Search 和重启恢复仍须通过真实镜像验收。权限检查不替代 HTTP 路径、embedding 或质量评测。本 PR 只调整比赛项目的部署配置和初始化授权，不修改 Memoria 的数据库路由或启动语义。

embedding 默认沿用 gitops dev 配置：上海 OpenAI-compatible `BAAI/bge-m3`、1024 维。其网络、认证、预算及真实检索质量需要联调确认；当前发布配置不要求 LLM 或 rerank，实际 Add/Search 路径没有调用 Answer/Eval 或 Observe 抽取。镜像均固定 digest，不混用不同 embedding 模型的既有向量。

上海 embedding 的公网接口需要鉴权。2026-10-06 从本机以正常 HTTPS 校验向 `https://bgem3.model.shanghai.idc.matrixorigin.cn:30443/v1/embeddings` 发送单条短文本、`model=BAAI/bge-m3` 的无 key 请求，返回 HTTP `401`、`{"error":"Unauthorized"}`，约 0.071 秒。结论限定为该接口不接受匿名调用，尚未验证有效 key、1024 维输出或 dev Pod 网络。发布使用 `AML_EMBEDDING_API_KEY`，由服务维护者提供有效 key；workflow 和 Pulumi 都要求其非空，缺失时不继续发布。模型调用仍需带 key 验收，`/ready` 不替代该验收。

### CI/CD 凭据边界与配置

离线 CI 无须手工配置 Secret；公开 Memoria 源码 checkout 使用 Actions 自动提供的 `GITHUB_TOKEN`。dev preview／deploy 均需要八个必需凭据：Repository Secrets `KUBECONFIG`、`PULUMI_TOKEN`、`AML_DATABASE_PASSWORD`、`AML_MEMORIA_MASTER_KEY`、`AML_ADAPTER_API_KEY`、`AML_EMBEDDING_API_KEY`，以及本仓库可访问的 Organization Secrets `NEW_ACR_USERNAME`／`NEW_ACR_PASSWORD`。来源、填写方法和当前准备状态统一列于 [发布说明的 Secrets 表](../../deploy/README.md#secrets)。

`KUBECONFIG` 存储用户提供的阿里云 unit 完整原始 YAML，不存本机路径或 base64；workflow 写临时文件供 Kubernetes provider 使用，结束时清理。`PULUMI_TOKEN` 映射为 `PULUMI_ACCESS_TOKEN`，只用于独立应用 stack 的 Pulumi Cloud 访问。ACR 凭据用于镜像推送并生成 namespace 内的拉取 Secret。数据库密码、master key、对外 adapter key 和 embedding key 进入 `aml-runtime`，按需供各 Pod 使用；数据库运行 DSN 使用专用用户，由 Pulumi 编码和生成。管理员数据库密码不进入持续发布。

环境标识使用 `AML_EXPECTED_API_SERVER`、`AML_EXPECTED_CLUSTER_UID`、`AML_DATABASE_ACCOUNT` 三个 Repository Variables，已从现有私有配置上传。仓库只提交 `Pulumi.new-dev.example.yaml`；本地实际 `Pulumi.new-dev.yaml` 被 gitignore 排除。CD 生成同一格式配置，preflight、初始化和凭据上传脚本继续读取该文件，保留集群及租户身份检查，不增加 Secret 数量。公开前仍须按基础设施披露要求检查文档和脚本中的其他环境信息。

2026-10-06 GitHub 名称元数据显示六个必需 Repository Secrets 均已存在，包括用户配置的 embedding key，两个组织 ACR Secrets 已对本仓库开放；当前没有缺失的必需 Secret 名称。这里只核对名称元数据，不读取密钥值，不等价于有效性或权限验证。`AML_ADAPTER_REPOSITORY`／`AML_MEMORIA_REPOSITORY` 均配置为共享 `moc-pub/plugin` 仓库，供现成镜像模式使用的 `AML_MEMORIA_IMAGE` 也已保存；源码模式与 main 默认值来自已编写的来源配置，其余非秘密参数按发布说明填写。

当前应用 Pulumi 只操作 Kubernetes，复用已有证书控制器与 Issuer，不将阿里云 RAM AccessKey、DNS 签发凭据或证书私钥加入本仓库 CI。DNS 凭据由基础设施管理。当前 Add/Search 发布不需要 LLM／rerank key，也不自动调用官方评测。正式参评时提供给 AML 的认证值是 adapter key，上游 Memoria master key 在内部使用。

### Kubernetes Ingress 公网方案

访问链路为 **AML → HTTPS 域名 → unit 集群 NGINX → 适配器 Service → 内部 Memoria**。本项目新增自己的 Ingress，使用 `nginx` 类；公共路由覆盖 `/add`、`/search`，对 Memoria 保留 ClusterIP 内部入口。

证书自动化复用现有 **cert-manager + `aliyun-prod-letsencrypt` ClusterIssuer**。已核实 cert-manager `v1.12.1` 的 controller、webhook、cainjector 均有可用副本，生产和 staging Issuer 均为 Ready；应用 CD 不安装或升级共享控制器，旧版本维护由基础设施负责人处理。Issuer 的 DNS-01 webhook 配置范围为 `cn-dev.matrixone.tech`，应用域名需位于该范围；其他域名需要提供对应的有效 Issuer。

启用公网时设置明确域名，并配置 DNS 指向 NGINX 公网地址。chart 声明专用 Certificate，cert-manager 自动申请、保存及续期 TLS Secret；发布等待 Certificate Ready。证书私钥由集群控制器管理，CI/CD 不负责重复签发或更新该 Secret。Let’s Encrypt 当前默认有效期为 90 天，足以覆盖一个月比赛；以真实 `notAfter` 为准并保留自动续期。[cert-manager DNS-01](https://cert-manager.io/docs/configuration/acme/dns01/)，[证书有效期](https://letsencrypt.org/docs/cert-lifetimes/)

公网域名选定为 `aml.cn-dev.matrixone.tech`，GitHub Repository Variable `AML_PUBLIC_HOST` 已设置，A 记录已配置。部署后的公开入口为 `https://aml.cn-dev.matrixone.tech/add` 与 `https://aml.cn-dev.matrixone.tech/search`。当前尚无应用 Ingress，也未请求证书；仍须部署并验收证书链和实际可达性。[Kubernetes Ingress](https://kubernetes.io/docs/concepts/services-networking/ingress/)

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

PR、main push 或手动触发 **AML offline CI** 时，执行依赖锁定安装、Ruff、pytest、固定版本 `actionlint v1.7.7` 的 workflow YAML／Actions 表达式检查、Helm lint／template、适配器镜像构建及 Pulumi 程序编译；独立 job 从配置的 Memoria ref 构建源码镜像，记录实际 commit，两者均不推送到 registry。Helm 检查使用假镜像 digest、模型 URL 和域名，覆盖内部入口／单 worker，以及公网 Ingress／Certificate／两个 worker。workflow 不引用集群、Pulumi、ACR 或跨仓库发布 Secret，不自动运行真实 HTTP smoke、LLM 基准或官方 Smoke／Full。这里的离线指不调用真实业务／评测接口，依赖安装和源码 checkout 仍需要网络。

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

以下工程措施已在代码中实现；实际发布和运行验收尚待完成。优先保证发布可控和检查完整，再按实测调整吞吐。

| 项目 | 当前事实 | 处理要求 |
|---|---|---|
| 发布冻结（P1） | `AML_FREEZE=true` 在任务入口及 `up` 前拒绝自动和手动部署，Pulumi 真实更新再检查 | 空值／`false` 允许，非法值拒绝继续；preview 可执行，已运行任务按下述流程处理 |
| Helm 检查（P2） | CI 和 CD 均安装 Helm 3.17.4；CI 两组 lint／template 已通过，本地 Helm 4.1.4 两组也通过 | 合成配置不含真实凭据或集群访问，不代替真实发布 |
| `/ready` 测试（P2） | 八个用例覆盖原有六种依赖状态，以及 40 个 Search 饱和时探针可用、并发 ready 不排队；本地完整 pytest 154 项通过 | 同时检查探针无鉴权、失败不泄露上游内容、journal 失败不访问上游；未运行真实服务验收 |
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

以下运行条件尚未验收，不能仅由静态代码判定通过：

1. **只读 Memoria 容器**：对实际部署 digest，在 UID 1000、只读根文件系统、仅 `/tmp` 可写的条件下，验收启动、迁移、健康检查、真实写入／检索与重启。当前自有 Dockerfile 直接执行 `memoria serve`，模板也覆盖启动命令；已读取的 serve 日志写标准输出，插件仓库临时文件使用临时目录，未找到已确认的 `/tmp` 外必需写入。若实测失败，应依据明确路径增加最小可写挂载或修正配置；不预先关闭只读限制。来源分支可更新，结论必须关联实际 SHA／digest，不能泛化到所有 0.5.x 镜像。
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

CI 只构建 `linux/amd64` 而不推送；CD 构建并推送至指定 Hangzhou ACR 仓库。源码模式下 `AML_MEMORIA_REPOSITORY` 默认使用适配器的同一仓库，由不同项目 tag 前缀区分两个镜像；仓库存在性及实际推送／拉取权限待验收。只有构建成功并返回有效 digest 后才进入 Pulumi／Helm；不会把新 digest 写回 GitHub Variable。记录包含请求的 ref、实际完整 commit、构建 tag／UTC 日期、镜像 digest、构建 Dockerfile hash、benchmark commit 和 Actions run。Actions summary／artifact 保存构建信息，Pulumi output 和 Memoria Pod annotation 标记来源版本。artifact 当前保留 30 天，长期评测结果应另存版本信息。

当前 ACR registry 为 `registry.cn-hangzhou.aliyuncs.com`，namespace 为 `moc-pub`，repository 统一为 `plugin`。tag 采用用户指定的 `<project>-<YYYYMMDD>-<7 位 SHA>` 格式；project 分别为 `aml-memory-adapter` 与 `aml-memoria`，日期统一取工作流生成 tag 时的 UTC 构建日期，SHA 来自各自的实际源码版本。共享 repository 由项目前缀区分，实际部署引用为 `<registry>/<ACR namespace>/<repository>@sha256:<digest>`。完整源码 SHA 仍保存在版本记录，不依赖短 SHA 追溯。ACR namespace 与 Kubernetes namespace `aml-memory-benchmarks` 是独立概念；可选现成 Memoria 镜像保留 `moc-pub/memoria` 的有效 digest 引用。完整示例和 Variable 填写规则见 [镜像名称结构](../../deploy/README.md#镜像名称结构)。

`main` 或其他分支在不同运行中可以更新；重放某次 preview／评测时可把实际 SHA 填入 `memoria_ref`，或改用那次镜像 digest。镜像 tag 只用于查找，实际发布与回退使用 digest；源码版本固定与最终镜像版本均可追溯。preview 后若分支改变，下一次默认分支 deploy 可能构建不同版本，需用记录的 SHA 指定相同来源。

保留 `image` 模式用于部署现成镜像与回退，手动 `memoria_build_mode` 可覆盖本次选择；`configured` 按 Variable 或 JSON 中默认模式执行。该模式要求 `AML_MEMORIA_IMAGE=repository@sha256:...`，跳过 Memoria checkout／构建。现有 Variable 沿用 gitops dev 的 `apiserver-20260817-a8c0397-dbfc6241`，不是源码模式的默认版本，也没有已确认的完整源码 SHA。源码 CI 构建已通过；两种模式的真实 Add/Search 兼容性均待验收。

### 当前交付状态

已编写通用 `/ready`、离线 CI、阿里云 dev CD、独立 Pulumi／Helm 配置、数据库初始化和凭据配置脚本。目标集群、现有证书控制器和 Issuer 已只读核实；数据库用户／角色／专用库已创建。GitHub `KUBECONFIG` 已替换为用户提供的阿里云 unit 配置，四个运行 Secret 及镜像 Variables 已配置；既有 Pulumi token 和组织 ACR 凭据的实际发布权限、embedding key 的实际模型调用仍需验证。

Memoria 默认源码构建与来源分支选择已接入 CI/CD，默认 ref 为 `main`；源码 CI 构建已通过，尚未推送新镜像。现成镜像模式保留 gitops dev 已保存且固定 digest 的版本用于可选发布，实际 API／数据库运行兼容性和 embedding 可用性均尚未验收。

代码与配置通过本分支 PR 交付；应用资源尚未部署，公网域名 `aml.cn-dev.matrixone.tech` 已解析到 NGINX 公网入口，证书尚未申请。冻结、worker 参数、Helm 检查、ready 用例、依赖锁定、私有配置和初始化恢复保护已编写；离线 CI 的 Ruff、154 个 pytest 用例、Helm 3.17.4 两组检查、两个镜像构建及 Go 编译均通过。探针隔离修复的本地完整 pytest 为 154 项通过。多库模板与初始化授权已修正；真实 Add/Search 和基准尚未运行，真实镜像只读运行、公网长请求和数据库恢复行为仍待验收。正式发布及质量验收需分别完成。

后续代码和实验通过本项目任务／PR 共建。旧独立 Memoria 路径未迁入；既有核实结论见 [Issue #1 独立修正评论](https://github.com/matrixorigin/memory_benchmarks/issues/1#issuecomment-5987006462)。
