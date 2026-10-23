# Cashflare

基于 Beancount + Fava 的个人账本部署模板，提供 Docker Compose、空白账本、初始化与备份工具，以及可选的 Cloudflare Access + GitHub 登录说明。

仓库只包含代码、配置和虚构示例。真实账本、账单、报表、备份和登录凭证保存在本机私有目录，不提交到 Git。现有服务迁移需另行执行；克隆本仓库不会改动其他正在运行的账本。

## 快速开始

需要 Docker Engine 或 Docker Desktop、Docker Compose v2，以及宿主机 Python 3.10+。初始化和备份脚本使用 POSIX 文件操作，支持 Linux/macOS，Windows 请在 WSL 中运行。当前依赖锁定于 CPython 3.13 / Linux amd64；Compose 明确使用 `linux/amd64`，Apple Silicon 通过 Docker Desktop 模拟运行，尚未提供原生 arm64 锁文件。

```bash
git clone https://github.com/xw7qwq/cashflare.git
cd cashflare
python3 scripts/init-ledger.py
cp -n .env.example .env
```

Linux 上运行 `id -u` 和 `id -g`，将结果分别填入 `.env` 的 `CASHFLARE_UID`、`CASHFLARE_GID`，使容器可以读写你创建的私有账本目录。默认值是 1000:1000。如果 5000 端口已被占用，修改 `CASHFLARE_PORT`。

```bash
docker compose build
docker compose run --rm --no-deps fava bean-check --cache-filename /tmp/check.picklecache /data/main.beancount
docker compose up -d --wait
```

打开 [本机 Cashflare](http://127.0.0.1:5000/)。若修改了端口，使用对应端口。模板不包含任何交易，初始化工具拒绝覆盖已有目标目录。

## 账本与操作

`data/main.beancount` 是入口，包含 `accounts.beancount` 和 `transactions/*.beancount`。真实文件均在 Git 忽略范围内。`CASHFLARE_DATA_DIR` 可改为本机其他私有目录，该目录必须已经存在并包含 `main.beancount`；不要直接指向尚未备份的唯一账本副本。

Fava 可编辑挂载的账本，因此数据目录可写；容器根文件系统为只读。账户、交易和分摊方式参见 [记账与共同费用说明](docs/accounting.md)。同一 Fava 实例的访问者共享同一账本，并不按登录用户隔离数据。

```bash
docker compose logs --tail=100 fava
docker compose run --rm --no-deps fava bean-check --cache-filename /tmp/check.picklecache /data/main.beancount
docker compose restart fava
docker compose stop fava
```

HTTP 健康检查只确认服务可响应；账本语义、金额和余额断言需要 `bean-check`。每次导入或重分类后都应校验，尤其不能将完整的集体付款直接当作本人消费。

## 备份与恢复

停止服务后备份，以免编辑器在备份途中写入文件。若使用自定义 `CASHFLARE_DATA_DIR`，将下面的 `data` 替换为实际目录。备份工具拒绝符号链接、覆盖已有文件和把归档写入源目录。

```bash
mkdir -m 700 -p backups
docker compose stop fava
python3 scripts/backup-ledger.py data "backups/ledger-$(date +%Y%m%d-%H%M%S).tar.gz"
docker compose up -d --wait
```

即使备份失败，也要执行最后一行恢复服务。归档权限为 0600；请另存一份到可靠的私有存储。当前没有定时或异地备份。

恢复时先停止服务，保留当前数据目录，将可信备份解压到一个新的目录，检查其中的 `data/main.beancount`，再把 `CASHFLARE_DATA_DIR` 指向恢复出的 `data`。恢复前后核对权限与内容，运行 `bean-check` 通过后再启动。不要直接在现有账本上解压覆盖。

## 外部访问

默认只发布 `127.0.0.1` 端口，Fava 本身不提供本项目的登录保护。需要域名访问时，按照 [Cloudflare Access + GitHub Passkey 指南](docs/cloudflare-access.md)先创建访问策略，再接入 Tunnel。指南不包含凭证，也不会自动创建外部资源。

GitHub Passkey 发生在 GitHub 登录环节；已有 GitHub/Access 会话可能被复用。Instant Auth 只跳过身份源选择页，不保证每次访问都弹出 Touch ID / Face ID。

## 构建与验证

应用版本为 Beancount 3.2.3、Fava 1.30.16；完整依赖在 `requirements.lock.txt` 中固定版本和 wheel SHA-256。基础 Python 镜像固定摘要，系统包在构建时从 Debian 源更新，因此系统包层并非字节级可复现。镜像安装后执行 `pip check`，移除运行时不需要的 pip/ensurepip，不依赖某台机器上的补丁镜像。

默认构建使用镜像系统 CA。只有在企业代理需要额外 CA 时，才使用可选的 BuildKit secret；CA 文件不会复制到镜像：

```bash
docker build --platform linux/amd64 --secret id=build_ca,src=/path/to/ca-bundle.pem -t cashflare:fava-1.30.16 .
docker compose up -d --no-build --wait
```

开发检查：

```bash
python3 -m unittest discover -s tests -v
docker compose config --quiet
docker run --rm --network none --mount type=bind,src="$PWD/examples",dst=/examples,readonly cashflare:fava-1.30.16 bean-check --cache-filename /tmp/example.picklecache /examples/shared-expenses.beancount
```

CI 在空白账本上构建、校验并启动 Fava。升级依赖或基础镜像后，应重复这些检查并验证备份恢复。

参考：[Fava 官方文档](https://beancount.github.io/fava/)、[Beancount 文档](https://beancount.github.io/docs/)、[Docker Compose 文档](https://docs.docker.com/compose/)。
