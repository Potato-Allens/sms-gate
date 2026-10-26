# SMS Gate · 卡密接码平台

一套开箱即用的 **卡密制短信验证码接收系统**，自带管理后台、卡密授权页和直链接码接口。
买家打开一条卡密链接即可自助取号、自动收码；运营方在后台生成卡密、看接码明细、管号码池、做安全审计。

> 仅用于自有账号、自有业务或已完成合法授权的测试场景。

---

## 功能特性

### 前台（买家侧）

- **卡密直链**：`https://your-domain.com/c/XXXX-XXXX-XXXX-XXXX`，打开即自动授权，无需手输卡密
- **先取号、再取码**：选择号段 → 分配号码 → 页面每 15 秒自动轮询验证码
- **收不到可换号**：45 秒后可点「收不到？换一个号码」，默认最多 3 次，超时自动释放不占号
- **收码即核销**：默认一卡一码，拿到验证码后立即核销并强制退出（清会话 + `Clear-Site-Data`），防止复用与逆向
- **一卡一次选段**：号段由服务端原子锁定，改数量、改号段、重放请求都无效

### 直链卡（可反复取码）

支持第二种卡密格式，适合「一个号码长期接码」且不暴露上游来源：

```
卡号:+17798001455----https://your-domain.com/smsrecord?token=<32位随机串>
```

- 上游真实接口地址在服务端 Fernet 加密落库，对外零泄漏，可套自己的域名
- 同一条链接可反复查询验证码，可加 `?fmt=json` 拿结构化结果、`?code=1` 只要验证码
- 内置 SSRF 防护：内网 / 回环 / 云元数据地址一律拦截，跳转逐跳校验

### 管理后台

- **登录即动态码**：只需输入 Google Authenticator 六位 TOTP（账号密码 + TOTP 接口保留作应急）
- **卡密管理**：批量生成（1–500 张）、次数、有效期、备注、导出、禁用
- **直链卡**：批量生成、按批次导出（`/admin/api/relay/batches`、`/admin/api/relay/export?batch=<id>`）
- **号码池**：批量导入 `手机号----上游取码地址`，按号段归类
- **数据看板**：兑换情况、接码明细、使用情况、成功率
- **安全审计**：登录、兑换、取号、收码、封禁、配置变更全量留痕
- **IP 管理**：单 IP / CIDR 封禁，跨进程精确限流
- **系统设置**：号源密钥、轮询间隔、换号上限、限流阈值

---

## 号段支持

| 号段 | 说明 |
|---|---|
| `us` | 美国 |
| `gb` | 英国 |
| `hk` | 香港 |
| `cn_real` | 国内实卡 |
| `cn_virtual` | 国内虚拟卡 |

可对接 **NexSMS**、**d1jiema**、**SMS-Activate**、**5SIM**，或用「号码池」接入任意自建/第三方取码 URL。

---

## 技术栈

- **后端**：Python 3.12 + FastAPI + Uvicorn
- **存储**：SQLite（WAL 模式，单机可撑百人并发）
- **前端**：Jinja2 模板 + 原生 JS / CSS，无构建步骤
- **部署**：systemd + nginx + certbot（Let's Encrypt 自动续期）

```
app/            FastAPI 应用（API + 模板 + 静态资源）
  main.py       路由装配、启动、janitor
  public_api.py 前台授权 / 取号 / 轮询取码
  admin_api.py  后台接口
  relay_api.py  直链接码接口
  providers.py  号源抽象（本地号池 / NexSMS / SMS-Activate / 5SIM）
  d1jiema.py    国内实卡、虚卡对接
  nexq.py       NexSMS 客户端
  db.py         SQLite schema 与访问层
  security.py   会话、CSRF、TOTP、限流、审计
deploy/         systemd unit / nginx 站点模板 / 证书脚本
```

---

## 快速开始（本地）

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

mkdir -p data logs
uvicorn app.main:app --host 127.0.0.1 --port 8861
```

首次启动会生成 `data/setup.token`，浏览器打开：

```
http://127.0.0.1:8861/admin/setup?t=<setup.token 内容>
```

创建管理员 → 用 Google Authenticator 扫码 → 输入 6 位动态码完成绑定。
令牌一次性，绑定后立即失效；中途中断可用原账号密码续做，不会重建管理员、不会重置密码。

---

## 部署

```bash
# 1. 上传代码到 /opt/smsgate
# 2. 安装依赖
python3.12 -m venv /opt/smsgate/venv
/opt/smsgate/venv/bin/pip install -r /opt/smsgate/requirements.txt

# 3. 安装 systemd 服务（示例见 deploy/）
cp deploy/smsgate.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now smsgate

# 4. nginx 反代 + 证书
cp deploy/nginx-smsgate.conf /etc/nginx/conf.d/smsgate.conf
nginx -t && systemctl reload nginx
certbot --nginx -d your-domain.com
```

生产建议：`uvicorn --workers 4 --limit-concurrency 400 --backlog 2048`，仅监听 `127.0.0.1`，由 nginx 终结 TLS。

---

## 卡密格式

**格式一 · 授权链接卡**

```
https://your-domain.com/c/XXXX-XXXX-XXXX-XXXX
```

卡密以 SHA-256 落库，明文仅在生成时返回一次；数据库泄露也无法还原。

**格式二 · 直链卡**

```
卡号:+17798001455----https://your-domain.com/smsrecord?token=0123456789abcdef0123456789abcdef
```

---

## 安全设计

- 管理员密码 PBKDF2-SHA256（24 万次迭代），TOTP 密钥加密落库
- 会话 HttpOnly + SameSite=Lax + HTTPS 下 Secure；写操作全量 CSRF 校验
- 登录防撞库：账号 / IP 双维度锁定 + 等时校验 + 随机延迟，失败 5 次 / 10 分钟封 30 分钟
- 跨进程精确限流（SQLite 计数表，多 worker 共享），坏 token 独立桶
- 号段选择原子锁 + 参数白名单，防改数量、改号段、重放
- 输出脱敏：上游单价、余额、消息 ID 一律不下发
- SSRF 防护、CSP、HSTS、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`
- systemd 沙箱（`ProtectSystem=strict` 等）

---

## 性能

单机 4 worker 实测：**120 并发 / 120 成功 / 200 全绿，p95 = 0.72s**，无排队、无阻塞。
号源实测到达率：国内实卡 ≈ 91%、英国 ≈ 73%、香港 ≈ 61%（随上游波动）。

---

## 测试

```bash
python test_smoke.py
python test_relay.py
python test_abuse.py
python test_concurrency.py
python test_retry.py
```

覆盖：授权流程、一卡一码、选段锁、直链卡、换号重试、限流与封禁、并发压测、强制退出。

---

## 免责声明

本项目仅用于**自有账号**或**已获得明确授权**的短信验证码接收场景。
使用者需自行遵守所在地法律法规及目标平台服务条款，由使用者承担全部责任。
