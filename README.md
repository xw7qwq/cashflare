# ledger

xw7 的记账：beancount 账本加 Fava 网页，只能经 Cloudflare Tunnel 和 Access 访问。旁边的定时任务把收支汇总推给[面板](https://lucius7.dev/me/ledger/)，并把账本备份到私有仓库。

**账本本身不在这个仓库。** 这里只有部署配置、定时任务和一份虚构的[示例账本](example/main.beancount)；真实账本放在服务器的数据目录里，备份到私有仓库 `xw7qwq/ledger-data`。

[面板记账页](https://lucius7.dev/me/ledger/) · [Fava](https://fava.lucius7.dev/) · [组织](https://github.com/xw7qwq)

## 组成

| 服务 | 作用 |
| --- | --- |
| `fava` | Fava 网页，只监听本机 `127.0.0.1:5000` |
| `tunnel` | Cloudflare Tunnel，把 `fava.lucius7.dev` 接到 `http://fava:5000`，前面有 Cloudflare Access |
| `jobs` | 每 15 分钟一次：账本有变化就推送汇总到面板，有改动就提交并推送到备份仓库 |

三个服务用同一个镜像（`python:3.12-slim` 加锁定版本的 Fava 1.30 和 beancount 3），都以数据目录属主的身份运行，根文件系统只读。

## 数据放在哪

| 内容 | 位置 | 公开吗 |
| --- | --- | --- |
| 账本 `.beancount` 和附件 | 服务器 `LEDGER_DATA_DIR`（本身是一个 Git 仓库） | 否 |
| 账本的历史备份 | 私有仓库 `xw7qwq/ledger-data` | 否 |
| 收支汇总、余额、近 90 天交易 | 面板的 KV，只有登录后能看 | 否 |
| 部署配置、定时任务、示例账本 | 这个仓库 | 是 |

`.gitignore` 忽略了 `example/` 以外的所有 `.beancount` 文件、`.env`、`state/` 和 `secrets/`，备份任务也拒绝把账本推到这个公开仓库。

## 部署

第一次部署（在服务器上）：

```sh
git clone https://github.com/xw7qwq/ledger.git && cd ledger
cp .env.example .env && chmod 600 .env    # 填数据目录、隧道令牌、面板令牌、备份仓库
mkdir -p state secrets && chmod 700 state secrets
ssh-keygen -t ed25519 -N '' -C ledger-backup -f secrets/ledger_backup_key
docker compose up -d --build
```

把 `secrets/ledger_backup_key.pub` 加为 `ledger-data` 的部署密钥并勾选写权限（仓库 Settings → Deploy keys，或 `gh repo deploy-key add secrets/ledger_backup_key.pub --repo xw7qwq/ledger-data --allow-write`）。`ledger-data` 建成私有空仓库，不要勾选生成 README。第一次推送时会把 GitHub 的主机密钥记到 `state/known_hosts`。

`LEDGER_UID` / `LEDGER_GID` 要和数据目录的属主一致（`stat -c '%u:%g' "$LEDGER_DATA_DIR"`），否则 Fava 写不了账本，备份也提交不了。镜像构建时会给这个 uid 建一个用户（ssh 推送备份需要），所以改了这两个值要带 `--build` 重新构建。

从现在的部署迁过来：先给数据目录做一次打包备份，停掉旧的 Fava 和隧道容器，把 `LEDGER_DATA_DIR` 指向原来的数据目录，`CLOUDFLARE_TUNNEL_TOKEN` 沿用原来的隧道，在 Zero Trust 里把 `fava.lucius7.dev` 的服务地址改成 `http://fava:5000`，然后 `docker compose up -d --build`。Access 策略不用动。

之后更新：

```sh
git pull && docker compose up -d --build
docker compose logs --tail 50 jobs
```

## 推送到面板

面板从不连接这台服务器。`jobs` 按 schema 1 生成快照推到 `POST https://lucius7.dev/api/v1/ingest/ledger`：

- `balances`：资产和负债账户的余额，按账本里最新的价格折算成主币种（第一个 `operating_currency`）；
- `monthly`：每月每个收入、支出账户的发生额，按交易当天的价格折算，沿用 beancount 的符号（收入为负、支出为正）；
- `net_worth`：每个月末的资产、负债和净资产，最后一个点是今天；
- `transactions`：近 90 天的交易，新的在前；
- `accounts`：账户的开户和销户日期。

找不到价格的币种不计入合计，日志会提示，快照的 `unconverted_currencies` 也会列出来。账本有错误时照样推送，`error_count` 记下错误数，用 `bean-check` 查。如果账本用 `option "name_assets"` 等改过根账户名，推送时会换回 `Assets`、`Liabilities`、`Income`、`Expenses`，面板按这几个名字统计。

内容和上次推送相同时不发请求；日期按 UTC+8 每天变化，所以每天至少推一次。失败后按 5 分钟、10 分钟……最长 6 小时退避，最近一次结果在 `state/panel.json`。没有设置 `LEDGER_PANEL_INGEST_TOKEN` 时不推送。

## 备份

`jobs` 每轮把数据目录里的改动提交一次（`backup: 2026-10-24 09:30 (UTC+8)`），有新提交就推到 `LEDGER_BACKUP_REMOTE`。没有设置远端时只在本地积累历史。恢复时直接克隆：

```sh
git clone git@github.com:xw7qwq/ledger-data.git
```

## 手动执行

```sh
docker compose exec jobs python -m jobs snapshot     # 生成快照，看条数和大小
docker compose exec jobs python -m jobs push --force # 立即推送
docker compose exec jobs python -m jobs backup       # 立即提交并推送备份
docker compose exec fava bean-check /data/main.beancount
```

## 开发

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
bean-check example/main.beancount
python -m unittest discover -s tests -v
LEDGER_FILE=example/main.beancount python -m jobs snapshot
fava example/main.beancount
```

`requirements.txt` 只写直接依赖，`requirements.lock` 是锁定的完整版本，镜像和 CI 都用它。测试只用示例账本和临时生成的数据。

## 目录结构

```text
compose.yaml        fava、tunnel、jobs 三个服务
Dockerfile          三个服务共用的镜像
jobs/
  snapshot.py       从账本生成面板快照
  panel.py          推送到面板，内容没变就跳过
  backup.py         提交并推送数据目录
  state.py          state/ 下的运行记录
  __main__.py       命令行和定时循环
example/            虚构的示例账本
tests/              单元测试（unittest）
```

## 许可证

代码以 [MIT](LICENSE) 许可发布。示例账本里的数据都是虚构的。
