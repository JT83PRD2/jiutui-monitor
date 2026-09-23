# 保研九推消息监控

定时抓取 985 高校研招网与公管/社会学相关院系官网的「通知公告」列表页，用关键词过滤出推免/九推相关的新公告，通过**微信 + 邮件**双通道第一时间推送，避免遗漏。

## 工作原理

```
GitHub Actions 每 10 分钟 → monitor.py
  → 遍历 targets.yaml 中的列表页 URL
  → 抓取页面（兼容 GBK 编码）→ 提取所有链接
  → 按「推免/九推/复试/拟录取…」关键词过滤
  → 与 state.json 的"已见链接"比对，找出新增
  → 新增 → 微信(PushPlus/Server酱) + 邮件(SMTP) 推送
  → 把新状态提交回仓库，完成去重
```

- **首次运行只建基线、不推送**，避免一次性把几十条历史公告都推给你。
- 抓取失败会单独发一条「抓取异常」告警，避免静默漏抓。

## 目录结构

```
jiutui-monitor/
├── monitor.py                 # 主脚本
├── targets.yaml               # 目标网页清单 + 关键词
├── state.json                 # 去重状态（自动生成，Actions 自动回推）
├── requirements.txt
├── .github/workflows/monitor.yml
└── README.md
```

## 快速开始

### 1. 建 GitHub 仓库（用 **public** 仓库，免费且定时无分钟数限制）

```bash
git init
git add .
git commit -m "init jiutui monitor"
# 在 GitHub 新建仓库 jiutui-monitor（public），然后：
git remote add origin https://github.com/<你的用户名>/jiutui-monitor.git
git push -u origin main
```

### 2. 配置微信推送（主通道）

任选其一，拿到 token 填入 Secrets：

| 服务 | 获取方式 | Secret 名 |
|---|---|---|
| PushPlus（推荐） | pushplus.plus 微信扫码登录 → 「一对一推送」复制 token | `PUSHPLUS_TOKEN` |
| Server酱 | sct.ftqq.com 登录 → SendKey | `SERVERCHAN_SENDKEY` |

### 3. 配置邮件（备份通道）

用 QQ/163 邮箱：设置 → 账户 → 开启 SMTP 服务 → 拿到**授权码**（不是登录密码）。
建议用 QQ 邮箱，并在「QQ邮箱 App/网页 → 设置 → 邮件提醒」里开启微信提醒，这样邮件也能推到微信。

### 4. 在 GitHub 仓库填 Secrets

`Settings → Secrets and variables → Actions → New repository secret`，添加：

| Secret | 值 |
|---|---|
| `PUSHPLUS_TOKEN` | PushPlus 的 token |
| `SMTP_HOST` | `smtp.qq.com`（或 `smtp.163.com`） |
| `SMTP_PORT` | `465`（QQ 用 465 即可） |
| `SMTP_USER` | 你的邮箱地址 |
| `SMTP_PASS` | SMTP 授权码 |
| `MAIL_TO` | 接收提醒的邮箱（可与 SMTP_USER 相同） |

### 5. 测试

在仓库 `Actions` 页点 `Run workflow` 手动触发一次。观察：
- 日志无报错，`state.json` 被正常 commit 回推；
- 若某个页面恰好有新的推免公告，微信/邮件会收到推送。

也可本地先验证（需装 Python 3 和 `pip install -r requirements.txt`）：

```bash
python monitor.py --dry-run      # 只打印抓到的候选链接，不推送
python monitor.py --test-notify  # 发一条测试通知，验证双通道
```

## 配置说明（targets.yaml）

```yaml
keywords: [推免, 九推, 预推免, 推荐免试, 预报名, 接收, 拟录取, 复试, 夏令营, 考核, 推免生]

targets:
  - name: 北京大学-政府管理学院
    url: https://www.sg.pku.edu.cn/tzgg.htm
    # keywords: [推免, 复试]   # 可选：覆盖该站点专属关键词
```

- 添加学校：在 `targets` 下新增一条，填 `name` 和该单位「通知公告」**列表页** URL（不是某条新闻详情页）。
- 新加的页面首次运行会先建基线（不推送），之后有新增才推送。

## 常见问题

- **乱码**：脚本已兼容 UTF-8 / GBK / GB2312，一般无需处理。
- **某页抓不到（动态渲染/反爬）**：优先换用该单位的静态「通知公告」列表页；确无静态页再单独处理，届时可在 `monitor.py` 的 `fetch` 里针对该站加特殊逻辑。
- **通知量太多**：可收紧 `keywords`，或在 `targets` 里给易发多条的站点配更少关键词。
