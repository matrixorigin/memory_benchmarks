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
