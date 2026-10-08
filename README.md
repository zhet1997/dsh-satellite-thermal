# 卫星热建模工作台

开发目录：`D:\Codes\dsh-satellite-thermal`。旧启动路径的兼容方式及运行数据位置见 [开发目录迁移](docs/开发目录迁移.md)。

将原 Agent Web 热流程拆为官方 DSH 网页插件：三维几何、可编辑热拓扑、标准参数、运行队列和 NX 证据查看。首版以稳态为目标，重型工程软件由独立 NX 工作站提供。

项目状态与实测范围见 [验证记录](docs/验证记录.md)。这是开发首版；结果页面只有在收到真实 NX 结果后才显示温度，不提供模拟成功的替代结果。

首次安装请直接阅读 [中文安装与使用说明](docs/安装与使用.md)。完整安装套件为 `dist/dsh-thermal-web-0.1.1-installation.zip`，本机可直接使用的解压目录是 `release/dsh-thermal-web-0.1.1`。套件包含服务部署文件和启动辅助脚本；首次部署依赖可能需要联网。

已提供 `dist/lbk-dsh-satellite-thermal-0.1.1.tgz`（网页插件）、`dist/dsh_satellite_thermal_executor-0.1.0-py3-none-any.whl`（独立 NX 执行器）及 `dist/dsh-satellite-thermal-0.1.1-source.zip`（源码与部署文件）。校验值见 `dist/release-manifest.json`。源码包保留本地验证记录，不包含用户几何、登录信息或 NX 安装程序。

## 三部分部署

| 部分 | 内容 | 环境 |
|---|---|---|
| `plugin` | 官方 DSH 插件、工作台、原生对话工具与 Skill | DSH Web 0.2.0-rc.2 / Node 22+ |
| `service` | 项目数据、资料提取、STEP 预览、外热流与队列 | Python 3.12 / Docker，默认 8790 |
| `nx-worker` | 接收冻结输入、调用 NX journal、回读/取消/返回工程 | Windows NX / Simcenter 工作站 |

NX 不包含在前两部分中。可以把 service 放在 Linux 容器，把 worker 放在具有许可的工程工作站上。项目数据需要持久卷。

## 安装到现有官方网页版

构建后的 `dist/lbk-dsh-satellite-thermal-0.1.1.tgz` 是标准 npm 插件包，在左侧 **插件 → 添加插件** 的输入框粘贴 DSH 宿主服务器上的完整包路径，或通过官方命令安装：

```powershell
dsh plugin --profile web add D:\path\lbk-dsh-satellite-thermal-0.1.1.tgz
```

保留原有 bundles 与 profile 配置。开发时也可在已安装依赖的 `plugin` 目录执行 `node install-profile.mjs <已有profile名称>`，它只追加热插件并保存配置备份。不要把整个既有 profile 换成示例 profile。

启动轻量服务后，在 **DSH 设置 → 内置插件 → 卫星热建模** 填写服务根地址和可选令牌。通过“检查连接”区分热服务可用和 NX 就绪。对话输入区点击“热建模工作台”进入；工作台“交给助手”会把当前 projectId、revision 和选区带入当前对话草稿，可编辑后发送。

工作台地址为 DSH 地址加 `/satellite-thermal/`。插件使用官方登录态，访问令牌保留在宿主，不发送到工作台浏览器。

## 本地开发

```powershell
cd web
npm ci
npm run build
cd ../plugin
npm ci
npm run build
npm test
npm pack --pack-destination ../dist
```

服务依赖和启动方式见 [service](service/README.md)，工程执行器见 [nx-worker](nx-worker/README.md)。使用 `web` 的 `npm run dev` 可在不启动 DSH 的情况下检查工作台，它将 `/api` 转到本机 8790；独立界面没有原生 DSH 对话。

工程工作站可直接用 `python -m pip install <执行器.whl>` 安装执行器，再按照其 README 设置 NX 路径与工作目录。容器部署时，将 `.env.example` 复制为 `.env`，填写工作站可访问的 `THERMAL_NX_WORKER_URL` 和令牌，然后运行 `docker compose up -d --build`。这里的工作站地址必须能从容器访问；容器中的 `127.0.0.1` 指容器自身。

## 使用顺序

1. 新建项目，导入 STEP 或几何交付 ZIP。可以先打开明确标注的示例项目熟悉界面；示例不含求解结果。
2. 导入参数资料或粘贴文本；检查标准参数中的橙色估值标记。用三维/拓扑选择对象，侧栏或表格编辑。
3. 检查工况。冷/热推荐按外热流选择整组时间点，也可指定时间或输入六向值。
4. 保存修改会创建带输入快照的新 run。NX 不在线时保留队列且仍可编辑；旧运行继续，必要时手动取消。
5. 在运行页面分别查看冻结输入、实际 NX 条件与结果，下载工程和数据包。

已确认的详细要求见 [开发基线](docs/首版需求与开发边界.md)，来源分析和访谈见 [资料分析](docs/资料分析与需求访谈.md)。

## 数据与来源

运行数据、凭据和真实卫星模型均不随插件发布。`vendor/nx-ug-thermal` 保留原项目源码及其 LICENSE；原六向外热流算法随 service 保留来源记录。热拓扑采用 React Flow（MIT），三维采用 Three.js（MIT）；Thermca 仅作为交互研究参考，未引入其求解器代码。
