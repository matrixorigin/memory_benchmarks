# 部署参考

本目录提供 Memoria 与 AML Add/Search 适配器的部署模板和辅助工具。
具体环境的部署计划、验收记录和历史运行结果由环境维护者保存在本地，
公开文档不声明某个线上版本已经通过验收。

## 目录与配置边界

| 路径 | 用途 |
| --- | --- |
| `chart/` | Memoria、适配器、持久卷、Service 和可选 Ingress/TLS 模板 |
| `pulumi/` | 历史独立 dev stack 与目标配置示例 |
| `docker/` | 从固定 Memoria 源码构建镜像的配方 |
| `scripts/` | 配置生成、目标核查、数据库初始化、镜像及发布辅助工具 |
| `memoria-source.json` | Memoria 源码构建来源配置 |

当前仓库的 GitHub Actions 提供离线检查、适配器镜像构建及手动 QA benchmark，
不会自动部署应用。历史阿里云直接部署 workflow 已移除；保留的 Pulumi 和辅助脚本
不是当前共享环境的发布入口。环境更新应沿用对应 GitOps 仓库的 review 与发布流程。

运行前应核对目标、存储、入口、数据库和模型服务配置。不要用示例值直接更新共享环境。
保留现有的目标身份核查和凭据边界；数据库初始化工具需要有权创建运行账号与授权的维护者执行。

## 运行配置

适配器必须连接兼容的 Memoria HTTP 服务，并配置 Memoria master key。
Memoria 的数据库和 embedding 配置由后端维护，普通 Add/Search 客户端不需要这些凭据。

| 配置 | 说明 |
| --- | --- |
| `MEMORIA_URL` | 适配器访问的 Memoria 地址 |
| `MEMORIA_MASTER_KEY` | 适配器上游凭据；通过私有环境或 Secret 注入 |
| `ADAPTER_API_KEY` | 客户端访问适配器使用的独立凭据 |
| `MEMORIA_NAMESPACE` | 将本次用途与其他评测或服务隔离的命名空间 |
| `ADAPTER_STATE_DIR` | 稳定、持久化的 SQLite journal 目录 |

配置项、默认值及协议要求见[适配器说明](../agent_memory_leaderboard/README.md)
和[环境变量示例](../agent_memory_leaderboard/.env.example)。
部署工具中的 `AML_*` 变量名与应用运行时变量名不同，不能直接混用；
按所选模板和脚本的实际映射配置。

镜像支持按固定源码构建或引用已有 `repository@sha256:...`。
保存实际源码与镜像 digest 的对应关系；镜像名称、tag 和 digest 本身不是凭据。

环境配置、私钥、运行报告和数据库文件应存放于 Git 忽略的目录。
内部文档可以使用仓库根目录的 `var/private-docs/`，或适配器目录下的 `var/`。
这些路径不属于公开文档依赖，复制模板时也不应把它们加入构建上下文或提交。

## 本地复现

### 1. 固定版本与依赖

准备 Git、Python 3.12+ 和 uv。部署完整后端时还需要 Docker 与 Compose；
使用 Kubernetes 模板时需要 Helm 及一个独立的复现环境。

检出待复现的完整 mb commit，不使用随时间变化的 `main` 作为版本标识：

```sh
git clone https://github.com/matrixorigin/memory_benchmarks.git
cd memory_benchmarks
git checkout --detach '<MB_FULL_COMMIT_SHA>'
```

用同一套源码运行该版本的锁定依赖。Memoria 必须支持 owner 隔离、metadata 查询
及相应写入协议；具体兼容行为见[重试与部署状态](../agent_memory_leaderboard/README.md#retries-and-deployment-state)。
若已有兼容 Memoria 服务，可直接执行第 3 步。

### 2. 准备 Memoria 与数据库

从 [Memoria 仓库](https://github.com/matrixorigin/memoria)检出待复现的完整 commit，
按照该版本 README 的 Quick Start 和 `docker-compose.yml` 配置 MatrixOne、API 和 embedding。
Memoria 与 MatrixOne 的配置属于后端部署；只启动 mb 不会自动创建完整后端。

最低需要核对：

- MatrixOne 可连接，数据库运行账号具备所选存储模式所需的建库、迁移与读写权限。
- Memoria master key 已配置，API 可访问；该 key 与适配器的客户端 key 分开。
- embedding 服务地址、模型、维度和鉴权有效，维度与该版本数据库配置一致。
- 数据目录持久化；重建实例、变更模型或维度不能被当作无影响的镜像替换。

新建完整 Compose 环境的操作由 Memoria 对应版本的部署说明定义。
若使用本仓库的 [Compose overlay](../agent_memory_leaderboard/README.md#docker-deployment-with-memoria)，
它会将适配器接入该 Compose 的网络并持久化 journal；先配置好上游 `.env`，
不要同时按 overlay 和下方 uv 方式启动两个占用相同端口的适配器。

### 3. 启动适配器

以下命令在仓库根目录开始执行。先填写自己的 Memoria 地址、与后端一致的 master key
和独立的适配器 key；示例值不能作为真实凭据使用。

```sh
cd agent_memory_leaderboard
uv sync --locked
mkdir -p var
chmod 700 var
cat > var/reproduction.env <<'EOF'
MEMORIA_URL=http://127.0.0.1:8100
MEMORIA_MASTER_KEY='replace-with-your-memoria-master-key'
ADAPTER_API_KEY='replace-with-a-separate-adapter-key'
MEMORIA_NAMESPACE=local-reproduction-v1
ADAPTER_STATE_DIR=./var
MEMORY_TIME_NORMALIZATION_ENABLED=false
EOF
chmod 600 var/reproduction.env
# 编辑本机的 var/reproduction.env，替换地址和凭据后，再显式加载：
set -a
. ./var/reproduction.env
set +a
uv run --no-sync uvicorn src.app:create_app --factory --host 127.0.0.1 --port 8000
```

应用不自动加载 env 文件。此例的 journal 保存在 `agent_memory_leaderboard/var/`，
重启或重试时保留该目录、namespace、用户和请求标识。需要全新独立运行时使用新的
namespace 并单独管理旧数据，不能通过清空 journal 恢复未知写入。
时间注释在示例中保持关闭；复现开启该功能的版本时应明确设为 `true` 并记录。

### 4. 验证链路与运行 benchmark

另开终端，在同一适配器目录加载自己刚才填写的环境文件，然后运行：

```sh
set -a
. ./var/reproduction.env
set +a
curl --fail --silent --show-error http://127.0.0.1:8000/ready
uv run --no-sync python -m src.smoke --adapter-url http://127.0.0.1:8000
```

项目 smoke 会实际写入并检索测试记忆，需要正常的数据库和 embedding 服务，
会消耗资源；它不等于 AML 官方 Smoke，也不替代完整 benchmark。
该版本的数据准备、运行命令、评分协议和已知限制见
[benchmark 总览](../agent_memory_leaderboard/benchmarks/README.md)及
[评估说明](../agent_memory_leaderboard/benchmarks/EVALUATION.md#local-commands)。
对齐官方结果还应核对官方冻结的版本、协议及模型要求，不能仅凭本地 smoke 成功判断一致。

## Kubernetes 模板复现

`chart/` 同时渲染 Memoria 与适配器，不包含 MatrixOne 数据库、embedding 服务、
运行 Secret、registry Secret 或 journal PVC 的完整创建流程。这些资源必须由
独立复现环境的维护者提前配置；相关默认名称可在 [values.yaml](chart/values.yaml) 中修改。

| 前置资源／配置 | 模板要求 |
| --- | --- |
| 数据库与 embedding | Memoria Pod 可访问，模型和维度一致 |
| 运行 Secret | 默认 `aml-runtime`，包含 `DATABASE_URL`、`MASTER_KEY`、`EMBEDDING_API_KEY`、`ADAPTER_API_KEY` |
| 镜像拉取 Secret | 默认 `aml-registry`，适用于所选镜像仓库 |
| journal PVC | 默认 `aml-adapter-state`，可写且能供 UID/GID 1000 使用 |
| 镜像与架构 | 适配器及 Memoria 的 repository/digest；当前模板选择 `amd64` 节点 |
| 入口（可选） | Ingress controller、DNS 与 TLS；开启证书模板时还需对应 ClusterIssuer |

维护者在 Git 忽略的 `var/` 中准备私有 values，填写两份镜像的真实 repository/digest、
embedding、Secret、PVC、资源和入口配置。若改变 release 名称，也要同步核对
`memoria.url`，避免继续引用默认服务名。运行 Secret 中的数据库账号需满足
当前模板启用的 Memoria multi-database 模式所需权限。

从仓库根目录可先执行离线模板检查：

```sh
python3 deploy/scripts/render_chart.py
```

该脚本使用合成值检查不同模板分支，不访问集群，也不能证明真实凭据或目标环境可用。
完成配置后，由独立复现环境维护者按其发布流程安装 chart；本仓库的 QA benchmark
不会替其准备这些资源。现有共享环境仍通过对应 GitOps PR 发布。

## 复现记录

部署文档保留操作方法；每次运行还应单独保存以下非秘密元数据：

| 项目 | 需要固定／记录的值 |
| --- | --- |
| 源码 | mb、Memoria 完整 commit SHA |
| 运行镜像 | 适配器、Memoria、MatrixOne 的 tag/digest 与架构 |
| 模型与功能 | embedding 模型／维度、相关模型版本、时间注释等开关 |
| 部署配置 | namespace／隔离规则、持久化、并发、排队、超时及入口限制 |
| 评测契约 | 数据版本／hash、范围、Top K、chunking、回答／Judge 的版本与预算 |
| 执行结果 | 开始结束时间、运行标识、成功／失败与评分覆盖率 |

记录凭据的配置方式，不记录真实值。公开方法的固定仓库版本与部署步骤应可核对；
内部环境地址、具体运行日志和验收进展可以留在受控本地记录中。

## 发布流程

1. 固定源码、镜像和配置版本，核对与已有数据库和 journal 的兼容性。
2. 在隔离环境中完成配置渲染、离线检查和必要的 Add/Search 验证。
3. 通过环境配置仓库的 PR 交维护者 review；按既有流程发布。
4. 发布后检查镜像、就绪状态、认证及新用户写入和检索，保留必要的版本与验收记录。

`/health` 和 `/ready` 成功不代表认证、模型调用或检索质量已验证。
入口、应用、后端、探针及优雅退出分别有自己的时间预算，应整体核对。
正式评测期间保持参评版本和 journal 状态稳定，不将日常升级与正式评测混用。

## 接入已部署的 Add/Search

向环境维护者取得适配器地址和客户端访问密钥。使用 HTTPS，并在进程环境中设置凭据，
不要把凭据写进 URL、示例文件或日志。

| 接口 | 用途 |
| --- | --- |
| `POST /add` | 按 `user_id` 写入消息；同步完成后返回成功及原始请求标识 |
| `POST /search` | 在同一 `user_id` 内检索，返回记忆证据而非最终答案 |
| `GET /health` | 进程存活检查 |
| `GET /ready` | journal 可读性与上游健康检查 |

可执行的 curl 示例见[根目录快速开始](../README.md#try-the-deployed-api)。
完整字段、重试、错误和 journal 迁移语义见
[适配器说明](../agent_memory_leaderboard/README.md#retries-and-deployment-state)。
重试写入应沿用原始请求 ID、用户和完整 payload，不清理 journal 来绕过未知写入状态。

## 评测与数据保留

手动 QA workflow 只运行 benchmark，不会部署后端或读取数据库管理员凭据。
支持范围和成本边界见[benchmark workflow 说明](../agent_memory_leaderboard/benchmarks/CI.md)。
本地回答与 Judge 不替代 AML 官方评测。原始评测正文、private cache、具体运行结果及
验收进展不加入公开仓库；按各数据来源的使用条件与保留要求管理。
Actions 附件过期不会自动清理 Memoria、journal 或历史快照，需要单独的清理安排。
