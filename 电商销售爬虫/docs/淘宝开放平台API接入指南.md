# 淘宝开放平台（TOP）API 接入指南

> 目标：让淘宝取数从"CDP 连浏览器 + 天天过滑块"，换成"服务端直接调官方接口"，彻底告别登录折腾。
> 配套可运行 demo：项目根目录 `淘宝API_demo.py`（填入凭证即可测第一个请求）。

---

## 0. 一句话原理

每次调 API，你要发这么一个 POST 请求给**网关**：

```
你的业务参数（如日期范围） + 一套公共参数（app_key/timestamp/…） + 一个 sign 签名
```

`sign` 是用你的 **AppSecret** 把所有参数算出来的一段指纹。服务端用同样算法验签，对得上才返回数据。
**所以"连接淘宝 API"= 会算这个 sign + 会拼这个请求。** 这份文档就教这两件事。

---

## 1. 总流程（5 步）

| 步骤 | 做什么 | 谁做 |
|---|---|---|
| ① 注册 + 建"自用型应用" | 拿到 **AppKey / AppSecret** | 你（用店铺账号）|
| ② 发第一个请求 | 用 `taobao.time.get` 验证凭证+签名对不对 | 你（跑 demo）|
| ③ 申请接口权限 | 申请交易/退款接口 | 你（要审核 1–3 天）|
| ④ OAuth 授权 | 店铺授权应用，拿到 **session（access_token）** | 你 |
| ⑤ 调业务接口 + 接进工具 | 取订单/退款 → 汇总日报 | 我 |

---

## 2. 第一步：注册 & 建"自用型应用"

1. 打开淘宝开放平台 **open.taobao.com**（或 developer.alibaba.com），用**店铺账号**登录、注册成为开发者。
2. 进"控制台 → 应用管理 → 创建应用"，类型选 **"自用型应用"**（商家给自己店铺用，门槛比 ISV 低）。
3. 创建后进应用详情，记下 **AppKey** 和 **AppSecret**（Secret 保密，别外传）。

> 可能需要企业资质/营业执照；首个应用创建可能要过一次审核。

---

## 3. 第二步：发出第一个请求（Hello World）

**目的**：先不碰授权，只验证"AppKey + AppSecret + 签名"这条链路通不通。用 `taobao.time.get`（取服务器时间，**不需要店铺授权**）。

1. 打开 `淘宝API_demo.py`，把顶部 `APP_KEY` / `APP_SECRET` 填成你的。
2. 运行：
   ```bash
   python 淘宝API_demo.py
   ```
3. **成功**长这样：
   ```json
   { "time_get_response": { "time": "2026-08-17 15:30:00" } }
   ```
   → 说明凭证和签名都对了，链路通了。✅
4. **失败**会返回错误码，最常见：
   - `isv.invalid-signature`（签名错）→ 见 §4 和 §7
   - `isv.invalid-timestamp`（时间戳错）→ 多半是**时区问题**，见 §4 提醒

---

## 4. 签名算法详解（最容易错的地方）

以 **md5** 方式为例（demo 里用的就是这个）：

1. **排序**：把除 `sign` 外的所有参数，按【参数名的 ASCII 顺序】排序。
2. **拼接**：按顺序把 `参数名 + 参数值` 首尾相接，拼成一个长字符串。
   例：`app_keyxxxxformatjsonmethodtaobao.time.get...`
3. **加密**：头尾各拼上 AppSecret → `secret + 上面的串 + secret`，整体 **MD5**，结果转**大写**。

对应 demo 里的代码：
```python
ordered = "".join(f"{k}{params[k]}" for k in sorted(params) if k != "sign")
raw = f"{secret}{ordered}{secret}"
sign = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
```

> ⚠ **两个最坑的点**：
> 1. **时区**：`timestamp` 必须是**北京时间（GMT+8）**。**本机是日本时区（JST=+9）**，直接用本地时间会差 1 小时 → 报时间戳/签名错。demo 里已用 `datetime.now(timezone(timedelta(hours=8)))` 显式取北京时间，别改。
> 2. **AppSecret 位置**：md5 方式是"头尾都拼 secret"；hmac 方式只在头部拼。用哪种由 `sign_method` 决定，别混。

---

## 5. 第三步：申请接口权限 + OAuth 授权拿 session

**读店铺自己的数据（订单/退款）需要两样东西：接口权限 + 用户授权令牌(session)。**

### 5.1 申请接口权限
在应用详情 → "API 权限"里，申请要用的接口（见 §6）。交易类多属敏感权限，**人工审核 1–3 个工作日**，个别要补材料。

### 5.2 OAuth 授权拿 session（access_token）
1. 拼授权链接，让店铺账号点进去授权：
   ```
   https://oauth.taobao.com/authorize?response_type=code&client_id=你的AppKey&redirect_uri=你的回调地址&state=1&view=web
   ```
2. 授权后会跳回你的回调地址，带一个 `code` 参数。
3. 用 `code` 换 token（调 `https://oauth.taobao.com/token`，POST：grant_type=authorization_code、code、client_id、client_secret、redirect_uri）：
   返回里的 **`access_token`** 就是后续所有请求要带的 `session`。
4. **有效期约 180 天**（返回的 `expires_in` / `re_expires_in`），到期前用 `refresh_token` 刷新或重新授权即可。

> 自用型应用的授权流程可能有简化入口（应用详情里常有"测试授权/一键授权"按钮直接给你 access_token），以实际控制台为准。

---

## 6. 第四步：调交易/退款接口，取销售数据

拿到 `session` 后，就能调业务接口了。我们要的"销售/退款/订单"大致这样取：

### 6.1 订单（销售、支付金额、订单数）
- 接口：`taobao.trades.sold.get`（查已卖出的交易列表）
- 关键参数：
  - `fields`：要返回的字段，如 `tid,status,payment,pay_time,created`
  - `start_created` / `end_created`：时间范围（`yyyy-MM-dd HH:mm:ss`）
  - `page_no` / `page_size`（最大 100）、`use_has_next=true` 翻页
- 汇总思路：把某天的订单拉全 → `payment` 求和 = 销售额、条数 = 订单数。
  （注意口径：按"下单时间 created"还是"支付时间 pay_time"筛，要和生意参谋对齐。）

### 6.2 退款
- 接口：`taobao.refunds.receive.get`（收到的退款列表）/ `taobao.refund.get`（单笔）
- 汇总某天的退款金额。

> 具体字段/参数以控制台里那个接口的**文档页 + "API 测试工具"**为准（登录后能在线试调、直接看返回结构）。先在测试工具里点通，再写代码最省事。

---

## 7. 常见错误码速查

| 错误码 | 含义 | 怎么办 |
|---|---|---|
| `isv.invalid-signature` | 签名不对 | 检查：参数排序、secret 头尾位置、有没有把 sign 也参与了签名（不能）、中文/编码 |
| `isv.invalid-timestamp` | 时间戳不对 | **时区**！用北京时间；也别用太旧的时间 |
| `isv.missing-parameter` | 少必填参数 | 对照接口文档补 |
| `isv.permission-api-*` | 没权限 | 去申请该接口权限并等审核 |
| `session 过期/无效` | 令牌失效 | 重新授权或用 refresh_token 刷新 |
| `isv.rate-limit` | 超调用频率 | 降频/加缓存 |

---

## 8. 跑通后怎么接进现有工具（我来做）

你把 §3 的 Hello World 跑通、§5 拿到 session、§6 在测试工具里点通后，把 **AppKey / AppSecret / session** 给我，我会：
1. 在 `platforms/taobao.py` 里新增一个"官方 API"取数方式（读订单+退款→汇总成 销售/退款/订单）；
2. 用 config 的 `crawl_method` 或新开关切换：淘宝从"CDP 连 Chrome"改成"服务端 API"；
3. 和生意参谋核对几天口径，对齐后正式切换。

**切完之后：淘宝再也不用开浏览器、不用过滑块、令牌 180 天——登录痛点彻底消失。**

---

## 参考
- 淘宝开放平台文档中心 / API 测试工具（登录后可在线试调）
- TOP API 调用说明（公共参数、签名、错误码）
