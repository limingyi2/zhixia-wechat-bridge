# 微信群桥接

让**指定的人格**在微信群里以真人方式接话。人格是 `preset_group` 这类配置项，
名字由人格自己定义（本项目当前用的是"麦麦"），不是项目名。

**当前跑在云服务器上，云服务器到期可整套切到虚拟机**，
切换只改一个配置文件里的一个地址。

## 快速上手

- **要部署到虚拟机** → 看 [`docs/部署手册.md`](docs/部署手册.md)
- **想了解为什么这么设计** → 看 [`docs/技术约束.md`](docs/技术约束.md)
- **要改配置** → 复制 [`config-example/wxbridge.local.example.json`](config-example/wxbridge.local.example.json)
  到机器的 `C:\zx\wxbridge.local.json` 再改

## 目录

这是主项目里的一个**子模块**：大脑（`brain/`）、桌面宠物（`pet/`）、手机 App（`android-app/`）
是主项目，本目录只负责"让她在微信群里接话"这一件事。它不含任何状态 ——
记忆、人设、日记全在云端大脑里，跟 App 和桌面宠物共用同一份。

```
wxauto_bridge.py              桥接本体：读消息 → 问大脑 → 发消息
brain-plugin/gate.py          云端发言闸门（装到云端 brain/plugins/）
config-example/               脱敏配置示例（真配置含凭据，不进仓库）
docs/                         部署手册 / 技术约束 / 虚拟机脚本说明

vm/                           虚拟机侧脚本
├─ zx.cmd                    ★ 主入口：一键装包 → 开隧道 → 起桥接
├─ GO.cmd                    解压安装包 + 建桌面快捷方式（发给用户的第一入口）
├─ FIX_AND_RUN.cmd           安装出错时的补救
├─ zx_launcher.py            启动器：唤醒无障碍闸门 → attach → 拉起桥接
├─ zx_uia_wake.py            微信 4.x 无障碍闸门唤醒
├─ zxrdp.cmd                 连虚拟机自带的远程桌面
├─ vbox/                     虚拟机本体：装 VirtualBox / 建虚拟机 / 配置
│  ├─ install_vbox.bat         静默装 VirtualBox 到 D 盘
│  ├─ zxvm.cmd                建虚拟机
│  └─ zxvm2.cmd               配虚拟机（网络 / 共享文件夹 / 串口）
└─ runtime/                  日常运行：隧道保活 + 自测
   ├─ VigilTunnel.bat         保持 127.0.0.1:18787 → 大脑 8788 不断
   └─ VigilSelfTest.bat             加群前先在小号上对一遍话
```

### `vm/` 里为什么分三层

`vm/` 下混过三种东西，放在一起时维护的人得自己猜哪个是"装虚拟机"、
哪个是"日常开隧道"、哪个是"一键启动"。现在按**使用时机**分：

- **根目录** —— 用户会双击的那几个（`zx.cmd` / `GO.cmd` / `FIX_AND_RUN.cmd`）
- **`vbox/`** —— 装虚拟机，机器上只做一次
- **`runtime/`** —— 天天用的（隧道保活、自测）

注意别被名字骗了：安装包里也有一个 `bridge\` 目录
（`C:\zx\bridge\wxauto_bridge.py`），那是**装完之后**的运行位置，
跟源码里的目录不是一回事。打包时会把这些脚本摊平放进安装包。

### 与 `brain/wechat_bot/` 的关系

`brain/wechat_bot/wxauto_bridge.py` 和本目录的 `wxauto_bridge.py` **是同一份代码**
（内容必须保持一致，md5 相同）。区别只在用在哪：

- `wechat_bridge/`（本目录）→ **源码**，进安装包发给虚拟机
- `brain/wechat_bot/` → **本机运行时位置**，部署脚本从这里读取

改逻辑时两边都要同步；只改一边会出现"虚拟机上是新版、本机还是旧的"。

## 安全闸

🔴 **大号微信绝不会被读取、发送或操作**，闸门在**代码层**生效，不靠自觉：

- `expected_wxid` 必须是麦麦小号的 wxid（形如 `wxid_一串字母数字`），对不上**拒绝运行**
- `forbidden_wxids` 里放大号的 wxid，**永久拒绝**
  （这里原来写的是两个真实 wxid。仓库是 PUBLIC，账号 ID 属于个人身份信息，
  写进来等于挂出去 —— 真值看本地那份 `wxbridge.local.json`，它被 .gitignore 挡着。）
- 运行中每 3 轮复查一次身份 —— 启动时校验过不算数，
  因为微信 4.x 是跨目录单实例，你把微信切回大号后桥接还开着
- 两道闸独立，`expected_wxid` 填错了 `forbidden` 那层也拦得住

## 凭据处理

仓库是 **PUBLIC**。以下内容**永远不进仓库**（已在 `.gitignore` 挡住并验证）：

- `brain_token`（等同于密码）
- SSH 私钥 `deploy/sshkeys/`
- `wxbridge.local.json`（含 token、桥接机 IP、群名）
- `*.apk` / `*.exe` / 联调截图

`config-example/` 里那份是**空的**，只留字段和说明。

### `zx.cmd` 里的 token 从环境变量读

一键脚本要打 `api/plugins` 探活，那个 token **不在文件里**，从环境变量读：

```bat
set ZX_BRAIN_TOKEN=<你的 brain_token>
vm\zx.cmd
```

没设的话这一步会打印 `[SKIP]` 并跳过，**不会**当成隧道失败 ——
没配检查项和检查没通过是两回事。

早期版本把 token 直接写在命令行里。仓库是 PUBLIC，同一个文件里已经带着
云机 IP，再顺手写死口令等于把两样一起发出去，所以改成环境变量。

## 相关

- 云端大脑本体、人设、记忆 → 仓库 `brain/`、`docs/微信群聊接入.md`
- 插件系统总览 → `docs/插件系统.md`
