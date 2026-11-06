# 微信 UIA 读取器

Windows 桌面版微信（4.x）的自动化脚本集：定位主窗口、当前会话与聊天区，判定会话类型，读取输入框内容和聊天记录，并可把文本填入聊天输入框或发送消息。

脚本默认不点击消息或切换会话；默认也不会改变窗口状态。微信最小化、隐藏到托盘或位于屏幕外时直接报错。只有 `read_private_chat.py`、`fill_input.py` 和 `send_message.py` 在显式传入 `--wake-minimized` / `--wake-tray` 时，才会尝试还原窗口。`fill_input.py` 只写输入框；`send_message.py` 会在校验后点击发送按钮（默认）或按 Enter 发送。

## 目录

- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [脚本一览](#脚本一览)
- [命令详解](#命令详解)
  - [get_hwnd.py](#get_hwndpy)
  - [get_chat.py](#get_chatpy)
  - [get_chat_type.py](#get_chat_typepy)
  - [get_input.py](#get_inputpy)
  - [fill_input.py](#fill_inputpy)
  - [send_message.py](#send_messagepy)
  - [get_messages.py](#get_messagespy)
  - [read_private_chat.py](#read_private_chatpy)
- [面向下游的原子操作](#面向下游的原子操作)
- [Python 库调用](#python-库调用)
- [行为约定](#行为约定)
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
- 微信的 UI Automation 控件树可被读取（若只能看到 Qt 外壳节点，需先开启系统“屏幕阅读器/辅助功能”再重启微信）

> UIA 的类名与 AutomationId 锚点基于微信 4.2 实测，微信版本升级后可能变化，需要相应调整 `uia_utils.py`。

## 快速开始

```powershell
pip install -r requirements.txt

python get_hwnd.py        # 确认微信主窗口可用
python get_chat_type.py   # 查看当前会话类型
python get_messages.py    # 读取当前视口的消息
python fill_input.py --chat 文件传输助手 --text "你好"  # 填入输入框，不发送
python send_message.py --chat 文件传输助手 --text "你好"  # 校验后点击发送按钮发送
```

单次读取（`get_chat.py`、`get_chat_type.py`、`get_input.py`、`get_messages.py`）都不会滚动列表，也不会改变窗口状态。`fill_input.py` 只写输入框，不按 Enter、不点击发送；`send_message.py` 会发送消息。

## 脚本一览

| 脚本 | 用途 | 常用接口 |
| --- | --- | --- |
| `get_hwnd.py` | 枚举微信窗口，定位主窗口句柄与窗口状态 | `find_wechat_main_window()`、`require_wechat_main_window()`、`window_state()` |
| `get_chat.py` | 获取当前会话信息、聊天区位置，可选列出会话列表 | `get_chat_info()`、`get_sessions()` |
| `get_chat_type.py` | 判定当前会话/视图类型，并给出是否可发送消息；可输出完整会话身份 | `get_current_chat_type()`、`classify_snapshot()`、`get_session_identity()` |
| `get_input.py` | 读取当前聊天输入框内容，判断是否为空 | `get_input_state()` |
| `fill_input.py` | 把文本填入当前聊天输入框（不发送），并校验会话 | `fill_input()` |
| `send_message.py` | 校验会话和输入框后，向当前聊天发送一条文本消息 | `send_message()` |
| `get_messages.py` | 读取当前会话已暴露的聊天记录（不滚动） | `get_messages()` |
| `read_private_chat.py` | 仅私聊：读取会话信息与聊天记录，可选向上滚动加载更多 | `read_private_chat()` |

## 命令详解

### get_hwnd.py

```powershell
python get_hwnd.py
```

输出所有微信相关窗口，并给出主窗口句柄与状态：

```
主窗口句柄: 0x123456 (1193046)
窗口状态  : normal
```

窗口状态：

| 状态 | 含义 |
| --- | --- |
| `normal` | 正常显示 |
| `minimized` | 最小化 |
| `hidden` | 隐藏到托盘 |
| `offscreen` | 位于屏幕外 |

非 `normal` 时会在 stderr 报错并给出还原提示，退出码为 `2`，不会主动还原窗口。

也可作为库使用：

```python
from errors import WindowUnavailableError
from get_hwnd import require_wechat_main_window

try:
    hwnd = require_wechat_main_window()  # 定位并校验窗口状态
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
```

`find_wechat_main_window()` 只负责定位句柄（可包含隐藏窗口）；`require_wechat_main_window()` 和 `require_window_ready(hwnd)` 会在窗口未启动、最小化、隐藏或位于屏幕外时立即抛出 `WindowUnavailableError`。

主窗口识别不依赖硬编码类名，而是按进程名（`WeChat.exe` / `Weixin.exe`）与窗口样式（可见标题栏、非工具窗口、可最小化/最大化等）综合判断。定位层同时兼容微信 3.x 与 4.x，但后续 UIA 读取以 4.x 为准。

### get_chat.py

```powershell
python get_chat.py             # 当前会话与聊天区
python get_chat.py --sessions  # 同时列出会话列表
```

输出示例：

```
主窗口句柄 : 0x123456 (1193046)  状态: 普通
聊天区句柄 : 无（Qt 子控件，归属于主窗口）
当前会话   : '某个会话'
聊天区位置 : (548, 66, 2004, 1446)  (物理像素，x,y,w,h)
  → 逻辑坐标: (274, 33, 1002, 723)
消息列表   : (548, 162, 2004, 1228)
```

微信 4.x 使用 Qt 单 HWND 渲染，聊天区没有独立窗口句柄，因此用“主窗口句柄 + UI Automation 控件矩形”描述。UIA 坐标是物理像素，脚本同时给出按屏幕 DPI 换算后的逻辑坐标。所有对外矩形统一为 `(x, y, width, height)`。

处于列表页（折叠的聊天、服务号等）时，会提示当前是列表页而非聊天区。

作为库使用时，`get_chat_info()` 只返回纯数据（不包含 UIA 控件对象）；列表页会抛出 `NotAConversationError`。会话列表用 `get_sessions()` 获取，每条记录包含 `name`、`detail`、`raw`、`rect` 和 `selected`，其中 `selected` 来自 `SelectionItemPattern.IsSelected`，CLI 会用 `[当前]` 标记当前会话。

### get_chat_type.py

```powershell
python get_chat_type.py
```

输出示例：

```
当前会话 : '某个群聊'
类型     : 群聊 (group)
可发送   : 是
成员数   : 26
判定依据 : 标题栏显示群成员数 (26)
```

类型与可发送性：

| 类型 | 含义 | 可发送 |
| --- | --- | --- |
| `private` | 私聊 | 是 |
| `group` | 群聊 | 是 |
| `official` | 公众号/服务号会话（有输入框） | 是 |
| `broadcast` | 公众号/服务号推送（无输入框） | 否 |
| `notice` | 服务通知等单聊样式、但没有输入框的会话 | 否 |
| `feed` | 公众号消息聚合页（文章列表，不是会话） | 否 |
| `list` | 列表页：折叠的聊天、服务号、服务号子列表 | 否 |
| `unknown` | 无法判定 | 否 |

判定要点：

- 可发送 = 存在聊天输入框（`mmui::ChatInputView` / `ChatInputField`）
- 群聊 = 标题栏中“名称”和“成员数”是两个独立文本控件；私聊只有“名称”一个控件，因此名称为 `(1)`、`(2)` 的私聊不会被误判
- 公众号/服务号会话（`official`）= 标题栏为 `ChatBrandView` 且有输入框
- 公众号推送（`broadcast`）= 标题栏为 `ChatBrandView` 但没有输入框
- 服务通知（`notice`）= 标题栏为单聊视图，但没有输入框
- 公众号聚合页（`feed`）= 显示文章列表的 `ChatBrandDisorderPage`，不是会话
- 列表页（`list`）= 带“返回”按钮的列表，列表名即页面标题

对于 `feed` / `list`，CLI 的标题行会显示“当前视图”，而不是“当前会话”。

需要把当前会话身份传给 `fill_input()` 时，可以输出完整身份：

```powershell
python get_chat_type.py --identity
```

```json
{
  "chat_name": "文件传输助手",
  "type": "private",
  "label": "私聊",
  "member_count": null,
  "can_send": true,
  "selected_session_name": "文件传输助手",
  "session_automation_id": "session_item_文件传输助手",
  "session_runtime_id": [42, 123456, 4, -1],
  "reason": "标题栏为单聊视图，且有聊天输入框"
}
```

`session_runtime_id` 是 UIA 运行时标识，在同一次微信运行期间通常稳定；微信重启或控件树重建后可能变化，不应作为持久 ID。

### get_input.py

```powershell
python get_input.py
```

可发送时输出输入框内容并判断是否为空：

```
会话     : '某个私聊'
类型     : 私聊 (private)
可发送   : 是
输入内容 : '你好'
是否为空 : 否
```

不可发送时向 stderr 报错，并以退出码 `2` 结束：

```
错误: 当前会话不可发送消息：'某公众号'（公众号推送 / broadcast）
```

微信窗口未启动、最小化或隐藏到托盘时同样报错并以 `2` 退出，例如：

```
错误: 微信窗口已隐藏到托盘，请手动还原后再运行（本脚本不会主动唤起窗口）
```

空白字符视为空内容。脚本只读取输入框，不会修改或发送。

### fill_input.py

```powershell
python fill_input.py --chat 文件传输助手 --text "你好"
python fill_input.py --chat 文件传输助手 --text "第一行`n第二行"
python fill_input.py --chat 文件传输助手 --text "..." --wake-tray
```

前置条件：

- 当前会话必须与调用方指定的会话身份一致；
- 当前会话必须可发送消息，并且能找到聊天输入框；
- 输入框内容在 `str.strip()` 意义下必须为空；非空时抛 `InputNotEmptyError`，**不会覆盖或追加**已有内容；
- 只填输入框，不按 Enter、不点击发送。

只传 `--chat` / `chat_name` 时，会检查该名称在当前会话列表中是否唯一；只有唯一时才允许按名称匹配。重名会抛 `ChatMismatchError`，必须传入完整会话身份：

```python
from get_chat_type import get_session_identity
from fill_input import fill_input

session = get_session_identity()  # 在目标会话打开时获取
result = fill_input("你好", session=session)
```

`fill_input` 会按 `session` 中实际提供的非 `None` 字段逐一比较：

- `chat_name`
- `type`
- `member_count`
- `selected_session_name`
- `session_automation_id`（选中会话项的 AutomationId）
- `session_runtime_id`（选中会话项的 RuntimeId）

不一致时抛 `ChatMismatchError`，不会写入。CLI 也支持这些可选字段：

```powershell
python fill_input.py --chat 文件传输助手 --text "你好" --chat-type private

python fill_input.py --chat 某群 --text "你好" --chat-type group --member-count 26

python fill_input.py --chat 文件传输助手 --text "你好" --session-runtime-id "42,123456,4,-1"
```

写入方式：

1. 首选 `ValuePattern.SetValue`：不需要焦点，支持中文和换行；
2. 失败时回退到 `SendInput + KEYEVENTF_UNICODE`：不经过剪贴板，需要输入框焦点。

两种方式都只在输入框 `strip()` 后为空时执行；纯空白视为空，写入前会清掉空白。不会覆盖或追加非空内容。回退方式中的换行使用 `Shift+Enter`，避免触发发送。

输出示例：

```
会话     : '文件传输助手'
填入方式 : value_pattern
文本长度 : 8
校验     : 通过
```

`method` 可能为 `value_pattern` 或 `sendinput`。返回结构：

```json
{
  "hwnd": 123456,
  "chat_name": "文件传输助手",
  "text": "你好",
  "method": "value_pattern",
  "verified": true
}
```

作为库使用：

```python
from errors import ChatMismatchError
from fill_input import fill_input

try:
    result = fill_input("你好", "文件传输助手")
    print(result["method"], result["verified"])
except ChatMismatchError as exc:
    print("当前会话不匹配:", exc)
```

### send_message.py

```powershell
python send_message.py --chat 文件传输助手 --text "你好"
python send_message.py --chat 文件传输助手 --text "第一行`n第二行"
python send_message.py --chat 文件传输助手 --text "你好" --chat-type private
python send_message.py --chat 文件传输助手 --text "你好" --send-method enter
python send_message.py --chat 文件传输助手 --text "你好" --send-method auto
```

前置条件：

- `text` 在 `str.strip()` 意义下必须非空；
- 当前会话必须与给定 `session` / `--chat` 一致；
- 只传 `--chat` 时，该名称必须在会话列表中唯一；重名时抛 `ChatMismatchError`，必须传完整 `session`；
- 输入框在 `str.strip()` 意义下必须为空。

发送方式：

- `button`（默认）：激活窗口后点击“发送”按钮，不发送 Enter；
- `enter`：聚焦输入框后按 Enter；
- `auto`：优先点击发送按钮，按钮不可用时回退 Enter。

发送流程：

1. 调用 `fill_input()` 校验会话、写入文本并读回校验；
2. 写入后再次校验会话身份，防止中途切换；
3. 默认激活窗口、重新扫描按钮位置并点击发送按钮；
4. 轮询输入框，确认已清空。

默认方式不发送 Enter，但会短暂移动鼠标点击发送按钮，点击后恢复鼠标位置。按钮不存在、点击失败、输入框未清空或会话切换时都会报错；除会话已切换的情况外，会尽量清空本次写入的内容。`enter` / `auto` 的 Enter 回退需要微信保持默认的“Enter 发送”设置。

输出示例：

```
会话     : '文件传输助手'
发送结果 : 成功
发送方式 : button
填充方式 : value_pattern
文本长度 : 8
```

返回结构：

```json
{
  "hwnd": 123456,
  "chat_name": "文件传输助手",
  "text": "你好",
  "fill_method": "value_pattern",
  "send_method": "button",
  "verified": true
}
```

作为库使用：

```python
from read_private_chat import read_private_chat
from send_message import send_message

data = read_private_chat(limit=20, max_scrolls=60)
send_message("你好", session=data["session"])
```

`send_message()` 与 `read_private_chat()`、`fill_input()` 一样，是对下游暴露的原子操作。

### get_messages.py

```powershell
python get_messages.py
```

不判断会话类型，读取消息列表**当前已暴露**的消息项，不滚动、不加载历史：

```
消息条数 : 3（未滚动，仅当前可获得）
[ 0] time   '10:24'
[ 1] text   '你好'
[ 2] image  '图片'
```

消息项的 `kind` 可能为：

| kind | 含义 |
| --- | --- |
| `time` | 时间分隔 |
| `text` | 文本消息 |
| `bubble` | 文件、链接、卡片等气泡 |
| `image` | 图片 |
| `voice` | 语音 |
| `app` | 小程序、引用等应用卡片 |
| `other` | 其他未识别项 |

无法定位消息列表时在 stderr 报运行时错误，并以退出码 `2` 退出：

```
错误: 未找到消息列表，无法读取聊天记录
```

消息列表存在但没有消息项时返回 `count: 0`、`messages: []`。由于不滚动，返回条数取决于 UIA 当前暴露的消息项，可能少于完整历史；消息内容取自控件名，不额外解析发送者与引用结构。需要更多历史时使用下面的 `read_private_chat.py`（仅私聊）。

### read_private_chat.py

面向下游的组合脚本：先校验微信 UIA 可用、当前是私聊会话，然后返回会话信息和聊天记录；默认向上滚动加载更多。

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
| `--scroll-step` | `1` | 每次滚动的鼠标滚轮格数 |
| `--settle` | `0` | 检测到滚动变化并稳定后额外等待的秒数；默认使用轮询等待 |
| `--timeout` | `20` | 滚动总超时（秒） |
| `--wake-minimized` | 关闭 | 允许自动还原最小化的微信窗口 |
| `--wake-tray` | 关闭 | 允许通过微信原生托盘回调唤起隐藏到托盘的微信 |
| `--restore` | 关闭 | 读取后恢复原始滚动位置 |
| `--no-restore` | **默认** | 不恢复原始滚动位置 |
| `--no-scroll` | 关闭 | 只读当前视口 |
| `--json` | 关闭 | 输出完整 JSON |

前置条件不满足时以退出码 `2` 报错：

- 微信窗口不可用（未启动、最小化、隐藏到托盘、屏幕外）
- UIA 控件树不可用
- 当前不是私聊会话
- 找不到私聊聊天区或消息列表

#### 窗口唤起

窗口最小化或隐藏到托盘时，默认直接报错，不会主动改变窗口状态。需要自动化时：

```powershell
python read_private_chat.py --wake-minimized   # 只允许还原普通最小化
python read_private_chat.py --wake-tray        # 只允许从托盘唤起
python read_private_chat.py --wake-minimized --wake-tray
```

- `--wake-minimized` 对真正的 Win32 最小化使用 `ShowWindow(SW_RESTORE)`。
- `--wake-tray` 不调用 `ShowWindow`。首选读取 Explorer 托盘工具栏并投递微信原生托盘回调消息，不移动鼠标、不模拟点击；该方式失败时才退回 `Shell_NotifyIconGetRect` + 鼠标双击。
- 对托盘隐藏的 Qt 窗口强制 `ShowWindow` 只会恢复 Win32 可见性，Qt 渲染循环不恢复，客户区会变成纯灰色。
- 找不到托盘项或唤起失败时仍以退出码 `2` 报错，并提示手动双击托盘图标。

#### 输出

```
会话     : '某个私聊' (私聊 / private)
窗口状态 : normal
消息条数 : 20
滚动     : 开启 | 步数 4 | 到达顶部: 否 | 截断: 是 | 恢复位置: 跳过
[  0] text   '较早的消息'
...
[ 19] text   '最新的消息'
```

返回结构包含：

- `hwnd`、`window_state`
- `chat_name`、`type`、`label`、`member_count`、`can_send`
- `session`：完整会话身份（`chat_name`、`type`、`member_count`、`selected_session_name`、`session_automation_id`、`session_runtime_id`），可直接传给 `fill_input(..., session=...)`
- `chat_info`：聊天区与消息列表矩形、逻辑坐标
- `count`、`messages`：按时间正序，字段与 `get_messages` 一致；历史消息的 `rect` 统一为 `null`
- `scroll`：`enabled`、`limit`、`max_scrolls`、`steps`、`reached_top`、`truncated`、`limit_hit`、`restored`、`timed_out`、`warnings`、`elapsed_seconds`

#### 滚动实现

- 只使用鼠标滚轮向上滚动，不使用键盘，避免误输入或误发送
- 优先用上一层视口的第一条消息作为锚点合并，避免重复
- Qt 的 RuntimeId 会被复用，因此去重键由 RuntimeId + 类名 + 文本 + 子文本共同组成
- 默认不恢复滚动位置，耗时更少；需要时用 `--restore` 把原始视口的第一条文本消息（找不到文本时取第一条非时间消息）滚回原位置。属于近似恢复，不保证像素级一致。无论是否恢复，鼠标位置和原前台窗口都会恢复
- 默认 `settle=0`：不固定等待，而是轮询视口首尾项签名，等滚动变化并稳定后立即读取；若机器较慢或出现“未找到重叠锚点”等 warnings，可把 `--settle` 调到 `0.05`～`0.2`
- 到达顶部采用保守判定：连续 3 次滚动后视口首项和消息集合都没有变化才认定到顶；图片预览加载慢时可能多等几轮
- 不保证读到完整历史：受 `limit`、`max_scrolls`、`timeout` 和微信自身懒加载限制

## 面向下游的原子操作

`read_private_chat.py`、`fill_input.py` 和 `send_message.py` 是给下游使用的三个原子操作：

- `read_private_chat()`：读取当前私聊，并返回 `session` 身份；
- `fill_input()`：接收文本和 `session`，先校验会话身份，再写入输入框；
- `send_message()`：接收文本和 `session`，校验后写入，默认点击发送按钮发送（可切换为 Enter）。

推荐组合：

```python
from read_private_chat import read_private_chat
from fill_input import fill_input
from send_message import send_message

data = read_private_chat(limit=20, max_scrolls=60)
fill_input("你好", session=data["session"])       # 只填入，不发送
send_message("你好", session=data["session"])     # 填入并发送
```

`session` 包含 `chat_name`、`type`、`member_count`、`selected_session_name`、`session_automation_id`、`session_runtime_id`。三个操作都会逐项比较，避免中途切换到同名会话后误写或误发。

如果读取后切换过会话，`fill_input` / `send_message` 会抛 `ChatMismatchError`，不会写入或发送。

## Python 库调用

读取类型与输入框：

```python
from errors import CannotSendError, WindowUnavailableError
from get_chat_type import get_current_chat_type
from get_input import get_input_state

try:
    info = get_current_chat_type()   # 窗口不可用时抛 WindowUnavailableError
    print(info["type"], info["can_send"])

    state = get_input_state()        # 不可发送时抛 CannotSendError
    print(state["text"], state["is_empty"])
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
except CannotSendError as exc:
    print("不能发送:", exc)
```

窗口不可用时，校验会在调用链最前端抛出 `WindowUnavailableError`，下游函数不会继续执行，也不会返回半成品数据。

读取当前消息列表：

```python
from errors import MessageListUnavailableError, WindowUnavailableError
from get_messages import get_messages

try:
    data = get_messages()   # 不判断会话类型
    print(data["count"])
    for msg in data["messages"]:
        print(msg["kind"], msg["text"])
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
except MessageListUnavailableError as exc:
    print("无法读取消息:", exc)
```

读取私聊历史（带滚动）：

```python
from errors import NotPrivateChatError, UIAUnavailableError, WindowUnavailableError
from read_private_chat import read_private_chat

try:
    data = read_private_chat(limit=20, max_scrolls=60)
    print(data["chat_name"], data["count"])
    print(data["scroll"]["reached_top"], data["scroll"]["truncated"])
    for msg in data["messages"]:
        print(msg["kind"], msg["text"])
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
except UIAUnavailableError as exc:
    print("UIA 不可用:", exc)
except NotPrivateChatError as exc:
    print("不是私聊:", exc)
```

## 行为约定

- 除 `fill_input.py` / `send_message.py` 外只读：不点击消息、不切换会话、不发送消息、不修改输入框
- `fill_input.py` 只写输入框，不按 Enter、不点击发送；仅传 `chat_name` 时要求该名称在会话列表中唯一，重名会抛 `ChatMismatchError`；输入框 `strip()` 后必须为空，非空时抛 `InputNotEmptyError`，不覆盖也不追加；写入后会用 UIA 读回校验
- `send_message.py` 校验会话和空输入框后调用 `fill_input`，再次校验会话；默认激活窗口、重新扫描并点击发送按钮，不发送 Enter；`--send-method enter` / `auto` 才使用 Enter，点击后恢复鼠标位置
- `read_private_chat.py` 的滚动是唯一常规界面副作用：只滚轮、不点消息；默认不恢复滚动位置，但会恢复鼠标位置和原前台窗口
- 默认不改变窗口状态：最小化/隐藏到托盘/屏幕外的微信立即报错并提示，退出码 `2`
- `--wake-minimized` 只对普通最小化窗口使用 `ShowWindow(SW_RESTORE)`；`--wake-tray` 首选投递微信原生托盘回调，不使用 `ShowWindow`，也不模拟点击
- early panic：窗口不可用时在入口处抛 `WindowUnavailableError`，调用链下游不会继续执行
- 一次读取只遍历一次控件树：类型、输入框、消息都消费同一份快照，避免结果来自不同时刻
- 所有矩形统一为 `(x, y, width, height)`；历史消息的 `rect` 为 `null`
- 异常都继承自 `WeChatUIAError`：`WindowUnavailableError`、`UIAUnavailableError`、`NotAConversationError`、`NotPrivateChatError`、`CannotSendError`、`ChatMismatchError`、`InputFillError`、`InputNotEmptyError`、`SendMessageError`、`MessageListUnavailableError`
- UIA 坐标为物理像素，高 DPI 环境下与常规逻辑坐标不同
- 依赖微信 UI 的类名与 AutomationId，微信升级后通常只需修改 `uia_utils.py` 中的锚点

## 已知限制

- UIA 读取仅在 Windows 桌面版微信 4.x 验证；定位层虽兼容 `WeChat.exe` / `Weixin.exe`，3.x 的控件树未做验证
- 需要微信已登录且打开过至少一个会话
- 公众号推送、服务通知、公众号聚合页、列表页等不可发送视图无法读取输入内容
- `get_messages.py` 不滚动列表，只返回当前已暴露的消息项，不保证完整历史
- `read_private_chat.py` 只支持私聊；滚动依赖鼠标滚轮，过程中会临时把微信置前。若前台切换失败或系统关闭悬停滚动，会在 `warnings` 中提示，且可能读不到更早消息
- `--wake-tray` 首选读取 Explorer 托盘工具栏并投递原生托盘回调消息，不移动鼠标、不模拟点击；Explorer 权限不足或结构变化时退回 `Shell_NotifyIconGetRect` + 鼠标双击，仍失败则报错并提示手动双击托盘图标
- 群聊发送者和消息方向无法通过 UIA 获取（见下一节）
- `send_message.py` 默认点击发送按钮；按钮不可用或点击失败时会报错。`--send-method enter` / `auto` 的 Enter 路径假设微信保持默认的“Enter 发送”设置；若设置为 `Ctrl+Enter`，会拒绝发送并清空输入
- 发送过程中如果用户切换会话，会在发送前重新校验并取消发送；已填入旧会话的草稿可能保留
- 历史消息来自不同滚动位置，`rect` 不再具有屏幕坐标意义

## UIA 能力实测

在微信 4.2 / `uiautomation 2.0.29` 上做过只读探查，结论如下：

| 能力 | 实测结果 | 影响 |
| --- | --- | --- |
| `chat_message_list` 的 `ScrollPattern` | 不支持 | 不能替代鼠标滚轮，`message_history.py` 继续使用滚轮 |
| `chat_message_list` 的 `ItemContainerPattern` / `VirtualizedItemPattern` | 不支持 | 不能按属性查找或实体化未渲染消息 |
| `chat_message_list` 的 `TextPattern` | 不支持 | 只能逐项读取消息 |
| 会话项 `SelectionItemPattern.IsSelected` | 支持 | `get_sessions()` 增加 `selected` 字段，CLI 标记 `[当前]` |
| 消息项子控件 | 无（`GetChildren()` 为 0） | 无法从头像/子控件取发送者 |
| 消息项矩形 | 横跨整个消息列表 | 无法用左右位置判断“自己/对方” |
| 输入框 `ValuePattern` / `TextPattern` | 支持 | `get_input.py` 优先用 `ValuePattern` 读取 |
| 输入框 `TextEditPattern` | 不支持 | 光标/选区能力不完整 |

因此，群聊发送者和消息方向目前无法通过 UIA 获取；需要另找方案（截图/OCR、点击消息查看详情等）。

## 测试

纯逻辑测试不需要真实微信窗口：

```powershell
python -m unittest discover -s tests -t . -v
```

## 项目结构

```
errors.py            # 统一异常族（不依赖第三方库）
get_hwnd.py          # L0 窗口句柄、状态与可选唤醒（纯 win32）
uia_utils.py         # L1 UIA 基础能力与锚点常量
snapshot.py          # L2 一次遍历生成窗口快照（唯一导入 uiautomation 的模块）
get_chat.py          # L3 会话与聊天区定位
get_chat_type.py     # L3 会话类型与可发送性
get_input.py         # L3 输入框内容读取
fill_input.py        # L3 输入框文本写入（首选 ValuePattern，回退 SendInput）
send_message.py      # L3 校验会话与空输入框后点击发送按钮/按 Enter
get_messages.py      # L3 当前消息列表读取（不判断会话类型）
message_history.py   # L4 滚轮历史采集、去重、位置恢复
read_private_chat.py # L5 面向下游的私聊组合入口
tests/               # 窗口唤醒、UIA 能力、填写/发送、类型判定、历史合并、矩形约定与分层规则测试
requirements.txt     # 运行依赖
```

依赖方向固定为 `errors / get_hwnd / uia_utils → snapshot → 各读取模块 → message_history → read_private_chat`。`get_messages` 不依赖 `message_history`，单次读取保持无副作用；`tests/test_structure.py` 会检查依赖白名单。
