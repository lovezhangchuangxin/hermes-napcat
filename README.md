# hermes-napcat

通过 NapCat 的 OneBot v11 网关，把 QQ 接入 Hermes Agent 的平台插件。

本插件会向 Hermes 注册一个名为 `napcat` 的 gateway platform，不需要修改 Hermes 核心代码。

## 功能

- QQ 私聊和群聊。
- OneBot v11 正向 WebSocket。
- OneBot v11 反向 WebSocket。
- OneBot HTTP 发送补充，用于 `send_message`、cron 等场景。
- 文本、`at`、`reply`、`face` 和入站图片消息段解析。
- Hermes ACL、home channel、platform hint、standalone sender 集成。

## 安装 NapCat

使用官方安装脚本部署 NapCat：

```bash
curl -o \
napcat.sh \
https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh \
&& bash napcat.sh
```

如果你希望使用 Docker 部署，请参考 [NapCat Docker 部署指南](docs/napcat-docker-deploy.md)。

## 安装插件

Hermes 会从 `~/.hermes/plugins/<插件名>/` 加载用户插件，用户插件需要显式启用。

```bash
mkdir -p ~/.hermes/plugins
git clone https://github.com/lovezhangchuangxin/hermes-napcat.git ~/.hermes/plugins/hermes-napcat
hermes plugins enable hermes-napcat
```

更新插件：

```bash
git -C ~/.hermes/plugins/hermes-napcat pull
hermes gateway restart
```

本地开发可以用软链接：

```bash
mkdir -p ~/.hermes/plugins
ln -s /path/to/hermes-napcat ~/.hermes/plugins/hermes-napcat
hermes plugins enable hermes-napcat
```

## 选择连接模式

WebSocket 连接模式只需要选一种：

- 正向 WebSocket：Hermes 主动连接 NapCat。配置 `mode: forward` 和 `ws_url`。
- 反向 WebSocket：Hermes 监听 WebSocket，NapCat 主动连接 Hermes。配置 `mode: reverse` 和 `reverse_*`。

不要把正向和反向同时接到同一个 Hermes 实例，否则同一条 QQ 消息可能会被处理两次。

`http_url` 不是收消息通道，它只用于发送补充。建议同时开启 NapCat HTTP 并配置 `http_url`，这样 cron、`send_message`、gateway 外独立发送等场景也能发 QQ 消息。

## 反向 WebSocket 配置

Hermes 配置示例：

```yaml
plugins:
  enabled:
    - hermes-napcat

napcat:
  enabled: true
  mode: reverse
  reverse_host: "0.0.0.0"            # 监听所有网卡
  reverse_port: 8765                 # 不要和 NapCat WebUI 的 6099 冲突
  reverse_path: "/onebot/v11/ws"
  http_url: "http://127.0.0.1:3000"  # 推荐：NapCat HTTP 地址
  access_token: ""                   # 如果 NapCat 配了 token，这里填同一个
  self_id: "123456789"               # 机器人 QQ 号，建议填写
  require_mention: true
```

NapCat 侧开启：

- 反向 WebSocket 客户端：必需。
- HTTP 服务端：推荐。

NapCat 反向 WebSocket 地址填 Hermes 的可访问地址：

```text
ws://127.0.0.1:8765/onebot/v11/ws
```

## 正向 WebSocket 配置

正向模式下，Hermes 主动连接 NapCat 的 WebSocket 地址。

NapCat 侧开启：

- 正向 WebSocket 服务端：必需。
- HTTP 服务端：推荐。

Hermes 配置示例：

```yaml
plugins:
  enabled:
    - hermes-napcat

napcat:
  enabled: true
  mode: forward
  ws_url: "ws://127.0.0.1:3001"      # NapCat 正向 WebSocket 地址
  http_url: "http://127.0.0.1:3000"  # 推荐：NapCat HTTP 地址
  access_token: ""                   # 如果 NapCat 配了 token，这里填同一个
  self_id: "123456789"               # 机器人 QQ 号，建议填写
  require_mention: true
```

如果 Hermes 和 NapCat 不在同一台机器，请把 `ws_url` / `http_url` 改成 Hermes 能访问到的 NapCat 地址。

## 授权

Hermes gateway 的用户授权仍然生效。本地初次测试可以临时开放：

```yaml
napcat:
  allow_all_users: true
```

正式使用建议只允许指定 QQ 用户：

```yaml
napcat:
  allow_from:
    - "10001"
    - "10002"
```

`allow_from` 里填 QQ 用户 ID。它同时适用于私聊和群聊里的发送者授权。

也可以用环境变量，适合放在 `~/.hermes/.env`：

```bash
NAPCAT_ALLOW_ALL_USERS=true
NAPCAT_ALLOWED_USERS=10001,10002
```

如果同时配置环境变量和 `config.yaml`，环境变量优先。

## 群聊行为

群聊默认需要显式 @ 机器人后才会触发 Hermes，避免 Hermes 回复群里的每一条消息。

允许指定群不需要 @ 就能触发：

```yaml
napcat:
  free_response_groups:
    - "group:123456"
```

只允许指定群触发：

```yaml
napcat:
  allowed_groups:
    - "group:123456"
```

忽略指定群：

```yaml
napcat:
  ignored_groups:
    - "group:123456"
```

## Home Channel

Home Channel 是 Hermes 用来发送 cron 结果、跨平台消息和部分系统通知的默认目标。

最简单的方式是在你希望作为默认目标的 QQ 私聊或群聊里发送：

```text
/sethome
```

也可以手动配置。建议使用带前缀的 chat ID，避免私聊和群聊 ID 歧义。

私聊示例：

```yaml
napcat:
  home_channel:
    chat_id: "private:2911331070"
    name: "QQ DM"
```

群聊示例：

```yaml
napcat:
  home_channel:
    chat_id: "group:123456"
    name: "QQ Home"
```

环境变量写法：

```bash
NAPCAT_HOME_CHANNEL=group:123456
NAPCAT_HOME_CHANNEL_NAME="QQ Home"
```

如果没有配置 home channel，Hermes 首次会话时会发送一条类似 `No home channel is set for Napcat` 的提示。这不是 NapCat 报错。

## Token 配置

如果 NapCat 配置了 OneBot access token，Hermes 侧也要配置同一个 token。

可以写在 `config.yaml`：

```yaml
napcat:
  access_token: "your-token"
```

也可以写在 `~/.hermes/.env`：

```bash
NAPCAT_ACCESS_TOKEN=your-token
```

`.env` 适合放敏感信息；如果同时配置 `.env` 和 `config.yaml`，环境变量优先。

反向 WebSocket 场景下，如果启用了 token，NapCat 的反向 WebSocket 客户端也要带同一个 token。可以在 NapCat 配置里填 token，或把 token 放到 URL 查询参数：

```text
ws://127.0.0.1:8765/onebot/v11/ws?access_token=your-token
```

## 重启和验证

改了 `~/.hermes/config.yaml`、`~/.hermes/.env`、插件代码或 NapCat 连接模式后，重启 Hermes gateway：

```bash
hermes gateway restart
hermes gateway status
```

运行测试：

```bash
python -m unittest discover -s tests -v
```

## 常见问题

### Hermes gateway 如何后台运行

先安装后台服务，再启动：

```bash
hermes gateway install
hermes gateway start
hermes gateway status
```

之后重启或停止：

```bash
hermes gateway restart
hermes gateway stop
```

如果你在 Linux 上以 system service 方式安装，可以使用：

```bash
hermes gateway install --system --run-as-user root
hermes gateway start --system
hermes gateway status --system
```

如果 `hermes gateway restart` 仍然占用当前终端，通常是还没有先通过 `hermes gateway install` 成功安装后台服务。

### 反向 WebSocket 401

如果 NapCat 日志里出现：

```text
Unexpected server response: 401
```

说明 Hermes 拒绝了 NapCat 的反向 WebSocket 连接。最常见原因是 Hermes 配置了 `access_token` 或 `NAPCAT_ACCESS_TOKEN`，但 NapCat 没带同一个 token。

处理方式：

- 在 NapCat 的反向 WebSocket 客户端配置里填写同一个 access token。
- 或把 token 放到反向 WebSocket URL 查询参数里：`ws://127.0.0.1:8765/onebot/v11/ws?access_token=your-token`
- 临时测试时可以删除 `NAPCAT_ACCESS_TOKEN`，并把 `napcat.access_token` 留空，然后重启 Hermes gateway。
