# hermes-napcat

通过 NapCat 的 OneBot v11 网关，把 QQ 接入 Hermes Agent 的平台插件。

本插件会向 Hermes 注册一个名为 `napcat` 的 gateway platform，不需要修改
Hermes 核心代码。

## 已支持功能

- OneBot v11 正向 WebSocket 收发消息。
- 反向 WebSocket 模式：Hermes 监听端口，NapCat 主动连接。
- HTTP fallback：用于 `send_message`、cron 等独立进程发送消息。
- QQ 私聊。
- QQ 群聊，默认要求 @ 机器人后才触发，避免刷屏。
- OneBot 文本、`at`、`reply`、`face` 和入站图片消息段解析。
- Hermes 平台注册、ACL 环境变量、home channel、platform hint、standalone sender 钩子。

## 安装

Hermes 会从 `~/.hermes/plugins/<插件名>/` 加载用户插件，用户插件需要在
Hermes 配置里显式启用。

```bash
mkdir -p ~/.hermes/plugins
ln -s /Users/keqing/Desktop/projects/codes/hermes-napcat ~/.hermes/plugins/hermes-napcat
hermes plugins enable hermes-napcat
```

如果你的 Hermes 版本没有 `hermes plugins enable`，可以手动在
`~/.hermes/config.yaml` 中加入：

```yaml
plugins:
  enabled:
    - hermes-napcat
```

## 正向 WebSocket 模式

先在 NapCat 中开启 OneBot HTTP 和正向 WebSocket，然后配置 Hermes：

```yaml
plugins:
  enabled:
    - hermes-napcat

napcat:
  enabled: true
  mode: forward
  ws_url: "ws://127.0.0.1:3001"
  http_url: "http://127.0.0.1:3000"
  access_token: ""
  self_id: "123456789"
  require_mention: true
```

`ws_url` 和 `http_url` 请改成你在 NapCat 里配置的实际端口。

## 反向 WebSocket 模式

Hermes 配置示例：

```yaml
plugins:
  enabled:
    - hermes-napcat

napcat:
  enabled: true
  mode: reverse
  reverse_host: "127.0.0.1"
  reverse_port: 6099
  reverse_path: "/onebot/v11/ws"
  http_url: "http://127.0.0.1:3000"
  access_token: ""
  self_id: "123456789"
  require_mention: true
```

然后把 NapCat 的反向 WebSocket 地址设置为：

```text
ws://127.0.0.1:6099/onebot/v11/ws
```

## 用户授权

Hermes gateway 的用户授权仍然生效。本地初次测试可以临时使用：

```bash
NAPCAT_ALLOW_ALL_USERS=true
```

正式使用建议配置允许访问 Hermes 的 QQ 号：

```bash
NAPCAT_ALLOWED_USERS=10001,10002
```

## 群聊行为

群聊默认需要显式 @ 机器人后才会触发 Hermes。这是为了避免 Hermes 回复群里的每一条消息。

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

建议使用带前缀的 chat ID，避免私聊和群聊 ID 歧义：

```yaml
napcat:
  home_channel:
    chat_id: "group:123456"
    name: "QQ Home"
```

对应环境变量写法：

```bash
NAPCAT_HOME_CHANNEL=group:123456
NAPCAT_HOME_CHANNEL_NAME="QQ Home"
```

## 验证

```bash
/Users/keqing/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests -v
```

安装并配置完成后：

```bash
hermes gateway restart
hermes gateway status
```
