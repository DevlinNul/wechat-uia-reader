# 微信窗口定位

Windows 桌面版微信（4.x）的只读自动化脚本集：定位主窗口、当前会话与聊天区，判定会话类型，读取聊天输入框内容。

脚本不会发送消息、不会点击或切换会话，也不会主动唤起已最小化的微信窗口。窗口最小化或隐藏到托盘时，脚本会直接报错并提示，不会继续读取 UI 数据。

## 环境要求

- Windows 10/11
- Python 3.10+（使用 `X | None` 类型标注）
- 依赖：

```powershell
pip install -r requirements.txt
```

- 微信已登录，并打开过至少一个会话
- 微信的 UI Automation 控件树可被读取（若只能看到 Qt 外壳节点，需先开启系统“屏幕阅读器/辅助功能”再重启微信）

> UI Automation 的类名与 AutomationId 锚点基于微信 4.2 实测，微信版本升级后可能变化，需要相应调整。

## 脚本一览

| 文件 | 用途 |
| --- | --- |
| `get_hwnd.py` | 枚举微信窗口，定位主窗口句柄与窗口状态 |
| `get_chat.py` | 获取当前会话信息、聊天区位置，可选列出会话列表 |
| `get_chat_type.py` | 判定当前会话/视图类型，并给出是否可发送消息 |
| `get_input.py` | 读取当前聊天输入框内容，判断是否为空；不可发送时报错 |
| `get_messages.py` | 读取当前会话可获得的聊天记录（不滚动）；不判断会话类型 |
| `read_private_chat.py` | 仅私聊：读取会话信息与聊天记录，可选向上滚动加载更多 |

## 使用方法

### get_hwnd.py

```powershell
python get_hwnd.py
```

输出所有微信相关窗口，并给出主窗口句柄与状态：

```
主窗口句柄: 0x123456 (1193046)
窗口状态  : normal
```

窗口状态可能为 `normal`、`minimized`、`hidden`、`offscreen`。非 `normal` 时会在 stderr 报错并给出还原提示，退出码为 `2`，不会主动还原窗口。

也可作为库使用：

```python
from errors import WindowUnavailableError
from get_hwnd import require_wechat_main_window

try:
    hwnd = require_wechat_main_window()  # 定位并校验窗口状态
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
```

`find_wechat_main_window()` 只负责定位句柄（可含隐藏窗口），`require_wechat_main_window()` 和 `require_window_ready(hwnd)` 会在窗口未启动、最小化、隐藏或位于屏幕外时立即抛 `WindowUnavailableError`。

主窗口识别不依赖硬编码类名，而是按进程名（`WeChat.exe` / `Weixin.exe`）与窗口样式（可见标题栏、非工具窗口、可最小化/最大化等）综合判断。

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

作为库使用时，`get_chat_info()` 只返回纯数据（不包含 UIA 控件对象）；列表页会抛 `NotAConversationError`。会话列表用 `get_sessions()` 获取；每条记录包含 `name`、`detail`、`raw`、`rect` 和 `selected`，其中 `selected` 来自 `SelectionItemPattern.IsSelected`。

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
| `official` | 公众号/服务号会话（有输入框，可发送） | 是 |
| `broadcast` | 公众号/服务号推送（无输入框，如消息通知） | 否 |
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
python read_private_chat.py --restore
python read_private_chat.py --wake-minimized
python read_private_chat.py --wake-tray
python read_private_chat.py --no-scroll
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

窗口最小化或隐藏到托盘时，默认直接报错，不会主动改变窗口状态。需要自动化时：

```powershell
python read_private_chat.py --wake-minimized   # 只允许还原普通最小化
python read_private_chat.py --wake-tray        # 只允许从托盘唤起
python read_private_chat.py --wake-minimized --wake-tray
```

`--wake-minimized` 对真正的 Win32 最小化使用 `ShowWindow(SW_RESTORE)`。`--wake-tray` 不调用 `ShowWindow`，而是优先用 `Shell_NotifyIconGetRect` 定位微信托盘图标并双击，不读取 Explorer 工具栏内存；图标折叠在溢出区时会临时展开通知区。只有该方式失败时，才退回读取 Explorer 托盘工具栏并投递微信自己的托盘回调消息。对托盘隐藏的 Qt 窗口强制 `ShowWindow` 只会恢复 Win32 可见性，Qt 渲染循环不恢复，客户区会变成纯灰色。找不到托盘项或唤起失败时仍以退出码 `2` 报错，并提示手动双击托盘图标。

输出示例：

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

- `chat_name`、`type`、`label`、`member_count`、`can_send`
- `chat_info`：聊天区与消息列表矩形、逻辑坐标
- `messages`：按时间正序，字段与 `get_messages` 一致；历史消息的 `rect` 统一为 `null`
- `scroll`：`steps`、`reached_top`、`truncated`、`limit_hit`、`restored`、`timed_out`、`warnings`

滚动实现说明：

- 只使用鼠标滚轮向上滚动，不使用键盘，避免误输入或误发送；
- 优先用上一层视口的第一条消息作为锚点合并，避免重复；
- Qt 的 RuntimeId 会被复用，因此去重键由 RuntimeId + 类名 + 文本共同组成；
- 默认不恢复滚动位置，耗时更少；需要时用 `--restore` 把原始视口的第一条非时间消息滚回原位置（近似恢复，不保证像素级一致）；无论是否恢复，鼠标位置和原前台窗口都会恢复；
- 默认 `settle=0`：不固定等待，而是轮询视口首尾项签名，等滚动变化并稳定后立即读取；若机器较慢或出现“未找到重叠锚点”等 warnings，可把 `--settle` 调到 `0.05`～`0.2`；
- 到达顶部采用保守判定：连续 3 次滚动后视口首项和消息集合都没有变化才认定到顶；图片预览加载慢时会多等几轮，避免被误判为顶部；
- 不保证读到完整历史：受 `limit`、`max_scrolls`、`timeout` 和微信自身懒加载限制。

## 作为库调用

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

- 只读数据：不点击消息、不切换会话、不发送消息、不修改输入框
- `read_private_chat.py` 的滚动是唯一常规界面副作用：只滚轮、不点消息；默认不恢复滚动位置，但会恢复鼠标位置和原前台窗口
- 默认不改变窗口状态：最小化/隐藏到托盘/屏幕外的微信立即报错并提示，退出码 `2`
- `--wake-minimized` 只对普通最小化窗口使用 `ShowWindow(SW_RESTORE)`；`--wake-tray` 只走微信原生托盘唤起路径，不使用 `ShowWindow`，避免托盘窗口被强制显示后变成纯灰色；它优先用 `Shell_NotifyIconGetRect` + 双击图标，必要时退回 Explorer 工具栏解析
- early panic：窗口不可用时在入口处抛 `WindowUnavailableError`，调用链下游不会继续执行
- 一次读取只遍历一次控件树：类型、输入框、消息都消费同一份快照，避免结果来自不同时刻
- 所有矩形统一为 `(x, y, width, height)`；历史消息的 `rect` 为 `null`
- 异常都继承自 `WeChatUIAError`：`WindowUnavailableError`、`UIAUnavailableError`、`NotAConversationError`、`NotPrivateChatError`、`CannotSendError`、`MessageListUnavailableError`
- UIA 坐标为物理像素，高 DPI 环境下与常规逻辑坐标不同
- 依赖微信 UI 的类名与 AutomationId，微信升级后通常只需修改 `uia_utils.py` 中的锚点

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

## 已知限制

- 仅在 Windows 桌面版微信 4.x 验证
- 需要微信已登录且打开过会话
- 公众号推送、服务通知、公众号聚合页、列表页等不可发送视图无法读取输入内容
- `get_messages.py` 不滚动列表，只返回当前已暴露的消息项，不保证完整历史
- `read_private_chat.py` 只支持私聊；滚动依赖鼠标滚轮，过程中会临时把微信置前，若前台切换失败或系统关闭悬停滚动，会在 `warnings` 中提示且可能读不到更早消息
- `--wake-tray` 优先使用 `Shell_NotifyIconGetRect`；若托盘图标在溢出区，会临时展开通知区。Shell API 不可用或图标位置异常时会退回 Explorer 工具栏解析，仍失败则报错并提示手动双击托盘图标
- 历史消息来自不同滚动位置，`rect` 不再具有屏幕坐标意义

## 测试

纯逻辑测试不需要真实微信窗口：

```powershell
python -m unittest discover -s tests -t . -v
```

## 项目结构

```
errors.py          # 统一异常族（不依赖第三方库）
get_hwnd.py        # L0 窗口句柄、状态与可选唤醒（纯 win32）
uia_utils.py       # L1 UIA 基础能力与锚点常量
snapshot.py        # L2 一次遍历生成窗口快照（唯一导入 uiautomation 的模块）
get_chat.py        # L3 会话与聊天区定位
get_chat_type.py   # L3 会话类型与可发送性
get_input.py       # L3 输入框内容读取
get_messages.py    # L3 当前消息列表读取（不判断会话类型）
message_history.py # L4 滚轮历史采集、去重、位置恢复
read_private_chat.py # L5 面向下游的私聊组合入口
tests/             # 窗口唤醒、UIA 能力、类型判定、历史合并、矩形约定与分层规则测试
requirements.txt   # 运行依赖
```

依赖方向固定为 `errors / get_hwnd / uia_utils → snapshot → 各读取模块 → message_history → read_private_chat`。`get_messages` 不依赖 `message_history`，单次读取保持无副作用；`tests/test_structure.py` 会检查依赖白名单。
