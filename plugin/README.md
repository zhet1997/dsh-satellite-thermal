# DSH 卫星热建模插件

适配官方 DSH Web **0.2.0-rc.2**。安装到已有 profile，与其他插件共存。插件包含工作台网页、Host、MCP、Skill，不包含 NX、Python/CAD 运行环境或用户数据。

在 DSH 左侧 **插件 → 添加插件** 的文本框粘贴宿主服务器上 npm tgz 的完整路径，或使用：

```text
dsh plugin --profile web add /path/to/lbk-dsh-satellite-thermal-0.1.1.tgz
```

DSH 设置 → 内置插件 → 卫星热建模，配置服务地址（默认 `http://127.0.0.1:8790`）和可选令牌。服务根地址不带 `/api`。点击“检查连接”确认热服务及 NX 状态。

对话区的“热建模工作台”按钮打开界面。三维/拓扑选区可带入当前对话草稿；模型通过 `satellite_thermal` MCP 工具读取与修改项目。支持断连时继续编辑，运行排队等待工程执行器。

独立工作台：`/satellite-thermal/`。所有请求复用官方 DSH 浏览器认证；仅服务端附加热服务访问令牌。

本包搭配 0.1.1 独立 service；NX worker 的 0.1.0 执行协议保持兼容，本次无需升级 NX worker。未收到真实求解证据时，不会展示伪造的计算结果。详细部署与验收范围在完整源码包 README 和 `docs/验证记录.md`。
