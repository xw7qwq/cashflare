# 用 Cloudflare Access 与 GitHub 登录访问 Cashflare

本指南把本机 Fava 接到 Cloudflare Tunnel，并用 Cloudflare Access 限制访问。GitHub 负责身份验证；GitHub Passkey 是可选的便捷登录方式。仓库不会自动创建 Cloudflare 资源、GitHub OAuth App 或登录凭证。

访问链路：浏览器 → Cloudflare Access → Cloudflare Tunnel → 本机 Fava。示例域名均需替换；配置前先完成 README 的本机部署，确认 `http://127.0.0.1:5000` 可以访问。

## 1. 准备域名与 GitHub Passkey

准备一个已接入 Cloudflare 的域名，以及 Cloudflare Zero Trust 团队。以下用 `ledger.example.com` 表示账本域名，用 `<team-name>.cloudflareaccess.com` 表示团队域名；两者用途不同。[Cloudflare 自托管应用前置条件](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)

在 GitHub 的 **Settings → Password and authentication → Passkeys → Add a passkey** 添加通行密钥，按设备提示保存。如果希望 Mac 和 iPhone 共用，请选择同一个 iCloud 通行密钥提供方，并确认 GitHub 中该密钥显示为 **Synced**。同步密钥可以跨同一提供方的设备使用；设备绑定密钥不会自动同步。[管理 GitHub Passkey](https://docs.github.com/en/authentication/authenticating-with-a-passkey/managing-your-passkeys)、[GitHub Passkey 原理与同步](https://docs.github.com/en/authentication/authenticating-with-a-passkey/about-passkeys)

先在 GitHub 登录页单独测试 **Sign in with a passkey**。系统可能要求 Touch ID、Face ID、设备密码或 PIN，取决于设备和密钥提供方，并非每次都显示生物识别窗口。[使用 Passkey 登录 GitHub](https://docs.github.com/en/authentication/authenticating-with-a-passkey/signing-in-with-a-passkey)

## 2. 将 GitHub 接为身份提供方

1. 在 GitHub **Settings → Developer settings → OAuth Apps → New OAuth App** 创建 OAuth App。
2. Application name 可填 `Cashflare Access`。Homepage URL 填团队域名：

   ```text
   https://<team-name>.cloudflareaccess.com
   ```

3. Authorization callback URL 填完整回调地址：

   ```text
   https://<team-name>.cloudflareaccess.com/cdn-cgi/access/callback
   ```

4. 创建后复制 Client ID，并生成 Client secret。
5. 在 Cloudflare **Zero Trust → Integrations → Identity providers → Add new identity provider → GitHub** 中，将 Client ID 填入 App ID，并填入 Client secret，保存。
6. 按 **Finish setup** 完成 GitHub 授权，再用该身份提供方旁的 **Test** 验证连接。

GitHub OAuth App 的回调域名是 Cloudflare 团队域名，不是 `ledger.example.com`。此集成会请求读取邮箱以及组织和团队信息；个人使用无需创建 GitHub 组织。Client secret 只保存于 Cloudflare 配置和你自己的密码管理器，不能写入仓库、PR、截图或公开日志。[Cloudflare GitHub 身份提供方配置](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/github/)

## 3. 先建立 Access 应用与允许规则

在 **Zero Trust → Access controls → Applications** 创建自托管应用，配置公共主机名 `ledger.example.com`。保护整个主机名，路径留空，覆盖 Fava 的账本页面、API、文件和静态资源。具体菜单名称可能随控制台版本调整。

创建一条 **Allow** 策略：

| 规则类型 | 选择器 | 值 |
| --- | --- | --- |
| Include | Emails | 你的确切邮箱，例如 `owner@example.com` |
| Require | Login Methods | 刚添加的 GitHub 身份提供方 |

使用 GitHub 实际返回给 Access 的邮箱；可先通过身份提供方测试和 Access 登录日志核对。这里的规则含义是“指定邮箱且通过指定 GitHub IdP 登录”。**Emails 匹配邮箱，不是 GitHub 用户名或不可变用户 ID**，不要把 GitHub 昵称填入 Emails。

Include 多条规则按 OR 组合，因此不要把“指定邮箱”和“所有 GitHub 登录者”并列放到两个 Include 条件中。GitHub 登录方法应放在 Require。不要另加 `Everyone` 允许规则或 `Bypass`。这些设置决定谁能通过入口，不会在 Fava 内创建独立用户或隔离每个人的账本。[Access 策略、规则逻辑与选择器](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)

如果需要按 GitHub 成员身份控制，可改用受你管理的 GitHub organization/team 规则，并实际验证成员与非成员的访问结果。标准 GitHub 集成的组织／团队规则不等同于任意个人 GitHub 用户 ID 白名单；本指南没有实现额外的身份校验服务。[GitHub 集成与组织策略](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/github/)、[GitHub organization 规则 API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/get/)

在应用的登录方法中：

- 仅选择本次配置的 GitHub IdP，取消 One-time PIN 和其他登录源；如果启用了“接受所有可用身份提供方”，改成明确选择 GitHub。
- 开启 **Apply instant authentication**（也称 Instant Auth），跳过 Cloudflare 的身份提供方选择页面。
- 保持通过 Cloudflare One Client 会话直接认证的选项关闭，以保持本指南的 GitHub 登录路径。
- 按自己的使用需求选择 Session Duration，先保存应用，再发布 Tunnel 路由。

Instant Auth 只减少一次选择页面。已有 Access 会话可以直接访问；需要跳往 GitHub 时，也可能复用 GitHub 登录状态。因此，这套流程不承诺每次访问重新验证 Passkey，也不保证“零延迟”。[Instant authentication](https://developers.cloudflare.com/learning-paths/clientless-access/customize-ux/login-page/)、[Access 授权 Cookie 与会话](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/)

**强制 GitHub Passkey 不在本指南的能力范围内。** Cloudflare 官方列出的基于 IdP 的 MFA 方法强制支持项不包含标准 GitHub IdP。仅仅给 GitHub 添加 Passkey，不会禁用 GitHub 的其他登录／恢复途径，也不能用 Access 的 Authentication Method 条件保证每次走 GitHub Passkey。如需额外的第二因素，应另行评估 Cloudflare independent MFA；它是独立的认证步骤。[Cloudflare MFA 支持范围](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/)

## 4. 创建 Tunnel 并开启源站校验

Access 应用保存后，在 Cloudflare **Networking → Tunnels** 创建持久 Tunnel，并按控制台对应操作系统的说明安装 `cloudflared`。本指南采用 `cloudflared` 运行在 Fava 所在宿主机的方式。[Cloudflare Tunnel 创建指南](https://developers.cloudflare.com/tunnel/get-started/)

添加 Published application 路由：

| 字段 | 值 |
| --- | --- |
| 公共主机名 | `ledger.example.com` |
| Service URL | `http://127.0.0.1:5000` |
| Protect with Access | 开启 |
| Access 团队 | `<team-name>` |
| Application Audience / AUD | 上一步 Access 应用的 AUD |

在 Access 应用的 Additional settings 中复制 AUD。**Protect with Access 让 `cloudflared` 校验 Access JWT**，从而在转发至 Fava 前核对授权。团队名和 AUD 必须对应保护该域名的应用。[源站 Access 参数](https://developers.cloudflare.com/tunnel/reference/origin-parameters/)、[获取 AUD 与校验 JWT](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)

保留 Fava 的宿主机监听地址 `127.0.0.1`，不要另开公网端口或直连域名。Access 只保护经过它的入口，无法保护绕过该入口访问 Fava 的连接。本机可信用户仍可以通过回环地址访问。

如果自行改成容器运行 `cloudflared`，容器里的 `127.0.0.1` 指向该容器自身，不能照抄宿主机地址；应将它接入相同的受控 Docker 网络，并使用 Fava 服务名和容器端口。改动后重新做下面的访问验证。

Tunnel token 是凭证，持有者可以启动连接器。仅按官方步骤保存到部署主机的受限配置或密钥存储；不要写进 Compose 示例、Git 文件或 PR。发生泄露时轮换 token，并按官方流程断开旧连接。[Tunnel token 管理](https://developers.cloudflare.com/tunnel/reference/tunnel-tokens/)

## 5. 验证访问边界

1. 用未登录的浏览器访问 `https://ledger.example.com`，应进入认证流程，不能先看到任何账本内容。
2. 使用允许邮箱对应的 GitHub 账号登录，确认 Fava 首页和账本子页面正常。
3. 用不在允许列表内的账号测试，确认拒绝访问；只测试自己的账号成功不足以证明策略正确。
4. 检查 Access 登录日志中的身份与应用是否正确，确认 Tunnel 的 Protect with Access 已开启。
5. 从其他设备测试宿主机地址的 `5000` 端口，确认没有可绕过 Access 的直接入口。

如果出现登录循环，先检查 OAuth callback、团队域名、允许邮箱及 AUD 是否一致。如果出现 `502`，先在 `cloudflared` 所在的网络环境核对 Service URL 是否能访问。不要通过添加 Bypass 或开放 Fava 公网端口来临时消除错误。

完成这些配置后，Cashflare 的公网访问入口由 Cloudflare Access 保护；本地账本文件、备份和部署主机仍由你自己管理。修改允许邮箱、登录方法或 Tunnel 路由后，应重新做允许与拒绝两种验证。
