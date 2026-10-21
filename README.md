# aliexpress-product-images

从一批商品素材批量生成速卖通（AliExpress）商品主图：每个 SKU 给出**产品图**与**效果参考图**，
输出 1:1、2048×2048 的 JPEG 主图。产品本体取自产品图，效果图只提供机位、光线、背景与构图；
产品上的印刷文字由本地程序叠印，保证可读。

本仓库同时是一个 **Agent Skill**（Claude Code / Hermes 可直接加载），也可以当普通命令行工具使用。

## 工作方式

```
来货目录 ──scan──> SKU 列表 + Excel 机位要求
                     │
                     ├─ 产品图（identity：部件/数量/颜色/材质/印字）
                     ├─ 效果图（只取机位/光线/背景/构图）
                     ▼
             六段提示词 ──render──> POST /v1/images/edits（图生图，产品图为条件）
                     │
                     ├─ 小字画不准 ──overlay──> 在干净底图上叠印真字
                     ▼
             output/<SKU>.jpg（成品） + _work/candidates/（候选）
```

提示词固定六段：`identity / camera / adapt / allow / forbid / tail`。写法与返工对照见
[`references/prompt-playbook.md`](references/prompt-playbook.md)。

## 环境要求

- Python 3.9+（核心只用标准库；`overlay` 子命令需要 Pillow）
- 商汤日日新（SenseNova）API Key，环境变量 `SENSENOVA_API_KEY`
- 默认模型：出图 `sensenova-u1.5-lite`，起草提示词 `sensenova-6.8-flash-lite`

```bash
export SENSENOVA_API_KEY=sk-xxxx          # 建议写进 ~/.zshrc
export SENSENOVA_BASE_URL=https://token.sensenova.cn/v1   # 可选，默认即此
```

## 安装

```bash
git clone https://github.com/jiawenlee1204/aliexpress-product-images.git
cd aliexpress-product-images
bash install.sh
```

`install.sh` 会把本目录复制到检测到的 agent 目录：

| 目标 | 路径 |
|---|---|
| Claude Code | `~/.claude/skills/aliexpress-product-images/` |
| Hermes | `${HERMES_HOME:-~/.hermes}/skills/creative/aliexpress-product-images/` |
| 任意 | `bash install.sh --target <目录>` |

安装后 `axn` 会软链到 `~/.local/bin/axn`（该目录存在时）。只想用一个 agent：
`bash install.sh --target claude` 或 `--target hermes`。

叠印功能另需 `pip install Pillow`。

## 快速开始

来货目录结构：

```
新/袜子图片方位需求/
   袜子产品方位需求.xlsx       # A 列 SKU，B 列机位要求（可选）
   SK0020603/
      SK0020603产品图.jpg      # 产品图，唯一真实性来源
      SK0020603效果图.jpg      # 效果参考，只借鉴机位/背景/光/构图
```

```bash
cd 你的项目目录

axn scan 新                                   # 1. 看有哪些 SKU、Excel 要什么机位
axn spec --src 新                              # 2. 起草六段提示词到 _work/specs.json
$EDITOR _work/specs.json                       #    改 camera / forbid（这一步决定质量）
axn render --specs _work/specs.json --src 新 --dry-run   # 3. 干跑核对
axn render --specs _work/specs.json --src 新              # 4. 出候选
axn pick --sku SK0020603 --cand A              # 5. 定稿进 output/
axn clean                                      # 6. 收尾
```

不想分步时，一条命令跑完（提示词由视觉模型起草，精度低于人工）：

```bash
axn auto --src 新 --review
```

## 目录约定

```
<项目根>/
  output/               成品 <SKU>.jpg（只有成品）
  _work/candidates/     候选 <SKU>_<候选>.jpg
  _work/replaced/       被替换的旧成品
  _work/specs.json      提示词规格
```

## 命令参考

| 命令 | 作用 |
|---|---|
| `axn scan <目录>` | 递归找出含「产品图/效果图」的 SKU 目录，打印 Excel 机位要求 |
| `axn spec --src <目录>` | 用视觉模型起草六段提示词到 `_work/specs.json`（已有 SKU 不覆盖，`--force` 覆盖） |
| `axn render --specs <json> --src <目录>` | 按规格出候选；`--only SKU`、`--cand A` 可选，`--dry-run` 只打印计划 |
| `axn auto --src <目录>` | 起草（缺规格时）→ 出图 → `--review` 复核 → 定稿 |
| `axn pick --sku <SKU> --cand <A\|B>` | 候选定稿到 `output/`，旧成品备份到 `_work/replaced/` |
| `axn overlay --base <图> --out <图> --text "COMFORT\|INSOLE"` | 在干净底图上叠印产品印字 |
| `axn clean` | 把 `output/` 里的过程目录挪进 `_work/`，清理 `__pycache__` |

`axn --help`、`axn <子命令> --help` 查看全部参数。

## 提示词规格（specs.json）

```json
{
  "skus": {
    "SK0020603": {
      "A": {
        "identity": "一双黑色中筒袜：……共 2 只",
        "camera": "① 真人赤脚穿着……⑤ 产品占画面约 75%",
        "adapt": "纯黑袜子在画面里极易糊成一整块死黑……",
        "allow": "穿着同款黑色中筒袜的两只脚、浅色木地板",
        "forbid": "鞋子、第二双袜子、文字、品牌标志",
        "tail": "两只袜子必须是同一双……"
      }
    }
  }
}
```

每个 SKU 可放多版候选（`A`、`B`…），同一装配关系、不同机位强调，出图后挑一张。
可选键：

- `refs`: `["局部放大.png"]` —— 额外参考图（相对该 SKU 目录）。对结构/符号形状有效，对字母拼写无效。
- `overlay`: `{"text": "COMFORT|INSOLE", "ink": 18, "cap": 0.034, "len": 0.40, "heel": 0.30}` —— 出图后自动叠印。

参考一份真实定稿：[`references/example-specs.json`](references/example-specs.json)。

## 产品印字叠印

模型把小字当纹理画，拼写不可控。做法是先出一张「表面干净、禁止任何文字」的底图，再叠印：

```bash
axn overlay --base 底图.jpg --out 成品.jpg --text "COMFORT|INSOLE"
```

- 自动识别主体像素、PCA 拟合长轴，沿长轴从后跟侧排字，`multiply` 叠印以保留底材纹理。
- `|` 之间留空表示插入符号：`--text "COMFORT| |INSOLE"`，默认画空心圆角方形波浪边印章。
- 比例按真品量取：`--len` = 整行长度 / 长轴，`--cap` = 字高 / 长轴（鞋垫实测 0.40 / 0.034）。
- 带字面在底图里应接近正立，否则长轴拟合会偏，叠印与透视错位。
- 改完可用 `ImageChops.difference` 与上一版比较：均值应在 1 以内、差异集中在文字窄带。

## 已知限制

- 出图串行，单张 16~25 秒；接口单并发，并发会返回 429。
- 产品上的小字无法靠模型准确渲染，需走叠印；加放大参考图只改善符号形状。
- `auto` 的提示词由视觉模型起草，产品识别与机位判断不如人工，出货前建议手改 `camera`。
- 依赖外部 API，需要联网与有效 Key；脚本默认绕过系统代理直连。

## 作为 Skill 使用

- **Claude Code**：安装后 skill 名为 `aliexpress-product-images`，直接描述需求即可。
- **Hermes**：按 frontmatter 的 `description` 自动加载。
- **其它 agent**：让它读 [`SKILL.md`](SKILL.md)，或直接调用 `axn`。

`SKILL.md` 的 frontmatter 只用 `name` + `description`，保持跨 agent 兼容。

## 目录

```
SKILL.md                      流程与提示词铁律（agent 读）
axn                           命令入口
install.sh                    安装
scripts/axn_img.py            全部子命令
references/prompt-playbook.md 六段写法与返工对照
references/example-specs.json 示例规格
```

## 许可

MIT，见 [LICENSE](LICENSE)。
