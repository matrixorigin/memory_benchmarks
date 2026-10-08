# 历史阿里云 dev 发布参考

本目录保留旧阿里云 dev 部署代码和历史验收参考。原直部署 workflow 已移除，不再提供
活动发布入口；本文的旧 workflow 操作步骤不再适用。当前 Memoria 和适配器运行于 QA，
由 moi-gitops 管理；评测使用 **QA memory benchmark** 与独立 QA key。
ACR 镜像仓库和 DashScope 模型服务仍可继续使用，与服务是否运行在阿里云集群无关。

## 实施状态与 Review

**Memoria 和适配器已在阿里云 dev 部署，公网最小联调通过。**
[部署 run 37459492335](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37459492335)
使用本仓库 commit `3048f6bab82966ee63c25fbf5f6cb51cadf87c6d`，2026-10-06 完成
离线检查、两个镜像构建与 ACR 推送、Pulumi up 和证书等待，全流程约 14 分 48 秒。
两个 Deployment 均为 1/1 Ready、无重启，20Gi journal PVC 已绑定。
公网入口为 `https://aml.cn-dev.matrixone.tech`；证书 Ready，正常 TLS 校验下
`/health`、`/ready` 返回 200，未带 key 的 `/search` 返回 401。

现有 `src.smoke` 通过公网适配器和临时本地 Memoria Service 转发验证了 owner 认证、
同步 Add 后立即 Search、跨会话检索、用户隔离、幂等重试、top_k 和空用户，共 7 项，
耗时 16.32 秒。测试仅使用随机 UUID 用户，测试记忆已逻辑停用，转发已关闭；
未物理删除用户库、journal 或历史快照。此为项目集成测试，不是 AML 官方 Smoke 或质量评分。
Memoria 默认从 `matrixorigin/memoria` 的 `main` 分支构建，
也支持选择其他分支、tag、commit 或已发布镜像；实际部署始终使用镜像 digest。
当前已于 2026-10-07 更新到 `main@81c5cc6d5e86a34072ecbc1bb41979b06364e7c7`，
包含 Memoria PR #277／#278，使用上述最小联调后的独立更新
[run 37568843033](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37568843033)。
新 Pod Ready，公网健康检查为 200；新版本完整基准仍待复测。上文 7 项联调属于旧版本。
具体 digest 和剩余验收范围见方案文档的[当前交付状态](../agent_memory_leaderboard/docs/competition-plan.md#当前交付状态)。

Memoria 显式采用多库模式：`DATABASE_URL` 与 `MEMORIA_SHARED_DATABASE_URL` 指向同一
专用共享库 `aml_memoria`，`MEMORIA_MULTI_DB=1`。初始化脚本为专用角色授予共享库权限及
`CREATE DATABASE ON ACCOUNT *`；用户库由 Memoria 按需创建并由该角色拥有。
当前版本的共享 schema、按需用户库及带上海 embedding 配置的真实 Add/Search 已通过最小联调。
控制项与权限边界见[数据库模式与部署兼容性](../agent_memory_leaderboard/docs/competition-plan.md#数据库模式与部署兼容性)。

完整方案、交付状态、源码导航和 Review 重点见
[CI/CD 交付概要与 Review 入口](../agent_memory_leaderboard/docs/competition-plan.md#cicd-交付概要与-review-入口)。
本文维护发布配置和操作步骤，设计决策统一维护在方案文档。

## 资源与所有权

| 项目 | 配置 |
|---|---|
| Pulumi stack | `MatrixOneCloud/memory-benchmarks/new-dev` |
| Kubernetes namespace | `aml-memory-benchmarks` |
| Memoria | 单副本，内部 Service `aml-memoria:8100` |
| Add/Search 适配器 | 单副本，内部 Service `aml:80`；公网由 NGINX Ingress 接入 |
| journal | `aml-adapter-state` PVC，20Gi，`alicloud-disk-essd-pl0`，RWO |
| 数据库 | 专用共享库 `aml_memoria`，以及 Memoria 按需创建的 `mem_u_<hash>` 用户库 |
| 数据库用户／角色 | `aml_memoria`／`aml_memoria_role`，共享库权限＋建库权限＋新建对象所有权 |

私有 `new-dev` 配置固定了 API Server 和 `kube-system` UID，阻止误用 AWS 或控制面
kubeconfig。此 stack 管理新建的应用资源，复用 unit 集群、MatrixOne 和 NGINX。
namespace 与 journal PVC 受 Pulumi `protect` 保护；删除环境时需要显式解除保护。
数据库用户、角色和库在一次性初始化步骤中创建，不由 Helm 卸载或 Pulumi destroy 删除。

同名 `new-dev` 存在于多个 gitops Pulumi project 中。控制面
`cloud-service/new-dev` 的 `moi-main/moi-memoria` 属于另一集群，本项目部署自己的
`aml-memoria`。应用发布入口只操作本项目独立 stack。

### 私有环境配置

`deploy/pulumi/Pulumi.new-dev.yaml` 只保存在本地并被 gitignore 排除；仓库提交
`Pulumi.new-dev.example.yaml`，集群地址、UID 和数据库租户标识使用占位符。
现有本地配置已保留。首次本地配置可复制 example，填入受控环境标识；不要提交实际文件。

CD 从三个 Repository Variables 生成同一格式的私有配置：
`AML_EXPECTED_API_SERVER`、`AML_EXPECTED_CLUSTER_UID`、`AML_DATABASE_ACCOUNT`。
这些是环境标识，不是认证密钥；已从现有私有配置上传。集群和租户身份校验继续生效。
`configure_github.py` 后续也会从本地私有文件同步这些 Variables。

## 一次性数据库初始化

管理员连接配置只用于本地初始化。支持 `DATABASE_URL=mysql://...` 和 `mysql -h ...`
形式；解析后使用驱动连接，不执行文件中的 shell 命令。

```sh
uv venv .deploy-venv --python 3.12
uv pip install --require-hashes --only-binary=:all: --python .deploy-venv/bin/python -r deploy/requirements.txt
.deploy-venv/bin/python deploy/scripts/bootstrap_db.py \
  --admin-file agent_memory_leaderboard/.env --apply
```

脚本创建数据库、数据库角色及独立用户，并将运行密码、Memoria master key 和适配器
API key 保存为 `deploy/.runtime.env`，权限 `600`。文件已被 gitignore 排除。
后续重跑复用这个文件，不重设既有用户密码，不删除库或数据。专用用户已经存在而文件
缺失时，脚本在生成凭据和执行 DDL 前停止，直接提示恢复方式；新建用户也不使用
`IF NOT EXISTS` 掩盖并发创建造成的密码差异。凭据更新采用原子替换，临时文件同样忽略。

恢复时优先从受控备份取回完整 `.runtime.env`；若需要管理员明确轮换 SQL 密码，随后
同步 `AML_DATABASE_PASSWORD` GitHub Secret 与服务配置。master key 和 adapter key
单独恢复或轮换。登录失败会提示保留原文件并检查密码／角色，不要删除后盲目重跑。

这里的独立账号指 **数据库登录用户**。现有连接属于一个 MatrixOne 租户；本方案没有
新建整个租户。角色拥有共享库和其表上的权限，以及租户级 `CREATE DATABASE` 权限。
运行时新建的用户库和表归该角色所有；不授予 `accountadmin`、全局表权限或租户级删库权限。
建库权限不能限制为某个名称前缀，owner 前缀也不是 SQL 权限边界；该用户可以创建其他新库，
但不应访问其他角色拥有的业务数据。计算资源仍共用；若需要租户级权限／容量隔离，应另建
MatrixOne account。重复运行上述初始化命令可补齐授权，原有密码和两个 API key 保持不变。
权限语法参见 [MatrixOne GRANT](https://docs.matrixorigin.cn/en/v26.3.0.13/MatrixOne/Reference/SQL-Reference/Data-Control-Language/grant/)。

2026-10-06 已用现有凭据重跑初始化并验证：共享库重复创建、新建自有库／表、CRUD、
ALTER、索引与自有库删除成功；另一个管理员角色拥有的临时库的读取、更新、删除均被拒绝。
权限验证仅使用合成数据，临时库已清理。部署后的最小联调也已验证 Memoria schema 初始化及首次 Add/Search。

### 测试用户与认证

通过适配器测试无需预注册 Memoria 用户，也无需为每位 AML 用户生成 Memoria API key。
客户端提供 `Authorization: Bearer <AML_ADAPTER_API_KEY>` 以及请求体中的 `user_id`。
适配器将 `[namespace, user_id]` 稳定哈希为 owner，使用内部
`Authorization: Memoria-Owner <AML_MEMORIA_MASTER_KEY>` 和 `X-User-Id: <owner>` 调用
Memoria；首次访问需要存储的用户时，Memoria 按需初始化用户库和 schema。

同一测试用户的 Add/Search 必须使用相同 `user_id`，跨会话可检索该用户的记忆。
更换 `user_id` 会进入另一用户范围；不要在重试时改变 `user_id` 或部署 owner 前缀。
AML auth key 验证调用方，`user_id` 决定数据归属，二者不能互相替代。
`AML_MEMORIA_MASTER_KEY` 和 SQL 密码仅用于内部服务，不交给 AML。
部署前仍须执行专用 SQL 用户／角色／共享库初始化；这与创建测试用户是两件事。

## GitHub Actions 配置

离线 `CI - Lint, Tests & Adapter Build` 无须手工配置 Secret。下面的运行凭据用于
`AML Aliyun dev deployment`，**preview 和 deploy 均需要**，因为 preview 也会检查
目标集群并推送镜像。源码公开仓库的 checkout 使用 Actions 自动提供的
`GITHUB_TOKEN`，当前无须额外配置 GitHub PAT。

### Secrets

| 名称 | 来源及用途 | 配置范围／当前状态 |
|---|---|---|
| `KUBECONFIG` | 必填，来自 `~/dev/unit-kubeconfig` 的完整原始 YAML；用于部署到指定阿里云 unit 集群。不填写文件路径，不做 base64 编码 | Repository Secret，已上传 |
| `PULUMI_TOKEN` | 必填，Pulumi Cloud access token；需有 `MatrixOneCloud/memory-benchmarks/new-dev` 创建／读写权限。workflow 映射为 `PULUMI_ACCESS_TOKEN` | Repository Secret，已通过本次 Pulumi up |
| `NEW_ACR_USERNAME` | 必填，Hangzhou ACR 登录用户名，用于推送和 Pod 拉取镜像 | Organization Secret，本仓库已有访问授权 |
| `NEW_ACR_PASSWORD` | 必填，与上述用户名匹配的 ACR 登录密码；需能推送／拉取适配器及 Memoria 仓库 | Organization Secret，已通过镜像推送与 Pod 拉取 |
| `AML_DATABASE_PASSWORD` | 必填，专用 `aml_memoria` SQL 用户密码，来自生成的 `deploy/.runtime.env`；供 Memoria 连接数据库 | Repository Secret，已上传 |
| `AML_MEMORIA_MASTER_KEY` | 必填，生成于 `deploy/.runtime.env`，至少 32 字符；Memoria 与适配器上游调用共用，独立于对外 key | Repository Secret，已上传 |
| `AML_ADAPTER_API_KEY` | 必填，生成于 `deploy/.runtime.env`，至少 32 字符且与 master key 不同；AML 调用 Add/Search 时使用 | Repository Secret，已上传；正式参评时向 AML 平台提供此 key |
| `AML_EMBEDDING_API_KEY` | 当前上海 embedding 服务必填，由该模型服务提供有效 key；Memoria 使用 `Authorization: Bearer <key>` 调用 | 必需 Repository Secret，已配置；无 key 返回 401，带配置 key 的真实 Add/Search 联调通过 |

2026-10-06 只读核对 GitHub 名称元数据：六个必需 Repository Secrets 均已存在，
两个必需 Organization Secrets 对本仓库可用，当前没有缺失的必需 Secret 名称。
Secret 名称核对本身不证明凭据有效；本次实际部署另行验证了集群、Pulumi、ACR、
数据库及应用认证链路。后续轮换或权限变更仍须重新验收。

配置入口是本仓库 `Settings → Secrets and variables → Actions → Secrets`。
新增 Repository Secret 时填写表中名称和值；通常只填写值，不填写 `KEY=` 或外围
引号。`KUBECONFIG` 的值是完整 YAML。组织已有的 ACR 凭据由组织维护，本仓库已有
授权，无需复制；若组织调整访问范围，需要保留 `memory_benchmarks` 的访问权限。
GitHub 配置方式见 [官方 Secrets 文档](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)。

Pulumi token 来源为 Pulumi Cloud 的 access token 管理，本项目读取的 GitHub Secret
名称是 `PULUMI_TOKEN`。组织中同名用途的 `PULUMI_ACCESS_TOKEN` 不会自动替代它，
以当前 workflow 引用为准。[Pulumi access tokens](https://www.pulumi.com/docs/administration/concepts/access-tokens/)

管理员密码不进入 workflow 或 Pod。运行 DSN 在 Pulumi 中使用独立用户密码生成，用户名
和密码按 URL 规则编码，以 secret value 传入 Kubernetes Secret；不在 stack output
中输出任何 key、DSN 或 kubeconfig。

首次配置可通过 `deploy/scripts/configure_github.py` 从本地文件上传 kubeconfig 和三个
运行 Secret，输入经 stdin 交给 `gh`，不显示或放入命令行参数。该工具同时设置适配器
镜像仓库及 Memoria 构建模式，可选配置来源 ref、Memoria 镜像仓库或现成镜像 digest；
不触发 Actions，也不启用 main 自动发布。默认 `--memoria-build-mode source`，
源码模式不要求 `--memoria-image`。

例如，在仓库根目录执行以下命令可从既有本地文件重新上传配置，命令不打印密钥值，
不会运行部署；已有配置完整时无须重跑：

```sh
rtk proxy .deploy-venv/bin/python deploy/scripts/configure_github.py \
  --kubeconfig ~/dev/unit-kubeconfig \
  --adapter-repository registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin \
  --memoria-build-mode source --memoria-ref main \
  --memoria-repository registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin
```

该脚本上传 kubeconfig、数据库密码和两种运行 key，设置镜像及来源 Variables。
Pulumi token、组织 ACR 凭据、embedding key 通过对应的 GitHub 设置单独维护。

当前发布代码只使用 Kubernetes provider，复用既有 cert-manager／ClusterIssuer，
无需在本仓库新增阿里云 RAM AccessKey、DNS 签发凭据或证书私钥。其 DNS 凭据由既有
基础设施维护。当前 Add/Search 流程不要求 LLM 或 rerank key；发布流程不运行官方
AML 评测，因此 AML 平台的评测授权 key 也不属于此 CI/CD 配置。

### Variables

| 名称 | 要求／用途 |
|---|---|
| `AML_EXPECTED_API_SERVER` | 必填，目标 unit Kubernetes API 的 HTTPS 地址；从私有 dev 配置上传 |
| `AML_EXPECTED_CLUSTER_UID` | 必填，目标 `kube-system` UID；从私有 dev 配置上传 |
| `AML_DATABASE_ACCOUNT` | 必填，现有 MatrixOne 租户 UUID；不是专用 SQL 用户密码 |
| `AML_ADAPTER_REPOSITORY` | 必填，当前为 `registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin`；需有该仓库推送权限 |
| `AML_MEMORIA_BUILD_MODE` | 可选，`source` 或 `image`；省略时按 `deploy/memoria-source.json`，默认 `source` |
| `AML_MEMORIA_REF` | 可选，Memoria 分支、tag 或 commit；省略时使用配置文件中的 `main`；手动发布可覆盖 |
| `AML_MEMORIA_REPOSITORY` | 源码模式可选，默认使用适配器的同一 ACR 仓库；当前同为 `registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin`，由不同项目 tag 前缀区分 |
| `AML_MEMORIA_IMAGE` | 仅 `image` 模式必填，现成的 `repository@sha256:...`；源码模式使用本次构建返回的 digest，忽略此变量 |
| `AML_DEV_CD_ENABLED` | 可选，`true` 才启用 main push 自动部署；默认仅手动发布 |
| `AML_FREEZE` | 可选，`true` 阻止自动和手动部署；空或 `false` 允许，其他值拒绝继续；preview 仍可执行 |
| `AML_ADAPTER_WORKERS` | 可选，正整数，默认 `1`；传给 Helm `workers` 与 Uvicorn，不增加 Pod 副本数 |
| `AML_PUBLIC_HOST` | 已设置为 `aml.cn-dev.matrixone.tech`，DNS、Ingress、证书及 HTTPS 最小联调已通过 |
| `AML_TLS_SECRET` | 可选，cert-manager 生成的 TLS Secret 名，默认 `aml-tls` |
| `AML_CERT_ISSUER` | 可选，默认复用 Ready 的 `aliyun-prod-letsencrypt` ClusterIssuer |

### 镜像名称结构

镜像 tag 引用格式为 `<registry>/<ACR namespace>/<repository>:<tag>`，
实际部署引用为 `<registry>/<ACR namespace>/<repository>@sha256:<64 位 digest>`。
当前统一使用 `registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin`：

| 服务 | ACR namespace | repository | project 前缀 |
|---|---|---|---|
| Add/Search 适配器 | `moc-pub` | `plugin` | `aml-memory-adapter` |
| 源码构建 Memoria | `moc-pub` | `plugin` | `aml-memoria` |

构建 tag 格式为 `<project>-<YYYYMMDD>-<7 位 commit SHA>`，日期在一次工作流运行中
统一取 UTC 构建日期。适配器 SHA 来自 `memory_benchmarks`，Memoria SHA 来自所选 ref
的实际 checkout；两者同仓库但使用不同项目前缀。对应完整格式为：

```text
registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin:aml-memory-adapter-<YYYYMMDD>-<benchmark 短 SHA>
registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin:aml-memoria-<YYYYMMDD>-<Memoria 短 SHA>
```

`AML_ADAPTER_REPOSITORY` 和 `AML_MEMORIA_REPOSITORY` 均配置为上述共享仓库；
Memoria 变量省略时也默认使用适配器仓库。构建脚本允许共享仓库，并生成不同前缀的 tag。
仓库 Variables 只填写 `<registry>/<namespace>/<repository>`，不带 tag 或 digest；
`AML_MEMORIA_IMAGE` 属于现成镜像模式，要求完整 digest 引用。
可选的既有现成镜像仍引用 `moc-pub/memoria` 下真实已发布的 digest；源码模式向
`moc-pub/plugin` 推送，不依赖该既有引用。此处 ACR namespace 与 Kubernetes namespace
`aml-memory-benchmarks` 独立。tag 用于查找，实际发布始终使用构建返回的 digest。
两个源码镜像已成功推送到该仓库，并由 dev Pod 成功拉取。

Memoria embedding 默认配置来自 gitops dev：`BAAI/bge-m3`、1024 维、上海服务
`https://bgem3.model.shanghai.idc.matrixorigin.cn:30443/v1`。当前 key 与配置下的 dev Memoria
写入／检索联调已通过；模型质量仍需基准评测。本适配器只调用普通存储／检索接口，
此发布配置不要求 LLM 或 rerank 模型。
不混用不同 embedding 模型的既有向量；固定模型后再建立质量基线。

2026-10-06 从本机使用正常 HTTPS 证书校验，向上述服务的 `/embeddings` 发送一条
`model=BAAI/bge-m3` 的短文本请求，不发送 Authorization 或其他 API key。服务在约
0.071 秒内返回 HTTP `401`，内容为 `{"error":"Unauthorized"}`。
**当前公网接口不接受匿名调用，部署必须提供 `AML_EMBEDDING_API_KEY`。**
workflow 配置检查与 Pulumi 程序均将该 key 列为必填，缺失时在创建应用资源前失败。
匿名探针仅验证鉴权行为；部署后的真实 Add/Search 已使用配置的 key 和模型完成。
该最小测试没有独立检查模型原始向量长度，也不提供检索质量评分。

当前 `AML_MEMORIA_IMAGE` 为
`registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin@sha256:16d7eefa847749ffd4d61569419110a52b8de8fb78a283a8cd57df004d04ea8b`，
对应 `main@81c5cc6` 的源码构建产物。只有选择 `image` 模式才使用该 Variable；
源码模式默认构建 `main`，流程不会自动将 Variable 改写为构建结果。更换现成版本时更新
`AML_MEMORIA_IMAGE`，并保留构建来源与对应质量基线。

### 从 Memoria 源码构建

Memoria 源码构建只在本项目的 **AML Aliyun dev deployment** 中执行。
日常 PR／main push 的 **CI - Lint, Tests & Adapter Build** 保留适配器镜像检查，不构建 Memoria。

| 发布选择 | 是否构建 Memoria |
|---|---|
| `deployment_target=both` 或 `memoria`，有效模式为 `source` | 构建并推送，`preview` 和 `deploy` 均执行 |
| `deployment_target=memory_benchmarks` | 跳过，保留当前 Memoria 镜像 |
| 更新 Memoria，有效模式为 `image` | 跳过，使用指定 digest 镜像 |

`configured` 按已有配置解析为 `source` 或 `image`。若启用 main push 自动发布，
该发布 workflow 也会按上述规则构建；自动发布默认关闭。
默认来源在 [memoria-source.json](memoria-source.json)：

```json
{"default_mode": "source", "repository": "matrixorigin/memoria", "ref": "main"}
```

手动发布时 `memoria_build_mode=configured` 使用仓库配置，选择 `source`／`image`
可覆盖本次模式；`memoria_ref` 留空时使用 `AML_MEMORIA_REF` 或配置文件中的 `main`。
填入 `feature/example` 可构建该分支，填入 tag 或完整 commit SHA 可指定相应版本。
benchmark workflow 自身仍从 `memory_benchmarks/main` 运行；Memoria 来源 ref 与它独立。
更换源码仓库时编辑上述 JSON，默认仓库是公开仓库，跨仓库 checkout 使用 GitHub token。
来源分支／tag 必须存在于该远端仓库；本地未推送的修改不会进入发布 workflow 的源码构建。
Memoria 仓库有新提交不会自动触发本仓库的发布，需要手动触发，或由已启用的
本仓库 main push 发布读取其最新版本。

工作流 checkout 来源 ref 后记录实际完整 commit；构建期间继续使用这份 checkout。
镜像推送至 `registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin`，以
`aml-memoria-<UTC 构建日期>-<实际 SHA 前 7 位>` 标记，再按构建返回的 digest 部署。
适配器使用同仓库和不同项目前缀；需要确保仓库存在且 ACR 凭据具备推送／拉取权限。
本次 CD 已验证两个镜像的推送与 Pod 拉取权限。

构建使用本项目的 [Memoria Dockerfile](docker/memoria.Dockerfile)，上下文是检出的
Memoria 仓库根目录，使用 `cargo build --locked --release --bin memoria`。
Rust `1.90.0` builder 和 Debian Bookworm runtime 均固定镜像 digest，使用 BuildKit
Cargo cache mounts 和 GHA 层缓存；临时 runner 不保证跨运行保留 cache mount 内容。
运行进程为 UID 1000，执行 `memoria serve`，日志输出到标准输出。
原 Memoria Dockerfile 默认 Rust `1.85`，而当前 lock 中含要求 `1.88` 的依赖；
此独立构建文件在本项目维护，源码分支不需要包含新的 Dockerfile。以后依赖提高最低
编译器版本时，应在本项目更新 builder 并验收构建。

本地使用同一构建文件的示例：

```sh
cd ~/runtime-agent/memoria
rtk proxy docker build --platform linux/amd64 \
  -f ../memory_benchmarks/deploy/docker/memoria.Dockerfile -t memoria:aml-local .
```

本地未执行此示例命令。发布 workflow 的源码模式负责 checkout、构建、推送及 digest
传递；构建失败会阻止后续发布。`image` 模式跳过 Memoria checkout 和构建。

CD 将 ref、实际完整 commit、构建 tag／UTC 日期、镜像 digest、构建 Dockerfile hash 和 benchmark commit
保存到 Actions summary 与 `memoria-build-<run id>-<attempt>` artifact，保留 30 天；
Pulumi output 和 Memoria Pod annotation 同时记录来源 repo／ref／commit。
该记录证明镜像选择，不能单独证明部署成功或评测通过。评测报告应另存这些版本信息。
使用 `main` 时不同运行可能得到不同 commit；需要重放 preview 或某次评测的版本时，
将记录中的完整 commit 填入 `memoria_ref`，或使用 `image` 模式指定那次镜像 digest。

在本项目接入源码构建的范围与版本记录要求见
[方案中的镜像选择与源码构建](../agent_memory_leaderboard/docs/competition-plan.md#memoria-镜像选择与源码构建)。

## 发布流程

### Actions 参数填写

在本仓库 **Actions → AML Aliyun dev deployment → Run workflow** 操作。
目前已有完整 dev 部署，后续使用同一 stack 更新已有服务；不用重新创建数据库或测试用户。

| 场景 | workflow 分支 | operation | deployment_target | memoria_build_mode | memoria_ref |
|---|---|---|---|---|---|
| 首次部署／更新两个服务 | `main` | `deploy` | `both` | `source` | `main` 或指定 Memoria ref |
| 只更新 Memoria | `main` | `deploy` | `memoria` | `source` | 指定 Memoria 分支、tag 或 SHA |
| 只更新适配器 | `main` | `deploy` | `memory_benchmarks` | `configured`（此场景忽略） | 留空（此场景忽略） |
| 预览上述变更 | `main` | `preview` | 对应范围 | 与实际发布一致 | 与实际发布一致 |

workflow 的分支决定 `memory_benchmarks` 代码；`memoria_build_mode` 和 `memoria_ref`
只控制 **Memoria**。`source` 从源码构建；`configured` 读取已配置模式（默认源码）；
`image` 使用 `AML_MEMORIA_IMAGE` 的固定 digest 镜像。ref 留空时使用已配置来源，默认 `main`。
输入 `deploy` 才实际更新应用；需要先检查变更时按下面的 preview／deploy 两步执行。

### 执行步骤

1. 将变更合入 `main`，填写上述 Secrets／Variables。
2. Actions 中选择 **AML Aliyun dev deployment**，点击 **Run workflow**。
   workflow 分支选择 `main`，`operation` 选择 `preview`，`deployment_target` 选择 `both`，
   `memoria_build_mode` 选择 `source`，`memoria_ref` 填 `main`。
   它先选择
   Memoria 来源并检出源码，核对目标集群、权限和 StorageClass，运行离线检查，
   构建并发布适配器及所选 Memoria 镜像，再预览独立 Pulumi stack。
   **preview 也会发布镜像，并可能创建空的 Pulumi stack，但不创建 Kubernetes 应用资源。**
3. 再次点击 **Run workflow**，保持上述分支／来源参数，将 `operation` 改为 `deploy`
   执行发布。程序等待 Helm 和 Pod 就绪，单个 dev 发布串行执行。
   任务入口和 `up` 前检查冻结，Pulumi 对真实更新也检查 `AML_FREEZE`。
   自动部署默认关闭，Actions 不调用真实 Add/Search 或启动基准／官方评测。
4. 核对公网域名的 DNS 和 `AML_PUBLIC_HOST`（当前均已配置）。chart 创建应用自己的 `Certificate`，
   现有 cert-manager 使用阿里云 DNS-01 自动签发、保存及续期 TLS Secret；发布等待
   Certificate Ready。DNS A 记录应指向 unit 集群
   NGINX 公网地址；2026-10-06 只读核实的地址是 `47.97.230.53`，以发布时实际值为准。
5. 公网启用后的目标接口为 `https://<host>/add` 和 `https://<host>/search`。
   验收 DNS、证书、鉴权、真实写入检索、用户隔离、重启恢复和端到端时延后再参评。

### 构建与部署的 runner 隔离

发布 workflow 分成两个顺序执行的 job，工作流级并发锁覆盖整个流程：

1. **build**：完成配置／集群预检、离线检查、未选应用快照、所选镜像构建与推送。
   只输出固定镜像 digest、Memoria 来源与快照 SHA-256；构建记录继续保存为原有 artifact。
2. **deploy**：使用新的 runner，重新生成私有配置、读取凭据并核对集群及当前部署。
   单项发布核对跨 job 快照摘要，以及执行 Pulumi 前的实时快照。变化时停止，重新发起运行。
   然后执行 Go 检查，编译到 `deploy/pulumi/.bin/aml-pulumi`，在 CI checkout 内配置 Go
   runtime 的 `binary` 选项，preview／up 直接运行该二进制。

Docker／Rust 构建存储与 Go／Pulumi 部署编译存储分别位于两个 runner。
`deploy/pulumi/Pulumi.yaml` 在仓库中仍采用默认 Go runtime，本地 Pulumi 使用方式保持一致；
只有 CI checkout 被修改为使用预编译程序。
[Pulumi Go 预编译程序说明](https://www.pulumi.com/docs/iac/languages-sdks/go/)

磁盘预检分别要求适配器构建前至少 3 GiB、Memoria 源码构建前至少 12 GiB、
部署程序编译前至少 6 GiB、执行 Pulumi 前至少 2 GiB 可用空间，同时检查 workspace
与 `RUNNER_TEMP`。不足时输出阶段和可用容量并停止；这些是启动阈值，不是构建峰值保证，
后续可按实际使用量调整。不要在同一个已耗尽磁盘的 runner 上重试编译。

kubeconfig、运行凭据、生成的私有 stack 配置和完整 Helm values 不进入 job outputs
或 artifact。两个 job 各自清理临时 kubeconfig、私有 stack 配置与本地快照；
部署 job 单独读取 `PULUMI_TOKEN`。冻结检查在 job 入口及真实部署前继续生效。

单个 **build** job 最长 90 分钟，**deploy** job 最长 60 分钟。
选择 `image` 且仅更新 Memoria 时，流程跳过 Docker 构建环境和镜像构建，直接使用配置的 digest。
参数填写、首次部署、单项更新、镜像命名及证书等待方式继续使用上述说明。

### ACR 推送格式

两个镜像的构建步骤均使用 `provenance: false` 和 `sbom: false`。当前 ACR 入口不接受
BuildKit 默认 provenance 附件的 `application/vnd.oci.empty.v1+json`，会在 manifest 推送
阶段拒绝请求。若出现 `unknown manifest class`，应使用已包含上述设置的最新 `main`
发起新的 Run workflow；重新运行旧记录仍会执行旧版本的 workflow。

镜像 digest、来源 SHA 和构建记录继续保留；这些记录用于追溯版本，不构成签名证明。
[Docker 构建附件说明](https://docs.docker.com/build/metadata/attestations/)

### 选择更新范围

`deployment_target` 控制本次更新的应用，默认 `both`。`operation=preview`／`deploy` 均支持。

| 选择 | 发布行为 |
|---|---|
| `both` | 更新 Memoria 和 memory_benchmarks 的 AML Add/Search 适配器；首次部署选择此项 |
| `memoria` | 按 `memoria_build_mode`／`memoria_ref` 构建或选择 Memoria 镜像，复用当前适配器镜像和应用配置 |
| `memory_benchmarks` | 构建并更新 AML 适配器，复用已部署 Memoria 的实际 digest、来源和应用配置；忽略 Memoria 构建参数 |

单项更新需要已有完整的 `aml` Helm release 和两个 Deployment。流程从目标集群读取当前
Helm values，核对实际镜像，保留未选应用的配置和共享 checksum；不会用仓库 Variable 中的
旧镜像替代实际部署。构建结束、Pulumi 操作前再次核对现有配置，变化时停止并要求重新运行。

当前共享数据库连接和认证／embedding key 必须与已部署 Secret 一致。要轮换这些配置，
应选 `both`。chart 改动若会更新未选应用的 Deployment，也必须选 `both`。
单项更新保留当前 Ingress、证书配置等共享 Helm values；共享资源配置更新使用 `both`。
无需新增 Secrets。自动部署若启用仍默认 `both`，冻结规则对所有更新范围生效。

Memoria 更新采用 Recreate，更新期间适配器进程可继续运行，但其 Add/Search 上游会暂时
不可用；比赛评测期间仍应冻结发布。这里的 `memory_benchmarks` 指运行中的 AML 适配器，
发布不会自动运行 benchmark 或官方评测。

离线检查流程是 **CI - Lint, Tests & Adapter Build**，不会部署应用。CI 使用固定版本
`actionlint v1.7.7` 检查 workflow YAML 和 Actions 表达式。若部署流程仅显示文件路径、
立即失败且没有 job，应先检查 workflow 文件语法；这种失败不会创建应用资源。

NGINX 路由设置 1800 秒读／发送超时和关闭上游自动重试，以支持同步 Add。
整个链路的 SLB、控制器限制和 AML 契约还要核实；这不构成已支持所有 30 分钟请求的证明。
不能将控制面 `mocloud-alb` 的设置直接复制到当前只有 `nginx` 的 unit 集群。

证书自动化复用 **cert-manager + 现有 ClusterIssuer**，TLS Secret 由 cert-manager
持续管理。CI/CD 声明应用自己的 Certificate，不保存证书私钥，也不部署第二套签发／
续期任务。当前集群已有 cert-manager `v1.12.1`，三个组件均有可用副本，生产／staging
Issuer 均为 Ready；此 CD 不安装或升级共享控制器。该旧版本的维护属于集群基础设施职责。

当前生产 Issuer 的 DNS webhook 配置范围为 `cn-dev.matrixone.tech`；已选定并配置
`aml.cn-dev.matrixone.tech`。2026-10-06 公共解析器 `223.5.5.5` 的 TCP 查询确认
A 记录为 `47.97.230.53`、TTL 600 秒，与只读核查的 NGINX LoadBalancer IP 一致。
应用 Ingress 已创建，`aml-tls` 证书已由该 Issuer 签发并 Ready。域名不属于该范围时，
需要基础设施维护者提供对应 Issuer，不能仅修改应用域名后假定签发成功。
[cert-manager DNS-01](https://cert-manager.io/docs/configuration/acme/dns01/)

DNS 解析区域为 `cn-dev.matrixone.tech`（已委派给 `ns1.alidns.com`／`ns2.alidns.com`），
域名管理员已配置以下记录；后续修改须使用拥有该区域权限的账号：

| 字段 | 值 |
|---|---|
| 主机记录 | `aml` |
| 类型 | `A` |
| 记录值 | `47.97.230.53`（部署时核对 unit NGINX LoadBalancer） |
| 线路 | 默认 |
| TTL | 600 秒 |

cert-manager 自动管理证书验证 TXT 和续期，不自动创建业务 A 记录；本应用流程也未
安装 ExternalDNS。这条 A 记录由域名管理员一次性配置，后续入口 IP 变化时维护。
有正确区域权限时可以通过阿里云 CLI／API 添加，不要求只能在控制台操作。
[阿里云添加记录 API](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-adddomainrecord)

Let’s Encrypt 当前默认有效期为 90 天，覆盖一个月比赛；以实际 Certificate 的 `notAfter`
为准，续期持续由 cert-manager 管理。
[Let’s Encrypt 有效期说明](https://letsencrypt.org/docs/cert-lifetimes/)

部署后如需本地联调，可转发适配器：

```sh
kubectl --kubeconfig "$HOME/dev/unit-kubeconfig" -n aml-memory-benchmarks \
  port-forward service/aml 8000:80
```

`/health` 直接在事件循环检查存活；`/ready` 使用独立线程容量和 HTTP 连接检查 journal
可读及 Memoria `/health`，每个 worker 最多一个检查，并发探针直接返回 503，不排队。
ready 不验证运行 master key、数据库授权、embedding 或 Add/Search 质量。

## 接入已部署的 Add/Search

客户端只需要公网地址和 **`AML_ADAPTER_API_KEY`**，无需 kubeconfig、数据库密码或
`MEMORIA_MASTER_KEY`，也无需预注册 Memoria 用户。运维从受控的 `deploy/.runtime.env`
取得适配器 key 并提供给调用方；GitHub Secret 页面不能回读已保存的值。
AML 平台配置 Add URL、Search URL 和该 key；你在本地联调也使用同样的接口。

| 配置 | 值 |
|---|---|
| Add URL | `https://aml.cn-dev.matrixone.tech/add` |
| Search URL | `https://aml.cn-dev.matrixone.tech/search` |
| 认证 Header | `Authorization: Bearer <AML_ADAPTER_API_KEY>` |
| Content-Type | `application/json` |
| 交互式接口说明 | `https://aml.cn-dev.matrixone.tech/docs` |

### curl：写入后检索

先设置环境变量。示例 key 是占位符，不是实际凭据；运行时替换为自己的 key。
每次独立演示生成新用户，避免与其他测试数据混在一起。

```sh
export AML_BASE_URL='https://aml.cn-dev.matrixone.tech'
export AML_ADAPTER_API_KEY='<实际适配器 key>'
export AML_DEMO_USER="aml-demo-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"

curl --fail-with-body -sS "$AML_BASE_URL/health"
curl --fail-with-body -sS "$AML_BASE_URL/ready"

curl --fail-with-body -sS --max-time 120 "$AML_BASE_URL/add" \
  -H "Authorization: Bearer $AML_ADAPTER_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<JSON
{
  "request_id": "eval:demo:conv-0:chunk-0",
  "user_id": "$AML_DEMO_USER",
  "session_id": "session-1",
  "messages": [
    {"role": "user", "content": "Orion project uses Rust for the server.", "timestamp": 1704067200000}
  ]
}
JSON

curl --fail-with-body -sS --max-time 120 "$AML_BASE_URL/search" \
  -H "Authorization: Bearer $AML_ADAPTER_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<JSON
{"user_id": "$AML_DEMO_USER", "query": "What language does the Orion server use?", "top_k": 10}
JSON
```

Add 成功返回 HTTP 200 和以下结构，三个 ID 与请求完全一致：

```json
{
  "success": true,
  "request_id": "eval:demo:conv-0:chunk-0",
  "user_id": "<本次 AML_DEMO_USER>",
  "session_id": "session-1"
}
```

Search 返回记忆证据，不生成最终答案。以下为结构示意；ID、内容格式、分数和时间取决于
实际后端结果，`score` 和 `created_at` 为可选字段，无匹配时返回 `{"data": []}`。

```json
{"data": [{"id": "<memory id>", "content": "<包含 Orion server uses Rust 的记忆证据>"}]}
```

### Python：只用标准库

先设置上述 `AML_BASE_URL` 和 `AML_ADAPTER_API_KEY`，将下面保存为 `aml_demo.py`，
运行 `python3 aml_demo.py`。它发送一条 Add 和一条 Search，不启动本地服务或评测。

```python
import json
import os
import time
import uuid
from urllib.request import Request, urlopen

base = os.getenv("AML_BASE_URL", "https://aml.cn-dev.matrixone.tech").rstrip("/")
api_key = os.environ["AML_ADAPTER_API_KEY"]
run_id = uuid.uuid4().hex
user_id = f"aml-demo:{run_id}:alice"


def post(path, payload):
    request = Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        return json.load(response)


add_payload = {
    "request_id": f"eval:{run_id}:conv-0:chunk-0",
    "user_id": user_id,
    "session_id": "session-1",
    "messages": [{
        "role": "user",
        "content": "Orion project uses Rust for the server.",
        "timestamp": int(time.time() * 1000),
    }],
}
print("Add:", json.dumps(post("/add", add_payload), ensure_ascii=False))
print("Search:", json.dumps(post("/search", {
    "user_id": user_id,
    "query": "What language does the Orion server use?",
    "top_k": 10,
}), ensure_ascii=False))
```

### 请求字段与使用规则

| 字段 | 要求／用途 |
|---|---|
| Add `request_id` | 必填非空字符串，整体视为不透明 ID；相同用户的不同写入使用不同 ID，原样重试保留 ID 和完整 payload |
| `user_id` | Add/Search 均必填非空字符串，决定记忆归属；同一用户跨会话检索仍用相同值 |
| Add `session_id` | 必填非空字符串，用于来源会话；Search 不需要它，不按会话限制检索 |
| Add `messages` | 必填非空数组，每条包含 `role` 和 `content` |
| message `role` | `user` 或 `assistant` |
| message `content`、Search `query` | 非空文本，或非空的 `[{"type":"text","text":"..."}]` 数组；当前不接受图片 |
| message `timestamp` | 可选，整数 Unix 毫秒，例如 `1704067200000`；不是秒或 ISO 字符串 |
| Search `top_k` | 必填正整数，返回数量不超过此值；当前后端最多检索 100 条 |
| Search `options` | 可选字符串数组；当前接受该字段，但不用于生成答案 |

未知请求字段会被拒绝。相同 `user_id`／`request_id` 原样重试不会重复写入；
同一 ID 改 payload 返回 400。最小示例没有自动重试或清理：新增写入会留在该随机用户下。
需要完整的验证和测试记忆逻辑停用时，使用已有 `src.smoke` 并配置内部 Memoria 访问；
它需要额外 master key，普通 Add/Search 客户端不需要。
错误码和重试规则见[适配器说明](../agent_memory_leaderboard/README.md#retries-and-deployment-state)。
示例客户端的 120 秒超时仅供小请求联调，不代表正式 AML 整批 Add 的总超时要求。

## 发布冻结与上线验收

进入正式评测前设置 `AML_FREEZE=true`，关闭自动部署，取消排队的 deploy，等待已运行
发布完成，再核对运行镜像 digest。Repository Variable 在任务中按当次解析值使用，
两次检查不表示实时重新读取；更改变量不会取消已经运行的任务。冻结期间不手工执行
`up`、重启应用或更新运行 Secret。preview 仍会发布镜像，不能当作完全只读操作。

CI/CD 已固定 Actions commit SHA，部署依赖安装强制校验 PyPI 发布 hash 并使用 wheel；
离线检查增加 Helm lint／template，覆盖内部入口＋单 worker 和公网 TLS＋两个 worker。
健康探针的八个用例覆盖健康、上游非 2xx／连接失败／超时、journal 不可用，
以及 40 个 Search 占满业务线程池时探针仍可用、并发 ready 不排队。

正式参评前仍须验收：

- 当前源码 digest 在 UID 1000／只读根文件系统下启动、schema 初始化和 Add/Search 已通过。
  重启恢复仍未验证；后续来源版本也须核对日志中的 `Read-only file system`／`Permission denied`。只有证据明确需要
  额外可写目录时才增加最小挂载；不预先取消只读限制。
- 核对公网负载均衡实例的产品、listener 和真实超时。验证无中间响应的 5 分钟请求，
  继续验证超过 900 秒以及接近组别契约上限的请求，并验证超时后的恢复。记录准确耗时、
  HTTP 状态、客户端／Ingress 日志；短请求通过或流式心跳不能代替同步 Add 验收。
- worker 默认保持 1。调整 `AML_ADAPTER_WORKERS` 后测量吞吐、P50／P95、错误率、
  内存以及 journal／数据库／embedding 压力，再决定容量；正式评测期间冻结参数。

## 发布与评测分开

手动评测入口为 **Actions → AML benchmark**，支持 LoCoMo-Refined 和 LongMemEval-S，
默认一个完整历史。它复用现有 `AML_ADAPTER_API_KEY`，不使用部署凭据；与 CD 共用并发组，
防止 workflow 发布中断正在执行的 benchmark。Summary 和下载报告的操作步骤见
[benchmark workflow 说明](../agent_memory_leaderboard/benchmarks/CI.md)。

CI/CD 完成服务发布；基准由明确的评测任务触发。owner 前缀、独立用户、专用库和
独立 Memoria 隔离权限及数据，MatrixOne、embedding、NGINX 和节点容量仍可能共享。
从一次小集任务开始测量吞吐、P50／P95、错误和模型额度；全局并发、速率与总量预算
尚未在适配器实现。正式 Full 前的质量／恢复问题见
[比赛设计](../agent_memory_leaderboard/docs/competition-plan.md)。
