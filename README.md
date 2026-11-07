# 微信 UIA 读取器

Windows 桌面版微信（4.x）的 UI Automation 工具：定位主窗口、判断当前会话类型、读取输入框和聊天记录，也可以把文本填入输入框或直接发送。

对下游只暴露三个原子操作：

| 入口 | 作用 |
| --- | --- |
| `read_private_chat.py` | 读取当前私聊，并返回可复用的会话身份 |
| `fill_input.py` | 只把文本填入输入框，不发送 |
| `send_message.py` | 校验会话和输入框后发送，并尽量独立确认 |

窗口、UIA 快照、会话识别、消息读取等实现都在 `wechat_uia/` 包里。根目录的三个文件只是薄包装，不复制实现。

默认行为偏保守：不点击消息、不切换会话、不改变窗口状态；微信最小化、隐藏到托盘或位于屏幕外时直接报错。只有显式传入 `--wake-minimized` / `--wake-tray` 时，才会尝试还原窗口。

## 目录

- [环境要求](#环境要求)
- [安装与快速开始](#安装与快速开始)
- [三个公开原子操作](#三个公开原子操作)
  - [read_private_chat](#read_private_chat)
  - [fill_input](#fill_input)
  - [send_message](#send_message)
- [Python 库调用](#python-库调用)
- [内部诊断模块](#内部诊断模块)
- [行为与安全约定](#行为与安全约定)
- [常见问题](#常见问题)
- [已知限制](#已知限制)
- [UIA 能力实测](#uia-能力实测)
- [测试](#测试)
- [项目结构](#项目结构)

## 环境要求

- Windows 10/11
- Python 3.10+（代码使用 `X | None` 类型标注）
- 依赖：

```powershell
pip install -r requirements.txt
```

- 微信已登录，并打开过至少一个会话
- 微信的 UI Automation 控件树可被读取。如果只能看到 Qt 外壳节点，需要先开启系统“屏幕阅读器/辅助功能”再重启微信。

> 类名和 AutomationId 锚点基于微信 4.2 实测；微信升级后可能需要调整 `wechat_uia/core/uia_utils.py`。

## 安装与快速开始

```powershell
pip install -r requirements.txt

# 内部诊断：确认微信主窗口、当前会话类型、当前消息
python -m wechat_uia.core.get_hwnd
python -m wechat_uia.core.get_chat_type
python -m wechat_uia.core.get_messages

# 公开入口：填入但不发送
python fill_input.py --chat 文件传输助手 --text "你好"

# 公开入口：发送（默认 Alt+S）
python send_message.py --chat 文件传输助手 --text "你好"
```

`read_private_chat.py`、`fill_input.py`、`send_message.py` 可以直接运行，也可以作为库导入。其他模块位于 `wechat_uia/core`，用 `python -m wechat_uia.core.<模块>` 运行。

## 三个公开原子操作

三个操作都围绕同一个 `session` 身份工作。`session` 包含：

```text
chat_name
type
member_count
selected_session_name
session_automation_id
session_runtime_id
```

`session_runtime_id` 是 UIA 运行时标识，只在本次微信运行期间有意义；微信重启或控件树重建后可能变化，不要当持久 ID 使用。

### read_private_chat

读取当前私聊的会话信息、聊天记录，并返回 `session`，供后续 `fill_input` / `send_message` 复用。

```powershell
python read_private_chat.py
python read_private_chat.py --limit 500 --max-scrolls 300
python read_private_chat.py --no-scroll
python read_private_chat.py --restore
python read_private_chat.py --wake-minimized
python read_private_chat.py --wake-tray
python read_private_chat.py --json
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--limit` | `20` | 最多返回多少条消息 |
| `--max-scrolls` | `60` | 最多滚动多少次；`0` 表示不滚动 |
| `--scroll-step` | `1` | 每次滚动的滚轮格数 |
| `--settle` | `0` | 滚动稳定后额外等待的秒数；默认用轮询等待 |
| `--timeout` | `20` | 滚动总超时（秒） |
| `--restore` | 关闭 | 读取后恢复原始滚动位置 |
| `--no-restore` | **默认** | 不恢复原始滚动位置 |
| `--no-scroll` | 关闭 | 只读当前视口 |
| `--wake-minimized` | 关闭 | 允许自动还原普通最小化窗口 |
| `--wake-tray` | 关闭 | 允许通过微信原生托盘回调唤起托盘窗口 |
| `--json` | 关闭 | 输出完整 JSON |

只支持私聊。窗口不可用、UIA 不可用、当前不是私聊或找不到聊天区时，退出码为 `2`。

输出示例：

```text
会话     : '某个私聊' (私聊 / private)
窗口状态 : normal
消息条数 : 20
滚动     : 开启 | 步数 4 | 到达顶部: 否 | 截断: 是 | 恢复位置: 跳过
[  0] text   '较早的消息'
...
[ 19] text   '最新的消息'
```

返回结构包含：

- `hwnd`、`window_state`、`window_kind`
- `chat_name`、`type`、`label`、`member_count`、`can_send`
- `session`：完整会话身份，可直接传给 `fill_input(..., session=...)`
- `chat_info`：聊天区与消息列表矩形、逻辑坐标
- `count`、`messages`：按时间正序；历史消息的 `rect` 为 `null`
- `scroll`：`enabled`、`limit`、`max_scrolls`、`steps`、`reached_top`、`truncated`、`limit_hit`、`restored`、`timed_out`、`warnings`、`elapsed_seconds`

滚动规则：

- 只用鼠标滚轮向上滚动，不使用键盘，避免误输入或误发送。
- 用上一页视口的第一条消息作为锚点合并，避免重复。
- 去重键由 RuntimeId + 类名 + 文本 + 子文本组成，因为 Qt 会复用 RuntimeId。
- 默认不恢复滚动位置；需要时用 `--restore`，属于近似恢复，不保证像素级一致。
- 连续 3 次滚动后视口首项和消息集合都没有变化，才认定到达顶部。
- 不保证完整历史：受 `limit`、`max_scrolls`、`timeout` 和微信懒加载限制。

### fill_input

只把文本写入当前聊天输入框，不按 Enter、不点击发送、不切换会话。

```powershell
python fill_input.py --chat 文件传输助手 --text "你好"
python fill_input.py --chat 文件传输助手 --text "第一行`n第二行"
python fill_input.py --chat 文件传输助手 --text "..." --wake-tray
python fill_input.py --chat 文件传输助手 --text "你好" --json
```

前置条件：

- 当前会话必须与给定 `chat_name` / `session` 一致；
- 当前会话必须可发送，并且能找到聊天输入框；
- 输入框在 `str.strip()` 意义下必须为空；非空时抛 `InputNotEmptyError`，**不覆盖也不追加**；
- 只写输入框，不发送。

只传 `--chat` / `chat_name` 时，名称校验分三种情况：

| 情况 | 行为 |
| --- | --- |
| 会话列表可见，名称唯一 | 允许按名称匹配，`session_check: "name_unique"` |
| 会话列表可见，名称重复 | 抛 `ChatMismatchError`，要求完整 `session` |
| 小窗口布局，会话列表不可见 | 只校验当前会话名，允许操作，`session_check: "name_only"`（未验证重名） |

小窗口布局下微信不显示会话列表，因此无法验证是否重名；这时结果会明确标记 `name_only`，不会假装已经验证过。需要严格身份时，传入完整 `session`。

`session` 比较的字段：

- `chat_name`
- `type`
- `member_count`
- `selected_session_name`
- `session_automation_id`
- `session_runtime_id`

只比较 `session` 中实际提供的非 `None` 字段。CLI 也可以直接传这些字段：

```powershell
python fill_input.py --chat 文件传输助手 --text "你好" --chat-type private
python fill_input.py --chat 某群 --text "你好" --chat-type group --member-count 26
python fill_input.py --chat 文件传输助手 --text "你好" --session-runtime-id "42,4457936,4,-2147483380"
```

写入方式：

1. 首选 `ValuePattern.SetValue`：不需要焦点，支持中文和换行；
2. 失败时回退 `SendInput + KEYEVENTF_UNICODE`：不经过剪贴板，需要输入框焦点。

两种方式都只在输入框为空时执行。回退方式里的换行使用 `Shift+Enter`，避免触发发送。写入后会轮询读回校验，确认内容就是准备写入的文本。

输出示例：

```text
会话     : '文件传输助手'
填入方式 : value_pattern
文本长度 : 2
会话校验 : 通过
```

小窗口布局下最后一行会变成：

```text
会话校验 : 只校验当前会话名（会话列表不可见，未验证重名）
```

返回结构：

```json
{
  "hwnd": 123456,
  "chat_name": "文件传输助手",
  "text": "你好",
  "method": "value_pattern",
  "session_check": "name_unique",
  "verified": true
}
```

`method` 为 `value_pattern` 或 `sendinput`。`verified` 表示写入后读回校验通过。

常用可选参数：

| 参数 | 含义 |
| --- | --- |
| `--chat-type` | 期望的会话类型，如 `private` / `group` / `official` |
| `--member-count` | 期望的群成员数 |
| `--session-automation-id` | 期望的选中会话项 AutomationId |
| `--session-runtime-id` | 期望的选中会话项 RuntimeId，逗号分隔整数 |
| `--wake-minimized` / `--wake-tray` | 允许还原窗口 |
| `--json` | 输出完整 JSON |

### send_message

校验会话和输入框后发送一条文本，并尽量独立确认消息真的出现在列表里。

```powershell
python send_message.py --chat 文件传输助手 --text "你好"
python send_message.py --chat 文件传输助手 --text "第一行`n第二行"
python send_message.py --chat 文件传输助手 --text "你好" --chat-type private
python send_message.py --chat 文件传输助手 --text "你好" --send-method alt_s
python send_message.py --chat 文件传输助手 --text "你好" --send-method enter
python send_message.py --chat 文件传输助手 --text "你好" --send-method button
python send_message.py --chat 文件传输助手 --text "你好" --send-method auto
python send_message.py --chat 文件传输助手 --text "你好" --clear-occluders
```

前置条件：

- `text` 在 `str.strip()` 意义下必须非空；
- 当前会话必须与给定 `session` / `--chat` 一致；
- 名称校验规则与 `fill_input` 相同：列表可见时要求唯一，小窗口布局标记 `name_only`；
- 输入框在 `str.strip()` 意义下必须为空；
- 当前窗口必须是微信窗口；聊天记录窗口只读，不能发送。

发送方式（默认 `alt_s`）：

| 方式 | 行为 |
| --- | --- |
| `alt_s` | 默认。发送微信自带的 `Alt+S` 快捷键；键盘操作，不受 Enter 设置影响，不移动鼠标 |
| `enter` | 聚焦输入框后按 Enter |
| `button` | 激活窗口、重新扫描位置并点击“发送”按钮 |
| `auto` | 按 `alt_s -> enter -> button` 顺序尝试 |

`auto` 只在“还没提交”时换下一种方式。一旦已经按下快捷键或点击按钮，即使没确认成功，也不会换方式重发，避免同一条消息发两遍。

发送流程：

1. 调用 `fill_input()` 校验会话、写入文本并读回校验；
2. 写入后再次校验会话身份；
3. 激活窗口、重新扫描，并再次确认输入框里仍是准备发送的文本；
4. 按发送方式提交；
5. 轮询输入框，确认已清空；
6. 在消息列表里确认出现了这条新消息。

`verified` 的含义：

- `verified: true`：输入框已清空，并且消息列表里看到了这条新消息；
- `verified: false`：已经提交，但没能独立确认。**不代表一定没发出去**，不要仅凭这个字段直接重试；
- `confirm` 会分别说明输入框是否清空、消息列表是否看到新消息，以及等待了多久。

如果输入框在提交前被改过（例如输入法改字、用户手动动了键盘），会取消发送并尽量清空，不会把错误内容发出去。

`button` 会短暂移动鼠标。点击前后都会用 `WindowFromPoint` 做系统命中测试：如果坐标上压着其他窗口，直接抛 `ClickTargetBlockedError`，不会把点击发给无关窗口。

`--clear-occluders` 可在发送前最小化挡住微信的其他窗口，发送后再还原；默认关闭。

`--wake-minimized` / `--wake-tray` 与另外两个公开入口一致，默认关闭；只有显式传入时才会尝试还原最小化或托盘隐藏的窗口。

输出示例：

```text
发送结果 : 成功（已独立确认）
会话     : '文件传输助手'
发送方式 : alt_s
填充方式 : value_pattern
文本长度 : 2
会话校验 : 通过
独立确认 : 消息列表已出现新消息
```

小窗口布局下会多一行：

```text
会话校验 : 只校验当前会话名（会话列表不可见，未验证重名）
```

返回结构：

```json
{
  "hwnd": 123456,
  "chat_name": "文件传输助手",
  "text": "你好",
  "fill_method": "value_pattern",
  "send_method": "alt_s",
  "session_check": "name_unique",
  "window_kind": "main",
  "verified": true,
  "confirm": {
    "input_cleared": true,
    "message_seen": true,
    "message_timeout": 3.0,
    "reason": "消息列表已出现新消息"
  }
}
```

如果输入框已清空、但消息列表没有在 3 秒内看到新消息，命令仍会正常返回，但 `verified` 是 `false`，并在 stderr 提醒“消息可能已经发出，不要直接重试”。

## Python 库调用

推荐继续使用根目录入口，导入方式稳定：

```python
from read_private_chat import read_private_chat
from fill_input import fill_input
from send_message import send_message
```

也可以走包入口：

```python
from wechat_uia import read_private_chat, fill_input, send_message
```

需要精确到模块时：

```python
from wechat_uia.api.read_private_chat import read_private_chat
from wechat_uia.api.fill_input import fill_input
from wechat_uia.api.send_message import send_message
```

推荐组合：

```python
from read_private_chat import read_private_chat
from fill_input import fill_input
from send_message import send_message

data = read_private_chat(limit=20, max_scrolls=60)

# 只填不发送
fill_input("你好", session=data["session"])

# 填入并发送
send_message("你好", session=data["session"])
```

如果读取后切换过会话，`fill_input` / `send_message` 会抛 `ChatMismatchError`，不会写入或发送。

常用异常都继承自 `WeChatUIAError`：

| 异常 | 含义 |
| --- | --- |
| `WindowUnavailableError` | 微信窗口未启动、最小化、隐藏或屏幕外 |
| `UIAUnavailableError` | UIA 控件树不可用 |
| `NotAConversationError` | 当前视图不是会话 |
| `NotPrivateChatError` | 当前会话不是私聊 |
| `CannotSendError` | 当前会话不能发送或找不到输入框 |
| `ChatMismatchError` | 当前会话与指定身份不一致 |
| `InputFillError` | 无法写入输入框 |
| `InputNotEmptyError` | 输入框已有非空内容 |
| `SendMessageError` | 发送失败或无法确认 |
| `ClickTargetBlockedError` | 点击位置被其他窗口占用 |
| `MessageListUnavailableError` | 找不到消息列表 |
| `ContactProfileUnavailableError` | 当前私聊没有可读的联系人资料页或标签 |

示例：

```python
from wechat_uia.core.errors import ChatMismatchError
from fill_input import fill_input

try:
    result = fill_input("你好", "文件传输助手")
    print(result["method"], result["session_check"])
except ChatMismatchError as exc:
    print("当前会话不匹配:", exc)
```

读取类型和输入框：

```python
from wechat_uia.core.errors import CannotSendError, WindowUnavailableError
from wechat_uia.core.get_chat_type import get_current_chat_type
from wechat_uia.core.get_input import get_input_state

try:
    info = get_current_chat_type()
    print(info["type"], info["can_send"])

    state = get_input_state()
    print(state["text"], state["is_empty"])
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
except CannotSendError as exc:
    print("不能发送:", exc)
```

## 内部诊断模块

这些模块位于 `wechat_uia/core`，不是稳定 API；适合排查问题，不建议下游直接依赖。

| 模块 | 用途 | 命令 |
| --- | --- | --- |
| `get_hwnd` | 窗口枚举、主窗口定位、窗口角色、命中测试 | `python -m wechat_uia.core.get_hwnd` |
| `get_chat` | 当前会话与聊天区位置，可选列出会话列表 | `python -m wechat_uia.core.get_chat --sessions` |
| `get_chat_type` | 判断私聊/群聊/公众号/列表页等，输出会话身份 | `python -m wechat_uia.core.get_chat_type --identity` |
| `get_input` | 读取当前输入框内容和是否为空 | `python -m wechat_uia.core.get_input` |
| `get_messages` | 读取当前视口已暴露的消息，不滚动 | `python -m wechat_uia.core.get_messages` |
| `read_private_tags` | 读取当前私聊联系人的标签（会临时打开聊天信息和资料页） | `python -m wechat_uia.core.read_private_tags --json` |

### get_hwnd

输出所有微信相关窗口，并给出主窗口句柄与状态。

窗口状态：

| 状态 | 含义 |
| --- | --- |
| `normal` | 正常显示 |
| `minimized` | 最小化 |
| `hidden` | 隐藏到托盘 |
| `offscreen` | 位于屏幕外 |

非 `normal` 时默认报错，退出码 `2`，不会主动还原窗口。

主窗口识别不依赖硬编码类名，而是按进程名（`WeChat.exe` / `Weixin.exe`）和窗口样式综合判断。

### get_chat

```powershell
python -m wechat_uia.core.get_chat             # 当前会话与聊天区
python -m wechat_uia.core.get_chat --sessions  # 同时列出会话列表
```

微信 4.x 使用 Qt 单 HWND 渲染，聊天区没有独立窗口句柄，因此用“主窗口句柄 + UIA 控件矩形”描述。UIA 坐标是物理像素，同时给出按 DPI 换算后的逻辑坐标。

`get_chat_info()` 只返回纯数据；列表页会抛 `NotAConversationError`。`get_sessions()` 返回 `name`、`detail`、`raw`、`rect`、`selected`；`selected` 来自 `SelectionItemPattern.IsSelected`。

### get_chat_type

```powershell
python -m wechat_uia.core.get_chat_type
python -m wechat_uia.core.get_chat_type --identity
```

类型与可发送性：

| 类型 | 含义 | 可发送 |
| --- | --- | --- |
| `private` | 私聊 | 是 |
| `group` | 群聊 | 是 |
| `official` | 公众号/服务号会话（有输入框） | 是 |
| `broadcast` | 公众号推送（无输入框） | 否 |
| `notice` | 服务通知等单聊样式、但无输入框 | 否 |
| `feed` | 公众号消息聚合页（文章列表） | 否 |
| `list` | 列表页：折叠聊天、服务号等 | 否 |
| `unknown` | 无法判定 | 否 |

判定要点：

- 可发送 = 存在聊天输入框；
- 群聊 = 标题栏里“名称”和“成员数”是两个独立文本控件；私聊只有名称，所以名称是 `(1)`、`(2)` 的私聊不会误判；
- 公众号会话 = 标题栏为 `ChatBrandView` 且有输入框；
- 公众号推送 = `ChatBrandView` 但没有输入框；
- 服务通知 = 单聊标题栏但没有输入框；
- 公众号聚合页 = `ChatBrandDisorderPage`；
- 列表页 = 带“返回”按钮的列表。

`--identity` 会输出完整会话身份，可直接传给 `fill_input()` / `send_message()`：

```json
{
  "chat_name": "文件传输助手",
  "type": "private",
  "label": "私聊",
  "member_count": null,
  "can_send": true,
  "selected_session_name": "文件传输助手",
  "session_automation_id": "session_item_文件传输助手",
  "session_runtime_id": [42, 4457936, 4, -2147483380],
  "reason": "标题栏为单聊视图，且有聊天输入框"
}
```

小窗口布局不显示会话列表时，`selected_session_name` / `session_automation_id` / `session_runtime_id` 可能为 `null`，此时只能做名称级校验。

### get_input

```powershell
python -m wechat_uia.core.get_input
```

读取输入框内容并判断是否为空。空白字符视为空。不可发送或窗口不可用时退出码为 `2`，不会修改或发送任何内容。

### get_messages

```powershell
python -m wechat_uia.core.get_messages
```

不判断会话类型，只读取消息列表当前已暴露的消息项，不滚动。`kind` 可能为：

| kind | 含义 |
| --- | --- |
| `time` | 时间分隔 |
| `text` | 文本消息 |
| `bubble` | 文件、链接、卡片等气泡 |
| `image` | 图片 |
| `voice` | 语音 |
| `app` | 小程序、引用等应用卡片 |
| `other` | 其他未识别项 |

需要更多历史时使用 `read_private_chat.py`（仅私聊）。

### read_private_tags

```powershell
python -m wechat_uia.core.read_private_tags
python -m wechat_uia.core.read_private_tags --json
```

只读取当前**私聊**联系人的标签。流程是：

1. 校验当前会话是私聊；
2. 点击“聊天信息”打开侧栏；
3. 点击联系人头像，打开独立的联系人资料页；
4. 读取“标签”字段；
5. 关闭资料页和侧栏，恢复原来的前台窗口。

微信会把多个标签放在同一个文本控件里，用逗号分隔，例如：

```text
高中同学,大学同学
```

因此返回时会拆成：

```json
["高中同学", "大学同学"]
```

示例输出：

```text
会话   : '韩佳彤'
联系人 : '韩佳彤'
标签   : 大学同学
```

没有标签时返回空列表，不报错：

```json
{
  "chat_name": "李心怡",
  "contact": "李心怡",
  "tags": [],
  "raw_tags": [],
  "hwnd": 123456,
  "window_state": "normal"
}
```

注意：

- 仅支持普通私聊；文件传输助手等没有联系人资料入口的会话会抛 `ContactProfileUnavailableError`；
- 需要临时点击两次（聊天信息、联系人头像），属于界面操作，不只是纯 UIA 读取；
- 如果已经有一个联系人资料页打开，会先报错，避免读错人；请关闭后再调用；
- 资料页或侧栏被其他窗口遮挡时，点击会抛 `ClickTargetBlockedError`，不会误点其他窗口；
- 标签读取完后会关闭资料页和侧栏；不会发送消息或修改输入框。

## 行为与安全约定

- 除 `fill_input.py` / `send_message.py` 外只读：不点击消息、不切换会话、不修改输入框。
- `wechat_uia.core.read_private_tags` 会临时打开聊天信息侧栏和联系人资料页，只读取标签；结束后关闭弹窗和侧栏，不发送消息、不修改输入框。
- `fill_input.py` 只写输入框，不按 Enter、不点击发送。
- `send_message.py` 默认用 `Alt+S`，不依赖“Enter 发送 / Ctrl+Enter 发送”设置。
- 发送前会再次确认输入框里仍是准备发送的文本；提交后会先看输入框是否清空，再在消息列表里确认新消息。
- 已经提交但没确认时，不会换一种方式重发，避免重复发送。
- 所有模拟鼠标点击（发送按钮、输入框聚焦、托盘图标备选）都会先做系统命中测试；命中其他窗口时抛 `ClickTargetBlockedError`，不会点击。
- 发送只接受微信进程的窗口；聊天记录窗口只读，拒绝发送。
- 默认不改变窗口状态；`--wake-minimized` 只对普通最小化使用 `ShowWindow(SW_RESTORE)`，`--wake-tray` 首选投递微信原生托盘回调。
- UIA 遍历带 RuntimeId 去重、深度上限和超时，控件树异常时不会无限卡住。
- 一次读取只遍历一次控件树：类型、输入框、消息消费同一份快照，避免结果来自不同时刻。
- 所有矩形统一为 `(x, y, width, height)`；历史消息的 `rect` 为 `null`。
- UIA 坐标是物理像素，高 DPI 环境下与常规逻辑坐标不同。
- 根目录三个入口是薄包装；输入框读取、窗口置前、命令行校验都只有一份实现，`tests/test_structure.py` 会检查是否有重复文件。

## 常见问题

### 小窗口布局提示 `session_check: "name_only"`

小窗口布局不显示会话列表，无法验证是否有重名。此时只校验当前打开的会话名，允许操作，但会明确标记“未验证重名”。需要严格身份时，请使用正常布局或传入完整 `session`。

### 提示微信窗口不可用

微信可能最小化、隐藏到托盘或位于屏幕外。先手动还原，或者显式加：

```powershell
--wake-minimized
--wake-tray
```

### `verified: false` 是不是发送失败了

不一定。它表示“已经提交，但没能在消息列表里独立确认”。可能原因包括消息列表不在底部、列表更新慢、表情/图片消息的 UIA 文本与原文差异大。**不要直接重试**，先人工确认。

### `button` 报 `ClickTargetBlockedError`

发送按钮被其他窗口挡住了。可以用 `--clear-occluders`，或者改用默认的 `alt_s`。

### 为什么默认用 Alt+S

Alt+S 是微信自带的发送快捷键，属于键盘操作，不受“Enter 发送 / Ctrl+Enter 发送”设置影响，也不需要移动鼠标，比点击按钮更稳。

### 群聊里为什么没有发送者

微信 4.x 的 UIA 消息项是扁平结构，没有发送者/头像子控件，消息矩形也横跨整个列表，无法可靠判断发送者和左右方向。

## 已知限制

- UIA 读取仅在 Windows 桌面版微信 4.x 验证；定位层兼容 `WeChat.exe` / `Weixin.exe`，但 3.x 控件树未验证。
- 需要微信已登录，并打开过至少一个会话。
- 公众号推送、服务通知、公众号聚合页、列表页等不可发送视图无法读取输入内容。
- `get_messages` 不滚动，只返回当前已暴露的消息项，不保证完整历史。
- `read_private_tags` 只支持普通私聊；没有联系人资料入口的会话（如文件传输助手）会抛 `ContactProfileUnavailableError`。
- `read_private_tags` 需要临时点击“聊天信息”和联系人头像；资料页被遮挡时会抛 `ClickTargetBlockedError`，不会误点其他窗口。
- `read_private_chat` 只支持私聊；滚动依赖鼠标滚轮，过程中会临时把微信置前。
- 如果前台切换失败或系统关闭悬停滚动，滚动会在 `warnings` 中提示，且可能读不到更早消息。
- `--wake-tray` 首选读取 Explorer 托盘工具栏并投递原生托盘回调；权限不足或结构变化时退回 `Shell_NotifyIconGetRect` + 鼠标双击，仍失败则报错。
- 群聊发送者和消息方向无法通过 UIA 获取。
- `alt_s` 依赖微信自带快捷键；如果被修改或禁用，会报错并清空输入。
- `--send-method enter` 假设微信保持默认的“Enter 发送”设置；若设置为 `Ctrl+Enter`，会拒绝发送并清空输入。
- `--send-method button` 使用鼠标点击；被遮挡时会抛 `ClickTargetBlockedError`，只建议显式使用。
- 发送后可能得到 `verified: false`；这不等于发送失败，不要直接重试。
- `--clear-occluders` 会最小化与微信重叠的其他窗口，只在显式开启时执行；正常结束会还原，但进程被强制结束时可能来不及还原。
- 小窗口布局不显示会话列表，只凭 `--chat` 无法验证重名，会标记 `session_check: "name_only"`。
- 发送过程中如果用户切换会话，会在发送前重新校验并取消发送；已填入旧会话的草稿可能保留。
- 历史消息来自不同滚动位置，`rect` 不再具有屏幕坐标意义。

## UIA 能力实测

在微信 4.2 / `uiautomation 2.0.29` 上做过只读探查：

| 能力 | 实测结果 | 影响 |
| --- | --- | --- |
| `chat_message_list` 的 `ScrollPattern` | 不支持 | 不能替代鼠标滚轮，继续使用滚轮 |
| `chat_message_list` 的 `ItemContainerPattern` / `VirtualizedItemPattern` | 不支持 | 不能按属性查找或实体化未渲染消息 |
| `chat_message_list` 的 `TextPattern` | 不支持 | 只能逐项读取消息 |
| 会话项 `SelectionItemPattern.IsSelected` | 支持 | `get_sessions()` 有 `selected` 字段 |
| 消息项子控件 | 无（`GetChildren()` 为 0） | 无法从头像/子控件取发送者 |
| 消息项矩形 | 横跨整个消息列表 | 无法用左右位置判断“自己/对方” |
| 输入框 `ValuePattern` / `TextPattern` | 支持 | 输入框读取优先 `ValuePattern` |
| 输入框 `TextEditPattern` | 不支持 | 光标/选区能力不完整 |

## 测试

纯逻辑测试不需要真实微信窗口：

```powershell
python -m unittest discover -s tests -t . -v
```

测试覆盖窗口、UIA 遍历、会话身份、填写/发送、发送确认、点击命中测试、历史合并、目录分层和重复文件检查。

## 项目结构

```text
read_private_chat.py       # 公开入口（薄包装）
fill_input.py              # 公开入口（薄包装）
send_message.py            # 公开入口（薄包装）

wechat_uia/
  __init__.py              # 按需导出三个原子操作
  cli_common.py            # 命令行参数共用校验
  api/
    __init__.py
    read_private_chat.py   # L5 私聊读取：会话信息 + 历史消息 + session 身份
    fill_input.py          # L3 输入框文本写入（ValuePattern，回退 SendInput）
    send_message.py        # L3 发送并独立确认（默认 Alt+S）
  core/
    errors.py              # 统一异常族
    get_hwnd.py            # L0 窗口句柄、窗口角色、命中测试、可选唤醒
    uia_utils.py           # L1 UIA 基础能力、锚点、防环/限深/超时遍历
    snapshot.py            # L2 一次遍历生成窗口快照（唯一导入 uiautomation）
    get_chat.py            # L3 会话与聊天区定位
    get_chat_type.py       # L3 会话类型与可发送性
    get_input.py           # L3 输入框内容读取
    get_messages.py        # L3 当前消息列表读取
    read_private_tags.py   # L3 私聊标签读取（临时打开资料页）
    message_history.py     # L4 滚轮历史采集、去重、位置恢复

tests/                     # 离线测试
requirements.txt           # 运行依赖
```

依赖方向：

```text
core/errors + core/get_hwnd + core/uia_utils
    -> core/snapshot
    -> core 各读取模块
    -> core/message_history
    -> api/read_private_chat
```

`api/send_message` 只依赖 `api/fill_input` 和 `core`；根目录三个文件只依赖各自的 `api` 模块。

输入框读取统一用 `core/snapshot.read_control_text()`，窗口置前统一用 `core/get_hwnd.activate_window()`，命令行正整数/RuntimeId 校验统一用 `wechat_uia/cli_common.py`，避免同一段逻辑在多个文件里复制。
