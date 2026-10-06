# 阿里云 dev 发布

本目录在阿里云 **dev unit 集群**部署专用 Memoria 和 AML Add/Search 适配器。
CI/CD、Helm chart 和 Pulumi 程序全部在 `memory_benchmarks` 维护。

## 实施状态与 Review

部署配置已编写，**Memoria 和适配器尚未实际部署**。专用数据库用户／角色／库和
GitHub 运行凭据已准备；本地 Ruff、Go 编译及两组 Helm 渲染通过。本地 pytest、
镜像构建、Pulumi preview／up、真实 Add/Search 及公网验收尚未执行；Actions 结果以
PR checks 为准。
本地渲染使用 Helm 4.1.4，CI/CD 固定 Helm 3.17.4，后者仍待 CI 验证。
Memoria 默认从 `matrixorigin/memoria` 的 `main` 分支构建，
也支持选择其他分支、tag、commit 或已发布镜像；实际部署始终使用镜像 digest。
本轮未执行源码构建，也未修改 Memoria 源码。

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
| 数据库 | 现有 dev MatrixOne 租户下的专用 `aml_memoria` 数据库 |
| 数据库用户／角色 | `aml_memoria`／`aml_memoria_role`，权限限制在该数据库 |

私有 `new-dev` 配置固定了 API Server 和 `kube-system` UID，阻止误用 AWS 或控制面
kubeconfig。此 stack 管理新建的应用资源，复用 unit 集群、MatrixOne 和 NGINX。
namespace 与 journal PVC 受 Pulumi `protect` 保护；删除环境时需要显式解除保护。
数据库用户、角色和库在一次性初始化步骤中创建，不由 Helm 卸载或 Pulumi destroy 删除。

同名 `new-dev` 存在于多个 gitops Pulumi project 中。控制面
`cloud-service/new-dev` 的 `moi-main/moi-memoria` 属于另一集群，本项目计划部署自己的
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
新建整个租户。角色拥有专用库和其表上的权限，不授予 `accountadmin`。
权限语法参见 [MatrixOne GRANT](https://docs.matrixorigin.cn/en/v26.3.0.13/MatrixOne/Reference/SQL-Reference/Data-Control-Language/grant/)。

## GitHub Actions 配置

离线 `AML offline CI` 无须手工配置 Secret。下面的运行凭据用于
`AML Aliyun dev deployment`，**preview 和 deploy 均需要**，因为 preview 也会检查
目标集群并推送镜像。源码公开仓库的 checkout 使用 Actions 自动提供的
`GITHUB_TOKEN`，当前无须额外配置 GitHub PAT。

### Secrets

| 名称 | 来源及用途 | 配置范围／当前状态 |
|---|---|---|
| `KUBECONFIG` | 必填，来自 `~/dev/unit-kubeconfig` 的完整原始 YAML；用于部署到指定阿里云 unit 集群。不填写文件路径，不做 base64 编码 | Repository Secret，已上传 |
| `PULUMI_TOKEN` | 必填，Pulumi Cloud access token；需有 `MatrixOneCloud/memory-benchmarks/new-dev` 创建／读写权限。workflow 映射为 `PULUMI_ACCESS_TOKEN` | Repository Secret，已存在，实际权限待发布验证 |
| `NEW_ACR_USERNAME` | 必填，Hangzhou ACR 登录用户名，用于推送和 Pod 拉取镜像 | Organization Secret，本仓库已有访问授权 |
| `NEW_ACR_PASSWORD` | 必填，与上述用户名匹配的 ACR 登录密码；需能推送／拉取适配器及 Memoria 仓库 | Organization Secret，本仓库已有访问授权，实际权限待发布验证 |
| `AML_DATABASE_PASSWORD` | 必填，专用 `aml_memoria` SQL 用户密码，来自生成的 `deploy/.runtime.env`；供 Memoria 连接数据库 | Repository Secret，已上传 |
| `AML_MEMORIA_MASTER_KEY` | 必填，生成于 `deploy/.runtime.env`，至少 32 字符；Memoria 与适配器上游调用共用，独立于对外 key | Repository Secret，已上传 |
| `AML_ADAPTER_API_KEY` | 必填，生成于 `deploy/.runtime.env`，至少 32 字符且与 master key 不同；AML 调用 Add/Search 时使用 | Repository Secret，已上传；正式参评时向 AML 平台提供此 key |
| `AML_EMBEDDING_API_KEY` | 当前上海 embedding 服务必填，由该模型服务提供有效 key；Memoria 使用 `Authorization: Bearer <key>` 调用 | 必需 Repository Secret，已配置；无 key 请求已返回 401，有效 key 调用尚未验收 |

2026-10-06 只读核对 GitHub 名称元数据：六个必需 Repository Secrets 均已存在，
两个必需 Organization Secrets 对本仓库可用，当前没有缺失的必需 Secret 名称。
此核对没有读取 Secret 值，也不证明
凭据有效、未过期或具备实际发布权限。

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
| `AML_PUBLIC_HOST` | 可选，设置后启用 HTTPS Ingress；未设置时仅部署集群内服务 |
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
新构建镜像尚未推送，仓库存在性与推送／拉取权限仍待验收。

Memoria embedding 默认配置来自 gitops dev：`BAAI/bge-m3`、1024 维、上海服务
`https://bgem3.model.shanghai.idc.matrixorigin.cn:30443/v1`。它的可达性、鉴权和真实质量
仍需验收。本适配器只调用普通存储／检索接口，此发布配置不要求 LLM 或 rerank 模型。
不混用不同 embedding 模型的既有向量；固定模型后再建立质量基线。

2026-10-06 从本机使用正常 HTTPS 证书校验，向上述服务的 `/embeddings` 发送一条
`model=BAAI/bge-m3` 的短文本请求，不发送 Authorization 或其他 API key。服务在约
0.071 秒内返回 HTTP `401`，内容为 `{"error":"Unauthorized"}`。
**当前公网接口不接受匿名调用，部署必须提供 `AML_EMBEDDING_API_KEY`。**
workflow 配置检查与 Pulumi 程序均将该 key 列为必填，缺失时在创建应用资源前失败。
这一探针只验证了本机无 key 请求的鉴权行为；尚未验证有效 key、向量维度或 dev Pod
到模型服务的调用。配置后需进行一次带 key 的最小请求验收。

当前保留的 `image` 模式镜像来自 gitops dev 已保存版本
`apiserver-20260817-a8c0397-dbfc6241`，已读取 registry manifest 并固定为
`registry.cn-hangzhou.aliyuncs.com/moc-pub/memoria@sha256:26c237a3da16143ff72d74008ac1fe733652944ac5f90b1f2d0ee5d1e87f6147`。
已核实 amd64 架构和 `memoria serve` 启动命令；运行 API、数据库授权和真实检索兼容性
仍需在本项目验收。只有选择 `image` 模式才使用它；源码模式默认构建 `main`，
不会将这份 GitHub Variable 改写为构建结果。更换现成版本时更新
`AML_MEMORIA_IMAGE` 并保留对应质量基线。

### 从 Memoria 源码构建

构建入口在本项目的 `AML offline CI` 和 `AML Aliyun dev deployment`。
默认来源在 [memoria-source.json](memoria-source.json)：

```json
{"default_mode": "source", "repository": "matrixorigin/memoria", "ref": "main"}
```

手动发布时 `memoria_build_mode=configured` 使用仓库配置，选择 `source`／`image`
可覆盖本次模式；`memoria_ref` 留空时使用 `AML_MEMORIA_REF` 或配置文件中的 `main`。
填入 `feature/example` 可构建该分支，填入 tag 或完整 commit SHA 可指定相应版本。
benchmark workflow 自身仍从 `memory_benchmarks/main` 运行；Memoria 来源 ref 与它独立。
更换源码仓库时编辑上述 JSON，默认仓库是公开仓库，跨仓库 checkout 使用 GitHub token。
来源分支／tag 必须存在于该远端仓库；本地未推送的修改不会进入 CI 构建。
Memoria 仓库有新提交不会自动触发本仓库的发布，需要手动触发，或由已启用的
本仓库 main push 发布读取其最新版本。

工作流 checkout 来源 ref 后记录实际完整 commit；构建期间继续使用这份 checkout。
镜像推送至 `registry.cn-hangzhou.aliyuncs.com/moc-pub/plugin`，以
`aml-memoria-<UTC 构建日期>-<实际 SHA 前 7 位>` 标记，再按构建返回的 digest 部署。
适配器使用同仓库和不同项目前缀；需要确保仓库存在且 ACR 凭据具备推送／拉取权限。
本轮未验证这些权限。

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

本轮未执行此命令。PR CI 从来源 ref 构建 Memoria，但不推送；CD 的源码模式负责
checkout、构建、推送及 digest 传递。`image` 模式跳过 Memoria checkout 和构建。

CD 将 ref、实际完整 commit、构建 tag／UTC 日期、镜像 digest、构建 Dockerfile hash 和 benchmark commit
保存到 Actions summary 与 `memoria-build-<run id>-<attempt>` artifact，保留 30 天；
Pulumi output 和 Memoria Pod annotation 同时记录来源 repo／ref／commit。
该记录证明镜像选择，不能单独证明部署成功或评测通过。评测报告应另存这些版本信息。
使用 `main` 时不同运行可能得到不同 commit；需要重放 preview 或某次评测的版本时，
将记录中的完整 commit 填入 `memoria_ref`，或使用 `image` 模式指定那次镜像 digest。

在本项目接入源码构建的范围与版本记录要求见
[方案中的镜像选择与源码构建](../agent_memory_leaderboard/docs/competition-plan.md#memoria-镜像选择与源码构建)。

## 发布流程

1. 将变更合入 `main`，填写上述 Secrets／Variables。
2. Actions 中选择 **AML Aliyun dev deployment** → `preview`。它先选择
   Memoria 来源并检出源码，核对目标集群、权限和 StorageClass，运行离线检查，
   构建并发布适配器及所选 Memoria 镜像，再预览独立 Pulumi stack。
   **preview 也会发布镜像，并可能创建空的 Pulumi stack，但不创建 Kubernetes 应用资源。**
3. 选择 `deploy` 执行发布。程序等待 Helm 和 Pod 就绪，单个 dev 发布串行执行。
   任务入口和 `up` 前检查冻结，Pulumi 对真实更新也检查 `AML_FREEZE`。
   自动部署默认关闭，Actions 不调用真实 Add/Search 或启动基准／官方评测。
4. 配置公网域名的 DNS 并启用 `AML_PUBLIC_HOST`。chart 创建应用自己的 `Certificate`，
   现有 cert-manager 使用阿里云 DNS-01 自动签发、保存及续期 TLS Secret；发布等待
   Certificate Ready。DNS A 记录应指向 unit 集群
   NGINX 公网地址；2026-10-06 只读核实的地址是 `47.97.230.53`，以发布时实际值为准。
5. 公网启用后的目标接口为 `https://<host>/add` 和 `https://<host>/search`。
   验收 DNS、证书、鉴权、真实写入检索、用户隔离、重启恢复和端到端时延后再参评。

NGINX 路由设置 1800 秒读／发送超时和关闭上游自动重试，以支持同步 Add。
整个链路的 SLB、控制器限制和 AML 契约还要核实；这不构成已支持所有 30 分钟请求的证明。
不能将控制面 `mocloud-alb` 的设置直接复制到当前只有 `nginx` 的 unit 集群。

证书自动化复用 **cert-manager + 现有 ClusterIssuer**，TLS Secret 由 cert-manager
持续管理。CI/CD 声明应用自己的 Certificate，不保存证书私钥，也不部署第二套签发／
续期任务。当前集群已有 cert-manager `v1.12.1`，三个组件均有可用副本，生产／staging
Issuer 均为 Ready；此 CD 不安装或升级共享控制器。该旧版本的维护属于集群基础设施职责。

当前生产 Issuer 的 DNS webhook 配置范围为 `cn-dev.matrixone.tech`；例如可选择
`aml.cn-dev.matrixone.tech`，但需要先确认域名未被占用并配置 DNS。域名不属于该范围时，
需要基础设施维护者提供对应 Issuer，不能仅修改应用域名后假定签发成功。
[cert-manager DNS-01](https://cert-manager.io/docs/configuration/acme/dns01/)

Let’s Encrypt 当前默认有效期为 90 天，覆盖一个月比赛；以实际 Certificate 的 `notAfter`
为准，续期持续由 cert-manager 管理。
[Let’s Encrypt 有效期说明](https://letsencrypt.org/docs/cert-lifetimes/)

部署后如需本地联调，可转发适配器：

```sh
kubectl --kubeconfig "$HOME/dev/unit-kubeconfig" -n aml-memory-benchmarks \
  port-forward service/aml 8000:80
```

`/health` 检查存活，`/ready` 检查 journal 可读及 Memoria `/health`。
ready 不验证运行 master key、数据库授权、embedding 或 Add/Search 质量。

## 发布冻结与上线验收

进入正式评测前设置 `AML_FREEZE=true`，关闭自动部署，取消排队的 deploy，等待已运行
发布完成，再核对运行镜像 digest。Repository Variable 在任务中按当次解析值使用，
两次检查不表示实时重新读取；更改变量不会取消已经运行的任务。冻结期间不手工执行
`up`、重启应用或更新运行 Secret。preview 仍会发布镜像，不能当作完全只读操作。

CI/CD 已固定 Actions commit SHA，部署依赖安装强制校验 PyPI 发布 hash 并使用 wheel；
离线检查增加 Helm lint／template，覆盖内部入口＋单 worker 和公网 TLS＋两个 worker。
`/ready` 的六个用例覆盖健康、上游非 2xx／连接失败／超时及 journal 不可用。

首次部署仍须验收：

- 在当前非 root／只读文件系统配置下确认 Memoria 启动、迁移、Add/Search 和重启，
  检查 Pod 日志是否出现 `Read-only file system`／`Permission denied`。只有证据明确需要
  额外可写目录时才增加最小挂载；不预先取消只读限制。
- 核对公网负载均衡实例的产品、listener 和真实超时。验证无中间响应的 5 分钟请求，
  继续验证超过 900 秒以及接近组别契约上限的请求，并验证超时后的恢复。记录准确耗时、
  HTTP 状态、客户端／Ingress 日志；短请求通过或流式心跳不能代替同步 Add 验收。
- worker 默认保持 1。调整 `AML_ADAPTER_WORKERS` 后测量吞吐、P50／P95、错误率、
  内存以及 journal／数据库／embedding 压力，再决定容量；正式评测期间冻结参数。

## 发布与评测分开

CI/CD 完成服务发布；基准由明确的评测任务触发。owner 前缀、独立用户、专用库和
独立 Memoria 隔离权限及数据，MatrixOne、embedding、NGINX 和节点容量仍可能共享。
从一次小集任务开始测量吞吐、P50／P95、错误和模型额度；全局并发、速率与总量预算
尚未在适配器实现。正式 Full 前的质量／恢复问题见
[比赛设计](../agent_memory_leaderboard/docs/competition-plan.md)。
